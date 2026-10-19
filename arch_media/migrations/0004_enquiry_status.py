from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("arch_media", "0003_enquiry"),
    ]

    operations = [
        migrations.AddField(
            model_name="enquiry",
            name="status",
            field=models.CharField(
                choices=[("new", "New"), ("replied", "Replied"), ("archived", "Archived")],
                default="new",
                max_length=20,
            ),
        ),
    ]
