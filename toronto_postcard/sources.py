"""Read-only HTTPS calendar feeds and Open-Meteo; never log private feed URLs."""

import json
import signal
import threading
import time as clock
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar
import recurring_ical_events

from .model import Event

UTC = timezone.utc
MAX_CALENDAR_EVENTS = 4096
MAX_EVENT_TEXT = 2048
CALENDAR_PARSE_SECONDS = 30


@contextmanager
def calendar_parse_deadline():
    """Bound parser work on the Unix publisher's main thread, not network or I/O."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = clock.monotonic()

    def expired(signum, frame):
        raise TimeoutError("Calendar parsing exceeded its time limit")

    try:
        signal.signal(signal.SIGALRM, expired)
        deadline = CALENDAR_PARSE_SECONDS
        if previous_timer[0] > 0:
            deadline = min(deadline, previous_timer[0])
        signal.setitimer(signal.ITIMER_REAL, deadline)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            remaining = previous_timer[0] - (clock.monotonic() - started)
            # An existing timer keeps its elapsed time and is delivered promptly if due.
            signal.setitimer(signal.ITIMER_REAL, max(remaining, 0.000001), previous_timer[1])


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def get_https(url, max_bytes=16 * 1024 * 1024):
    if not url.startswith("https://"):
        raise ValueError("Data feeds must use HTTPS")
    # Redirects disabled so credentials cannot be forwarded to another origin.
    with requests.get(url, timeout=(8, 45), stream=True, allow_redirects=False,
                      headers={"User-Agent": "TorontoPostcard/0.1"}) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Unexpected data source status")
        chunks, total = [], 0
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Data feed is too large")
            chunks.append(chunk)
        return b"".join(chunks)


def parse_ics(payload, day, zone, owner_email=""):
    with calendar_parse_deadline():
        return _parse_ics(payload, day, zone, owner_email)


def _parse_ics(payload, day, zone, owner_email):
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    calendar = Calendar.from_ical(payload)
    if sum(component.name == "VEVENT" for component in calendar.walk()) > MAX_CALENDAR_EVENTS:
        raise ValueError("Calendar contains too many events")
    components = recurring_ical_events.of(calendar).between(start, end)
    if len(components) > MAX_CALENDAR_EVENTS:
        raise ValueError("Calendar recurrence expands to too many events")
    events = []
    for component in components:
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
        attendees = component.get("ATTENDEE", [])
        if not isinstance(attendees, list):
            attendees = [attendees]
        if owner_email and any(str(a).removeprefix("mailto:").casefold() == owner_email.casefold()
                               and str(a.params.get("PARTSTAT", "")).upper() == "DECLINED"
                               for a in attendees):
            continue
        raw_start = component.decoded("DTSTART")
        all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)
        raw_end = component.decoded("DTEND") if "DTEND" in component else None
        if raw_end is None:
            raw_end = raw_start + component.decoded("DURATION") if "DURATION" in component else (
                raw_start + timedelta(days=1) if all_day else raw_start + timedelta(minutes=1))
        def local(value):
            if not isinstance(value, datetime):
                return datetime.combine(value, time.min, zone)
            return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
        event_start, event_end = local(raw_start), local(raw_end)
        if not (event_start < end and event_end > start):
            continue
        uid = str(component.get("UID", "")) + ":" + event_start.isoformat()
        events.append(Event(uid, str(component.get("SUMMARY", "Untitled event"))[:MAX_EVENT_TEXT],
                            event_start, event_end, all_day, str(component.get("LOCATION", ""))[:MAX_EVENT_TEXT]))
    return events


def load_calendar(config, root, state, now):
    cache = state / "calendar.json"
    feeds = config.get("calendar_feeds", [])
    zone = ZoneInfo(config["timezone"])
    configured, combined = bool(feeds), []
    for feed in feeds:
        secret = root / feed["url_file"]
        if not secret.is_file():
            configured = False
            break
    if configured:
        try:
            for feed in feeds:
                url = (root / feed["url_file"]).read_text().strip()
                combined += parse_ics(get_https(url), now.date(), zone, feed.get("owner_email", ""))
                if len(combined) > MAX_CALENDAR_EVENTS:
                    raise ValueError("Combined calendars contain too many events")
            combined = list({event.uid: event for event in combined}.values())
            atomic_json(cache, {"date": now.date().isoformat(), "fetched_at": now.isoformat(),
                                "events": [event.to_dict() for event in combined]})
            return combined, "live"
        except Exception as exc:
            # Exception messages may contain a secret URL. Emit only its class.
            print("Calendar fetch failed: " + type(exc).__name__, flush=True)
    if cache.is_file():
        data = json.loads(cache.read_text())
        age = now.timestamp() - datetime.fromisoformat(data["fetched_at"]).timestamp()
        if data["date"] == now.date().isoformat() and 0 <= age <= config.get("calendar_cache_max_seconds", 86400):
            return [Event.from_dict(e) for e in data["events"]], "cached"
    bootstrap = state / "calendar-bootstrap.json"
    if not configured and bootstrap.is_file():
        data = json.loads(bootstrap.read_text())
        if data["date"] == now.date().isoformat():
            return [Event.from_dict(e) for e in data["events"]], "snapshot"
    return [], "unavailable"


def fetch_weather(config):
    params = {"latitude": config["latitude"], "longitude": config["longitude"],
              "timezone": config["timezone"], "timeformat": "unixtime", "forecast_days": 2,
              "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m",
              "hourly": "temperature_2m,precipitation_probability",
              "daily": "temperature_2m_max,temperature_2m_min", "wind_speed_unit": "kmh"}
    url = "https://api.open-meteo.com/v1/forecast?" + requests.compat.urlencode(params)
    result = json.loads(get_https(url, max_bytes=1024 * 1024))
    current, hourly, daily = result["current"], result["hourly"], result["daily"]
    for field in ("temperature_2m", "apparent_temperature", "weather_code", "time"):
        if current.get(field) is None:
            raise ValueError("Missing current weather value")
    index = min(range(len(hourly["time"])), key=lambda i: abs(hourly["time"][i] - current["time"]))
    return {"temperature": current["temperature_2m"], "feels_like": current["apparent_temperature"],
            "code": current["weather_code"], "is_day": bool(current["is_day"]),
            "observed_at": current["time"], "high": daily["temperature_2m_max"][0],
            "low": daily["temperature_2m_min"][0], "rain": hourly["precipitation_probability"][index],
            "hourly": list(zip(hourly["time"], hourly["temperature_2m"]))}


def load_weather(config, state, now):
    cache = state / "weather.json"
    data = json.loads(cache.read_text()) if cache.is_file() else None
    age = (now.timestamp() - data["fetched_at"]) if data else float("inf")
    if 0 <= age < config.get("weather_refresh_seconds", 900):
        return data["weather"], "live"
    try:
        weather = fetch_weather(config)
        atomic_json(cache, {"fetched_at": now.timestamp(), "weather": weather})
        return weather, "live"
    except Exception as exc:
        print("Weather fetch failed: " + type(exc).__name__, flush=True)
        if data and 0 <= age < config.get("weather_cache_max_seconds", 21600):
            return data["weather"], "cached"
        return None, "unavailable"
