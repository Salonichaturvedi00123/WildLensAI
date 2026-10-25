import json
import logging
from decimal import Decimal
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Profile, CreditPlan, PaymentOrder, WalletTransaction
from .services import (
    create_razorpay_payment_order,
    credit_wallet_for_verified_order,
    reconcile_payment_order,
    process_razorpay_webhook,
    PaymentGatewayConfigurationError,
    PaymentOrderError,
    PaymentVerificationError,
)

logger = logging.getLogger(__name__)


def register(request):
    """Handle new user registration with Django password validation and initial wallet credit setup."""
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if not username or not password:
            messages.error(request, "Username and password are required.")
            return render(request, "auth/register.html")

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "auth/register.html")

        # Validate password using Django's built-in password validators
        candidate_user = User(username=username, email=email, first_name=first_name, last_name=last_name)
        try:
            validate_password(password, user=candidate_user)
        except ValidationError as err:
            for error_message in err.messages:
                messages.error(request, error_message)
            return render(request, "auth/register.html")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists. Please choose another.")
            return render(request, "auth/register.html")

        if email and User.objects.filter(email=email).exists():
            messages.error(request, "Email is already registered.")
            return render(request, "auth/register.html")

        user = User.objects.create_user(
            first_name=first_name,
            last_name=last_name,
            username=username,
            email=email,
            password=password,
        )

        Profile.objects.get_or_create(
            user=user,
            defaults={"wallet_balance": Decimal("5.00")}
        )

        messages.success(request, "Registration successful! You received 5 free credits. Please log in.")
        return redirect("login")

    return render(request, "auth/register.html")


def user_login(request):
    """Authenticate existing users and restore their session."""
    if request.user.is_authenticated:
        return redirect("home")

    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        if not username or not password:
            messages.error(request, "Please enter both username and password.")
            return render(request, "auth/login.html", {"next": next_url})

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:
            login(request, user)
            Profile.objects.get_or_create(
                user=user,
                defaults={"wallet_balance": Decimal("5.00")}
            )
            messages.success(request, f"Welcome back, {user.username}!")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect("home")
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, "auth/login.html", {"next": next_url})


def user_logout(request):
    """Log out the current user session safely using POST."""
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "Logged out successfully.")
    return redirect("login")


@login_required(login_url="login")
def profile(request):
    """Display user profile, wallet balance, recent transactions, and account overview dashboard."""
    user_profile, _ = Profile.objects.get_or_create(
        user=request.user,
        defaults={"wallet_balance": Decimal("5.00")}
    )

    recent_predictions = request.user.predictions.all()[:5]
    recent_transactions = request.user.wallet_transactions.all()[:10]
    credit_plans = CreditPlan.objects.filter(is_active=True).order_by("display_order", "price")
    recent_orders = request.user.payment_orders.all()[:5]

    context = {
        "profile": user_profile,
        "user": request.user,
        "recent_predictions": recent_predictions,
        "recent_transactions": recent_transactions,
        "credit_plans": credit_plans,
        "recent_orders": recent_orders,
        "razorpay_key_id": getattr(settings, "RAZORPAY_KEY_ID", ""),
        "razorpay_mode": getattr(settings, "RAZORPAY_MODE", "test"),
    }

    return render(request, "auth/profile.html", context)


@login_required(login_url="login")
def recharge_wallet(request):
    """
    Dedicated Razorpay credit recharge page.
    Displays wallet balance, active packages, checkout triggers,
    recent orders, and transaction ledger.
    """
    user_profile, _ = Profile.objects.get_or_create(
        user=request.user,
        defaults={"wallet_balance": Decimal("5.00")}
    )

    credit_plans = CreditPlan.objects.filter(is_active=True).order_by("display_order", "price")
    recent_orders = request.user.payment_orders.all()[:15]
    recent_transactions = request.user.wallet_transactions.all()[:15]

    razorpay_key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    razorpay_mode = getattr(settings, "RAZORPAY_MODE", "test")

    context = {
        "profile": user_profile,
        "user": request.user,
        "credit_plans": credit_plans,
        "recent_orders": recent_orders,
        "recent_transactions": recent_transactions,
        "razorpay_key_id": razorpay_key_id,
        "razorpay_mode": razorpay_mode,
    }
    return render(request, "auth/recharge.html", context)


def forgot_password(request):
    """Password reset request placeholder view."""
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        if not email:
            messages.error(request, "Please enter your registered email address.")
        elif not User.objects.filter(email=email).exists():
            messages.error(request, "No account was found with that email address.")
        else:
            messages.success(
                request,
                "If an account exists with that email, reset instructions have been dispatched."
            )
            return redirect("login")

    return render(request, "auth/forgot.html")


@require_GET
def list_credit_plans(request):
    """
    Public API returning currently active wildlife credit recharge plans.
    """
    plans = CreditPlan.objects.filter(is_active=True).values(
        "id", "name", "description", "price", "credits", "display_order"
    )
    # Convert Decimal price to float/string for JSON serialization
    serialized_plans = [
        {
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "price": float(p["price"]),
            "price_formatted": f"₹{p['price']:.2f}",
            "credits": p["credits"],
            "display_order": p["display_order"],
        }
        for p in plans
    ]
    return JsonResponse({"success": True, "plans": serialized_plans})


@login_required(login_url="login")
@require_POST
def create_payment_order(request):
    """
    Initiates a Razorpay order on the server and returns checkout parameters.

    Security & Integrity:
      - Authenticated users only.
      - Never trusts client-supplied price or credits.
      - Plan ID is looked up on the server.
      - Returns only public checkout parameters (Key ID, Order ID, amount, prefill).
    """
    plan_id = None
    if request.content_type == "application/json":
        try:
            body_data = json.loads(request.body.decode("utf-8")) if request.body else {}
            plan_id = body_data.get("plan_id")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({
                "success": False,
                "error": "Invalid JSON request body format."
            }, status=400)
    else:
        plan_id = request.POST.get("plan_id")

    if not plan_id:
        return JsonResponse({
            "success": False,
            "error": "Field 'plan_id' is required to initiate a credit recharge order."
        }, status=400)

    try:
        checkout_payload = create_razorpay_payment_order(user=request.user, plan_id=plan_id)
        return JsonResponse({
            "success": True,
            "order": checkout_payload
        }, status=201)
    except PaymentGatewayConfigurationError as exc:
        logger.error(f"Gateway configuration issue: {exc}")
        return JsonResponse({
            "success": False,
            "error_type": "configuration_error",
            "error": str(exc)
        }, status=503)
    except PaymentOrderError as exc:
        logger.warning(f"Payment order creation rejected for user {request.user.username}: {exc}")
        return JsonResponse({
            "success": False,
            "error_type": "order_error",
            "error": str(exc)
        }, status=400)
    except Exception as exc:
        logger.exception(f"Unexpected error in create_payment_order: {exc}")
        return JsonResponse({
            "success": False,
            "error_type": "server_error",
            "error": "An unexpected error occurred while initiating your order. Please try again later."
        }, status=500)


@login_required(login_url="login")
@require_POST
def verify_payment_order(request):
    """
    Verifies Razorpay payment signature and capture status, then credits wallet.

    Security Rules:
      - Authenticated users only.
      - Never trust order ID sent from browser without matching request.user's stored order.
      - Never trust a client-side status.
      - Remote payment status verification via Razorpay REST API.
      - Atomic, idempotent SQLite wallet update.
    """
    razorpay_order_id = ""
    razorpay_payment_id = ""
    razorpay_signature = ""

    if request.content_type == "application/json":
        try:
            body_data = json.loads(request.body.decode("utf-8")) if request.body else {}
            razorpay_order_id = body_data.get("razorpay_order_id", "").strip()
            razorpay_payment_id = body_data.get("razorpay_payment_id", "").strip()
            razorpay_signature = body_data.get("razorpay_signature", "").strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"success": False, "error": "Invalid JSON request payload."}, status=400)
    else:
        razorpay_order_id = request.POST.get("razorpay_order_id", "").strip()
        razorpay_payment_id = request.POST.get("razorpay_payment_id", "").strip()
        razorpay_signature = request.POST.get("razorpay_signature", "").strip()

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return JsonResponse({
            "success": False,
            "error": "Missing required payment verification parameters (order_id, payment_id, signature)."
        }, status=400)

    # SECURE LOOKUP: match to authenticated user's stored order!
    order = PaymentOrder.objects.filter(
        razorpay_order_id=razorpay_order_id,
        user=request.user
    ).first()

    if not order:
        logger.warning(
            f"Payment verification rejected: Order {razorpay_order_id} not found for user {request.user.username}"
        )
        return JsonResponse({
            "success": False,
            "error": "Order not found or unauthorized. Payment verification rejected."
        }, status=404)

    try:
        success, message, tx, status_code = credit_wallet_for_verified_order(
            order=order,
            payment_id=razorpay_payment_id,
            signature=razorpay_signature
        )

        request.user.profile.refresh_from_db()
        return JsonResponse({
            "success": success,
            "status": status_code,
            "message": message,
            "order_id": order.razorpay_order_id,
            "credits_granted": order.credits_to_grant if success else 0,
            "wallet_balance": float(request.user.profile.wallet_balance),
        }, status=200)

    except PaymentVerificationError as exc:
        logger.warning(f"Payment verification error for order {order.razorpay_order_id}: {exc}")
        return JsonResponse({
            "success": False,
            "status": "failed",
            "error": str(exc)
        }, status=400)
    except Exception as exc:
        logger.exception(f"Unexpected error in verify_payment_order: {exc}")
        return JsonResponse({
            "success": False,
            "status": "error",
            "error": "An internal error occurred during payment verification. Please check order status or contact support."
        }, status=500)


@login_required(login_url="login")
@require_GET
def check_order_status(request, order_id):
    """
    Returns current status and credit state of a user's stored payment order.
    """
    order = PaymentOrder.objects.filter(
        razorpay_order_id=order_id,
        user=request.user
    ).first()

    if not order:
        return JsonResponse({"success": False, "error": "Order not found."}, status=404)

    request.user.profile.refresh_from_db()
    return JsonResponse({
        "success": True,
        "order_id": order.razorpay_order_id,
        "plan_name": order.plan_name,
        "amount_rupees": float(order.amount_in_rupees),
        "credits": order.credits_to_grant,
        "status": order.status,
        "status_display": order.get_status_display(),
        "credited_at": order.credited_at.isoformat() if order.credited_at else None,
        "failure_reason": order.failure_reason,
        "wallet_balance": float(request.user.profile.wallet_balance),
    })


@login_required(login_url="login")
@require_POST
def reconcile_order(request, order_id):
    """
    Directly queries Razorpay to reconcile any captured payments for this order.
    Enables safe manual recovery for interrupted or pending checkouts on localhost.
    """
    order = PaymentOrder.objects.filter(
        razorpay_order_id=order_id,
        user=request.user
    ).first()

    if not order:
        return JsonResponse({"success": False, "error": "Order not found."}, status=404)

    try:
        success, message, tx, status_code = reconcile_payment_order(order=order)
        request.user.profile.refresh_from_db()
        return JsonResponse({
            "success": success,
            "status": status_code,
            "message": message,
            "wallet_balance": float(request.user.profile.wallet_balance),
        })
    except Exception as exc:
        logger.exception(f"Reconciliation error for order {order_id}: {exc}")
        return JsonResponse({
            "success": False,
            "error": f"Failed to reconcile order: {str(exc)}"
        }, status=500)


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """
    Receives and processes asynchronous Razorpay webhooks.
    Validates HMAC signature and reconciles capture events idempotently.
    """
    signature = request.headers.get("X-Razorpay-Signature") or request.META.get("HTTP_X_RAZORPAY_SIGNATURE")
    if not signature:
        logger.warning("Rejected webhook request missing X-Razorpay-Signature header.")
        return HttpResponse("Missing signature header", status=400)

    try:
        success, msg = process_razorpay_webhook(
            raw_body=request.body,
            signature=signature
        )
        return HttpResponse(msg, status=200)
    except PaymentGatewayConfigurationError as exc:
        logger.error(f"Webhook configuration issue: {exc}")
        return HttpResponse(str(exc), status=503)
    except PaymentVerificationError as exc:
        logger.warning(f"Webhook verification rejected: {exc}")
        return HttpResponse(str(exc), status=400)
    except Exception as exc:
        logger.exception(f"Unhandled exception in razorpay_webhook: {exc}")
        return HttpResponse("Internal webhook processing error", status=500)


