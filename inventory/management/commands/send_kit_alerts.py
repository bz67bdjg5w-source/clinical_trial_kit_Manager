import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from inventory.email_utils import (
    email_configuration_error,
    notification_email_for_user,
    send_expiry_reminder,
)
from inventory.models import ExpiryReminder, KitLot

logger = logging.getLogger("inventory.alerts")

class Command(BaseCommand):
    help = "Send each inventory manager one reminder per kit expiry date, up to 30 days before expiry."

    def handle(self, *args, **options):
        configuration_error = email_configuration_error()
        if configuration_error:
            raise CommandError(configuration_error)

        today = timezone.localdate()
        expiring_kits = KitLot.objects.filter(
            active=True,
            owner__is_active=True,
            expiry_date__gte=today,
            expiry_date__lte=today + timedelta(days=30),
        ).select_related("owner").order_by("expiry_date")
        failures = 0
        sent_count = 0
        missing_addresses = 0

        for kit in expiring_kits:
            recipient = kit.owner
            email = notification_email_for_user(recipient)
            if not email:
                missing_addresses += 1
                logger.error(
                    "Expiry reminder skipped: kit_lot_id=%s owner_user_id=%s has no valid active email address.",
                    kit.pk,
                    recipient.pk,
                )
                self.stderr.write(
                    f"Kit lot {kit.pk} owner user_id={recipient.pk} has no valid email address; reminder was not sent."
                )
                continue

            with transaction.atomic():
                reminder, _ = ExpiryReminder.objects.get_or_create(
                    kit_lot=kit,
                    recipient=recipient,
                    expiry_date=kit.expiry_date,
                )
                reminder = ExpiryReminder.objects.select_for_update().get(pk=reminder.pk)
                if reminder.sent_at is not None:
                    continue

                reminder.attempt_count += 1
                reminder.last_attempt_at = timezone.now()
                reminder.last_error = ""
                reminder.save(update_fields=["attempt_count", "last_attempt_at", "last_error"])
                try:
                    accepted = send_expiry_reminder(kit, recipient)
                    if accepted != 1:
                        raise RuntimeError("The email backend did not accept the expiry reminder.")
                except Exception as error:
                    reminder.last_error = str(error)[:2000] or error.__class__.__name__
                    reminder.save(update_fields=["last_error"])
                    failures += 1
                    logger.exception(
                        "Expiry reminder failed for kit_lot_id=%s owner_user_id=%s attempt=%s.",
                        kit.pk,
                        recipient.pk,
                        reminder.attempt_count,
                    )
                    self.stderr.write(
                        f"Expiry reminder failed for kit_lot_id={kit.pk}, owner user_id={recipient.pk}; it will be retried."
                    )
                else:
                    reminder.sent_at = timezone.now()
                    reminder.save(update_fields=["sent_at"])
                    sent_count += 1
                    logger.info(
                        "Expiry reminder accepted by email backend for kit_lot_id=%s owner_user_id=%s expiry_date=%s.",
                        kit.pk,
                        recipient.pk,
                        kit.expiry_date,
                    )

        if missing_addresses:
            raise CommandError(
                f"{missing_addresses} kit owner account(s) have no valid email address. Add a valid address to each account and retry."
            )
        if failures:
            raise CommandError(
                f"{failures} expiry reminder(s) failed and were not marked as sent; check the log and retry."
            )
        self.stdout.write(self.style.SUCCESS(
            f"Expiry reminder check complete: {sent_count} sent, {len(expiring_kits)} eligible kit lot(s) checked."
        ))
