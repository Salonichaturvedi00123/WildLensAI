from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class Profile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile"
    )

    profile_image = models.ImageField(
        upload_to="profile_images/",
        default="profile_images/default.png",
        blank=True
    )

    phone = models.CharField(max_length=15, blank=True)
    address = models.TextField(blank=True)

    wallet_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00")
    )

    total_predictions = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.user.username if self.user else f"Profile #{self.pk}"


class CreditPlan(models.Model):
    """
    Admin-manageable credit plans for Razorpay wallet recharges.
    """
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True, default="")
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Price in INR (₹)"
    )
    credits = models.PositiveIntegerField(
        help_text="Number of wildlife identification credits granted"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this plan is visible and purchasable by users"
    )
    display_order = models.PositiveIntegerField(
        default=0,
        help_text="Display order in list views (lowest numbers first)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "price"]
        verbose_name = "Credit Plan"
        verbose_name_plural = "Credit Plans"

    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"{self.name} — {self.credits} Credits for ₹{self.price:.2f} ({status})"

    @property
    def price_in_paise(self) -> int:
        """Calculate amount in integer paise for Razorpay Orders API."""
        return int(self.price * 100)


class PaymentOrder(models.Model):
    """
    Razorpay payment order record preserving immutable snapshots of plan,
    price in paise, credits, and gateway status.
    """
    STATUS_CHOICES = [
        ("created", "Created"),
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="payment_orders"
    )
    plan = models.ForeignKey(
        CreditPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders"
    )

    # Immutable snapshot fields at the time order was initiated
    plan_name = models.CharField(
        max_length=100,
        help_text="Snapshot of the plan name at time of order creation"
    )
    credits_to_grant = models.PositiveIntegerField(
        help_text="Snapshot of credits to grant upon verified payment"
    )
    amount_paise = models.PositiveIntegerField(
        help_text="Amount in integer paise (e.g. 5000 paise = ₹50.00)"
    )
    currency = models.CharField(
        max_length=10,
        default="INR"
    )

    # Razorpay Gateway Identifiers
    razorpay_order_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Unique Razorpay Order ID (e.g., order_...)"
    )
    razorpay_payment_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        db_index=True,
        help_text="Razorpay Payment ID assigned once user attempts payment"
    )
    razorpay_signature = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Cryptographic signature provided by Razorpay on checkout completion"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="created",
        db_index=True
    )
    failure_reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reason for failure or pending verification status"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    credited_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when credits were actually credited to user's wallet"
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Payment Order"
        verbose_name_plural = "Payment Orders"

    def __str__(self):
        return f"Order {self.razorpay_order_id} ({self.user.username}) — ₹{self.amount_in_rupees:.2f} [{self.get_status_display()}]"

    @property
    def amount_in_rupees(self) -> Decimal:
        """Returns the order amount in INR (₹)."""
        return Decimal(self.amount_paise) / Decimal("100.00")


class WalletTransaction(models.Model):
    TRANSACTION_TYPES = [
        ("signup_bonus", "Sign-up Bonus"),
        ("prediction_charge", "Prediction Charge"),
        ("admin_grant", "Admin Grant"),
        ("refund", "Refund"),
        ("demo_recharge", "Demo Recharge"),
        ("credit_purchase", "Credit Purchase (Razorpay)"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="wallet_transactions"
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Signed transaction amount (positive for additions, negative for deductions)"
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=TRANSACTION_TYPES,
        default="prediction_charge"
    )
    balance_after = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )
    prediction = models.ForeignKey(
        "animal.AnimalPrediction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions"
    )
    payment_order = models.ForeignKey(
        "PaymentOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transactions"
    )
    request_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        db_index=True
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        prefix = "+" if self.amount > 0 else ""
        return f"{self.user.username}: {self.get_transaction_type_display()} ({prefix}₹{self.amount:.2f})"


class RazorpayWebhookEvent(models.Model):
    """
    Audit log for processed Razorpay webhooks with unique event_id constraint
    to guarantee idempotency and replay-attack resistance.
    """
    event_id = models.CharField(max_length=120, unique=True, db_index=True)
    event_type = models.CharField(max_length=100)
    payment_order = models.ForeignKey(
        PaymentOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events"
    )
    payload = models.TextField(blank=True, default="")
    processed_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=True)
    message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-processed_at"]
        verbose_name = "Razorpay Webhook Event"
        verbose_name_plural = "Razorpay Webhook Events"

    def __str__(self):
        status_text = "Success" if self.success else "Failed"
        return f"{self.event_type} ({self.event_id}) — {status_text}"


@receiver(post_save, sender=User)
def create_or_save_user_profile(sender, instance, created, **kwargs):
    if created:
        profile, was_created = Profile.objects.get_or_create(
            user=instance,
            defaults={'wallet_balance': Decimal("5.00")}
        )
        if not WalletTransaction.objects.filter(user=instance, transaction_type="signup_bonus").exists():
            WalletTransaction.objects.create(
                user=instance,
                amount=Decimal("5.00"),
                transaction_type="signup_bonus",
                balance_after=profile.wallet_balance if profile else Decimal("5.00"),
                description="Welcome promotional credit grant (5 free predictions)"
            )
