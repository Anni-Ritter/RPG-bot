from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto, Message

ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets"

SELIN_ASSETS = {
    "neutral": "selin/neutral.png",
    "smirk": "selin/smirk.png",
    "soft_smile": "selin/soft_smile.png",
    "happy": "selin/happy.png",
    "curious": "selin/curious.png",
    "thoughtful": "selin/curious.png",
    "sleepy": "selin/sleepy.png",
    "surprised": "selin/surprised.png",
    "judging": "selin/judging.png",
    "skeptical": "selin/judging.png",
    "annoyed": "selin/judging.png",
    "beckoning": "selin/beckoning.png",
    "beckon": "selin/beckoning.png",
    "anxious": "selin/scared.png",
    "scared": "selin/scared.png",
    "triumphant": "selin/triumphant.png",
    "victory": "selin/triumphant.png",
}

TORI_ASSETS = {
    "neutral": "tori/neutral.png",
    "curious": "tori/curious.png",
    "confused": "tori/confused.png",
    "happy": "tori/happy.png",
    "sleepy": "tori/sleepy.png",
    "judging": "tori/judging.png",
    "surprised": "tori/surprised.png",
    "treasure": "tori/treasure.png",
    "at_door": "tori/at_door.png",
    "beckoning": "tori/at_door.png",
    "beckon": "tori/at_door.png",
    "scared": "tori/scared.png",
    "sad": "tori/sad.png",
    "angry": "tori/angry.png",
    "affectionate": "tori/affectionate.png",
    "proud": "tori/proud.png",
}

BACKGROUND_ASSETS = {
    "atelier_default": "backgrounds/atelier_main.png",
    "main_atelier": "backgrounds/atelier_main.png",
    "atelier_wardrobe": "backgrounds/atelier_wardrobe.png",
    "wardrobe": "backgrounds/atelier_wardrobe.png",
    "wardrobe_atelier": "backgrounds/atelier_wardrobe.png",
    "archive": "backgrounds/archive.png",
    "training": "backgrounds/training_hall.png",
    "training_hall": "backgrounds/training_hall.png",
    "promenade": "backgrounds/walk_evening.png",
    "walk_evening": "backgrounds/walk_evening.png",
    "study": "backgrounds/work_study.png",
    "work_study": "backgrounds/work_study.png",
    "food_nook": "backgrounds/food_nook.png",
    "rest": "backgrounds/rest_room.png",
    "bedroom": "backgrounds/rest_room.png",
    "rest_room": "backgrounds/rest_room.png",
    "seal": "backgrounds/seal_chamber.png",
    "seal_chamber": "backgrounds/seal_chamber.png",
    "ruins_temple": "backgrounds/story_ruins.png",
    "story_ruins": "backgrounds/story_ruins.png",
    "forest_temptation": "backgrounds/story_forest.png",
    "story_forest": "backgrounds/story_forest.png",
    "devourer_lair": "backgrounds/story_devourer.png",
    "story_devourer": "backgrounds/story_devourer.png",
    "tarren_city": "backgrounds/story_tarren.png",
    "story_tarren": "backgrounds/story_tarren.png",
    "road_to_spire": "backgrounds/story_spire_road.png",
    "story_spire_road": "backgrounds/story_spire_road.png",
    "spire_gate": "backgrounds/story_spire_gate.png",
    "story_spire_gate": "backgrounds/story_spire_gate.png",
}


def _asset_path(relative: str) -> Path:
    return ASSET_ROOT / relative


def _reaction_relative(character: str, emotion: str) -> str | None:
    if character == "selin":
        return SELIN_ASSETS.get(emotion)
    if character == "tori":
        return TORI_ASSETS.get(emotion)
    return None


async def send_reaction(
    message: Message,
    character: str,
    emotion: str,
    caption: str,
    *,
    reply_markup=None,
) -> None:
    relative = _reaction_relative(character, emotion)
    if not relative:
        await message.answer(caption, reply_markup=reply_markup)
        return

    path = _asset_path(relative)
    if not path.exists():
        await message.answer(caption, reply_markup=reply_markup)
        return

    # Telegram photo captions are shorter than normal messages, so long text is split safely.
    if len(caption) <= 900:
        await message.answer_photo(FSInputFile(path), caption=caption, reply_markup=reply_markup)
    else:
        await message.answer_photo(FSInputFile(path))
        await message.answer(caption, reply_markup=reply_markup)


async def send_reaction_to_chat(
    bot: Bot,
    chat_id: int,
    character: str,
    emotion: str,
    caption: str,
    *,
    reply_markup=None,
) -> None:
    relative = _reaction_relative(character, emotion)
    if not relative:
        await bot.send_message(chat_id, caption, reply_markup=reply_markup)
        return

    path = _asset_path(relative)
    if not path.exists():
        await bot.send_message(chat_id, caption, reply_markup=reply_markup)
        return

    if len(caption) <= 900:
        await bot.send_photo(chat_id, FSInputFile(path), caption=caption, reply_markup=reply_markup)
    else:
        await bot.send_photo(chat_id, FSInputFile(path))
        await bot.send_message(chat_id, caption, reply_markup=reply_markup)


async def send_background(
    message: Message,
    name: str,
    caption: str,
    *,
    reply_markup=None,
) -> None:
    relative = BACKGROUND_ASSETS.get(name)
    if not relative:
        await message.answer(caption, reply_markup=reply_markup)
        return

    path = _asset_path(relative)
    if not path.exists():
        await message.answer(caption, reply_markup=reply_markup)
        return

    if len(caption) <= 900:
        await message.answer_photo(FSInputFile(path), caption=caption, reply_markup=reply_markup)
    else:
        await message.answer_photo(FSInputFile(path))
        await message.answer(caption, reply_markup=reply_markup)


async def replace_text_view(message: Message, text: str, *, reply_markup=None) -> Message | None:
    """Replace a bot UI message in place whenever Telegram allows it.

    Inline navigation should feel like one screen instead of leaving a trail of
    menu messages. Media messages keep their current image and update caption;
    plain messages update text. If Telegram cannot edit the old message, the
    helper removes it and sends a replacement.
    """
    try:
        if message.photo:
            if len(text) <= 1000:
                return await message.edit_caption(caption=text, reply_markup=reply_markup)
            await message.delete()
            return await message.answer(text, reply_markup=reply_markup)
        return await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return message
    except Exception as exc:
        print("UI text replace error:", repr(exc))

    try:
        await message.delete()
    except Exception:
        pass
    return await message.answer(text, reply_markup=reply_markup)


async def replace_photo_view(message: Message, photo, caption: str, *, reply_markup=None) -> Message | None:
    """Replace the current UI screen with a photo/caption screen."""
    if len(caption) > 1000:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer_photo(photo)
        return await message.answer(caption, reply_markup=reply_markup)

    try:
        if message.photo:
            media = InputMediaPhoto(media=photo, caption=caption)
            return await message.edit_media(media=media, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            try:
                await message.edit_reply_markup(reply_markup=reply_markup)
            except Exception:
                pass
            return message
    except Exception as exc:
        print("UI photo replace error:", repr(exc))

    try:
        await message.delete()
    except Exception:
        pass
    return await message.answer_photo(photo, caption=caption, reply_markup=reply_markup)


async def replace_background(
    message: Message,
    name: str,
    caption: str,
    *,
    reply_markup=None,
) -> Message | None:
    relative = BACKGROUND_ASSETS.get(name)
    if not relative:
        return await replace_text_view(message, caption, reply_markup=reply_markup)
    path = _asset_path(relative)
    if not path.exists():
        return await replace_text_view(message, caption, reply_markup=reply_markup)
    return await replace_photo_view(message, FSInputFile(path), caption, reply_markup=reply_markup)


async def replace_reaction(
    message: Message,
    character: str,
    emotion: str,
    caption: str,
    *,
    reply_markup=None,
) -> Message | None:
    relative = _reaction_relative(character, emotion)
    if not relative:
        return await replace_text_view(message, caption, reply_markup=reply_markup)
    path = _asset_path(relative)
    if not path.exists():
        return await replace_text_view(message, caption, reply_markup=reply_markup)
    return await replace_photo_view(message, FSInputFile(path), caption, reply_markup=reply_markup)


# Aliases for possible future handlers.
send_character = send_reaction
send_character_bot = send_reaction_to_chat


def step_reaction_emotion(tier_key: str) -> str:
    return {
        "very_low": "sleepy",
        "low": "judging",
        "ok": "neutral",
        "good": "happy",
        "great": "proud",
        "high": "surprised",
        "very_high": "surprised",
        "what": "confused",
        "where_are_you_going": "confused",
    }.get(tier_key, "neutral")


def story_background_key(day: int) -> str:
    if day <= 7:
        return "ruins_temple"
    if day <= 12:
        return "forest_temptation"
    if day <= 14:
        return "devourer_lair"
    if day <= 21:
        return "tarren_city"
    if day <= 25:
        return "road_to_spire"
    if day <= 27:
        return "spire_gate"
    return "seal_chamber"


def story_background(day: int, chapter: int | None = None) -> str:
    return story_background_key(day)
