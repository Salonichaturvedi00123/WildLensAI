from django.db import models
from django.contrib.auth.models import User


class AnimalPrediction(models.Model):
    IDENTIFICATION_STATUS_CHOICES = [
        ("identified", "Identified"),
        ("uncertain", "Uncertain"),
        ("not_animal", "Not Animal"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="predictions"
    )
    uploaded_image = models.ImageField(upload_to="uploads/")
    common_name = models.CharField(max_length=255, default="Unknown Species")
    scientific_name = models.CharField(max_length=255, blank=True, default="")
    confidence = models.FloatField(default=0.0)
    credit_used = models.FloatField(default=1.0)
    identification_status = models.CharField(
        max_length=50,
        choices=IDENTIFICATION_STATUS_CHOICES,
        default="identified"
    )
    identification_notes = models.TextField(blank=True, default="")
    visual_clues = models.JSONField(default=list, blank=True)
    alternative_species = models.JSONField(default=list, blank=True)
    wildlife_information = models.JSONField(default=dict, blank=True)
    source_references = models.JSONField(default=list, blank=True)
    request_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.common_name} ({self.confidence:.2f}%)"

    @property
    def image(self):
        """Backward-compatibility alias for uploaded_image."""
        return self.uploaded_image

    @property
    def image_url(self):
        """Backward-compatibility alias for uploaded_image."""
        return self.uploaded_image

    @property
    def species(self):
        """Backward-compatibility alias for common_name."""
        return self.common_name

    @property
    def clean_wildlife_summary(self) -> str:
        """Extract clean human-readable summary text without provider metadata or content block lists."""
        info = self.wildlife_information
        if isinstance(info, dict):
            raw = info.get("summary")
        else:
            raw = info
        from animal.utils import extract_clean_message_text
        return extract_clean_message_text(raw)

    @property
    def rendered_dossier(self) -> str:
        """Render clean wildlife summary as safe HTML markdown."""
        from animal.utils import render_markdown_safe
        return render_markdown_safe(self.clean_wildlife_summary)


class WildlifeConversation(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="wildlife_conversations"
    )
    prediction = models.ForeignKey(
        AnimalPrediction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations"
    )
    title = models.CharField(max_length=255, default="Wildlife Inquiry")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user.username} - {self.title} ({self.created_at.strftime('%Y-%m-%d')})"


class WildlifeMessage(models.Model):
    ROLE_CHOICES = [
        ("user", "User"),
        ("assistant", "Assistant"),
        ("system", "System"),
    ]

    conversation = models.ForeignKey(
        WildlifeConversation,
        on_delete=models.CASCADE,
        related_name="messages"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="user")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.role.upper()}] {self.content[:50]}"

    @property
    def clean_content(self) -> str:
        """Extract clean text without provider metadata or content block lists."""
        from animal.utils import extract_clean_message_text
        return extract_clean_message_text(self.content)

    @property
    def rendered_content(self) -> str:
        """Render message content as safe HTML markdown."""
        from animal.utils import render_markdown_safe
        return render_markdown_safe(self.clean_content)


class Contact(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField()
    subject = models.CharField(max_length=255)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.email})"
