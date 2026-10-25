"""
Approved read-only tools and controlled ToolNode execution for the WildLensAI agent.
"""

import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

logger = logging.getLogger(__name__)

# Strictly allowlisted HTTPS endpoints
GBIF_MATCH_ENDPOINT = "https://api.gbif.org/v1/species/match"
WIKIPEDIA_API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
ALLOWED_HTTPS_HOSTS = {"api.gbif.org", "en.wikipedia.org"}
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_SPECIES_NAME_LENGTH = 150
MAX_EXTERNAL_TOOL_CALLS = 3


def _validate_species_query(name: str) -> str:
    """Validate species query parameters against malicious characters and length limits."""
    if not isinstance(name, str):
        raise ValueError("Species parameter must be a string.")
    
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Species parameter cannot be empty.")
    
    if len(cleaned) > MAX_SPECIES_NAME_LENGTH:
        raise ValueError(
            f"Species name exceeds maximum allowed length of {MAX_SPECIES_NAME_LENGTH} characters."
        )
    
    # Allow alphanumeric, spaces, hyphens, periods, and parentheses common in taxonomy
    if not re.match(r"^[A-Za-z0-9\s\-\.\(\)\']+$", cleaned):
        raise ValueError("Species name contains invalid or unsafe characters.")
    
    return cleaned


def _verify_allowlisted_url(url: str) -> bool:
    """Verify that an endpoint URL belongs to an explicitly approved HTTPS host."""
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.netloc in ALLOWED_HTTPS_HOSTS
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tool 1: lookup_species_taxonomy
# ---------------------------------------------------------------------------

@tool
def lookup_species_taxonomy(scientific_name: str) -> str:
    """Lookup taxonomic classification of a wildlife species using the official GBIF Species API.

    Returns the accepted scientific name, family, order, class, genus, species,
    taxonomic status, match type, and authoritative GBIF source URL.
    """
    start_time = time.time()
    logger.info("Executing lookup_species_taxonomy for query: %s", scientific_name)

    try:
        validated_name = _validate_species_query(scientific_name)
    except ValueError as val_err:
        logger.warning("lookup_species_taxonomy parameter validation error: %s", val_err)
        return json.dumps({
            "status": "VALIDATION_ERROR",
            "message": str(val_err),
            "source_name": "GBIF Species API",
            "source_url": "https://www.gbif.org"
        })

    if not _verify_allowlisted_url(GBIF_MATCH_ENDPOINT):
        return json.dumps({
            "status": "SECURITY_ERROR",
            "message": "Endpoint not permitted by allowlist.",
            "source_url": "https://www.gbif.org"
        })

    params = {"name": validated_name, "verbose": "false"}
    
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            response = client.get(GBIF_MATCH_ENDPOINT, params=params)
            duration = round(time.time() - start_time, 3)

            if response.status_code != 200:
                logger.warning(
                    "GBIF Species API returned HTTP %s in %ss",
                    response.status_code,
                    duration
                )
                return json.dumps({
                    "status": "API_ERROR",
                    "message": f"GBIF service returned HTTP status {response.status_code}.",
                    "source_name": "GBIF Species API",
                    "source_url": "https://www.gbif.org"
                })

            data = response.json()
            logger.info("GBIF Species API call succeeded in %ss", duration)

            match_type = data.get("matchType", "NONE")
            if match_type == "NONE" or not data.get("usageKey"):
                return json.dumps({
                    "status": "NO_MATCH",
                    "scientific_name_queried": validated_name,
                    "message": f"No definitive taxonomic match found in the GBIF backbone taxonomy for '{validated_name}'.",
                    "source_name": "GBIF Species API",
                    "source_url": "https://www.gbif.org"
                })

            usage_key = data.get("usageKey")
            source_url = f"https://www.gbif.org/species/{usage_key}" if usage_key else "https://www.gbif.org"

            result = {
                "status": "MATCHED",
                "accepted_scientific_name": data.get("scientificName") or data.get("canonicalName", validated_name),
                "canonical_name": data.get("canonicalName", validated_name),
                "kingdom": data.get("kingdom", "Animalia"),
                "phylum": data.get("phylum", "Chordata"),
                "class": data.get("class", "Not recorded"),
                "order": data.get("order", "Not recorded"),
                "family": data.get("family", "Not recorded"),
                "genus": data.get("genus", "Not recorded"),
                "species": data.get("species", data.get("canonicalName", validated_name)),
                "taxonomic_match_status": data.get("status", "ACCEPTED"),
                "match_type": match_type,
                "confidence": data.get("confidence", 0),
                "source_name": "Global Biodiversity Information Facility (GBIF)",
                "source_url": source_url
            }
            return json.dumps(result, indent=2)

    except httpx.TimeoutException:
        logger.warning("GBIF Species API request timed out after %ss", DEFAULT_TIMEOUT_SECONDS)
        return json.dumps({
            "status": "TIMEOUT",
            "message": f"Taxonomy lookup timed out after {DEFAULT_TIMEOUT_SECONDS} seconds.",
            "source_name": "GBIF Species API",
            "source_url": "https://www.gbif.org"
        })
    except Exception as exc:
        logger.error("GBIF Species API unexpected error: %s", exc)
        return json.dumps({
            "status": "ERROR",
            "message": "Taxonomy lookup encountered an unexpected error. Continuing with partial data.",
            "source_name": "GBIF Species API",
            "source_url": "https://www.gbif.org"
        })


# ---------------------------------------------------------------------------
# Tool 2: get_wildlife_facts
# ---------------------------------------------------------------------------

@tool
def get_wildlife_facts(species_name: str) -> str:
    """Retrieve verified wildlife biology, habitat, diet, distribution, and behavior from MediaWiki.

    Provides educational summary and reference links. General encyclopedia text
    is not treated as independently verified conservation status.
    """
    start_time = time.time()
    logger.info("Executing get_wildlife_facts for query: %s", species_name)

    try:
        validated_name = _validate_species_query(species_name)
    except ValueError as val_err:
        logger.warning("get_wildlife_facts parameter validation error: %s", val_err)
        return json.dumps({
            "status": "VALIDATION_ERROR",
            "message": str(val_err),
            "source_name": "MediaWiki API",
            "source_url": "https://en.wikipedia.org"
        })

    if not _verify_allowlisted_url(WIKIPEDIA_API_ENDPOINT):
        return json.dumps({
            "status": "SECURITY_ERROR",
            "message": "Endpoint not permitted by allowlist.",
            "source_url": "https://en.wikipedia.org"
        })

    headers = {
        "User-Agent": "WildLensAI/1.0 (Educational Wildlife Project; B.Tech Demonstration)"
    }
    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts|info",
        "exintro": 1,
        "explaintext": 1,
        "inprop": "url",
        "titles": validated_name,
        "redirects": 1,
    }

    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, headers=headers) as client:
            response = client.get(WIKIPEDIA_API_ENDPOINT, params=params)
            duration = round(time.time() - start_time, 3)

            if response.status_code != 200:
                logger.warning(
                    "MediaWiki API returned HTTP %s in %ss",
                    response.status_code,
                    duration
                )
                return json.dumps({
                    "status": "API_ERROR",
                    "message": f"MediaWiki service returned HTTP status {response.status_code}.",
                    "source_name": "MediaWiki API",
                    "source_url": "https://en.wikipedia.org"
                })

            data = response.json()
            logger.info("MediaWiki API call succeeded in %ss", duration)

            pages = data.get("query", {}).get("pages", {})
            page_data = None
            for pid, pinfo in pages.items():
                if pid != "-1" and "missing" not in pinfo:
                    page_data = pinfo
                    break

            if not page_data:
                return json.dumps({
                    "status": "NOT_FOUND",
                    "species_queried": validated_name,
                    "message": f"No encyclopedia article found for '{validated_name}'.",
                    "source_name": "MediaWiki API",
                    "source_url": "https://en.wikipedia.org"
                })

            extract = page_data.get("extract", "").strip()
            # Bounded context: keep first 2500 characters
            if len(extract) > 2500:
                extract = extract[:2500].rsplit(".", 1)[0] + "."

            source_url = page_data.get("fullurl") or f"https://en.wikipedia.org/wiki/{page_data.get('title', validated_name).replace(' ', '_')}"

            result = {
                "status": "FOUND",
                "species_queried": validated_name,
                "article_title": page_data.get("title", validated_name),
                "summary": extract,
                "conservation_status_note": "Not verified by this source. Authoritative IUCN Red List or wildlife management verification required.",
                "source_name": "MediaWiki / Wikipedia Encyclopedia",
                "source_url": source_url
            }
            return json.dumps(result, indent=2)

    except httpx.TimeoutException:
        logger.warning("MediaWiki API request timed out after %ss", DEFAULT_TIMEOUT_SECONDS)
        return json.dumps({
            "status": "TIMEOUT",
            "message": f"Wildlife facts lookup timed out after {DEFAULT_TIMEOUT_SECONDS} seconds.",
            "source_name": "MediaWiki API",
            "source_url": "https://en.wikipedia.org"
        })
    except Exception as exc:
        logger.error("MediaWiki API unexpected error: %s", exc)
        return json.dumps({
            "status": "ERROR",
            "message": "Wildlife facts lookup encountered an unexpected error. Continuing with partial data.",
            "source_name": "MediaWiki API",
            "source_url": "https://en.wikipedia.org"
        })


APPROVED_TOOLS = [lookup_species_taxonomy, get_wildlife_facts]
APPROVED_TOOL_NAMES = {t.name for t in APPROVED_TOOLS}


# ---------------------------------------------------------------------------
# Controlled ToolNode
# ---------------------------------------------------------------------------

class ControlledToolNode(ToolNode):
    """
    LangGraph ToolNode subclass enforcing strict execution limits:
    - Maximum external tool executions (default: 3).
    - Rejects unknown tool names safely.
    - Prevents repeated identical tool calls.
    - Catches malformed arguments without crashing.
    """

    def __init__(self, tools=None, max_tool_calls: int = MAX_EXTERNAL_TOOL_CALLS):
        tools = tools or APPROVED_TOOLS
        super().__init__(tools, handle_tool_errors=True)
        self.max_tool_calls = max_tool_calls

    def invoke(self, state: dict[str, Any], config: Optional[dict[str, Any]] = None, **kwargs) -> dict[str, Any]:
        messages = state.get("messages", [])
        current_count = state.get("tool_call_count", 0)
        executed = list(state.get("executed_tool_calls", []))

        last_msg = messages[-1] if messages else None
        if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
            return {
                "messages": [],
                "tool_call_count": current_count,
                "executed_tool_calls": executed
            }

        tool_messages: list[ToolMessage] = []
        new_executed = list(executed)
        new_count = current_count
        valid_calls_for_super: list[dict[str, Any]] = []

        for tc in last_msg.tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            tc_id = tc.get("id") or f"call_{len(tool_messages)}"
            sig = f"{name}:{json.dumps(args, sort_keys=True)}"

            if name not in self.tools_by_name:
                logger.warning("Rejected unapproved tool invocation: %s", name)
                tool_messages.append(ToolMessage(
                    content=f"Error: Unknown tool '{name}'. Approved read-only tools: {list(self.tools_by_name.keys())}",
                    name=name or "unknown",
                    tool_call_id=tc_id,
                    status="error"
                ))
            elif sig in new_executed:
                logger.info("Prevented duplicate tool call: %s", sig)
                tool_messages.append(ToolMessage(
                    content=f"Notice: Tool '{name}' with arguments {args} was already executed in this session. Reusing prior results.",
                    name=name,
                    tool_call_id=tc_id,
                    status="error"
                ))
            elif new_count >= self.max_tool_calls:
                logger.warning("Maximum tool call limit (%s) reached; halting further external requests.", self.max_tool_calls)
                tool_messages.append(ToolMessage(
                    content=f"Notice: Session limit of {self.max_tool_calls} external tool calls reached. Please synthesize final answer with available information.",
                    name=name,
                    tool_call_id=tc_id,
                    status="error"
                ))
            else:
                valid_calls_for_super.append(tc)
                new_executed.append(sig)
                new_count += 1

        # Execute only valid, budgeted calls
        for tc in valid_calls_for_super:
            name = tc.get("name")
            args = tc.get("args", {})
            tc_id = tc.get("id") or f"call_{len(tool_messages)}"
            tool_obj = self.tools_by_name.get(name)
            try:
                result_content = tool_obj.invoke(args)
                tool_messages.append(ToolMessage(
                    content=result_content if isinstance(result_content, str) else json.dumps(result_content),
                    name=name,
                    tool_call_id=tc_id,
                    status="success"
                ))
            except Exception as exc:
                logger.error("Error executing tool %s: %s", name, exc)
                tool_messages.append(ToolMessage(
                    content=f"Error executing tool '{name}': {exc}",
                    name=name,
                    tool_call_id=tc_id,
                    status="error"
                ))

        return {
            "messages": tool_messages,
            "tool_call_count": new_count,
            "executed_tool_calls": new_executed
        }
