from datetime import datetime

from pixkeep.delivery import plan_uploads
from pixkeep.texts import build_list_text, fmt_bytes, quota_label


def test_plan_skips_duplicates_in_folder_and_batch():
    plan = plan_uploads(["a.jpg", "b.jpg", "c.jpg"], ["h1", "h2", "h2"],
                        known={"h1": "old.jpg"}, names_in_folder={}, replace_duplicates=False)
    assert plan == ["old.jpg", None, "b.jpg"]


def test_replace_wins_over_content_skip():
    # a.jpg exists with other content; the new bytes also exist as other.jpg
    plan = plan_uploads(["a.jpg"], ["h2"], known={"h1": "a.jpg", "h2": "other.jpg"},
                        names_in_folder={"a.jpg": {"h1"}, "other.jpg": {"h2"}}, replace_duplicates=True)
    assert plan == [None]


def test_same_name_same_content_still_skipped_with_replace():
    plan = plan_uploads(["a.jpg"], ["h1"], known={"h1": "a.jpg"},
                        names_in_folder={"a.jpg": {"h1"}}, replace_duplicates=True)
    assert plan == ["a.jpg"]


def test_fmt_bytes_and_quota():
    assert fmt_bytes(512) == "512 Б"
    assert fmt_bytes(184 * 1024 ** 2) == "184,0 МБ"
    assert quota_label((184 * 1024 ** 2, 15 * 1024 ** 3)) == "184,0 МБ из 15,0 ГБ"
    assert quota_label(None) == "?"


def test_list_text():
    results = [
        {"upload_name": "001.jpg", "orig_name": "x.jpg", "success": True, "edited": True, "rotated": True},
        {"upload_name": "002.jpg", "orig_name": "y.jpg", "success": True, "duplicate_of": "001.jpg"},
        {"upload_name": "003.jpg", "orig_name": "z.jpg", "success": False},
    ]
    s = {"clean_metadata": True, "photo_fix": "auto"}
    text = build_list_text(" Trip ", results, s, now=datetime(2026, 1, 2, 3, 4))
    assert "Folder: Trip" in text
    assert "Files: 1 uploaded, 1 skipped as duplicates, 1 failed" in text
    assert "001.jpg - edited, rotated upright [was: x.jpg]" in text
    assert "002.jpg - duplicate, skipped (same as 001.jpg)" in text
    assert "z.jpg - FAILED" in text
