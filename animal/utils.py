"""
Wildlife Species Identification & Response Processing Interface for WildLensAI.

Provides:
- `predict_animal`: Routes uploaded user photographs to the Gemini vision pipeline.
- `extract_clean_message_text`: Extracts human-readable text from AIMessage, content blocks,
  or stringified lists, strictly stripping provider metadata (extras, signatures).
- `render_markdown_safe`: Renders markdown text into sanitized HTML using markdown-it-py,
  disabling raw HTML to prevent XSS attacks.
- `is_valid_source_url`: Validates http/https URLs.
- `sanitize_source_references`: Filters and validates external references without fabricating URLs.
"""

import ast
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse
from markdown_it import MarkdownIt

from animal.services.gemini_service import (
    identify_animal_from_image,
    AnimalIdentificationResult,
    GeminiServiceError,
    ImageValidationError,
)

logger = logging.getLogger(__name__)

# Initialize MarkdownIt with raw HTML processing disabled (XSS defense) and table support enabled
_md_renderer = MarkdownIt("commonmark", {"html": False}).enable("table")


def predict_animal(image_file) -> AnimalIdentificationResult:
    """
    Execute AI wildlife identification on an uploaded image file using Gemini Vision.

    Args:
        image_file: Uploaded image file-like object or raw bytes.

    Returns:
        AnimalIdentificationResult: Pydantic structured output from Gemini.
    """
    return identify_animal_from_image(image_file)


def extract_clean_message_text(val: Any) -> str:
    """
    Extract human-readable text from an AIMessage, message content blocks,
    or legacy database values, strictly excluding provider metadata and signatures.

    Args:
        val: AIMessage, dict, list of content blocks, or string.

    Returns:
        Clean, human-readable text string.
    """
    if val is None:
        return ""

    # 1. Extract content attribute from message objects if present
    content = getattr(val, "content", val)

    # 2. Handle list of content blocks: [{'type': 'text', 'text': '...', 'extras': {...}}]
    # We prioritize this before AIMessage.text because LangChain's built-in .text concatenates
    # blocks with "" (empty string), which causes text across distinct blocks to collide.
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str) and block.strip():
                parts.append(block.strip())
            elif isinstance(block, dict):
                # Standard LangChain / Gemini content block
                text_val = block.get("text")
                if text_val and isinstance(text_val, str) and text_val.strip():
                    parts.append(text_val.strip())
        if parts:
            return "\n\n".join(parts)
        return ""

    # 3. Use AIMessage.text where supported (when content is not a multi-block list)
    if hasattr(val, "text") and isinstance(val.text, str) and val.text.strip():
        return val.text.strip()

    # 4. Handle dictionary structures (e.g., {'summary': '...'} or {'text': '...'})
    if isinstance(content, dict):
        if "summary" in content:
            return extract_clean_message_text(content["summary"])
        if "text" in content:
            return extract_clean_message_text(content["text"])
        return ""

    # 5. Handle string content (including legacy stringified Python list literals)
    if isinstance(content, str):
        trimmed = content.strip()
        # Detect stringified Python or JSON lists of content blocks
        if trimmed.startswith("[") and trimmed.endswith("]") and ("type" in trimmed and "text" in trimmed):
            # Attempt safe parsing via ast.literal_eval (never eval())
            try:
                parsed = ast.literal_eval(trimmed)
                if isinstance(parsed, list):
                    parts = []
                    for item in parsed:
                        if isinstance(item, dict) and "text" in item and item["text"]:
                            parts.append(str(item["text"]).strip())
                        elif isinstance(item, str) and item.strip():
                            parts.append(item.strip())
                    if parts:
                        return "\n\n".join(parts)
            except Exception:
                # If literal_eval fails, try json.loads
                try:
                    parsed_json = json.loads(trimmed)
                    if isinstance(parsed_json, list):
                        parts = [
                            str(item["text"]).strip()
                            for item in parsed_json
                            if isinstance(item, dict) and "text" in item and item["text"]
                        ]
                        if parts:
                            return "\n\n".join(parts)
                except Exception:
                    pass

        return trimmed

    return str(content)


def render_markdown_safe(text: Any) -> str:
    """
    Renders clean markdown text into safe HTML using markdown-it-py.
    Raw HTML processing is strictly disabled to prevent XSS.

    Args:
        text: Markdown string or message content block.

    Returns:
        Safe HTML string ready for display.
    """
    clean_text = extract_clean_message_text(text)
    if not clean_text:
        return ""
    return _md_renderer.render(clean_text)


def is_valid_source_url(url: Any) -> bool:
    """
    Validate that a URL is a well-formed http/https link with a valid network location.
    Rejects javascript:, data:, and malformed URIs.
    """
    if not url or not isinstance(url, str):
        return False
    url_str = url.strip()
    try:
        parsed = urlparse(url_str)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc
    except Exception:
        return False


def sanitize_source_references(references: Any) -> list[dict[str, str]]:
    """
    Filters and validates source references, ensuring only genuine, reachable
    http/https external URLs are returned. Never fabricates source links.

    Args:
        references: List of reference dicts or URLs.

    Returns:
        List of sanitized reference dicts with 'title', 'url', and 'source'.
    """
    if not isinstance(references, list):
        return []

    sanitized: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for item in references:
        if isinstance(item, dict):
            raw_url = item.get("url")
            if is_valid_source_url(raw_url) and raw_url not in seen_urls:
                seen_urls.add(raw_url)
                title = item.get("title") or item.get("source") or "Verified Source"
                source = item.get("source") or "External Biological Registry"
                sanitized.append({
                    "title": str(title).strip(),
                    "url": str(raw_url).strip(),
                    "source": str(source).strip(),
                })
        elif isinstance(item, str) and is_valid_source_url(item) and item not in seen_urls:
            seen_urls.add(item)
            sanitized.append({
                "title": item.strip(),
                "url": item.strip(),
                "source": "External Biological Registry",
            })

    return sanitized


def get_section_icon(title: str) -> str:
    """Return appropriate Bootstrap icon class for a wildlife discovery section title."""
    t = str(title).lower()
    if any(k in t for k in ("taxonom", "classif", "kingdom", "family", "genus", "scientific", "about")):
        return "bi bi-info-circle"
    if any(k in t for k in ("habitat", "distribut", "range", "geographic", "native", "where it lives")):
        return "bi bi-geo-alt"
    if any(k in t for k in ("diet", "feed", "food", "prey", "nutrition", "carnivore", "herbivore", "what it eats")):
        return "bi bi-egg-fried"
    if any(k in t for k in ("physical", "morpholog", "characterist", "trait", "appearance", "feather", "fur", "size")):
        return "bi bi-eye"
    if any(k in t for k in ("behavior", "behaviour", "ecolog", "social", "activity", "life in the wild")):
        return "bi bi-activity"
    if any(k in t for k in ("reproduct", "breed", "nest", "mating", "gestation", "offspring", "family")):
        return "bi bi-heart"
    if any(k in t for k in ("lifespan", "longevity", "life expectancy", "age")):
        return "bi bi-hourglass-split"
    if any(k in t for k in ("conservation", "status", "threat", "iucn", "endangered", "vulnerable", "protection")):
        return "bi bi-shield-exclamation"
    if any(k in t for k in ("fact", "trivia", "did you know", "interesting", "amazing")):
        return "bi bi-lightbulb"
    if any(k in t for k in ("reference", "source", "citation", "registry", "link", "learn more")):
        return "bi bi-journal-bookmark"
    return "bi bi-card-text"


def format_friendly_section_title(raw_title: str) -> str:
    """Format section titles into friendly, visitor-oriented discovery headings."""
    t = str(raw_title).lower().strip()
    if any(k in t for k in ("taxonom", "classif", "overview", "about", "species summary", "profile")):
        return "About This Animal"
    if any(k in t for k in ("habitat", "distribut", "range", "where it lives", "geographic", "native")):
        return "Where It Lives"
    if any(k in t for k in ("diet", "feed", "food", "prey", "nutrition", "what it eats", "carnivore", "herbivore", "omnivore")):
        return "What It Eats"
    if any(k in t for k in ("appearance", "physical", "morpholog", "characterist", "trait", "feather", "fur", "size", "look")):
        return "Appearance & Characteristics"
    if any(k in t for k in ("behavior", "behaviour", "ecolog", "social", "activity", "life in the wild")):
        return "Life in the Wild"
    if any(k in t for k in ("reproduct", "breed", "nest", "mating", "gestation", "offspring", "family")):
        return "Family & Reproduction"
    if any(k in t for k in ("lifespan", "longevity", "life expectancy", "age")):
        return "Lifespan"
    if any(k in t for k in ("conservation", "status", "threat", "iucn", "endangered", "vulnerable", "protection")):
        return "Conservation & Protection"
    if any(k in t for k in ("fact", "trivia", "did you know", "interesting", "amazing")):
        return "Amazing Facts"
    if any(k in t for k in ("reference", "source", "citation", "registry", "learn more")):
        return "Learn More & Sources"
    return raw_title


def organize_dossier_sections(wildlife_info: Any, source_references: Any = None) -> list[dict[str, Any]]:
    """
    Organizes wildlife information into readable, visually consistent sections.
    Handles headings, safe markdown rendering, missing values, and verified references.
    Avoids unnecessarily splitting plain text into multiple empty cards.
    """
    clean_text = extract_clean_message_text(wildlife_info)
    sections: list[dict[str, Any]] = []

    if clean_text:
        # Split markdown text by headers (#, ##, ###, ####)
        header_pattern = re.compile(r'(?m)^(#{1,6})\s+(.+)$')
        matches = list(header_pattern.finditer(clean_text))

        if matches:
            for idx, match in enumerate(matches):
                raw_title = match.group(2).strip()
                # Remove leading numbers like "1. ", "2. ", "1 - "
                title = re.sub(r'^\d+[\.\-\)]\s*', '', raw_title)
                friendly_title = format_friendly_section_title(title)
                start_pos = match.end()
                end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(clean_text)
                section_body = clean_text[start_pos:end_pos].strip()

                if not section_body:
                    body_html = '<p class="text-muted fst-italic mb-0">Not available</p>'
                else:
                    body_html = render_markdown_safe(section_body)

                sections.append({
                    "title": title,
                    "friendly_title": friendly_title,
                    "icon": get_section_icon(title),
                    "content_html": body_html,
                    "is_references": any(k in title.lower() for k in ("reference", "source", "citation", "learn more")),
                })
        else:
            # Single cohesive section without artificial splitting
            sections.append({
                "title": "Wildlife Overview & Biology",
                "friendly_title": "About This Animal",
                "icon": "bi bi-card-text",
                "content_html": render_markdown_safe(clean_text),
                "is_references": False,
            })

    # Append References section if not already present in the parsed text
    sanitized_refs = sanitize_source_references(source_references)
    has_ref_section = any(s.get("is_references") for s in sections)

    if sanitized_refs and not has_ref_section:
        ref_items = []
        for ref in sanitized_refs:
            url = ref.get("url", "#")
            title_text = ref.get("title") or ref.get("source") or url
            source = ref.get("source") or "Verified Registry"
            ref_items.append(
                f'<a href="{url}" target="_blank" rel="noopener noreferrer" class="badge bg-white text-forest border py-2 px-3 text-decoration-none shadow-sm me-2 mb-2 d-inline-flex align-items-center">'
                f'<i class="bi bi-box-arrow-up-right text-success me-2" aria-hidden="true"></i>'
                f'<span><strong>{title_text}</strong> <small class="text-muted ms-1">({source})</small></span>'
                f'</a>'
            )
        sections.append({
            "title": "Verified References",
            "friendly_title": "Learn More & Sources",
            "icon": "bi bi-journal-bookmark",
            "content_html": "".join(ref_items),
            "is_references": True,
        })
    elif not sections:
        sections.append({
            "title": "Wildlife Dossier",
            "friendly_title": "About This Animal",
            "icon": "bi bi-info-circle",
            "content_html": '<p class="text-muted fst-italic mb-0">Not verified or not available for this specimen.</p>',
            "is_references": False,
        })

    return sections
