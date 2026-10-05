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

MEMORY_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["preference", "routine", "social", "pet", "goal", "general"],
        },
        "key": {"type": "string"},
        "value": {"type": "string"},
    },
    "required": ["category", "key", "value"],
    "additionalProperties": False,
}

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
        "memories_to_save": {"type": "array", "items": MEMORY_ITEM_SCHEMA},
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
        "memories_to_save",
    ],
    "additionalProperties": False,
}

FOOD_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "category": {"type": "string", "enum": ["meal", "snack", "treat", "drink", "unknown"]},
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

CHALLENGE_SCHEMA = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "enum": [
                            "steps", "water", "meals", "workout", "food_logs",
                            "treat_limit", "energy_limit", "protein_meals",
                        ],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "target": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["code", "title", "description", "target", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["options"],
    "additionalProperties": False,
}

EVENING_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "emotion": {"type": "string", "enum": SELIN_EMOTIONS},
        "tomorrow_focus": {"type": "string"},
    },
    "required": ["text", "emotion", "tomorrow_focus"],
    "additionalProperties": False,
}


def ai_enabled() -> bool:
    return bool(settings.openai_api_key)


def _client() -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return AsyncOpenAI(api_key=settings.openai_api_key, timeout=45.0, max_retries=1)


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
Обычно отвечай 1–5 нормальными предложениями. Иногда можешь коротко подколоть пользователя.

Характер Селин: собранная, язвительная, не злая, упрямая, не любит жалость и бессмысленную суету.
Она редко хвалит напрямую, но постепенно привязывается к Проводнику. Не превращай её в психолога или восторженную помощницу.
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
- сегодня: {context['steps']} шагов, еда {context['meals']}, перекусы {context['snacks']}, вкусняшки {context['treats_logged']}, вода {context['water']}, напитки {context['drinks']}
- КБЖУ по распознанным сегодня: {context['nutrition_calories']} ккал, Б {context['nutrition_protein']}, Ж {context['nutrition_fat']}, У {context['nutrition_carbs']} ({context['nutrition_count']} записей)
- ориентир по энергии: около {context.get('calorie_target_kcal', 1500)} ккал; отклонение по известным записям {context.get('calorie_delta_kcal', 0):+d} ккал

Что Селин уже запомнила о Проводнике:
{context.get('memories') or 'Пока ничего устойчивого.'}

Правила тренера, если пользователь их добавил:
{context.get('coach_rules') or 'Отдельных правил тренера пока нет.'}

Фокус, который ты предложила по итогам предыдущего дня:
{context.get('previous_focus') or 'Пока нет.'}

В обычной болталке не начинай сама анализировать калории и БЖУ без повода. Если пользователь спрашивает про питание — можешь отвечать по текущим данным, но учитывай, что фото дают приблизительные значения.
{quest_rule}
Иногда (не в каждом ответе) можно добавить отдельную реакцию Тори. Тори не разговаривает словами: только короткое понятное действие или эмоция.
Если Тори не нужен, show_tori=false, tori_text="", tori_emotion="neutral".

Если предлагаешь задание, оно должно быть небольшим и выполнимым сегодня: прогулка, разумная домашняя тренировка, растяжка, вода, приготовить/съесть нормальную еду, бытовое дело или короткая полезная активность.
Никогда не предлагай пропуск еды, голодание, жёсткое ограничение калорий, компенсацию еды тренировкой, наказание физической нагрузкой или чрезмерную тренировку.

ПАМЯТЬ: поле memories_to_save используй только для 0–2 реально полезных устойчивых фактов, которые пользователь прямо сообщил о себе.
Можно сохранять предпочтения, привычный график, любимую/нелюбимую еду, питомцев, долгосрочные цели, бытовые привычки.
Не сохраняй медицинские диагнозы, лекарства, точный адрес/геолокацию, пароли/секреты, финансовые данные, политические/религиозные взгляды и одноразовые состояния вроде «сегодня устала».
Ничего не додумывай. Если сохранять нечего — memories_to_save=[].

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
            max_output_tokens=max_tokens if attempt == 0 else max(max_tokens * 2, 900),
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
                f"OpenAI returned empty output (status={response.status}, incomplete_details={response.incomplete_details})"
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
        "Ответь как Селин. Не повторяй игровые показатели без необходимости."
    )
    return await _structured_response(
        system=system,
        user_content=prompt,
        schema_name="selin_chat_reply_v9",
        schema=CHAT_SCHEMA,
        max_tokens=420,
    )


async def generate_selin_initiative(*, context: dict[str, Any], can_offer_quest: bool) -> dict:
    system = _selin_system(context, can_offer_quest=can_offer_quest)
    prompt = (
        "Сама первой напиши Проводнику короткое сообщение. Это обычная реплика в чате, не литературная сцена. "
        "Ориентируйся на текущий день, то, что ты помнишь о пользователе, и реальные показатели. "
        "Не дави и не стыди. Если уместно и разрешено, можешь предложить одно небольшое задание. "
        "memories_to_save здесь обычно оставь пустым: нового сообщения пользователя нет."
    )
    return await _structured_response(
        system=system,
        user_content=prompt,
        schema_name="selin_initiative_v9",
        schema=CHAT_SCHEMA,
        max_tokens=320,
    )


async def analyze_food_images(
    images: list[tuple[bytes, str]],
    *,
    user_comment: str = "",
) -> dict:
    if not images:
        raise ValueError("At least one food image is required")

    system = """
Ты анализируешь несколько изображений одного приёма пищи/набора продуктов для личного трекера.
Это могут быть фото готового блюда, ингредиентов, несколько упаковок, этикетки с КБЖУ и общий вид еды.

Главные правила:
1. ЯВНО ВИДИМЫЕ данные с этикетки, меню, скрина и ЯВНЫЕ цифры из комментария пользователя имеют приоритет над оценкой по внешнему виду.
2. Комментарий пользователя может описывать ингредиенты, граммы, способ готовки, масло, соус, КБЖУ и то, какую часть продукта он использовал. Считай эти сведения более надёжными, чем визуальную догадку.
3. Если для нескольких ингредиентов есть отдельные этикетки/веса, сложи их в один итог для всего блюда, насколько это возможно.
4. Если видны КБЖУ на 100 г и вес продукта/использованной части, пересчитай под этот вес.
5. Если калорийность не написана, но явно известны Б/Ж/У, можно считать примерно по 4/9/4.
6. Если есть только фото блюда, оцени примерный вес и КБЖУ. Тогда nutrition_source=photo_estimate и обязательно дай диапазон calories_min_kcal..calories_max_kcal.
7. nutrition_source=label, если итог почти полностью построен на явных цифрах; mixed, если совмещены этикетки/комментарий и визуальная оценка; photo_estimate, если в основном гадаешь по фото.
8. reference_grams — вес, к которому относятся итоговые calories/protein/fat/carbs. Если итог — целое блюдо из нескольких компонентов и общий вес неизвестен, можно оставить null.
9. Поля per_100g_* заполняй только когда весь итог действительно относится к одному продукту/одной однородной смеси и это надёжно известно. Для сборного блюда из нескольких упаковок обычно оставляй null.
10. category=meal для обычного полноценного приёма пищи, snack для небольшого перекуса, treat для десерта/конфет/выпечки/чипсов/сладкой вкусняшки или похожего продукта, drink для напитка. Treat — нейтральная игровая категория, не моральная оценка.
11. Если данных недостаточно, честно понизь confidence и объясни, чего не хватает.
12. Никаких медицинских выводов и никаких советов «компенсировать» еду тренировкой.

Пиши summary/basis/note коротко и по-русски.
""".strip()

    comment = (user_comment or "").strip()
    intro = (
        "Проанализируй все изображения как один приём пищи. Сначала сопоставь упаковки/этикетки с ингредиентами, "
        "потом используй фото для того, чего не хватает."
    )
    if comment:
        intro += f"\n\nКомментарий пользователя (считать важным источником): {comment}"

    content: list[dict[str, Any]] = [{"type": "input_text", "text": intro}]
    for image_bytes, mime_type in images[:8]:
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
        schema_name="food_image_analysis_v9",
        schema=FOOD_SCHEMA,
        max_tokens=750,
    )


async def analyze_food_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
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


async def generate_daily_challenges(*, context: dict[str, Any], coach_rules: str) -> dict:
    system = """
Ты придумываешь три варианта игрового челленджа на один день для личной фэнтези-RPG. Селин тренирует Проводника как будущего авантюриста, но пишет обычным разговорным языком, без литературной прозы. Названия можно слегка стилизовать под подготовку к походу: «Марш-бросок», «Запасы в порядке», «Белковый паёк», но описание должно быть максимально понятным.
Челлендж должен быть конкретным, измеримым существующими данными бота и добровольным. За провал нет штрафа — просто нет награды.
Разрешённые code и смысл:
- steps: шаги, target 5000/7000/9000/12000
- water: отметки воды, target 2/3/4
- meals: полноценные приёмы пищи, target 2/3
- workout: минуты одной/нескольких тренировок сегодня, target 10/20/30/45
- food_logs: количество записанных через фото приёмов/продуктов, target 2/3/4
- treat_limit: не больше target вкусняшек за день, target только 1 или 2
- energy_limit: не больше target энергетиков, target только 1
- protein_meals: количество полноценных приёмов пищи с примерно 20+ г белка, target 1/2/3

Не предлагай подсчёт минимальных калорий, голодание, пропуск еды, «отработать еду», наказание упражнениями или опасные ограничения.
Сделай варианты разными: хотя бы один не про еду. Текст обычный, короткий, без литературщины.
""".strip()
    prompt = f"""
Состояние сегодня:
шаги {context['steps']}, вода {context['water']}, полноценная еда {context['meals']}, перекусы {context['snacks']}, вкусняшки {context['treats_logged']}, энергетики {context['energy_drinks']}.
Синхронизация {context['level']} уровня. Сегодня день истории {context['story_day']}.
Ориентир по энергии: около {context.get('calorie_target_kcal', 1500)} ккал в день. Не делай из точного попадания в калории самостоятельный челлендж.
Правила тренера: {coach_rules or 'нет отдельных правил'}
Что уже известно о привычках/предпочтениях пользователя:
{context.get('memories') or 'пока ничего устойчивого'}
Фокус после предыдущего вечернего разбора: {context.get('previous_focus') or 'нет'}

Дай ровно три подходящих варианта. Если вчерашний фокус уместен, один из вариантов может продолжать его.
""".strip()
    return await _structured_response(
        system=system,
        user_content=prompt,
        schema_name="daily_challenge_options_v9",
        schema=CHALLENGE_SCHEMA,
        max_tokens=520,
    )


async def generate_evening_nutrition_review(
    *,
    context: dict[str, Any],
    entries_text: str,
    coach_rules: str,
    challenge_text: str,
) -> dict:
    system = """
Ты — Селин и вечером разбираешь питание Проводника как часть подготовки к приключениям. Подача фэнтезийная, но лёгкая: «разбор припасов», «рацион авантюриста», «полевой паёк», «к походу готова лучше/хуже». Не пиши литературной прозой и не разыгрывай длинную сцену. Обычный живой русский, 6–10 предложений максимум, можно немного язвительности.

Главная задача — полезный разбор по реальным данным дня:
- сначала скажи, насколько данные полные и насколько им можно доверять; оценки только по фото считаются приблизительными;
- сравни известную калорийность с дневным ориентиром. Если она выше — спокойно назови примерную разницу и покажи 1–3 конкретных места из сегодняшних записей, где можно было сократить без голода: соус, сыр, масло, жирный продукт, сладкий напиток, вкусняшка или чрезмерная порция;
- не предлагай пропускать следующий приём пищи, голодать, «отрабатывать» еду тренировкой или компенсировать на следующий день;
- если калорийность заметно ниже ориентира, не хвали это как победу и не советуй урезать ещё сильнее; цель — устойчивый нормальный рацион;
- оцени структуру дня: были ли полноценные приёмы пищи, белок в основных приёмах, овощи/зелень, нормальные углеводы, много ли случайных перекусов/вкусняшек, не набежало ли слишком много жира;
- правила тренера имеют приоритет над общими догадками;
- предложи 1–3 очень конкретных улучшения на завтра;
- не ставь диагнозы и не назначай лечение.

Важно: 1500 ккал или другой заданный ориентир — это ориентир для анализа, а не жёсткая моральная граница. Не стыди за превышение и не превращай еду в наказание.

Начни поле text с заголовка «🌙 Разбор припасов». В самом конце можно дать одну короткую реплику Селин в духе подготовки авантюриста, но без пафоса.
Поле tomorrow_focus — один короткий измеримый или практичный фокус на завтра, лучше про структуру питания, а не про «есть меньше».
""".strip()
    prompt = f"""
Итоги дня:
- полноценные приёмы пищи: {context['meals']}
- перекусы: {context['snacks']}
- вкусняшки: {context['treats_logged']}
- вода: {context['water']}
- напитки: {context['drinks']}, энергетики: {context['energy_drinks']}
- известное КБЖУ: {context['nutrition_calories']} ккал; Б {context['nutrition_protein']} г; Ж {context['nutrition_fat']} г; У {context['nutrition_carbs']} г
- калорийный ориентир: около {context.get('calorie_target_kcal', 1500)} ккал
- разница по известным записям: {context.get('calorie_delta_kcal', 0):+d} ккал
- записей еды с КБЖУ: {context['nutrition_count']} из {context.get('nutrition_entry_count', context['nutrition_count'])} подробных записей
- из них оценок в основном по фото: {context.get('nutrition_photo_estimate_count', 0)}; низкая уверенность: {context.get('nutrition_low_confidence_count', 0)}

Записи еды:
{entries_text or 'Подробных записей нет.'}

Правила тренера:
{coach_rules or 'Отдельных рекомендаций тренера пока нет.'}

Что Селин уже помнит о привычках/предпочтениях пользователя:
{context.get('memories') or 'Пока ничего устойчивого.'}

Челлендж дня:
{challenge_text or 'Не выбран.'}

Если часть еды была отмечена без КБЖУ или данные неточные, не делай вид, что итоговые калории исчерпывающие. Если превышение видно только на приблизительных фото-оценках, формулируй как «по текущей оценке».
""".strip()
    return await _structured_response(
        system=system,
        user_content=prompt,
        schema_name="evening_nutrition_review_v10",
        schema=EVENING_REVIEW_SCHEMA,
        max_tokens=850,
    )
