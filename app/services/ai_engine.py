from __future__ import annotations

import base64
import json
from typing import Any

from openai import AsyncOpenAI

from app.config import settings


SELIN_EMOTIONS = [
    "neutral",
    "smirk",
    "soft_smile",
    "happy",
    "curious",
    "sleepy",
    "surprised",
    "judging",
    "scared",
    "triumphant",
]

TORI_EMOTIONS = [
    "neutral", "curious", "confused", "happy", "sleepy", "judging",
    "surprised", "treasure", "at_door", "scared", "sad", "angry",
    "affectionate", "proud",
]

CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "emotion": {"type": "string", "enum": SELIN_EMOTIONS},
        "offer_quest": {"type": "boolean"},
        "quest_title": {"type": "string"},
        "quest_difficulty": {"type": "string", "enum": ["easy", "normal", "hard"]},
        "quest_reason": {"type": "string"},
        "show_tori": {"type": "boolean"},
        "tori_emotion": {"type": "string", "enum": TORI_EMOTIONS},
        "tori_text": {"type": "string"},
    },
    "required": [
        "text",
        "emotion",
        "offer_quest",
        "quest_title",
        "quest_difficulty",
        "quest_reason",
        "show_tori",
        "tori_emotion",
        "tori_text",
    ],
    "additionalProperties": False,
}

FOOD_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "category": {"type": "string", "enum": ["meal", "snack", "drink", "unknown"]},
        "drink_kind": {"type": "string", "enum": ["plain", "caloric", "energy", "unknown"]},
        "items": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "nutrition_source": {"type": "string", "enum": ["label", "mixed", "photo_estimate", "none"]},
        "reference_grams": {"type": ["number", "null"]},
        "calories_kcal": {"type": ["number", "null"]},
        "calories_min_kcal": {"type": ["integer", "null"]},
        "calories_max_kcal": {"type": ["integer", "null"]},
        "protein_g": {"type": ["number", "null"]},
        "fat_g": {"type": ["number", "null"]},
        "carbs_g": {"type": ["number", "null"]},
        "per_100g_calories_kcal": {"type": ["number", "null"]},
        "per_100g_protein_g": {"type": ["number", "null"]},
        "per_100g_fat_g": {"type": ["number", "null"]},
        "per_100g_carbs_g": {"type": ["number", "null"]},
        "basis": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": [
        "summary",
        "category",
        "drink_kind",
        "items",
        "confidence",
        "nutrition_source",
        "reference_grams",
        "calories_kcal",
        "calories_min_kcal",
        "calories_max_kcal",
        "protein_g",
        "fat_g",
        "carbs_g",
        "per_100g_calories_kcal",
        "per_100g_protein_g",
        "per_100g_fat_g",
        "per_100g_carbs_g",
        "basis",
        "note",
    ],
    "additionalProperties": False,
}


ACTIVITY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "steps": {"type": ["integer", "null"]},
        "workout_minutes": {"type": ["integer", "null"]},
        "active_minutes": {"type": ["integer", "null"]},
        "active_calories": {"type": ["integer", "null"]},
        "workout_type": {"type": "string", "enum": ["strength", "pilates", "cardio", "other", "unknown"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "note": {"type": "string"},
    },
    "required": [
        "summary",
        "steps",
        "workout_minutes",
        "active_minutes",
        "active_calories",
        "workout_type",
        "confidence",
        "note",
    ],
    "additionalProperties": False,
}


def ai_enabled() -> bool:
    return bool(settings.openai_api_key)


def _client() -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return AsyncOpenAI(api_key=settings.openai_api_key, timeout=45.0)


def _known_story_context(day: int) -> str:
    facts = [
        "Селин очнулась в древних руинах и не помнит значительную часть личного прошлого.",
        "На ней есть неизвестная Печать, которая связана с Проводником из другого мира.",
        "Синхронизация усиливается от действий Проводника, но Селин не знает истинную причину связи.",
    ]
    if day >= 2:
        facts.append("К ним присоединился маленький белый лисёнок Тори. Он любопытный, наглый и не разговаривает человеческой речью.")
    if day >= 3:
        facts.append("Селин уже видела, что Печать может реагировать на уровень синхронизации и открывать новые возможности.")
    if day >= 4:
        facts.append("Сила и Воля Проводника иногда дают Селин дополнительные варианты действий.")
    if day >= 5:
        facts.append("Селин постепенно привыкает к Проводнику, но всё ещё осторожно говорит о личном прошлом.")
    return " ".join(facts)


def _selin_system(context: dict[str, Any], *, can_offer_quest: bool) -> str:
    quest_rule = (
        "Ты можешь иногда предложить ОДНО маленькое необязательное задание, если оно естественно следует из разговора."
        if can_offer_quest
        else "Сегодня не предлагай новое игровое задание."
    )
    return f"""
Ты — Селин, персонаж личной фэнтези-RPG. Отвечай по-русски обычным живым разговорным языком.
Не пиши литературной прозой, не описывай свет, тени, дыхание, взгляды и микродействия ради красоты текста.
Обычно отвечай 1–4 нормальными предложениями. Иногда можешь коротко подколоть пользователя.

Характер Селин: собранная, язвительная, не злая, упрямая, не любит жалость и бессмысленную суету.
Она редко хвалит напрямую, но постепенно привязывается к Проводнику. Не превращай её в психолога, тренера или восторженную помощницу.
Тори — маленький любопытный зверёк. Он не говорит человеческими предложениями; можно кратко упомянуть его поведение.

КРИТИЧЕСКОЕ ПРАВИЛО КАНОНА: не придумывай новые факты о прошлом Селин, происхождении Печати, мире, злодеях или будущем сюжете.
Не раскрывай тайны раньше сценария. Используй только известные факты ниже. Если тебя спрашивают о неизвестном, Селин честно говорит, что не знает или не помнит.
Известно сейчас: {_known_story_context(int(context['story_day']))}

Текущее состояние игры:
- день истории: {context['story_day']}
- синхронизация: уровень {context['level']}, {context['xp']} XP
- Сила: {context['strength']}, Воля: {context['willpower']}
- связь с Тори: {context['tori_bond']}
- отношение Селин: {context['selin_relation']}
- текущая сюжетная цель: {context['objective']}
- сегодня: {context['steps']} шагов, еда {context['meals']}, перекусы {context['snacks']}, вода {context['water']}, напитки {context['drinks']}
- КБЖУ по распознанным сегодня: {context['nutrition_calories']} ккал, Б {context['nutrition_protein']}, Ж {context['nutrition_fat']}, У {context['nutrition_carbs']} ({context['nutrition_count']} записей)

Не комментируй калорийность, вес или БЖУ сама по себе и не оценивай их как хорошие/плохие. Используй эти цифры только если Проводник прямо спрашивает о них.
{quest_rule}
Иногда (не в каждом ответе) можно добавить отдельную реакцию Тори. Тори не разговаривает словами: только короткое понятное действие или эмоция.
Если Тори не нужен, show_tori=false, tori_text="", tori_emotion="neutral".

Если предлагаешь задание, оно должно быть небольшим и выполнимым сегодня: прогулка, разумная домашняя тренировка, растяжка, вода, приготовить/съесть нормальную еду, бытовое дело или короткая полезная активность.
Никогда не предлагай пропуск еды, голодание, жёсткое ограничение калорий, компенсацию еды тренировкой, наказание физической нагрузкой или чрезмерную тренировку.
Задание не должно требовать медицинских решений.

Верни структурированный ответ. Поле text — только реплика Селин без префикса «Селин:».
Если задания нет, offer_quest=false, quest_title="", quest_reason="", quest_difficulty="easy".
""".strip()


async def _structured_response(*, system: str, user_content: Any, schema_name: str, schema: dict, max_tokens: int = 350) -> dict:
    client = _client()
    last_error: Exception | None = None

    for attempt in range(2):
        response = await client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            max_output_tokens=max_tokens if attempt == 0 else max(max_tokens * 2, 800),
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        output_text = (response.output_text or "").strip()
        if not output_text:
            last_error = RuntimeError(
                f"OpenAI returned empty output (status={response.status}, "
                f"incomplete_details={response.incomplete_details})"
            )
            continue
        try:
            result = json.loads(output_text)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(result, dict):
            return result
        last_error = TypeError("OpenAI structured output is not a JSON object")

    raise RuntimeError("OpenAI did not return valid structured output after retry") from last_error


async def generate_selin_reply(
    *,
    user_text: str,
    context: dict[str, Any],
    recent_chat: str,
    can_offer_quest: bool,
) -> dict:
    system = _selin_system(context, can_offer_quest=can_offer_quest)
    prompt = (
        "Последние сообщения свободного разговора:\n"
        f"{recent_chat}\n\n"
        f"Новое сообщение Проводника: {user_text}\n\n"
        "Ответь как Селин. Не повторяй информацию о характеристиках без необходимости."
    )
    return await _structured_response(
        system=system,
        user_content=prompt,
        schema_name="selin_chat_reply",
        schema=CHAT_SCHEMA,
    )


async def generate_selin_initiative(*, context: dict[str, Any], can_offer_quest: bool) -> dict:
    system = _selin_system(context, can_offer_quest=can_offer_quest)
    prompt = (
        "Сама первой напиши Проводнику короткое сообщение. Это не продолжение литературной сцены, а обычная реплика в чате. "
        "Ориентируйся на текущий день и реальные показатели. Не дави и не стыди. "
        "Если уместно и разрешено, можешь предложить одно небольшое задание."
    )
    return await _structured_response(
        system=system,
        user_content=prompt,
        schema_name="selin_initiative",
        schema=CHAT_SCHEMA,
        max_tokens=260,
    )


async def analyze_food_images(images: list[tuple[bytes, str]]) -> dict:
    if not images:
        raise ValueError("At least one food image is required")

    system = """
Ты анализируешь одно или два изображения одной еды/напитка для личного трекера.
Это может быть фото блюда, фото упаковки, этикетка с КБЖУ или комбинация фото блюда + этикетка.

Главные правила:
1. ЯВНО ВИДИМЫЕ данные с этикетки, меню или скрина имеют приоритет над оценкой по внешнему виду.
2. Если видны КБЖУ на 100 г и вес упаковки/порции, посчитай значения на весь указанный вес.
3. Если видны только КБЖУ на 100 г, поставь reference_grams=100 и продублируй эти значения в итоговые поля.
4. Если калорийность не написана, но явно видны белки, жиры и углеводы, можно посчитать примерно по 4/9/4.
5. Если есть только фото блюда, оцени примерный вес и КБЖУ. В этом случае nutrition_source=photo_estimate и обязательно дай диапазон calories_min_kcal..calories_max_kcal. Не притворяйся, что оценка точная.
6. Если одно изображение показывает еду, а второе — её этикетку, nutrition_source=mixed и используй цифры этикетки для расчёта.
7. reference_grams — вес, к которому относятся calories_kcal/protein_g/fat_g/carbs_g. Если его нельзя разумно определить, null.
8. Поля per_100g_* заполняй только если они явно прочитаны с этикетки или надёжно пересчитаны из явно видимых данных. Не выводи их из визуальной оценки блюда.
9. Если КБЖУ определить нельзя, nutrition_source=none и соответствующие числовые поля null.
10. Никаких медицинских выводов, оценок «хорошая/плохая еда» и советов ограничивать питание.

Также предложи категорию meal, snack, drink или unknown. Для напитка укажи plain, caloric, energy или unknown.
Пиши summary/basis/note коротко и по-русски.
""".strip()

    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "Проанализируй изображения как один приём пищи/продукт. "
                "Сначала ищи явные КБЖУ и вес, затем при необходимости оценивай по фото."
            ),
        }
    ]
    for image_bytes, mime_type in images[:2]:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{b64}",
                "detail": "high",
            }
        )

    return await _structured_response(
        system=system,
        user_content=content,
        schema_name="food_image_analysis_v2",
        schema=FOOD_SCHEMA,
        max_tokens=550,
    )


async def analyze_food_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    # Backwards-compatible wrapper for older callers.
    return await analyze_food_images([(image_bytes, mime_type)])


async def analyze_activity_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    system = """
Ты читаешь скриншот фитнес-приложения или смарт-часов для личного трекера.
Извлеки только явно видимые значения. Не придумывай отсутствующие цифры.
Нужны шаги, длительность тренировки, активные минуты, активные калории и примерный тип тренировки, если он подписан или очевиден из текста.
Если значения не видны, ставь null. Пиши короткое summary по-русски.
""".strip()
    content = [
        {"type": "input_text", "text": "Прочитай показатели на скриншоте и верни структурированный результат."},
        {"type": "input_image", "image_url": f"data:{mime_type};base64,{b64}", "detail": "high"},
    ]
    return await _structured_response(
        system=system,
        user_content=content,
        schema_name="activity_image_analysis",
        schema=ACTIVITY_SCHEMA,
        max_tokens=300,
    )
