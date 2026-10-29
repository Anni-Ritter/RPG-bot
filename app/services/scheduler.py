from __future__ import annotations

import asyncio
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.keyboards import (
    ai_initiative_menu,
    daily_challenge_open_menu,
    monday_finish_menu,
    tori_autonomous_menu,
    temptation_after_pause_menu,
    thursday_second_menu,
    workout_start_menu,
)
from app.keyboards import challenge_finalize_menu
from app.models import Notification, PendingTemptation, UserProfile, WorkoutSession
from app.services.assets import send_reaction_to_chat
from app.services.ai_engine import ai_enabled, generate_selin_initiative
from app.services.ai_features import (
    append_recent_message,
    build_ai_context,
    can_offer_ai_quest,
    create_ai_quest_offer,
    get_or_create_ai_state,
    reserve_ai_call,
)
from app.services.dialogue import pick_dialogue
from app.services.evening_review import create_evening_review
from app.services.story import (advance_week1_if_due, get_or_create_story_progress, initialize_week1_v2, selin_chat_gate, story_chapter, story_day)



# SELIN_LIVING_WORLD_AI_20261029_BEGIN
from app.services.world_autonomy import (
    WorldScene, recent_scenes, select_scene_item, select_character,
    generate_world_scene, apply_world_scene,
)
from app.services.wandering_shop import inventory
# SELIN_LIVING_WORLD_AI_20261029_END

TZ = ZoneInfo(settings.timezone)


def local_dt(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def random_dt(day: date, start_h: int, start_m: int, end_h: int, end_m: int) -> datetime:
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    minute_of_day = random.randint(start, end)
    return local_dt(day, minute_of_day // 60, minute_of_day % 60)


async def _ensure_notification(
    session,
    profile: UserProfile,
    kind: str,
    scheduled_at: datetime,
    key: str,
    payload: dict | None = None,
) -> None:
    exists = await session.scalar(select(Notification.id).where(Notification.dedupe_key == key))
    if exists:
        return
    session.add(
        Notification(
            user_id=profile.id,
            kind=kind,
            scheduled_at=scheduled_at,
            dedupe_key=key,
            payload=payload or {},
        )
    )


async def ensure_day_schedule(day: date) -> None:
    async with SessionLocal() as session:
        profiles = list((await session.scalars(select(UserProfile))).all())
        weekday = day.weekday()  # Mon=0

        for profile in profiles:
            prefix = f"{profile.id}:{day.isoformat()}"

            daytime = random_dt(day, 8, 0, 15, 0)
            evening = random_dt(day, 15, 1, 23, 0)
            await _ensure_notification(
                session,
                profile,
                "autonomous_event",
                daytime,
                f"{prefix}:autonomous:day",
                {"period": "day"},
            )
            await _ensure_notification(
                session,
                profile,
                "autonomous_event",
                evening,
                f"{prefix}:autonomous:evening",
                {"period": "evening"},
            )
            await _ensure_notification(
                session,
                profile,
                "daily_challenge_offer",
                local_dt(day, 8, 30),
                f"{prefix}:daily_challenge",
            )
            await _ensure_notification(
                session,
                profile,
                "evening_nutrition_review",
                local_dt(day, settings.evening_review_hour, settings.evening_review_minute),
                f"{prefix}:evening_review",
            )

            if weekday == 0:
                await _ensure_notification(
                    session, profile, "monday_start", local_dt(day, 19, 15), f"{prefix}:monday_start"
                )
                await _ensure_notification(
                    session, profile, "monday_finish", local_dt(day, 20, 15), f"{prefix}:monday_finish"
                )
            elif weekday == 3:
                await _ensure_notification(
                    session, profile, "thursday_start", local_dt(day, 18, 30), f"{prefix}:thursday_start"
                )
                await _ensure_notification(
                    session, profile, "thursday_second", local_dt(day, 19, 15), f"{prefix}:thursday_second"
                )

        await session.commit()


async def ensure_schedule() -> None:
    today = datetime.now(TZ).date()
    await ensure_day_schedule(today)
    await ensure_day_schedule(today + timedelta(days=1))


async def _get_profile_telegram_id(session, user_id: int) -> int:
    profile = await session.get(UserProfile, user_id)
    return profile.telegram_id


async def _send_world_autonomous_event(
    session, notification: Notification, bot: Bot, telegram_id: int,
    profile: UserProfile, now: datetime, chapter: int,
) -> bool | None:
    """True=delivered; False=skipped; None=retry saved scene after network error.

    The world event and item use are committed BEFORE the Telegram message. If
    delivery fails, next worker tick resends exactly that scene, not a new one.
    """
    from app.services.world_autonomy import WorldScene
    from sqlalchemy import select

    existing = await session.scalar(select(WorldScene).where(
        WorldScene.notification_id == notification.id))
    if existing is not None:
        try:
            await send_reaction_to_chat(bot, telegram_id, existing.character,
                                        existing.emotion, existing.text)
            return True
        except Exception:
            import logging
            logging.getLogger('selin.world').exception('World scene delivery failed, retrying same scene')
            return None

    if not ai_enabled():
        return False
    progress = await get_or_create_story_progress(session, profile.id, now.date())
    initialize_week1_v2(progress, profile, now.date())
    advance_week1_if_due(progress, now.date())
    allowed, _ = selin_chat_gate(profile, progress, now.date())
    if not allowed or not await reserve_ai_call(session, profile.id, 'initiative'):
        return False
    try:
        context = await build_ai_context(session, profile)
        history = await recent_scenes(session, profile.id)
        objects = await inventory(session, profile.id, limit=50)
        item = select_scene_item(objects, history)
        character = select_character(chapter, item)
        period = str((notification.payload or {}).get('period') or 'day')
        await session.commit()
        scene = await generate_world_scene(context=context, period=period,
                                           actor=character, item=item, history=history)
        from difflib import SequenceMatcher
        if any(SequenceMatcher(None, scene['text'].casefold(), old.text.casefold()).ratio() > 0.79
               for old in history[:7]):
            return False
        await apply_world_scene(session, profile, notification, scene, item)
        notification.payload = {**dict(notification.payload or {}),
                                'event_type': 'world_ai', 'status': 'closed',
                                'character': scene['character'],
                                'item_id': item.id if item and scene['uses_item'] else None}
        state = await get_or_create_ai_state(session, profile)
        append_recent_message(state, 'assistant', scene['text'])
        state.last_initiative_on = now.date()
        await session.commit()
        # No claiming to use an item until the actual change is persisted.
        try:
            await send_reaction_to_chat(bot, telegram_id, scene['character'],
                                        scene['emotion'], scene['text'])
            return True
        except Exception:
            import logging
            logging.getLogger('selin.world').exception('World scene delivery failed; retry pending')
            return None
    except Exception:
        import logging
        logging.getLogger('selin.world').exception('AI world scene failed; no canned fallback')
        await session.rollback()
        await session.refresh(notification)
        return False


async def send_due(bot: Bot) -> None:
    now = datetime.now(TZ)

    async with SessionLocal() as session:
        due = list(
            (
                await session.scalars(
                    select(Notification)
                    .where(Notification.sent_at.is_(None), Notification.scheduled_at <= now)
                    .order_by(Notification.scheduled_at)
                    .limit(50)
                )
            ).all()
        )

        for n in due:
            telegram_id = await _get_profile_telegram_id(session, n.user_id)
            progress = await get_or_create_story_progress(session, n.user_id, now.date())
            current_story_day = story_day(progress, now.date())
            chapter = story_chapter(current_story_day)

            if n.kind == "autonomous_event":
                profile = await session.get(UserProfile, n.user_id)
                if profile:
                    delivered = await _send_world_autonomous_event(
                        session, n, bot, telegram_id, profile, now, chapter,
                    )
                    if delivered is None:
                        # DB already stores the scene; retry Telegram delivery next tick.
                        continue

            elif n.kind == "activity_nudge":
                replacement = await session.scalar(
                    select(Notification.id).where(
                        Notification.user_id == n.user_id,
                        Notification.kind == "autonomous_event",
                        Notification.scheduled_at >= local_dt(now.date(), 0, 0),
                        Notification.scheduled_at < local_dt(now.date() + timedelta(days=1), 0, 0),
                    ).limit(1)
                )
                if replacement:
                    n.sent_at = now
                    continue
                sent_ai = False
                profile = await session.get(UserProfile, n.user_id)
                if profile and ai_enabled():
                    progress_for_chat = await get_or_create_story_progress(session, profile.id, now.date())
                    initialize_week1_v2(progress_for_chat, profile, now.date())
                    advance_week1_if_due(progress_for_chat, now.date())
                    chat_allowed, _ = selin_chat_gate(profile, progress_for_chat, now.date())
                    if chat_allowed and await reserve_ai_call(session, profile.id, "initiative"):
                        try:
                            context = await build_ai_context(session, profile)
                            can_offer = await can_offer_ai_quest(session, profile.id, now.date())
                            # Release the SQLite write transaction before the network call.
                            await session.commit()
                            result = await generate_selin_initiative(context=context, can_offer_quest=can_offer)
                            offer = None
                            if result.get("offer_quest") and can_offer:
                                title = str(result.get("quest_title") or "").strip()
                                if title:
                                    offer = await create_ai_quest_offer(
                                        session,
                                        profile,
                                        title=title,
                                        difficulty=str(result.get("quest_difficulty") or "easy"),
                                        reason=str(result.get("quest_reason") or ""),
                                    )
                            text = str(result.get("text") or "Ты сегодня вообще собираешься двигаться?")
                            if offer:
                                text += (
                                    f"\n\n📜 Задание: {offer.title}"
                                    f"\nНаграда: +{offer.reward_xp} XP · +{offer.reward_coins} монет"
                                )
                                if offer.reason:
                                    text += f"\n{offer.reason}"
                            markup = ai_initiative_menu(n.id, offer.id if offer else None)
                            n.payload = {
                                **dict(n.payload or {}),
                                "event_type": "selin",
                                "status": "open",
                                "initiative_text": text,
                            }
                            await send_reaction_to_chat(
                                bot, telegram_id, "selin", str(result.get("emotion") or "neutral"), text,
                                reply_markup=markup,
                            )
                            if result.get("show_tori") and str(result.get("tori_text") or "").strip():
                                await send_reaction_to_chat(
                                    bot, telegram_id, "tori", str(result.get("tori_emotion") or "neutral"),
                                    str(result.get("tori_text")),
                                )
                            ai_state = await get_or_create_ai_state(session, profile)
                            append_recent_message(ai_state, "assistant", text)
                            ai_state.last_initiative_on = now.date()
                            sent_ai = True
                        except Exception as exc:
                            print("AI initiative error:", repr(exc))
                if not sent_ai:
                    # No repetitive canned fallback. This is an optional initiative.
                    pass

            elif n.kind == "daily_challenge_offer":
                await send_reaction_to_chat(
                    bot,
                    telegram_id,
                    "selin",
                    "curious",
                    "— Новый день. Я подготовила три испытания. Можешь взять одно, два или вообще все три. За провал ничего не снимаю — награда просто останется у меня.",
                    reply_markup=daily_challenge_open_menu(),
                )

            elif n.kind == "evening_nutrition_review":
                profile = await session.get(UserProfile, n.user_id)
                if profile:
                    try:
                        review, chest, _ = await create_evening_review(session, profile, now.date(), force=True, finalize_challenge=False)
                        await session.commit()
                        text = review.text
                        if review.tomorrow_focus:
                            text += f"\n\nНа завтра я бы оставила один фокус: {review.tomorrow_focus}"
                        text += (
                            "\n\n🎯 Челленджи я пока не закрываю. "
                            "Если что-то забыла отметить — внеси это сейчас. "
                            "Когда всё будет записано, нажми кнопку ниже."
                        )
                        await send_reaction_to_chat(
                            bot, telegram_id, "selin", review.emotion or "neutral", text,
                            reply_markup=challenge_finalize_menu(now.date().isoformat()),
                        )
                    except Exception as exc:
                        print("Evening review send error:", repr(exc))

            elif n.kind == "monday_start":
                await send_reaction_to_chat(
                    bot, telegram_id, "selin", "beckoning",
                    pick_dialogue("monday_start", chapter),
                    reply_markup=workout_start_menu("monday"),
                )

            elif n.kind == "monday_finish":
                workout = await session.scalar(
                    select(WorkoutSession).where(
                        WorkoutSession.user_id == n.user_id,
                        WorkoutSession.workout_date == now.date(),
                        WorkoutSession.kind == "monday",
                        WorkoutSession.status == "in_progress",
                    )
                )
                if workout:
                    await send_reaction_to_chat(
                        bot, telegram_id, "selin", "triumphant",
                        pick_dialogue("monday_finish", chapter),
                        reply_markup=monday_finish_menu(),
                    )

            elif n.kind == "thursday_start":
                await send_reaction_to_chat(
                    bot, telegram_id, "selin", "smirk",
                    pick_dialogue("thursday_start", chapter),
                    reply_markup=workout_start_menu("thursday_first"),
                )

            elif n.kind == "thursday_second":
                workout = await session.scalar(
                    select(WorkoutSession).where(
                        WorkoutSession.user_id == n.user_id,
                        WorkoutSession.workout_date == now.date(),
                        WorkoutSession.kind == "thursday_first",
                        WorkoutSession.status == "in_progress",
                    )
                )
                if workout:
                    await send_reaction_to_chat(
                        bot, telegram_id, "tori", "at_door",
                        pick_dialogue("thursday_second", chapter),
                        reply_markup=thursday_second_menu(),
                    )

            elif n.kind == "temptation_followup":
                temptation_id = int((n.payload or {}).get("temptation_id", 0))
                pending = await session.get(PendingTemptation, temptation_id) if temptation_id else None
                if pending and pending.user_id == n.user_id and pending.status == "waiting":
                    await send_reaction_to_chat(
                        bot, telegram_id, "selin", "judging",
                        "Пятнадцать минут прошло.\n\nСелин: — Ну что? Всё ещё хочешь?",
                        reply_markup=temptation_after_pause_menu(pending.id),
                    )

            elif n.kind == "story_checkpoint":
                payload = n.payload or {}
                await send_reaction_to_chat(
                    bot,
                    telegram_id,
                    str(payload.get("character", "selin")),
                    str(payload.get("emotion", "neutral")),
                    str(payload.get("text", "В Истории появилось продолжение.")),
                )

            n.sent_at = now

        await session.commit()


async def notification_worker(bot: Bot) -> None:
    while True:
        try:
            await ensure_schedule()
            await send_due(bot)
        except Exception as exc:
            print("notification_worker error:", repr(exc))
        await asyncio.sleep(30)

# SELIN_LIVING_RHYTHM_20261027_BEGIN
_living_rhythm_original_send_due = send_due

async def send_due(bot):
    from app.services.living_rhythm import suppress_nonessential_autonomy
    await suppress_nonessential_autonomy()
    return await _living_rhythm_original_send_due(bot)
# SELIN_LIVING_RHYTHM_20261027_END

# SELIN_ACTIVITY_BREAK_20261027_BEGIN
# Outer wrapper preserves the installed Living Rhythm suppression policy.
_activity_break_previous_send_due = send_due

async def send_due(bot):
    from app.services.activity_breaks import (
        now_local, send_due_checkins, suppress_due_sport_notifications,
    )
    now = now_local()
    # Already queued sport reminders must be suppressed *before* dispatch.
    await suppress_due_sport_notifications(now)
    await send_due_checkins(bot, now)
    return await _activity_break_previous_send_due(bot)
# SELIN_ACTIVITY_BREAK_20261027_END
