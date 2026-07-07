"""TODO: create the durable TravelBuddy workflow per docs/steps/08-workflow.md."""

from __future__ import annotations

# TODO: Add specialist executors, checkpoint storage, consolidation, approval gate,
# and final itinerary workflow wiring following docs/steps/08-workflow.md.


def build_workflow():
    raise NotImplementedError(
        "TODO: implement workflow.py per docs/steps/08-workflow.md"
    )


async def run_workflow(prompt: str) -> None:
    workflow = build_workflow()
    async for event in workflow.run(prompt, stream=True):
        print(event)
