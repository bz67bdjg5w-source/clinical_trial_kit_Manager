import copy
import logging
import smtplib
from functools import partial

from django.conf import settings
from django.core.mail import BadHeaderError
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .email_utils import (
    email_configuration_error,
    notification_email_for_user,
    send_low_stock_notification,
)
from .models import KitLot

logger = logging.getLogger("inventory.alerts")


@receiver(pre_save, sender=KitLot)
def remember_previous_stock(
    sender,
    instance,
    using,
    raw=False,
    update_fields=None,
    **kwargs,
):
    if raw or not instance.pk:
        instance._previous_quantity_available = None
        return
    if update_fields is not None and "quantity_available" not in update_fields:
        instance._previous_quantity_available = None
        return
    instance._previous_quantity_available = (
        sender._default_manager.using(using)
        .filter(pk=instance.pk)
        .values_list("quantity_available", flat=True)
        .first()
    )


def notify_inventory_managers_of_low_stock(kit, using):
    configuration_error = email_configuration_error()
    if configuration_error:
        logger.error(
            "Low-stock notification not sent for kit_lot_id=%s: %s",
            kit.pk,
            configuration_error,
        )
        return

    email = notification_email_for_user(kit.owner)
    if not email:
        logger.warning(
            "Low-stock notification not sent for kit_lot_id=%s: owner user_id=%s has no valid active email address.",
            kit.pk, kit.owner_id,
        )
        return

    try:
        send_low_stock_notification(kit, email)
    except (BadHeaderError, OSError, RuntimeError, smtplib.SMTPException):
        logger.exception(
            "Low-stock notification failed for kit_lot_id=%s owner_user_id=%s.",
            kit.pk,
            kit.owner_id,
        )
    else:
        logger.info(
            "Low-stock notification accepted by email backend for kit_lot_id=%s owner_user_id=%s.",
            kit.pk,
            kit.owner_id,
        )


@receiver(post_save, sender=KitLot)
def notify_on_low_stock_crossing(
    sender,
    instance,
    created,
    using,
    raw=False,
    update_fields=None,
    **kwargs,
):
    previous_quantity = getattr(instance, "_previous_quantity_available", None)
    if hasattr(instance, "_previous_quantity_available"):
        del instance._previous_quantity_available

    if raw or created or previous_quantity is None:
        return
    if update_fields is not None and "quantity_available" not in update_fields:
        return
    if not instance.active:
        return
    if previous_quantity <= settings.LOW_STOCK_THRESHOLD:
        return
    if instance.quantity_available > settings.LOW_STOCK_THRESHOLD:
        return

    kit_snapshot = copy.copy(instance)
    transaction.on_commit(
        partial(notify_inventory_managers_of_low_stock, kit_snapshot, using),
        using=using,
    )
