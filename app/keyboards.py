from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Сегодня"), KeyboardButton(text="🚶 Шаги")],
            [KeyboardButton(text="🍽 Еда"), KeyboardButton(text="🏋️ Тренировка")],
            [KeyboardButton(text="📜 Квесты"), KeyboardButton(text="🎁 Гардероб")],
            [KeyboardButton(text="📖 История"), KeyboardButton(text="💬 Селин")],
            [KeyboardButton(text="📷 Анализ фото"), KeyboardButton(text="🎯 Челлендж")],
            [KeyboardButton(text="🏆 Ачивки")],
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
                InlineKeyboardButton(text="🍰 Вкусняшка", callback_data="food:treat"),
                InlineKeyboardButton(text="☕ Напиток", callback_data="food:drink"),
            ],
            [
                InlineKeyboardButton(text="💧 Вода", callback_data="food:water"),
                InlineKeyboardButton(text="🧠 Искушение", callback_data="food:temptation"),
            ],
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


def back_menu(callback_data: str, text: str = "⬅️ Назад") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback_data)]]
    )


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
    rows.append([InlineKeyboardButton(text="⬅️ В гардероб", callback_data="wardrobe:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def generation_menu(generation_id: int, is_primary: bool, scroll_id: str) -> InlineKeyboardMarkup:
    rows = []
    if not is_primary:
        rows.append([InlineKeyboardButton(text="⭐ Сделать основной", callback_data=f"generation:primary:{generation_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ К свитку", callback_data=f"scroll:open:{scroll_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def atelier_shop_menu(coins: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎴 Таинственный свиток — 150", callback_data="shop:coin:random")],
            [InlineKeyboardButton(text=f"Монеты: {coins}", callback_data="noop")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="wardrobe:menu")],
        ]
    )


def dust_shop_menu(dust: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎴 Rare+ свиток — 180", callback_data="shop:dust:rare")],
            [InlineKeyboardButton(text="💎 Epic+ свиток — 300", callback_data="shop:dust:epic")],
            [InlineKeyboardButton(text=f"Пыль ателье: {dust}", callback_data="noop")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="wardrobe:menu")],
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
            [InlineKeyboardButton(text=choice["label"], callback_data=f"storychoice:{day}:{choice['id']}")]
            for choice in choices
        ]
    )


def story_v2_choices_menu(day: int, choices: list[dict]) -> InlineKeyboardMarkup | None:
    if not choices:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=choice["label"], callback_data=f"storyv2:{day}:{choice['id']}")]
            for choice in choices
        ]
    )


def selin_chat_menu(include_tori: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✨ Свободный разговор", callback_data="selinchat:free")],
        [InlineKeyboardButton(text="О тебе", callback_data="selinchat:self")],
        [InlineKeyboardButton(text="О Печати", callback_data="selinchat:seal")],
        [InlineKeyboardButton(text="Как ты?", callback_data="selinchat:how")],
    ]
    if include_tori:
        rows.append([InlineKeyboardButton(text="О Тори", callback_data="selinchat:tori")])
    rows.append([InlineKeyboardButton(text="Просто посидеть рядом", callback_data="selinchat:sit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_chat_stop_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🛑 Закончить разговор", callback_data="aichat:stop")]]
    )


def ai_quest_offer_menu(offer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 Взять задание", callback_data=f"aiquest:accept:{offer_id}")],
            [InlineKeyboardButton(text="Не сейчас", callback_data=f"aiquest:decline:{offer_id}")],
        ]
    )


def ai_initiative_menu(notification_id: int, offer_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"initiative:reply:{notification_id}")],
        [InlineKeyboardButton(text="Позже", callback_data=f"initiative:later:{notification_id}")],
    ]
    if offer_id is not None:
        rows.insert(
            0,
            [InlineKeyboardButton(text="📜 Взять задание", callback_data=f"aiquest:accept:{offer_id}")],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_initiative_stop_menu(notification_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛑 Закончить разговор", callback_data=f"initiative:stop:{notification_id}")]
        ]
    )


def tori_autonomous_menu(notification_id: int, choices: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=str(choice["label"]),
                    callback_data=f"toriauto:react:{notification_id}:{choice['id']}",
                )
            ]
            for choice in choices
        ]
    )


def photo_kind_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🍽 Еда / напиток", callback_data="photoai:kind:food")],
            [InlineKeyboardButton(text="⌚ Активность / часы", callback_data="photoai:kind:activity")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="photoai:cancel")],
        ]
    )


def food_photo_prepare_menu(photo_count: int = 1, *, has_comment: bool = False, max_photos: int = 8) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🔎 Посчитать ({photo_count} фото)", callback_data="photoai:food:analyze")],
    ]
    if photo_count < max_photos:
        rows.append([InlineKeyboardButton(text=f"📷 Добавить фото ({photo_count}/{max_photos})", callback_data="photoai:food:add_photo")])
    rows.append([
        InlineKeyboardButton(
            text="✏️ Изменить комментарий" if has_comment else "💬 Добавить комментарий",
            callback_data="photoai:food:comment",
        )
    ])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="photoai:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def daily_challenge_options_menu(plan_id: int, options: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for index, option in enumerate(options[:3]):
        rows.append([
            InlineKeyboardButton(
                text=f"{index + 1}. {str(option.get('title') or 'Челлендж')[:48]}",
                callback_data=f"challenge:select:{plan_id}:{index}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def daily_challenge_active_menu(plan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Проверить челлендж", callback_data=f"challenge:check:{plan_id}")]]
    )


def daily_challenge_open_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎯 Выбрать челлендж", callback_data="challenge:open")]]
    )

def food_analysis_menu(analysis_id: int, *, can_enter_grams: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✅ Записать всю порцию", callback_data=f"photoai:portion:full:{analysis_id}")],
        [
            InlineKeyboardButton(text="½ порции", callback_data=f"photoai:portion:half:{analysis_id}"),
            InlineKeyboardButton(text="⅓ порции", callback_data=f"photoai:portion:third:{analysis_id}"),
        ],
    ]
    if can_enter_grams:
        rows.append([InlineKeyboardButton(text="⌨️ Ввести граммы", callback_data=f"photoai:portion:grams:{analysis_id}")])
    rows.extend(
        [
            [InlineKeyboardButton(text="✏️ Исправить категорию", callback_data=f"photoai:foodcategory:{analysis_id}")],
            [InlineKeyboardButton(text="❌ Не записывать", callback_data=f"photoai:discard:{analysis_id}")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def food_category_menu(analysis_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🍲 Полноценная еда", callback_data=f"photoai:foodcat:meal:{analysis_id}")],
            [InlineKeyboardButton(text="🍎 Перекус", callback_data=f"photoai:foodcat:snack:{analysis_id}")],
            [InlineKeyboardButton(text="🍰 Вкусняшка", callback_data=f"photoai:foodcat:treat:{analysis_id}")],
            [InlineKeyboardButton(text="☕ Напиток", callback_data=f"photoai:foodcat:drink:{analysis_id}")],
            [InlineKeyboardButton(text="❌ Не записывать", callback_data=f"photoai:discard:{analysis_id}")],
        ]
    )


def activity_analysis_menu(analysis_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Записать найденное", callback_data=f"photoai:activity:accept:{analysis_id}")],
            [InlineKeyboardButton(text="❌ Не записывать", callback_data=f"photoai:discard:{analysis_id}")],
        ]
    )
