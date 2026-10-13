import random
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.keyboards import (
    atelier_shop_menu,
    back_menu,
    dust_shop_menu,
    generation_menu,
    scroll_item_menu,
    wardrobe_menu,
)
from app.models import ScrollDefinition, ScrollGeneration, ScrollInventory
from app.services.assets import (
    replace_background,
    replace_photo_view,
    replace_text_view,
    send_reaction,
)
from app.services.gacha import open_trial_chest
from app.services.phrases import CHEST_RARITY_REACTIONS, pick
from app.services.rewards import get_or_create_profile
from app.services.scrolls import get_scroll_target, get_scroll_type_label, grant_scroll

router = Router()
TZ = ZoneInfo(settings.timezone)


class ScrollImageState(StatesGroup):
    waiting_photo = State()


async def _owned_ids(session, user_id: int) -> set[str]:
    return set(
        (await session.scalars(
            select(ScrollInventory.scroll_id).where(ScrollInventory.user_id == user_id)
        )).all()
    )


async def _primary_generation(session, user_id: int, scroll_id: str) -> ScrollGeneration | None:
    return await session.scalar(
        select(ScrollGeneration).where(
            ScrollGeneration.user_id == user_id,
            ScrollGeneration.scroll_id == scroll_id,
            ScrollGeneration.is_primary.is_(True),
        )
    )


async def _send_scroll_card(
    callback: CallbackQuery,
    user_id: int,
    inv: ScrollInventory,
    scroll: ScrollDefinition,
    *,
    replace_current: bool = True,
) -> None:
    status_names = {"sealed": "запечатан", "revealed": "раскрыт", "generated": "сгенерирован"}
    favorite = " · 💜" if inv.is_favorite else ""
    text = (
        f"{scroll.name}\n"
        f"{scroll.rarity.title()} · {get_scroll_target(scroll)} · "
        f"{status_names.get(inv.status, inv.status)}{favorite}\n"
        f"Коллекция: {scroll.collection}"
    )

    async with SessionLocal() as session:
        primary = await _primary_generation(session, user_id, scroll.id)

    markup = scroll_item_menu(scroll.id, inv.status, inv.is_favorite)
    if replace_current:
        if primary:
            await replace_photo_view(
                callback.message,
                primary.telegram_file_id,
                text,
                reply_markup=markup,
            )
        else:
            await replace_background(
                callback.message,
                "atelier_wardrobe",
                text,
                reply_markup=markup,
            )
    elif primary:
        await callback.message.answer_photo(
            primary.telegram_file_id,
            caption=text,
            reply_markup=markup,
        )
    else:
        await callback.message.answer(text, reply_markup=markup)


async def _send_wardrobe_menu(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        await session.commit()
    await replace_background(
        callback.message,
        "atelier_wardrobe",
        f"Сундуки испытания: {profile.trial_chests}\n"
        f"Монеты: {profile.coins}\n"
        f"Пыль ателье: {profile.atelier_dust}",
        reply_markup=wardrobe_menu(profile.trial_chests),
    )


@router.callback_query(F.data == "wardrobe:menu")
async def wardrobe_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _send_wardrobe_menu(callback)
    await callback.answer()


@router.callback_query(F.data == "chest:open")
async def chest_open(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        try:
            result = await open_trial_chest(session, profile)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await session.commit()

    scroll = result["scroll"]
    rarity = result["rarity"]
    if result["duplicate"]:
        detail = f"Дубликат: {scroll.name}\nОн рассыпался в Пыль ателье. +{result['dust']}."
        emotion = "judging"
    else:
        detail = (
            f"Получено: {rarity.upper()}\n"
            f"Запечатанный свиток «{scroll.name}»\n"
            f"Для: {get_scroll_target(scroll)}\n"
            f"Тип: {get_scroll_type_label(scroll)}\n"
            f"Тема: {' / '.join(scroll.tags[:3])}"
        )
        emotion = "surprised" if rarity in {"epic", "legendary"} else "treasure"
    await send_reaction(
        callback.message,
        "tori",
        emotion,
        pick(CHEST_RARITY_REACTIONS[rarity]) + "\n\n" + detail,
        reply_markup=back_menu("wardrobe:menu", "⬅️ В гардероб"),
    )
    await callback.answer()


# Gallery filters live in callback_data, so flipping a card does not require
# mutable per-user navigation state or any changes to the existing SQLite data.
GALLERY_FILTER_NAMES = {
    "a": "Все",
    "s": "Селин",
    "t": "Тори",
    "b": "Фоны",
    "g": "С артом",
    "n": "Без арта",
    "v": "Любимые",
}


def _gallery_filter_rows(rows, images_by_scroll: dict, filter_code: str):
    if filter_code == "s":
        return [(inv, scroll) for inv, scroll in rows if scroll.item_type in {"outfit", "accessory"}]
    if filter_code == "t":
        return [(inv, scroll) for inv, scroll in rows if scroll.item_type == "tori"]
    if filter_code == "b":
        return [(inv, scroll) for inv, scroll in rows if scroll.item_type == "background"]
    if filter_code == "g":
        return [(inv, scroll) for inv, scroll in rows if scroll.id in images_by_scroll]
    if filter_code == "n":
        return [(inv, scroll) for inv, scroll in rows if scroll.id not in images_by_scroll]
    if filter_code == "v":
        return [(inv, scroll) for inv, scroll in rows if inv.is_favorite]
    return rows


def _gallery_filter_buttons(active: str):
    def filter_button(code: str, short_name: str) -> InlineKeyboardButton:
        label = ("• " if active == code else "") + short_name
        return InlineKeyboardButton(text=label, callback_data=f"wc:{code}:0")
    return [
        [filter_button("a", "Все"), filter_button("s", "Селин"),
         filter_button("t", "Тори"), filter_button("b", "Фоны")],
        [filter_button("g", "С артом"), filter_button("n", "Без арта"),
         filter_button("v", "💜")],
    ]


def _gallery_card_markup(
    filter_code: str, position: int, total: int,
    inv: ScrollInventory, scroll: ScrollDefinition, count: int,
) -> InlineKeyboardMarkup:
    next_pos = (position + 1) % total
    prev_pos = (position - 1) % total
    rows = [
        [InlineKeyboardButton(text="⬅️", callback_data=f"wc:{filter_code}:{prev_pos}"),
         InlineKeyboardButton(text=f"{position + 1}/{total}", callback_data="noop"),
         InlineKeyboardButton(text="➡️", callback_data=f"wc:{filter_code}:{next_pos}")],
    ]
    rows += _gallery_filter_buttons(filter_code)
    if inv.status == "sealed":
        rows.append([InlineKeyboardButton(text="🔓 Раскрыть свиток", callback_data=f"wr:{filter_code}:{position}:{inv.id}")])
    else:
        rows.append([InlineKeyboardButton(text="📜 Промпт", callback_data=f"wp:{filter_code}:{position}:{inv.id}"),
                     InlineKeyboardButton(text="🖼 Добавить арт", callback_data=f"wa:{filter_code}:{position}:{inv.id}")])
        if count:
            rows.append([InlineKeyboardButton(text=f"🎨 Варианты ({count})", callback_data=f"wv:{filter_code}:{position}:0")])
        elif inv.status != "generated":
            rows.append([InlineKeyboardButton(text="✅ Отметить сгенерированным", callback_data=f"wm:{filter_code}:{position}:{inv.id}")])
    rows.append([InlineKeyboardButton(
        text="💜 Убрать из любимого" if inv.is_favorite else "♡ В любимое",
        callback_data=f"wf:{filter_code}:{position}:{inv.id}",
    )])
    rows.append([InlineKeyboardButton(text="⬅️ В гардероб", callback_data="wardrobe:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _gallery_data(user_telegram_id: int, filter_code: str, full_name: str = ""):
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, user_telegram_id, full_name)
        rows = (await session.execute(
            select(ScrollInventory, ScrollDefinition)
            .join(ScrollDefinition, ScrollDefinition.id == ScrollInventory.scroll_id)
            .where(ScrollInventory.user_id == profile.id)
            .order_by(ScrollInventory.obtained_at.desc(), ScrollInventory.id.desc())
        )).all()
        generations = (await session.scalars(
            select(ScrollGeneration)
            .where(ScrollGeneration.user_id == profile.id)
            .order_by(ScrollGeneration.created_at.asc(), ScrollGeneration.id.asc())
        )).all()
        await session.commit()
    by_scroll: dict[str, list[ScrollGeneration]] = {}
    for generation in generations:
        by_scroll.setdefault(generation.scroll_id, []).append(generation)
    result = _gallery_filter_rows(rows, by_scroll, filter_code)
    return result, by_scroll, len(rows)


async def _render_gallery_card(callback: CallbackQuery, filter_code: str = "a", position: int = 0) -> None:
    if filter_code not in GALLERY_FILTER_NAMES:
        filter_code = "a"
    rows, by_scroll, grand_total = await _gallery_data(callback.from_user.id, filter_code, callback.from_user.full_name)
    if not rows:
        text = (
            f"📚 Гардероб · всего свитков: {grand_total}\n"
            f"Фильтр: {GALLERY_FILTER_NAMES[filter_code]}\n\n"
            "Здесь пока пусто. Выбери другой фильтр."
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            *_gallery_filter_buttons(filter_code),
            [InlineKeyboardButton(text="⬅️ В гардероб", callback_data="wardrobe:menu")],
        ])
        await replace_background(callback.message, "atelier_wardrobe", text, reply_markup=markup)
        return
    position = max(0, min(position, len(rows) - 1))
    inv, scroll = rows[position]
    generation_options = by_scroll.get(scroll.id, [])
    primary = next((g for g in generation_options if g.is_primary), None)
    if primary is None and generation_options:
        primary = generation_options[0]
    status = {"sealed": "🔒 Не раскрыт", "revealed": "📜 Раскрыт", "generated": "🖼 Сгенерирован"}
    type_labels = {
        "accessory": "Образ с акцентом на аксессуар",
        "outfit": "Полный образ",
        "tori": "Образ Тори",
        "background": "Фон",
    }
    text = (
        f"📚 {GALLERY_FILTER_NAMES[filter_code]} · {position + 1} из {len(rows)}\n\n"
        f"🎴 {scroll.name}\n"
        f"{scroll.rarity.title()} · {get_scroll_target(scroll)}"
        f"{' · 💜' if inv.is_favorite else ''}\n"
        f"Коллекция: {scroll.collection}\n"
        f"Тип: {type_labels.get(scroll.item_type, get_scroll_type_label(scroll))}\n"
        f"Статус: {status.get(inv.status, inv.status)}\n"
        f"{'🖼 Загружено артов: ' + str(len(generation_options)) if generation_options else '🎨 Арт ещё не прикреплён'}"
    )
    markup = _gallery_card_markup(filter_code, position, len(rows), inv, scroll, len(generation_options))
    if primary:
        try:
            await replace_photo_view(callback.message, primary.telegram_file_id, text, reply_markup=markup)
            return
        except Exception as exc:
            print("Wardrobe preview error:", repr(exc))
    await replace_background(callback.message, "atelier_wardrobe", text, reply_markup=markup)


async def _gallery_selected(callback: CallbackQuery, filter_code: str, position: int):
    if filter_code not in GALLERY_FILTER_NAMES:
        filter_code = "a"
    rows, by_scroll, _ = await _gallery_data(callback.from_user.id, filter_code, callback.from_user.full_name)
    if not 0 <= position < len(rows):
        return None
    inv, scroll = rows[position]
    return inv, scroll, by_scroll.get(scroll.id, [])


@router.callback_query(F.data == "wardrobe:list")
async def wardrobe_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_gallery_card(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("wardrobe:listpage:"))
async def wardrobe_list_page(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        page = 0
    await _render_gallery_card(callback, "a", page * 8)
    await callback.answer()


@router.callback_query(F.data.startswith("wc:"))
async def wardrobe_gallery_card(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        _, filter_code, raw_index = callback.data.split(":")
        position = int(raw_index)
    except (AttributeError, ValueError):
        await callback.answer("Не удалось открыть карточку.", show_alert=True)
        return
    await _render_gallery_card(callback, filter_code, position)
    await callback.answer()


@router.callback_query(F.data.startswith(("wp:", "wr:", "wa:", "wf:", "wm:")))
async def wardrobe_gallery_action(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        action, filter_code, raw_position, raw_inv_id = callback.data.split(":")
        position, expected_inventory_id = int(raw_position), int(raw_inv_id)
    except (AttributeError, ValueError):
        await callback.answer("Не удалось открыть действие.", show_alert=True)
        return
    current = await _gallery_selected(callback, filter_code, position)
    if current is None:
        await callback.answer("Свиток переместился. Открой галерею заново.", show_alert=True)
        return
    inv, scroll, _ = current
    if inv.id != expected_inventory_id:
        await callback.answer("Порядок свитков изменился. Открой карточку заново.", show_alert=True)
        return
    back = f"wc:{filter_code}:{position}"
    if action == "wp":
        if inv.status == "sealed":
            await callback.answer("Сначала раскрой свиток.", show_alert=True)
            return
        await replace_text_view(
            callback.message,
            f"🎨 Промпт для генерации: {get_scroll_target(scroll)}\n"
            f"«{scroll.name}»\n\n{scroll.prompt}",
            reply_markup=back_menu(back, "⬅️ К образу"),
        )
    elif action == "wr":
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            actual = await session.scalar(select(ScrollInventory).where(
                ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll.id,
            ))
            if not actual:
                await callback.answer("Свиток не найден.", show_alert=True)
                return
            if actual.status == "sealed":
                actual.status = "revealed"
                actual.revealed_at = datetime.now(TZ)
                await session.commit()
        await replace_text_view(
            callback.message,
            f"🔓 «{scroll.name}» раскрыт.\n"
            f"Промпт для {get_scroll_target(scroll)}:\n\n{scroll.prompt}",
            reply_markup=back_menu(back, "⬅️ К образу"),
        )
    elif action == "wa":
        if inv.status == "sealed":
            await callback.answer("Сначала раскрой свиток.", show_alert=True)
            return
        await state.set_state(ScrollImageState.waiting_photo)
        await state.update_data(
            scroll_id=scroll.id,
            gallery_back="wc:g:0" if filter_code == "n" else back,
        )
        await replace_text_view(
            callback.message,
            f"🖼 Отправь арт для: {get_scroll_target(scroll)}\n"
            f"Свиток «{scroll.name}»",
            reply_markup=back_menu(back, "⬅️ Отмена"),
        )
    elif action == "wf":
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            actual = await session.scalar(select(ScrollInventory).where(
                ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll.id,
            ))
            if actual:
                actual.is_favorite = not actual.is_favorite
                await session.commit()
        # Removing the only favorite can empty the filter; the gallery handles it.
        await _render_gallery_card(callback, filter_code, position)
    elif action == "wm":
        async with SessionLocal() as session:
            profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
            actual = await session.scalar(select(ScrollInventory).where(
                ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll.id,
            ))
            if actual and actual.status != "sealed":
                actual.status = "generated"
                actual.generated_at = datetime.now(TZ)
                await session.commit()
        await _render_gallery_card(callback, filter_code, position)
    await callback.answer()


async def _render_generation_card(
    callback: CallbackQuery, filter_code: str, position: int, image_position: int,
) -> None:
    current = await _gallery_selected(callback, filter_code, position)
    if current is None:
        await _render_gallery_card(callback, filter_code, 0)
        return
    _, scroll, images = current
    if not images:
        await callback.answer("Арты для этого свитка ещё не добавлены.", show_alert=True)
        return
    image_position = max(0, min(image_position, len(images) - 1))
    gen = images[image_position]
    count = len(images)
    markup_rows = [
        [InlineKeyboardButton(text="⬅️", callback_data=f"wv:{filter_code}:{position}:{(image_position - 1) % count}"),
         InlineKeyboardButton(text=f"{image_position + 1}/{count}", callback_data="noop"),
         InlineKeyboardButton(text="➡️", callback_data=f"wv:{filter_code}:{position}:{(image_position + 1) % count}")],
    ]
    if not gen.is_primary:
        markup_rows.append([InlineKeyboardButton(
            text="⭐ Сделать основным", callback_data=f"wvs:{gen.id}:{filter_code}:{position}:{image_position}",
        )])
    markup_rows.append([InlineKeyboardButton(text="⬅️ К свитку", callback_data=f"wc:{filter_code}:{position}")])
    await replace_photo_view(
        callback.message,
        gen.telegram_file_id,
        f"🖼 {scroll.name}\nВариант {image_position + 1} из {count}"
        + (" · ⭐ основной" if gen.is_primary else ""),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=markup_rows),
    )


@router.callback_query(F.data.startswith("wv:"))
async def wardrobe_variant(callback: CallbackQuery) -> None:
    try:
        _, filter_code, raw_position, raw_image = callback.data.split(":")
        position, image_position = int(raw_position), int(raw_image)
    except (AttributeError, ValueError):
        await callback.answer("Не удалось открыть арт.", show_alert=True)
        return
    await _render_generation_card(callback, filter_code, position, image_position)
    await callback.answer()


@router.callback_query(F.data.startswith("wvs:"))
async def wardrobe_set_primary(callback: CallbackQuery) -> None:
    try:
        _, raw_id, filter_code, raw_position, raw_image = callback.data.split(":")
        generation_id, position, image_position = int(raw_id), int(raw_position), int(raw_image)
    except (AttributeError, ValueError):
        await callback.answer("Не удалось выбрать арт.", show_alert=True)
        return
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        chosen = await session.get(ScrollGeneration, generation_id)
        if not chosen or chosen.user_id != profile.id:
            await callback.answer("Арт не найден.", show_alert=True)
            return
        siblings = (await session.scalars(select(ScrollGeneration).where(
            ScrollGeneration.user_id == profile.id,
            ScrollGeneration.scroll_id == chosen.scroll_id,
        ))).all()
        for item in siblings:
            item.is_primary = item.id == chosen.id
        await session.commit()
    await _render_generation_card(callback, filter_code, position, image_position)
    await callback.answer("Основной арт обновлён.")


@router.callback_query(F.data.startswith("scroll:open:"))
async def open_scroll(callback: CallbackQuery, state: FSMContext) -> None:
    scroll_id = callback.data.rsplit(":", 1)[1]
    await state.clear()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        row = (
            await session.execute(
                select(ScrollInventory, ScrollDefinition)
                .join(ScrollDefinition, ScrollDefinition.id == ScrollInventory.scroll_id)
                .where(
                    ScrollInventory.user_id == profile.id,
                    ScrollInventory.scroll_id == scroll_id,
                )
            )
        ).one_or_none()
        user_id = profile.id
        await session.commit()
    if not row:
        await callback.answer("Свиток не найден.", show_alert=True)
        return
    inv, scroll = row
    await _send_scroll_card(callback, user_id, inv, scroll)
    await callback.answer()


@router.callback_query(F.data.startswith("scroll:reveal:"))
async def reveal_scroll(callback: CallbackQuery) -> None:
    scroll_id = callback.data.rsplit(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        inv = await session.scalar(select(ScrollInventory).where(
            ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll_id
        ))
        scroll = await session.get(ScrollDefinition, scroll_id)
        if not inv or not scroll:
            await callback.answer("Свиток не найден.", show_alert=True)
            return
        if inv.status == "sealed":
            inv.status = "revealed"
            inv.revealed_at = datetime.now(TZ)
        await session.commit()
    target = get_scroll_target(scroll)
    await send_reaction(
        callback.message,
        "selin",
        "curious",
        f"🔓 «{scroll.name}» раскрыт.\n\n"
        f"🎨 Промпт для генерации: {target}\n\n"
        f"{scroll.prompt}",
        reply_markup=back_menu(f"scroll:open:{scroll.id}", "⬅️ К свитку"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("scroll:show:"))
async def show_scroll(callback: CallbackQuery) -> None:
    scroll_id = callback.data.rsplit(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        inv = await session.scalar(select(ScrollInventory).where(
            ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll_id
        ))
        scroll = await session.get(ScrollDefinition, scroll_id)
        if not inv or not scroll or inv.status == "sealed":
            await callback.answer("Сначала раскрой свиток.", show_alert=True)
            return
        await session.commit()
    target = get_scroll_target(scroll)
    await replace_text_view(
        callback.message,
        f"🎨 Промпт для генерации: {target}\n\n"
        f"{scroll.prompt}",
        reply_markup=back_menu(f"scroll:open:{scroll.id}", "⬅️ К свитку"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("scroll:generated:"))
async def generated_scroll(callback: CallbackQuery) -> None:
    scroll_id = callback.data.rsplit(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        inv = await session.scalar(select(ScrollInventory).where(
            ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll_id
        ))
        if not inv:
            await callback.answer("Свиток не найден.", show_alert=True)
            return
        inv.status = "generated"
        inv.generated_at = datetime.now(TZ)
        await session.commit()
    await send_reaction(
        callback.message,
        "selin",
        "soft_smile",
        "Селин: — Покажешь потом. Я хочу знать, что из этого получилось.",
        reply_markup=back_menu(f"scroll:open:{scroll_id}", "⬅️ К свитку"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("scroll:add_image:"))
async def add_scroll_image(callback: CallbackQuery, state: FSMContext) -> None:
    scroll_id = callback.data.rsplit(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        inv = await session.scalar(select(ScrollInventory).where(
            ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll_id
        ))
        scroll = await session.get(ScrollDefinition, scroll_id)
        if not inv or not scroll:
            await callback.answer("Свиток не найден.", show_alert=True)
            return
        if inv.status == "sealed":
            await callback.answer("Сначала раскрой свиток.", show_alert=True)
            return

    await state.set_state(ScrollImageState.waiting_photo)
    await state.update_data(scroll_id=scroll_id, gallery_back=f"scroll:open:{scroll_id}")
    target = get_scroll_target(scroll)
    await replace_text_view(
        callback.message,
        f"🖼 Отправь результат генерации для: {target}\n"
        f"Свиток: «{scroll.name}»",
        reply_markup=back_menu(f"scroll:open:{scroll.id}", "⬅️ Отмена"),
    )
    await callback.answer()


@router.message(ScrollImageState.waiting_photo, F.photo)
async def save_scroll_image(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    scroll_id = data.get("scroll_id")
    if not scroll_id:
        await state.clear()
        await message.answer("Не получилось понять, к какому свитку прикреплять картинку. Открой его снова из гардероба.")
        return

    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        inv = await session.scalar(select(ScrollInventory).where(
            ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll_id
        ))
        scroll = await session.get(ScrollDefinition, scroll_id)
        if not inv or not scroll:
            await state.clear()
            await message.answer("Свиток больше не найден.")
            return

        existing = list((await session.scalars(select(ScrollGeneration).where(
            ScrollGeneration.user_id == profile.id, ScrollGeneration.scroll_id == scroll_id
        ))).all())

        generation = ScrollGeneration(
            user_id=profile.id,
            scroll_id=scroll_id,
            telegram_file_id=message.photo[-1].file_id,
            telegram_file_unique_id=message.photo[-1].file_unique_id,
            is_primary=len(existing) == 0,
        )
        session.add(generation)
        inv.status = "generated"
        inv.generated_at = datetime.now(TZ)
        await session.commit()

    await state.clear()
    await send_reaction(
        message,
        "selin",
        "soft_smile",
        f"Селин: — Вот, теперь другое дело.\nК «{scroll.name}» добавлен вариант #{len(existing) + 1}.",
        reply_markup=back_menu(data.get("gallery_back") or f"scroll:open:{scroll.id}", "⬅️ К свитку"),
    )


@router.message(ScrollImageState.waiting_photo)
async def scroll_image_requires_photo(message: Message, state: FSMContext) -> None:
    state_data = await state.get_data()
    scroll_id = state_data.get("scroll_id")
    reply_markup = back_menu(
        state_data.get("gallery_back") or f"scroll:open:{scroll_id}", "⬅️ Отмена",
    ) if scroll_id else None
    await message.answer(
        "Мне нужна именно картинка. Отправь её как фото, и я привяжу к свитку.",
        reply_markup=reply_markup,
    )


@router.callback_query(F.data.startswith("scroll:gallery:"))
async def scroll_gallery(callback: CallbackQuery) -> None:
    scroll_id = callback.data.rsplit(":", 1)[1]
    rows, _, _ = await _gallery_data(callback.from_user.id, "a")
    position = next((i for i, (_, scroll) in enumerate(rows) if scroll.id == scroll_id), None)
    if position is None:
        await callback.answer("Свиток не найден.", show_alert=True)
        return
    await _render_generation_card(callback, "a", position, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("generation:primary:"))
async def generation_primary(callback: CallbackQuery) -> None:
    generation_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        generation = await session.get(ScrollGeneration, generation_id)
        if not generation or generation.user_id != profile.id:
            await callback.answer("Вариант не найден.", show_alert=True)
            return
        siblings = list((await session.scalars(select(ScrollGeneration).where(
            ScrollGeneration.user_id == profile.id,
            ScrollGeneration.scroll_id == generation.scroll_id,
        ))).all())
        for item in siblings:
            item.is_primary = item.id == generation.id
        await session.commit()
    await callback.answer("Этот вариант теперь основной.", show_alert=True)


@router.callback_query(F.data.startswith("scroll:favorite:"))
async def favorite_scroll(callback: CallbackQuery) -> None:
    scroll_id = callback.data.rsplit(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        inv = await session.scalar(select(ScrollInventory).where(
            ScrollInventory.user_id == profile.id, ScrollInventory.scroll_id == scroll_id
        ))
        if not inv:
            await callback.answer("Свиток не найден.", show_alert=True)
            return
        inv.is_favorite = not inv.is_favorite
        state = inv.is_favorite
        await session.commit()
    await callback.answer("Добавлено в любимое." if state else "Убрано из любимого.", show_alert=True)


@router.callback_query(F.data == "shop:atelier")
async def atelier_shop(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        await session.commit()
    await replace_text_view(
        callback.message,
        "Ателье продаёт запечатанный свиток из своей коллекции. Что внутри — заранее не видно.",
        reply_markup=atelier_shop_menu(profile.coins),
    )
    await callback.answer()


@router.callback_query(F.data == "shop:coin:random")
async def buy_atelier_scroll(callback: CallbackQuery) -> None:
    price = 150
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        if profile.coins < price:
            await callback.answer("Не хватает монет.", show_alert=True)
            return
        owned = await _owned_ids(session, profile.id)
        candidates = list((await session.scalars(
            select(ScrollDefinition).where(ScrollDefinition.source == "shop")
        )).all())
        candidates = [s for s in candidates if s.id not in owned]
        if not candidates:
            await callback.answer("Все свитки ателье уже собраны.", show_alert=True)
            return
        scroll = random.choice(candidates)
        profile.coins -= price
        await grant_scroll(session, profile, scroll.id)
        await session.commit()
    await send_reaction(
        callback.message,
        "tori",
        "treasure",
        f"🎴 Получен запечатанный свиток «{scroll.name}».\n{scroll.rarity.upper()} · {' / '.join(scroll.tags[:3])}",
        reply_markup=back_menu("shop:atelier", "⬅️ В ателье"),
    )
    await callback.answer()


@router.callback_query(F.data == "shop:dust")
async def dust_shop(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        await session.commit()
    await replace_text_view(
        callback.message,
        "Пыль ателье собирается из дубликатов. Её можно обменять на новый гарантированно незнакомый свиток.",
        reply_markup=dust_shop_menu(profile.atelier_dust),
    )
    await callback.answer()


async def _buy_with_dust(callback: CallbackQuery, min_rarities: set[str], price: int) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        if profile.atelier_dust < price:
            await callback.answer("Не хватает Пыли ателье.", show_alert=True)
            return
        owned = await _owned_ids(session, profile.id)
        candidates = list((await session.scalars(
            select(ScrollDefinition).where(
                ScrollDefinition.rarity.in_(min_rarities),
                ScrollDefinition.is_secret.is_(False),
            )
        )).all())
        candidates = [s for s in candidates if s.id not in owned and s.source not in {"story", "achievement"}]
        if not candidates:
            await callback.answer("Подходящих новых свитков больше нет.", show_alert=True)
            return
        scroll = random.choice(candidates)
        profile.atelier_dust -= price
        await grant_scroll(session, profile, scroll.id)
        await session.commit()
    await send_reaction(
        callback.message,
        "tori",
        "treasure",
        f"✨ Пыль собралась в запечатанный свиток «{scroll.name}».\n{scroll.rarity.upper()}",
        reply_markup=back_menu("shop:dust", "⬅️ В магазин Пыли"),
    )
    await callback.answer()


@router.callback_query(F.data == "shop:dust:rare")
async def buy_dust_rare(callback: CallbackQuery) -> None:
    await _buy_with_dust(callback, {"rare", "epic", "legendary"}, 180)


@router.callback_query(F.data == "shop:dust:epic")
async def buy_dust_epic(callback: CallbackQuery) -> None:
    await _buy_with_dust(callback, {"epic", "legendary"}, 300)
