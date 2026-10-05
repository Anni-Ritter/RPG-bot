
from __future__ import annotations

import json
import random

from app.services.content import game_data_path

DIALOGUE_PATH = game_data_path("dialogue.json")
_DATA = json.loads(DIALOGUE_PATH.read_text(encoding="utf-8"))


def pick_dialogue(category: str, chapter: int | None = None) -> str:
    value = _DATA[category]
    if isinstance(value, dict):
        if chapter is None:
            chapter = 1
        pool = value[str(max(1, min(chapter, 4)))]
    else:
        pool = value
    return random.choice(pool)


_TORI_AUTONOMOUS_EVENTS = [
    {
        "id": "found_treasure",
        "emotion": "treasure",
        "text": {
            "day": "Тори возникает рядом с очень довольным видом и кладёт перед тобой свою сегодняшнюю «добычу»: {find}.",
            "evening": "Под вечер Тори торжественно приносит {find} и явно ждёт оценки находки.",
        },
        "choices": [
            {
                "id": "inspect",
                "label": "🔎 Рассмотреть находку",
                "emotion": "proud",
                "text": "Тори позволяет осмотреть добычу ровно три секунды, после чего прижимает её лапами. Показ состоялся, право собственности не обсуждается.",
            },
            {
                "id": "praise",
                "label": "✨ Похвалить Тори",
                "emotion": "happy",
                "text": "Тори распушается так, будто только что спас весь отряд. На сегодня этого признания ему явно достаточно.",
            },
        ],
    },
    {
        "id": "demands_attention",
        "emotion": "at_door",
        "text": {
            "day": "Тори появляется посреди всех дел, садится прямо на пути и демонстративно смотрит на тебя. У него явно есть требование, но формулировать его словами он не собирается.",
            "evening": "Тори решает, что на сегодня дел было достаточно, и укладывается поперёк дороги с совершенно недвусмысленным видом.",
        },
        "choices": [
            {
                "id": "pet",
                "label": "🤍 Погладить",
                "emotion": "affectionate",
                "text": "Тори великодушно принимает внимание, устраивается удобнее и делает вид, что именно так всё и было запланировано.",
            },
            {
                "id": "negotiate",
                "label": "🗨 Спросить, чего он хочет",
                "emotion": "curious",
                "text": "Тори поворачивает голову к ближайшему выходу, потом снова к тебе. Похоже, вопрос был риторическим: он хочет идти смотреть, что там интересного.",
            },
        ],
    },
    {
        "id": "stolen_nest",
        "emotion": "judging",
        "text": {
            "day": "Тори устроил маленькое логово из вещей, которые точно не лежали там раньше. Среди них обнаруживается {find}.",
            "evening": "Перед сном выясняется, что Тори собрал себе новое гнездо. Часть материалов подозрительно похожа на ваши вещи, а в центре лежит {find}.",
        },
        "choices": [
            {
                "id": "tidy",
                "label": "🧺 Вернуть вещи",
                "emotion": "judging",
                "text": "Тори следит за разбором гнезда с глубоким неодобрением. Последнюю вещь он всё-таки отдаёт, но только после короткой погони.",
            },
            {
                "id": "leave",
                "label": "🦊 Оставить как есть",
                "emotion": "sleepy",
                "text": "Тори немедленно сворачивается в центре своего сооружения. Судя по довольной морде, решение признано единственно разумным.",
            },
        ],
    },
]

_TORI_CHAPTER_FINDS = {
    1: ["обломок древней ленты", "гладкий камень со странной царапиной", "маленькую металлическую шайбу"],
    2: ["яркое перо", "веточку с серебристыми листьями", "совершенно круглый желудь"],
    3: ["чужую пуговицу", "обрывок цветной тесьмы", "блестящую монетку неизвестного происхождения"],
    4: ["осколок тёмного стекла", "тонкое металлическое колечко", "кусочек ткани с вышитой звездой"],
}


def pick_tori_autonomous_event(chapter: int, period: str) -> dict:
    event = random.choice(_TORI_AUTONOMOUS_EVENTS)
    safe_chapter = max(1, min(chapter, 4))
    find = random.choice(_TORI_CHAPTER_FINDS[safe_chapter])
    return {
        "id": event["id"],
        "emotion": event["emotion"],
        "text": event["text"].get(period, event["text"]["day"]).format(find=find),
        "choices": [
            {"id": choice["id"], "label": choice["label"]}
            for choice in event["choices"]
        ],
    }


def get_tori_autonomous_response(event_id: str, choice_id: str) -> tuple[str, str] | None:
    event = next((item for item in _TORI_AUTONOMOUS_EVENTS if item["id"] == event_id), None)
    if not event:
        return None
    choice = next((item for item in event["choices"] if item["id"] == choice_id), None)
    if not choice:
        return None
    return choice["emotion"], choice["text"]
