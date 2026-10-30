
from django.db import migrations, models
import django.db.models.deletion


def assign_existing_kit_lots_to_studies(apps, schema_editor):
    Study = apps.get_model("inventory", "Study")
    KitLot = apps.get_model("inventory", "KitLot")

    database = schema_editor.connection.alias

    owner_ids = (
        KitLot.objects.using(database)
        .filter(study__isnull=True)
        .values_list("owner_id", flat=True)
        .distinct()
    )

    for owner_id in owner_ids:
        study, created = Study.objects.using(database).get_or_create(
            owner_id=owner_id,
            name="Existing Inventory",
        )

        KitLot.objects.using(database).filter(
            owner_id=owner_id,
            study__isnull=True,
        ).update(study_id=study.pk)


class Migration(migrations.Migration):

    dependencies = [
        (
            "inventory",
            "0007_alter_kitlot_kit_name_study_kitlot_study_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(
            assign_existing_kit_lots_to_studies,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="kitlot",
            name="study",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="kit_lots",
                to="inventory.study",
            ),
        ),
    ]
