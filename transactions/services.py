import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from .models import Profile, WalletTransaction

logger = logging.getLogger(__name__)


import logging
import time
import uuid
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db import transaction, IntegrityError
from django.db.models import F
from .models import Profile, WalletTransaction, CreditPlan, PaymentOrder, RazorpayWebhookEvent

logger = logging.getLogger(__name__)


class InsufficientCreditsError(Exception):
    """Raised when an account lacks the required credits for a paid transaction."""
    pass


class PaymentGatewayError(Exception):
    """Base exception for payment gateway operations."""
    pass


class PaymentGatewayConfigurationError(PaymentGatewayError):
    """Raised when Razorpay configuration keys are missing or invalid."""
    pass


class PaymentOrderError(PaymentGatewayError):
    """Raised when order creation or validation fails."""
    pass


class PaymentVerificationError(PaymentGatewayError):
    """Raised when payment signature verification or order crediting fails."""
    pass


def is_razorpay_configured() -> bool:
    """
    Check if valid Razorpay API keys are configured in Django settings.
    Rejects missing, empty, or placeholder dummy credentials.
    """
    key_id = (getattr(settings, "RAZORPAY_KEY_ID", "") or "").strip()
    key_secret = (getattr(settings, "RAZORPAY_KEY_SECRET", "") or "").strip()
    if not key_id or not key_secret:
        return False
    if "placeholder" in key_id.lower() or "placeholder" in key_secret.lower():
        return False
    return True


def get_razorpay_client(validate_configured: bool = True):
    """
    Initializes and returns a Razorpay Client instance using configured keys.

    Args:
        validate_configured: When True, checks that credentials are not placeholders.
    """
    key_id = (getattr(settings, "RAZORPAY_KEY_ID", "") or "").strip()
    key_secret = (getattr(settings, "RAZORPAY_KEY_SECRET", "") or "").strip()

    if validate_configured and not is_razorpay_configured():
        raise PaymentGatewayConfigurationError(
            "Razorpay API credentials are not properly configured. "
            "Please configure RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in environment variables."
        )

    if not key_id or not key_secret:
        raise PaymentGatewayConfigurationError(
            "Razorpay Key ID and Secret must be non-empty."
        )

    import razorpay
    return razorpay.Client(auth=(key_id, key_secret))


def create_razorpay_payment_order(user, plan_id, client=None) -> dict:
    """
    Validates the credit plan, invokes Razorpay API to create an order,
    and records an immutable PaymentOrder record with status='created'.

    CRITICAL RULES:
      - Never trust client-provided prices or credit quantities.
      - Calculate amount in paise exclusively on the server from the database plan.
      - The external Razorpay network request is executed OUTSIDE any DB transaction.
      - NO CREDITS are added to the user wallet at order creation time.

    Returns:
      Dict with public checkout parameters for Razorpay Checkout.
    """
    if not user or not user.is_authenticated:
        raise PaymentOrderError("An authenticated user is required to initiate an order.")

    try:
        plan = CreditPlan.objects.get(id=plan_id, is_active=True)
    except (CreditPlan.DoesNotExist, ValueError, TypeError):
        raise PaymentOrderError(f"Credit plan with ID '{plan_id}' does not exist or is inactive.")

    amount_paise = plan.price_in_paise
    if amount_paise <= 0:
        raise PaymentOrderError(f"Invalid plan amount ({amount_paise} paise). Plan price must be positive.")

    # Unique receipt identifier (max 40 chars)
    receipt = f"rcpt_{user.id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"[:40]

    # Initialize Razorpay Client and invoke order creation outside database transaction
    rzp_client = client if client is not None else get_razorpay_client(validate_configured=True)

    order_payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "notes": {
            "user_id": str(user.id),
            "username": user.username,
            "plan_id": str(plan.id),
            "plan_name": plan.name,
            "credits": str(plan.credits),
        },
        "payment_capture": 1
    }

    try:
        rzp_order = rzp_client.order.create(data=order_payload)
    except Exception as exc:
        logger.exception(f"Razorpay order creation API request failed for user {user.username}: {exc}")
        raise PaymentOrderError(f"Failed to create order with Razorpay: {str(exc)}") from exc

    razorpay_order_id = rzp_order.get("id")
    if not razorpay_order_id:
        raise PaymentOrderError("Razorpay returned an invalid response without an order ID.")

    # Persist immutable snapshot in database
    order = PaymentOrder.objects.create(
        user=user,
        plan=plan,
        plan_name=plan.name,
        credits_to_grant=plan.credits,
        amount_paise=amount_paise,
        currency="INR",
        razorpay_order_id=razorpay_order_id,
        status="created"
    )

    logger.info(
        f"Created PaymentOrder #{order.id} ({razorpay_order_id}) for {user.username}: "
        f"{plan.credits} credits for ₹{order.amount_in_rupees:.2f}"
    )

    # Return safe public parameters for frontend Razorpay checkout
    user_contact = ""
    if hasattr(user, "profile") and user.profile and user.profile.phone:
        user_contact = user.profile.phone

    return {
        "order_db_id": order.id,
        "razorpay_order_id": order.razorpay_order_id,
        "amount": order.amount_paise,
        "amount_in_rupees": float(order.amount_in_rupees),
        "currency": order.currency,
        "key_id": getattr(settings, "RAZORPAY_KEY_ID", ""),
        "plan_name": order.plan_name,
        "credits": order.credits_to_grant,
        "prefill": {
            "name": f"{user.first_name} {user.last_name}".strip() or user.username,
            "email": user.email or "",
            "contact": user_contact,
        },
    }


def credit_wallet_for_verified_order(
    order: PaymentOrder,
    payment_id: str,
    signature: str,
    client=None,
    verify_remote_capture: bool = True
):
    """
    Verifies Razorpay payment signature and remote capture status, then atomically
    credits the user's wallet.

    Verification Steps:
      1. Cryptographic HMAC signature verification (via Razorpay SDK).
      2. Remote Payment Status Verification (via Razorpay REST API):
         - Payment belongs to stored order.
         - Payment ID matches.
         - Currency is INR.
         - Amount exactly equals order.amount_paise.
         - Status is 'captured' (if 'authorized', marks 'pending' without crediting).
      3. SQLite-compatible atomic wallet credit with idempotency protection.

    Returns:
      (success: bool, message: str, transaction: WalletTransaction | None, status: str)
    """
    if not payment_id or not signature:
        raise PaymentVerificationError("Both payment_id and signature are required for payment verification.")

    # 1. Early Idempotency Check: if already credited, return early
    if order.status == "paid" and order.credited_at is not None:
        logger.info(f"PaymentOrder {order.razorpay_order_id} is already paid and credited. Idempotent return.")
        existing_tx = WalletTransaction.objects.filter(
            payment_order=order,
            transaction_type="credit_purchase"
        ).first()
        return True, "Payment has already been verified and credited.", existing_tx, "paid"

    rzp_client = client if client is not None else get_razorpay_client(validate_configured=True)

    # 2. Cryptographic Signature Verification
    signature_params = {
        "razorpay_order_id": order.razorpay_order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": signature
    }

    try:
        rzp_client.utility.verify_payment_signature(signature_params)
    except Exception as exc:
        logger.warning(
            f"Signature verification failed for order {order.razorpay_order_id}: {exc}"
        )
        PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
            status="failed",
            razorpay_payment_id=payment_id,
            razorpay_signature=signature,
            failure_reason="Cryptographic signature verification failed."
        )
        raise PaymentVerificationError("Cryptographic signature verification failed.") from exc

    # 3. Remote Payment Status Verification (Executed OUTSIDE DB Transaction)
    if verify_remote_capture:
        try:
            payment = rzp_client.payment.fetch(payment_id)
        except Exception as exc:
            logger.exception(f"Razorpay payment fetch failed for payment {payment_id}: {exc}")
            raise PaymentVerificationError(f"Could not verify payment with Razorpay: {str(exc)}") from exc

        # Ensure payment response is a dictionary
        if isinstance(payment, dict):
            # Check 3a: Payment belongs to this stored order
            p_order_id = payment.get("order_id")
            if p_order_id and p_order_id != order.razorpay_order_id:
                PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
                    status="failed",
                    razorpay_payment_id=payment_id,
                    razorpay_signature=signature,
                    failure_reason=f"Payment order ID mismatch ({p_order_id} != {order.razorpay_order_id})."
                )
                raise PaymentVerificationError("Payment does not belong to the stored order.")

            # Check 3b: Currency match
            p_currency = str(payment.get("currency", "")).upper()
            if p_currency != order.currency.upper():
                PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
                    status="failed",
                    razorpay_payment_id=payment_id,
                    razorpay_signature=signature,
                    failure_reason=f"Currency mismatch ({p_currency} != {order.currency})."
                )
                raise PaymentVerificationError(f"Currency mismatch: expected {order.currency}, got {p_currency}.")

            # Check 3c: Amount match
            p_amount = int(payment.get("amount", 0))
            if p_amount != order.amount_paise:
                PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
                    status="failed",
                    razorpay_payment_id=payment_id,
                    razorpay_signature=signature,
                    failure_reason=f"Amount mismatch ({p_amount} paise != {order.amount_paise} paise)."
                )
                raise PaymentVerificationError(f"Amount mismatch: expected {order.amount_paise} paise, got {p_amount} paise.")

            # Check 3d: Status must be 'captured'
            p_status = payment.get("status")
            if p_status == "authorized":
                # Authorized but not captured: mark pending, DO NOT grant credits
                PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
                    status="pending",
                    razorpay_payment_id=payment_id,
                    razorpay_signature=signature,
                    failure_reason="Payment authorized; awaiting capture by gateway."
                )
                return False, "Payment is authorized and pending capture by Razorpay. Credits will be granted once captured.", None, "pending"
            elif p_status != "captured":
                PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
                    status="failed",
                    razorpay_payment_id=payment_id,
                    razorpay_signature=signature,
                    failure_reason=f"Payment status is '{p_status}' (not captured)."
                )
                raise PaymentVerificationError(f"Payment is not captured (status: '{p_status}'). Credits cannot be granted.")

    # 4. Atomic conditional transition to prevent duplicate crediting & race conditions
    with transaction.atomic():
        # Ensure this payment_id has never credited another order
        other_credited = PaymentOrder.objects.filter(
            razorpay_payment_id=payment_id,
            status="paid"
        ).exclude(id=order.id).exists()
        if other_credited:
            raise PaymentVerificationError(
                f"Payment ID '{payment_id}' has already been credited to another order."
            )

        updated_rows = PaymentOrder.objects.filter(
            id=order.id
        ).exclude(status="paid").update(
            status="paid",
            razorpay_payment_id=payment_id,
            razorpay_signature=signature,
            failure_reason="",
            credited_at=timezone.now()
        )

        if updated_rows == 0:
            # Another concurrent request or webhook already updated this order
            logger.info(f"Concurrent request already credited order {order.razorpay_order_id}.")
            existing_tx = WalletTransaction.objects.filter(
                payment_order=order,
                transaction_type="credit_purchase"
            ).first()
            return True, "Payment has already been verified and credited.", existing_tx, "paid"

        order.refresh_from_db()

        # Atomically increment user's wallet balance
        profile, _ = Profile.objects.get_or_create(
            user=order.user,
            defaults={"wallet_balance": Decimal("0.00")}
        )

        credits_amount = Decimal(str(order.credits_to_grant))
        Profile.objects.filter(id=profile.id).update(
            wallet_balance=F("wallet_balance") + credits_amount
        )
        profile.refresh_from_db()

        tx = WalletTransaction.objects.create(
            user=order.user,
            amount=credits_amount,
            transaction_type="credit_purchase",
            balance_after=profile.wallet_balance,
            payment_order=order,
            description=f"Razorpay recharge: {order.plan_name} (+{order.credits_to_grant} credits)"
        )

        logger.info(
            f"Successfully credited {credits_amount} credits to {order.user.username} for "
            f"order {order.razorpay_order_id}. New balance: {profile.wallet_balance}. Tx ID: {tx.id}"
        )
        return True, f"Successfully added {order.credits_to_grant} credits to your account.", tx, "paid"


def reconcile_payment_order(order: PaymentOrder, client=None):
    """
    Checks with Razorpay API for any payments associated with an order
    and updates/reconciles status and credits if captured.
    Safe for localhost demonstration and recovering interrupted payments.

    Returns:
      (success: bool, message: str, transaction: WalletTransaction | None, status: str)
    """
    if order.status == "paid" and order.credited_at is not None:
        existing_tx = WalletTransaction.objects.filter(
            payment_order=order,
            transaction_type="credit_purchase"
        ).first()
        return True, "Order is already paid and credited.", existing_tx, "paid"

    rzp_client = client if client is not None else get_razorpay_client(validate_configured=True)

    try:
        payments_data = rzp_client.order.fetch_payments(order.razorpay_order_id)
    except Exception as exc:
        logger.warning(f"Failed to fetch order payments from Razorpay for {order.razorpay_order_id}: {exc}")
        return False, f"Could not contact Razorpay: {str(exc)}", None, order.status

    items = payments_data.get("items", []) if isinstance(payments_data, dict) else []
    if not items:
        return False, "No payment attempts found for this order on Razorpay.", None, order.status

    # Check for captured payments first
    for p in items:
        p_status = p.get("status")
        p_id = p.get("id")
        p_amount = int(p.get("amount", 0))
        p_currency = str(p.get("currency", "")).upper()

        if p_status == "captured" and p_amount == order.amount_paise and p_currency == order.currency.upper():
            with transaction.atomic():
                existing_other = PaymentOrder.objects.filter(
                    razorpay_payment_id=p_id,
                    status="paid"
                ).exclude(id=order.id).exists()
                if existing_other:
                    return False, f"Payment {p_id} was already credited to another order.", None, "failed"

                updated = PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
                    status="paid",
                    razorpay_payment_id=p_id,
                    credited_at=timezone.now(),
                    failure_reason=""
                )
                if updated == 0:
                    existing_tx = WalletTransaction.objects.filter(
                        payment_order=order,
                        transaction_type="credit_purchase"
                    ).first()
                    return True, "Order already credited.", existing_tx, "paid"

                profile, _ = Profile.objects.get_or_create(user=order.user)
                credits_amount = Decimal(str(order.credits_to_grant))
                Profile.objects.filter(id=profile.id).update(wallet_balance=F("wallet_balance") + credits_amount)
                profile.refresh_from_db()

                tx = WalletTransaction.objects.create(
                    user=order.user,
                    amount=credits_amount,
                    transaction_type="credit_purchase",
                    balance_after=profile.wallet_balance,
                    payment_order=order,
                    description=f"Razorpay reconciliation: {order.plan_name} (+{order.credits_to_grant} credits)"
                )
                return True, f"Payment reconciled. {order.credits_to_grant} credits added to your wallet.", tx, "paid"

    # Check for authorized payments (pending capture)
    for p in items:
        if p.get("status") == "authorized":
            PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
                status="pending",
                razorpay_payment_id=p.get("id"),
                failure_reason="Payment authorized; awaiting capture by gateway."
            )
            return False, "Payment is authorized and pending capture by the gateway.", None, "pending"

    # All attempts failed
    PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
        status="failed",
        failure_reason="All payment attempts on Razorpay failed."
    )
    return False, "Payment attempts on Razorpay were unsuccessful.", None, "failed"


def process_razorpay_webhook(raw_body: bytes, signature: str, secret: str = None, client=None):
    """
    Verifies webhook HMAC signature, records idempotent webhook event log,
    and reconciles payment.captured or order.paid events.
    """
    webhook_secret = (secret or getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "") or "").strip()
    if not webhook_secret:
        raise PaymentGatewayConfigurationError("RAZORPAY_WEBHOOK_SECRET is not configured in settings.")

    rzp_client = client if client is not None else get_razorpay_client(validate_configured=False)

    try:
        body_text = raw_body.decode("utf-8")
        rzp_client.utility.verify_webhook_signature(body_text, signature, webhook_secret)
    except Exception as exc:
        logger.warning(f"Razorpay webhook HMAC signature verification failed: {exc}")
        raise PaymentVerificationError("Invalid webhook signature.") from exc

    import json
    try:
        event_data = json.loads(body_text)
    except Exception as exc:
        raise PaymentVerificationError(f"Malformed webhook JSON body: {exc}") from exc

    event_id = event_data.get("id") or f"evt_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    event_type = event_data.get("event", "unknown")

    # Idempotency check using unique event_id
    if RazorpayWebhookEvent.objects.filter(event_id=event_id).exists():
        logger.info(f"Webhook event {event_id} has already been processed. Skipping duplicate.")
        return True, "Webhook event already processed (idempotent duplicate)."

    try:
        with transaction.atomic():
            webhook_log = RazorpayWebhookEvent.objects.create(
                event_id=event_id,
                event_type=event_type,
                payload=body_text[:5000],
                success=True,
                message="Event received and queued for processing."
            )
    except IntegrityError:
        logger.info(f"Webhook event {event_id} duplicate caught on insert. Skipping duplicate.")
        return True, "Webhook event already processed (idempotent duplicate)."

    # Extract payment and order entity
    payload_entity = event_data.get("payload", {})
    payment_entity = payload_entity.get("payment", {}).get("entity", {})
    order_entity = payload_entity.get("order", {}).get("entity", {})

    order_id = payment_entity.get("order_id") or order_entity.get("id")
    payment_id = payment_entity.get("id")

    if not order_id:
        webhook_log.message = "No order_id found in webhook payload."
        webhook_log.save(update_fields=["message"])
        return True, "Ignored: no order_id in event."

    order = PaymentOrder.objects.filter(razorpay_order_id=order_id).first()
    if not order:
        webhook_log.message = f"Order {order_id} not found in database."
        webhook_log.save(update_fields=["message"])
        return True, f"Order {order_id} not found."

    webhook_log.payment_order = order
    webhook_log.save(update_fields=["payment_order"])

    if order.status == "paid":
        webhook_log.message = f"Order {order.razorpay_order_id} was already credited."
        webhook_log.save(update_fields=["message"])
        return True, "Order already credited."

    if event_type in ["payment.captured", "order.paid"]:
        p_amount = int(payment_entity.get("amount", order.amount_paise))
        p_curr = str(payment_entity.get("currency", "INR")).upper()

        if p_amount == order.amount_paise and p_curr == order.currency.upper():
            with transaction.atomic():
                existing_other = PaymentOrder.objects.filter(
                    razorpay_payment_id=payment_id,
                    status="paid"
                ).exclude(id=order.id).exists()
                if existing_other:
                    webhook_log.success = False
                    webhook_log.message = "Payment ID already associated with another paid order."
                    webhook_log.save(update_fields=["success", "message"])
                    return False, "Duplicate payment ID."

                updated = PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
                    status="paid",
                    razorpay_payment_id=payment_id,
                    credited_at=timezone.now(),
                    failure_reason=""
                )
                if updated > 0:
                    profile, _ = Profile.objects.get_or_create(user=order.user)
                    credits_amount = Decimal(str(order.credits_to_grant))
                    Profile.objects.filter(id=profile.id).update(wallet_balance=F("wallet_balance") + credits_amount)
                    profile.refresh_from_db()

                    WalletTransaction.objects.create(
                        user=order.user,
                        amount=credits_amount,
                        transaction_type="credit_purchase",
                        balance_after=profile.wallet_balance,
                        payment_order=order,
                        description=f"Razorpay webhook recharge: {order.plan_name} (+{order.credits_to_grant} credits)"
                    )
                    webhook_log.message = f"Successfully credited {order.credits_to_grant} credits via webhook."
                    webhook_log.save(update_fields=["message"])
                    return True, "Successfully credited."

    elif event_type == "payment.failed":
        error_desc = (
            payment_entity.get("error_description")
            or payload_entity.get("error", {}).get("description")
            or "Payment attempt failed on Razorpay gateway."
        )
        PaymentOrder.objects.filter(id=order.id).exclude(status="paid").update(
            status="failed",
            failure_reason=error_desc
        )
        webhook_log.message = f"Order marked failed: {error_desc}"
        webhook_log.save(update_fields=["message"])
        return True, f"Payment failed: {error_desc}"

    return True, f"Webhook event {event_type} handled."



def deduct_prediction_credit(user, prediction=None, request_id=None, cost=Decimal("1.00")):
    """
    Atomically deduct prediction credits using Django F expressions within transaction.atomic().

    Guarantees:
      - Strictly prevents negative balances using `wallet_balance__gte=cost`.
      - Prevents duplicate deductions using `request_id` check.
      - Never runs during external network/API operations.
      - Supports zero-cost deductions (e.g. non-animal images).

    Returns:
      (success: bool, transaction: WalletTransaction | None, message: str)
    """
    cost = Decimal(str(cost))

    # 1. Zero cost bypass (non-animal or promotional free request)
    if cost <= Decimal("0.00"):
        return True, None, "Zero credit deduction."

    # 2. Idempotency Check: Don't charge twice for the exact same request_id
    if request_id:
        existing_tx = WalletTransaction.objects.filter(
            user=user,
            request_id=request_id,
            transaction_type="prediction_charge"
        ).first()
        if existing_tx:
            return True, existing_tx, "Request already charged."

    profile = Profile.objects.filter(user=user).first()
    if not profile:
        return False, None, "User profile not found."

    # 3. Atomic Conditional Balance Deduction using F expression
    with transaction.atomic():
        rows_updated = Profile.objects.filter(
            id=profile.id,
            wallet_balance__gte=cost
        ).update(
            wallet_balance=F("wallet_balance") - cost,
            total_predictions=F("total_predictions") + 1
        )

        if rows_updated == 0:
            return False, None, f"Insufficient wallet credits (Cost: ₹{cost:.2f})."

        profile.refresh_from_db()

        species_name = prediction.common_name if prediction else "wildlife specimen"
        tx = WalletTransaction.objects.create(
            user=user,
            amount=-cost,
            transaction_type="prediction_charge",
            balance_after=profile.wallet_balance,
            prediction=prediction,
            request_id=request_id,
            description=f"Identification fee for {species_name}"
        )

        logger.info(
            f"Deducted {cost} credit from {user.username}. Balance after: {profile.wallet_balance}. Tx ID: {tx.id}"
        )
        return True, tx, ""


def grant_demo_credits(user, amount=Decimal("5.00"), description="Admin demo credit grant", tx_type="admin_grant"):
    """
    Atomically grant demo credits to a user profile and record a ledger entry.

    Returns:
      WalletTransaction instance
    """
    amount = Decimal(str(amount))
    if amount <= Decimal("0.00"):
        raise ValueError("Credit grant amount must be positive.")

    profile, _ = Profile.objects.get_or_create(
        user=user,
        defaults={"wallet_balance": Decimal("0.00")}
    )

    with transaction.atomic():
        Profile.objects.filter(id=profile.id).update(
            wallet_balance=F("wallet_balance") + amount
        )
        profile.refresh_from_db()

        tx = WalletTransaction.objects.create(
            user=user,
            amount=amount,
            transaction_type=tx_type,
            balance_after=profile.wallet_balance,
            description=description
        )

        logger.info(
            f"Granted {amount} credits to {user.username}. New balance: {profile.wallet_balance}. Tx ID: {tx.id}"
        )
        return tx

