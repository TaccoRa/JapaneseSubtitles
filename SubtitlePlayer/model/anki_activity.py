"""Profile-scoped daily Anki-add activity helpers."""

from __future__ import annotations

from datetime import date, timedelta


RANGE_OPTIONS = ("7D", "30D", "90D", "1Y", "All")


def normalize_daily_history(value) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for raw_day, raw_count in value.items():
        try:
            day = date.fromisoformat(str(raw_day).strip())
            count = max(0, int(raw_count or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if count > 0:
            normalized[day.isoformat()] = count
    return dict(sorted(normalized.items()))


def migrate_daily_history(value, legacy_date="", legacy_count=0) -> dict[str, int]:
    history = normalize_daily_history(value)
    try:
        day = date.fromisoformat(str(legacy_date or "").strip()).isoformat()
        count = max(0, int(legacy_count or 0))
    except (TypeError, ValueError, OverflowError):
        return history
    if count > 0:
        history[day] = max(count, int(history.get(day, 0) or 0))
    return dict(sorted(history.items()))


def increment_daily_history(value, day: date | str | None = None, amount: int = 1) -> dict[str, int]:
    history = normalize_daily_history(value)
    if day is None:
        key = date.today().isoformat()
    elif isinstance(day, date):
        key = day.isoformat()
    else:
        key = date.fromisoformat(str(day).strip()).isoformat()
    increment = max(0, int(amount or 0))
    if increment:
        history[key] = int(history.get(key, 0) or 0) + increment
    return dict(sorted(history.items()))


def build_activity_series(value, range_key: str, *, today: date | None = None) -> tuple[list[dict], str]:
    history = normalize_daily_history(value)
    current = today or date.today()
    key = str(range_key or "30D").strip().upper()

    if key in {"7D", "30D", "90D"}:
        days = int(key[:-1])
        start = current - timedelta(days=days - 1)
        return _daily_series(history, start, current), "day"
    if key == "1Y":
        start = current - timedelta(days=364)
        return _weekly_series(history, start, current), "week"

    parsed_days = [date.fromisoformat(day) for day in history if day <= current.isoformat()]
    start = min(parsed_days) if parsed_days else current
    return _monthly_series(history, start, current), "month"


def _daily_series(history: dict[str, int], start: date, end: date) -> list[dict]:
    points = []
    cursor = start
    while cursor <= end:
        count = int(history.get(cursor.isoformat(), 0) or 0)
        points.append(
            {
                "label": cursor.strftime("%d.%m"),
                "tooltip": cursor.strftime("%d.%m.%Y"),
                "count": count,
            }
        )
        cursor += timedelta(days=1)
    return points


def _weekly_series(history: dict[str, int], start: date, end: date) -> list[dict]:
    points = []
    cursor = start
    while cursor <= end:
        bucket_end = min(cursor + timedelta(days=6), end)
        count = _sum_dates(history, cursor, bucket_end)
        points.append(
            {
                "label": cursor.strftime("%d.%m"),
                "tooltip": f"{cursor:%d.%m.%Y} - {bucket_end:%d.%m.%Y}",
                "count": count,
            }
        )
        cursor = bucket_end + timedelta(days=1)
    return points


def _monthly_series(history: dict[str, int], start: date, end: date) -> list[dict]:
    points = []
    cursor = date(start.year, start.month, 1)
    final = date(end.year, end.month, 1)
    while cursor <= final:
        next_month = _next_month(cursor)
        bucket_end = min(next_month - timedelta(days=1), end)
        bucket_start = max(cursor, start)
        count = _sum_dates(history, bucket_start, bucket_end)
        points.append(
            {
                "label": cursor.strftime("%m.%y"),
                "tooltip": cursor.strftime("%B %Y"),
                "count": count,
            }
        )
        cursor = next_month
    return points


def _sum_dates(history: dict[str, int], start: date, end: date) -> int:
    return sum(
        int(count or 0)
        for day, count in history.items()
        if start.isoformat() <= day <= end.isoformat()
    )


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
