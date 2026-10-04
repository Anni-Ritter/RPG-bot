LEVEL_THRESHOLDS = [
    0, 250, 550, 900, 1300, 1750, 2250, 2800, 3400, 4050, 4750, 5500
]


def level_from_xp(xp: int) -> tuple[int, int, int | None]:
    level = 1
    for idx, threshold in enumerate(LEVEL_THRESHOLDS, start=1):
        if xp >= threshold:
            level = idx
        else:
            break

    current_threshold = LEVEL_THRESHOLDS[level - 1]
    next_threshold = LEVEL_THRESHOLDS[level] if level < len(LEVEL_THRESHOLDS) else None
    return level, current_threshold, next_threshold
