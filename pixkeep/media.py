"""
MediaRef — a "receipt" for one incoming file: everything needed to fetch it
from Telegram later (file_id) and to report on it, without keeping the
aiogram Message object. Plain data, so it can be stored and restored.
Also: batch-level helpers that only need receipts (unique names, folder date).
"""
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from aiogram.types import Message

from pixkeep.runtime import LOCAL_TZ

BOT_API_DOWNLOAD_LIMIT = 20 * 1024 * 1024   # Bot API: bots can't download files > 20 MB

_DEFAULT_EXT = {"photo": ".jpg", "document": ".jpg", "audio": ".mp3", "voice": ".ogg"}
# Name prefix for media that arrive without a file name (like Telegram Desktop:
# photo_2026-10-28_22-09-01.jpg).
_PREFIX = {"photo": "photo", "document": "file", "video": "video", "audio": "audio",
           "voice": "voice", "video_note": "round", "animation": "animation"}


@dataclass
class MediaRef:
    kind: str            # photo | document | video | audio | voice | video_note | animation
    file_id: str
    file_unique_id: str
    name: str
    size: int | None
    message_id: int
    date: datetime | None   # when the message was sent (UTC)

    @classmethod
    def from_message(cls, message: Message) -> "MediaRef | None":
        kind, media = None, None
        for k in ("document", "photo", "video", "audio", "voice", "video_note", "animation"):
            media = getattr(message, k)
            if media:
                kind = k
                break
        if not media:
            return None
        if kind == "photo":
            media = media[-1]   # largest size
        name = getattr(media, "file_name", None) or \
            _default_name(kind, message.date, media.file_unique_id)
        return cls(kind=kind, file_id=media.file_id, file_unique_id=media.file_unique_id,
                   name=os.path.basename(name.replace("\\", "/")) or media.file_unique_id,
                   size=media.file_size, message_id=message.message_id, date=message.date)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat() if self.date else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MediaRef":
        d = dict(d)
        d["date"] = datetime.fromisoformat(d["date"]) if d.get("date") else None
        return cls(**d)


def _default_name(kind: str, sent: datetime | None, file_unique_id: str) -> str:
    """Readable and stable: built from the send time in LOCAL_TZ (several
    files of one album share it and get _2, _3 via unique_names)."""
    ext = _DEFAULT_EXT.get(kind, ".mp4")
    if not sent:
        return f"{file_unique_id}{ext}"
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    return f"{_PREFIX[kind]}_{sent.astimezone(LOCAL_TZ):%Y-%m-%d_%H-%M-%S}{ext}"


def unique_names(names: list[str]) -> list[str]:
    """Same names inside one batch get suffixes in order: a.jpg, a_2.jpg, a_3.jpg
    (otherwise they would overwrite each other on disk)."""
    taken: set[str] = set()
    out = []
    for name in names:
        base, ext = os.path.splitext(name)
        candidate, n = name, 1
        while candidate.lower() in taken:
            n += 1
            candidate = f"{base}_{n}{ext}"
        taken.add(candidate.lower())
        out.append(candidate)
    return out


_EXIF_DATETIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")


def parse_exif_date(raw: str | None) -> date | None:
    """EXIF DateTimeOriginal/CreateDate string -> date, or None if unusable."""
    if not raw:
        return None
    for fmt in _EXIF_DATETIME_FORMATS:
        try:
            return datetime.strptime(str(raw)[:19], fmt).date()
        except ValueError:
            continue
    return None


def pick_folder_date(exif_dates: list[date], sent: list[datetime]) -> date:
    """One date for the whole batch: the most common EXIF capture date (ties
    broken by the earliest), else the earliest send time in LOCAL_TZ, else today."""
    if exif_dates:
        counts = Counter(exif_dates)
        top = max(counts.values())
        return min(d for d, c in counts.items() if c == top)
    sent = [d for d in sent if d]
    if sent:
        dt = min(sent)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LOCAL_TZ).date()
    return datetime.now(LOCAL_TZ).date()
