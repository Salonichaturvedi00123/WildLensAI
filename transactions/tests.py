import json
from unittest.mock import MagicMock, patch
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from transactions.models import Profile, CreditPlan, PaymentOrder, WalletTransaction


class TransactionsAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.test_user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="StrongPassword123!"
        )

    def test_profile_auto_created_on_user_creation(self):
        """Verify that creating a user automatically provisions a Profile with default credits."""
        profile = Profile.objects.filter(user=self.test_user).first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.wallet_balance, Decimal("5.00"))
        self.assertEqual(str(profile), "testuser")

    def test_registration_view_get(self):
        """Verify registration page loads successfully."""
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/register.html")

    def test_registration_view_post_success(self):
        """Verify successful user registration creates user and profile."""
        response = self.client.post(reverse("register"), {
            "first_name": "Jane",
            "last_name": "Doe",
            "username": "janedoe",
            "email": "jane@example.com",
            "password": "StrongPassword987!",
            "confirm_password": "StrongPassword987!"
        }, follow=True)
        self.assertRedirects(response, reverse("login"))
        self.assertTrue(User.objects.filter(username="janedoe").exists())
        jane = User.objects.get(username="janedoe")
        self.assertEqual(jane.profile.wallet_balance, Decimal("5.00"))

    def test_registration_view_password_validation_enforced(self):
        """Verify Django built-in password validators reject weak/common passwords."""
        # Simple/numeric password
        response = self.client.post(reverse("register"), {
            "username": "weakuser",
            "password": "123",
            "confirm_password": "123"
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="weakuser").exists())

    def test_registration_view_password_mismatch(self):
        """Verify password mismatch generates an error and does not create user."""
        response = self.client.post(reverse("register"), {
            "username": "mismatchuser",
            "password": "StrongPassword123!",
            "confirm_password": "DifferentPassword123!"
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="mismatchuser").exists())

    def test_login_view_get(self):
        """Verify login page loads successfully."""
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/login.html")

    def test_login_view_post_success(self):
        """Verify valid credentials authenticate user and redirect."""
        response = self.client.post(reverse("login"), {
            "username": "testuser",
            "password": "StrongPassword123!"
        }, follow=True)
        self.assertRedirects(response, reverse("home"))

    def test_login_view_post_failure(self):
        """Verify invalid credentials fail authentication."""
        response = self.client.post(reverse("login"), {
            "username": "testuser",
            "password": "WrongPassword999!"
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["user"].is_authenticated)

    def test_logout_view_post(self):
        """Verify logout using POST ends the session safely."""
        self.client.login(username="testuser", password="StrongPassword123!")
        response = self.client.post(reverse("logout"), follow=True)
        self.assertRedirects(response, reverse("login"))
        self.assertFalse(response.context["user"].is_authenticated)

    def test_protected_dashboard_requires_login(self):
        """Verify unauthenticated user cannot view protected dashboard."""
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_protected_dashboard_view_authenticated(self):
        """Verify authenticated user can view protected dashboard with wallet balance."""
        self.client.login(username="testuser", password="StrongPassword123!")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/profile.html")
        self.assertEqual(response.context["profile"].wallet_balance, Decimal("5.00"))

    def test_forgot_password_view_get(self):
        """Verify forgot password page loads."""
        response = self.client.get(reverse("forgot_password"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/forgot.html")


class Phase7WalletDemoCreditTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user_a = User.objects.create_user(
            username="wallet_user_a",
            email="usera@example.com",
            password="StrongPassword123!"
        )
        self.user_b = User.objects.create_user(
            username="wallet_user_b",
            email="userb@example.com",
            password="StrongPassword123!"
        )

    def test_new_user_gets_5_credits_and_signup_bonus_transaction(self):
        """Rule 1: Verify every new user gets 5 free demo credits and a sign-up transaction ledger entry."""
        profile = self.user_a.profile
        self.assertEqual(profile.wallet_balance, Decimal("5.00"))
        tx = self.user_a.wallet_transactions.filter(transaction_type="signup_bonus").first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount, Decimal("5.00"))
        self.assertEqual(tx.balance_after, Decimal("5.00"))

    def test_deduct_prediction_credit_success(self):
        """Rule 2: Verify valid prediction deducts 1 credit and records transaction."""
        from transactions.services import deduct_prediction_credit
        success, tx, msg = deduct_prediction_credit(
            user=self.user_a,
            cost=Decimal("1.00"),
            request_id="req-test-123"
        )
        self.assertTrue(success)
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount, Decimal("-1.00"))
        self.assertEqual(tx.balance_after, Decimal("4.00"))
        self.user_a.profile.refresh_from_db()
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("4.00"))
        self.assertEqual(self.user_a.profile.total_predictions, 1)

    def test_deduct_prediction_credit_insufficient_funds(self):
        """Rule 8: Verify deducting credits fails if account lacks sufficient balance."""
        from transactions.services import deduct_prediction_credit
        # Deplete balance to 0
        self.user_a.profile.wallet_balance = Decimal("0.50")
        self.user_a.profile.save()

        success, tx, msg = deduct_prediction_credit(
            user=self.user_a,
            cost=Decimal("1.00"),
            request_id="req-insufficient-123"
        )
        self.assertFalse(success)
        self.assertIsNone(tx)
        self.assertIn("Insufficient wallet credits", msg)
        self.user_a.profile.refresh_from_db()
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("0.50"))

    def test_deduct_prediction_credit_duplicate_request_id_prevented(self):
        """Rule D: Verify duplicate submissions with the same request_id do not cause duplicate deductions."""
        from transactions.services import deduct_prediction_credit
        # First deduction
        success1, tx1, _ = deduct_prediction_credit(
            user=self.user_a,
            cost=Decimal("1.00"),
            request_id="req-idempotent-999"
        )
        self.assertTrue(success1)
        self.user_a.profile.refresh_from_db()
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("4.00"))

        # Second deduction attempt with same request_id
        success2, tx2, msg2 = deduct_prediction_credit(
            user=self.user_a,
            cost=Decimal("1.00"),
            request_id="req-idempotent-999"
        )
        self.assertTrue(success2)
        self.assertEqual(tx1.id, tx2.id)
        self.assertIn("already", msg2)
        # Invariant: Balance was only deducted ONCE
        self.user_a.profile.refresh_from_db()
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("4.00"))
        self.assertEqual(self.user_a.wallet_transactions.filter(transaction_type="prediction_charge").count(), 1)

    def test_concurrent_credit_update_f_expression_prevents_negative_balance(self):
        """Rule C: Verify conditional F expression filter prevents balance from going negative."""
        from django.db.models import F
        profile = self.user_a.profile
        profile.wallet_balance = Decimal("0.80")
        profile.save()

        # Attempt to deduct 1.00 using conditional filter
        updated = Profile.objects.filter(
            id=profile.id,
            wallet_balance__gte=Decimal("1.00")
        ).update(
            wallet_balance=F("wallet_balance") - Decimal("1.00")
        )
        self.assertEqual(updated, 0)
        profile.refresh_from_db()
        self.assertEqual(profile.wallet_balance, Decimal("0.80"))

    def test_admin_grant_demo_credits(self):
        """Rule 10: Verify Django admin can grant demo credits to user profiles."""
        from transactions.services import grant_demo_credits
        tx = grant_demo_credits(
            user=self.user_a,
            amount=Decimal("10.00"),
            description="Admin bonus grant"
        )
        self.assertEqual(tx.amount, Decimal("10.00"))
        self.assertEqual(tx.balance_after, Decimal("15.00"))
        self.user_a.profile.refresh_from_db()
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("15.00"))

    def test_transaction_privacy_and_isolation(self):
        """Verify users can only see their own wallet transactions."""
        from transactions.services import grant_demo_credits
        grant_demo_credits(user=self.user_a, amount=Decimal("5.00"))
        grant_demo_credits(user=self.user_b, amount=Decimal("7.00"))

        self.client.login(username="wallet_user_a", password="StrongPassword123!")
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        user_txs = response.context["recent_transactions"]
        for tx in user_txs:
            self.assertEqual(tx.user, self.user_a)
            self.assertNotEqual(tx.user, self.user_b)


class Phase2RazorpayFoundationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="razor_user",
            email="razor@example.com",
            password="StrongPassword123!"
        )
        self.plan_starter = CreditPlan.objects.create(
            name="Test Starter Pack",
            description="50 credits for test",
            price=Decimal("49.00"),
            credits=50,
            is_active=True,
            display_order=1
        )
        self.plan_inactive = CreditPlan.objects.create(
            name="Test Inactive Pack",
            description="Disabled pack",
            price=Decimal("99.00"),
            credits=100,
            is_active=False,
            display_order=9
        )

    def test_credit_plan_properties_and_paise_conversion(self):
        """Verify CreditPlan price in paise is strictly calculated as integer paise."""
        self.assertEqual(self.plan_starter.price_in_paise, 4900)
        self.assertIn("Test Starter Pack", str(self.plan_starter))
        self.assertIn("Active", str(self.plan_starter))
        self.assertIn("Inactive", str(self.plan_inactive))

    def test_get_razorpay_client_placeholder_validation(self):
        """Verify get_razorpay_client blocks placeholder keys when validate_configured is True."""
        from transactions.services import get_razorpay_client, PaymentGatewayConfigurationError
        with patch("django.conf.settings.RAZORPAY_KEY_ID", "rzp_test_placeholder_key"):
            with patch("django.conf.settings.RAZORPAY_KEY_SECRET", "placeholder_secret"):
                with self.assertRaises(PaymentGatewayConfigurationError):
                    get_razorpay_client(validate_configured=True)

    def test_create_payment_order_server_side_calculation_and_no_premature_credit(self):
        """
        Critical Rule:
          1. Server calculates paise from active plan.
          2. Creates PaymentOrder in database with status='created'.
          3. NO credits are added to user's wallet at order creation time.
        """
        from transactions.services import create_razorpay_payment_order

        initial_balance = self.user.profile.wallet_balance
        self.assertEqual(initial_balance, Decimal("5.00"))

        mock_rzp_client = MagicMock()
        mock_rzp_client.order.create.return_value = {
            "id": "order_test_1234567890",
            "amount": 4900,
            "currency": "INR",
            "status": "created"
        }

        checkout_data = create_razorpay_payment_order(
            user=self.user,
            plan_id=self.plan_starter.id,
            client=mock_rzp_client
        )

        # 1. Check returned checkout dictionary
        self.assertEqual(checkout_data["razorpay_order_id"], "order_test_1234567890")
        self.assertEqual(checkout_data["amount"], 4900)
        self.assertEqual(checkout_data["amount_in_rupees"], 49.00)
        self.assertEqual(checkout_data["credits"], 50)
        self.assertEqual(checkout_data["prefill"]["email"], "razor@example.com")

        # 2. Check Razorpay SDK call arguments
        mock_rzp_client.order.create.assert_called_once()
        create_call_data = mock_rzp_client.order.create.call_args[1]["data"]
        self.assertEqual(create_call_data["amount"], 4900)
        self.assertEqual(create_call_data["currency"], "INR")
        self.assertEqual(create_call_data["notes"]["user_id"], str(self.user.id))

        # 3. Check database record
        order = PaymentOrder.objects.get(razorpay_order_id="order_test_1234567890")
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.plan, self.plan_starter)
        self.assertEqual(order.plan_name, "Test Starter Pack")
        self.assertEqual(order.credits_to_grant, 50)
        self.assertEqual(order.amount_paise, 4900)
        self.assertEqual(order.status, "created")
        self.assertIsNone(order.credited_at)

        # 4. INVARIANT: Wallet balance has NOT been credited yet
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.wallet_balance, initial_balance)
        self.assertEqual(
            self.user.wallet_transactions.filter(transaction_type="credit_purchase").count(),
            0
        )

    def test_create_payment_order_rejects_inactive_plan(self):
        """Verify order creation fails when selecting an inactive or deleted plan."""
        from transactions.services import create_razorpay_payment_order, PaymentOrderError

        mock_rzp_client = MagicMock()
        with self.assertRaises(PaymentOrderError):
            create_razorpay_payment_order(
                user=self.user,
                plan_id=self.plan_inactive.id,
                client=mock_rzp_client
            )
        mock_rzp_client.order.create.assert_not_called()

    def test_create_payment_order_view_unauthenticated_forbidden(self):
        """Verify unauthenticated requests cannot initiate orders."""
        response = self.client.post(
            reverse("create_payment_order"),
            data={"plan_id": self.plan_starter.id}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_create_payment_order_view_authenticated_json(self):
        """Verify authenticated JSON POST creates order and returns checkout payload."""
        self.client.login(username="razor_user", password="StrongPassword123!")

        with patch("transactions.views.create_razorpay_payment_order") as mock_create:
            mock_create.return_value = {
                "order_db_id": 99,
                "razorpay_order_id": "order_view_test_888",
                "amount": 4900,
                "amount_in_rupees": 49.00,
                "currency": "INR",
                "key_id": "rzp_test_mock",
                "plan_name": "Test Starter Pack",
                "credits": 50,
                "prefill": {"name": "razor_user", "email": "razor@example.com", "contact": ""}
            }
            response = self.client.post(
                reverse("create_payment_order"),
                data='{"plan_id": %d}' % self.plan_starter.id,
                content_type="application/json"
            )
            self.assertEqual(response.status_code, 201)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["order"]["razorpay_order_id"], "order_view_test_888")
            self.assertEqual(data["order"]["amount"], 4900)

    def test_create_payment_order_view_missing_plan_id(self):
        """Verify request without plan_id returns 400."""
        self.client.login(username="razor_user", password="StrongPassword123!")
        response = self.client.post(
            reverse("create_payment_order"),
            data='{}',
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("plan_id", data["error"])

    def test_credit_wallet_for_verified_order_success_and_ledger(self):
        """Verify valid signature marks order paid, credits wallet, and generates ledger entry."""
        from transactions.services import credit_wallet_for_verified_order

        # Create created order
        order = PaymentOrder.objects.create(
            user=self.user,
            plan=self.plan_starter,
            plan_name=self.plan_starter.name,
            credits_to_grant=50,
            amount_paise=4900,
            currency="INR",
            razorpay_order_id="order_verified_test_001",
            status="created"
        )

        mock_rzp_client = MagicMock()
        mock_rzp_client.utility.verify_payment_signature.return_value = True

        success, msg, tx, status_code = credit_wallet_for_verified_order(
            order=order,
            payment_id="pay_verified_123",
            signature="sig_verified_456",
            client=mock_rzp_client
        )

        self.assertTrue(success)
        self.assertEqual(status_code, "paid")
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount, Decimal("50.00"))
        self.assertEqual(tx.transaction_type, "credit_purchase")

        # Check order updated in DB
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.razorpay_payment_id, "pay_verified_123")
        self.assertEqual(order.razorpay_signature, "sig_verified_456")
        self.assertIsNotNone(order.credited_at)

        # Check user wallet balance: 5.00 initial + 50 = 55.00
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.wallet_balance, Decimal("55.00"))

    def test_credit_wallet_for_verified_order_idempotency_prevents_duplicate_credits(self):
        """Verify re-running verification for already credited order does not grant duplicate credits."""
        from transactions.services import credit_wallet_for_verified_order

        order = PaymentOrder.objects.create(
            user=self.user,
            plan=self.plan_starter,
            plan_name=self.plan_starter.name,
            credits_to_grant=50,
            amount_paise=4900,
            currency="INR",
            razorpay_order_id="order_idempotent_test_002",
            status="created"
        )

        mock_rzp_client = MagicMock()
        mock_rzp_client.utility.verify_payment_signature.return_value = True

        # First verification
        credit_wallet_for_verified_order(
            order=order,
            payment_id="pay_idemp_1",
            signature="sig_idemp_1",
            client=mock_rzp_client
        )
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.wallet_balance, Decimal("55.00"))

        # Second verification attempt with same order
        order.refresh_from_db()
        success2, msg2, tx2, status2 = credit_wallet_for_verified_order(
            order=order,
            payment_id="pay_idemp_1",
            signature="sig_idemp_1",
            client=mock_rzp_client
        )
        self.assertTrue(success2)
        self.assertEqual(status2, "paid")
        self.assertIn("already", msg2)

        # CRITICAL INVARIANT: Wallet balance remains 55.00, not 105.00!
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.wallet_balance, Decimal("55.00"))
        self.assertEqual(
            self.user.wallet_transactions.filter(transaction_type="credit_purchase").count(),
            1
        )

    def test_credit_wallet_signature_failure_marks_failed_and_preserves_balance(self):
        """Verify invalid signature raises PaymentVerificationError and leaves balance intact."""
        from razorpay.errors import SignatureVerificationError
        from transactions.services import credit_wallet_for_verified_order, PaymentVerificationError

        order = PaymentOrder.objects.create(
            user=self.user,
            plan=self.plan_starter,
            plan_name=self.plan_starter.name,
            credits_to_grant=50,
            amount_paise=4900,
            currency="INR",
            razorpay_order_id="order_tampered_test_003",
            status="created"
        )

        mock_rzp_client = MagicMock()
        mock_rzp_client.utility.verify_payment_signature.side_effect = SignatureVerificationError("Mismatch")

        with self.assertRaises(PaymentVerificationError):
            credit_wallet_for_verified_order(
                order=order,
                payment_id="pay_fake_999",
                signature="sig_tampered_999",
                client=mock_rzp_client
            )

        order.refresh_from_db()
        self.assertEqual(order.status, "failed")
        self.assertIsNone(order.credited_at)

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.wallet_balance, Decimal("5.00"))
        self.assertEqual(
            self.user.wallet_transactions.filter(transaction_type="credit_purchase").count(),
            0
        )

    def test_list_credit_plans_api(self):
        """Verify public API returns active plans and omits inactive plans."""
        response = self.client.get(reverse("list_credit_plans"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        plan_names = [p["name"] for p in data["plans"]]
        self.assertIn("Test Starter Pack", plan_names)
        self.assertNotIn("Test Inactive Pack", plan_names)


class Phase3RazorpayCheckoutTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user_a = User.objects.create_user(
            username="checkout_user_a",
            email="usera@example.com",
            password="StrongPassword123!"
        )
        self.user_b = User.objects.create_user(
            username="checkout_user_b",
            email="userb@example.com",
            password="StrongPassword123!"
        )
        self.plan = CreditPlan.objects.create(
            name="Field Explorer Pack",
            description="120 credits for testing",
            price=Decimal("99.00"),
            credits=120,
            is_active=True,
            display_order=1
        )
        self.order_a = PaymentOrder.objects.create(
            user=self.user_a,
            plan=self.plan,
            plan_name=self.plan.name,
            credits_to_grant=120,
            amount_paise=9900,
            currency="INR",
            razorpay_order_id="order_phase3_test_001",
            status="created"
        )

    def test_recharge_wallet_view_authenticated(self):
        """Verify authenticated user can view recharge page with plans, test badge, and balance."""
        self.client.login(username="checkout_user_a", password="StrongPassword123!")
        response = self.client.get(reverse("recharge"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/recharge.html")
        content = response.content.decode("utf-8")
        self.assertIn("Field Explorer Pack", content)
        self.assertIn("Razorpay Sandbox Test Mode", content)
        self.assertIn("https://checkout.razorpay.com/v1/checkout.js", content)
        self.assertIn("wallet_checkout.js", content)
        self.assertIn("btn-recharge-plan", content)
        self.assertIn("checkout-status-alert-container", content)
        self.assertIn("Your Discovery Credits", content)
        self.assertIn("Available Credits", content)
        self.assertIn("Transaction History", content)
        self.assertIn("Test Payment", content)

    def test_recharge_wallet_view_unauthenticated_redirects(self):
        """Verify unauthenticated user cannot view recharge page."""
        response = self.client.get(reverse("recharge"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_verify_payment_order_successful_captured_payment(self):
        """
        Verify:
          1. Signature is verified.
          2. Remote payment status is checked via API and confirmed 'captured'.
          3. Wallet is atomically credited with 120 credits.
          4. WalletTransaction is created with type 'credit_purchase'.
        """
        self.client.login(username="checkout_user_a", password="StrongPassword123!")

        with patch("transactions.services.get_razorpay_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.utility.verify_payment_signature.return_value = True
            mock_client.payment.fetch.return_value = {
                "id": "pay_phase3_valid_101",
                "order_id": "order_phase3_test_001",
                "amount": 9900,
                "currency": "INR",
                "status": "captured"
            }
            mock_get_client.return_value = mock_client

            payload = {
                "razorpay_order_id": "order_phase3_test_001",
                "razorpay_payment_id": "pay_phase3_valid_101",
                "razorpay_signature": "sig_valid_hash_101"
            }
            response = self.client.post(
                reverse("verify_payment_order"),
                data=json.dumps(payload),
                content_type="application/json"
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["status"], "paid")
            self.assertEqual(data["credits_granted"], 120)
            # Initial 5.00 + 120 = 125.00
            self.assertEqual(data["wallet_balance"], 125.00)

            # Check DB records
            self.order_a.refresh_from_db()
            self.assertEqual(self.order_a.status, "paid")
            self.assertEqual(self.order_a.razorpay_payment_id, "pay_phase3_valid_101")
            self.assertIsNotNone(self.order_a.credited_at)

            self.user_a.profile.refresh_from_db()
            self.assertEqual(self.user_a.profile.wallet_balance, Decimal("125.00"))

            tx = self.user_a.wallet_transactions.filter(payment_order=self.order_a).first()
            self.assertIsNotNone(tx)
            self.assertEqual(tx.amount, Decimal("120.00"))
            self.assertEqual(tx.transaction_type, "credit_purchase")

    def test_verify_payment_order_invalid_signature_fails_safely(self):
        """Verify signature mismatch marks order failed and prevents wallet credit."""
        from razorpay.errors import SignatureVerificationError

        self.client.login(username="checkout_user_a", password="StrongPassword123!")

        with patch("transactions.services.get_razorpay_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.utility.verify_payment_signature.side_effect = SignatureVerificationError("Tampered")
            mock_get_client.return_value = mock_client

            payload = {
                "razorpay_order_id": "order_phase3_test_001",
                "razorpay_payment_id": "pay_tampered_202",
                "razorpay_signature": "sig_tampered_202"
            }
            response = self.client.post(
                reverse("verify_payment_order"),
                data=json.dumps(payload),
                content_type="application/json"
            )

            self.assertEqual(response.status_code, 400)
            data = response.json()
            self.assertFalse(data["success"])
            self.assertEqual(data["status"], "failed")

            # Balance is unchanged
            self.user_a.profile.refresh_from_db()
            self.assertEqual(self.user_a.profile.wallet_balance, Decimal("5.00"))

            self.order_a.refresh_from_db()
            self.assertEqual(self.order_a.status, "failed")
            self.assertIsNone(self.order_a.credited_at)

    def test_verify_payment_order_authorized_not_captured_marks_pending(self):
        """
        Verify:
          When payment is authorized but not captured by gateway:
          - Response status is 'pending'
          - Order is marked status='pending'
          - NO credits are granted
        """
        self.client.login(username="checkout_user_a", password="StrongPassword123!")

        with patch("transactions.services.get_razorpay_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.utility.verify_payment_signature.return_value = True
            mock_client.payment.fetch.return_value = {
                "id": "pay_auth_only_303",
                "order_id": "order_phase3_test_001",
                "amount": 9900,
                "currency": "INR",
                "status": "authorized" # NOT captured
            }
            mock_get_client.return_value = mock_client

            payload = {
                "razorpay_order_id": "order_phase3_test_001",
                "razorpay_payment_id": "pay_auth_only_303",
                "razorpay_signature": "sig_auth_303"
            }
            response = self.client.post(
                reverse("verify_payment_order"),
                data=json.dumps(payload),
                content_type="application/json"
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertFalse(data["success"])
            self.assertEqual(data["status"], "pending")

            # Balance remains uncredited
            self.user_a.profile.refresh_from_db()
            self.assertEqual(self.user_a.profile.wallet_balance, Decimal("5.00"))

            self.order_a.refresh_from_db()
            self.assertEqual(self.order_a.status, "pending")
            self.assertIsNone(self.order_a.credited_at)

    def test_verify_payment_order_amount_and_currency_mismatches_rejected(self):
        """Verify tampered amount or currency rejected by server-side verification."""
        self.client.login(username="checkout_user_a", password="StrongPassword123!")

        with patch("transactions.services.get_razorpay_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.utility.verify_payment_signature.return_value = True
            # Remote amount is 1000 paise instead of 9900
            mock_client.payment.fetch.return_value = {
                "id": "pay_mismatch_404",
                "order_id": "order_phase3_test_001",
                "amount": 1000,
                "currency": "INR",
                "status": "captured"
            }
            mock_get_client.return_value = mock_client

            payload = {
                "razorpay_order_id": "order_phase3_test_001",
                "razorpay_payment_id": "pay_mismatch_404",
                "razorpay_signature": "sig_mismatch_404"
            }
            response = self.client.post(
                reverse("verify_payment_order"),
                data=json.dumps(payload),
                content_type="application/json"
            )

            self.assertEqual(response.status_code, 400)
            self.user_a.profile.refresh_from_db()
            self.assertEqual(self.user_a.profile.wallet_balance, Decimal("5.00"))

    def test_user_cannot_verify_another_users_order(self):
        """Security: Verify User B cannot verify or intercept User A's order."""
        self.client.login(username="checkout_user_b", password="StrongPassword123!")

        payload = {
            "razorpay_order_id": "order_phase3_test_001", # Belongs to user_a!
            "razorpay_payment_id": "pay_hacker_505",
            "razorpay_signature": "sig_hacker_505"
        }
        response = self.client.post(
            reverse("verify_payment_order"),
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 404)
        self.user_b.profile.refresh_from_db()
        self.assertEqual(self.user_b.profile.wallet_balance, Decimal("5.00"))
        self.order_a.refresh_from_db()
        self.assertEqual(self.order_a.status, "created")

    def test_check_order_status_view(self):
        """Verify check_order_status returns current state and balance."""
        self.client.login(username="checkout_user_a", password="StrongPassword123!")
        url = reverse("check_order_status", kwargs={"order_id": self.order_a.razorpay_order_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["order_id"], self.order_a.razorpay_order_id)
        self.assertEqual(data["status"], "created")
        self.assertEqual(data["credits"], 120)

    def test_reconcile_order_success(self):
        """Verify manual/localhost reconciliation fetches payments and credits captured order."""
        self.client.login(username="checkout_user_a", password="StrongPassword123!")

        with patch("transactions.services.get_razorpay_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.order.fetch_payments.return_value = {
                "count": 1,
                "items": [{
                    "id": "pay_reconciled_606",
                    "amount": 9900,
                    "currency": "INR",
                    "status": "captured"
                }]
            }
            mock_get_client.return_value = mock_client

            url = reverse("reconcile_order", kwargs={"order_id": self.order_a.razorpay_order_id})
            response = self.client.post(url)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["status"], "paid")

            self.user_a.profile.refresh_from_db()
            self.assertEqual(self.user_a.profile.wallet_balance, Decimal("125.00"))

            self.order_a.refresh_from_db()
            self.assertEqual(self.order_a.status, "paid")

    def test_webhook_payment_captured_and_idempotent_replay(self):
        """Verify webhook processes capture event, credits wallet, and deduplicates identical replays."""
        webhook_body = json.dumps({
            "id": "evt_webhook_test_707",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_webhook_707",
                        "order_id": "order_phase3_test_001",
                        "amount": 9900,
                        "currency": "INR",
                        "status": "captured"
                    }
                }
            }
        }).encode("utf-8")

        with patch("django.conf.settings.RAZORPAY_WEBHOOK_SECRET", "test_wh_secret_xyz"):
            with patch("transactions.services.get_razorpay_client") as mock_get_client:
                mock_client = MagicMock()
                mock_client.utility.verify_webhook_signature.return_value = True
                mock_get_client.return_value = mock_client

                # 1. First webhook delivery
                response1 = self.client.post(
                    reverse("razorpay_webhook"),
                    data=webhook_body,
                    content_type="application/json",
                    HTTP_X_RAZORPAY_SIGNATURE="valid_hmac_wh_sig"
                )
                self.assertEqual(response1.status_code, 200)

                self.user_a.profile.refresh_from_db()
                self.assertEqual(self.user_a.profile.wallet_balance, Decimal("125.00"))

                # 2. Second webhook delivery (Replay attack / duplicate delivery)
                response2 = self.client.post(
                    reverse("razorpay_webhook"),
                    data=webhook_body,
                    content_type="application/json",
                    HTTP_X_RAZORPAY_SIGNATURE="valid_hmac_wh_sig"
                )
                self.assertEqual(response2.status_code, 200)

                # INVARIANT: Wallet was NOT double-credited (remains 125.00, not 245.00)
                self.user_a.profile.refresh_from_db()
                self.assertEqual(self.user_a.profile.wallet_balance, Decimal("125.00"))

    def test_webhook_invalid_signature_rejected(self):
        """Verify webhook with invalid HMAC signature returns 400 Bad Request."""
        from razorpay.errors import SignatureVerificationError

        webhook_body = b'{"id": "evt_bad_sig", "event": "payment.captured"}'

        with patch("django.conf.settings.RAZORPAY_WEBHOOK_SECRET", "test_wh_secret_xyz"):
            with patch("transactions.services.get_razorpay_client") as mock_get_client:
                mock_client = MagicMock()
                mock_client.utility.verify_webhook_signature.side_effect = SignatureVerificationError("Bad Sig")
                mock_get_client.return_value = mock_client

                response = self.client.post(
                    reverse("razorpay_webhook"),
                    data=webhook_body,
                    content_type="application/json",
                    HTTP_X_RAZORPAY_SIGNATURE="tampered_wh_sig"
                )
                self.assertEqual(response.status_code, 400)


class Phase4AdminAnalyticsTests(TestCase):
    """
    Automated verification for Phase 4:
    - Modern Django Unfold Admin Panel integration
    - Role-based authorization & security
    - SQLite ORM KPI calculations & zero-division safety
    - Date filtering ('7d', '30d', 'all', invalid period fallback)
    - Chart.js dataset generation
    - Administrative credit grant actions
    - Read-only protection for payment and AI records
    """

    def setUp(self):
        self.client = Client()

        # Regular non-staff user
        self.regular_user = User.objects.create_user(
            username="regular_user",
            email="regular@example.com",
            password="StrongPassword123!"
        )

        # Superuser / staff admin
        self.admin_user = User.objects.create_superuser(
            username="admin_user",
            email="admin@example.com",
            password="SuperPassword123!"
        )

    def test_admin_requires_staff_user(self):
        """Security: Verify anonymous and non-staff users cannot access /admin/."""
        # 1. Anonymous user
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

        # 2. Regular non-staff user
        self.client.login(username="regular_user", password="StrongPassword123!")
        response2 = self.client.get("/admin/")
        self.assertEqual(response2.status_code, 302)
        self.assertIn("/admin/login/", response2.url)

    def test_admin_accessible_by_staff_superuser(self):
        """Verify staff superuser can load Unfold admin and custom dashboard successfully."""
        self.client.login(username="admin_user", password="SuperPassword123!")
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)

        # Verify Unfold branding and custom elements
        content = response.content.decode("utf-8")
        self.assertIn("WildLensAI Analytics Dashboard", content)
        self.assertIn("Razorpay Test Mode Active", content)
        self.assertIn("Registered Users", content)

    def test_dashboard_callback_empty_database_safety(self):
        """Verify dashboard_callback runs without zero-division or null errors on an empty dataset."""
        from django.test import RequestFactory
        from transactions.dashboard import dashboard_callback

        factory = RequestFactory()
        request = factory.get("/admin/?period=30d")
        request.user = self.admin_user

        context = {}
        result = dashboard_callback(request, context)

        kpi = result["kpi"]
        self.assertEqual(kpi["period"], "30d")
        self.assertEqual(kpi["identified_rate"], 0)
        self.assertEqual(kpi["credits_purchased"], 0)
        self.assertEqual(kpi["test_volume_rupees"], Decimal("0.00"))

        charts = result["charts"]
        for chart_key in ("user_registrations", "animal_predictions", "popular_animals", "payment_activity", "identification_outcomes"):
            self.assertIn(chart_key, charts)
            chart_obj = json.loads(charts[chart_key])
            self.assertIn("labels", chart_obj)
            self.assertIn("datasets", chart_obj)

    def test_dashboard_callback_with_records_and_kpi_calculation(self):
        """Verify dashboard_callback calculates aggregated KPIs from real database rows."""
        from animal.models import AnimalPrediction, WildlifeConversation, WildlifeMessage
        from django.test import RequestFactory
        from transactions.dashboard import dashboard_callback

        # 1. Create Predictions
        AnimalPrediction.objects.create(
            user=self.regular_user,
            common_name="Bengal Tiger",
            scientific_name="Panthera tigris tigris",
            confidence=95.5,
            identification_status="identified",
            request_id="req-tiger-1"
        )
        AnimalPrediction.objects.create(
            user=self.regular_user,
            common_name="Unknown Feline",
            confidence=42.0,
            identification_status="uncertain",
            request_id="req-uncertain-2"
        )
        AnimalPrediction.objects.create(
            user=self.regular_user,
            common_name="Park Bench",
            confidence=12.0,
            identification_status="not_animal",
            request_id="req-bench-3"
        )

        # 2. Create Wildlife Conversation & Messages
        conv = WildlifeConversation.objects.create(
            user=self.regular_user,
            title="Tiger Diet Inquiry"
        )
        WildlifeMessage.objects.create(
            conversation=conv,
            role="user",
            content="What does the Bengal Tiger eat?"
        )
        WildlifeMessage.objects.create(
            conversation=conv,
            role="assistant",
            content="The Bengal Tiger primarily preys on deer, wild boar, and water buffalo."
        )

        # 3. Create Payment Orders
        plan = CreditPlan.objects.create(
            name="Pro Plan",
            credits=150,
            price=Decimal("120.00")
        )
        # Paid order
        PaymentOrder.objects.create(
            user=self.regular_user,
            plan=plan,
            razorpay_order_id="order_paid_001",
            amount_paise=12000,
            credits_to_grant=150,
            status="paid"
        )
        # Pending order
        PaymentOrder.objects.create(
            user=self.regular_user,
            plan=plan,
            razorpay_order_id="order_pending_002",
            amount_paise=12000,
            credits_to_grant=150,
            status="pending"
        )

        factory = RequestFactory()
        request = factory.get("/admin/?period=all")
        request.user = self.admin_user

        context = {}
        result = dashboard_callback(request, context)
        kpi = result["kpi"]

        self.assertEqual(kpi["period"], "all")
        self.assertEqual(kpi["total_predictions"], 3)
        self.assertEqual(kpi["identified_count"], 1)
        self.assertEqual(kpi["uncertain_count"], 1)
        self.assertEqual(kpi["not_animal_count"], 1)
        self.assertEqual(kpi["identified_rate"], 33.3)
        self.assertEqual(kpi["total_conversations"], 1)
        self.assertEqual(kpi["total_messages"], 2)
        self.assertEqual(kpi["successful_payments"], 1)
        self.assertEqual(kpi["pending_payments"], 1)
        self.assertEqual(kpi["credits_purchased"], 150)
        self.assertEqual(kpi["test_volume_rupees"], Decimal("120.00"))

        # Verify popular animals chart contains "Bengal Tiger"
        popular_chart = json.loads(result["charts"]["popular_animals"])
        self.assertIn("Bengal Tiger", popular_chart["labels"])

    def test_dashboard_period_filters_and_fallback(self):
        """Verify period selector supports '7d', '30d', 'all', and falls back safely on invalid input."""
        from django.test import RequestFactory
        from transactions.dashboard import dashboard_callback

        factory = RequestFactory()

        for period_param, expected_label in [
            ("7d", "Last 7 Days"),
            ("30d", "Last 30 Days"),
            ("all", "All Time"),
            ("invalid_value", "Last 30 Days"),  # Safe fallback
        ]:
            req = factory.get(f"/admin/?period={period_param}")
            req.user = self.admin_user
            res = dashboard_callback(req, {})
            self.assertEqual(res["kpi"]["period_label"], expected_label)

    def test_admin_batch_grant_demo_credits_action(self):
        """Verify admin batch actions grant_5_demo_credits and grant_10_demo_credits work on User queryset."""
        from transactions.admin import grant_5_demo_credits, grant_10_demo_credits
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get("/admin/auth/user/")
        request.user = self.admin_user
        # Mock message storage for Django messages framework
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        # Baseline balance is 5.00 from user creation
        self.assertEqual(self.regular_user.profile.wallet_balance, Decimal("5.00"))

        # Grant 5 demo credits
        grant_5_demo_credits(None, request, User.objects.filter(id=self.regular_user.id))
        self.regular_user.profile.refresh_from_db()
        self.assertEqual(self.regular_user.profile.wallet_balance, Decimal("10.00"))

        # Grant 10 demo credits
        grant_10_demo_credits(None, request, User.objects.filter(id=self.regular_user.id))
        self.regular_user.profile.refresh_from_db()
        self.assertEqual(self.regular_user.profile.wallet_balance, Decimal("20.00"))

        # Verify WalletTransaction audit trail
        txs = self.regular_user.wallet_transactions.filter(transaction_type="admin_grant")
        self.assertEqual(txs.count(), 2)

    def test_payment_and_ai_admin_readonly_security(self):
        """Security: Verify payment orders cannot be created or deleted via admin, and AI fields are protected."""
        from transactions.admin import PaymentOrderAdmin, RazorpayWebhookEventAdmin
        from animal.admin import AnimalPredictionAdmin
        from animal.models import AnimalPrediction
        from django.contrib.admin.sites import AdminSite

        site = AdminSite()
        payment_admin = PaymentOrderAdmin(PaymentOrder, site)
        ai_admin = AnimalPredictionAdmin(AnimalPrediction, site)

        # Payment records cannot be created or deleted in admin
        self.assertFalse(payment_admin.has_add_permission(None))
        self.assertFalse(payment_admin.has_delete_permission(None))

        # Critical gateway fields are strictly read-only
        self.assertIn("razorpay_order_id", payment_admin.readonly_fields)
        self.assertIn("razorpay_payment_id", payment_admin.readonly_fields)
        self.assertIn("razorpay_signature", payment_admin.readonly_fields)
        self.assertIn("amount_paise", payment_admin.readonly_fields)

        # AI prediction result data is strictly read-only
        self.assertIn("wildlife_information", ai_admin.readonly_fields)
        self.assertIn("confidence", ai_admin.readonly_fields)
        self.assertIn("identification_status", ai_admin.readonly_fields)
        self.assertIn("request_id", ai_admin.readonly_fields)


class Phase5IntegrationAndSecurityTests(TestCase):
    """
    Automated verification for Phase 5:
    - Multi-device responsive template rendering across all views
    - Floating & resizable assistant components and speech controls
    - Full end-to-end Razorpay Test Mode flow (Order -> Verify -> Spend Credits)
    - Replay attack & duplicate payment protection
    - Failed/cancelled payment handling
    - Non-authenticated and cross-user security isolation
    - Multi-period admin analytics rendering
    - LAN & HTTPS tunnel settings compatibility
    """

    def setUp(self):
        self.client = Client()

        # Regular user A
        self.user_a = User.objects.create_user(
            username="p5_user_a",
            email="p5_a@example.com",
            password="StrongPassword123!"
        )

        # Regular user B
        self.user_b = User.objects.create_user(
            username="p5_user_b",
            email="p5_b@example.com",
            password="StrongPassword123!"
        )

        # Superuser / staff admin
        self.admin_user = User.objects.create_superuser(
            username="p5_admin",
            email="p5_admin@example.com",
            password="SuperPassword123!"
        )

        # Credit plan
        self.plan = CreditPlan.objects.create(
            name="Explorer Pack",
            description="120 Wildlife Credits for Field Testing",
            price=Decimal("99.00"),
            credits=120,
            is_active=True,
            display_order=2
        )

    def test_all_core_views_render_responsively(self):
        """Responsive Audit: Verify all pages load with 200 OK and responsive viewport meta tag."""
        # Public views (unauthenticated)
        public_urls = [
            reverse("home"),
            reverse("contact"),
            reverse("login"),
            reverse("register"),
            reverse("forgot_password"),
        ]
        for url in public_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"Failed GET on {url}")
            content = response.content.decode("utf-8")
            self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0">', content)

        # Authenticated views
        self.client.login(username="p5_user_a", password="StrongPassword123!")
        auth_urls = [
            reverse("dashboard"),
            reverse("profile"),
            reverse("recharge"),
            reverse("predict"),
            reverse("history"),
        ]
        for url in auth_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"Failed authenticated GET on {url}")
            content = response.content.decode("utf-8")
            self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0">', content)

    def test_floating_assistant_and_voice_elements_rendered(self):
        """Floating Assistant & Voice: Verify all window controls and speech elements render on prediction detail."""
        from animal.models import AnimalPrediction, WildlifeConversation

        pred = AnimalPrediction.objects.create(
            user=self.user_a,
            common_name="Snow Leopard",
            scientific_name="Panthera uncia",
            confidence=94.2,
            identification_status="identified",
            wildlife_information={"summary": "Apex predator inhabiting high mountains of Central Asia."},
            source_references=[{"title": "GBIF", "url": "https://www.gbif.org/species/5219436"}]
        )
        conv = WildlifeConversation.objects.create(
            user=self.user_a,
            prediction=pred,
            title="Snow Leopard Ecology"
        )

        self.client.login(username="p5_user_a", password="StrongPassword123!")
        detail_url = reverse("prediction_detail", kwargs={"prediction_id": pred.id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        # Library & drag handle
        self.assertIn("interact.min.js", content)
        self.assertIn("chat-header-drag", content)

        # Window mode action controls (Dock, Minimize, Maximize, Reset)
        self.assertIn("btn-assistant-dock", content)
        self.assertIn("btn-assistant-minimize", content)
        self.assertIn("btn-assistant-maximize", content)
        self.assertIn("btn-assistant-reset", content)

        # Launchers
        self.assertIn("assistant-minimized-launcher", content)
        self.assertIn("mobile-assistant-launcher", content)

        # Voice input & TTS elements
        self.assertIn("chat-mic-btn", content)
        self.assertIn("auto-read-toggle", content)
        self.assertIn("tts-speed-select", content)
        self.assertIn("voice-lang-select", content)
        self.assertIn("en-IN", content)
        self.assertIn("hi-IN", content)

    def test_razorpay_complete_purchase_to_spending_flow(self):
        """E2E Verification: User registers -> purchases credits -> spends on prediction -> idempotent checks."""
        from transactions.services import deduct_prediction_credit

        # 1. Baseline balance is 5.00 from signup bonus
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("5.00"))

        self.client.login(username="p5_user_a", password="StrongPassword123!")

        # 2. Create Razorpay order
        with patch("transactions.services.get_razorpay_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.order.create.return_value = {
                "id": "order_p5_e2e_001",
                "amount": 9900,
                "currency": "INR",
                "status": "created"
            }
            mock_get_client.return_value = mock_client

            create_resp = self.client.post(
                reverse("create_payment_order"),
                data=json.dumps({"plan_id": self.plan.id}),
                content_type="application/json"
            )
            self.assertIn(create_resp.status_code, (200, 201))
            create_data = create_resp.json()
            self.assertTrue(create_data["success"])
            self.assertEqual(create_data["order"]["razorpay_order_id"], "order_p5_e2e_001")

        # 3. Verify order with signature and remote capture
        with patch("transactions.services.get_razorpay_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.utility.verify_payment_signature.return_value = True
            mock_client.payment.fetch.return_value = {
                "id": "pay_p5_e2e_001",
                "order_id": "order_p5_e2e_001",
                "status": "captured",
                "amount": 9900,
                "currency": "INR",
            }
            mock_get_client.return_value = mock_client

            verify_resp = self.client.post(
                reverse("verify_payment_order"),
                data=json.dumps({
                    "razorpay_order_id": "order_p5_e2e_001",
                    "razorpay_payment_id": "pay_p5_e2e_001",
                    "razorpay_signature": "sig_p5_e2e_valid"
                }),
                content_type="application/json"
            )
            self.assertEqual(verify_resp.status_code, 200)
            verify_data = verify_resp.json()
            self.assertTrue(verify_data["success"])

        # 4. Invariant: User balance is 5.00 + 120.00 = 125.00
        self.user_a.profile.refresh_from_db()
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("125.00"))

        # Verify transaction ledger record
        tx = self.user_a.wallet_transactions.filter(transaction_type="credit_purchase").first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount, Decimal("120.00"))
        self.assertEqual(tx.balance_after, Decimal("125.00"))

        # 5. User spends 1 credit on identification
        success, deduct_tx, msg = deduct_prediction_credit(
            user=self.user_a,
            cost=Decimal("1.00"),
            request_id="req-p5-test-spend-001"
        )
        self.assertTrue(success)
        self.user_a.profile.refresh_from_db()
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("124.00"))

        # 6. Idempotent check: Re-submitting the exact same verify payload does not double-credit
        with patch("transactions.services.get_razorpay_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.utility.verify_payment_signature.return_value = True
            mock_get_client.return_value = mock_client

            replay_resp = self.client.post(
                reverse("verify_payment_order"),
                data=json.dumps({
                    "razorpay_order_id": "order_p5_e2e_001",
                    "razorpay_payment_id": "pay_p5_e2e_001",
                    "razorpay_signature": "sig_p5_e2e_valid"
                }),
                content_type="application/json"
            )
            self.assertEqual(replay_resp.status_code, 200)
            replay_data = replay_resp.json()
            self.assertTrue(replay_data["success"])
            self.assertEqual(replay_data["status"], "paid")

            # Balance remains 124.00, NOT 244.00 (No double crediting)
            self.user_a.profile.refresh_from_db()
            self.assertEqual(self.user_a.profile.wallet_balance, Decimal("124.00"))

    def test_razorpay_failed_payment_webhook(self):
        """Security: Verify failed payment webhook updates order status to 'failed' and does not credit wallet."""
        order = PaymentOrder.objects.create(
            user=self.user_a,
            plan=self.plan,
            razorpay_order_id="order_p5_fail_002",
            amount_paise=9900,
            credits_to_grant=120,
            status="created"
        )

        failed_webhook_payload = json.dumps({
            "id": "evt_fail_909",
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_fail_909",
                        "order_id": "order_p5_fail_002",
                        "error_description": "Card declined by issuing bank"
                    }
                }
            }
        }).encode("utf-8")

        with patch("django.conf.settings.RAZORPAY_WEBHOOK_SECRET", "test_wh_secret_xyz"):
            with patch("transactions.services.get_razorpay_client") as mock_get_client:
                mock_client = MagicMock()
                mock_client.utility.verify_webhook_signature.return_value = True
                mock_get_client.return_value = mock_client

                resp = self.client.post(
                    reverse("razorpay_webhook"),
                    data=failed_webhook_payload,
                    content_type="application/json",
                    HTTP_X_RAZORPAY_SIGNATURE="valid_sig_for_fail"
                )
                self.assertEqual(resp.status_code, 200)

                order.refresh_from_db()
                self.assertEqual(order.status, "failed")
                self.assertEqual(order.failure_reason, "Card declined by issuing bank")

                # Wallet balance unaffected
                self.user_a.profile.refresh_from_db()
                self.assertEqual(self.user_a.profile.wallet_balance, Decimal("5.00"))

    def test_unauthenticated_and_cross_user_security(self):
        """Security: Verify unauthenticated and cross-user boundaries are strictly enforced."""
        # 1. Unauthenticated order creation blocked
        anon_resp = self.client.post(
            reverse("create_payment_order"),
            data=json.dumps({"plan_id": self.plan.id}),
            content_type="application/json"
        )
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn(reverse("login"), anon_resp.url)

        # 2. Cross-user order verification blocked
        order_b = PaymentOrder.objects.create(
            user=self.user_b,
            plan=self.plan,
            razorpay_order_id="order_user_b_private",
            amount_paise=9900,
            credits_to_grant=120,
            status="created"
        )

        # User A tries to verify User B's order
        self.client.login(username="p5_user_a", password="StrongPassword123!")
        cross_resp = self.client.post(
            reverse("verify_payment_order"),
            data=json.dumps({
                "razorpay_order_id": "order_user_b_private",
                "razorpay_payment_id": "pay_hacker",
                "razorpay_signature": "sig_hacker"
            }),
            content_type="application/json"
        )
        self.assertEqual(cross_resp.status_code, 404)
        self.user_a.profile.refresh_from_db()
        self.assertEqual(self.user_a.profile.wallet_balance, Decimal("5.00"))

    def test_admin_dashboard_with_all_period_filters(self):
        """Admin: Verify staff superuser can load Unfold admin with 7d, 30d, and all-time filters."""
        self.client.login(username="p5_admin", password="SuperPassword123!")

        for period in ("7d", "30d", "all"):
            response = self.client.get(f"/admin/?period={period}")
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn("WildLensAI Analytics Dashboard", content)
            self.assertIn("Razorpay Test Mode Active", content)
            self.assertIn("Registered Users", content)

    def test_settings_allowed_hosts_and_csrf_trusted_origins(self):
        """Configuration: Verify ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS parse lists from settings."""
        from django.conf import settings
        self.assertIsInstance(settings.ALLOWED_HOSTS, (list, tuple))
        self.assertIsInstance(settings.CSRF_TRUSTED_ORIGINS, (list, tuple))





