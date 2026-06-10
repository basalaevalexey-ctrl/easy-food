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
    reminder_time: str | None
    reminder_last_sent_date: str | None
    created_at: datetime


@dataclass
class FoodEstimate:
    title: str
    description: str
    calories: float
    protein: float
    fat: float
    carbs: float
    confidence: str
    comment: str


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
    confidence: str
    source: str
    created_at: datetime
