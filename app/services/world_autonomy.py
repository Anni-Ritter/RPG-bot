"""Generative, persistent background life for Selin and Tori.

The model writes narrative only. Python chooses an actual owned item, checks owner,
records its usage, and stores continuity. No autonomous money/inventory changes.
"""
from __future__ import annotations

import json
import random
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.services.ai_features import now_local
from app.services.wandering_shop import OwnedWorldItem, inventory, use_item, save_reaction
from app.services.ai_engine import _structured_response, SELIN_EMOTIONS, TORI_EMOTIONS


class WorldScene(Base):
    """Actual scenes sent by the bot, not suggestions invented by the LLM."""
    __tablename__ = 'selin_world_autonomous_scenes'
    __table_args__ = (UniqueConstraint('notification_id', name='uq_selin_world_scene_notification'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_profiles.id', ondelete='CASCADE'), index=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey('notifications.id'), index=True)
    character: Mapped[str] = mapped_column(String(10))
    item_id: Mapped[int | None] = mapped_column(ForeignKey('selin_wander_owned_items.id'), nullable=True)
    emotion: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_local)


SCENE_SCHEMA = {
    'type': 'object',
    'properties': {
        'text': {'type': 'string'},
        'emotion': {'type': 'string', 'enum': sorted(set(SELIN_EMOTIONS + TORI_EMOTIONS))},
        'uses_item': {'type': 'boolean'},
    },
    'required': ['text', 'emotion', 'uses_item'],
    'additionalProperties': False,
}


async def recent_scenes(session: AsyncSession, user_id: int, limit: int = 9) -> list[WorldScene]:
    return list((await session.scalars(select(WorldScene).where(
        WorldScene.user_id == user_id
    ).order_by(WorldScene.id.desc()).limit(limit))).all())


def select_scene_item(items: list[OwnedWorldItem], scenes: list[WorldScene], *, rng=None) -> OwnedWorldItem | None:
    """Avoid the same toy in consecutive notifications; occasionally no item at all."""
    rng = rng or random
    if not items or rng.random() < 0.30:
        return None
    blocked = {scene.item_id for scene in scenes[:3] if scene.item_id is not None}
    eligible = [item for item in items if item.id not in blocked]
    if not eligible:
        return None
    # Old or never-used items deserve a chance. Randomness avoids a deterministic carousel.
    return rng.choice(sorted(eligible, key=lambda item: (int(item.uses or 0), item.id))[:8])


def select_character(chapter: int, item: OwnedWorldItem | None, *, rng=None) -> str:
    rng = rng or random
    if chapter < 2:
        return 'selin'
    if item and item.recipient in {'tori', 'selin'} and rng.random() < 0.80:
        return item.recipient
    return rng.choice(['tori', 'selin'])


def scene_history_text(scenes: list[WorldScene]) -> str:
    if not scenes:
        return 'Пока нет самостоятельных сцен.'
    return '\n'.join(f'- {row.character}: {row.text[:230]}' for row in reversed(scenes[:8]))[:2100]


async def world_continuity_context(session: AsyncSession, user_id: int) -> str:
    scenes = await recent_scenes(session, user_id, 6)
    if not scenes:
        return ''
    return ('Недавние реальные эпизоды между Селин и Тори (не повторяй дословно; '
            'это уже произошло):\n' + scene_history_text(scenes))


async def generate_world_scene(*, context: dict[str, Any], period: str,
                               actor: str, item: OwnedWorldItem | None,
                               history: list[WorldScene]) -> dict[str, Any]:
    """One fresh scene. The model cannot choose/forge an item ID or affect rewards."""
    if actor not in {'selin', 'tori'}:
        raise ValueError('Unknown actor')
    available = json.dumps({
        'character': actor,
        'time_of_day': period,
        'story_day': int(context.get('story_day') or 1),
        'story_objective': str(context.get('objective') or '')[:300],
        'relationship': str(context.get('selin_relation') or '')[:150],
        'tori_bond': int(context.get('tori_bond') or 0),
        'recent_user_context': str(context.get('memories') or '')[:1700],
        'previous_scenes': scene_history_text(history),
        'available_owned_item': ({'name':item.name, 'description':item.description,
                                  'recipient':item.recipient, 'use_hint':item.use_hint}
                                 if item else None),
    }, ensure_ascii=False)
    system = '''Ты создаёшь новую инициативную реплику для живой фэнтезийной RPG с Селин и Тори.
Это ОДНО СВЕЖЕЕ событие, а не повторение заготовленного сценария. Тори — маленький белый лис,
он НЕ говорит человеческими словами. Селин — самостоятельная, язвительная, сдержанная;
её характер отличается от манеры Тори. Пиши живо, коротко (1–4 предложения), без
пафосной прозы, нотаций, призывов к спорту или контролю питания.
Не используй типичный циклический сюжет "опять что-то притащил / стащил и спрятал":
чередуй игру, отдых, смешные привычки, маленькие разговоры, бытовые эпизоды и детали пути.
Если дан КОНКРЕТНЫЙ купленный предмет, можешь естественно показать, как он теперь
используется персонажем. Не забывай предыдущую историю, но не пересказывай её.
Если дан null — никаких выдуманных "уже купленных" вещей.
Окружающие мелочи можно находить/перекладывать для атмосферы, но это НЕ новая покупка,
не начисление монет, не запись в инвентарь и не сюжетный артефакт.
Не придумывай необратимые изменения главного канона, угрозы здоровью, дорогие покупки,
обязательные задания или обещания выполненных действий. Никаких меню и кнопок.
Используй ровно одного рассказчика — character из входных данных.
Если предмет упомянут как физически использованный в сцене, uses_item=true;
если нет предмета или он не используется — false. Верни строго JSON.'''
    raw = await _structured_response(system=system, user_content=available,
                                     schema_name='selin_world_scene_v1', schema=SCENE_SCHEMA,
                                     max_tokens=650, retry_max_tokens=1200)
    text = str(raw.get('text') or '').strip()[:850]
    emotion = str(raw.get('emotion') or 'neutral')
    if not 25 <= len(text) <= 850:
        raise ValueError('Empty or unsuitable generated scene')
    if actor == 'tori' and emotion not in TORI_EMOTIONS:
        emotion = 'curious'
    if actor == 'selin' and emotion not in SELIN_EMOTIONS:
        emotion = 'neutral'
    # Never trust model to invent IDs. Its only writable effect is this Boolean.
    return {'character': actor, 'text': text, 'emotion': emotion,
            'uses_item': bool(item is not None and raw.get('uses_item') is True)}


async def apply_world_scene(session: AsyncSession, profile, notification,
                            scene: dict, item: OwnedWorldItem | None) -> WorldScene:
    """Store only a sent scene. Item usage is rechecked inside the DB transaction."""
    existing = await session.scalar(select(WorldScene).where(
        WorldScene.notification_id == notification.id))
    if existing:
        return existing
    owned_id = None
    if scene['uses_item'] and item is not None:
        actual = await session.scalar(select(OwnedWorldItem).where(
            OwnedWorldItem.user_id == profile.id, OwnedWorldItem.id == item.id))
        if actual is None:
            raise ValueError('Scene item is not owned by this user')
        await use_item(session, profile, actual.id)
        await save_reaction(session, profile.id, actual.id, scene['text'],
                            event_type='autonomous_scene')
        owned_id = actual.id
    row = WorldScene(user_id=profile.id, notification_id=notification.id,
                     character=scene['character'], item_id=owned_id,
                     emotion=scene['emotion'], text=scene['text'])
    session.add(row)
    await session.flush()
    return row
