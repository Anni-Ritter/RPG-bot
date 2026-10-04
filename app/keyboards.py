from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Сегодня"), KeyboardButton(text="🚶 Шаги")],
            [KeyboardButton(text="🍽 Еда"), KeyboardButton(text="🏋️ Тренировка")],
            [KeyboardButton(text="📜 Квесты"), KeyboardButton(text="🎁 Гардероб")],
            [KeyboardButton(text="📖 История"), KeyboardButton(text="🏆 Ачивки")],
        ],
        resize_keyboard=True,
    )



def manual_workout_type_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💪 Силовая", callback_data="manualworkout:type:strength"),
                InlineKeyboardButton(text="🧘 Пилатес / растяжка", callback_data="manualworkout:type:pilates"),
            ],
            [
                InlineKeyboardButton(text="❤️ Кардио", callback_data="manualworkout:type:cardio"),
                InlineKeyboardButton(text="✨ Другое", callback_data="manualworkout:type:other"),
            ],
        ]
    )


def manual_workout_duration_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="10 мин", callback_data="manualworkout:minutes:10"),
                InlineKeyboardButton(text="20 мин", callback_data="manualworkout:minutes:20"),
                InlineKeyboardButton(text="30 мин", callback_data="manualworkout:minutes:30"),
            ],
            [
                InlineKeyboardButton(text="45 мин", callback_data="manualworkout:minutes:45"),
                InlineKeyboardButton(text="60+ мин", callback_data="manualworkout:minutes:60"),
            ],
            [InlineKeyboardButton(text="⌨️ Ввести минуты", callback_data="manualworkout:minutes:custom")],
        ]
    )

def food_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🍲 Полноценная еда", callback_data="food:meal"),
                InlineKeyboardButton(text="🍎 Перекус", callback_data="food:snack"),
            ],
            [
                InlineKeyboardButton(text="☕ Напиток", callback_data="food:drink"),
                InlineKeyboardButton(text="💧 Вода", callback_data="food:water"),
            ],
            [InlineKeyboardButton(text="🍰 Искушение", callback_data="food:temptation")],
        ]
    )


def drink_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без существенных добавок", callback_data="drink:plain")],
            [InlineKeyboardButton(text="С молоком / сахаром / сиропом", callback_data="drink:caloric")],
            [InlineKeyboardButton(text="⚡ Энергетик", callback_data="drink:energy")],
        ]
    )


def temptation_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🍰 Да, хочу", callback_data="tempt:want")],
            [InlineKeyboardButton(text="⏳ Подожду 15 минут", callback_data="tempt:pause")],
            [InlineKeyboardButton(text="🤷 Просто увидела", callback_data="tempt:impulse")],
        ]
    )


def temptation_after_pause_menu(temptation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🍰 Всё ещё хочу", callback_data=f"tempt:still_want:{temptation_id}")],
            [InlineKeyboardButton(text="✨ Уже не хочется", callback_data=f"tempt:no_longer:{temptation_id}")],
        ]
    )


def workout_start_menu(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Начинаю", callback_data=f"workout:start:{kind}")],
            [InlineKeyboardButton(text="🚪 Сегодня пропускаю", callback_data=f"workout:skip:{kind}")],
        ]
    )


def monday_finish_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Закончила", callback_data="workout:finish:monday")],
            [InlineKeyboardButton(text="⏳ Ещё занимаюсь", callback_data="workout:later:monday")],
        ]
    )


def thursday_second_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Иду на вторую", callback_data="workout:bonus:go")],
            [InlineKeyboardButton(text="🚪 Ухожу", callback_data="workout:bonus:leave")],
        ]
    )


def bonus_finish_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Закончила вторую", callback_data="workout:finish:thursday_bonus")]
        ]
    )


def wardrobe_menu(chests: int) -> InlineKeyboardMarkup:
    buttons = []
    if chests > 0:
        buttons.append([InlineKeyboardButton(text=f"✨ Открыть сундук ({chests})", callback_data="chest:open")])
    buttons.extend(
        [
            [InlineKeyboardButton(text="📚 Мои свитки", callback_data="wardrobe:list")],
            [InlineKeyboardButton(text="🪙 Ателье", callback_data="shop:atelier")],
            [InlineKeyboardButton(text="✨ Пыль ателье", callback_data="shop:dust")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def scroll_item_menu(scroll_id: str, status: str, favorite: bool) -> InlineKeyboardMarkup:
    rows = []
    if status == "sealed":
        rows.append([InlineKeyboardButton(text="🔓 Раскрыть промт", callback_data=f"scroll:reveal:{scroll_id}")])
    elif status in {"revealed", "generated"}:
        rows.append([InlineKeyboardButton(text="📜 Показать промт", callback_data=f"scroll:show:{scroll_id}")])
        rows.append([InlineKeyboardButton(text="🖼 Добавить результат", callback_data=f"scroll:add_image:{scroll_id}")])
        rows.append([InlineKeyboardButton(text="🎨 Галерея генераций", callback_data=f"scroll:gallery:{scroll_id}")])
    if status != "generated":
        rows.append([InlineKeyboardButton(text="✅ Отметить как сгенерированный", callback_data=f"scroll:generated:{scroll_id}")])
    fav_label = "💜 Убрать из любимого" if favorite else "♡ В любимое"
    rows.append([InlineKeyboardButton(text=fav_label, callback_data=f"scroll:favorite:{scroll_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def generation_menu(generation_id: int, is_primary: bool) -> InlineKeyboardMarkup | None:
    if is_primary:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⭐ Сделать основной", callback_data=f"generation:primary:{generation_id}")]]
    )


def atelier_shop_menu(coins: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎴 Таинственный свиток — 150", callback_data="shop:coin:random")],
            [InlineKeyboardButton(text=f"Монеты: {coins}", callback_data="noop")],
        ]
    )


def dust_shop_menu(dust: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎴 Rare+ свиток — 180", callback_data="shop:dust:rare")],
            [InlineKeyboardButton(text="💎 Epic+ свиток — 300", callback_data="shop:dust:epic")],
            [InlineKeyboardButton(text=f"Пыль ателье: {dust}", callback_data="noop")],
        ]
    )


def quest_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Новый квест", callback_data="quest:new")],
            [InlineKeyboardButton(text="📋 Активные", callback_data="quest:list")],
        ]
    )


def story_choices_menu(day: int, choices: list[dict]) -> InlineKeyboardMarkup | None:
    if not choices:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=choice["label"],
                    callback_data=f"storychoice:{day}:{choice['id']}",
                )
            ]
            for choice in choices
        ]
    )
