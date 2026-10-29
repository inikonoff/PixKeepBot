"""Telegram HTML: only supported tags, balanced, user data escaped."""
from html.parser import HTMLParser

from pixkeep import texts
from pixkeep.pipeline import PreparedBatch

ALLOWED = {"b", "i", "a", "blockquote"}
EVIL = "<b>x</b> & <script>.jpg"


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"tag {tag}")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"unbalanced {tag}")


def _valid(text: str) -> None:
    c = _Checker()
    c.feed(text)
    assert not c.errors and not c.stack, (c.errors, c.stack, text)
    assert "<script>" not in text and EVIL not in text


SETTINGS = {"clean_metadata": False, "anonymize_names": False, "auto_date_folder": True,
            "replace_duplicates": True, "photo_fix": "auto", "upload_list": True}


def _batch():
    items = [{"orig_name": EVIL, "upload_name": EVIL, "kind": "photo", "clean_failed": True},
             {"orig_name": "b.jpg", "upload_name": "b.jpg", "kind": "document"}]
    return PreparedBatch(items=items, skipped=[(EVIL, "больше 20 МБ <лимит>")], gps_count=1,
                         rotated_count=1, edited_count=1)


def test_intake_note():
    note = texts.intake_note(_batch(), SETTINGS) + texts.auto_folder_line(EVIL, True)
    _valid(note)
    assert "Принято: 2 файла" in note and "Как «Фото» пришло: 1" in note
    assert "уйдут на Диск" in note       # GPS kept because cleaning is off
    _valid(texts.intake_note(_batch(), {**SETTINGS, "clean_metadata": True}) + texts.ask_folder_line())
    _valid(texts.nothing_received(_batch().skipped))


def test_report():
    results = [
        {"upload_name": f"{i}.jpg", "orig_name": f"{i}.jpg", "success": True} for i in range(50)
    ] + [
        {"upload_name": EVIL, "orig_name": EVIL, "success": True, "duplicate_of": "a.jpg"},
        {"upload_name": "z.jpg", "orig_name": EVIL, "success": False},
    ]
    text = texts.upload_report(results, EVIL, 'https://drive.google.com/drive/folders/x"y',
                               {"name": "list.txt"}, False, "a@b.c", (184 * 1024 ** 2, 15 * 1024 ** 3))
    _valid(text)
    assert "Загружено: 50 файлов" in text and "… и ещё 10" in text
    assert "<blockquote expandable>" in text and len(text) < 4096
    assert "184 МБ из 15 ГБ" in text and 'x&quot;y' in text


def test_report_only_duplicates_names_folder():
    results = [{"upload_name": "a.jpg", "orig_name": "a.jpg", "success": True, "duplicate_of": "a.jpg"}]
    text = texts.upload_report(results, "Trip", "https://x", None, False, "e", None)
    _valid(text)
    assert "Trip" in text and "Загружено" not in text


def test_other_messages():
    for t in (texts.uploading(EVIL, 1, 2), texts.quota_warning(EVIL, 1, 2),
              texts.upload_error(RuntimeError(EVIL)), texts.messages_not_deleted(3), texts.queued_line()):
        _valid(t)


def test_short_link_keeps_folder_id_end():
    link = "https://drive.google.com/drive/folders/1DYni1DJ-T7-p1C711jY3lLcpEixm6gmL"
    short = "drive.google.com/…/…lLcpEixm6gmL"
    assert texts.short_link(link) == short
    text = texts.upload_report([{"upload_name": "a.jpg", "orig_name": "a.jpg", "success": True}],
                               "28.10.26", link, None, False, "e", None)
    assert f'<a href="{link}">{short}</a>' in text
    assert texts.short_link("https://x.io/a") == "x.io/a"


def test_plural():
    assert [texts.files(n) for n in (1, 2, 5, 11, 21, 22, 112)] == \
        ["1 файл", "2 файла", "5 файлов", "11 файлов", "21 файл", "22 файла", "112 файлов"]


def test_queue_texts():
    from datetime import datetime, timezone
    entries = [{"files": [{}, {}]}, {"files": [{}]}]
    at = datetime(2026, 10, 29, 10, 47, tzinfo=timezone.utc)    # 13:47 Minsk
    status = texts.queue_status(entries, at, False, False, "14:00", texts.queue_added(2, EVIL))
    _valid(status)
    assert "2 пачки, 3 файла" in status and "≈ в 13:47" in status and "до 14:00" in status
    _valid(texts.queue_status(entries, at, True, False, "14:00"))
    _valid(texts.queue_status(entries, None, False, True, ""))
    summary = texts.queue_summary([
        {"folder_id": "A", "folder_name": EVIL, "link": "https://d/f/A", "uploaded": 3, "duplicates": 1, "unclean": 0},
        {"folder_id": "A", "folder_name": EVIL, "link": "https://d/f/A", "uploaded": 2, "duplicates": 0, "unclean": 1},
        {"folder_id": "B", "folder_name": "B", "link": "https://d/f/B", "uploaded": 1, "duplicates": 0, "unclean": 0},
    ])
    _valid(summary)
    assert "3 пачки, 6 файлов" in summary and summary.count("📁") == 2 and "— 5 файлов" in summary
    for t in (texts.queue_dropped([EVIL], EVIL), texts.queue_no_account(), texts.queue_revoked(),
              texts.queue_crashed(), texts.queue_added(1, None)):
        _valid(t)
