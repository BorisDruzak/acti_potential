from datetime import date as date_cls
from datetime import datetime
from typing import Optional


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%d.%m.%Y",
    "%d/%m/%Y",
)


def parse_flexible_datetime(value) -> Optional[datetime]:
    """Parse common document date formats used across old/new databases."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date_cls):
        return datetime(value.year, value.month, value.day)

    text = str(value).strip()
    if not text:
        return None

    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(iso_candidate)
    except ValueError:
        pass

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def to_iso_date(value) -> Optional[str]:
    parsed = parse_flexible_datetime(value)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def format_date_for_display(value) -> str:
    parsed = parse_flexible_datetime(value)
    if parsed:
        return parsed.strftime("%d.%m.%Y")
    return "" if value is None else str(value)
