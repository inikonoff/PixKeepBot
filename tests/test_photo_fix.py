from PIL import Image

from pixkeep.photo_fix import _build_lut, _strength_for, fix_photo, normalize_orientation


def test_lut_is_monotonic_and_keeps_white():
    for s in (0.0, 1.0, 3.5):
        lut = _build_lut(8, s)
        assert lut == sorted(lut) and lut[255] == 255 and lut[0] == 0


def test_strength_zero_for_bright_shadows():
    assert _strength_for(120) == 0.0
    assert 0 < _strength_for(30) <= 3.5


def test_dark_photo_is_lifted(tmp_path):
    src, dst = tmp_path / "dark.jpg", tmp_path / "out.jpg"
    Image.new("RGB", (64, 64), (25, 25, 25)).save(src)
    res = fix_photo(str(src), str(dst), "auto")
    assert res["edited"]
    with Image.open(dst) as im:
        assert im.getpixel((10, 10))[0] > 25


def test_bright_photo_untouched_in_auto(tmp_path):
    src, dst = tmp_path / "bright.jpg", tmp_path / "out.jpg"
    Image.new("RGB", (64, 64), (200, 200, 200)).save(src)
    assert not fix_photo(str(src), str(dst), "auto")["edited"]
    assert not dst.exists()


def test_off_and_unsupported(tmp_path):
    src = tmp_path / "a.gif"
    Image.new("P", (8, 8)).save(src)
    assert fix_photo(str(src), str(tmp_path / "o"), "off")["note"] == "off"
    assert not fix_photo(str(src), str(tmp_path / "o"), "always")["edited"]


def test_orientation_is_baked_in(tmp_path):
    src, dst = tmp_path / "rot.jpg", tmp_path / "up.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6   # rotate 90 CW to display
    Image.new("RGB", (40, 20)).save(src, exif=exif)
    assert normalize_orientation(str(src), str(dst))
    with Image.open(dst) as im:
        assert im.size == (20, 40)
