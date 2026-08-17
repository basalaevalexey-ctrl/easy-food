from __future__ import annotations

from dataclasses import dataclass


LEAGUE_TIER_BRONZE = "bronze"
LEAGUE_TIER_SILVER = "silver"
LEAGUE_TIER_GOLD = "gold"
LEAGUE_TIERS = (LEAGUE_TIER_BRONZE, LEAGUE_TIER_SILVER, LEAGUE_TIER_GOLD)
LEAGUE_PROMOTION_PLACES = 3
GROUP_CAPACITY = 10
FOOD_LOGGED_POINTS = 50
CALORIE_TARGET_POINTS = 50
WATER_TARGET_POINTS = 20
PERFECT_DAY_POINTS = 20
STREAK_POINTS = 30
DAILY_BASE_MAX_POINTS = (
    FOOD_LOGGED_POINTS + CALORIE_TARGET_POINTS + WATER_TARGET_POINTS + PERFECT_DAY_POINTS
)


def promote_league_tier(tier: str | None) -> str:
    """Return the next league tier, keeping the top tier unchanged."""
    try:
        index = LEAGUE_TIERS.index(str(tier or LEAGUE_TIER_BRONZE))
    except ValueError:
        return LEAGUE_TIER_BRONZE
    return LEAGUE_TIERS[min(index + 1, len(LEAGUE_TIERS) - 1)]


@dataclass(frozen=True)
class CompetitionDailyScore:
    food_logged_score: int
    calorie_target_score: int
    water_score: int
    perfect_day_score: int
    streak_score: int

    @property
    def total_score(self) -> int:
        return (
            self.food_logged_score
            + self.calorie_target_score
            + self.water_score
            + self.perfect_day_score
            + self.streak_score
        )


def calculate_competition_daily_score(
    *,
    food_entries: int,
    calories: float,
    calorie_target: int | None,
    water_ml: float,
    water_target: int | None,
    food_streak_days_before_today: int = 0,
) -> CompetitionDailyScore:
    """Build one idempotent competition score from the actual daily totals."""
    has_food = food_entries > 0
    calorie_in_range = bool(
        calorie_target
        and calorie_target > 0
        and calorie_target * 0.9 <= calories <= calorie_target * 1.1
    )
    water_complete = bool(water_target and water_target > 0 and water_ml >= water_target)
    perfect_day = has_food and calorie_in_range and water_complete
    streak_continues = has_food and food_streak_days_before_today >= 2

    return CompetitionDailyScore(
        food_logged_score=FOOD_LOGGED_POINTS if has_food else 0,
        calorie_target_score=CALORIE_TARGET_POINTS if calorie_in_range else 0,
        water_score=WATER_TARGET_POINTS if water_complete else 0,
        perfect_day_score=PERFECT_DAY_POINTS if perfect_day else 0,
        streak_score=STREAK_POINTS if streak_continues else 0,
    )
