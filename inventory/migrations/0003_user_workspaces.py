import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F


def require_explicit_ownership(apps, schema_editor):
    KitLot = apps.get_model("inventory", "KitLot")
    Visit = apps.get_model("inventory", "Visit")
    VisitKitRequirement = apps.get_model("inventory", "VisitKitRequirement")
    ExpiryReminder = apps.get_model("inventory", "ExpiryReminder")
    alias = schema_editor.connection.alias

    unowned_kits = KitLot.objects.using(alias).filter(owner__isnull=True).count()
    unowned_visits = Visit.objects.using(alias).filter(workspace_owner__isnull=True).count()
    cross_workspace_requirements = (
        VisitKitRequirement.objects.using(alias)
        .exclude(visit__workspace_owner_id=F("kit_lot__owner_id"))
        .count()
    )
    mismatched_reminders = (
        ExpiryReminder.objects.using(alias)
        .exclude(kit_lot__owner_id=F("recipient_id"))
        .count()
    )
    if unowned_kits or unowned_visits or cross_workspace_requirements or mismatched_reminders:
        raise RuntimeError(
            "Workspace migration stopped without changing inventory data: "
            f"{unowned_kits} kit lot(s) and {unowned_visits} visit(s) lack an owner; "
            f"{cross_workspace_requirements} visit-kit requirement(s) link different owners; "
            f"{mismatched_reminders} expiry reminder(s) target someone other than the kit owner. "
            "Assign each record to its verified owner and align reminder recipients, then retry."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_alter_kitlot_kit_name_expiryreminder"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="kitlot",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="kit_lots",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="visit",
            name="workspace_owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inventory_visits",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="StockChangeHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("previous_quantity", models.PositiveIntegerField(blank=True, null=True)),
                ("new_quantity", models.PositiveIntegerField()),
                ("reason", models.CharField(max_length=250)),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "changed_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_changes_made",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "kit_lot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stock_changes",
                        to="inventory.kitlot",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_change_history",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-changed_at", "-pk"]},
        ),
        migrations.RunPython(require_explicit_ownership, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="kitlot",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="kit_lots",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="visit",
            name="workspace_owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inventory_visits",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
