from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from inventory.models import KitLot

class Command(BaseCommand):
    help = "Load fake demonstration records. Never use real patient data."

    def add_arguments(self, parser):
        parser.add_argument("--owner-username", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            owner = user_model.objects.get(
                username=options["owner_username"],
                is_active=True,
            )
        except user_model.DoesNotExist as error:
            raise CommandError("The active demo-data owner account was not found.") from error

        today = timezone.localdate()
        kits = [
            KitLot.objects.get_or_create(owner=owner, lot_number="DEMO-L001", defaults={
                "kit_name": "Blood Collection Kit", "kit_code": "DEMO-BLD",
                "expiry_date": today + timedelta(days=24), "quantity_available": 8,
            })[0],
            KitLot.objects.get_or_create(owner=owner, lot_number="DEMO-L002", defaults={
                "kit_name": "PK Sample Kit", "kit_code": "DEMO-PK",
                "expiry_date": today + timedelta(days=75), "quantity_available": 15,
            })[0],
            KitLot.objects.get_or_create(owner=owner, lot_number="DEMO-L003", defaults={
                "kit_name": "ctDNA Kit", "kit_code": "DEMO-CTDNA",
                "expiry_date": today + timedelta(days=7), "quantity_available": 3,
            })[0],
        ]
        self.stdout.write(self.style.SUCCESS("Added any missing dummy kit lots without replacing existing data."))
