from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from animal.models import AnimalPrediction
from transactions.models import Profile


class ProtectedMediaViewTests(TestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        self.owner = User.objects.create_user(
            username="media_owner",
            password="StrongPassword123!",
        )
        self.other_user = User.objects.create_user(
            username="media_other",
            password="StrongPassword123!",
        )
        self.prediction = AnimalPrediction.objects.create(
            user=self.owner,
            common_name="Test species",
            uploaded_image=ContentFile(b"private animal image", name="animal.png"),
        )
        self.media_url = self.prediction.uploaded_image.url

    def test_owner_can_retrieve_prediction_image(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.media_url)

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content)
        response.close()
        self.assertEqual(body, b"private animal image")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_other_user_and_anonymous_user_cannot_retrieve_prediction_image(self):
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(self.media_url).status_code, 404)

        self.client.logout()
        self.assertEqual(self.client.get(self.media_url).status_code, 404)

    def test_media_path_traversal_is_rejected(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse(
            "protected_media",
            kwargs={"media_path": "uploads/../db.sqlite3"},
        ))

        self.assertEqual(response.status_code, 404)

    def test_database_health_endpoint_returns_ok(self):
        response = self.client.get(reverse("health_check"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_profile_image_is_only_available_to_owner_or_staff(self):
        profile = Profile.objects.get(user=self.owner)
        profile.profile_image.save(
            "profile.png",
            ContentFile(b"private profile image"),
            save=True,
        )

        self.client.force_login(self.owner)
        owner_response = self.client.get(profile.profile_image.url)
        self.assertEqual(owner_response.status_code, 200)
        owner_response.close()

        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(profile.profile_image.url).status_code, 404)

        staff_user = User.objects.create_user(
            username="media_staff",
            password="StrongPassword123!",
            is_staff=True,
        )
        self.client.force_login(staff_user)
        staff_response = self.client.get(profile.profile_image.url)
        self.assertEqual(staff_response.status_code, 200)
        staff_response.close()
