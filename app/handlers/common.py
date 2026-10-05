import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.keyboards import back_menu, drink_menu, food_menu, main_menu, quest_menu, temptation_menu, wardrobe_menu
from app.models import CustomQuest, Notification, PendingTemptation, UserAchievement
from app.services.achievements import achievement_messages, check_achievements
from app.services.ai_engine import ai_enabled
from app.services.ai_features import daily_treat_count, get_calorie_target
from app.services.assets import send_background, send_reaction, step_reaction_emotion
from app.services.levels import level_from_xp
from app.services.nutrition import daily_nutrition_totals
from app.services.phrases import (
    DRINK_REACTIONS,
    MEAL_REACTIONS,
    WATER_REACTIONS,
    custom_quest_created,
    custom_quest_done,
    pick,
    snack_reaction,
)
from app.services.rewards import apply_reward, get_or_create_daily, get_or_create_profile
from app.services.steps import evaluate_steps
from app.services.self_workouts import weekly_workout_summary
from app.services.challenges import challenge_value, get_plan, progress_text
from app.services.story import (
    advance_week1_if_due, affinity_label, current_story_objective,
    get_or_create_story_progress, initialize_week1_v2, next_sync_unlock,
    selin_chat_gate, story_day, tori_bond_label,
)

router = Router()
TZ = ZoneInfo(settings.timezone)


def now_local() -> datetime:
    return datetime.now(TZ)


class StepState(StatesGroup):
    waiting_steps = State()


class QuestState(StatesGroup):
    waiting_title = State()
    waiting_difficulty = State()


@router.callback_query(F.data == "state:cancel")
async def cancel_state(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Действие отменено.", reply_markup=main_menu())
    await callback.answer()


@router.message(CommandStart())
async def start(message: Message) -> None:
    today = now_local().date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(
            session, message.from_user.id, message.from_user.full_name if message.from_user else None
        )
        progress = await get_or_create_story_progress(session, profile.id, today)
        initialize_week1_v2(progress, profile, today)
        advance_week1_if_due(progress, today)
        await session.commit()
    await send_background(
        message,
        "atelier_default",
        "Связь с Селин активна. Теперь развитие персонажа связано с сюжетом: XP усиливает синхронизацию, характеристики открывают варианты, а отношения — новые разговоры.\n\nНачни с 📖 История.",
        reply_markup=main_menu(),
    )


@router.message(F.text == "📊 Сегодня")
async def today(message: Message) -> None:
    today_date = now_local().date()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, today_date)
        progress = await get_or_create_story_progress(session, profile.id, today_date)
        initialize_week1_v2(progress, profile, today_date)
        advance_week1_if_due(progress, today_date)
        workout_week = await weekly_workout_summary(session, profile.id, today_date)
        nutrition = await daily_nutrition_totals(session, profile.id, today_date)
        treats = await daily_treat_count(session, profile.id, today_date)
        calorie_target = await get_calorie_target(session, profile.id)
        level, current_threshold, next_threshold = level_from_xp(profile.xp)
        day = story_day(progress, today_date)
        objective = current_story_objective(profile, progress, today_date)
        relation = affinity_label(progress)
        tori_relation = tori_bond_label(profile.tori_bond)
        chat_allowed, chat_reason = selin_chat_gate(profile, progress, today_date)
        challenge = await get_plan(session, profile.id, today_date)
        challenge_line = "🎯 Челлендж: ещё не выбран"
        if challenge and challenge.status == "active" and challenge.selected:
            current = await challenge_value(session, profile, challenge)
            selected = dict(challenge.selected or {})
            challenge_line = (
                f"🎯 {selected.get('title', 'Челлендж')}: "
                + progress_text(str(selected.get('code') or ''), current, int(selected.get('target') or 0))
            )
        elif challenge and challenge.status == "completed":
            challenge_line = "🎯 Челлендж: выполнен ✅"
        elif challenge and challenge.status == "failed":
            challenge_line = "🎯 Челлендж: не выполнен"
        await session.commit()

    if next_threshold is None:
        sync_line = f"Уровень {level} · {profile.xp} XP"
        to_next = "MAX"
    else:
        sync_line = f"Уровень {level} · {profile.xp} / {next_threshold} XP"
        to_next = f"До следующего уровня: {max(0, next_threshold - profile.xp)} XP"

    if not ai_enabled():
        ai_line = "✨ AI-общение: не настроено"
    elif chat_allowed:
        ai_line = "💬 Селин: свободный разговор доступен"
    else:
        ai_line = f"💬 Селин: связь занята сюжетом — {chat_reason}"


    nutrition_line = f"\n🧭 Ориентир по энергии: около {calorie_target} ккал"
    if nutrition["count"] > 0:
        delta = int(nutrition["calories_kcal"]) - int(calorie_target)
        delta_text = f" · {delta:+d} к ориентиру" if delta else " · по текущей оценке ровно в ориентир"
        nutrition_line += (
            f"\n📐 КБЖУ по распознанным записям: {nutrition['calories_kcal']} ккал"
            f" · Б {nutrition['protein_g']:g} · Ж {nutrition['fat_g']:g} · У {nutrition['carbs_g']:g}"
            f" ({nutrition['count']} шт.){delta_text}"
        )

    chapter = "Руины" if day <= 7 else ({2: "Лес", 3: "Таррен", 4: "Шпиль"}.get(((day - 1) // 7) + 1, "История"))
    await send_background(
        message,
        "atelier_default",
        (
            f"Глава · {chapter}\n"
            f"День сюжета: {day}/28\n\n"
            f"✦ Синхронизация с Селин: {sync_line}\n"
            f"{to_next}\n"
            f"Следующее открытие: {next_sync_unlock(level)}\n\n"
            f"💪 Сила: {profile.strength} · 🜂 Воля: {profile.willpower}\n"
            f"💬 Селин: {relation}\n"
            f"🦊 Тори: {profile.tori_bond} · {tori_relation}\n"
            f"{ai_line}\n"
            f"{challenge_line}\n\n"
            f"🎯 Текущая сюжетная задача:\n{objective}\n\n"
            f"Сегодня:\n"
            f"🍲 Еда: {stat.meals} · 🍎 Перекусы: {stat.snacks} · 🍰 Вкусняшки: {treats}\n"
            f"☕ Напитки: {stat.drinks} · ⚡ Энергетики: {stat.energy_drinks}\n"
            f"💧 Вода: {stat.water} · 🚶 Шаги: {stat.steps:,}{nutrition_line}\n"
            f"🏋️ Тренировки за неделю: {workout_week['count']} · {workout_week['minutes']} мин\n\n"
            f"Ателье: {profile.coins} монет · {profile.atelier_dust} Пыли · {profile.trial_chests} сундуков"
        ).replace(",", " "),
    )


@router.message(F.text == "🏆 Ачивки")
async def achievements(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        rows = list(
            (await session.scalars(
                select(UserAchievement)
                .where(UserAchievement.user_id == profile.id)
                .order_by(UserAchievement.unlocked_at.desc())
            )).all()
        )
        await session.commit()
    if not rows:
        await message.answer("Пока ни одной ачивки. Они скрыты до момента получения.")
        return
    await message.answer("🏆 Ачивки\n\n" + "\n".join(f"• {r.title}" for r in rows))


@router.message(F.text == "🍽 Еда")
async def food(message: Message) -> None:
    await send_background(message, "food_nook", "Что отмечаем?", reply_markup=food_menu())


@router.callback_query(F.data == "food:meal")
async def meal(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now_local().date())
        stat.meals += 1
        rewarded = stat.meals <= 3
        if rewarded:
            await apply_reward(session, profile, event_type="meal_complete", xp=20, coins=3)
        unlocked = await check_achievements(session, profile)
        await session.commit()
    suffix = "\n+20 XP · +3 монеты" if rewarded else "\nЛимит награды за еду на сегодня уже достигнут."
    await send_reaction(callback.message, "tori", "curious", pick(MEAL_REACTIONS) + suffix)
    for text in achievement_messages(unlocked):
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "food:water")
async def water(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now_local().date())
        stat.water += 1
        rewarded = stat.water <= 3
        if rewarded:
            await apply_reward(session, profile, event_type="water", xp=3, bond=1)
        unlocked = await check_achievements(session, profile)
        await session.commit()
    suffix = "\n+3 XP · +1 связь с Тори" if rewarded else "\nСегодняшний лимит награды за воду уже закрыт."
    await send_reaction(callback.message, "tori", "happy", pick(WATER_REACTIONS) + suffix)
    for text in achievement_messages(unlocked):
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "food:snack")
async def snack(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now_local().date())
        stat.snacks += 1
        count = stat.snacks
        await apply_reward(session, profile, event_type="snack", payload={"count_today": count})
        await session.commit()
    await send_reaction(callback.message, "tori", "judging" if count >= 3 else "curious", snack_reaction(count))
    await callback.answer()


@router.callback_query(F.data == "food:treat")
async def treat(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now_local().date())
        stat.snacks += 1
        await apply_reward(
            session,
            profile,
            event_type="treat_logged",
            payload={"source": "manual", "count_today": stat.snacks},
        )
        await session.commit()
    await send_reaction(
        callback.message,
        "selin",
        "neutral",
        "— Записала как вкусняшку. Никаких штрафов; просто челленджи и вечерний разбор теперь видят её отдельно.",
    )
    await callback.answer()


@router.callback_query(F.data == "food:drink")
async def drink(callback: CallbackQuery) -> None:
    await callback.message.answer("Какой напиток?", reply_markup=drink_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("drink:"))
async def drink_type(callback: CallbackQuery) -> None:
    kind = callback.data.split(":", 1)[1]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now_local().date())
        stat.drinks += 1
        if kind == "plain":
            stat.plain_drinks += 1
        elif kind == "caloric":
            stat.caloric_drinks += 1
        elif kind == "energy":
            stat.energy_drinks += 1
        await apply_reward(session, profile, event_type="drink", payload={"kind": kind})
        await session.commit()
    await callback.message.answer(pick(DRINK_REACTIONS))
    await callback.answer()


@router.callback_query(F.data == "food:temptation")
async def temptation(callback: CallbackQuery) -> None:
    await send_reaction(
        callback.message, "selin", "curious",
        "Селин: — Для начала ответь честно: ты действительно этого хочешь?",
        reply_markup=temptation_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "tempt:want")
async def temptation_want(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now_local().date())
        stat.temptation_count += 1
        await apply_reward(session, profile, event_type="temptation_accepted")
        await session.commit()
    await send_reaction(callback.message, "selin", "neutral", "Селин: — Тогда ешь и перестань устраивать из этого нравственную трагедию.")
    await callback.answer()


@router.callback_query(F.data == "tempt:impulse")
async def temptation_impulse(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now_local().date())
        stat.temptation_count += 1
        rewarded = stat.rewarded_temptations < 3
        if rewarded:
            stat.rewarded_temptations += 1
            await apply_reward(session, profile, event_type="temptation_impulse_caught", xp=10, willpower=1)
        unlocked = await check_achievements(session, profile)
        await session.commit()
    text = "Селин: — Вот это мне уже нравится."
    if rewarded:
        text += "\n+10 XP · +1 Воля"
    await send_reaction(callback.message, "selin", "smirk", text)
    for msg in achievement_messages(unlocked):
        await callback.message.answer(msg)
    await callback.answer()


@router.callback_query(F.data == "tempt:pause")
async def temptation_pause(callback: CallbackQuery) -> None:
    now = now_local()
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now.date())
        stat.temptation_count += 1
        eligible = stat.rewarded_temptations < 3
        if eligible:
            stat.rewarded_temptations += 1
            await apply_reward(session, profile, event_type="temptation_pause", xp=5)
        pending = PendingTemptation(
            user_id=profile.id,
            status="waiting",
            reward_eligible=eligible,
            due_at=now + timedelta(minutes=15),
        )
        session.add(pending)
        await session.flush()
        session.add(Notification(
            user_id=profile.id,
            kind="temptation_followup",
            scheduled_at=pending.due_at,
            dedupe_key=f"temptation:{pending.id}",
            payload={"temptation_id": pending.id},
        ))
        await session.commit()
    extra = "\n+5 XP за паузу." if eligible else ""
    await send_reaction(callback.message, "selin", "neutral", "Селин: — Хорошо. Пятнадцать минут. Потом решишь ещё раз." + extra)
    await callback.answer()


@router.callback_query(F.data.startswith("tempt:still_want:"))
async def temptation_still_want(callback: CallbackQuery) -> None:
    temptation_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        pending = await session.get(PendingTemptation, temptation_id)
        if not pending or pending.user_id != profile.id or pending.status != "waiting":
            await callback.answer("Это решение уже закрыто.", show_alert=True)
            return
        pending.status = "accepted"
        pending.resolved_at = now_local()
        await apply_reward(session, profile, event_type="temptation_after_pause_accepted")
        await session.commit()
    await send_reaction(callback.message, "selin", "soft_smile", "Селин: — Значит, это хотя бы было решение, а не рефлекс.")
    await callback.answer()


@router.callback_query(F.data.startswith("tempt:no_longer:"))
async def temptation_no_longer(callback: CallbackQuery) -> None:
    temptation_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        pending = await session.get(PendingTemptation, temptation_id)
        if not pending or pending.user_id != profile.id or pending.status != "waiting":
            await callback.answer("Это решение уже закрыто.", show_alert=True)
            return
        pending.status = "declined"
        pending.resolved_at = now_local()
        if pending.reward_eligible:
            await apply_reward(
                session, profile, event_type="temptation_after_pause_declined", xp=10, willpower=1, bond=2
            )
        unlocked = await check_achievements(session, profile)
        await session.commit()
    text = "Селин: — Вот это мне уже нравится."
    if pending.reward_eligible:
        text += "\n+10 XP · +1 Воля · +2 связь с Тори"
    await send_reaction(callback.message, "selin", "soft_smile", text)
    for msg in achievement_messages(unlocked):
        await callback.message.answer(msg)
    await callback.answer()


@router.message(F.text == "🚶 Шаги")
async def ask_steps(message: Message, state: FSMContext) -> None:
    await state.set_state(StepState.waiting_steps)
    await send_background(
        message,
        "promenade",
        "Сколько шагов сегодня? Введи число.",
        reply_markup=back_menu("state:cancel", "❌ Отмена"),
    )


@router.message(StepState.waiting_steps)
async def save_steps(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(" ", "").replace(",", "")
    if not raw.isdigit():
        await message.answer("Нужно просто число, например 8742.")
        return
    steps = int(raw)
    if steps > 100_000:
        await message.answer("Селин: — Я отказываюсь верить в это число. Введи нормальное значение.")
        return
    tier = evaluate_steps(steps)
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        stat = await get_or_create_daily(session, profile.id, now_local().date())
        xp_delta = max(0, tier.xp - stat.step_reward_xp)
        coin_delta = max(0, tier.coins - stat.step_reward_coins)
        stat.steps = steps
        stat.step_tier = tier.key
        stat.step_reward_xp = max(stat.step_reward_xp, tier.xp)
        stat.step_reward_coins = max(stat.step_reward_coins, tier.coins)
        if xp_delta or coin_delta:
            await apply_reward(
                session, profile, event_type="steps_update", xp=xp_delta, coins=coin_delta,
                payload={"steps": steps, "tier": tier.key},
            )
        unlocked = await check_achievements(session, profile, steps=steps)
        await session.commit()
    reward = f"\n+{xp_delta} XP · +{coin_delta} монет" if xp_delta or coin_delta else ""
    await send_reaction(
        message,
        "tori",
        step_reaction_emotion(tier.key),
        random.choice(tier.reactions) + reward,
    )
    for msg in achievement_messages(unlocked):
        await message.answer(msg)
    await state.clear()


@router.message(F.text == "📜 Квесты")
async def quests(message: Message) -> None:
    await send_background(message, "study", "Пользовательские квесты:", reply_markup=quest_menu())


@router.callback_query(F.data == "quest:new")
async def quest_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(QuestState.waiting_title)
    await callback.message.answer(
        "Как называется квест?",
        reply_markup=back_menu("state:cancel", "❌ Отмена"),
    )
    await callback.answer()


@router.message(QuestState.waiting_title)
async def quest_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Нужно название квеста.")
        return
    await state.update_data(title=title)
    await state.set_state(QuestState.waiting_difficulty)
    await message.answer(
        "Сложность: лёгкий / обычный / сложный",
        reply_markup=back_menu("state:cancel", "❌ Отмена"),
    )


@router.message(QuestState.waiting_difficulty)
async def quest_difficulty(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().lower()
    mapping = {
        "лёгкий": ("easy", 15, 3), "легкий": ("easy", 15, 3),
        "обычный": ("normal", 30, 6), "сложный": ("hard", 50, 10),
    }
    if raw not in mapping:
        await message.answer("Напиши: лёгкий, обычный или сложный.")
        return
    data = await state.get_data()
    difficulty, xp, coins = mapping[raw]
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        session.add(CustomQuest(
            user_id=profile.id, title=data["title"], difficulty=difficulty,
            reward_xp=xp, reward_coins=coins,
        ))
        await session.commit()
    await send_reaction(message, "tori", "confused", custom_quest_created())
    await state.clear()


@router.callback_query(F.data == "quest:list")
async def quest_list(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        rows = list((await session.scalars(
            select(CustomQuest).where(
                CustomQuest.user_id == profile.id, CustomQuest.status == "active"
            ).order_by(CustomQuest.id.desc())
        )).all())
        await session.commit()
    if not rows:
        await callback.message.answer("Активных пользовательских квестов нет.")
        await callback.answer()
        return
    for quest in rows[:10]:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"quest:done:{quest.id}")
        ]])
        await callback.message.answer(
            f"{quest.title}\n+{quest.reward_xp} XP · +{quest.reward_coins} монет", reply_markup=kb
        )
    await callback.answer()


@router.callback_query(F.data.startswith("quest:done:"))
async def quest_done(callback: CallbackQuery) -> None:
    quest_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, callback.from_user.id, callback.from_user.full_name)
        quest = await session.get(CustomQuest, quest_id)
        if not quest or quest.user_id != profile.id or quest.status != "active":
            await callback.answer("Квест уже закрыт или не найден.", show_alert=True)
            return
        quest.status = "completed"
        quest.completed_at = now_local()
        await apply_reward(
            session, profile, event_type="custom_quest_complete",
            xp=quest.reward_xp, coins=quest.reward_coins,
            payload={"quest_id": quest.id, "title": quest.title},
        )
        unlocked = await check_achievements(session, profile)
        await session.commit()
    await send_reaction(callback.message, "tori", "proud", custom_quest_done() + f"\n+{quest.reward_xp} XP · +{quest.reward_coins} монет")
    for msg in achievement_messages(unlocked):
        await callback.message.answer(msg)
    await callback.answer()


@router.message(F.text == "🎁 Гардероб")
async def wardrobe(message: Message) -> None:
    async with SessionLocal() as session:
        profile = await get_or_create_profile(session, message.from_user.id, message.from_user.full_name)
        await session.commit()
    await send_background(
        message,
        "atelier_wardrobe",
        f"Сундуки испытания: {profile.trial_chests}\nМонеты: {profile.coins}\nПыль ателье: {profile.atelier_dust}",
        reply_markup=wardrobe_menu(profile.trial_chests),
    )


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
