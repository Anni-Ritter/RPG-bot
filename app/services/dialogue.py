
from __future__ import annotations

import json
import random
from pathlib import Path

DIALOGUE_PATH = Path(__file__).resolve().parent.parent / "game_data" / "dialogue.json"
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
