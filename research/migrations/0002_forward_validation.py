from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("research", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ForwardCohort",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        default="Forward Cohort",
                        max_length=96,
                    ),
                ),
                (
                    "engine_version",
                    models.CharField(
                        default="1.3",
                        max_length=16,
                    ),
                ),
                (
                    "window_ticks",
                    models.PositiveIntegerField(default=25000),
                ),
                ("stake", models.FloatField(default=1.0)),
                (
                    "status",
                    models.CharField(
                        default="active",
                        max_length=24,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ForwardMarket",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("symbol", models.CharField(max_length=32)),
                ("name", models.CharField(blank=True, max_length=128)),
                ("anchor_epoch", models.BigIntegerField()),
                ("next_epoch", models.BigIntegerField()),
                (
                    "windows_completed",
                    models.PositiveIntegerField(default=0),
                ),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cohort",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="markets",
                        to="research.forwardcohort",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="ForwardWindow",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("window_number", models.PositiveIntegerField()),
                ("start_epoch", models.BigIntegerField()),
                ("end_epoch", models.BigIntegerField()),
                ("tick_count", models.PositiveIntegerField()),
                (
                    "pip_size",
                    models.PositiveSmallIntegerField(default=2),
                ),
                ("data_hash", models.CharField(max_length=64)),
                ("tests_run", models.PositiveIntegerField(default=0)),
                (
                    "discovery_candidates",
                    models.PositiveIntegerField(default=0),
                ),
                (
                    "validated_candidates",
                    models.PositiveIntegerField(default=0),
                ),
                (
                    "live_candidates",
                    models.PositiveIntegerField(default=0),
                ),
                ("results", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "market",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="windows",
                        to="research.forwardmarket",
                    ),
                ),
            ],
            options={
                "ordering": ["market_id", "window_number"],
            },
        ),
        migrations.AddConstraint(
            model_name="forwardmarket",
            constraint=models.UniqueConstraint(
                fields=("cohort", "symbol"),
                name="unique_forward_market_per_cohort",
            ),
        ),
        migrations.AddConstraint(
            model_name="forwardwindow",
            constraint=models.UniqueConstraint(
                fields=("market", "window_number"),
                name="unique_forward_window_number",
            ),
        ),
    ]
