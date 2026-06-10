from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Настроить цель")],
            [KeyboardButton(text="Сегодня"), KeyboardButton(text="Дневник")],
            [KeyboardButton(text="Помощь")],
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


def food_actions(entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Порция меньше", callback_data=f"food:scale:{entry_id}:0.75"),
                InlineKeyboardButton(text="Порция больше", callback_data=f"food:scale:{entry_id}:1.25"),
            ],
            [InlineKeyboardButton(text="Указать граммы", callback_data=f"food:grams:{entry_id}")],
            [
                InlineKeyboardButton(text="Удалить", callback_data=f"food:delete:{entry_id}"),
                InlineKeyboardButton(text="Ок", callback_data=f"food:ok:{entry_id}"),
            ],
        ]
    )
