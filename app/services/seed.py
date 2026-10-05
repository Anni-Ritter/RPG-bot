import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScrollDefinition
from app.services.content import game_data_path


async def seed_scrolls(session: AsyncSession) -> None:
    path = game_data_path("scrolls.json")
    data = json.loads(path.read_text(encoding="utf-8"))

    for row in data:
        existing = await session.get(ScrollDefinition, row["id"])
        if existing:
            for key, value in row.items():
                setattr(existing, key, value)
        else:
            session.add(ScrollDefinition(**row))
