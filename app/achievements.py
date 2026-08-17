from dataclasses import dataclass
from typing import Any


VEGETABLE_MARKERS = (
    "овощ",
    "салат",
    "помидор",
    "томат",
    "огур",
    "морков",
    "капуст",
    "броккол",
    "перец",
    "кабач",
    "баклаж",
    "свек",
    "тыкв",
    "редис",
    "зелень",
    "шпинат",
)

SWEET_MARKERS = (
    "сахар",
    "конфет",
    "шоколад",
    "печенье",
    "печенья",
    "торт",
    "пирож",
    "морож",
    "десерт",
    "зефир",
    "мармелад",
    "вафл",
    "пончик",
    "донат",
    "булоч",
    "кекс",
    "варень",
    "сироп",
)


def daily_food_signals(entries: list[tuple[str, str]]) -> tuple[int, int]:
    vegetable_entries = 0
    sweet_entries = 0
    for title, description in entries:
        text = f"{title or ''} {description or ''}".lower().replace("ё", "е")
        text = text.replace("сладкий перец", "перец")
        for phrase in ("без сахара", "без сахар", "без сладкого"):
            text = text.replace(phrase, " ")
        vegetable_entries += int(any(marker in text for marker in VEGETABLE_MARKERS))
        sweet_entries += int(any(marker in text for marker in SWEET_MARKERS))
    return vegetable_entries, sweet_entries


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
        emoji="📝",
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
        emoji="📚",
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
        emoji="⚖️",
        description="15 записей еды в дневнике. Ты все лучше видишь общую картину дня.",
    ),
    "no_sweets_day": Achievement(
        key="no_sweets_day",
        title="День без сладкого",
        emoji="🍬",
        description="За день добавлено минимум 3 приема пищи — и среди них не было сладкого.",
    ),
    "vegetable_day": Achievement(
        key="vegetable_day",
        title="Овощной день",
        emoji="🥦",
        description="Овощи встретились минимум в двух приемах пищи за день.",
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
    "referral_one": Achievement(
        key="referral_one",
        title="Позвал к столу",
        emoji="💚",
        description="Первый друг начал пользоваться Нямметром по твоему приглашению.",
    ),
    "referral_five": Achievement(
        key="referral_five",
        title="Ням-компания",
        emoji="🍽",
        description="Пять друзей начали пользоваться Нямметром по твоему приглашению.",
    ),
    "referral_ten": Achievement(
        key="referral_ten",
        title="Большой стол",
        emoji="🏆",
        description="Десять друзей присоединились к Нямметру по твоему приглашению.",
    ),
    "league_podium": Achievement(
        key="league_podium",
        title="Кубок лиги",
        emoji="🏆",
        description="Ты вошел в тройку лидеров Лиги недели. Заслуженный кубок!",
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
    vegetable_entries_today = int(context.get("vegetable_entries_today") or 0)
    sweet_entries_today = int(context.get("sweet_entries_today") or 0)
    referral_count = int(context.get("referral_count") or 0)
    league_podiums = int(context.get("league_podiums") or 0)

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
    if today_entries >= 3 and sweet_entries_today == 0:
        result.append(ACHIEVEMENTS["no_sweets_day"])
    if vegetable_entries_today >= 2:
        result.append(ACHIEVEMENTS["vegetable_day"])
    if breakfast_entries >= 1:
        result.append(ACHIEVEMENTS["breakfast"])
    if total_entries >= 50:
        result.append(ACHIEVEMENTS["secret"])
    if referral_count >= 1:
        result.append(ACHIEVEMENTS["referral_one"])
    if referral_count >= 5:
        result.append(ACHIEVEMENTS["referral_five"])
    if referral_count >= 10:
        result.append(ACHIEVEMENTS["referral_ten"])
    if league_podiums >= 1:
        result.append(ACHIEVEMENTS["league_podium"])

    return result
