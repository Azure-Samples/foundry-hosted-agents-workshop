# travel_assistant/workflow.py
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Executor,
    FileCheckpointStorage,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from dotenv import load_dotenv

from coordinator import (
    _build_skills_provider,
    create_activities_agent,
    create_flights_agent,
    create_hotels_agent,
    make_client,
)

load_dotenv(override=True)

# Local checkpoint directory. Swap FileCheckpointStorage for CosmosCheckpointStorage
# (agent-framework-azure-cosmos) for a distributed, multi-user deployment.
CHECKPOINT_DIR = Path(".workshop-state") / "workflow-checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DraftPlan:
    session_id: str
    flights: str = ""
    hotels: str = ""
    activities: str = ""


@dataclass
class ApprovalRequest:
    prompt: str
    draft: str
    session_id: str


FINALIZE_INSTRUCTIONS = (
    "You write the final travel itinerary from an approved draft. Merge the flight, "
    "hotel, and activity sections into one concise, actionable plan. Do not invent "
    "bookings; clearly mark any assumptions.\n"
    "You are the dedicated finalize node: unlike the Step 7 handoff Coordinator "
    "(a pure router that couldn't own tools/skills), you own the final deliverable "
    "and its safety:\n"
    "- Always use the travel-guide skill to turn the plan into a downloadable, shareable PDF trip guide.\n"
    "- Always apply the response-guardrails skill to your answer before you return it."
)


def _finalize_prompt(draft: str, changes: str = "") -> str:
    if changes:
        return (
            "Revise the draft using the traveler's requested changes, then produce the "
            f"final itinerary.\n\nDraft:\n{draft}\n\nRequested changes:\n{changes}"
        )
    return f"Produce the final travel itinerary from this draft.\n\n{draft}"


class GatherPreferences(Executor):
    """Fan the user request out to the three specialists in one superstep."""

    def __init__(self) -> None:
        super().__init__(id="gather_preferences")

    @handler
    async def gather(self, request: str, ctx: WorkflowContext[AgentExecutorRequest]) -> None:
        ctx.set_state("session_id", ctx.get_state("session_id", "local-user"))
        ctx.set_state("original_request", request)

        base = (
            "The user is planning a trip. Use only the details relevant to your "
            "specialty and preserve budget, dates, travelers, and constraints.\n\n"
            f"User request:\n{request}"
        )
        for target_id, focus in {
            "flights": "Recommend flight approach, timing, and trade-offs.",
            "hotels": "Recommend neighbourhoods, hotel style, and budget split.",
            "activities": "Recommend a balanced day-by-day activity plan.",
        }.items():
            await ctx.send_message(
                AgentExecutorRequest(
                    messages=[Message(role="user", contents=[f"{base}\n\nFocus: {focus}"])],
                    should_respond=True,
                ),
                target_id=target_id,
            )


class Consolidate(Executor):
    """Collect the three specialist answers, checkpoint, then continue.

    With ``approval_target`` set, the draft is sent there for human review; otherwise
    the finalize prompt is sent straight to ``finalize_itinerary``.
    """

    def __init__(self, approval_target: str | None = None) -> None:
        super().__init__(id="consolidate")
        self._approval_target = approval_target
        self._draft: DraftPlan | None = None

    @handler
    async def collect(
        self, response: AgentExecutorResponse, ctx: WorkflowContext[AgentExecutorRequest | ApprovalRequest]
    ) -> None:
        draft = ctx.get_state("draft_plan", DraftPlan(session_id=ctx.get_state("session_id", "local-user")))
        if response.executor_id == "flights":
            draft.flights = response.agent_response.text
        elif response.executor_id == "hotels":
            draft.hotels = response.agent_response.text
        elif response.executor_id == "activities":
            draft.activities = response.agent_response.text
        ctx.set_state("draft_plan", draft)
        self._draft = draft

        if not (draft.flights and draft.hotels and draft.activities):
            return  # Wait for the remaining specialists.

        consolidated = (
            "Draft trip plan.\n\n"
            f"## Flights\n{draft.flights}\n\n"
            f"## Hotels\n{draft.hotels}\n\n"
            f"## Activities\n{draft.activities}\n"
        )
        ctx.set_state("consolidated_plan", consolidated)

        if self._approval_target:
            await ctx.send_message(
                ApprovalRequest(
                    prompt="Approve this draft or describe changes before finalising.",
                    draft=consolidated,
                    session_id=draft.session_id,
                ),
                target_id=self._approval_target,
            )
        else:
            await ctx.send_message(
                AgentExecutorRequest(
                    messages=[Message(role="user", contents=[_finalize_prompt(consolidated)])],
                    should_respond=True,
                ),
                target_id="finalize_itinerary",
            )

    async def on_checkpoint_save(self) -> dict[str, Any]:
        # Node-local state captured alongside the framework's shared state so a
        # resume from this superstep does not lose the partially built draft.
        return {"draft": self._draft.__dict__ if self._draft else None}

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        data = state.get("draft")
        self._draft = DraftPlan(**data) if data else None


class ApprovalGate(Executor):
    """Optional human-in-the-loop gate (see build_workflow(require_approval=True)).

    ``ctx.request_info`` pauses the workflow and emits a request_info event. When the
    workflow is hosted as an agent, that request is surfaced to the caller as a
    function call; locally it is answered by streaming a response back into run().
    """

    def __init__(self) -> None:
        super().__init__(id="approval_gate")

    @handler
    async def request_approval(self, request: ApprovalRequest, ctx: WorkflowContext[str]) -> None:
        ctx.set_state("pending_draft", request.draft)
        await ctx.request_info(request_data=request, response_type=str)

    @response_handler
    async def on_approval(
        self, original_request: ApprovalRequest, feedback: str, ctx: WorkflowContext[AgentExecutorRequest]
    ) -> None:
        approved = feedback.strip().lower() in {"approve", "approved", "looks good", "finalise", "finalize"}
        prompt = _finalize_prompt(original_request.draft, "" if approved else feedback)
        await ctx.send_message(
            AgentExecutorRequest(
                messages=[Message(role="user", contents=[prompt])], should_respond=True
            ),
            target_id="finalize_itinerary",
        )


def build_workflow(require_approval: bool = False):
    """Build the durable trip-planning workflow.

    Default graph (runs end-to-end when hosted):
        gather -> {flights, hotels, activities} -> consolidate -> finalize
    With require_approval=True, an ApprovalGate is inserted before finalize.
    """
    client = make_client()

    flights = AgentExecutor(create_flights_agent(client), id="flights", context_mode="last_agent")
    hotels = AgentExecutor(create_hotels_agent(client), id="hotels", context_mode="last_agent")
    activities = AgentExecutor(create_activities_agent(client), id="activities", context_mode="last_agent")

    # The finalize agent owns the deliverable: travel-guide (PDF) + response-guardrails.
    finalize_agent = Agent(
        client=client,
        name="finalize_itinerary",
        instructions=FINALIZE_INSTRUCTIONS,
        context_providers=[_build_skills_provider()],
        default_options={"store": False},
    )
    finalize = AgentExecutor(finalize_agent, id="finalize_itinerary", context_mode="last_agent")

    gather = GatherPreferences()
    approval = ApprovalGate() if require_approval else None
    consolidate = Consolidate(approval_target="approval_gate" if require_approval else None)

    builder = (
        WorkflowBuilder(
            name="travel_planning_workflow",
            start_executor=gather,
            checkpoint_storage=FileCheckpointStorage(
                str(CHECKPOINT_DIR),
                # FileCheckpointStorage uses restricted-pickle deserialization; our
                # own dataclasses must be allow-listed by "module:qualname" or a
                # resume raises on restore.
                allowed_checkpoint_types=["workflow:DraftPlan", "workflow:ApprovalRequest"],
            ),
            output_executors=[finalize],
        )
        .add_edge(gather, flights)
        .add_edge(gather, hotels)
        .add_edge(gather, activities)
        .add_edge(flights, consolidate)
        .add_edge(hotels, consolidate)
        .add_edge(activities, consolidate)
    )
    if approval is not None:
        builder = builder.add_edge(consolidate, approval).add_edge(approval, finalize)
    else:
        builder = builder.add_edge(consolidate, finalize)

    return builder.build()


def build_workflow_agent(require_approval: bool = False) -> Agent:
    """Expose the workflow as a single hosted agent for the Responses server."""
    return build_workflow(require_approval=require_approval).as_agent()
