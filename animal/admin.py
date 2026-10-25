from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import AnimalPrediction, WildlifeConversation, WildlifeMessage, Contact


# ---------------------------------------------------------------------------
# Animal Prediction Administration
# ---------------------------------------------------------------------------

@admin.register(AnimalPrediction)
class AnimalPredictionAdmin(ModelAdmin):
    list_display = (
        "common_name",
        "scientific_name",
        "user",
        "status_badge",
        "confidence_display",
        "credit_used_display",
        "request_id",
        "created_at",
    )
    list_filter = ("identification_status", "created_at")
    search_fields = (
        "common_name",
        "scientific_name",
        "user__username",
        "identification_notes",
        "request_id",
    )
    readonly_fields = (
        "user",
        "uploaded_image",
        "confidence",
        "credit_used",
        "identification_status",
        "identification_notes",
        "visual_clues",
        "alternative_species",
        "wildlife_information",
        "source_references",
        "request_id",
        "created_at",
    )
    ordering = ("-created_at",)

    @display(
        description="Identification Status",
        label={
            "identified": "success",
            "uncertain": "warning",
            "not_animal": "danger",
        }
    )
    def status_badge(self, obj):
        return obj.identification_status

    @display(description="Confidence", ordering="confidence")
    def confidence_display(self, obj):
        return f"{obj.confidence:.1f}%"

    @display(description="Credits", ordering="credit_used")
    def credit_used_display(self, obj):
        return f"₹{obj.credit_used:.2f}"


# ---------------------------------------------------------------------------
# Wildlife Conversations & Messages Administration
# ---------------------------------------------------------------------------

class WildlifeMessageInline(TabularInline):
    model = WildlifeMessage
    extra = 0
    readonly_fields = ("role", "content", "created_at")
    can_delete = False


@admin.register(WildlifeConversation)
class WildlifeConversationAdmin(ModelAdmin):
    list_display = (
        "title",
        "user",
        "prediction",
        "messages_count_display",
        "created_at",
        "updated_at",
    )
    list_filter = ("created_at", "updated_at")
    search_fields = ("title", "user__username")
    ordering = ("-updated_at",)
    inlines = [WildlifeMessageInline]

    @display(description="Messages Count")
    def messages_count_display(self, obj):
        return obj.messages.count()


@admin.register(WildlifeMessage)
class WildlifeMessageAdmin(ModelAdmin):
    list_display = ("conversation", "role_badge", "content_preview", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("content", "conversation__title", "conversation__user__username")
    readonly_fields = ("conversation", "role", "content", "created_at")
    ordering = ("-created_at",)

    @display(
        description="Sender Role",
        label={
            "user": "info",
            "assistant": "success",
            "system": "secondary",
        }
    )
    def role_badge(self, obj):
        return obj.role

    @display(description="Content Preview")
    def content_preview(self, obj):
        clean = obj.clean_content
        return clean[:80] + "..." if len(clean) > 80 else clean


# ---------------------------------------------------------------------------
# Contact Inquiries Administration
# ---------------------------------------------------------------------------

@admin.register(Contact)
class ContactAdmin(ModelAdmin):
    list_display = ("name", "email", "subject", "created_at")
    list_filter = ("created_at",)
    search_fields = ("name", "email", "subject", "message")
    readonly_fields = ("name", "email", "subject", "message", "created_at")
    ordering = ("-created_at",)
