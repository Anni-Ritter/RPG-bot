
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
