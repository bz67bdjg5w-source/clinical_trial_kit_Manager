from django.core.exceptions import PermissionDenied, ValidationError
from django.db import router, transaction

from .models import KitLot, StockChangeHistory


def save_kit_lot(*, kit, actor, reason, quantity_used=None):
    reason = (reason or "").strip()
    if not reason and quantity_used is None:
        raise ValidationError({"reason": "Enter a reason for this stock change."})
    if not kit.owner_id or kit.owner_id != actor.pk:
        raise PermissionDenied("You can only manage kit lots in your own workspace.")

    using = router.db_for_write(KitLot, instance=kit)
    with transaction.atomic(using=using):
        if kit.pk:
            previous_quantity = (
                KitLot.objects.using(using)
                .select_for_update()
                .filter(pk=kit.pk, owner_id=actor.pk)
                .values_list("quantity_available", flat=True)
                .first()
            )
            if previous_quantity is None:
                raise PermissionDenied("You can only manage kit lots in your own workspace.")
        else:
            previous_quantity = None

        kit.save(using=using)
        if previous_quantity != kit.quantity_available:
            StockChangeHistory.objects.using(using).create(
                kit_lot=kit,
                owner=kit.owner,
                changed_by=actor,
                previous_quantity=previous_quantity,
                new_quantity=kit.quantity_available,
                quantity_used=quantity_used,
                reason=reason,
            )
    return kit


def record_kit_usage(*, kit_id, actor, quantity_used, reason):
    reason = (reason or "").strip()
    if not isinstance(quantity_used, int) or isinstance(quantity_used, bool) or quantity_used <= 0:
        raise ValidationError("Enter a positive whole number of kits used.")

    using = router.db_for_write(KitLot)
    with transaction.atomic(using=using):
        kit = (
            KitLot.objects.using(using)
            .select_for_update()
            .filter(pk=kit_id, owner_id=actor.pk, active=True)
            .first()
        )
        if kit is None:
            raise PermissionDenied("You can only manage active kit lots in your own workspace.")
        if quantity_used > kit.quantity_available:
            raise ValidationError("Cannot use more kits than the current available stock.")

        kit.quantity_available -= quantity_used
        return save_kit_lot(
            kit=kit,
            actor=actor,
            reason=reason,
            quantity_used=quantity_used,
        )
