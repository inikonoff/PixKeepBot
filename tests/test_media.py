from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

from pixkeep.media import MediaRef, parse_exif_date, pick_folder_date, unique_names

SENT = datetime(2026, 10, 28, 19, 9, 1, tzinfo=timezone.utc)


def _msg(**media):
    fields = dict(document=None, photo=None, video=None, audio=None, voice=None,
                  video_note=None, animation=None)
    fields.update(media)
    return NS(message_id=7, date=SENT, **fields)


def _file(uid="UQ", name=None, size=10):
    return NS(file_id="fid-" + uid, file_unique_id=uid, file_size=size, file_name=name)


def test_document_keeps_its_name():
    ref = MediaRef.from_message(_msg(document=_file(name="IMG_0001.HEIC")))
    assert (ref.kind, ref.name, ref.file_id, ref.message_id) == ("document", "IMG_0001.HEIC", "fid-UQ", 7)


def test_photo_uses_largest_size():
    ref = MediaRef.from_message(_msg(photo=[_file("small"), _file("big")]))
    assert ref.kind == "photo" and ref.file_id == "fid-big"


def test_nameless_media_get_stable_readable_names():
    a = MediaRef.from_message(_msg(voice=_file("V1")))
    b = MediaRef.from_message(_msg(voice=_file("V1")))
    assert a.name == b.name == "voice_2026-10-28_22-09-01.ogg"     # 19:09 UTC = 22:09 Minsk
    photo = MediaRef.from_message(_msg(photo=[_file("P")]))
    assert photo.name == "photo_2026-10-28_22-09-01.jpg"


def test_path_in_document_name_is_stripped():
    ref = MediaRef.from_message(_msg(document=_file(name="..\\..\\evil.jpg")))
    assert ref.name == "evil.jpg"


def test_no_media():
    assert MediaRef.from_message(_msg()) is None


def test_roundtrip_dict():
    ref = MediaRef.from_message(_msg(document=_file(name="a.jpg")))
    assert MediaRef.from_dict(ref.to_dict()) == ref


def test_unique_names_in_order():
    assert unique_names(["a.jpg", "b.jpg", "a.jpg", "A.JPG", "a.jpg"]) == \
        ["a.jpg", "b.jpg", "a_2.jpg", "A_3.JPG", "a_4.jpg"]


def test_parse_exif_date():
    assert parse_exif_date("2024:05:01 10:20:30") == date(2024, 5, 1)
    assert parse_exif_date("2024-05-01 10:20:30+03:00") == date(2024, 5, 1)
    assert parse_exif_date("0000:00:00 00:00:00") is None
    assert parse_exif_date(None) is None


def test_folder_date_most_common_then_earliest():
    d1, d2 = date(2024, 5, 1), date(2024, 5, 2)
    assert pick_folder_date([d2, d1, d2], []) == d2
    assert pick_folder_date([d2, d1], []) == d1


def test_folder_date_falls_back_to_local_send_date():
    late_utc = datetime(2026, 10, 28, 22, 30, tzinfo=timezone.utc)   # 01:30 next day in Minsk
    assert pick_folder_date([], [late_utc]) == date(2026, 10, 29)
