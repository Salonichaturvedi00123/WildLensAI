"""
LangGraph StateGraph implementation for the WildLensAI Wildlife Agent.

Features:
- StateGraph architecture with START, END, and conditional routing.
- Integration with Gemini multimodal vision identification.
- ToolNode with approved read-only tools (GBIF Species API & MediaWiki API).
- Controlled execution guards (max 3 tool executions, max 5 iterations, recursion limit 10).
- Pure SQLite / Django ORM conversation memory without external vector databases.
"""

import json
import logging
from typing import Any, Optional

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from animal.agent.state import WildlifeAgentState
from animal.agent.tools import (
    APPROVED_TOOLS,
    MAX_EXTERNAL_TOOL_CALLS,
    ControlledToolNode,
)
from animal.services.gemini_service import (
    GeminiModelAccessError,
    GeminiQuotaError,
    GeminiServiceError,
    GeminiTimeoutError,
    get_gemini_model,
    identify_animal_from_image,
)
from animal.services.image_validator import ImageValidationError
from animal.utils import extract_clean_message_text, sanitize_source_references

logger = logging.getLogger(__name__)

MAX_GRAPH_ITERATIONS = 5

WILDLIFE_AGENT_SYSTEM_PROMPT = """You are WildLensAI, an expert, evidence-grounded agentic wildlife biologist and conversational assistant.

Your mission is to provide accurate, educational, and scientifically verified wildlife profiles and answers.

Reasoning and Tool Guidelines:
1. Tool Selection:
   - You have access to two read-only tools:
     * `lookup_species_taxonomy(scientific_name)`: Look up authoritative taxonomy (kingdom, phylum, class, order, family, genus, species) via GBIF.
     * `get_wildlife_facts(species_name)`: Retrieve verified species biology, habitat, diet, distribution, and behavior via MediaWiki.
   - Only call tools when the needed information is not already present in the conversation history or when scientific verification is required.
   - Do NOT execute tools unnecessarily or repeatedly for the same arguments.
   - A strict budget of at most 3 external tool calls applies to the entire session.

2. Information Quality & Structure:
   - When presenting a wildlife profile or answering user inquiries, provide:
     * Common Name & Scientific Name
     * Taxonomic Classification (from GBIF if retrieved)
     * Habitat & Geographical Distribution
     * Diet & Feeding Behavior
     * Physical Features & Diagnostic Traits
     * Behavior & Ecology
     * Lifespan & Ecological Importance (if verified)
     * Conservation Status: State "Not verified" unless confirmed by authoritative data. Do not treat general encyclopedia text as verified conservation status.
     * Interesting Facts (source-supported)
     * Verified References & Links
   - If certain information cannot be found or verified by tools, explicitly admit that it is unavailable.
   - Never invent or hallucinate facts, measurements, or conservation statuses.

3. Language Support (English & Hindi):
   - You fully support both English and Hindi.
   - If the user asks in Hindi, requests an explanation in Hindi (e.g., "Explain it in Hindi", "हिंदी में समझाएं", "हिंदी में बताओ"), or switches languages during the conversation, respond fluently, accurately, and educationally in Hindi using Devanagari script.
   - Keep language clear, educational, and accessible for students and wildlife enthusiasts.

4. Domain Boundaries & Specimen Grounding:
   - Always ground your responses in the context of the active wildlife specimen being discussed.
   - If the user asks about diet, habitat, behavior, or traits, answer specifically regarding the active animal.
   - Strictly refuse off-topic non-wildlife inquiries (e.g., programming, general chatter, politics) politely, steering the user back to the animal.
   - If the specimen identification status is "uncertain", clearly emphasize that conclusions are tentative hypotheses based on visible field marks, not verified facts.

5. Tone and Output Constraints:
   - Professional, educational, and scientifically accurate.
   - Strictly NEVER output emojis in any language.
"""


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------

def input_router_node(state: WildlifeAgentState) -> dict[str, Any]:
    """Inspects the request type and initializes baseline state defaults."""
    return {
        "tool_call_count": state.get("tool_call_count", 0),
        "executed_tool_calls": state.get("executed_tool_calls", []),
        "iteration_count": state.get("iteration_count", 0),
        "source_references": state.get("source_references", []),
    }


def vision_node(state: WildlifeAgentState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    """
    Multimodal Vision Node:
    Reuses Phase 3 Gemini identification logic to analyze uploaded images.
    Extracts raw image bytes from runtime config without persisting binaries in state.
    """
    configurable = (config or {}).get("configurable", {})
    image_bytes = configurable.get("image_bytes")
    mime_type = configurable.get("mime_type", "image/jpeg")

    if not image_bytes:
        logger.error("vision_node invoked without image_bytes in runtime config.")
        return {
            "error": "No image data was provided for visual identification.",
            "final_result": None
        }

    try:
        ident_result = identify_animal_from_image(image_bytes, mime_type=mime_type)
    except (GeminiQuotaError, GeminiTimeoutError, GeminiModelAccessError, GeminiServiceError, ImageValidationError) as exc:
        logger.error("Vision identification error in vision_node: %s", exc)
        return {
            "error": str(exc),
            "final_result": None
        }

    status = ident_result.identification_status
    ident_dict = {
        "is_animal": ident_result.is_animal,
        "identification_status": status,
        "common_name": ident_result.common_name,
        "scientific_name": ident_result.scientific_name,
        "visual_clues": ident_result.visual_clues,
        "alternative_species": ident_result.alternative_species,
        "identification_note": ident_result.identification_note,
    }

    # Case A: Not an animal -> Conclude immediately with structured result
    if status == "not_animal":
        final_res = {
            "identification_status": "not_animal",
            "common_name": "No Animal Detected",
            "scientific_name": None,
            "visual_clues": [],
            "alternative_species": [],
            "wildlife_information": ident_result.identification_note,
            "source_references": [],
        }
        return {
            "identification": ident_dict,
            "final_result": final_res,
            "messages": [AIMessage(content=ident_result.identification_note)]
        }

    # Case B: Uncertain identification -> Conclude with visual clues & alternatives
    if status == "uncertain":
        final_res = {
            "identification_status": "uncertain",
            "common_name": ident_result.common_name or "Uncertain Species",
            "scientific_name": ident_result.scientific_name,
            "visual_clues": ident_result.visual_clues,
            "alternative_species": ident_result.alternative_species,
            "wildlife_information": ident_result.identification_note,
            "source_references": [],
        }
        return {
            "identification": ident_dict,
            "final_result": final_res,
            "messages": [AIMessage(content=ident_result.identification_note)]
        }

    # Case C: Clearly identified animal -> Prepare prompt to route into Agent Node
    analysis_prompt = (
        f"An animal has been visually identified in the uploaded image.\n"
        f"Common Name: {ident_result.common_name}\n"
        f"Scientific Name: {ident_result.scientific_name or 'Unspecified'}\n"
        f"Visual Clues Observed: {', '.join(ident_result.visual_clues) if ident_result.visual_clues else 'None recorded'}\n"
        f"Field Notes: {ident_result.identification_note}\n\n"
        f"Please verify its taxonomic classification using GBIF, retrieve verified wildlife biology and facts from MediaWiki, "
        f"and generate a comprehensive educational wildlife profile following your guidelines."
    )

    return {
        "identification": ident_dict,
        "messages": [HumanMessage(content=analysis_prompt)]
    }


def agent_node(state: WildlifeAgentState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    """
    Agent Reasoning Node:
    Decides whether taxonomy or facts tools are required, invokes tools or synthesizes final response.
    Enforces maximum iteration and tool call guards.
    """
    messages = list(state.get("messages", []))
    iter_count = state.get("iteration_count", 0) + 1
    tool_call_count = state.get("tool_call_count", 0)

    # Inject system instruction if not already present
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=WILDLIFE_AGENT_SYSTEM_PROMPT)] + messages

    model = get_gemini_model()

    # Budget guard: if budget exhausted or approaching max iterations, force final synthesis without tools
    force_final_synthesis = (
        tool_call_count >= MAX_EXTERNAL_TOOL_CALLS or iter_count >= (MAX_GRAPH_ITERATIONS - 1)
    )

    if force_final_synthesis:
        logger.info(
            "Agent budget guard active (calls=%s, iter=%s). Forcing final response without tool bindings.",
            tool_call_count,
            iter_count
        )
        response = model.invoke(messages)
    else:
        model_with_tools = model.bind_tools(APPROVED_TOOLS)
        response = model_with_tools.invoke(messages)

    # Case 1: Agent produced tool calls -> return AIMessage for ToolNode
    if getattr(response, "tool_calls", None):
        return {
            "messages": [response],
            "iteration_count": iter_count,
        }

    # Case 2: Agent completed response without tool calls -> synthesize final result & extract references
    source_references: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for msg in messages + [response]:
        if isinstance(msg, ToolMessage) and msg.content:
            try:
                data = json.loads(msg.content)
                if isinstance(data, dict):
                    url = data.get("source_url")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        source_references.append({
                            "title": data.get("article_title") or data.get("accepted_scientific_name") or data.get("source_name", "External Source"),
                            "url": url,
                            "source": data.get("source_name", "Verified Wildlife Database"),
                        })
            except Exception:
                pass

    ident_status = (state.get("identification") or {}).get("identification_status", "identified")
    common_name = (state.get("identification") or {}).get("common_name")
    scientific_name = (state.get("identification") or {}).get("scientific_name")
    visual_clues = (state.get("identification") or {}).get("visual_clues", [])
    alternative_species = (state.get("identification") or {}).get("alternative_species", [])

    clean_wildlife_text = extract_clean_message_text(response)
    sanitized_source_refs = sanitize_source_references(source_references)

    final_result = {
        "identification_status": ident_status,
        "common_name": common_name,
        "scientific_name": scientific_name,
        "visual_clues": visual_clues,
        "alternative_species": alternative_species,
        "wildlife_information": clean_wildlife_text,
        "source_references": sanitized_source_refs,
    }

    return {
        "messages": [response],
        "iteration_count": iter_count,
        "final_result": final_result,
        "source_references": sanitized_source_refs,
    }


# ---------------------------------------------------------------------------
# Conditional Routing Functions
# ---------------------------------------------------------------------------

def route_request(state: WildlifeAgentState) -> str:
    """Route initial input to either the Vision Node or directly to the Agent Node."""
    req_type = state.get("request_type", "chat")
    if req_type == "image":
        return "vision_node"
    return "agent_node"


def route_vision(state: WildlifeAgentState) -> str:
    """Evaluate vision output: exit immediately on errors, non-animals, or uncertain findings."""
    if state.get("error"):
        return END

    status = (state.get("identification") or {}).get("identification_status")
    if status in ("not_animal", "uncertain"):
        return END
    return "agent_node"


def should_continue(state: WildlifeAgentState) -> str:
    """Decide whether to execute tool calls or terminate the agent cycle."""
    iter_count = state.get("iteration_count", 0)
    if iter_count >= MAX_GRAPH_ITERATIONS:
        logger.warning("Agent reached maximum graph iteration limit (%s). Terminating.", MAX_GRAPH_ITERATIONS)
        return END

    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None

    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        if state.get("tool_call_count", 0) >= MAX_EXTERNAL_TOOL_CALLS:
            logger.info("Tool budget reached (%s). Terminating agent loop.", MAX_EXTERNAL_TOOL_CALLS)
            return END
        return "tools"

    return END


# ---------------------------------------------------------------------------
# Graph Assembly
# ---------------------------------------------------------------------------

def build_wildlife_agent_graph() -> StateGraph:
    """
    Constructs and compiles the full WildLensAI StateGraph:
    - input_router -> [vision_node, agent_node]
    - vision_node -> [agent_node, END]
    - agent_node -> [tools, END]
    - tools -> agent_node
    """
    workflow = StateGraph(WildlifeAgentState)

    # Register Nodes
    workflow.add_node("input_router", input_router_node)
    workflow.add_node("vision_node", vision_node)
    workflow.add_node("agent_node", agent_node)
    workflow.add_node("tools", ControlledToolNode(APPROVED_TOOLS, max_tool_calls=MAX_EXTERNAL_TOOL_CALLS))

    # Entrypoint
    workflow.add_edge(START, "input_router")

    # Conditional Routing Edges
    workflow.add_conditional_edges(
        "input_router",
        route_request,
        {
            "vision_node": "vision_node",
            "agent_node": "agent_node",
        }
    )

    workflow.add_conditional_edges(
        "vision_node",
        route_vision,
        {
            "agent_node": "agent_node",
            END: END,
        }
    )

    workflow.add_conditional_edges(
        "agent_node",
        should_continue,
        {
            "tools": "tools",
            END: END,
        }
    )

    # Tool feedback loop
    workflow.add_edge("tools", "agent_node")

    return workflow.compile()


# Singleton compiled graph instance
wildlife_agent_app = build_wildlife_agent_graph()


def run_wildlife_agent(
    request_type: str = "chat",
    image_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = "image/jpeg",
    user_message: Optional[str] = None,
    conversation_history: Optional[list[AnyMessage]] = None,
) -> dict[str, Any]:
    """
    Public execution entrypoint for views and tests.

    Executes the LangGraph StateGraph with explicit recursion limits and runtime config.
    """
    messages: list[AnyMessage] = []
    if conversation_history:
        messages.extend(conversation_history)

    if user_message:
        messages.append(HumanMessage(content=user_message))

    initial_state: WildlifeAgentState = {
        "messages": messages,
        "request_type": request_type,
        "identification": None,
        "source_references": [],
        "tool_call_count": 0,
        "executed_tool_calls": [],
        "iteration_count": 0,
        "final_result": None,
        "error": None,
    }

    config = {
        "recursion_limit": 10,
        "configurable": {
            "image_bytes": image_bytes,
            "mime_type": mime_type,
        }
    }

    return wildlife_agent_app.invoke(initial_state, config=config)
