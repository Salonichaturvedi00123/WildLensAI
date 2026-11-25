from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class LoginRedirectSafetyTests(TestCase):
    def test_login_rejects_scheme_relative_external_next_url(self):
        User.objects.create_user(
            username="redirect_test",
            password="StrongPassword123!",
        )

        response = self.client.post(
            reverse("login"),
            {
                "username": "redirect_test",
                "password": "StrongPassword123!",
                "next": "//attacker.example",
            },
        )

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
