from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("db", "0121_alter_estimate_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfirmedAction",
            fields=[
                ("action_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("action_type", models.CharField(max_length=64)),
                ("status", models.CharField(default="pending", max_length=16)),
                ("result", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="db.user",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="db.workspace",
                    ),
                ),
            ],
            options={"db_table": "crete_ai_confirmed_actions"},
        ),
    ]
