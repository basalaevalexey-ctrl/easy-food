from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


LEAGUE_TIER_BRONZE = "bronze"
LEAGUE_TIER_SILVER = "silver"
LEAGUE_TIER_GOLD = "gold"
LEAGUE_TIERS = (LEAGUE_TIER_BRONZE, LEAGUE_TIER_SILVER, LEAGUE_TIER_GOLD)
LEAGUE_PROMOTION_PLACES = 3
GROUP_CAPACITY = 10
# The first public round begins on Wednesday, 19 August 2026 (Moscow time).
# Until then no competition participant or score row may be created.
COMPETITION_LAUNCH_DATE = date(2026, 8, 19)
COMPETITION_START_WEEKDAY = 2  # Wednesday, where Monday is 0.
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


def competition_is_started(day: date) -> bool:
    return day >= COMPETITION_LAUNCH_DATE


def competition_week_start(day: date) -> date:
    """Return the Wednesday that starts the current Wednesday-Tuesday round."""
    return day - timedelta(days=(day.weekday() - COMPETITION_START_WEEKDAY) % 7)


COMPETITION_TASK_SETS = (
    ("food", "calories", "water", "perfect"),
    ("food", "three_meals", "protein", "water"),
    ("food", "calories", "photo", "breakfast"),
    ("three_meals", "protein", "calories_light", "water"),
)

COMPETITION_TASKS = {
    "food": ("Питание записано", "Добавь хотя бы одну запись еды.", 50),
    "calories": ("Калории в диапазоне", "Попади в 90-110% своей нормы калорий.", 50),
    "water": ("Норма воды", "Добери дневную норму воды с учётом еды.", 20),
    "perfect": ("Полный день", "Запиши еду, попади в норму калорий и добери воду.", 20),
    "three_meals": ("Три приёма пищи", "Добавь три записи еды за день.", 35),
    "protein": ("Белковый день", "Добери хотя бы 80% цели по белку.", 35),
    "photo": ("Фото еды", "Добавь хотя бы одну еду по фото.", 20),
    "breakfast": ("Запиши завтрак", "Добавь первую еду до 12:00.", 20),
    "calories_light": ("Калории в диапазоне", "Попади в 90-110% своей нормы калорий.", 45),
}


def competition_tasks_for_day(competition_id: int, score_day: date) -> list[dict[str, str | int]]:
    task_keys = COMPETITION_TASK_SETS[(competition_id + score_day.toordinal()) % len(COMPETITION_TASK_SETS)]
    return [
        {"key": key, "title": COMPETITION_TASKS[key][0], "description": COMPETITION_TASKS[key][1], "points": COMPETITION_TASKS[key][2]}
        for key in task_keys
    ]


def calculate_competition_task_scores(
    tasks: list[dict[str, str | int]], *, food_entries: int, calories: float,
    calorie_target: int | None, water_ml: float, water_target: int | None,
    protein: float, protein_target: int | None, photo_entries: int,
    first_entry_before_noon: bool,
) -> dict[str, int]:
    calorie_in_range = bool(calorie_target and calorie_target * 0.9 <= calories <= calorie_target * 1.1)
    water_complete = bool(water_target and water_ml >= water_target)
    completed = {
        "food": food_entries > 0,
        "calories": calorie_in_range,
        "calories_light": calorie_in_range,
        "water": water_complete,
        "perfect": food_entries > 0 and calorie_in_range and water_complete,
        "three_meals": food_entries >= 3,
        "protein": bool(protein_target and protein >= protein_target * 0.8),
        "photo": photo_entries > 0,
        "breakfast": food_entries > 0 and first_entry_before_noon,
    }
    return {str(task["key"]): int(task["points"]) if completed.get(str(task["key"]), False) else 0 for task in tasks}


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
