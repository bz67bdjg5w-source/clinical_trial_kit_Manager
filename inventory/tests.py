from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.management import call_command, CommandError
from django.db import transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from inventory.admin import KitLotAdmin
from inventory.forms import KitLotForm, SignUpForm
from inventory.models import ExpiryReminder, KitLot, StockChangeHistory
from inventory.services import save_kit_lot


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ExpiryReminderCommandTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_user(
            username="inventory-manager",
            email="manager@example.test",
            password="test-password",
        )
        permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="can_manage_inventory",
        )
        self.manager.user_permissions.add(permission)
        today = timezone.localdate()
        self.expiring_kit = KitLot.objects.create(
            owner=self.manager,
            kit_name="Study Alpha",
            lot_number="LOT-30",
            expiry_date=today + timedelta(days=30),
            quantity_available=5,
        )
        self.later_kit = KitLot.objects.create(
            owner=self.manager,
            kit_name="Study Beta",
            lot_number="LOT-31",
            expiry_date=today + timedelta(days=31),
            quantity_available=6,
        )

    def run_alerts(self):
        call_command("send_kit_alerts", stdout=StringIO(), stderr=StringIO())

    def test_30_days_sends_details_and_31_days_does_not(self):
        get_user_model().objects.create_user(
            username="other-user",
            email="other@example.test",
            password="test-password",
        )
        self.run_alerts()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["manager@example.test"])
        self.assertIn("Study name: Study Alpha", mail.outbox[0].body)
        self.assertIn("Lot number: LOT-30", mail.outbox[0].body)
        self.assertIn(f"Expiry date: {self.expiring_kit.expiry_date}", mail.outbox[0].body)
        self.assertIn("Days remaining: 30", mail.outbox[0].body)
        self.assertFalse(
            ExpiryReminder.objects.filter(kit_lot=self.later_kit).exists()
        )

    def test_duplicate_runs_do_not_repeat_and_changed_expiry_starts_new_event(self):
        self.run_alerts()
        self.run_alerts()
        self.assertEqual(len(mail.outbox), 1)

        self.expiring_kit.expiry_date -= timedelta(days=1)
        self.expiring_kit.save(update_fields=["expiry_date"])
        self.run_alerts()

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            ExpiryReminder.objects.filter(kit_lot=self.expiring_kit).count(),
            2,
        )

    def test_expiry_reminders_are_sent_only_to_each_lot_owner(self):
        other_owner = get_user_model().objects.create_user(
            username="other-owner",
            email="other-owner@example.test",
            password="test-password",
        )
        other_kit = KitLot.objects.create(
            owner=other_owner,
            kit_name="Private Study Beta",
            lot_number="PRIVATE-EXPIRING",
            expiry_date=timezone.localdate() + timedelta(days=5),
            quantity_available=10,
        )

        self.run_alerts()

        self.assertEqual(
            {tuple(message.to) for message in mail.outbox},
            {("manager@example.test",), ("other-owner@example.test",)},
        )
        messages_by_recipient = {
            message.to[0]: message.body for message in mail.outbox
        }
        self.assertIn("Study Alpha", messages_by_recipient["manager@example.test"])
        self.assertNotIn("Private Study Beta", messages_by_recipient["manager@example.test"])
        self.assertIn("Private Study Beta", messages_by_recipient["other-owner@example.test"])
        self.assertNotIn("Study Alpha", messages_by_recipient["other-owner@example.test"])
        self.assertEqual(
            ExpiryReminder.objects.get(kit_lot=other_kit).recipient,
            other_owner,
        )

    def test_failed_send_is_logged_not_marked_and_can_be_retried(self):
        with patch(
            "inventory.management.commands.send_kit_alerts.send_expiry_reminder",
            side_effect=OSError("SMTP unavailable"),
        ):
            with self.assertLogs("inventory.alerts", level="ERROR"):
                with self.assertRaises(CommandError):
                    self.run_alerts()

        reminder = ExpiryReminder.objects.get(kit_lot=self.expiring_kit)
        self.assertIsNone(reminder.sent_at)
        self.assertEqual(reminder.attempt_count, 1)
        self.assertIn("SMTP unavailable", reminder.last_error)

        self.run_alerts()
        reminder.refresh_from_db()
        self.assertIsNotNone(reminder.sent_at)
        self.assertEqual(reminder.attempt_count, 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_missing_account_email_reports_error_without_sending(self):
        self.manager.email = ""
        self.manager.save(update_fields=["email"])

        with self.assertRaisesMessage(CommandError, "no valid email address"):
            self.run_alerts()

        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(ExpiryReminder.objects.filter(kit_lot=self.expiring_kit).exists())


class KitInventoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="viewer",
            email="viewer@example.test",
            password="test-password",
        )
        self.client.force_login(self.user)
        today = timezone.localdate()
        self.low_kit = KitLot.objects.create(
            owner=self.user,
            kit_name="Low stock study",
            lot_number="LOT-LOW",
            expiry_date=today + timedelta(days=90),
            quantity_available=5,
        )
        self.normal_kit = KitLot.objects.create(
            owner=self.user,
            kit_name="Available study",
            lot_number="LOT-OK",
            expiry_date=today + timedelta(days=90),
            quantity_available=6,
        )

    def test_fixed_threshold_marks_five_but_not_six_as_low_stock(self):
        self.assertTrue(self.low_kit.is_low_stock)
        self.assertFalse(self.normal_kit.is_low_stock)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["low_stock"]), [self.low_kit])
        self.assertEqual(response.context["low_stock_threshold"], 5)

    def test_form_uses_study_name_and_hides_legacy_fields(self):
        form = KitLotForm()

        self.assertEqual(form.fields["kit_name"].label, "Study name")
        self.assertNotIn("reorder_threshold", form.fields)
        self.assertNotIn("average_weekly_use", form.fields)
        self.assertNotIn("lead_time_days", form.fields)
        self.assertNotIn("storage_location", form.fields)

    def test_inventory_manager_can_open_edit_form_without_legacy_fields(self):
        permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="can_manage_inventory",
        )
        self.user.user_permissions.add(permission)

        response = self.client.get(f"/kits/{self.low_kit.pk}/edit/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Study name")
        self.assertNotContains(response, "Reorder threshold")
        self.assertNotContains(response, "Storage location")

    def test_inventory_page_omits_legacy_fields(self):
        response = self.client.get("/kits/")

        self.assertContains(response, "Study name")
        self.assertContains(response, "Low stock is 5 or fewer kits.")
        self.assertNotContains(response, "Reorder threshold")
        self.assertNotContains(response, "Storage location")

    def test_demo_seed_keeps_existing_inventory_records(self):
        existing_ids = {self.low_kit.pk, self.normal_kit.pk}

        call_command("seed_demo", "--owner-username", self.user.username, stdout=StringIO())

        self.assertTrue(KitLot.objects.filter(pk__in=existing_ids).count() == len(existing_ids))

    def test_user_without_inventory_permission_cannot_edit_or_send_test_email(self):
        edit_response = self.client.post(f"/kits/{self.low_kit.pk}/edit/")
        email_response = self.client.post("/kits/test-email/")

        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(email_response.status_code, 403)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TestEmailViewTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username="email-manager",
            email="account@example.test",
            password="test-password",
        )
        permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="can_manage_inventory",
        )
        self.manager.user_permissions.add(permission)
        self.client.force_login(self.manager)

    def test_test_email_is_sent_only_to_logged_in_account(self):
        response = self.client.post("/kits/test-email/")

        self.assertRedirects(response, "/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["account@example.test"])

    def test_missing_account_email_shows_clear_error(self):
        self.manager.email = ""
        self.manager.save(update_fields=["email"])

        response = self.client.post("/kits/test-email/", follow=True)

        self.assertContains(response, "does not have an email address")
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    AUTHORIZED_SIGNUP_EMAILS={"new.user@example.test", "existing@example.test"},
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class SignUpTests(TestCase):
    def registration_data(self, **overrides):
        data = {
            "email": "new.user@example.test",
            "username": "new-user",
            "password1": "Tide$Forest#8247",
            "password2": "Tide$Forest#8247",
        }
        data.update(overrides)
        return data

    def confirm_from_last_email(self):
        confirmation_url = mail.outbox[-1].body.strip().splitlines()[2]
        path = urlsplit(confirmation_url).path
        confirmation_page = self.client.get(path)
        self.assertEqual(confirmation_page.status_code, 200)
        self.assertContains(confirmation_page, "Confirm your email")
        return self.client.post(path)

    def test_signup_creates_hashed_unprivileged_user_and_redirects_to_login(self):
        response = self.client.post(
            "/accounts/signup/",
            self.registration_data(),
            follow=True,
        )

        user = get_user_model().objects.get(username="new-user")
        self.assertRedirects(response, "/accounts/login/")
        self.assertContains(response, "Your account has been created")
        self.assertEqual(user.email, "new.user@example.test")
        self.assertTrue(user.check_password("Tide$Forest#8247"))
        self.assertNotEqual(user.password, "Tide$Forest#8247")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_perm("inventory.can_manage_inventory"))
        self.assertFalse(user.is_active)
        self.assertFalse(
            self.client.login(
                username=user.username,
                password=self.registration_data()["password1"],
            )
        )
        confirmation_response = self.confirm_from_last_email()
        self.assertRedirects(confirmation_response, "/accounts/login/")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(self.client.login(username="new-user", password="Tide$Forest#8247"))

    def test_confirmation_get_does_not_activate_and_invalid_token_is_rejected(self):
        self.client.post("/accounts/signup/", self.registration_data())
        user = get_user_model().objects.get(username="new-user")
        confirmation_path = urlsplit(
            mail.outbox[-1].body.strip().splitlines()[2]
        ).path

        self.assertEqual(self.client.get(confirmation_path).status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

        invalid_path = reverse(
            "verify_signup",
            kwargs={"uidb64": "invalid", "token": "invalid"},
        )
        response = self.client.get(invalid_path, follow=True)
        self.assertContains(response, "confirmation link is invalid or expired")
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_confirmation_can_be_resent_without_revealing_account_existence(self):
        user = get_user_model().objects.create_user(
            username="pending-user",
            email="new.user@example.test",
            password=self.registration_data()["password1"],
            is_active=False,
        )

        response = self.client.post(
            "/accounts/signup/resend-confirmation/",
            {"email": user.email},
            follow=True,
        )

        self.assertContains(response, "a confirmation email may be sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])

    def test_unapproved_address_cannot_resend_confirmation(self):
        user = get_user_model().objects.create_user(
            username="unapproved-pending",
            email="outsider@example.test",
            password=self.registration_data()["password1"],
            is_active=False,
        )

        response = self.client.post(
            "/accounts/signup/resend-confirmation/",
            {"email": user.email},
            follow=True,
        )

        self.assertContains(response, "a confirmation email may be sent")
        self.assertEqual(len(mail.outbox), 0)

    def test_duplicate_username_and_email_are_rejected_case_insensitively(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="ExistingUser",
            email="existing@example.test",
            password="Safe$Password8247",
        )

        duplicate_username = SignUpForm(self.registration_data(username="existinguser"))
        self.assertFalse(duplicate_username.is_valid())
        self.assertIn("username", duplicate_username.errors)

        duplicate_email = SignUpForm(
            self.registration_data(email="EXISTING@example.test")
        )
        self.assertFalse(duplicate_email.is_valid())
        self.assertIn("email", duplicate_email.errors)

    def test_signup_requires_email_and_matching_strong_passwords(self):
        missing_email = SignUpForm(self.registration_data(email=""))
        self.assertFalse(missing_email.is_valid())
        self.assertIn("email", missing_email.errors)

        mismatched_password = SignUpForm(
            self.registration_data(password2="Different$Password8247")
        )
        self.assertFalse(mismatched_password.is_valid())
        self.assertIn("password2", mismatched_password.errors)

        weak_password = SignUpForm(
            self.registration_data(
                password1="password",
                password2="password",
            )
        )
        self.assertFalse(weak_password.is_valid())
        self.assertIn("password2", weak_password.errors)

    def test_signup_page_is_public_and_login_page_links_to_it(self):
        signup_response = self.client.get("/accounts/signup/")
        login_response = self.client.get("/accounts/login/")

        self.assertEqual(signup_response.status_code, 200)
        self.assertContains(signup_response, "Password confirmation")
        self.assertContains(login_response, 'href="/accounts/signup/"')

    def test_unapproved_email_cannot_register(self):
        response = self.client.post(
            "/accounts/signup/",
            self.registration_data(email="outsider@example.test"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not approved for registration")
        self.assertFalse(
            get_user_model().objects.filter(username="new-user").exists()
        )

    @override_settings(AUTHORIZED_SIGNUP_EMAILS=set())
    def test_registration_fails_closed_when_no_approved_emails_are_configured(self):
        response = self.client.post(
            "/accounts/signup/",
            self.registration_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not approved for registration")
        self.assertFalse(
            get_user_model().objects.filter(username="new-user").exists()
        )

    def test_inventory_pages_still_require_authentication(self):
        for path in ("/", "/kits/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertRedirects(response, f"/accounts/login/?next={path}")

    def test_all_private_pages_and_actions_require_authentication(self):
        private_owner = get_user_model().objects.create_user(
            username="private-route-owner",
            email="private-route@example.test",
            password="test-password",
        )
        private_kit = KitLot.objects.create(
            owner=private_owner,
            kit_name="Confidential route test kit",
            lot_number="PRIVATE-ROUTE",
            expiry_date=timezone.localdate() + timedelta(days=90),
            quantity_available=12,
        )
        private_routes = (
            ("/", "GET"),
            ("/kits/", "GET"),
            ("/kits/add/", "GET"),
            ("/kits/add/", "POST"),
            (f"/kits/{private_kit.pk}/edit/", "GET"),
            (f"/kits/{private_kit.pk}/edit/", "POST"),
            (f"/kits/{private_kit.pk}/delete/", "GET"),
            (f"/kits/{private_kit.pk}/delete/", "POST"),
            ("/kits/quick-update/", "GET"),
            ("/kits/quick-update/", "POST"),
            ("/kits/test-email/", "POST"),
            ("/stock-history/", "GET"),
            ("/stock-history/", "POST"),
            ("/profile/", "GET"),
            ("/profile/", "POST"),
            ("/", "POST"),
            ("/kits/", "POST"),
        )
        for path, method in private_routes:
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method.lower())(
                    path,
                    {
                        "owner": private_owner.pk,
                        "kit": private_kit.pk,
                        "kit_name": "Tampered private kit",
                        "quantity_available": 0,
                        "quantity_used": 999,
                        "reason": "Forged anonymous request",
                    },
                )
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/accounts/login/?next="))
                self.assertNotContains(
                    response,
                    "Confidential route test kit",
                    status_code=302,
                )
                self.assertNotContains(
                    response,
                    "private-route@example.test",
                    status_code=302,
                )

    def test_unauthenticated_admin_is_redirected_to_admin_login(self):
        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/admin/login/"))

    def test_newly_registered_user_can_manage_only_their_own_inventory(self):
        owner = get_user_model().objects.create_user(
            username="another-owner",
            email="another-owner@example.test",
            password="Owner$Password8247",
        )
        other_kit = KitLot.objects.create(
            owner=owner,
            kit_name="Private other kit",
            lot_number="OTHER-PRIVATE",
            expiry_date=timezone.localdate() + timedelta(days=90),
            quantity_available=10,
        )
        self.client.post("/accounts/signup/", self.registration_data())
        user = get_user_model().objects.get(username="new-user")
        self.assertFalse(user.has_perm("inventory.can_manage_inventory"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_active)
        self.assertFalse(
            self.client.login(
                username=user.username,
                password=self.registration_data()["password1"],
            )
        )
        confirmation_response = self.confirm_from_last_email()
        self.assertRedirects(confirmation_response, "/accounts/login/")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(self.client.login(username="new-user", password="Tide$Forest#8247"))

        create_response = self.client.post(
            "/kits/add/",
            {
                "kit_name": "My new kit",
                "kit_code": "",
                "lot_number": "MY-PRIVATE",
                "expiry_date": (timezone.localdate() + timedelta(days=90)).isoformat(),
                "quantity_available": 8,
                "reason": "Initial stock entry",
                "supplier_or_lab": "",
                "notes": "",
                "active": "on",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        own_kit = KitLot.objects.get(lot_number="MY-PRIVATE")
        self.assertEqual(own_kit.owner, user)

        self.assertEqual(self.client.get(f"/kits/{own_kit.pk}/edit/").status_code, 200)
        self.assertEqual(self.client.get(f"/kits/{other_kit.pk}/edit/").status_code, 404)
        self.assertEqual(self.client.get(f"/kits/{other_kit.pk}/delete/").status_code, 404)
        self.assertEqual(
            self.client.post(f"/kits/{other_kit.pk}/delete/").status_code,
            404,
        )
        self.assertTrue(KitLot.objects.filter(pk=other_kit.pk).exists())

        delete_confirmation = self.client.get(f"/kits/{own_kit.pk}/delete/")
        self.assertEqual(delete_confirmation.status_code, 200)
        delete_response = self.client.post(f"/kits/{own_kit.pk}/delete/")
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(KitLot.objects.filter(pk=own_kit.pk).exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class UserWorkspaceIsolationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user_a = user_model.objects.create_user(
            username="workspace-a",
            email="workspace-a@example.test",
            password="test-password",
        )
        self.user_b = user_model.objects.create_user(
            username="workspace-b",
            email="workspace-b@example.test",
            password="test-password",
        )
        permissions = Permission.objects.filter(
            content_type__app_label="inventory",
            codename="can_manage_inventory",
        )
        self.user_a.user_permissions.add(*permissions)
        self.user_b.user_permissions.add(*permissions)
        today = timezone.localdate()
        self.kit_a = KitLot.objects.create(
            owner=self.user_a,
            kit_name="Private study A",
            lot_number="PRIVATE-A",
            expiry_date=today + timedelta(days=90),
            quantity_available=10,
        )
        self.kit_b = KitLot.objects.create(
            owner=self.user_b,
            kit_name="Private study B",
            lot_number="PRIVATE-B",
            expiry_date=today + timedelta(days=90),
            quantity_available=10,
        )
        self.client.force_login(self.user_a)

    def test_each_user_sees_only_own_kits_dashboard_and_history(self):
        self.kit_a.quantity_available = 9
        self.kit_b.quantity_available = 9
        save_kit_lot(kit=self.kit_a, actor=self.user_a, reason="Initial test adjustment")
        save_kit_lot(kit=self.kit_b, actor=self.user_b, reason="Initial test adjustment")

        kits_response = self.client.get("/kits/")
        self.assertEqual(
            list(kits_response.context["kits"]),
            [self.kit_a],
        )
        self.assertContains(kits_response, "Private study A")
        self.assertNotContains(kits_response, "Private study B")

        dashboard_response = self.client.get("/")
        self.assertEqual(list(dashboard_response.context["expiring"]), [self.kit_a])
        self.assertNotContains(dashboard_response, "Private study B")

        history_response = self.client.get("/stock-history/")
        self.assertEqual(
            list(history_response.context["changes"].values_list("kit_lot_id", flat=True)),
            [self.kit_a.pk],
        )
        change = history_response.context["changes"].get()
        self.assertEqual(change.previous_quantity, 10)
        self.assertEqual(change.new_quantity, 9)
        self.assertEqual(change.changed_by, self.user_a)
        self.assertEqual(change.reason, "Initial test adjustment")
        self.assertNotContains(history_response, "Private study B")

    def test_dashboard_and_profile_do_not_disclose_other_users_workspace(self):
        save_kit_lot(
            kit=self.kit_b,
            actor=self.user_b,
            reason="Private history entry",
        )

        dashboard_response = self.client.get("/")
        profile_response = self.client.get("/profile/")

        for response in (dashboard_response, profile_response):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Private study B")
                self.assertNotContains(response, "workspace-b")
                self.assertNotContains(response, "workspace-b@example.test")

    def test_changing_another_users_record_id_cannot_read_or_edit_it(self):
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    f"/kits/{self.kit_b.pk}/edit/",
                    {
                        "kit_name": "Tampered",
                        "kit_code": "",
                        "lot_number": self.kit_b.lot_number,
                        "expiry_date": self.kit_b.expiry_date.isoformat(),
                        "quantity_available": 1,
                        "reason": "Unauthorized change attempt",
                        "supplier_or_lab": "",
                        "notes": "",
                        "active": "on",
                    } if method == "post" else None,
                )
                self.assertEqual(response.status_code, 404)

        self.assertEqual(KitLot.objects.get(pk=self.kit_b.pk).quantity_available, 10)
        self.assertFalse(StockChangeHistory.objects.filter(kit_lot=self.kit_b).exists())
        self.assertEqual(self.client.get("/visits/").status_code, 404)

    def test_changing_another_users_record_id_cannot_delete_or_update_stock(self):
        original_quantity = self.kit_b.quantity_available
        original_history_count = StockChangeHistory.objects.filter(
            kit_lot=self.kit_b
        ).count()

        delete_response = self.client.post(f"/kits/{self.kit_b.pk}/delete/")
        self.assertEqual(delete_response.status_code, 404)

        quick_update_page = self.client.get("/kits/quick-update/")
        token = quick_update_page.context["submission_token"]
        update_response = self.client.post(
            "/kits/quick-update/",
            {
                "kit": self.kit_b.pk,
                "quantity_used": 3,
                "reason": "Forged cross-workspace update",
                "submission_token": token,
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertTrue(update_response.context["form"].has_error("kit"))

        self.kit_b.refresh_from_db()
        self.assertEqual(self.kit_b.quantity_available, original_quantity)
        self.assertEqual(
            StockChangeHistory.objects.filter(kit_lot=self.kit_b).count(),
            original_history_count,
        )
        self.assertNotContains(update_response, "Private study B")

    def test_user_can_edit_own_kit_and_admin_queryset_is_workspace_scoped(self):
        response = self.client.post(
            f"/kits/{self.kit_a.pk}/edit/",
            {
                "kit_name": self.kit_a.kit_name,
                "kit_code": self.kit_a.kit_code,
                "lot_number": self.kit_a.lot_number,
                "expiry_date": self.kit_a.expiry_date.isoformat(),
                "quantity_available": 9,
                "reason": "Routine count correction",
                "supplier_or_lab": self.kit_a.supplier_or_lab,
                "notes": self.kit_a.notes,
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        change = StockChangeHistory.objects.get(kit_lot=self.kit_a)
        self.assertEqual(change.changed_by, self.user_a)
        self.assertEqual(change.reason, "Routine count correction")

        request = RequestFactory().get("/admin/inventory/kitlot/")
        request.user = self.user_a
        admin_queryset = KitLotAdmin(KitLot, admin.site).get_queryset(request)
        self.assertEqual(
            set(admin_queryset.values_list("pk", flat=True)),
            {self.kit_a.pk},
        )

    def test_new_kit_owner_is_forced_to_logged_in_user(self):
        response = self.client.post(
            "/kits/add/",
            {
                "owner": self.user_b.pk,
                "kit_name": "Created in A",
                "kit_code": "",
                "lot_number": "CREATED-A",
                "expiry_date": (timezone.localdate() + timedelta(days=120)).isoformat(),
                "quantity_available": 20,
                "reason": "New test lot",
                "supplier_or_lab": "",
                "notes": "",
                "active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = KitLot.objects.get(lot_number="CREATED-A")
        self.assertEqual(created.owner, self.user_a)
        self.assertEqual(
            StockChangeHistory.objects.get(kit_lot=created).changed_by,
            self.user_a,
        )

    def test_low_stock_email_goes_only_to_the_kit_owner(self):
        self.kit_a.quantity_available = 5
        with self.captureOnCommitCallbacks(execute=True):
            save_kit_lot(kit=self.kit_a, actor=self.user_a, reason="Low-stock test")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["workspace-a@example.test"])
        self.assertNotIn("Private study B", mail.outbox[0].body)

    def test_email_and_history_cannot_cross_workspace(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            ExpiryReminder.objects.create(
                kit_lot=self.kit_a,
                recipient=self.user_b,
                expiry_date=self.kit_a.expiry_date,
            )
        with self.assertRaises(ValidationError):
            StockChangeHistory.objects.create(
                kit_lot=self.kit_a,
                owner=self.user_a,
                changed_by=self.user_b,
                previous_quantity=10,
                new_quantity=9,
                reason="Cross-workspace attempt",
            )


class ProfileTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user_a = user_model.objects.create_user(
            username="profile-a",
            email="profile-a@example.test",
            password="test-password",
        )
        self.user_b = user_model.objects.create_user(
            username="profile-b",
            email="profile-b@example.test",
            password="test-password",
        )
        today = timezone.localdate()
        self.low_kit = KitLot.objects.create(
            owner=self.user_a,
            kit_name="Profile low stock",
            lot_number="PROFILE-LOW",
            expiry_date=today + timedelta(days=15),
            quantity_available=5,
        )
        self.normal_kit = KitLot.objects.create(
            owner=self.user_a,
            kit_name="Profile normal stock",
            lot_number="PROFILE-NORMAL",
            expiry_date=today + timedelta(days=31),
            quantity_available=10,
        )
        self.other_kit = KitLot.objects.create(
            owner=self.user_b,
            kit_name="Other user's kit",
            lot_number="OTHER-PROFILE",
            expiry_date=today + timedelta(days=1),
            quantity_available=1,
        )
        self.low_kit.quantity_available = 4
        save_kit_lot(
            kit=self.low_kit,
            actor=self.user_a,
            reason="Profile test stock history",
        )
        self.other_kit.quantity_available = 0
        save_kit_lot(
            kit=self.other_kit,
            actor=self.user_b,
            reason="Other user's private history",
        )

    def test_profile_requires_login(self):
        response = self.client.get("/profile/")

        self.assertRedirects(response, "/accounts/login/?next=/profile/")

    def test_profile_shows_only_current_users_details_and_statistics(self):
        self.client.force_login(self.user_a)
        response = self.client.get("/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "profile-a")
        self.assertContains(response, "profile-a@example.test")
        self.assertNotContains(response, "My inventory")
        self.assertNotContains(response, "Total kit lots")
        self.assertNotContains(response, "Low-stock lots")
        self.assertNotContains(response, "Lots expiring")
        self.assertContains(response, "Stock-change history records:</strong> 1")
        self.assertNotContains(response, "profile-b")
        self.assertNotContains(response, "Other user's kit")
        self.assertContains(response, 'href="/">Back to dashboard</a>')
        self.assertContains(response, 'method="post" action="/accounts/logout/"')
        self.assertContains(response, "Sign out")

    def test_profile_handles_empty_workspace(self):
        self.client.force_login(self.user_b)
        self.other_kit.delete()
        response = self.client.get("/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock-change history records:</strong> 0")

    def test_navigation_has_separators_and_no_sign_out_button(self):
        self.client.force_login(self.user_a)
        response = self.client.get("/profile/")

        self.assertContains(
            response,
            'Dashboard</a><span class="separator" aria-hidden="true">|</span><a href="/kits/">Kit Inventory',
        )
        self.assertContains(response, "Stock History")
        self.assertContains(response, "My Profile")
        self.assertEqual(response.content.count(b"Sign out"), 1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class QuickStockUpdateTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="quick-update-owner",
            email="quick-update@example.test",
            password="test-password",
        )
        self.other_user = get_user_model().objects.create_user(
            username="quick-update-other",
            email="other@example.test",
            password="test-password",
        )
        self.kit = KitLot.objects.create(
            owner=self.owner,
            kit_name="Quick update study",
            lot_number="QUICK-UPDATE",
            expiry_date=timezone.localdate() + timedelta(days=90),
            quantity_available=8,
        )
        self.client.force_login(self.owner)

    def get_submission_token(self):
        response = self.client.get("/kits/quick-update/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select lab kit")
        self.assertContains(response, "Quick update study")
        return response.context["submission_token"]

    def submit_usage(self, *, quantity_used=3, reason="Used for PCR testing", kit=None, token=None):
        token = token or self.get_submission_token()
        return self.client.post(
            "/kits/quick-update/",
            {
                "kit": (kit or self.kit).pk,
                "quantity_used": quantity_used,
                "reason": reason,
                "submission_token": token,
            },
        )

    def test_usage_deducts_stock_and_records_complete_history(self):
        token = self.get_submission_token()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.submit_usage(
                quantity_used=3,
                reason="Used for cell culture experiment",
                token=token,
            )

        self.assertRedirects(response, "/kits/quick-update/")
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.quantity_available, 5)
        change = StockChangeHistory.objects.get(kit_lot=self.kit)
        self.assertEqual(change.previous_quantity, 8)
        self.assertEqual(change.new_quantity, 5)
        self.assertEqual(change.quantity_used, 3)
        self.assertEqual(change.reason, "Used for cell culture experiment")
        self.assertEqual(change.changed_by, self.owner)
        self.assertEqual(change.owner, self.owner)
        self.assertIsNotNone(change.changed_at)

    def test_reason_is_optional(self):
        token = self.get_submission_token()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.submit_usage(quantity_used=2, reason="", token=token)

        self.assertRedirects(response, "/kits/quick-update/")
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.quantity_available, 6)
        change = StockChangeHistory.objects.get(kit_lot=self.kit)
        self.assertEqual(change.quantity_used, 2)
        self.assertEqual(change.reason, "")

    def test_invalid_quantity_and_insufficient_stock_are_rejected(self):
        invalid_submissions = (
            {"quantity_used": 0, "reason": "Invalid zero"},
            {"quantity_used": -1, "reason": "Invalid negative"},
            {"quantity_used": "not-a-number", "reason": "Invalid type"},
            {"quantity_used": 9, "reason": "More than available"},
        )
        for data in invalid_submissions:
            with self.subTest(data=data):
                response = self.submit_usage(
                    quantity_used=data["quantity_used"],
                    reason=data["reason"],
                )
                self.assertEqual(response.status_code, 200)
                self.kit.refresh_from_db()
                self.assertEqual(self.kit.quantity_available, 8)
                self.assertFalse(StockChangeHistory.objects.filter(kit_lot=self.kit).exists())
                self.assertEqual(len(mail.outbox), 0)

    def test_other_user_cannot_update_kit_by_changing_record_id(self):
        other_kit = KitLot.objects.create(
            owner=self.other_user,
            kit_name="Other user's private kit",
            lot_number="OTHER-QUICK",
            expiry_date=timezone.localdate() + timedelta(days=90),
            quantity_available=8,
        )
        response = self.submit_usage(
            quantity_used=2,
            reason="Unauthorized usage",
            kit=other_kit,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].has_error("kit"))
        other_kit.refresh_from_db()
        self.assertEqual(other_kit.quantity_available, 8)
        self.assertFalse(StockChangeHistory.objects.filter(kit_lot=other_kit).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_threshold_crossing_sends_owner_low_stock_notification(self):
        token = self.get_submission_token()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.submit_usage(
                quantity_used=3,
                reason="Used for PCR testing",
                token=token,
            )

        self.assertRedirects(response, "/kits/quick-update/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["quick-update@example.test"])
        self.assertIn("Available quantity: 5", mail.outbox[0].body)

    def test_replayed_submission_token_does_not_deduct_twice(self):
        token = self.get_submission_token()
        with self.captureOnCommitCallbacks(execute=True):
            first_response = self.submit_usage(quantity_used=2, token=token)
            replay_response = self.submit_usage(quantity_used=2, token=token)

        self.assertRedirects(first_response, "/kits/quick-update/")
        self.assertRedirects(replay_response, "/kits/quick-update/")
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.quantity_available, 6)
        self.assertEqual(StockChangeHistory.objects.filter(kit_lot=self.kit).count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_inventory_list_has_no_quick_update_form_and_dashboard_icon_links_form(self):
        list_response = self.client.get("/kits/")
        dashboard_response = self.client.get("/")

        self.assertNotContains(list_response, "Quick Update Stock")
        self.assertNotContains(list_response, 'name="reason"')
        self.assertContains(
            dashboard_response,
            'aria-label="Open Lab Kit Update"',
        )
        self.assertContains(
            dashboard_response,
            'href="/kits/quick-update/"',
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LowStockNotificationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_user(
            username="low-stock-manager",
            email="manager@example.test",
            password="test-password",
        )
        self.permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="can_manage_inventory",
        )
        self.manager.user_permissions.add(self.permission)
        self.client.force_login(self.manager)
        self.kit = KitLot.objects.create(
            owner=self.manager,
            kit_name="Low stock study",
            lot_number="LOT-LOW",
            expiry_date=timezone.localdate() + timedelta(days=90),
            quantity_available=6,
        )

    def update_stock(self, quantity):
        self.kit.quantity_available = quantity
        self.kit.save(update_fields=["quantity_available"])

    def test_edit_form_emails_only_active_managers_with_valid_addresses(self):
        user_model = get_user_model()
        invalid_manager = user_model.objects.create_user(
            username="invalid-email-manager",
            email="not-an-email",
            password="test-password",
        )
        invalid_manager.user_permissions.add(self.permission)
        inactive_manager = user_model.objects.create_user(
            username="inactive-manager",
            email="inactive@example.test",
            password="test-password",
            is_active=False,
        )
        inactive_manager.user_permissions.add(self.permission)
        user_model.objects.create_user(
            username="ordinary-user",
            email="ordinary@example.test",
            password="test-password",
        )

        other_manager = user_model.objects.create_user(
            username="other-workspace-manager",
            email="other-manager@example.test",
            password="test-password",
        )
        other_manager.user_permissions.add(self.permission)
        KitLot.objects.create(
            owner=other_manager,
            kit_name="Other private study",
            lot_number="OTHER-LOW",
            expiry_date=timezone.localdate() + timedelta(days=90),
            quantity_available=6,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/kits/{self.kit.pk}/edit/",
                {
                    "kit_name": self.kit.kit_name,
                    "kit_code": self.kit.kit_code,
                    "lot_number": self.kit.lot_number,
                    "expiry_date": self.kit.expiry_date.isoformat(),
                    "quantity_available": 5,
                    "reason": "Routine dispensing",
                    "supplier_or_lab": self.kit.supplier_or_lab,
                    "notes": self.kit.notes,
                    "active": "on",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["manager@example.test"])
        self.assertIn("Available quantity: 5", mail.outbox[0].body)

    def test_repeat_edits_do_not_resend_but_recovery_allows_new_alert(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.update_stock(5)
            self.update_stock(4)
            self.update_stock(6)
            self.update_stock(5)

        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(all(message.to == ["manager@example.test"] for message in mail.outbox))

    def test_admin_stock_change_sends_only_after_commit(self):
        request = RequestFactory().post("/admin/inventory/kitlot/")
        request.user = self.manager
        model_admin = KitLotAdmin(KitLot, admin.site)

        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                self.kit.quantity_available = 5
                model_admin.save_model(
                    request,
                    self.kit,
                    form=type("Form", (), {"cleaned_data": {"reason": "Routine dispensing"}})(),
                    change=True,
                )
                self.assertEqual(len(mail.outbox), 0)

        self.assertEqual(len(mail.outbox), 1)

    def test_rolled_back_stock_change_does_not_send(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with transaction.atomic():
                    self.update_stock(5)
                    raise RuntimeError("rollback")

        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.quantity_available, 6)

    def test_inactive_kit_lots_do_not_trigger_notifications(self):
        self.kit.active = False
        self.kit.save(update_fields=["active"])

        with self.captureOnCommitCallbacks(execute=True):
            self.update_stock(5)

        self.assertEqual(len(mail.outbox), 0)
