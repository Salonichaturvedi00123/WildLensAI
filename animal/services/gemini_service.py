"""
Gemini Vision Identification Service for WildLensAI.

Integrates with Google Gemini models using `langchain-google-genai` and Pydantic
structured outputs to perform multimodal animal species identification.
"""

import os
import base64
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from animal.services.image_validator import validate_and_process_image, ImageValidationError


# ---------------------------------------------------------------------------
# Custom Exception Hierarchy
# ---------------------------------------------------------------------------

class GeminiServiceError(Exception):
    """Base exception for all Gemini vision service errors."""
    pass


class GeminiConfigurationError(GeminiServiceError):
    """Raised when required API keys or environment variables are missing."""
    pass


class GeminiModelAccessError(GeminiServiceError):
    """Raised when the requested Gemini model is not accessible or not found."""
    pass


class GeminiQuotaError(GeminiServiceError):
    """Raised when API rate limits or quota boundaries are exceeded."""
    pass


class GeminiTimeoutError(GeminiServiceError):
    """Raised when a request to the Gemini API times out."""
    pass


class GeminiNetworkError(GeminiServiceError):
    """Raised when a network or transport failure occurs communicating with Gemini."""
    pass


class GeminiResponseError(GeminiServiceError):
    """Raised when the model response is malformed or unparseable."""
    pass


# ---------------------------------------------------------------------------
# Pydantic Structured Output Schema
# ---------------------------------------------------------------------------

class AnimalIdentificationResult(BaseModel):
    """Structured response schema for multimodal wildlife identification."""

    is_animal: bool = Field(
        description="True if one or more animals (mammals, birds, reptiles, amphibians, fish, insects, arachnids, etc.) are visible in the image."
    )

    identification_status: Literal["identified", "uncertain", "not_animal"] = Field(
        description=(
            "Outcome classification: 'identified' if the species can be diagnosed with confidence from visible traits; "
            "'uncertain' if visible traits are ambiguous, distant, blurry, or multiple conflicting species appear; "
            "'not_animal' if no fauna is present in the image."
        )
    )

    common_name: Optional[str] = Field(
        default=None,
        description="Primary common name of the identified or dominant animal (e.g., 'Bengal Tiger', 'Red Fox'). None if not an animal."
    )

    scientific_name: Optional[str] = Field(
        default=None,
        description="Formal binomial nomenclature (e.g., 'Panthera tigris tigris', 'Vulpes vulpes'). None if not an animal or unknown."
    )

    visual_clues: List[str] = Field(
        default_factory=list,
        description="Concrete, visible morphological features observed directly in the image (e.g., 'Prominent dorsal stripe', 'Pointed triangular ears', 'Barred flight feathers')."
    )

    alternative_species: List[str] = Field(
        default_factory=list,
        description="Plausible look-alike or closely related species considered, especially if status is uncertain or multiple animals appear."
    )

    identification_note: str = Field(
        description=(
            "A comprehensive, objective taxonomic explanation grounded strictly in visible image evidence. "
            "If multiple animals are present, specify the scene composition and describe the dominant specimen. "
            "If no animal or uncertain, explain exactly why without fabricating details."
        )
    )


# ---------------------------------------------------------------------------
# Model Factory
# ---------------------------------------------------------------------------

def get_gemini_model(
    model_name: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    temperature: float = 0.1,
) -> ChatGoogleGenerativeAI:
    """
    Factory creating a configured ChatGoogleGenerativeAI instance.

    Reads configuration strictly from environment variables:
    - GOOGLE_API_KEY (or GEMINI_API_KEY fallback)
    - GEMINI_MODEL (defaults to 'gemini-3.8-flash')
    - GEMINI_TIMEOUT_SECONDS (defaults to 30)

    Args:
        model_name: Optional model override.
        timeout_seconds: Optional timeout in seconds.
        temperature: Sampling temperature for deterministic structured extraction.

    Returns:
        ChatGoogleGenerativeAI instance.

    Raises:
        GeminiConfigurationError: If no API key is available in the environment.
    """
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        raise GeminiConfigurationError(
            "Google Gemini API key is missing. Please configure 'GOOGLE_API_KEY' or 'GEMINI_API_KEY' in your environment."
        )

    resolved_model = model_name or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    if not resolved_model:
        resolved_model = "gemini-3.5-flash-lite"

    try:
        resolved_timeout = timeout_seconds or int(os.getenv("GEMINI_TIMEOUT_SECONDS", "120"))
    except ValueError:
        resolved_timeout = 120

    return ChatGoogleGenerativeAI(
        model=resolved_model,
        api_key=api_key.strip(),
        timeout=resolved_timeout,
        temperature=temperature,
        max_retries=1,
    )


# ---------------------------------------------------------------------------
# Multimodal Identification Pipeline
# ---------------------------------------------------------------------------

TAXONOMIST_SYSTEM_PROMPT = (
    "You are an expert wildlife biologist, taxonomist, and field naturalist. "
    "Analyze the provided photograph and identify any animal specimens visible. "
    "Rules you must strictly follow:\n"
    "1. Ground all findings ONLY in observable physical traits in this specific photograph.\n"
    "2. Never invent calibrated numerical confidence percentages.\n"
    "3. Never guess or default to famous animals (such as Lion, Tiger, Elephant) unless clearly supported by visible evidence.\n"
    "4. If multiple animals are present, state the full scope of what is visible and designate the primary/dominant animal.\n"
    "5. If the specimen cannot be diagnosed with certainty (e.g. blurry, poor lighting, obscured landmarks), "
    "set identification_status to 'uncertain', list plausible alternatives, and explain the ambiguity in identification_note.\n"
    "6. If the image does not depict an animal (e.g. vehicle, human, empty landscape, furniture), "
    "set is_animal to False, identification_status to 'not_animal', leave common_name and scientific_name as null, "
    "and describe the scene in identification_note.\n"
    "7. Provide specific, visible visual clues supporting the identification."
)


def identify_animal_from_image(
    image_file,
    model_name: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    mime_type: Optional[str] = None,
) -> AnimalIdentificationResult:
    """
    Execute end-to-end multimodal wildlife identification on an uploaded image file.

    1. Validates image size, format, resolution, and decompression safety with Pillow.
    2. Initializes Gemini through the reusable model factory.
    3. Invokes structured multimodal extraction.
    4. Handles network, timeout, quota, and schema failures gracefully.

    Args:
        image_file: Django UploadedFile, file-like object, or raw image bytes.
        model_name: Optional Gemini model name override.
        timeout_seconds: Optional timeout override.
        mime_type: Optional MIME type override (e.g. 'image/jpeg').

    Returns:
        AnimalIdentificationResult Pydantic model instance.

    Raises:
        ImageValidationError: If image verification fails.
        GeminiServiceError (or subclass): If an API or parsing error occurs.
    """
    # 1. Validate image bytes and MIME type via Pillow
    raw_bytes, detected_mime = validate_and_process_image(image_file)
    effective_mime = mime_type or detected_mime

    # 2. Build Gemini model with structured output
    llm = get_gemini_model(model_name=model_name, timeout_seconds=timeout_seconds)
    structured_llm = llm.with_structured_output(AnimalIdentificationResult)

    # 3. Construct multimodal message with base64 data URI
    base64_data = base64.b64encode(raw_bytes).decode("utf-8")
    data_uri = f"data:{effective_mime};base64,{base64_data}"

    user_message = HumanMessage(
        content=[
            {"type": "text", "text": TAXONOMIST_SYSTEM_PROMPT},
            {
                "type": "image_url",
                "image_url": {"url": data_uri},
            },
        ]
    )

    # 4. Invoke model with error classification
    try:
        result = structured_llm.invoke([user_message])
    except TimeoutError as e:
        raise GeminiTimeoutError(f"Gemini API request timed out after configured duration: {e}") from e
    except Exception as e:
        err_msg = str(e).lower()
        err_type = type(e).__name__

        # Check for quota / rate limit errors
        if "429" in err_msg or "quota" in err_msg or "resourceexhausted" in err_msg:
            raise GeminiQuotaError(
                "Gemini API quota exceeded or rate limit reached. Please try again shortly."
            ) from e

        # Check for model access / not found errors
        if "404" in err_msg or "not found" in err_msg or "invalid model" in err_msg:
            configured_model = model_name or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
            raise GeminiModelAccessError(
                f"Configured model '{configured_model}' is not accessible or does not exist with the supplied API key: {e}"
            ) from e

        # Check for timeout errors
        if "timeout" in err_msg or "timed out" in err_msg:
            raise GeminiTimeoutError(f"Gemini request timed out: {e}") from e

        # Check for network / connection errors
        if "connection" in err_msg or "network" in err_msg or "httpx" in err_msg or "dns" in err_msg:
            raise GeminiNetworkError(f"Network error while communicating with Gemini API: {e}") from e

        # Check for schema validation or bad response errors
        if "validation" in err_msg or "json" in err_msg or "schema" in err_msg:
            raise GeminiResponseError(f"Failed to parse Gemini identification schema: {e}") from e

        raise GeminiServiceError(f"Gemini vision identification failed ({err_type}): {e}") from e

    # 5. Validate result type integrity
    if not isinstance(result, AnimalIdentificationResult):
        # In case library returned dict or None
        if isinstance(result, dict):
            try:
                result = AnimalIdentificationResult(**result)
            except Exception as e:
                raise GeminiResponseError(f"Could not convert response mapping to schema: {e}") from e
        else:
            raise GeminiResponseError(f"Unexpected response type from model: {type(result)}")

    return result
