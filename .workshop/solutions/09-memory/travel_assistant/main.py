# travel_assistant/main.py
from agent_framework_foundry_hosting import ResponsesHostServer

from workflow import build_workflow_agent


def main() -> None:
    # The durable workflow (gather -> specialists -> consolidate -> finalize) is
    # exposed as a single agent via `.as_agent()`, so hosting is unchanged from the
    # earlier steps. Set require_approval=True to insert the human approval gate.
    agent = build_workflow_agent(require_approval=False)
    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
