"""Turn Garmin's original-activity ZIP into JSON a model can read."""

import io
import zipfile
from datetime import datetime
from typing import Any

from garmin_fit_sdk import Decoder, Stream

SEMICIRCLES_TO_DEGREES = 180 / 2**31
# Per-second series that would swamp the output: the decoder merges hr_mesgs into records,
# gps_metadata repeats the records' speed and altitude, and hrv_mesgs is opt-in via include_hrv.
OMITTED_MESSAGES = ("record_mesgs", "hr_mesgs", "gps_metadata_mesgs", "hrv_mesgs")
# GPX/TCX uploads are XML text; cap them so one call can't flood the model's context.
MAX_TEXT_CHARS = 200_000


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def is_named(key: Any) -> bool:
    # The SDK keys fields and messages missing from the public FIT profile by number; they have no meaning to decode.
    return not str(key).isdigit()


def readable_message(message: dict[Any, Any]) -> dict[str, Any]:
    readable: dict[str, Any] = {}
    for key, value in message.items():
        if not is_named(key):
            continue
        name = str(key)
        # FIT stores positions as semicircles (int32); every position field is named *_lat / *_long.
        if isinstance(value, int) and (name.endswith("_lat") or name.endswith("_long")):
            readable[name] = value * SEMICIRCLES_TO_DEGREES
            continue
        readable[name] = json_safe(value)
    return readable


def downsample(
    records: list[dict[str, Any]], interval_seconds: int, fields: list[str] | None
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    last_kept: datetime | None = None
    for record in records:
        timestamp = record.get("timestamp")
        if interval_seconds > 0 and isinstance(timestamp, datetime) and last_kept is not None:
            if (timestamp - last_kept).total_seconds() < interval_seconds:
                continue
        if isinstance(timestamp, datetime):
            last_kept = timestamp
        readable = readable_message(record)
        if fields is not None:
            readable = {name: readable[name] for name in ["timestamp", *fields] if name in readable}
        kept.append(readable)
    return kept


def decode_fit(
    content: bytes, record_interval_seconds: int, record_fields: list[str] | None, include_hrv: bool
) -> dict[str, Any]:
    stream = Stream.from_byte_array(bytearray(content))
    messages, errors = Decoder(stream).read()
    counts = {str(name): len(items) for name, items in messages.items()}
    named = {name: items for name, items in messages.items() if is_named(name)}
    result: dict[str, Any] = {
        "message_counts": counts,
        "messages": {
            name: [readable_message(item) for item in items]
            for name, items in named.items()
            if name not in OMITTED_MESSAGES
        },
        "record_interval_seconds": record_interval_seconds,
        "records": downsample(named.get("record_mesgs", []), record_interval_seconds, record_fields),
        "decode_errors": [str(error) for error in errors],
    }
    if include_hrv:
        hrv = named.get("hrv_mesgs", [])
        result["hrv_rr_intervals_seconds"] = [interval for item in hrv for interval in item.get("time", [])]
    return result


def parse_activity_archive(
    archive: bytes, record_interval_seconds: int, record_fields: list[str] | None, include_hrv: bool
) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        names = [name for name in zipped.namelist() if not name.endswith("/")]
        if len(names) == 0:
            raise ValueError("The activity archive is empty")
        name = names[0]
        content = zipped.read(name)
    extension = name.rsplit(".", 1)[-1].lower()
    if extension == "fit":
        decoded = decode_fit(content, record_interval_seconds, record_fields, include_hrv)
        return {"file": name, "format": "fit", **decoded}
    text = content.decode("utf-8", errors="replace")
    return {
        "file": name,
        "format": extension,
        "truncated": len(text) > MAX_TEXT_CHARS,
        "content": text[:MAX_TEXT_CHARS],
    }
