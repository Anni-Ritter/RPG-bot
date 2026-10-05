from __future__ import annotations

from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent


def game_data_path(filename: str) -> Path:
    """Return a static game-data file path.

    `game_data` is the canonical directory. `data` is kept as a fallback so an
    older local checkout still starts while migrating from the Bothost-reserved
    `/app/data` name.
    """
    for dirname in ("game_data", "data"):
        candidate = _APP_ROOT / dirname / filename
        if candidate.exists():
            return candidate
    return _APP_ROOT / "game_data" / filename
