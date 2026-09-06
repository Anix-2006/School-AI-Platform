from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Shared state threaded through the orchestrator graph. Every node
    reads/writes a subset of this - keeps agents decoupled from each other's
    internals while sharing one source of truth per conversation turn."""

    messages: Annotated[list, add_messages]
    tenant_id: str
    student_id: Optional[str]
    guardian_id: str
    language: str            # en / hi / te
    age_tier: Optional[str]  # pre_primary / primary_lower / primary
    intent: Optional[str]    # set by orchestrator's router step
    route_to: Optional[str]  # which specialist agent handles this turn
