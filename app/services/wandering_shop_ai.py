"""Structured AI for a text-only shop; never grants rewards or changes SQL."""
from __future__ import annotations

from app.services.ai_engine import _structured_response, ai_enabled
from app.services.wandering_shop import fallback_items, validate_items

ITEM = {
    'type': 'object', 'properties': {
        'name': {'type': 'string'}, 'description': {'type': 'string'},
        'category': {'type': 'string', 'enum': ['gift','toy','accessory','camp','tool','curiosity']},
        'recipient': {'type': 'string', 'enum': ['selin','tori','camp','party']},
        'rarity': {'type': 'string', 'enum': ['common','uncommon','rare']},
        'use_hint': {'type': 'string'},
    },
    'required': ['name','description','category','recipient','rarity','use_hint'],
    'additionalProperties': False,
}
CATALOG_SCHEMA = {'type': 'object', 'properties': {'items': {'type': 'array', 'items': ITEM}},
                  'required': ['items'], 'additionalProperties': False}
REACTION_SCHEMA = {'type': 'object', 'properties': {'text': {'type': 'string'}},
                   'required': ['text'], 'additionalProperties': False}

CATALOG_PROMPT = '''Ты управляешь фэнтезийной лавкой в дружелюбной RPG о Селин и её фамильяре Тори.
Предложи ровно ШЕСТЬ разных небольших текстовых предметов (не картинки и не реальные товары).
Это подарки, игрушки, аксессуары, предметы убежища, походные инструменты и странные находки.
К каждому придумай одно конкретное применение в будущей сцене. Покупки должны оставлять
долгую память: герои их носят, хранят, используют, теряют и снова находят.
НЕ назначай цены и НЕ придумывай механических бонусов к монетам, XP, физической активности,
питанию или здоровью. Предметы не могут быть платными ключами к основному сюжету.
Не дублируй уже показанные имена. Не упоминай реальные покупки и цены. Укажи ровно 6 предметов,
из них не больше 1 редкого, и хотя бы 1 для Селин и 1 для Тори.
Описание 1 предложение, применение 1 предложение, названия короткие. Строгий JSON.'''

REACTION_PROMPT = '''Ты пишешь только небольшую сценку-реакцию для фэнтези-RPG.
Селин — самостоятельная и иногда ироничная спутница, Тори — маленький белый лис-фамильяр
с красным грибным бантом, который не говорит по-человечески.
Учитывай кому принадлежит купленная вещь и при каких обстоятельствах она появилась.
Если событие use — покажи конкретное применение предмета по его свойствам, а не
очередное любование подарком. Если уже была реакция, НЕ повторяй её.
Тори общается действиями, звуками и жестами, но не человеческой речью.
Предмет физически существует и может использоваться позже. Не делай каждый подарок грандиозным
событием. 2-4 предложения на русском. Без параметров, монет, наград, интерфейса или призывов
к похудению/ограничениям. Никаких новых фактов о прошлом персонажей и основном каноне.
Игровое действие уже произошло; не объявляй новых покупок и не изменяй параметры.
Сцена может быть тёплой, смешной или слегка странной. Верни только text.'''


async def make_catalog(excluded: list[str], *, seed: int, ai_allowed: bool) -> tuple[list[dict], bool]:
    """Use AI once if allowed; on error return stable stock without charging real money."""
    if ai_allowed and ai_enabled():
        try:
            raw = await _structured_response(
                system=CATALOG_PROMPT,
                user_content='Недавно показанные товары (не повторяй): ' + ', '.join(excluded[:35]),
                schema_name='selin_wander_catalog_v1', schema=CATALOG_SCHEMA, max_tokens=1100,
                retry_max_tokens=1600,
            )
            items = validate_items(raw.get('items'))
            if sum(x['rarity'] == 'rare' for x in items) > 1:
                raise ValueError('Too many rare items')
            if not {'selin', 'tori'} <= {x['recipient'] for x in items}:
                raise ValueError('Missing character goods')
            if set(x['name'].casefold() for x in items) & set(v.casefold() for v in excluded[:25]):
                raise ValueError('Repeated stock')
            return items, True
        except Exception as exc:
            print('[WANDER_SHOP] generator fallback:', repr(exc))
    return fallback_items(seed, set(excluded[:24])), False


async def make_reaction(item, kind: str, *, ai_allowed: bool = True) -> str:
    name, rec, hint = item.name, item.recipient, item.use_hint
    if ai_allowed and ai_enabled():
        try:
            result = await _structured_response(
                system=REACTION_PROMPT,
                user_content=f'Событие: {kind}. Предмет: {name}. Адресат: {rec}. '
                             f'Описание: {item.description}. Возможное применение: {hint}. '
                             f'Использований раньше: {int(item.uses or 0)}. '
                             f'Не повторяй предыдущую реакцию: {str(item.last_reaction or "нет")[:350]}',
                schema_name='selin_wander_reaction_v1', schema=REACTION_SCHEMA,
                max_tokens=240, retry_max_tokens=400,
            )
            text = str(result.get('text') or '').strip()[:850]
            if len(text) >= 15:
                return text
        except Exception as exc:
            print('[WANDER_SHOP] reaction fallback:', repr(exc))
    if kind == 'use':
        return f'Селин достаёт «{name}». {hint} Тори с любопытством наблюдает за происходящим.'
    if rec == 'tori':
        return f'Тори принюхивается к подарку «{name}», осторожно касается его лапой и утаскивает поближе к своим сокровищам.'
    if rec == 'selin':
        return f'Селин разглядывает «{name}» и слегка улыбается. — Не ожидала. Но мне нравится. Она бережно убирает подарок.'
    if rec == 'camp':
        return f'«{name}» занимает своё место в убежище. Селин оценивает обновку, а Тори тут же отправляется её исследовать.'
    return f'Селин укладывает «{name}» среди походных вещей. — В дороге пригодится. Тори уже заинтересовался обновкой.'
