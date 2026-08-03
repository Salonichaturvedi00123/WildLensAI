from django.db import models
from django.contrib.auth.models import User

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
        default=0.00
    )

    total_predictions = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username