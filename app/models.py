from dataclasses import dataclass
from datetime import datetime


@dataclass
class User:
    id: int
    telegram_id: int
    sex: str | None
    age: int | None
    height: int | None
    weight: float | None
    goal: str | None
    activity: str | None
    calorie_target: int | None
    protein_target: int | None
    water_target: int | None
    goal_set_at: str | None
    reminder_time: str | None
    reminder_last_sent_date: str | None
    water_reminders_enabled: bool
    water_reminder_skip_date: str | None
    current_streak: int
    best_streak: int
    last_active_date: str | None
    activation_step: int
    last_activation_message_at: str | None
    activation_disabled: bool
    display_name: str | None
    created_at: datetime


@dataclass
class FoodEstimate:
    is_food: bool
    title: str
    description: str
    calories: float
    protein: float
    fat: float
    carbs: float
    water_ml: float
    confidence: str
    comment: str
    not_food_reason: str


@dataclass
class FoodEntry:
    id: int
    user_id: int
    title: str
    description: str
    calories: float
    protein: float
    fat: float
    carbs: float
    water_ml: float
    confidence: str
    source: str
    created_at: datetime
