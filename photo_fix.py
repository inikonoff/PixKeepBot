"""
photo_fix.py
v1.0 - "auto" brightness / shadow lift for dark photos (Pillow only, no numpy)

Modes (user setting `photo_fix`):
    off     - never touch pixels
    auto    - only fix clearly dark photos (median brightness below DARK_MEDIAN)
    always  - lift shadows on every photo (at least a mild lift)

How: a single per-channel tone curve (look-up table) built from a small
thumbnail's statistics — black point trimmed, then a gamma lift so the median
brightness approaches TARGET_MEDIAN. White stays white, so highlights are not
blown. Applied with Image.point(), so memory stays ~2-3x the image size.

Only JPEG/PNG in plain RGB are handled; everything else is left untouched.
JPEG is re-saved at quality 95 with the original EXIF + ICC profile (EXIF is
removed afterwards by the normal metadata-cleaning step if that is enabled).

normalize_orientation(): rotates pixels according to the EXIF Orientation tag
(used when metadata cleaning is on, since cleaning drops that tag).
"""
import math
import os

from PIL import Image, ImageOps, ImageStat, JpegImagePlugin

DARK_MEDIAN = 85        # auto mode: median luma (0-255) below this = "dark"
TARGET_MEDIAN = 120     # where the curve tries to bring the median
MIN_GAMMA = 0.5         # never lift harder than this (limits noise blow-up)
ALWAYS_GAMMA = 0.82     # "always" mode: at least this much lift
MAX_PIXELS = 40_000_000  # skip anything bigger (memory on small hosts)
JPEG_QUALITY = 95


def _build_lut(black: int, gamma: float) -> list[int]:
    span = max(255 - black, 1)
    lut = []
    for x in range(256):
        v = max(x - black, 0) / span
        lut.append(min(255, round(255 * (v ** gamma))))
    return lut


def normalize_orientation(src: str, dst: str) -> bool:
    """Bakes the EXIF Orientation tag into the pixels (writes dst) when the
    photo isn't stored upright. Needed before metadata cleaning: `exiftool
    -all=` also deletes the Orientation tag, which would leave portrait
    shots lying on their side. Returns True if dst was written.
    JPEG is re-saved at quality 95 (same chroma sampling + ICC profile)."""
    try:
        with Image.open(src) as im:
            fmt = im.format
            if fmt not in ("JPEG", "PNG"):
                return False
            if (im.getexif().get(0x0112) or 1) == 1:
                return False
            if im.size[0] * im.size[1] > MAX_PIXELS:
                return False
            im.load()
            kwargs = {}
            icc = im.info.get("icc_profile")
            if icc:
                kwargs["icc_profile"] = icc
            sub = JpegImagePlugin.get_sampling(im) if fmt == "JPEG" else -1
            out = ImageOps.exif_transpose(im)
            if fmt == "JPEG":
                if sub != -1:
                    kwargs["subsampling"] = sub
                out.save(dst, "JPEG", quality=JPEG_QUALITY, **kwargs)
            else:
                out.save(dst, "PNG", **kwargs)
        return True
    except Exception:
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        return False


def fix_photo(src: str, dst: str, mode: str) -> dict:
    """Returns {"edited": bool, "note": str}. Writes dst only when edited."""
    if mode not in ("auto", "always"):
        return {"edited": False, "note": "off"}
    try:
        with Image.open(src) as probe:
            fmt = probe.format
            size = probe.size
            if fmt not in ("JPEG", "PNG"):
                return {"edited": False, "note": f"unsupported format {fmt}"}
            if probe.mode != "RGB":
                return {"edited": False, "note": f"unsupported mode {probe.mode}"}
            if size[0] * size[1] > MAX_PIXELS:
                return {"edited": False, "note": "too large"}
            thumb = probe.copy() if fmt == "PNG" else probe
            if fmt == "JPEG":
                probe.draft("RGB", (320, 320))   # fast, low-memory decode
                thumb = probe.convert("L")
            else:
                thumb = thumb.convert("L")
            thumb.thumbnail((320, 320))
            stat = ImageStat.Stat(thumb)
            median = stat.median[0]
            hist = thumb.histogram()
            total = sum(hist)
            acc, black = 0, 0
            for i, c in enumerate(hist):
                acc += c
                if acc >= total * 0.01:
                    black = i
                    break
            black = min(black, 20)

        if mode == "auto" and median >= DARK_MEDIAN:
            return {"edited": False, "note": "not dark"}

        med = max(median, 8)
        gamma = math.log(TARGET_MEDIAN / 255) / math.log(med / 255)
        gamma = max(MIN_GAMMA, min(gamma, 1.0))
        if mode == "always":
            gamma = min(gamma, ALWAYS_GAMMA)
        if gamma > 0.97 and black == 0:
            return {"edited": False, "note": "no change needed"}

        lut = _build_lut(black, gamma) * 3
        with Image.open(src) as im:
            im.load()
            out = im.point(lut)
            kwargs = {}
            icc = im.info.get("icc_profile")
            if icc:
                kwargs["icc_profile"] = icc
            if fmt == "JPEG":
                exif = im.info.get("exif")
                if exif:
                    kwargs["exif"] = exif
                sub = JpegImagePlugin.get_sampling(im)  # keep original chroma sampling
                if sub != -1:
                    kwargs["subsampling"] = sub
                out.save(dst, "JPEG", quality=JPEG_QUALITY, **kwargs)
            else:
                out.save(dst, "PNG", optimize=False, **kwargs)
        return {"edited": True,
                "note": f"brightness adjusted (median {int(median)} -> gamma {gamma:.2f})"}
    except Exception as e:  # corrupt file, unsupported variant, etc.
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        return {"edited": False, "note": f"skipped: {e}"[:120]}
