from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Achievement:
    key: str
    title: str
    emoji: str
    description: str


ACHIEVEMENTS = {
    "first_nyam": Achievement(
        key="first_nyam",
        title="Первый ням",
        emoji="🥄",
        description="Первая запись еды в дневнике. Нямметр рад знакомству!",
    ),
    "in_rhythm": Achievement(
        key="in_rhythm",
        title="Вошел в ритм",
        emoji="🔥",
        description="Три дня с записями еды подряд. Спокойный ритм уже складывается.",
    ),
    "full_day": Achievement(
        key="full_day",
        title="Полный день",
        emoji="🍽",
        description="Сегодня в дневнике уже 3 записи еды. День выглядит собранно.",
    ),
    "target_day": Achievement(
        key="target_day",
        title="День в цель",
        emoji="🎯",
        description="Сегодня калории попали в мягкий коридор цели. Хорошее попадание.",
    ),
    "five_entries": Achievement(
        key="five_entries",
        title="Пять записей",
        emoji="💧",
        description="В дневнике уже 5 записей еды. Маленькими шагами получается легче.",
    ),
    "five_day_streak": Achievement(
        key="five_day_streak",
        title="5 дней подряд",
        emoji="🔥",
        description="Пять активных дней подряд. Привычка уже заметно крепнет.",
    ),
    "photo_lunch": Achievement(
        key="photo_lunch",
        title="Фото-обед",
        emoji="📸",
        description="Ты впервые добавил еду по фото. Быстро и без таблиц.",
    ),
    "ten_entries": Achievement(
        key="ten_entries",
        title="10 записей",
        emoji="🥦",
        description="В дневнике уже 10 записей еды. Нямметр начинает знать твой ритм.",
    ),
    "planner": Achievement(
        key="planner",
        title="Собранный день",
        emoji="🍱",
        description="У тебя уже 5 активных дней с Нямметром. Дневник становится полезной привычкой.",
    ),
    "sweet_control": Achievement(
        key="sweet_control",
        title="Баланс дня",
        emoji="🍬",
        description="15 записей еды в дневнике. Ты все лучше видишь общую картину дня.",
    ),
    "breakfast": Achievement(
        key="breakfast",
        title="Завтрак учтен",
        emoji="🌅",
        description="Первая запись до полудня. День начался с понятного учета.",
    ),
    "secret": Achievement(
        key="secret",
        title="Секрет",
        emoji="✨",
        description="50 записей еды в дневнике. Это уже настоящий опыт Нямметра.",
    ),
}

ACHIEVEMENT_ORDER = tuple(ACHIEVEMENTS)


def available_achievements(context: dict[str, Any]) -> list[Achievement]:
    result: list[Achievement] = []
    calorie_target = context.get("calorie_target")
    today_calories = float(context.get("today_calories") or 0)
    today_entries = int(context.get("today_entries") or 0)
    total_entries = int(context.get("total_entries") or 0)
    photo_entries = int(context.get("photo_entries") or 0)
    current_streak = int(context.get("current_streak") or 0)
    active_days = int(context.get("active_days") or 0)
    breakfast_entries = int(context.get("breakfast_entries") or 0)

    if total_entries >= 1:
        result.append(ACHIEVEMENTS["first_nyam"])
    if current_streak >= 3:
        result.append(ACHIEVEMENTS["in_rhythm"])
    if today_entries >= 3:
        result.append(ACHIEVEMENTS["full_day"])
    if calorie_target and today_entries >= 2:
        lower = float(calorie_target) * 0.9
        upper = float(calorie_target) * 1.1
        if lower <= today_calories <= upper:
            result.append(ACHIEVEMENTS["target_day"])
    if total_entries >= 5:
        result.append(ACHIEVEMENTS["five_entries"])
    if current_streak >= 5:
        result.append(ACHIEVEMENTS["five_day_streak"])
    if photo_entries >= 1:
        result.append(ACHIEVEMENTS["photo_lunch"])
    if total_entries >= 10:
        result.append(ACHIEVEMENTS["ten_entries"])
    if active_days >= 5:
        result.append(ACHIEVEMENTS["planner"])
    if total_entries >= 15:
        result.append(ACHIEVEMENTS["sweet_control"])
    if breakfast_entries >= 1:
        result.append(ACHIEVEMENTS["breakfast"])
    if total_entries >= 50:
        result.append(ACHIEVEMENTS["secret"])

    return result
