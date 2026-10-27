from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

class KitLot(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="kit_lots",
    )
    kit_name = models.CharField("Study name", max_length=120)
    kit_code = models.CharField(max_length=60, blank=True)
    lot_number = models.CharField(max_length=80)
    expiry_date = models.DateField()
    quantity_available = models.PositiveIntegerField(default=0)
    reorder_threshold = models.PositiveIntegerField(default=0)
    average_weekly_use = models.PositiveIntegerField(default=0)
    lead_time_days = models.PositiveIntegerField(default=14)
    storage_location = models.CharField(max_length=120, blank=True)
    supplier_or_lab = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=250, blank=True)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["expiry_date", "kit_name", "lot_number"]
        permissions = [("can_manage_inventory", "Can manage kit inventory")]

    def __str__(self):
        return f"{self.kit_name} / {self.lot_number}"

    @property
    def days_to_expiry(self):
        return (self.expiry_date - timezone.localdate()).days

    @property
    def is_expired(self):
        return self.days_to_expiry < 0

    @property
    def is_low_stock(self):
        return self.quantity_available <= settings.LOW_STOCK_THRESHOLD


class ExpiryReminder(models.Model):
    kit_lot = models.ForeignKey(KitLot, on_delete=models.CASCADE, related_name="expiry_reminders")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="expiry_reminders")
    expiry_date = models.DateField()
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kit_lot", "recipient", "expiry_date"],
                name="unique_expiry_reminder_per_recipient_and_date",
            ),
        ]

    def clean(self):
        if (
            self.kit_lot_id
            and self.recipient_id
            and self.kit_lot.owner_id != self.recipient_id
        ):
            raise ValidationError("An expiry reminder can only be sent to the kit lot owner.")

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Expiry reminder for kit lot {self.kit_lot_id} to user {self.recipient_id}"


class StockChangeHistory(models.Model):
    kit_lot = models.ForeignKey(KitLot, on_delete=models.CASCADE, related_name="stock_changes")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_change_history",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_changes_made",
    )
    previous_quantity = models.PositiveIntegerField(null=True, blank=True)
    new_quantity = models.PositiveIntegerField()
    quantity_used = models.PositiveIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=250, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at", "-pk"]

    def clean(self):
        if self.kit_lot_id and self.owner_id and self.kit_lot.owner_id != self.owner_id:
            raise ValidationError("Stock history must have the same owner as its kit lot.")
        if self.owner_id and self.changed_by_id and self.changed_by_id != self.owner_id:
            raise ValidationError("Stock changes can only be made by the workspace owner.")

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Stock change for kit lot {self.kit_lot_id} by user {self.changed_by_id}"
