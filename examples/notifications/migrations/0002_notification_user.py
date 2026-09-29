from django.conf import settings
from django.db import migrations, models


def delete_ownerless(apps, schema_editor):
    # Rows from before notifications had an owner were shown to everyone; there
    # is no one to give them to.
    apps.get_model("notifications", "Notification").objects.filter(user__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="notifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(delete_ownerless, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="notification",
            name="user",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="notifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
