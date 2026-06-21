from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu(has_goal: bool = False) -> ReplyKeyboardMarkup:
    goal_button_text = "Изменить цель/параметры" if has_goal else "Настроить цель"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=goal_button_text)],
            [KeyboardButton(text="Сегодня"), KeyboardButton(text="Дневник")],
            [KeyboardButton(text="Инструкция")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Фото еды или что вы съели",
    )


def sex_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мужской", callback_data="setup:sex:male"),
                InlineKeyboardButton(text="Женский", callback_data="setup:sex:female"),
            ]
        ]
    )


def setup_goal_intro_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Указать параметры", callback_data="setup:start")],
        ]
    )


def goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Похудеть", callback_data="setup:goal:lose")],
            [InlineKeyboardButton(text="Поддерживать", callback_data="setup:goal:maintain")],
            [InlineKeyboardButton(text="Набрать", callback_data="setup:goal:gain")],
        ]
    )


def activity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Низкая", callback_data="setup:activity:low")],
            [InlineKeyboardButton(text="Средняя", callback_data="setup:activity:medium")],
            [InlineKeyboardButton(text="Высокая", callback_data="setup:activity:high")],
        ]
    )


def reminder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Выбрать время", callback_data="reminder:choose")],
            [InlineKeyboardButton(text="Не напоминать", callback_data="reminder:disable")],
        ]
    )


def reminder_time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="09:00", callback_data="reminder:time:09:00"),
                InlineKeyboardButton(text="10:00", callback_data="reminder:time:10:00"),
            ],
            [
                InlineKeyboardButton(text="12:00", callback_data="reminder:time:12:00"),
                InlineKeyboardButton(text="20:00", callback_data="reminder:time:20:00"),
            ],
            [InlineKeyboardButton(text="Выбрать свое", callback_data="reminder:custom")],
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Общее", callback_data="admin:total"),
                InlineKeyboardButton(text="Сегодня", callback_data="admin:today"),
            ],
            [
                InlineKeyboardButton(text="7 дней", callback_data="admin:week"),
                InlineKeyboardButton(text="Пользователи", callback_data="admin:users"),
            ],
        ]
    )


def instruction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Группа Нямметра", url="https://t.me/nyammetr_group")],
        ]
    )


def activation_keyboard(step: int) -> InlineKeyboardMarkup:
    if step == 1:
        keyboard = [
            [InlineKeyboardButton(text="📸 Отправить фото еды", callback_data="activation:photo")],
            [InlineKeyboardButton(text="✍️ Написать текстом", callback_data="activation:text")],
            [InlineKeyboardButton(text="🎯 Настроить цель", callback_data="activation:setup")],
        ]
    elif step == 2:
        keyboard = [
            [InlineKeyboardButton(text="🍽 Попробовать на одной еде", callback_data="activation:try")],
            [InlineKeyboardButton(text="🎯 Настроить цель", callback_data="activation:setup")],
            [InlineKeyboardButton(text="🔕 Не напоминать", callback_data="activation:disable")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton(text="📸 Попробовать", callback_data="activation:photo")],
            [InlineKeyboardButton(text="🔕 Не напоминать", callback_data="activation:disable")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def food_actions(entry_id: int, can_fix_dish: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="Порция меньше", callback_data=f"food:scale:{entry_id}:0.75"),
            InlineKeyboardButton(text="Порция больше", callback_data=f"food:scale:{entry_id}:1.25"),
        ],
        [InlineKeyboardButton(text="Указать граммы", callback_data=f"food:grams:{entry_id}")],
    ]
    if can_fix_dish:
        keyboard.append([InlineKeyboardButton(text="Исправить блюдо", callback_data=f"food:fix:{entry_id}")])
    keyboard.append(
        [
            InlineKeyboardButton(text="Удалить", callback_data=f"food:delete:{entry_id}"),
            InlineKeyboardButton(text="Ок", callback_data=f"food:ok:{entry_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
