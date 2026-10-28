"""prepare_batch end to end with a fake Telegram download (real Pillow and exiftool)."""
import asyncio
import shutil
from datetime import date, datetime, timezone

import pytest
from PIL import Image

from pixkeep import pipeline
from pixkeep.media import MediaRef

pytestmark = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")

SETTINGS = {"clean_metadata": True, "anonymize_names": False, "auto_date_folder": False,
            "replace_duplicates": False, "photo_fix": "auto", "upload_list": False}


def _jpeg(path, color, orientation=1):
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x0110] = "TestCam"                         # Model
    exif.get_ifd(0x8769)[0x9003] = "2024:05:01 10:20:30"   # DateTimeOriginal
    Image.new("RGB", (40, 20), color).save(path, "JPEG", exif=exif)


def _ref(name, mid, size=100):
    return MediaRef(kind="document", file_id=f"f{mid}", file_unique_id=f"u{mid}", name=name,
                    size=size, message_id=mid, date=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_prepare_batch(tmp_path, monkeypatch):
    sources = tmp_path / "tg"
    sources.mkdir()
    _jpeg(sources / "f1", (20, 20, 20), orientation=6)   # dark + sideways
    _jpeg(sources / "f2", (220, 220, 220))
    (sources / "f3").write_bytes(b"not an image at all")

    async def fake_download(ref, path):
        if ref.file_id == "f4":
            raise pipeline.DownloadSkipped("больше 20 МБ")
        shutil.copy(sources / ref.file_id, path)

    monkeypatch.setattr(pipeline, "_download", fake_download)
    refs = [_ref("IMG.jpg", 1), _ref("IMG.jpg", 2), _ref("notes.txt", 3), _ref("big.mov", 4)]
    batch = asyncio.run(pipeline.prepare_batch(refs, SETTINGS, str(tmp_path / "work")))

    assert batch.skipped == [("big.mov", "больше 20 МБ")]
    assert [it["upload_name"] for it in batch.items] == ["IMG.jpg", "IMG_2.jpg", "notes.txt"]
    assert batch.exif_dates == [date(2024, 5, 1), date(2024, 5, 1)]
    assert batch.rotated_count == 1 and batch.edited_count == 1
    first = batch.items[0]
    assert first["rotated"] and first["edited"]
    with Image.open(first["upload_path"]) as im:
        assert im.size == (20, 40)                   # upright
        assert not im.getexif()                      # metadata cleaned
    assert open(batch.items[2]["upload_path"], "rb").read() == b"not an image at all"


def test_batch_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.config, "MAX_BATCH_MB", 1)

    async def fake_download(ref, path):
        open(path, "wb").write(b"x")

    monkeypatch.setattr(pipeline, "_download", fake_download)
    refs = [_ref("a.bin", 1, size=700_000), _ref("b.bin", 2, size=700_000)]
    batch = asyncio.run(pipeline.prepare_batch(refs, {**SETTINGS, "clean_metadata": False,
                                                       "photo_fix": "off"}, str(tmp_path / "w")))
    assert len(batch.items) == 1 and len(batch.skipped) == 1
