from datetime import timedelta
import logging
import secrets
import smtplib

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import BadHeaderError
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils import timezone
from .models import KitLot, StockChangeHistory
from .forms import KitLotForm, QuickStockUpdateForm, ResendSignupConfirmationForm, SignUpForm
from .email_utils import (
    email_configuration_error,
    send_signup_confirmation as send_signup_confirmation_message,
    send_test_email as send_test_email_message,
)
from .services import record_kit_usage, save_kit_lot

logger = logging.getLogger("inventory.alerts")
QUICK_UPDATE_TOKEN_SESSION_KEY = "quick_stock_update_token"


def signup(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.email = form.cleaned_data["email"]
            user.is_active = False
            try:
                with transaction.atomic():
                    user.save()
                    _send_signup_confirmation(request, user)
            except (BadHeaderError, OSError, RuntimeError, smtplib.SMTPException):
                logger.exception("Signup confirmation email failed for user_id=%s.", user.pk)
                messages.error(
                    request,
                    "We could not send the confirmation email. Please try again later.",
                )
            else:
                messages.success(
                    request,
                    "Your account has been created. Check your email to confirm before signing in.",
                )
            return redirect("login")
    else:
        form = SignUpForm()
    return render(request, "registration/signup.html", {"form": form})


def _send_signup_confirmation(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    confirmation_path = reverse(
        "verify_signup",
        kwargs={"uidb64": uid, "token": default_token_generator.make_token(user)},
    )
    return send_signup_confirmation_message(
        user,
        request.build_absolute_uri(confirmation_path),
    )


def verify_signup(request, uidb64, token):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    user = None
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = get_user_model()._default_manager.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist):
        pass

    if (
        user is not None
        and not user.is_active
        and user.email.casefold() in settings.AUTHORIZED_SIGNUP_EMAILS
        and default_token_generator.check_token(user, token)
    ):
        if request.method == "GET":
            return render(request, "registration/confirm_signup.html")
        user.is_active = True
        user.save(update_fields=["is_active"])
        messages.success(request, "Your email is confirmed. You can now sign in.")
        return redirect("login")

    messages.error(
        request,
        "This confirmation link is invalid or expired. Request a new confirmation link from the signup page or contact your site administrator.",
    )
    return redirect("signup")


def resend_signup_confirmation(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    if request.method == "POST":
        form = ResendSignupConfirmationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip()
            user = get_user_model()._default_manager.filter(
                email__iexact=email,
                is_active=False,
            ).first()
            if user is None:
                logger.error(
                    "DEBUG: Confirmation resend skipped; no inactive account found."
                )
            elif email.casefold() not in settings.AUTHORIZED_SIGNUP_EMAILS:
                logger.error(
                    "DEBUG: Confirmation resend skipped; email is not authorized."
                )
            else:
                try:
                    _send_signup_confirmation(request, user)
                    logger.warning(
                        "DEBUG: Confirmation email send function completed."
                    )
                except Exception:
                    logger.exception(
                        "DEBUG: Confirmation email sending failed."
                    )

            messages.info(
                request,
                "If this is an approved address with a pending account, a confirmation email may be sent. If you do not receive one, check the address and try again later.",
            )
            return redirect("login")
    else:
        form = ResendSignupConfirmationForm()

    return render(request, "registration/resend_signup_confirmation.html", {"form": form})


@login_required
def dashboard(request):
    today = timezone.localdate()
    soon = today + timedelta(days=90)
    kits = KitLot.objects.filter(owner=request.user, active=True)
    expiring = kits.filter(expiry_date__gte=today, expiry_date__lte=soon).order_by("expiry_date")
    expired = kits.filter(expiry_date__lt=today).order_by("expiry_date")
    low_stock = kits.filter(quantity_available__lte=settings.LOW_STOCK_THRESHOLD).order_by("quantity_available")
    return render(request, "inventory/dashboard.html", {
        "expiring": expiring, "expired": expired, "low_stock": low_stock,
        "today": today,
        "low_stock_threshold": settings.LOW_STOCK_THRESHOLD,
    })

@login_required
def kit_list(request):
    kits = KitLot.objects.filter(owner=request.user, active=True).order_by("expiry_date")
    return render(
        request,
        "inventory/kit_list.html",
        {"kits": kits, "low_stock_threshold": settings.LOW_STOCK_THRESHOLD},
    )


@login_required
def quick_update_stock(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    if request.method == "GET":
        form = QuickStockUpdateForm(user=request.user)
    else:
        expected_token = request.session.get(QUICK_UPDATE_TOKEN_SESSION_KEY, "")
        submitted_token = request.POST.get("submission_token", "")
        if not expected_token or not secrets.compare_digest(expected_token, submitted_token):
            messages.error(request, "This stock update was already submitted or has expired. Please try again.")
            return redirect("quick_update_stock")
        request.session.pop(QUICK_UPDATE_TOKEN_SESSION_KEY, None)
        form = QuickStockUpdateForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                record_kit_usage(
                    kit_id=form.cleaned_data["kit"].pk,
                    actor=request.user,
                    quantity_used=form.cleaned_data["quantity_used"],
                    reason=form.cleaned_data["reason"],
                )
            except (PermissionDenied, ValidationError) as error:
                form.add_error("quantity_used", error)
            else:
                messages.success(request, "Stock usage recorded.")
                return redirect("quick_update_stock")

    submission_token = secrets.token_urlsafe(32)
    request.session[QUICK_UPDATE_TOKEN_SESSION_KEY] = submission_token
    return render(
        request,
        "inventory/quick_update_stock.html",
        {
            "form": form,
            "submission_token": submission_token,
            "selected_kit_id": form.data.get("kit", "") if form.is_bound else "",
        },
    )

@login_required
def kit_create(request):
    if request.method == "POST":
        form = KitLotForm(request.POST)
        if form.is_valid():
            kit = form.save(commit=False)
            kit.owner = request.user
            save_kit_lot(kit=kit, actor=request.user, reason=form.cleaned_data["reason"])
            messages.success(request, "Kit lot added.")
            return redirect("kit_list")
    else:
        form = KitLotForm()
    return render(request, "inventory/form.html", {"form": form, "title": "Add kit lot", "back_url": "kit_list"})

@login_required
def edit_kit(request, pk):
    kit = get_object_or_404(KitLot, pk=pk, owner=request.user)
    if request.method == "POST":
        form = KitLotForm(request.POST, instance=kit)
        if form.is_valid():
            save_kit_lot(
                kit=form.save(commit=False),
                actor=request.user,
                reason=form.cleaned_data["reason"],
            )
            messages.success(request, "Kit lot updated.")
            return redirect("dashboard")
    else:
        form = KitLotForm(instance=kit)
    return render(request, "inventory/edit_kit.html", {"form": form, "kit": kit})


@login_required
def delete_kit(request, pk):
    kit = get_object_or_404(KitLot, pk=pk, owner=request.user)
    if request.method == "POST":
        kit.delete()
        messages.success(request, "Kit lot deleted.")
        return redirect("kit_list")
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])
    return render(request, "inventory/delete_kit.html", {"kit": kit})


@login_required
@permission_required("inventory.can_manage_inventory", raise_exception=True)
def send_test_email(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    recipient = request.user.email.strip()
    if not recipient:
        messages.error(request, "Your account does not have an email address. Add one to your account before sending a test email.")
        return redirect("dashboard")
    configuration_error = email_configuration_error()
    if configuration_error:
        messages.error(request, configuration_error)
        logger.error("Test email not sent for user_id=%s: %s", request.user.pk, configuration_error)
        return redirect("dashboard")
    try:
        send_test_email_message(recipient)
    except Exception:
        logger.exception("Test email failed for user_id=%s", request.user.pk)
        messages.error(request, "The test email could not be sent. Check the server log and SMTP settings, then try again.")
    else:
        logger.info("Test email accepted by email backend for user_id=%s", request.user.pk)
        messages.success(request, f"Test email accepted by the configured email service for {recipient}. Inbox delivery is not confirmed.")
    return redirect("dashboard")

@login_required
def stock_history(request):
    changes = StockChangeHistory.objects.filter(owner=request.user).select_related(
        "kit_lot", "changed_by"
    )
    return render(request, "inventory/stock_history.html", {"changes": changes})


@login_required
def profile(request):
    today = timezone.localdate()
    kits = KitLot.objects.filter(owner=request.user)
    active_kits = kits.filter(active=True)
    expiring_cutoff = today + timedelta(days=30)
    statistics = {
        "total_kit_lots": kits.count(),
        "low_stock_kit_lots": active_kits.filter(
            quantity_available__lte=settings.LOW_STOCK_THRESHOLD
        ).count(),
        "expiring_kit_lots": active_kits.filter(
            expiry_date__gte=today,
            expiry_date__lte=expiring_cutoff,
        ).count(),
        "stock_change_count": StockChangeHistory.objects.filter(
            owner=request.user
        ).count(),
    }
    return render(
        request,
        "inventory/profile.html",
        {
            "profile_user": request.user,
            "statistics": statistics,
        },
    )
