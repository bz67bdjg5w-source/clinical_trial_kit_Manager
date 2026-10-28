
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from inventory.email_utils import (
    email_configuration_error,
    notification_email_for_user,
    send_expiry_reminder,
    send_low_stock_notification,
)
from inventory.models import ExpiryReminder, KitLot

logger = logging.getLogger("inventory.alerts")


class Command(BaseCommand):
    help = "Send weekly expiry and low-stock reminders."

    def handle(self, *args, **options):
        configuration_error = email_configuration_error()
        if configuration_error:
            raise CommandError(configuration_error)

        today = timezone.localdate()
        now = timezone.now()
        weekly_cutoff = now - timedelta(days=7)

        kits = KitLot.objects.filter(
            active=True,
            owner__is_active=True,
            expiry_date__lte=today + timedelta(days=30),
        ).select_related("owner").order_by("expiry_date")

        expiry_sent = 0
        stock_sent = 0
        failures = 0
        missing_addresses = 0

        for kit in kits:
            recipient = kit.owner
            email = notification_email_for_user(recipient)

            if not email:
                missing_addresses += 1
                logger.error(
                    "No valid email for kit_lot_id=%s owner_user_id=%s",
                    kit.pk, recipient.pk,
                )
                continue

            # Expiry reminder: repeat weekly for the same lot and expiry date.
            try:
                with transaction.atomic():
                    reminder, _ = ExpiryReminder.objects.get_or_create(
                        kit_lot=kit,
                        recipient=recipient,
                        expiry_date=kit.expiry_date,
                    )
                    reminder = ExpiryReminder.objects.select_for_update().get(
                        pk=reminder.pk
                    )

                    if (
                        reminder.sent_at is None
                        or reminder.sent_at <= weekly_cutoff
                    ):
                        reminder.attempt_count += 1
                        reminder.last_attempt_at = now
                        reminder.last_error = ""
                        reminder.save(update_fields=[
                            "attempt_count", "last_attempt_at", "last_error"
                        ])

                        try:
                            accepted = send_expiry_reminder(kit, recipient)
                            if accepted != 1:
                                raise RuntimeError(
                                    "Email backend did not accept expiry reminder."
                                )
                        except Exception as error:
                            reminder.last_error = str(error)[:2000]
                            reminder.save(update_fields=["last_error"])
                            failures += 1
                            logger.exception(
                                "Expiry email failed for kit_lot_id=%s", kit.pk
                            )
                        else:
                            reminder.sent_at = timezone.now()
                            reminder.save(update_fields=["sent_at"])
                            expiry_sent += 1

            except Exception:
                failures += 1
                logger.exception(
                    "Could not process expiry reminder for kit_lot_id=%s",
                    kit.pk,
                )

            # Low-stock reminder: send when at or below this lot's reorder threshold.
            if kit.quantity_available <= kit.reorder_threshold:
                try:
                    accepted = send_low_stock_notification(kit, email)
                    if accepted != 1:
                        raise RuntimeError(
                            "Email backend did not accept low-stock reminder."
                        )
                    stock_sent += 1
                except Exception:
                    failures += 1
                    logger.exception(
                        "Low-stock email failed for kit_lot_id=%s", kit.pk
                    )

        self.stdout.write(
            f"Alert check complete: {expiry_sent} expiry email(s), "
            f"{stock_sent} low-stock email(s)."
        )

        if missing_addresses or failures:
            raise CommandError(
                f"{missing_addresses} account(s) missing email addresses; "
                f"{failures} email processing failure(s). Check logs."
            )
