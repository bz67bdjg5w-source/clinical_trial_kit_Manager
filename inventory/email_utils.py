from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import EmailValidator


def email_configuration_error():
    if settings.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
        if not settings.EMAIL_HOST:
            return "Email is not configured: set EMAIL_HOST in your .env file."
        if not settings.DEFAULT_FROM_EMAIL or settings.DEFAULT_FROM_EMAIL.endswith(".invalid"):
            return "Email is not configured: set DEFAULT_FROM_EMAIL to an address authorized by your SMTP service."
        if bool(settings.EMAIL_HOST_USER) != bool(settings.EMAIL_HOST_PASSWORD):
            return "Email is not configured: set both EMAIL_HOST_USER and EMAIL_HOST_PASSWORD, or leave both blank for an unauthenticated relay."
    return ""


def send_test_email(recipient):
    sent = send_mail(
        "Clinical Trial Kit Manager test email",
        "This test email checks the configured email service. It is not an expiry reminder.",
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        fail_silently=False,
    )
    if sent != 1:
        raise RuntimeError("The email backend did not accept the test message.")


def send_signup_confirmation(user, confirmation_url):
    sent = send_mail(
        "Confirm your Study Kit Tracker account",
        "\n".join([
            "A registration was requested for this email address.",
            "Confirm that you control this approved coordinator email address by opening the link below:",
            confirmation_url,
            "",
            "If you did not request this account, you can ignore this message.",
        ]),
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )
    if sent != 1:
        raise RuntimeError("The email backend did not accept the signup confirmation.")
    return sent


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
    return send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [recipient.email],
        fail_silently=False,
    )


def notification_email_for_user(user):
    if not user.is_active:
        return None
    email = (user.email or "").strip()
    if not email:
        return None
    validate_email = EmailValidator()
    try:
        validate_email(email)
    except ValidationError:
        return None
    return email


def send_low_stock_notification(kit, recipient_email):
    sent = send_mail(
        f"Low stock alert: {kit.kit_name} / lot {kit.lot_number}",
        "\n".join([
            "A lab kit lot has reached the low-stock threshold.",
            f"Study name: {kit.kit_name}",
            f"Lot number: {kit.lot_number}",
            f"Available quantity: {kit.quantity_available}",
            f"Low-stock threshold: {settings.LOW_STOCK_THRESHOLD}",
        ]),
        settings.DEFAULT_FROM_EMAIL,
        [recipient_email],
        fail_silently=False,
    )
    if sent != 1:
        raise RuntimeError("The email backend did not accept the low-stock notification.")
