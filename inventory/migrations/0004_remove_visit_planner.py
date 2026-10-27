from django.db import migrations


def ensure_visit_tables_empty(apps, schema_editor):
    Visit = apps.get_model("inventory", "Visit")
    VisitKitRequirement = apps.get_model("inventory", "VisitKitRequirement")
    alias = schema_editor.connection.alias
    visits = Visit.objects.using(alias).count()
    requirements = VisitKitRequirement.objects.using(alias).count()
    if visits or requirements:
        raise RuntimeError(
            "Visit Planner removal stopped without deleting data: "
            f"{visits} visit(s) and {requirements} visit-kit requirement(s) remain. "
            "Export or explicitly remove these records before retrying."
        )


def remove_visit_permission(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    alias = schema_editor.connection.alias
    content_type = ContentType.objects.using(alias).filter(
        app_label="inventory",
        model="visit",
    ).first()
    if content_type:
        content_type.delete(using=alias)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0003_user_workspaces"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(ensure_visit_tables_empty, migrations.RunPython.noop),
        migrations.DeleteModel(name="VisitKitRequirement"),
        migrations.DeleteModel(name="Visit"),
        migrations.RunPython(remove_visit_permission, migrations.RunPython.noop),
    ]
