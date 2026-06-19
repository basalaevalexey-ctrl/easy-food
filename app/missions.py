from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DailyMission:
    key: str
    title: str
    emoji: str
    description: str
    short_success_text: str


MISSIONS = {
    "photo_food": DailyMission(
        key="photo_food",
        title="Фото-старт",
        emoji="📸",
        description="Добавь хотя бы одну еду через фото.",
        short_success_text="Фото еды уже в дневнике. Отличный легкий старт.",
    ),
    "text_food": DailyMission(
        key="text_food",
        title="Текстовый ням",
        emoji="✍️",
        description="Добавь хотя бы одну еду текстом.",
        short_success_text="Текстовая запись добавлена. Все спокойно, день движется.",
    ),
    "full_day": DailyMission(
        key="full_day",
        title="Полный день",
        emoji="🍽",
        description="Запиши 3 приема пищи за день.",
        short_success_text="Три записи за день есть. Дневник выглядит собранно.",
    ),
    "calorie_range": DailyMission(
        key="calorie_range",
        title="Попади в коридор",
        emoji="🎯",
        description="Попади в 90–110% от дневной нормы калорий.",
        short_success_text="Сегодня ты попал в мягкий коридор цели.",
    ),
    "protein_day": DailyMission(
        key="protein_day",
        title="Белковый день",
        emoji="🍗",
        description="Добери хотя бы 80% цели по белку.",
        short_success_text="Белок хорошо добран. Тело скажет спасибо.",
    ),
    "breakfast": DailyMission(
        key="breakfast",
        title="Запиши завтрак",
        emoji="🌅",
        description="Добавь первую еду до 12:00.",
        short_success_text="Первая запись появилась до полудня. Мягкий старт дня засчитан.",
    ),
    "evening_summary": DailyMission(
        key="evening_summary",
        title="Закрой день",
        emoji="🌙",
        description="Открой дневной итог вечером.",
        short_success_text="Дневной итог открыт вечером. День аккуратно закрыт.",
    ),
    "adjust_portion": DailyMission(
        key="adjust_portion",
        title="Уточни порцию",
        emoji="⚖️",
        description="Поправь размер хотя бы одной порции.",
        short_success_text="Порция уточнена. Так оценка становится ближе к реальности.",
    ),
    "keep_streak": DailyMission(
        key="keep_streak",
        title="Сохрани Ням-стрик",
        emoji="🔥",
        description="Добавь хотя бы одну еду сегодня.",
        short_success_text="Еда записана, Ням-стрик держится.",
    ),
}

MISSION_ORDER = tuple(MISSIONS)
SIMPLE_MISSION_KEYS = ("photo_food", "text_food", "full_day", "keep_streak")


def mission_is_completed(mission_key: str, context: dict[str, Any]) -> bool:
    food_entries_today = int(context.get("food_entries_today") or 0)
    calories_today = float(context.get("calories_today") or 0)
    protein_today = float(context.get("protein_today") or 0)
    calorie_target = context.get("calorie_target")
    protein_target = context.get("protein_target")

    if mission_key == "photo_food":
        return int(context.get("photo_entries_today") or 0) >= 1
    if mission_key == "text_food":
        return int(context.get("text_entries_today") or 0) >= 1
    if mission_key == "full_day":
        return food_entries_today >= 3
    if mission_key == "calorie_range":
        return bool(calorie_target) and float(calorie_target) * 0.9 <= calories_today <= float(calorie_target) * 1.1
    if mission_key == "protein_day":
        return bool(protein_target) and protein_today >= float(protein_target) * 0.8
    if mission_key == "breakfast":
        first_entry_time = context.get("first_entry_time")
        return bool(first_entry_time) and str(first_entry_time) < "12:00:00"
    if mission_key == "evening_summary":
        return int(context.get("evening_today_opened") or 0) >= 1
    if mission_key == "adjust_portion":
        return int(context.get("portion_adjustments_today") or 0) >= 1
    if mission_key == "keep_streak":
        return food_entries_today >= 1
    return False


def mission_progress_text(mission_key: str, context: dict[str, Any]) -> str:
    food_entries_today = int(context.get("food_entries_today") or 0)
    calories_today = float(context.get("calories_today") or 0)
    protein_today = float(context.get("protein_today") or 0)
    calorie_target = context.get("calorie_target")
    protein_target = context.get("protein_target")

    if mission_is_completed(mission_key, context):
        return "Готово 💚"
    if mission_key == "photo_food":
        return "Пока фото-записей сегодня нет."
    if mission_key == "text_food":
        return "Пока текстовых записей сегодня нет."
    if mission_key == "full_day":
        return f"{min(food_entries_today, 3)}/3 записей еды."
    if mission_key == "calorie_range":
        if not calorie_target:
            return "Сначала нужна настроенная цель."
        return f"{round(calories_today)} из {int(calorie_target)} ккал."
    if mission_key == "protein_day":
        if not protein_target:
            return "Сначала нужна настроенная цель по белку."
        return f"{round(protein_today)} из {round(float(protein_target) * 0.8)} г белка."
    if mission_key == "breakfast":
        return "Нужна первая запись еды до 12:00."
    if mission_key == "evening_summary":
        return "Открой «Сегодня» после 18:00."
    if mission_key == "adjust_portion":
        return "Поправь порцию у любой записи."
    if mission_key == "keep_streak":
        return "Добавь хотя бы одну еду сегодня."
    return "Миссия ждет своего момента."
