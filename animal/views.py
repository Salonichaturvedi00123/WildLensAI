import json
import logging
import time
import uuid
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.contrib import messages
from django.db import transaction
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, AnyMessage

from .models import AnimalPrediction, Contact, WildlifeConversation, WildlifeMessage
from animal.services.gemini_service import (
    AnimalIdentificationResult,
    GeminiConfigurationError,
    GeminiModelAccessError,
    GeminiQuotaError,
    GeminiTimeoutError,
    GeminiNetworkError,
    GeminiResponseError,
    GeminiServiceError,
)
from animal.services.image_validator import validate_and_process_image, ImageValidationError
from animal.agent import run_wildlife_agent
from animal.utils import extract_clean_message_text, render_markdown_safe, sanitize_source_references
from transactions.models import Profile, WalletTransaction
from transactions.services import deduct_prediction_credit

logger = logging.getLogger(__name__)


def home(request):
    """Render landing page with platform overview and pricing."""
    return render(request, 'animal/home.html')


@login_required(login_url='login')
def predict(request):
    """
    Handle animal species prediction from uploaded image using Gemini Vision & LangGraph.
    Requires user authentication, validates wallet balance and image safety,
    executes Gemini multimodal structured extraction, and records results to SQLite.

    Demo credit rules (Phase 7):
      - Cost: 1 credit per successfully completed animal identification.
      - Free: Non-animal images, invalid images, failed Gemini calls, viewing previous predictions.
      - Atomic balance deduction using Django F expressions and conditional checks.
      - Enforces idempotency via request_id to prevent duplicate charges or submissions.
    """
    user_profile, _ = Profile.objects.get_or_create(
        user=request.user,
        defaults={'wallet_balance': Decimal('5.00')}
    )

    # 1. Parse or initialize unique request_id for idempotency
    if request.method == 'POST':
        raw_request_id = request.POST.get('request_id', '').strip()
        request_id = raw_request_id if raw_request_id else str(uuid.uuid4())
    else:
        request_id = str(uuid.uuid4())

    prediction = None

    if request.method == 'POST':
        # 2. Check for duplicate request_id submission (Idempotency)
        existing_pred = AnimalPrediction.objects.filter(user=request.user, request_id=request_id).first()
        if existing_pred:
            messages.info(request, "Displaying previously processed result for this request.")
            return render(request, 'animal/predict.html', {
                'prediction': existing_pred,
                'profile': user_profile,
                'request_id': str(uuid.uuid4()),
                'insufficient_credits': False,
            })

        image = request.FILES.get('image')

        if not image:
            messages.error(request, 'Please select an image file to upload.')
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })

        # 3. Check wallet credits (1 credit required per prediction)
        required_credits = Decimal('1.00')
        if user_profile.wallet_balance < required_credits:
            messages.error(
                request,
                f'Insufficient wallet balance (Current: ₹{user_profile.wallet_balance:.2f}). '
                f'Wildlife identification requires ₹{required_credits:.2f}. Please contact an admin or recharge demo credits.'
            )
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': True,
            })

        # 4. Validate image safely via Pillow (invalid images are free)
        try:
            raw_bytes, mime_type = validate_and_process_image(image)
        except ImageValidationError as e:
            messages.error(request, f'Image validation error: {e}')
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })

        # 5. Execute LangGraph Wildlife Agent Pipeline (Failed calls are free)
        try:
            agent_output = run_wildlife_agent(
                request_type="image",
                image_bytes=raw_bytes,
                mime_type=mime_type,
            )
        except GeminiConfigurationError as e:
            messages.error(request, f'Gemini configuration notice: {e}')
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })
        except GeminiQuotaError as e:
            messages.error(request, f'API quota exceeded: {e}')
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })
        except GeminiTimeoutError as e:
            messages.error(request, f'Vision identification timed out: {e}')
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })
        except GeminiModelAccessError as e:
            messages.error(request, f'Model access error: {e}')
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })
        except (GeminiNetworkError, GeminiResponseError, GeminiServiceError) as e:
            messages.error(request, f'Wildlife identification failed: {e}')
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })
        except Exception as e:
            messages.error(request, f'Wildlife agent execution encountered an error: {e}')
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })

        if agent_output.get("error"):
            messages.error(request, f"Identification notice: {agent_output['error']}")
            return render(request, 'animal/predict.html', {
                'prediction': None,
                'profile': user_profile,
                'request_id': request_id,
                'insufficient_credits': False,
            })

        final_res = agent_output.get("final_result") or {}
        ident_res = agent_output.get("identification") or {}

        status = final_res.get("identification_status") or ident_res.get("identification_status", "uncertain")
        if status == "not_animal":
            display_name = "No Animal Detected"
            scientific = ""
        elif final_res.get("common_name") or ident_res.get("common_name"):
            display_name = final_res.get("common_name") or ident_res.get("common_name")
            scientific = final_res.get("scientific_name") or ident_res.get("scientific_name") or ""
        else:
            display_name = "Uncertain Species"
            scientific = final_res.get("scientific_name") or ident_res.get("scientific_name") or ""

        visual_clues = final_res.get("visual_clues") or ident_res.get("visual_clues", [])
        alternative_species = final_res.get("alternative_species") or ident_res.get("alternative_species", [])
        notes = ident_res.get("identification_note") or ""
        wildlife_info = extract_clean_message_text(final_res.get("wildlife_information") or "")
        source_refs = sanitize_source_references(final_res.get("source_references") or agent_output.get("source_references") or [])

        # Rewind image file pointer before saving to ImageField
        if hasattr(image, "seek"):
            image.seek(0)

        # 6. Rule 7: Images with no identifiable animal do not consume credits
        is_animal = ident_res.get("is_animal", True)
        if status == "not_animal" or not is_animal:
            cost = Decimal("0.00")
        else:
            cost = Decimal("1.00")

        # 7. Atomic Credit Deduction & Specimen Finalization
        tx = None
        with transaction.atomic():
            if cost > Decimal("0.00"):
                success, tx, err_msg = deduct_prediction_credit(
                    user=request.user,
                    cost=cost,
                    request_id=request_id,
                    prediction=None
                )
                if not success:
                    messages.error(request, f"Credit deduction notice: {err_msg}")
                    return render(request, 'animal/predict.html', {
                        'prediction': None,
                        'profile': user_profile,
                        'request_id': request_id,
                        'insufficient_credits': True,
                    })

            # Create persistent record in SQLite
            saved_record = AnimalPrediction.objects.create(
                user=request.user,
                uploaded_image=image,
                common_name=display_name,
                scientific_name=scientific,
                confidence=0.0,
                credit_used=float(cost),
                identification_status=status,
                identification_notes=notes,
                visual_clues=visual_clues,
                alternative_species=alternative_species,
                wildlife_information={
                    "summary": wildlife_info,
                    "tool_calls_executed": agent_output.get("tool_call_count", 0),
                    "is_animal": is_animal,
                },
                source_references=source_refs,
                request_id=request_id
            )

            # Link transaction to prediction if transaction was created
            if tx:
                tx.prediction = saved_record
                tx.description = f"Identification fee for {saved_record.common_name}"
                tx.save(update_fields=["prediction", "description"])

            # Initialize conversation in SQLite
            conv = WildlifeConversation.objects.create(
                user=request.user,
                prediction=saved_record,
                title=f"{saved_record.common_name} Inquiry"
            )
            if wildlife_info:
                WildlifeMessage.objects.create(
                    conversation=conv,
                    role="assistant",
                    content=wildlife_info
                )

        user_profile.refresh_from_db()

        prediction = {
            'id': saved_record.id,
            'common_name': saved_record.common_name,
            'species': saved_record.common_name,
            'scientific_name': saved_record.scientific_name,
            'confidence': saved_record.confidence,
            'credit_used': saved_record.credit_used,
            'image_url': saved_record.uploaded_image,
            'uploaded_image': saved_record.uploaded_image,
            'identification_status': saved_record.identification_status,
            'identification_notes': saved_record.identification_notes,
            'visual_clues': saved_record.visual_clues,
            'alternative_species': saved_record.alternative_species,
            'wildlife_information': wildlife_info,
            'clean_wildlife_summary': wildlife_info,
            'rendered_dossier': render_markdown_safe(wildlife_info),
            'source_references': source_refs,
            'is_animal': is_animal,
        }

        if cost == Decimal("0.00"):
            messages.info(
                request,
                'Analysis complete: No wildlife detected in the uploaded photograph. Zero credits deducted.'
            )
        elif status == "identified":
            messages.success(
                request,
                f'Species identified: {saved_record.common_name}. ₹1.00 credit deducted.'
            )
        elif status == "uncertain":
            messages.warning(
                request,
                f'Identification uncertain. Visual clues and alternative candidate species recorded. ₹1.00 credit deducted.'
            )

    return render(request, 'animal/predict.html', {
        'prediction': prediction,
        'profile': user_profile,
        'request_id': str(uuid.uuid4()),
        'insufficient_credits': user_profile.wallet_balance < Decimal("1.00"),
    })


def contact(request):
    """Handle contact form submissions."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        subject = request.POST.get('subject', '').strip()
        message = request.POST.get('message', '').strip()

        if not name or not email or not message:
            messages.error(request, 'Name, email, and message are required.')
            return render(request, 'animal/contact.html')

        Contact.objects.create(
            name=name,
            email=email,
            subject=subject or 'General Inquiry',
            message=message,
        )

        messages.success(request, 'Thank you! Your message has been received.')
        return render(request, 'animal/contact.html', {'success': True})

    return render(request, 'animal/contact.html')


@login_required(login_url='login')
def history(request):
    """Display user-specific prediction history for authenticated users."""
    predictions = AnimalPrediction.objects.filter(
        user=request.user
    ).order_by('-created_at')

    return render(request, 'animal/history.html', {'predictions': predictions})


@login_required(login_url='login')
def prediction_detail(request, prediction_id):
    """Display single prediction details with user data isolation and interactive multi-turn conversational assistant."""
    prediction = get_object_or_404(
        AnimalPrediction,
        id=prediction_id,
        user=request.user
    )
    conv, _ = WildlifeConversation.objects.get_or_create(
        user=request.user,
        prediction=prediction,
        defaults={"title": f"{prediction.common_name} Inquiry"}
    )

    if request.method == "POST":
        user_query = request.POST.get("message", "").strip()
        if user_query:
            # 1. Store user inquiry in SQLite
            WildlifeMessage.objects.create(
                conversation=conv,
                role="user",
                content=user_query
            )

            # 2. Build bounded context from SQLite (last 10 messages)
            history_msgs = []
            history_msgs.append(
                HumanMessage(
                    content=f"Context: We are discussing a verified specimen of {prediction.common_name} "
                            f"(Scientific: {prediction.scientific_name or 'Unspecified'}, "
                            f"Status: {prediction.identification_status}). "
                            f"Visual clues: {', '.join(prediction.visual_clues) if prediction.visual_clues else 'None'}."
                )
            )
            for pm in conv.messages.all().order_by("created_at")[:10]:
                if pm.role == "user":
                    history_msgs.append(HumanMessage(content=pm.content))
                elif pm.role == "assistant":
                    history_msgs.append(AIMessage(content=pm.content))

            # 3. Invoke LangGraph agent with chat request
            agent_result = run_wildlife_agent(
                request_type="chat",
                user_message=user_query,
                conversation_history=history_msgs[:-1]
            )

            # 4. Save assistant reply to SQLite
            bot_reply = "I was unable to retrieve that information."
            if agent_result.get("messages"):
                last_m = agent_result["messages"][-1]
                bot_reply = extract_clean_message_text(last_m)
            if not bot_reply:
                bot_reply = extract_clean_message_text(agent_result.get("final_result", {}).get("wildlife_information", ""))

            WildlifeMessage.objects.create(
                conversation=conv,
                role="assistant",
                content=bot_reply
            )

            messages.success(request, "Wildlife agent responded to your inquiry.")
            return redirect("prediction_detail", prediction_id=prediction.id)

    conversations = prediction.conversations.filter(user=request.user)
    conversation_messages = conv.messages.all().order_by("created_at")

    return render(request, 'animal/prediction_detail.html', {
        'prediction': prediction,
        'conversation': conv,
        'conversations': conversations,
        'conversation_messages': conversation_messages,
    })


@login_required(login_url='login')
@require_POST
def assistant_chat(request):
    """
    JSON API endpoint for the WildLens conversational wildlife assistant.
    POST /assistant/chat/

    Input:
        - conversation_id: int
        - message: str (1 to 1000 characters)

    Guards:
        - Authenticated user determines conversation ownership.
        - Loads bounded context (most recent 10 messages from SQLite).
        - Strictly grounds dialogue in the active specimen.
        - Supports English and Hindi dynamically.
        - Only saves successful, non-empty AI answers to SQLite.
    """
    # 1. Parse payload (supports JSON and form-encoded data)
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": "Invalid JSON payload."}, status=400)
    else:
        payload = request.POST

    raw_conv_id = payload.get("conversation_id")
    raw_message = payload.get("message", "")

    if not raw_conv_id:
        return JsonResponse({"success": False, "error": "conversation_id is required."}, status=400)

    try:
        conv_id = int(raw_conv_id)
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Invalid conversation_id format."}, status=400)

    message = str(raw_message).strip()
    if not message:
        return JsonResponse({"success": False, "error": "Message cannot be empty."}, status=400)

    if len(message) > 1000:
        return JsonResponse({
            "success": False,
            "error": "Message exceeds maximum allowed length of 1000 characters."
        }, status=400)

    # 2. Check conversation ownership
    try:
        conv = WildlifeConversation.objects.select_related("prediction").get(id=conv_id, user=request.user)
    except WildlifeConversation.DoesNotExist:
        return JsonResponse({"success": False, "error": "Conversation not found or access denied."}, status=404)

    prediction = conv.prediction
    if not prediction:
        return JsonResponse({
            "success": False,
            "error": "Associated wildlife prediction record has been removed."
        }, status=404)

    # 3. Simple application-level rate limiting (minimum 1.0 second delay between queries)
    now_ts = time.time()
    rate_key = f"last_chat_ts_{conv.id}"
    last_ts = request.session.get(rate_key, 0)
    if now_ts - last_ts < 1.0:
        return JsonResponse({
            "success": False,
            "error": "Please wait a moment before sending another inquiry."
        }, status=429)
    request.session[rate_key] = now_ts

    # 4. Assemble bounded context from SQLite
    context_header = (
        f"Active Specimen Context:\n"
        f"- Common Name: {prediction.common_name}\n"
        f"- Scientific Name: {prediction.scientific_name or 'Unspecified'}\n"
        f"- Identification Status: {prediction.identification_status}\n"
        f"- Visual Field Clues: {', '.join(prediction.visual_clues) if prediction.visual_clues else 'None recorded'}\n"
        f"- Notes: {prediction.identification_notes}\n"
    )
    if prediction.identification_status == "uncertain":
        context_header += (
            f"- Note: This specimen identification is UNCERTAIN. Potential candidate lookalikes: "
            f"{', '.join(prediction.alternative_species) if prediction.alternative_species else 'None'}. "
            f"Do not present observations as confirmed species facts; discuss them as plausible hypotheses.\n"
        )

    if prediction.wildlife_information and isinstance(prediction.wildlife_information, dict):
        base_summary = prediction.wildlife_information.get("summary")
        if base_summary:
            context_header += f"- Stored Baseline Profile: {base_summary[:1000]}\n"

    # Retrieve most recent 10 messages from SQLite in chronological order
    recent_db_msgs = list(conv.messages.all().order_by("-created_at")[:10])
    recent_db_msgs.reverse()

    history: list[AnyMessage] = [SystemMessage(content=context_header)]
    for m in recent_db_msgs:
        if m.role == "user":
            history.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            history.append(AIMessage(content=m.content))

    # 5. Execute LangGraph Wildlife Agent
    try:
        agent_output = run_wildlife_agent(
            request_type="chat",
            user_message=message,
            conversation_history=history
        )
    except (GeminiQuotaError, GeminiTimeoutError, GeminiModelAccessError, GeminiServiceError) as exc:
        logger.error("LangGraph assistant execution error: %s", exc)
        return JsonResponse({"success": False, "error": f"Assistant service notice: {exc}"}, status=503)
    except Exception as exc:
        logger.error("Unexpected assistant execution error: %s", exc)
        return JsonResponse({
            "success": False,
            "error": "The assistant was unable to process your inquiry at this moment."
        }, status=500)

    if agent_output.get("error"):
        return JsonResponse({"success": False, "error": agent_output["error"]}, status=500)

    final_res = agent_output.get("final_result") or {}
    bot_reply = ""
    if agent_output.get("messages"):
        last_m = agent_output["messages"][-1]
        bot_reply = extract_clean_message_text(last_m)
    if not bot_reply:
        bot_reply = extract_clean_message_text(final_res.get("wildlife_information", ""))

    if not bot_reply:
        return JsonResponse({
            "success": False,
            "error": "The assistant produced an empty response. Please try rephrasing your question."
        }, status=500)

    # 6. Save verified interaction to SQLite
    user_record = WildlifeMessage.objects.create(
        conversation=conv,
        role="user",
        content=message
    )
    asst_record = WildlifeMessage.objects.create(
        conversation=conv,
        role="assistant",
        content=bot_reply
    )

    raw_sources = final_res.get("source_references") or agent_output.get("source_references") or []
    sources = sanitize_source_references(raw_sources)

    return JsonResponse({
        "success": True,
        "answer": bot_reply,
        "rendered_answer": render_markdown_safe(bot_reply),
        "conversation_id": conv.id,
        "sources": sources,
        "created_at": asst_record.created_at.strftime("%I:%M %p")
    })
