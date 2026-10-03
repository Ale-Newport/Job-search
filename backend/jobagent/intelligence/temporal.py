"""Date arithmetic with explicit precision and union of overlapping evidence."""

from datetime import date, timedelta
import calendar
import re


def interval(start, end, today, ongoing=False):
    """Partial months conservatively use last start-day and first end-day."""
    if not start or (not end and not ongoing):
        return None
    if len(start) == 7:
        y, m = map(int, start.split("-"))
        start += f"-{calendar.monthrange(y, m)[1]:02d}"
    if end and len(end) == 7:
        end += "-01"
    try:
        a, b = date.fromisoformat(start), min(date.fromisoformat(end) if end else today, today)
    except ValueError:
        return None
    return (a, b) if a <= b else None


def exposure(intervals):
    merged = []
    for a, b in sorted(i for i in intervals if i):
        if merged and a <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    days = sum((b - a).days + 1 for a, b in merged)
    return {
        "days": days,
        "years": round(days / 365.2425, 2),
        "integer_years": int(days / 365.2425),
        "months": int(days / 30.436875),
        "intervals": [[a.isoformat(), b.isoformat()] for a, b in merged],
    }


def requested_date(text):
    match = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
    if match:
        try:
            return date.fromisoformat(match[1]), "day"
        except ValueError:
            return None
    for month in range(1, 13):
        m = re.search(r"\b" + calendar.month_name[month].lower() + r"\s+(20\d\d)\b", text.lower())
        if m:
            return date(int(m[1]), month, 1), "month"
    return None
