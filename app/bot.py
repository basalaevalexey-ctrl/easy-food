import asyncio
import logging
import re
from datetime import datetime
from io import BytesIO

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.calorie_calculator import calculate_targets
from app.config import load_config
from app.database import Database
from app.keyboards import (
    admin_keyboard,
    activity_keyboard,
    food_actions,
    goal_keyboard,
    main_menu,
    reminder_keyboard,
    reminder_time_keyboard,
    sex_keyboard,
)
from app.models import FoodEntry, User
from app.openai_client import FoodRecognitionClient, NotFoodError, OpenAIRecognitionError


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()
config = load_config()
db = Database(config.database_path, legacy_paths=config.legacy_database_paths)
food_ai = FoodRecognitionClient(config.openai_api_key, config.openai_model)


class SetupGoal(StatesGroup):
    age = State()
    height = State()
    weight = State()


class PortionCorrection(StatesGroup):
    grams = State()
    dish = State()


class ReminderSetup(StatesGroup):
    custom_time = State()


CONFIDENCE_LABELS = {
    "low": "низкая",
    "medium": "средняя",
    "high": "высокая",
}

GOAL_LABELS = {
    "lose": "похудеть",
    "maintain": "поддерживать",
    "gain": "набрать",
}

ACTIVITY_LABELS = {
    "low": "низкая",
    "medium": "средняя",
    "high": "высокая",
}


def round_num(value: float) -> int:
    return round(float(value))


def today_totals(entries: list[FoodEntry]) -> dict[str, float]:
    return {
        "calories": sum(entry.calories for entry in entries),
        "protein": sum(entry.protein for entry in entries),
        "fat": sum(entry.fat for entry in entries),
        "carbs": sum(entry.carbs for entry in entries),
    }


def format_food_saved(entry: FoodEntry, user: User, entries: list[FoodEntry], is_photo: bool = False) -> str:
    totals = today_totals(entries)
    lines = ["Записал 🍽", "", entry.title]
    if entry.description and entry.description.strip().lower() != entry.title.strip().lower():
        lines.extend(["", entry.description])
    lines.extend(["", "Примерно:"])
    lines.extend(
        [
            f"{round_num(entry.calories)} ккал",
            f"Б: {round_num(entry.protein)} г",
            f"Ж: {round_num(entry.fat)} г",
            f"У: {round_num(entry.carbs)} г",
            "",
        ]
    )
    if user.calorie_target:
        left = max(0, user.calorie_target - round_num(totals["calories"]))
        lines.append(f"Осталось на сегодня: {left} ккал")
    if is_photo:
        lines.append("Оценка по фото примерная.")
    lines.append(f"Точность: {CONFIDENCE_LABELS.get(entry.confidence, 'средняя')}")
    return "\n".join(lines)


async def answer_not_food(message: Message, reason: str = "") -> None:
    text = "Похоже, это не еда, поэтому я ничего не записал 🙂"
    if reason:
        text += f"\n\nПричина: {reason}"
    text += "\n\nМожно отправить фото блюда или написать, что ты съел. Если нужно, нажми «Инструкция»."
    await message.answer(text)


async def send_food_entry(message: Message, entry: FoodEntry, is_photo: bool = False) -> None:
    user = db.get_or_create_user(message.from_user.id)
    entries = db.get_today_entries(message.from_user.id)
    await message.answer(
        format_food_saved(entry, user, entries, is_photo=is_photo),
        reply_markup=food_actions(entry.id, can_fix_dish=entry.source == "photo"),
    )


def format_today(user: User, entries: list[FoodEntry]) -> str:
    totals = today_totals(entries)
    target = user.calorie_target
    protein_target = user.protein_target

    if target:
        left = target - round_num(totals["calories"])
        calorie_line = f"Калории: {round_num(totals['calories'])} / {target} ккал"
        left_line = f"Осталось: {max(0, left)} ккал" if left >= 0 else f"Перебор: {abs(left)} ккал"
    else:
        calorie_line = f"Калории: {round_num(totals['calories'])} ккал"
        left_line = "Цель пока не настроена."

    protein_line = f"Белки: {round_num(totals['protein'])}"
    if protein_target:
        protein_line += f" / {protein_target} г"
    else:
        protein_line += " г"

    lines = [
        "Сегодня",
        "",
        calorie_line,
        left_line,
        protein_line,
        f"Жиры: {round_num(totals['fat'])} г",
        f"Углеводы: {round_num(totals['carbs'])} г",
        "",
        "Еда:",
    ]

    if not entries:
        lines.append("Пока записей нет.")
    else:
        for entry in entries:
            lines.append(f"- {entry.title}: {round_num(entry.calories)} ккал")
    return "\n".join(lines)


def parse_positive_int(text: str, min_value: int, max_value: int) -> int | None:
    try:
        value = int(text.strip())
    except ValueError:
        return None
    if min_value <= value <= max_value:
        return value
    return None


def parse_positive_float(text: str, min_value: float, max_value: float) -> float | None:
    normalized = text.strip().replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    if min_value <= value <= max_value:
        return value
    return None


def normalize_reminder_time(text: str) -> str | None:
    value = text.strip()
    match = re.fullmatch(r"([01]?\d|2[0-3])[:. ]([0-5]\d)", value)
    if not match:
        match = re.fullmatch(r"([01]?\d|2[0-3])", value)
        if not match:
            return None
        return f"{int(match.group(1)):02d}:00"
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def is_admin(telegram_id: int) -> bool:
    return telegram_id in config.admin_ids


def format_admin_stats() -> str:
    stats = db.admin_stats()
    users_total = int(stats["users_total"])
    users_with_goal = int(stats["users_with_goal"])
    users_with_reminders = int(stats["users_with_reminders"])
    entries_total = int(stats["entries_total"])
    entries_today = int(stats["entries_today"])
    entries_week = int(stats["entries_week"])

    average_entries = entries_total / users_total if users_total else 0
    return (
        "Админка Нямметра\n\n"
        f"Пользователей всего: {users_total}\n"
        f"С настроенной целью: {users_with_goal}\n"
        f"С напоминаниями: {users_with_reminders}\n\n"
        f"Записей еды всего: {entries_total}\n"
        f"Сегодня: {entries_today}\n"
        f"За 7 дней: {entries_week}\n"
        f"В среднем на пользователя: {average_entries:.1f}\n\n"
        f"Активных сегодня: {int(stats['active_today'])}\n"
        f"Активных за 7 дней: {int(stats['active_week'])}\n\n"
        f"Фото-записей: {int(stats['photo_entries'])}\n"
        f"Текстовых записей: {int(stats['text_entries'])}\n\n"
        f"Ккал сегодня: {round_num(stats['calories_today'])}\n"
        f"Ккал за 7 дней: {round_num(stats['calories_week'])}"
    )


def format_admin_today() -> str:
    rows = db.admin_today_food()
    lines = ["Сегодня по пользователям", ""]
    if not rows:
        lines.append("Сегодня записей еды пока нет.")
    else:
        for row in rows:
            lines.append(
                f"{row['telegram_id']}: {row['entries']} записей, "
                f"{round_num(row['calories'] or 0)} ккал, Б {round_num(row['protein'] or 0)} г"
            )
    return "\n".join(lines)


def format_admin_week() -> str:
    rows = db.admin_week_food()
    lines = ["Последние 7 дней", ""]
    if not rows:
        lines.append("За неделю записей пока нет.")
    else:
        for row in rows:
            lines.append(
                f"{row['day']}: {row['users']} пользователей, "
                f"{row['entries']} записей, {round_num(row['calories'] or 0)} ккал"
            )
    return "\n".join(lines)


def format_admin_users() -> str:
    rows = db.admin_latest_users()
    lines = ["Последние пользователи", ""]
    if not rows:
        lines.append("Пользователей пока нет.")
    else:
        for row in rows:
            goal_status = "цель есть" if row["calorie_target"] else "без цели"
            reminder = row["reminder_time"] or "без напоминаний"
            lines.append(
                f"{row['telegram_id']}: {row['entries']} записей, {goal_status}, "
                f"{reminder}, с {row['created_at']}"
            )
    return "\n".join(lines)


@router.message(Command("start"))
async def start(message: Message) -> None:
    db.get_or_create_user(message.from_user.id)
    await message.answer(
        "Привет, я Нямметр 🍽\n\n"
        "Помогаю считать калории без весов и таблиц.\n\n"
        "Просто отправь фото еды или напиши, что съел — я примерно посчитаю калории, белки, жиры и углеводы.\n\n"
        "Важно: это примерная оценка, не медицинская рекомендация.",
        reply_markup=main_menu(),
    )
    await message.answer(
        "Во сколько тебе обычно напоминать про учет еды?",
        reply_markup=reminder_keyboard(),
    )


@router.callback_query(F.data == "reminder:choose")
async def reminder_choose(callback: CallbackQuery) -> None:
    await callback.message.answer("Выбери удобное время:", reply_markup=reminder_time_keyboard())
    await callback.answer()


@router.callback_query(F.data == "reminder:disable")
async def reminder_disable(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    db.set_reminder_time(callback.from_user.id, None)
    await callback.message.answer("Хорошо, не буду напоминать.")
    await callback.answer()


@router.callback_query(F.data.startswith("reminder:time:"))
async def reminder_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    reminder_time_value = callback.data.removeprefix("reminder:time:")
    db.set_reminder_time(callback.from_user.id, reminder_time_value)
    await callback.message.answer(
        f"Договорились! Буду напоминать про учет еды ежедневно в {reminder_time_value}."
    )
    await callback.answer()


@router.callback_query(F.data == "reminder:custom")
async def reminder_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ReminderSetup.custom_time)
    await callback.message.answer("Напиши время в формате 08:30 или 19:00.")
    await callback.answer()


@router.message(ReminderSetup.custom_time)
async def reminder_custom_apply(message: Message, state: FSMContext) -> None:
    reminder_time_value = normalize_reminder_time(message.text or "")
    if reminder_time_value is None:
        await message.answer("Не понял время. Напиши, например: 08:30 или 19:00.")
        return
    db.set_reminder_time(message.from_user.id, reminder_time_value)
    await state.clear()
    await message.answer(f"Договорились! Буду напоминать про учет еды ежедневно в {reminder_time_value}.")


@router.message(Command("help"))
@router.message(F.text == "Инструкция")
async def help_command(message: Message) -> None:
    await message.answer(
        "Инструкция по Нямметру 🍽\n\n"
        "1. Как добавить еду\n"
        "📸 Отправь фото блюда.\n"
        "✍️ Или напиши текстом: «2 яйца, творог 200 г, банан».\n\n"
        "2. Что я посчитаю\n"
        "🔥 Калории\n"
        "🥩 Белки\n"
        "🥑 Жиры\n"
        "🍚 Углеводы\n\n"
        "3. Как исправить запись\n"
        "Под каждой записью есть кнопки:\n"
        "➖ Порция меньше\n"
        "➕ Порция больше\n"
        "⚖️ Указать граммы\n"
        "🗑 Удалить\n"
        "Если еда была по фото, можно нажать «Исправить блюдо» и написать, что там на самом деле.\n\n"
        "4. Где смотреть прогресс\n"
        "📊 Нажми «Сегодня» или напиши /today — покажу калории, БЖУ и список еды за день.\n"
        "📅 Нажми «Дневник» или напиши /history — покажу последние 7 дней.\n\n"
        "5. Цель и напоминания\n"
        "🎯 Через «Настроить цель» можно задать норму калорий и белка.\n"
        "⏰ После старта можно выбрать время ежедневного напоминания.\n\n"
        "Важно: все оценки примерные, особенно по фото. Нямметр помогает вести учет, но не заменяет врача или нутрициолога."
    )


@router.message(Command("today"))
@router.message(F.text == "Сегодня")
async def today_command(message: Message) -> None:
    user = db.get_or_create_user(message.from_user.id)
    entries = db.get_today_entries(message.from_user.id)
    await message.answer(format_today(user, entries))


@router.message(Command("history"))
@router.message(F.text == "Дневник")
async def history_command(message: Message) -> None:
    user = db.get_or_create_user(message.from_user.id)
    rows = db.get_daily_history(message.from_user.id)
    lines = ["Последние 7 дней", ""]
    if not rows:
        lines.append("Пока записей нет.")
    else:
        for row in rows:
            calories = round_num(row["calories"] or 0)
            protein = round_num(row["protein"] or 0)
            if not user.calorie_target:
                status = "цель не настроена"
            elif calories < user.calorie_target * 0.9:
                status = "недобор"
            elif calories <= user.calorie_target * 1.1:
                status = "норма"
            else:
                status = "перебор"
            lines.append(f"{row['day']}: {calories} ккал, Б {protein} г, {status}")
    await message.answer("\n".join(lines))


@router.message(Command("admin"))
async def admin_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для админа.")
        return
    db.get_or_create_user(message.from_user.id)
    await message.answer(format_admin_stats(), reply_markup=admin_keyboard())


@router.message(Command("stats"))
async def stats_command(message: Message) -> None:
    await admin_command(message)


@router.callback_query(F.data.startswith("admin:"))
async def admin_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Команда только для админа.", show_alert=True)
        return

    action = callback.data.split(":")[-1]
    if action == "today":
        text = format_admin_today()
    elif action == "week":
        text = format_admin_week()
    elif action == "users":
        text = format_admin_users()
    else:
        text = format_admin_stats()

    try:
        await callback.message.edit_text(text, reply_markup=admin_keyboard())
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer()


@router.message(Command("setup"))
@router.message(F.text == "Настроить цель")
async def setup_goal_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Начнем с простого. Укажи пол:", reply_markup=sex_keyboard())


@router.callback_query(F.data.startswith("setup:sex:"))
async def setup_sex(callback: CallbackQuery, state: FSMContext) -> None:
    sex = callback.data.split(":")[-1]
    await state.update_data(sex=sex)
    await state.set_state(SetupGoal.age)
    await callback.message.answer("Сколько тебе лет? Напиши число.")
    await callback.answer()


@router.message(SetupGoal.age)
async def setup_age(message: Message, state: FSMContext) -> None:
    age = parse_positive_int(message.text or "", 10, 100)
    if age is None:
        await message.answer("Напиши возраст числом, например 32.")
        return
    await state.update_data(age=age)
    await state.set_state(SetupGoal.height)
    await message.answer("Какой рост в сантиметрах?")


@router.message(SetupGoal.height)
async def setup_height(message: Message, state: FSMContext) -> None:
    height = parse_positive_int(message.text or "", 100, 230)
    if height is None:
        await message.answer("Напиши рост числом в сантиметрах, например 176.")
        return
    await state.update_data(height=height)
    await state.set_state(SetupGoal.weight)
    await message.answer("Какой вес в килограммах?")


@router.message(SetupGoal.weight)
async def setup_weight(message: Message, state: FSMContext) -> None:
    weight = parse_positive_float(message.text or "", 30, 300)
    if weight is None:
        await message.answer("Напиши вес числом, например 72 или 72.5.")
        return
    await state.update_data(weight=weight)
    await message.answer("Какая цель?", reply_markup=goal_keyboard())


@router.callback_query(F.data.startswith("setup:goal:"))
async def setup_goal(callback: CallbackQuery, state: FSMContext) -> None:
    goal = callback.data.split(":")[-1]
    await state.update_data(goal=goal)
    await callback.message.answer("Какая активность?", reply_markup=activity_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("setup:activity:"))
async def setup_activity(callback: CallbackQuery, state: FSMContext) -> None:
    activity = callback.data.split(":")[-1]
    data = await state.get_data()
    data["activity"] = activity
    calorie_target, protein_target = calculate_targets(
        sex=data["sex"],
        age=data["age"],
        height=data["height"],
        weight=data["weight"],
        goal=data["goal"],
        activity=data["activity"],
    )
    data["calorie_target"] = calorie_target
    data["protein_target"] = protein_target
    db.update_user_goal(callback.from_user.id, data)
    await state.clear()
    await callback.message.answer(
        "Готово, цель настроена.\n\n"
        f"Цель: {GOAL_LABELS[data['goal']]}\n"
        f"Активность: {ACTIVITY_LABELS[data['activity']]}\n"
        f"Дневная норма: {calorie_target} ккал\n"
        f"Белок: {protein_target} г в день"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("food:scale:"))
async def food_scale(callback: CallbackQuery) -> None:
    _, _, raw_entry_id, raw_factor = callback.data.split(":")
    entry = db.scale_food_entry(int(raw_entry_id), callback.from_user.id, float(raw_factor))
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    user = db.get_or_create_user(callback.from_user.id)
    entries = db.get_today_entries(callback.from_user.id)
    await callback.message.edit_text(
        format_food_saved(entry, user, entries),
        reply_markup=food_actions(entry.id, can_fix_dish=entry.source == "photo"),
    )
    await callback.answer("Порция обновлена")


@router.callback_query(F.data.startswith("food:delete:"))
async def food_delete(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.split(":")[-1])
    deleted = db.delete_food_entry(entry_id, callback.from_user.id)
    if deleted:
        await callback.message.edit_text("Удалил запись.")
        await callback.answer("Удалено")
    else:
        await callback.answer("Запись не найдена", show_alert=True)


@router.callback_query(F.data.startswith("food:ok:"))
async def food_ok(callback: CallbackQuery) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("food:grams:"))
async def food_grams(callback: CallbackQuery, state: FSMContext) -> None:
    entry_id = int(callback.data.split(":")[-1])
    if db.get_food_entry(entry_id, callback.from_user.id) is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await state.set_state(PortionCorrection.grams)
    await state.update_data(entry_id=entry_id)
    await callback.message.answer("Напиши новый вес или порцию, например: 180 г, 2 тарелки, половина порции.")
    await callback.answer()


@router.callback_query(F.data.startswith("food:fix:"))
async def food_fix(callback: CallbackQuery, state: FSMContext) -> None:
    entry_id = int(callback.data.split(":")[-1])
    entry = db.get_food_entry(entry_id, callback.from_user.id)
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await state.set_state(PortionCorrection.dish)
    await state.update_data(entry_id=entry_id)
    await callback.message.answer("Напиши, что это за блюдо и примерную порцию. Например: плов с курицей, одна тарелка.")
    await callback.answer()


@router.message(PortionCorrection.grams)
async def food_grams_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entry = db.get_food_entry(int(data["entry_id"]), message.from_user.id)
    if entry is None:
        await state.clear()
        await message.answer("Не нашел запись. Попробуй добавить еду заново.")
        return
    try:
        estimate = await food_ai.estimate_with_portion(
            previous_description=f"{entry.title}. {entry.description}",
            portion=message.text or "",
        )
    except NotFoodError as exc:
        await answer_not_food(message, exc.reason)
        return
    except OpenAIRecognitionError:
        await message.answer("Не смог распознать, попробуй еще раз или опиши еду текстом.")
        return
    updated = db.replace_food_entry_estimate(entry.id, message.from_user.id, estimate)
    await state.clear()
    await send_food_entry(message, updated)


@router.message(PortionCorrection.dish)
async def food_fix_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entry = db.get_food_entry(int(data["entry_id"]), message.from_user.id)
    if entry is None:
        await state.clear()
        await message.answer("Не нашел запись. Попробуй добавить еду заново.")
        return
    try:
        estimate = await food_ai.estimate_text(message.text or "")
    except NotFoodError as exc:
        await answer_not_food(message, exc.reason)
        return
    except OpenAIRecognitionError:
        await message.answer("Не смог распознать, попробуй еще раз или опиши еду текстом.")
        return
    updated = db.replace_food_entry_estimate(entry.id, message.from_user.id, estimate)
    await state.clear()
    await message.answer("Исправил блюдо и пересчитал примерные калории.")
    await send_food_entry(message, updated, is_photo=entry.source == "photo")


@router.message(F.photo)
async def photo_food(message: Message, bot: Bot) -> None:
    await message.answer("Смотрю фото и прикидываю калории...")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buffer = BytesIO()
    await bot.download_file(file.file_path, buffer)
    image_bytes = buffer.getvalue()
    try:
        estimate = await food_ai.estimate_image(image_bytes)
    except NotFoodError as exc:
        await answer_not_food(message, exc.reason)
        return
    except OpenAIRecognitionError:
        await message.answer("Не смог распознать, попробуй еще раз или опиши еду текстом.")
        return
    entry = db.add_food_entry(message.from_user.id, estimate, source="photo")
    await send_food_entry(message, entry, is_photo=True)


@router.message(F.text)
async def text_food(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    await message.answer("Считаю примерные калории...")
    try:
        estimate = await food_ai.estimate_text(text)
    except NotFoodError as exc:
        await answer_not_food(message, exc.reason)
        return
    except OpenAIRecognitionError:
        await message.answer("Не смог распознать, попробуй еще раз или опиши еду текстом.")
        return
    entry = db.add_food_entry(message.from_user.id, estimate, source="text")
    await send_food_entry(message, entry)


async def main() -> None:
    if not config.bot_token:
        raise RuntimeError("BOT_TOKEN is not set")
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    db.init()
    bot = Bot(token=config.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    logger.info("Bot started")
    reminder_task = asyncio.create_task(reminder_loop(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        reminder_task.cancel()
        await bot.session.close()


async def reminder_loop(bot: Bot) -> None:
    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        today = now.date().isoformat()
        for user in db.get_users_for_reminder(current_time, today):
            try:
                await bot.send_message(
                    user.telegram_id,
                    "Нямметр на связи 🍽\n\nСамое время записать еду за сегодня. Можно фото или просто текстом.",
                )
                db.mark_reminder_sent(user.telegram_id, today)
            except Exception:
                logger.exception("Failed to send reminder to user %s", user.telegram_id)
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
