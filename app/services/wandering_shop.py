"""Living RPG shop. AI proposes flavour; Python owns currency, prices and inventory.

All tables are additive. No images and no generated art are required.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base
from app.models import GameEvent, UserProfile

TZ = ZoneInfo(settings.timezone)
CATEGORIES = {'gift', 'toy', 'accessory', 'camp', 'tool', 'curiosity'}
RECIPIENTS = {'selin', 'tori', 'camp', 'party'}
RARITIES = {'common', 'uncommon', 'rare'}
# Fixed ranges are controlled by Python, never by a model response.
PRICES = {'common': (20, 35, 45), 'uncommon': (60, 85, 110), 'rare': (150, 190, 240)}
FALLBACK = [
    ('Клубок лунной пряжи', 'Мягкая серебристая нить, в которой Тори уже видит игрушку.', 'toy', 'tori', 'common', 'Игрушка может неожиданно появиться в следующем разговоре.'),
    ('Колокольчик с трещинкой', 'Звенит едва слышно, зато Тори считает его сокровищем.', 'gift', 'tori', 'common', 'Тори носит его с собой и звенит, когда волнуется.'),
    ('Плащ с карманами', 'Практичный плащ для холодных дорог и мелких находок.', 'accessory', 'selin', 'uncommon', 'Селин может брать его в путешествия и прятать находки.'),
    ('Закладка из тёмного стекла', 'На свету отражает едва заметные символы.', 'gift', 'selin', 'common', 'Селин кладёт её между страницами своей книги.'),
    ('Лампа светляков', 'Тихое сияние для вечеров в убежище.', 'camp', 'camp', 'uncommon', 'Будет освещать сцену возвращения в убежище.'),
    ('Кружка с узором из звёзд', 'Очень большая кружка для долгих разговоров.', 'gift', 'selin', 'common', 'Селин может пользоваться ею за вечерним чаем.'),
    ('Карта без северной стрелки', 'На обороте есть странный маршрут, требующий разгадки.', 'curiosity', 'party', 'uncommon', 'В нужной сцене может стать поводом для поиска.'),
    ('Складная подзорная труба', 'Удобна для наблюдения за далёкой дорогой.', 'tool', 'party', 'uncommon', 'Может помочь осмотреть окрестности в игре.'),
    ('Набор разноцветных ленточек', 'Тори почему-то считает их чудесной добычей.', 'toy', 'tori', 'common', 'Ленты могут стать частью его любимого тайника.'),
    ('Маленький звёздный медальон', 'Не обладает магическими свойствами, зато красиво блестит.', 'accessory', 'selin', 'rare', 'Селин может надеть его в важный вечер.'),
    ('Старая дорожная фляга', 'Надёжная и вся в отметках прежних хозяев.', 'tool', 'party', 'common', 'Появляется в сценах похода и привала.'),
    ('Шкатулка с деревянной лисой', 'Небольшая коробка с тайным отделением.', 'camp', 'camp', 'rare', 'Позже может хранить важную находку.'),
    ('Мягкая подушка для Тори', 'Кажется, на ней невозможно не уснуть.', 'camp', 'tori', 'uncommon', 'Тори использует её во время отдыха.'),
    ('Полевой блокнот', 'Тонкие страницы для карт, зарисовок и заметок.', 'tool', 'selin', 'uncommon', 'Селин может вести в нём записи о приключениях.'),
    ('Обломок голубого стекла', 'Края отполированы водой, но в глубине пляшет свет.', 'curiosity', 'party', 'common', 'Может привлечь внимание встречного персонажа.'),
    ('Плетёная корзина', 'Достаточно вместительна для находок Тори.', 'camp', 'camp', 'common', 'Приключения могут пополнять её содержимое.'),
    ('Перо с золотым кончиком', 'Пишет необычайно тонко и ровно.', 'gift', 'selin', 'rare', 'Селин может использовать его для письма.'),
    ('Плюшевый мотылёк', 'Нелепая, но очаровательная игрушка с четырьмя крыльями.', 'toy', 'tori', 'uncommon', 'Тори может таскать его с собой и прятать.'),
]


def now_local() -> datetime:
    return datetime.now(TZ)


class ShopState(Base):
    __tablename__ = 'selin_wander_shop_state'
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True)
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    rerolls_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    rerolls_today: Mapped[int] = mapped_column(Integer, default=0)


class ShopOffer(Base):
    __tablename__ = 'selin_wander_shop_offers'
    __table_args__ = (UniqueConstraint('user_id', 'rotation', 'slot', name='uq_wander_shop_slot'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    rotation: Mapped[int] = mapped_column(Integer, index=True)
    slot: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(110))
    description: Mapped[str] = mapped_column(String(250))
    category: Mapped[str] = mapped_column(String(24))
    recipient: Mapped[str] = mapped_column(String(24))
    rarity: Mapped[str] = mapped_column(String(20))
    use_hint: Mapped[str] = mapped_column(String(240))
    price: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_local)


class OwnedWorldItem(Base):
    __tablename__ = 'selin_wander_owned_items'
    __table_args__ = (UniqueConstraint('user_id', 'offer_id', name='uq_wander_owned_offer'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey('selin_wander_shop_offers.id'), index=True)
    name: Mapped[str] = mapped_column(String(110))
    description: Mapped[str] = mapped_column(String(250))
    category: Mapped[str] = mapped_column(String(24))
    recipient: Mapped[str] = mapped_column(String(24))
    use_hint: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(20), default='carried')
    uses: Mapped[int] = mapped_column(Integer, default=0)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_local)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reaction: Mapped[str] = mapped_column(Text, default='')


class WorldItemEvent(Base):
    __tablename__ = 'selin_wander_item_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey('selin_wander_owned_items.id'), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    reaction: Mapped[str] = mapped_column(Text, default='')
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_local)


def reroll_cost(state: ShopState | None, day: date | None = None) -> int:
    """First refresh each day is free; subsequent refreshes 5..25 coins."""
    day = day or now_local().date()
    count = int(state.rerolls_today or 0) if state and state.rerolls_on == day else 0
    return min(25, count * 5)


def server_price(rarity: str, category: str, slot: int) -> int:
    if rarity not in RARITIES or category not in CATEGORIES:
        raise ValueError('Bad generated item')
    tier = min(2, max(0, slot % 3))
    return PRICES[rarity][tier]


def validate_items(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != 6:
        raise ValueError('The shop needs exactly six items')
    clean = []
    seen = set()
    for slot, item in enumerate(rows):
        if not isinstance(item, dict):
            raise ValueError('Bad item structure')
        name = str(item.get('name') or '').strip()[:90]
        desc = str(item.get('description') or '').strip()[:220]
        hint = str(item.get('use_hint') or '').strip()[:210]
        category = str(item.get('category') or '')
        recipient = str(item.get('recipient') or '')
        rarity = str(item.get('rarity') or '')
        if not name or not desc or not hint or len(name) < 3 or name.casefold() in seen:
            raise ValueError('Shop entries must be useful and unique')
        if category not in CATEGORIES or recipient not in RECIPIENTS or rarity not in RARITIES:
            raise ValueError('Unknown shop item type')
        seen.add(name.casefold())
        clean.append(dict(slot=slot, name=name, description=desc, use_hint=hint,
                          category=category, recipient=recipient, rarity=rarity,
                          price=server_price(rarity, category, slot)))
    return clean


def fallback_items(seed: int = 0, excluded: set[str] | None = None) -> list[dict]:
    excluded = {x.casefold() for x in (excluded or set())}
    pool = [row for row in FALLBACK if row[0].casefold() not in excluded]
    if len(pool) < 6:
        pool = list(FALLBACK)
    # Deterministic but rotating; costs are set independently of AI or sampling.
    start = (seed * 7 + seed // 3) % len(pool)
    sampled = [pool[(start + i * 5) % len(pool)] for i in range(6)]
    if len({x[0] for x in sampled}) != 6:
        sampled = pool[:6]
    return validate_items([dict(zip(('name', 'description', 'category', 'recipient', 'rarity', 'use_hint'), v)) for v in sampled])


async def get_state(session: AsyncSession, user_id: int, *, lock: bool = False) -> ShopState | None:
    query = select(ShopState).where(ShopState.user_id == user_id)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def current_offers(session: AsyncSession, user_id: int) -> list[ShopOffer]:
    state = await get_state(session, user_id)
    if not state or not state.rotation:
        return []
    return list((await session.scalars(select(ShopOffer).where(
        ShopOffer.user_id == user_id, ShopOffer.rotation == state.rotation
    ).order_by(ShopOffer.slot))).all())


async def recent_names(session: AsyncSession, user_id: int) -> list[str]:
    rows = list((await session.scalars(select(ShopOffer.name).where(
        ShopOffer.user_id == user_id
    ).order_by(ShopOffer.id.desc()).limit(35))).all())
    return rows


async def install_rotation(session: AsyncSession, profile: UserProfile, items: list[dict],
                           *, expected_rotation: int, reroll: bool, today: date | None = None) -> int:
    """Charge + create offers in one transaction (caller commits). Concurrent UI rerolls fail closed."""
    today = today or now_local().date()
    items = validate_items(items)
    state = await get_state(session, profile.id, lock=True)
    if state is None:
        state = ShopState(user_id=profile.id, rotation=0, rerolls_today=0)
        session.add(state)
        await session.flush()
    if int(state.rotation) != expected_rotation:
        raise ValueError('Лавка уже обновлена, открой актуальный ассортимент.')
    if reroll and not state.rotation:
        raise ValueError('Сначала открой лавку.')
    if not reroll and state.rotation:
        raise ValueError('Лавка уже открыта.')
    cost = reroll_cost(state, today) if reroll else 0
    if cost:
        result = await session.execute(update(UserProfile)
            .where(UserProfile.id == profile.id, UserProfile.coins >= cost)
            .values(coins=UserProfile.coins - cost))
        if result.rowcount != 1:
            raise ValueError(f'Недостаточно монет для обновления ({cost}).')
        session.add(GameEvent(user_id=profile.id, event_type='wander_shop_refresh',
                              coins_delta=-cost, payload={'rotation': state.rotation + 1}))
    state.rotation += 1
    if reroll:
        count = (state.rerolls_today if state.rerolls_on == today else 0)
        state.rerolls_on = today
        state.rerolls_today = int(count or 0) + 1
    for row in items:
        session.add(ShopOffer(user_id=profile.id, rotation=state.rotation, **row))
    await session.flush()
    return cost


async def purchase(session: AsyncSession, profile: UserProfile, offer_id: int) -> OwnedWorldItem:
    """Requires explicit button action. No bonuses or automatic reward grants."""
    state = await get_state(session, profile.id, lock=True)
    offer = await session.scalar(select(ShopOffer).where(
        ShopOffer.id == offer_id, ShopOffer.user_id == profile.id).with_for_update())
    if not state or not offer or offer.rotation != state.rotation:
        raise ValueError('Этот ассортимент устарел. Открой лавку заново.')
    owned = await session.scalar(select(OwnedWorldItem).where(
        OwnedWorldItem.user_id == profile.id, OwnedWorldItem.offer_id == offer.id))
    if owned:
        raise ValueError('Этот предмет уже куплен.')
    result = await session.execute(update(UserProfile)
        .where(UserProfile.id == profile.id, UserProfile.coins >= offer.price)
        .values(coins=UserProfile.coins - offer.price))
    if result.rowcount != 1:
        raise ValueError(f'Нужно {offer.price} монет. Можно заработать ещё в приключениях.')
    status = 'gifted' if offer.recipient in {'selin', 'tori'} else 'placed' if offer.recipient == 'camp' else 'carried'
    owned = OwnedWorldItem(user_id=profile.id, offer_id=offer.id,
        name=offer.name, description=offer.description, category=offer.category,
        recipient=offer.recipient, use_hint=offer.use_hint, status=status)
    session.add(owned)
    await session.flush()
    session.add(GameEvent(user_id=profile.id, event_type='wander_item_purchase', coins_delta=-offer.price,
                          payload={'offer_id': offer.id, 'owned_id': owned.id, 'item': offer.name}))
    session.add(WorldItemEvent(user_id=profile.id, item_id=owned.id, event_type='acquired', reaction=''))
    return owned


async def use_item(session: AsyncSession, profile: UserProfile, item_id: int) -> OwnedWorldItem:
    item = await session.scalar(select(OwnedWorldItem).where(
        OwnedWorldItem.user_id == profile.id, OwnedWorldItem.id == item_id).with_for_update())
    if item is None:
        raise ValueError('Этот предмет не найден в твоём инвентаре.')
    item.uses = int(item.uses or 0) + 1
    item.last_used_at = now_local()
    session.add(WorldItemEvent(user_id=profile.id, item_id=item.id, event_type='used', reaction=''))
    await session.flush()
    return item


async def save_reaction(session: AsyncSession, user_id: int, item_id: int,
                        reaction: str, *, event_type: str = 'reaction') -> None:
    owned = await session.scalar(select(OwnedWorldItem).where(
        OwnedWorldItem.id == item_id, OwnedWorldItem.user_id == user_id))
    if not owned:
        return
    reaction = reaction.strip()[:850]
    owned.last_reaction = reaction
    session.add(WorldItemEvent(user_id=user_id, item_id=item_id,
                               event_type=event_type, reaction=reaction))


async def inventory(session: AsyncSession, user_id: int, limit: int = 20) -> list[OwnedWorldItem]:
    return list((await session.scalars(select(OwnedWorldItem).where(
        OwnedWorldItem.user_id == user_id
    ).order_by(OwnedWorldItem.id.desc()).limit(limit))).all())


async def inventory_context(session: AsyncSession, user_id: int) -> str:
    items = await inventory(session, user_id, limit=12)
    if not items:
        return ''
    lines = ['Реально приобретённые игровые вещи; НЕ выдавай за них монеты/XP и не придумывай покупки:']
    for item in reversed(items):
        usage = f' (использована {item.uses} раз)' if item.uses else ''
        lines.append(f'- {item.name} [{item.recipient}, {item.status}]{usage}: {item.use_hint}')
        if item.last_reaction:
            lines.append(f'  Предыдущая реакция: {item.last_reaction[:160]}')
    lines.append('В свободном диалоге можешь ненавязчиво вспомнить одну уместную вещь, ' 
                 'а не перечислять все сразу. Не переписывай покупку и прошлые события.')
    return '\n'.join(lines)[:2800]
