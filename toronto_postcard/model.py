"""Small, timezone-aware event model and display selection rules."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Event:
    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str = ""

    def to_dict(self):
        return {"uid": self.uid, "title": self.title, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "all_day": self.all_day, "location": self.location}

    @classmethod
    def from_dict(cls, value):
        start, end = datetime.fromisoformat(value["start"]), datetime.fromisoformat(value["end"])
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Event timestamps must include a timezone")
        return cls(value["uid"], value["title"], start, end,
                   bool(value.get("all_day", False)), value.get("location", ""))


def agenda(events, now, limit=4):
    """Prefer current/upcoming timed events; all-day events never become NEXT UP."""
    ordered = sorted(events, key=lambda e: (e.start.timestamp(), e.end.timestamp(), e.uid))
    all_day = [e for e in ordered if e.all_day]
    timed = [e for e in ordered if not e.all_day]
    remaining = [e for e in timed if e.end.timestamp() > now.timestamp()]
    completed = [e for e in timed if e.end.timestamp() <= now.timestamp()]
    if remaining:
        visible = remaining[:limit]
        if len(visible) < limit:
            visible = completed[-(limit - len(visible)):] + visible
    else:
        visible = completed[-limit:]
    visible.sort(key=lambda e: (e.start.timestamp(), e.end.timestamp(), e.uid))
    current = next((e for e in remaining if e.start.timestamp() <= now.timestamp() < e.end.timestamp()), None)
    upcoming = next((e for e in remaining if e.start.timestamp() > now.timestamp()), None)
    highlighted = current or upcoming
    return all_day, visible, highlighted, max(0, len(timed) - len(visible))
