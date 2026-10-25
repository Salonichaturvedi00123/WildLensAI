"""
State definition for the WildLensAI LangGraph Wildlife Agent.
"""

from typing import Annotated, Any, Literal, Optional
from typing_extensions import TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class WildlifeAgentState(TypedDict):
    """
    Minimal LangGraph state schema for wildlife identification and inquiry.

    Adheres strictly to the principle of minimal persisted state:
    - No raw binary images, API keys, or Django ORM instances are stored here.
    - Message history is accumulated via LangGraph's standard add_messages reducer.
    """
    messages: Annotated[list[AnyMessage], add_messages]
    request_type: Literal["image", "chat"]
    identification: Optional[dict[str, Any]]
    source_references: list[dict[str, str]]
    tool_call_count: int
    executed_tool_calls: list[str]
    iteration_count: int
    final_result: Optional[dict[str, Any]]
    error: Optional[str]
