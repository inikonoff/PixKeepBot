"""
User-facing texts that are built from data (reports, notes, sizes, dates).
Pure functions — no I/O — so they are covered by tests.
"""
import html
from datetime import datetime

from pixkeep.runtime import LOCAL_TZ


def fmt_bytes(n: float) -> str:
    """1536 -> "2 КБ", 184 МБ -> "184 МБ", 1.5 ГБ -> "1,5 ГБ" (no trailing ",0")."""
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    if i < 2 or n >= 100:
        return f"{n:.0f} {units[i]}"
    return f"{n:.1f}".rstrip("0").rstrip(".").replace(".", ",") + f" {units[i]}"


def quota_label(q: tuple[int, int | None] | None) -> str:
    if not q:
        return "?"
    used, limit = q
    return f"{fmt_bytes(used)} из {fmt_bytes(limit)}" if limit else f"{fmt_bytes(used)} (без лимита)"


def fmt_dt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")
    except Exception:
        return "?"


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(2, "файл", "файла", "файлов") -> "2 файла"."""
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def files(n: int) -> str:
    return plural(n, "файл", "файла", "файлов")


# Everything below returns Telegram HTML (send with parse_mode="HTML");
# every piece of user data goes through esc().
esc = html.escape

MAX_LISTED = 40   # names per list; keeps a report far below Telegram's 4096 chars


def name_list(names: list[str]) -> str:
    """Names one per line in a quote; long lists collapse (tap to expand)."""
    shown = [esc(n) for n in names[:MAX_LISTED]]
    if len(names) > MAX_LISTED:
        shown.append(f"… и ещё {len(names) - MAX_LISTED}")
    tag = "blockquote expandable" if len(shown) > 3 else "blockquote"
    return f"<{tag}>" + "\n".join(shown) + "</blockquote>"


def short_link(link: str, tail: int = 12) -> str:
    """https://drive.google.com/drive/folders/1DYni1DJ-T7-p1C711jY3lLcpEixm6gmL ->
    "drive.google.com/…/…lLcpEixm6gmL": short, but the end of the folder id stays
    visible, so a glance tells whether uploads keep going into the same folder."""
    rest = link.split("://", 1)[-1]
    host, _, path = rest.partition("/")
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if not path or len(rest) <= len(host) + tail + 4:
        return rest
    return f"{host}/…/…{last[-tail:]}" if len(last) > tail else f"{host}/…/{last}"


def skipped_text(skipped: list[tuple[str, str]]) -> str:
    lines = [f"🚫 <b>Не принято: {files(len(skipped))}</b>"]
    lines += [f"• {esc(name)} — {esc(reason)}" for name, reason in skipped[:10]]
    if len(skipped) > 10:
        lines.append(f"… и ещё {len(skipped) - 10}")
    lines.append("<i>Их сообщения остались в чате.</i>")
    return "\n".join(lines)


def intake_note(batch, settings: dict) -> str:
    """What the bot did to a freshly received batch (batch: PreparedBatch)."""
    items = batch.items
    parts = [f"📥 <b>Принято: {files(len(items))}</b>"]

    done = [
        "🧹 Метаданные — удаляю (EXIF, GPS)" if settings["clean_metadata"] else "🧹 Метаданные — оставляю",
        "🔤 Имена — 001, 002…" if settings["anonymize_names"] else "🔤 Имена — оригинальные",
    ]
    if settings["replace_duplicates"]:
        done.append("♻️ Одноимённые файлы в папке заменю")
    if settings["upload_list"]:
        done.append("📝 Положу в папку список файлов")
    if batch.rotated_count:
        done.append(f"↻ Повернул в нормальное положение: {batch.rotated_count}")
    if settings["photo_fix"] in ("auto", "always"):
        done.append(f"🌗 Подправил яркость: {batch.edited_count} из {len(items)}" if batch.edited_count
                    else "🌗 Тёмных кадров нет — яркость не трогал")
    parts.append("\n".join(done))

    warnings = []
    if batch.gps_count:
        tail = "" if settings["clean_metadata"] else " — они уйдут на Диск вместе с файлами"
        warnings.append(f"📍 GPS-координаты есть в {files(batch.gps_count)}{tail}")
    unclean = [it["orig_name"] for it in items if it.get("clean_failed")]
    if unclean:
        warnings.append("⚠️ Не удалось почистить метаданные, загружу как есть:\n" + name_list(unclean))
    compressed = sum(1 for it in items if it.get("kind") == "photo")
    if compressed:
        warnings.append(f"📷 Как «Фото» пришло: {compressed} — Telegram их сжал. "
                        "Оригиналы присылай файлом (📎 → Файл).")
    if warnings:
        parts.append("\n".join(warnings))
    if batch.skipped:
        parts.append(skipped_text(batch.skipped))
    return "\n\n".join(parts)


SETTINGS_HINT = "\n\n<i>Изменить — /settings</i>"


def auto_folder_line(folder_name: str, from_exif: bool) -> str:
    source = "по дате съёмки" if from_exif else "по дате отправки (в файлах нет даты съёмки)"
    return f"\n\n📁 Папка <b>{esc(folder_name)}</b> — {source}" + SETTINGS_HINT


def ask_folder_line() -> str:
    return ("\n\n📁 <b>В какую папку?</b> Напиши имя или выбери ниже — "
            "если такой папки нет, создам." + SETTINGS_HINT)


def queued_line() -> str:
    return "\n\n⏳ Сначала ответь про папку для предыдущей пачки — потом спрошу про эту."


def nothing_received(skipped: list[tuple[str, str]]) -> str:
    return skipped_text(skipped) if skipped else "Не нашёл файлов для обработки."


def uploading(folder_name: str, done: int, total: int) -> str:
    return f"⏫ Загружаю в <b>{esc(folder_name)}</b>… {done}/{total}"


def quota_warning(email: str, free: int, need: int) -> str:
    return (f"⚠️ <b>Может не хватить места</b>\n"
            f"На {esc(email)} свободно {fmt_bytes(free)}, а пачка весит {fmt_bytes(need)}.\n"
            "<i>Если часть не загрузится — переключи аккаунт в /accounts и нажми «Повторить неудачные».</i>")


def upload_error(e: Exception) -> str:
    return f"❌ <b>Загрузка не удалась</b>\n{esc(str(e)[:500])}"


def messages_not_deleted(n: int) -> str:
    return (f"🗑 Не смог удалить из чата исходных сообщений: {n}. "
            "<i>Так бывает, если прошло больше 48 часов — удали вручную при желании.</i>")


def upload_report(results: list[dict], folder_name: str, link: str, report_entry: dict | None,
                  list_failed: bool, email: str, quota: tuple | None) -> str:
    succeeded = [r for r in results if r["success"]]
    uploaded = [r for r in succeeded if not r.get("duplicate_of")]
    dups = [r for r in succeeded if r.get("duplicate_of")]
    failed = [r for r in results if not r["success"]]
    folder = f"📁 <b>{esc(folder_name.strip())}</b>"
    parts = []
    if uploaded:
        parts.append(f"✅ <b>Загружено: {files(len(uploaded))}</b> в {folder}\n"
                     + name_list([r["upload_name"] for r in uploaded]))
    if dups:
        head = f"♻️ <b>Дубликаты, пропущено: {files(len(dups))}</b>"
        if not uploaded:
            head += f" — {folder}"
        parts.append(head + "\n" + name_list([r["upload_name"] for r in dups]))
    if failed:
        parts.append(f"❌ <b>Не загрузилось: {files(len(failed))}</b>\n"
                     + name_list([r["orig_name"] for r in failed]))
    extra = []
    if report_entry:
        extra.append(f"📝 Список файлов: {esc(report_entry['name'])}")
    elif list_failed:
        extra.append("⚠️ txt-список загрузить не удалось (файлы при этом загружены).")
    if succeeded:
        extra.append(f'🔗 <a href="{esc(link, quote=True)}">{esc(short_link(link))}</a>\n'
                     "<i>доступ на чтение по ссылке</i>")
        if quota:
            extra.append(f"💾 {esc(email)} · {quota_label(quota)}")
    if extra:
        parts.append("\n".join(extra))
    return "\n\n".join(parts)


def build_list_text(folder_name: str, results: list[dict], s: dict, now: datetime | None = None) -> str:
    """The optional txt list put into the folder (English, for any reader)."""
    now = now or datetime.now(LOCAL_TZ)
    dup = [r for r in results if r.get("duplicate_of")]
    ok = [r for r in results if r["success"] and not r.get("duplicate_of")]
    bad = [r for r in results if not r["success"]]
    lines = [
        "PixKeep upload list",
        f"Date: {now:%Y-%m-%d %H:%M} (Europe/Minsk)",
        f"Folder: {folder_name.strip()}",
        f"Metadata: {'cleaned' if s['clean_metadata'] else 'kept'}",
        f"Photo correction: {s['photo_fix']}",
        f"Files: {len(ok)} uploaded, {len(dup)} skipped as duplicates, {len(bad)} failed",
        "",
    ]
    for r in results:
        if r.get("duplicate_of"):
            line = f"{r['upload_name']} - duplicate, skipped (same as {r['duplicate_of']})"
        elif r["success"]:
            line = f"{r['upload_name']} - {'edited' if r.get('edited') else 'original'}"
            if r.get("rotated"):
                line += ", rotated upright"
            if r.get("clean_failed"):
                line += " [metadata NOT cleaned]"
            if r["upload_name"] != r["orig_name"]:
                line += f" [was: {r['orig_name']}]"
        else:
            line = f"{r['orig_name']} - FAILED"
        lines.append(line)
    return "\n".join(lines) + "\n"
