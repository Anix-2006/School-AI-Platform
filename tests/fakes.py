from langchain_core.messages import AIMessage


class FakeGraph:
    """Stands in for orchestrator_graph: records each call and replies."""

    def __init__(self, reply="Here is your update.", route="daily_update"):
        self.reply = reply
        self.route = route
        self.calls = []

    def invoke(self, state, config=None):
        self.calls.append({"state": state, "config": config})
        return {
            "messages": [*state["messages"], AIMessage(content=self.reply)],
            "route_to": self.route,
            "intent_source": "llm_router",
            "intent_confidence": None,
        }
