# Mehrere Kategorien je Objekt: Einzelkategorie wird in die Liste uebernommen.

from django.db import migrations, models


def copy_category(apps, schema_editor):
    for name in ("Location", "PublishedLocation"):
        Model = apps.get_model("locations", name)
        for obj in Model.objects.exclude(category="").only("pk", "category"):
            Model.objects.filter(pk=obj.pk).update(categories=[obj.category])


def copy_back(apps, schema_editor):
    for name in ("Location", "PublishedLocation"):
        Model = apps.get_model("locations", name)
        for obj in Model.objects.only("pk", "categories"):
            Model.objects.filter(pk=obj.pk).update(category=(obj.categories or [""])[0])


class Migration(migrations.Migration):

    dependencies = [
        ("locations", "0009_editorial_direct_publish"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="categories",
            field=models.JSONField(blank=True, default=list, verbose_name="Kategorien"),
        ),
        migrations.AddField(
            model_name="publishedlocation",
            name="categories",
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(copy_category, copy_back),
        migrations.RemoveField(model_name="location", name="category"),
        migrations.RemoveField(model_name="publishedlocation", name="category"),
    ]
