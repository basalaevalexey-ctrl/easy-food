import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from app.competitions import (
    COMPETITION_LAUNCH_DATE,
    LEAGUE_TIER_BRONZE,
    LEAGUE_TIER_GOLD,
    LEAGUE_TIER_SILVER,
    calculate_competition_daily_score,
    competition_week_start,
    promote_league_tier,
)
from app.database import Database
from app.models import FoodEstimate


def estimate(calories: float, water_ml: float = 0) -> FoodEstimate:
    return FoodEstimate(
        is_food=True,
        title="Тестовая еда",
        description="Порция",
        calories=calories,
        protein=20,
        fat=10,
        carbs=30,
        water_ml=water_ml,
        confidence="high",
        comment="",
        not_food_reason="",
    )


class CompetitionScoringTests(unittest.TestCase):
    def test_rounds_run_from_wednesday_through_tuesday(self) -> None:
        self.assertEqual(competition_week_start(date(2026, 8, 19)), date(2026, 8, 19))
        self.assertEqual(competition_week_start(date(2026, 8, 25)), date(2026, 8, 19))
        self.assertEqual(competition_week_start(date(2026, 8, 26)), date(2026, 8, 26))

    def test_league_promotion_stops_at_gold(self) -> None:
        self.assertEqual(promote_league_tier(LEAGUE_TIER_BRONZE), LEAGUE_TIER_SILVER)
        self.assertEqual(promote_league_tier(LEAGUE_TIER_SILVER), LEAGUE_TIER_GOLD)
        self.assertEqual(promote_league_tier(LEAGUE_TIER_GOLD), LEAGUE_TIER_GOLD)

    def test_one_food_log_scores_only_once_per_day(self) -> None:
        score = calculate_competition_daily_score(
            food_entries=2,
            calories=100,
            calorie_target=2000,
            water_ml=0,
            water_target=2000,
        )
        self.assertEqual(score.food_logged_score, 50)
        self.assertEqual(score.total_score, 50)

    def test_calories_on_lower_and_upper_bounds_count(self) -> None:
        for calories in (1800, 2200):
            score = calculate_competition_daily_score(
                food_entries=1,
                calories=calories,
                calorie_target=2000,
                water_ml=0,
                water_target=2000,
            )
            self.assertEqual(score.calorie_target_score, 50)

    def test_calories_outside_range_do_not_count(self) -> None:
        for calories in (1799, 2201):
            score = calculate_competition_daily_score(
                food_entries=1,
                calories=calories,
                calorie_target=2000,
                water_ml=0,
                water_target=2000,
            )
            self.assertEqual(score.calorie_target_score, 0)

    def test_water_target_counts_once(self) -> None:
        score = calculate_competition_daily_score(
            food_entries=0,
            calories=0,
            calorie_target=2000,
            water_ml=2000,
            water_target=2000,
        )
        self.assertEqual(score.water_score, 20)
        self.assertEqual(score.total_score, 20)

    def test_perfect_day_requires_all_three_conditions(self) -> None:
        score = calculate_competition_daily_score(
            food_entries=1,
            calories=2000,
            calorie_target=2000,
            water_ml=2000,
            water_target=2000,
        )
        self.assertEqual(score.food_logged_score, 50)
        self.assertEqual(score.calorie_target_score, 50)
        self.assertEqual(score.water_score, 20)
        self.assertEqual(score.perfect_day_score, 20)
        self.assertEqual(score.total_score, 140)

    def test_streak_bonus_requires_two_previous_food_days(self) -> None:
        score = calculate_competition_daily_score(
            food_entries=1,
            calories=0,
            calorie_target=2000,
            water_ml=0,
            water_target=2000,
            food_streak_days_before_today=2,
        )
        self.assertEqual(score.streak_score, 30)


class CompetitionDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "competitions.sqlite3")
        self.database.init()
        self.competition_day = COMPETITION_LAUNCH_DATE + timedelta(days=1)
        self.database._competition_today = lambda: self.competition_day

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def set_goal(self, telegram_id: int, target: int = 2000) -> None:
        self.database.update_user_goal(
            telegram_id,
            {
                "sex": "male",
                "age": 30,
                "height": 180,
                "weight": 70,
                "goal": "maintain",
                "activity": "medium",
                "calorie_target": target,
                "protein_target": 100,
                "water_target": 200,
            },
        )
        user = self.database.get_or_create_user(telegram_id)
        with self.database.connect() as conn:
            conn.execute("UPDATE users SET water_target = 200 WHERE id = ?", (user.id,))

    def today_breakdown(self, telegram_id: int) -> dict:
        state = self.database.get_competition_state(telegram_id)
        self.assertTrue(state["eligible"])
        return state["today_score_breakdown"]

    def add_food(self, telegram_id: int, food: FoodEstimate, source: str = "text"):
        created_at = datetime.combine(
            self.competition_day,
            time(9, 0),
            tzinfo=timezone(timedelta(hours=3)),
        )
        return self.database.add_food_entry(telegram_id, food, source, created_at=created_at)

    def test_competition_waits_for_launch_without_creating_rows(self) -> None:
        self.database._competition_today = lambda: COMPETITION_LAUNCH_DATE - timedelta(days=1)
        telegram_id = 1000
        self.set_goal(telegram_id)

        state = self.database.get_competition_state(telegram_id)

        self.assertEqual(state["reason"], "not_started")
        with self.database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM competitions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM competition_participants").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM competition_daily_scores").fetchone()[0], 0)

    def test_food_and_water_changes_recalculate_the_same_day(self) -> None:
        telegram_id = 1001
        self.set_goal(telegram_id)
        entry = self.add_food(telegram_id, estimate(2000))
        score_after_first_entry = self.today_breakdown(telegram_id)["total"]
        self.assertGreater(score_after_first_entry, 0)

        self.add_food(telegram_id, estimate(100))
        self.assertGreaterEqual(self.today_breakdown(telegram_id)["total"], 0)

        self.database.add_water_entry(telegram_id, 200)
        score_after_water = self.today_breakdown(telegram_id)["total"]
        self.assertGreaterEqual(score_after_water, 0)

        self.database.delete_food_entry(entry.id, telegram_id)
        breakdown = self.today_breakdown(telegram_id)
        self.assertLessEqual(breakdown["total"], score_after_water)

    def test_deleting_food_recalculates_rotating_tasks(self) -> None:
        telegram_id = 1002
        self.set_goal(telegram_id)
        self.add_food(telegram_id, estimate(500))
        self.add_food(telegram_id, estimate(500))
        entry = self.add_food(telegram_id, estimate(500))
        before_delete = self.today_breakdown(telegram_id)["total"]
        self.assertGreater(before_delete, 0)
        self.database.delete_food_entry(entry.id, telegram_id)
        self.assertLessEqual(self.today_breakdown(telegram_id)["total"], before_delete)

    def test_user_has_no_more_than_one_active_competition(self) -> None:
        telegram_id = 1003
        self.set_goal(telegram_id)
        self.database.get_competition_state(telegram_id)
        self.database.update_user_goal(
            telegram_id,
            {
                "sex": "male", "age": 30, "height": 180, "weight": 70,
                "goal": "lose", "activity": "medium", "calorie_target": 1800,
                "protein_target": 126, "water_target": 200,
            },
        )
        user = self.database.get_or_create_user(telegram_id)
        with self.database.connect() as conn:
            active = conn.execute(
                """
                SELECT COUNT(*) FROM competition_participants
                JOIN competitions ON competitions.id = competition_participants.competition_id
                WHERE competition_participants.user_id = ? AND competitions.status = 'active'
                """,
                (user.id,),
            ).fetchone()[0]
        self.assertEqual(active, 1)

    def test_leaderboard_is_sorted_by_score(self) -> None:
        first, second = 1004, 1005
        self.set_goal(first)
        self.set_goal(second)
        self.database.set_user_display_name(first, "Аня")
        self.database.set_user_display_name(second, "Борис")
        self.add_food(first, estimate(2000))
        self.add_food(first, estimate(0))
        self.add_food(first, estimate(0))
        self.database.add_water_entry(first, 200)
        self.add_food(second, estimate(300))

        state = self.database.get_competition_state(second)
        self.assertEqual(state["participants"][0]["name"], "Аня")
        self.assertGreater(state["participants"][0]["score"], state["participants"][1]["score"])

    def test_leaderboard_breaks_score_ties_by_food_entry_count(self) -> None:
        one_entry, two_entries = 1007, 1008
        self.set_goal(one_entry, target=1000)
        self.set_goal(two_entries, target=1000)
        self.database.set_user_display_name(one_entry, "Одна запись")
        self.database.set_user_display_name(two_entries, "Две записи")

        self.add_food(one_entry, estimate(1000))
        self.add_food(two_entries, estimate(500))
        self.add_food(two_entries, estimate(500))

        state = self.database.get_competition_state(two_entries)
        self.assertEqual(state["participants"][0]["name"], "Две записи")
        self.assertEqual(state["participants"][0]["score"], state["participants"][1]["score"])

        competition_id = state["competition"]["id"]
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE competitions SET end_date = ? WHERE id = ?",
                ((self.competition_day + timedelta(days=1)).isoformat(), competition_id),
            )
            self.database._finalize_expired_competitions(conn, self.competition_day + timedelta(days=1))
        with self.database.connect() as conn:
            final = conn.execute(
                """
                SELECT users.display_name, competition_participants.final_rank
                FROM competition_participants
                JOIN users ON users.id = competition_participants.user_id
                WHERE competition_participants.competition_id = ?
                ORDER BY competition_participants.final_rank ASC
                """,
                (competition_id,),
            ).fetchall()
        self.assertEqual(final[0]["display_name"], "Две записи")
        self.assertEqual(final[0]["final_rank"], 1)

    def test_top_three_join_the_next_week_in_a_higher_league(self) -> None:
        telegram_id = 1009
        self.set_goal(telegram_id)
        state = self.database.get_competition_state(telegram_id)
        competition_id = state["competition"]["id"]
        next_week = date.fromisoformat(state["competition"]["start_date"]) + timedelta(days=7)
        user = self.database.get_or_create_user(telegram_id)

        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE competitions
                SET status = 'completed', end_date = ?
                WHERE id = ?
                """,
                (next_week.isoformat(), competition_id),
            )
            conn.execute(
                "UPDATE competition_participants SET final_rank = 3 WHERE competition_id = ?",
                (competition_id,),
            )
            promoted = self.database._get_or_join_weekly_competition(conn, user, next_week)
            self.assertEqual(promoted["league_tier"], LEAGUE_TIER_SILVER)

            conn.execute(
                "UPDATE competition_participants SET final_rank = 4 WHERE competition_id = ?",
                (competition_id,),
            )
            self.assertEqual(
                self.database._league_tier_for_user(conn, user.id, next_week),
                LEAGUE_TIER_BRONZE,
            )

    def test_completed_competition_keeps_final_rank(self) -> None:
        telegram_id = 1006
        self.set_goal(telegram_id)
        state = self.database.get_competition_state(telegram_id)
        competition_id = state["competition"]["id"]
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE competitions SET end_date = ? WHERE id = ?",
                (self.competition_day.isoformat(), competition_id),
            )
        self.database.finalize_expired_competitions()
        with self.database.connect() as conn:
            finished = conn.execute(
                "SELECT status FROM competitions WHERE id = ?",
                (competition_id,),
            ).fetchone()["status"]
            rank = conn.execute(
                "SELECT final_rank FROM competition_participants WHERE competition_id = ?",
                (competition_id,),
            ).fetchone()["final_rank"]
        self.assertEqual(finished, "completed")
        self.assertEqual(rank, 1)

    def test_league_podium_unlocks_trophy_achievement(self) -> None:
        telegram_id = 1010
        self.set_goal(telegram_id)
        state = self.database.get_competition_state(telegram_id)
        competition_id = state["competition"]["id"]
        with self.database.connect() as conn:
            conn.execute("UPDATE competitions SET status = 'completed' WHERE id = ?", (competition_id,))
            conn.execute(
                "UPDATE competition_participants SET final_rank = 2 WHERE competition_id = ?",
                (competition_id,),
            )

        unlocked = self.database.unlock_available_achievements(telegram_id)
        self.assertIn("league_podium", {achievement.key for achievement in unlocked})


if __name__ == "__main__":
    unittest.main()
