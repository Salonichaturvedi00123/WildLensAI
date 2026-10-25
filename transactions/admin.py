from decimal import Decimal
from django.contrib import admin, messages
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, GroupAdmin as BaseGroupAdmin
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.decorators import display, action

from .models import Profile, WalletTransaction, CreditPlan, PaymentOrder, RazorpayWebhookEvent
from .services import grant_demo_credits


# ---------------------------------------------------------------------------
# Administrative Credit Grant Actions (Preserved & Enhanced)
# ---------------------------------------------------------------------------

@action(description="Grant 5 Demo Credits to selected users/profiles")
def grant_5_demo_credits(modeladmin, request, queryset):
    count = 0
    for item in queryset:
        target_user = item if isinstance(item, User) else getattr(item, "user", None)
        if target_user:
            grant_demo_credits(
                user=target_user,
                amount=Decimal("5.00"),
                description="Admin batch grant (+5 demo credits)",
                tx_type="admin_grant"
            )
            count += 1
    messages.success(request, f"Successfully granted 5 demo credits to {count} user account(s).")


@action(description="Grant 10 Demo Credits to selected users/profiles")
def grant_10_demo_credits(modeladmin, request, queryset):
    count = 0
    for item in queryset:
        target_user = item if isinstance(item, User) else getattr(item, "user", None)
        if target_user:
            grant_demo_credits(
                user=target_user,
                amount=Decimal("10.00"),
                description="Admin batch grant (+10 demo credits)",
                tx_type="admin_grant"
            )
            count += 1
    messages.success(request, f"Successfully granted 10 demo credits to {count} user account(s).")


# ---------------------------------------------------------------------------
# User & Profile Administration (Unfold Modernized)
# ---------------------------------------------------------------------------

class ProfileInline(StackedInline):
    model = Profile
    can_delete = False
    readonly_fields = ("total_predictions", "created_at")
    fields = ("wallet_balance", "total_predictions", "phone", "address", "profile_image", "created_at")


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    inlines = [ProfileInline]
    actions = [grant_5_demo_credits, grant_10_demo_credits]

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "wallet_credits_display",
        "identifications_count_display",
        "is_staff",
        "is_active",
        "date_joined",
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "date_joined")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("-date_joined",)

    @display(description="Wallet Balance", ordering="profile__wallet_balance")
    def wallet_credits_display(self, obj):
        bal = getattr(obj.profile, "wallet_balance", Decimal("0.00")) if hasattr(obj, "profile") else Decimal("0.00")
        return f"₹{bal:.2f}"

    @display(description="Identifications", ordering="profile__total_predictions")
    def identifications_count_display(self, obj):
        return getattr(obj.profile, "total_predictions", 0) if hasattr(obj, "profile") else 0


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(Profile)
class ProfileAdmin(ModelAdmin):
    list_display = (
        "user",
        "wallet_balance_display",
        "total_predictions",
        "phone",
        "created_at",
    )
    search_fields = ("user__username", "user__email", "phone")
    list_filter = ("created_at",)
    ordering = ("-created_at",)
    actions = [grant_5_demo_credits, grant_10_demo_credits]

    @display(description="Balance", ordering="wallet_balance")
    def wallet_balance_display(self, obj):
        return f"₹{obj.wallet_balance:.2f}"


# ---------------------------------------------------------------------------
# Credit Plans Administration
# ---------------------------------------------------------------------------

@admin.register(CreditPlan)
class CreditPlanAdmin(ModelAdmin):
    list_display = (
        "name",
        "price_display",
        "credits",
        "status_badge",
        "display_order",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "description")
    list_editable = ("display_order",)
    ordering = ("display_order", "price")

    @display(description="Price (INR)", ordering="price")
    def price_display(self, obj):
        return f"₹{obj.price:.2f}"

    @display(
        description="Active Status",
        label={
            True: "success",
            False: "danger",
        }
    )
    def status_badge(self, obj):
        return "Active" if obj.is_active else "Inactive"


# ---------------------------------------------------------------------------
# Payment Orders Administration (Strictly Audited & Protected)
# ---------------------------------------------------------------------------

@admin.register(PaymentOrder)
class PaymentOrderAdmin(ModelAdmin):
    list_display = (
        "razorpay_order_id",
        "user",
        "plan_name",
        "amount_display",
        "credits_to_grant",
        "status_badge",
        "razorpay_payment_id",
        "created_at",
        "credited_at",
    )
    list_filter = ("status", "currency", "created_at")
    search_fields = (
        "razorpay_order_id",
        "razorpay_payment_id",
        "user__username",
        "user__email",
        "plan_name",
    )
    readonly_fields = (
        "user",
        "plan",
        "plan_name",
        "credits_to_grant",
        "amount_paise",
        "currency",
        "razorpay_order_id",
        "razorpay_payment_id",
        "razorpay_signature",
        "status",
        "failure_reason",
        "created_at",
        "updated_at",
        "credited_at",
    )
    ordering = ("-created_at",)

    @display(description="Amount", ordering="amount_paise")
    def amount_display(self, obj):
        return f"₹{obj.amount_in_rupees:.2f}"

    @display(
        description="Gateway Status",
        label={
            "paid": "success",
            "pending": "warning",
            "created": "info",
            "failed": "danger",
        }
    )
    def status_badge(self, obj):
        return obj.status

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Razorpay Webhook Events Audit
# ---------------------------------------------------------------------------

@admin.register(RazorpayWebhookEvent)
class RazorpayWebhookEventAdmin(ModelAdmin):
    list_display = (
        "event_id",
        "event_type",
        "payment_order",
        "status_badge",
        "processed_at",
    )
    list_filter = ("event_type", "success", "processed_at")
    search_fields = ("event_id", "event_type", "message")
    readonly_fields = (
        "event_id",
        "event_type",
        "payment_order",
        "payload",
        "success",
        "message",
        "processed_at",
    )
    ordering = ("-processed_at",)

    @display(
        description="Outcome",
        label={
            True: "success",
            False: "danger",
        }
    )
    def status_badge(self, obj):
        return "Processed" if obj.success else "Failed"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Wallet Transaction Ledger Administration (Immutable Audit Trail)
# ---------------------------------------------------------------------------

@admin.register(WalletTransaction)
class WalletTransactionAdmin(ModelAdmin):
    list_display = (
        "id",
        "user",
        "transaction_type_badge",
        "amount_display",
        "balance_after_display",
        "payment_order",
        "prediction",
        "request_id",
        "created_at",
    )
    list_filter = ("transaction_type", "created_at")
    search_fields = ("user__username", "description", "request_id")
    ordering = ("-created_at",)
    readonly_fields = (
        "user",
        "amount",
        "transaction_type",
        "balance_after",
        "prediction",
        "payment_order",
        "request_id",
        "description",
        "created_at",
    )

    @display(
        description="Type",
        label={
            "credit_purchase": "success",
            "signup_bonus": "primary",
            "admin_grant": "warning",
            "prediction_charge": "secondary",
            "refund": "info",
        }
    )
    def transaction_type_badge(self, obj):
        return obj.transaction_type

    @display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        prefix = "+" if obj.amount > 0 else ""
        return f"{prefix}₹{obj.amount:.2f}"

    @display(description="Balance After", ordering="balance_after")
    def balance_after_display(self, obj):
        return f"₹{obj.balance_after:.2f}"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
