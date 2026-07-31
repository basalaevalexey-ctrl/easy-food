from __future__ import annotations

from app.admin_stats import AdminStatsService
from app.database import Database
from app.models import FoodEstimate


SMOKE_TELEGRAM_IDS = (9_000_000_000_001, 9_000_000_000_002)


def run_database_smoke_test(database: Database) -> None:
    primary_id, invited_id = SMOKE_TELEGRAM_IDS
    _cleanup(database)
    try:
        user = database.record_start(primary_id)
        database.update_user_goal(
            primary_id,
            {
                "sex": "male",
                "age": 30,
                "height": 180,
                "weight": 80.0,
                "goal": "maintain",
                "activity": "medium",
                "calorie_target": 2400,
                "protein_target": 120,
                "water_target": 2400,
            },
        )
        estimate = FoodEstimate(
            is_food=True,
            title="PostgreSQL smoke meal",
            description="Synthetic staging entry",
            calories=420,
            protein=30,
            fat=14,
            carbs=45,
            water_ml=120,
            confidence="high",
            comment="",
            not_food_reason="",
        )
        first_entry = database.add_food_entry(primary_id, estimate, source="text")
        database.add_food_entry(primary_id, estimate, source="photo")
        assert database.get_food_entry(first_entry.id, primary_id) is not None
        assert len(database.get_today_entries(primary_id)) == 2
        assert database.get_entries_between(primary_id, "2000-01-01", "2100-01-01")
        assert database.get_food_entry_days(primary_id)
        assert database.get_popular_foods(primary_id, limit=3)

        water = database.add_water_entry(primary_id, 200)
        assert water["total_ml"] >= 200
        database.set_reminder_time(primary_id, "09:00")
        database.set_water_reminders_enabled(primary_id, True)
        database.log_reminder(primary_id, "daily", "09:00", "sent")
        database.log_broadcast(primary_id, "postgres-smoke", "sent")

        database.mark_nyam_streak_if_first_today(primary_id)
        database.unlock_available_achievements(primary_id)
        database.get_daily_mission_status(primary_id)
        database.complete_daily_mission_if_ready(primary_id)
        progress = database.get_user_progress_stats(primary_id)
        assert progress["total_entries"] >= 2

        database.record_start(invited_id)
        assert database.register_referral(invited_id, primary_id)
        database.add_food_entry(invited_id, estimate, source="text")
        assert database.activate_referral(invited_id)
        assert database.get_referral_progress(primary_id)["activated"] == 1

        database.stats()
        database.database_info()
        database.admin_stats()
        database.admin_period_stats(7)
        database.admin_today_food()
        database.admin_week_food()
        database.admin_popular_food(days=7)
        database.admin_latest_users()

        stats = AdminStatsService(database)
        stats.get_stats_today()
        stats.get_stats_7d()
        stats.get_stats_30d()
        stats.get_stats_total()
        stats.get_daily_stats(7)
        stats.get_funnel_stats(7)
        stats.get_retention_stats()
        stats.get_channel_stats()
        stats.get_reminders_stats(7)
    finally:
        _cleanup(database)


def _cleanup(database: Database) -> None:
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM users WHERE telegram_id IN (?, ?)",
            SMOKE_TELEGRAM_IDS,
        )
