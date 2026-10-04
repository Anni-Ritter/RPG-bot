import random
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.keyboards import atelier_shop_menu, dust_shop_menu, generation_menu, scroll_item_menu
from app.models import ScrollDefinition, ScrollGeneration, ScrollInventory
from app.services.assets import send_reaction
from app.services.gacha import open_trial_chest
from app.services.phrases import CHEST_RARITY_REACTIONS, pick
from app.services.rewards import get_or_create_profile
from app.services.scrolls import grant_scroll

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


async def _send_scroll_card(callback: CallbackQuery, user_id: int, inv: ScrollInventory, scroll: ScrollDefinition) -> None:
    status_names = {"sealed": "запечатан", "revealed": "раскрыт", "generated": "сгенерирован"}
    favorite = " · 💜" if inv.is_favorite else ""
    text = (
        f"{scroll.name}\n{scroll.rarity.upper()} · {status_names.get(inv.status, inv.status)}{favorite}\n"
        f"Коллекция: {scroll.collection}"
    )

    async with SessionLocal() as session:
        primary = await _primary_generation(session, user_id, scroll.id)

    if primary:
        await callback.message.answer_photo(
            primary.telegram_file_id,
            caption=text,
            reply_markup=scroll_item_menu(scroll.id, inv.status, inv.is_favorite),
        )
    else:
        await callback.message.answer(
            text,
            reply_markup=scroll_item_menu(scroll.id, inv.status, inv.is_favorite),
        )


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
            f"Тема: {' / '.join(scroll.tags[:3])}"
        )
        emotion = "surprised" if rarity in {"epic", "legendary"} else "treasure"
    await send_reaction(
        callback.message,
        "tori",
        emotion,
        pick(CHEST_RARITY_REACTIONS[rarity]) + "\n\n" + detail,
    )
    await callback.answer()


@router.callback_query(F.data == "wardrobe:list")
async def wardrobe_list(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        rows = (
            await session.execute(
                select(ScrollInventory, ScrollDefinition)
                .join(ScrollDefinition, ScrollDefinition.id == ScrollInventory.scroll_id)
                .where(ScrollInventory.user_id == profile.id)
                .order_by(ScrollInventory.is_favorite.desc(), ScrollInventory.obtained_at.desc())
            )
        ).all()
        user_id = profile.id
        await session.commit()

    if not rows:
        await callback.message.answer("Пока ни одного свитка.")
        await callback.answer()
        return

    await callback.message.answer(f"Гардероб Селин: {len(rows)} свитков.")
    for inv, scroll in rows[:20]:
        await _send_scroll_card(callback, user_id, inv, scroll)
    if len(rows) > 20:
        await callback.message.answer("Показываю последние 20, чтобы не устроить свиткопад в чате.")
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
    await send_reaction(callback.message, "selin", "curious", f"🔓 «{scroll.name}» раскрыт.\n\n{scroll.prompt}")
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
    await callback.message.answer(scroll.prompt)
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
    await send_reaction(callback.message, "selin", "soft_smile", "Селин: — Покажешь потом. Я хочу знать, что из этого получилось.")
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
    await state.update_data(scroll_id=scroll_id)
    await callback.message.answer(
        f"Пришли картинку для «{scroll.name}». Можно загружать несколько вариантов — потом выберешь основной."
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
    )


@router.message(ScrollImageState.waiting_photo)
async def scroll_image_requires_photo(message: Message) -> None:
    await message.answer("Мне нужна именно картинка. Отправь её как фото, и я привяжу к свитку.")


@router.callback_query(F.data.startswith("scroll:gallery:"))
async def scroll_gallery(callback: CallbackQuery) -> None:
    scroll_id = callback.data.rsplit(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        scroll = await session.get(ScrollDefinition, scroll_id)
        generations = list((await session.scalars(
            select(ScrollGeneration)
            .where(ScrollGeneration.user_id == profile.id, ScrollGeneration.scroll_id == scroll_id)
            .order_by(ScrollGeneration.is_primary.desc(), ScrollGeneration.created_at.asc())
        )).all())
        await session.commit()

    if not scroll or not generations:
        await callback.answer("К этому свитку пока нет загруженных картинок.", show_alert=True)
        return

    for index, generation in enumerate(generations, start=1):
        primary = " · основной" if generation.is_primary else ""
        await callback.message.answer_photo(
            generation.telegram_file_id,
            caption=f"{scroll.name} · вариант {index}{primary}",
            reply_markup=generation_menu(generation.id, generation.is_primary),
        )
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
    await callback.message.answer(
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
    )
    await callback.answer()


@router.callback_query(F.data == "shop:dust")
async def dust_shop(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        await session.commit()
    await callback.message.answer(
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
    )
    await callback.answer()


@router.callback_query(F.data == "shop:dust:rare")
async def buy_dust_rare(callback: CallbackQuery) -> None:
    await _buy_with_dust(callback, {"rare", "epic", "legendary"}, 180)


@router.callback_query(F.data == "shop:dust:epic")
async def buy_dust_epic(callback: CallbackQuery) -> None:
    await _buy_with_dust(callback, {"epic", "legendary"}, 300)
