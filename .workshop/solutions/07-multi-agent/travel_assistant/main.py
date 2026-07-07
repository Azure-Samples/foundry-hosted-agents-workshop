# travel_assistant/main.py
from agent_framework_foundry_hosting import ResponsesHostServer

from coordinator import build_travel_coordinator


def main() -> None:
    # The Coordinator + specialists handoff is exposed as a single agent, so the
    # rest of the hosting stack is unchanged from earlier steps.
    agent = build_travel_coordinator()
    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
