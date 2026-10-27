
import json
import os
import urllib.error
import urllib.request

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator


def email_configuration_error():
    if not getattr(settings, "BREVO_API_KEY", ""):
        return "Email is not configured: set BREVO_API_KEY."
    if (
        not settings.DEFAULT_FROM_EMAIL
        or settings.DEFAULT_FROM_EMAIL.endswith(".invalid")
    ):
        return "Email is not configured: set DEFAULT_FROM_EMAIL to a verified Brevo sender."
    return ""


def _send_brevo_email(subject, message, recipients):
    api_key = getattr(settings, "BREVO_API_KEY", "")
    sender_email = settings.DEFAULT_FROM_EMAIL

    if not api_key:
        raise RuntimeError("BREVO_API_KEY is not configured.")

    if not sender_email or sender_email.endswith(".invalid"):
        raise RuntimeError("DEFAULT_FROM_EMAIL is not configured correctly.")

    payload = {
        "sender": {"email": sender_email},
        "to": [{"email": email} for email in recipients],
        "subject": subject,
        "textContent": message,
    }

    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status not in (200, 201, 202):
                raise RuntimeError(
                    f"Brevo returned unexpected status {response.status}."
                )
   except urllib.error.HTTPError as exc:
    details = exc.read().decode("utf-8", errors="replace")
    raise RuntimeError(
        f"Brevo rejected the email request (HTTP {exc.code}): {details}"
    ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(
            "Could not connect to Brevo's email API. Check the network "
            "connection and try again."
        ) from None

    return 1


def send_test_email(recipient):
    return _send_brevo_email(
        "Clinical Trial Kit Manager test email",
        "This test email checks the configured email service. "
        "It is not an expiry reminder.",
        [recipient],
    )


def send_signup_confirmation(user, confirmation_url):
    message = "\n".join([
        "A registration was requested for this email address.",
        "Confirm that you control this approved coordinator email address "
        "by opening the link below:",
        confirmation_url,
        "",
        "If you did not request this account, you can ignore this message.",
    ])

    return _send_brevo_email(
        "Confirm your Study Kit Tracker account",
        message,
        [user.email],
    )


def send_expiry_reminder(kit, recipient):
    days_remaining = kit.days_to_expiry

    subject = f"Kit expiry reminder: {kit.kit_name} / lot {kit.lot_number}"
    message = "\n".join([
        "A lab kit is approaching its expiry date.",
        f"Study name: {kit.kit_name}",
        f"Lot number: {kit.lot_number}",
        f"Expiry date: {kit.expiry_date}",
        f"Days remaining: {days_remaining}",
    ])

    return _send_brevo_email(subject, message, [recipient.email])


def notification_email_for_user(user):
    if not user.is_active:
        return None

    email = (user.email or "").strip()
    if not email:
        return None

    try:
        EmailValidator()(email)
    except ValidationError:
        return None

    return email


def send_low_stock_notification(kit, recipient_email):
    subject = f"Low stock alert: {kit.kit_name} / lot {kit.lot_number}"
    message = "\n".join([
        "A lab kit lot has reached the low-stock threshold.",
        f"Study name: {kit.kit_name}",
        f"Lot number: {kit.lot_number}",
        f"Available quantity: {kit.quantity_available}",
        f"Low-stock threshold: {settings.LOW_STOCK_THRESHOLD}",
    ])

    return _send_brevo_email(subject, message, [recipient_email])
