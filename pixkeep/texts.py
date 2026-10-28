"""
User-facing texts that are built from data (reports, notes, sizes, dates).
Pure functions — no I/O — so they are covered by tests.
"""
from datetime import datetime

from pixkeep.runtime import LOCAL_TZ


def fmt_bytes(n: float) -> str:
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.0f} {units[i]}" if i < 2 else f"{n:.1f} {units[i]}".replace(".", ",")


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


def skipped_text(skipped: list[tuple[str, str]]) -> str:
    lines = [f"⚠️ Не принял {len(skipped)} файл(ов):"]
    for name, reason in skipped[:10]:
        lines.append(f"• {name} — {reason}")
    if len(skipped) > 10:
        lines.append(f"… и ещё {len(skipped) - 10}")
    return "\n".join(lines)


def intake_note(batch, settings: dict) -> str:
    """What the bot did to a freshly received batch (batch: PreparedBatch)."""
    items = batch.items
    note = f"Получил {len(items)} файл(ов)."
    if batch.skipped:
        note += "\n" + skipped_text(batch.skipped) + "\n(их сообщения остаются в чате)"
    unclean = [it["orig_name"] for it in items if it.get("clean_failed")]
    if unclean:
        note += ("\n⚠️ Метаданные не удалось почистить (загружу как есть): "
                 + ", ".join(unclean[:5]) + (f" и ещё {len(unclean) - 5}" if len(unclean) > 5 else ""))
    if batch.gps_count:
        note += f"\n⚠️ В {batch.gps_count} из них есть GPS-координаты съёмки."
    note += (
        f"\nМетаданные: {'чищу' if settings['clean_metadata'] else 'оставляю как есть'}. "
        f"Имена: {'обезличиваю (001, 002…)' if settings['anonymize_names'] else 'оставляю оригинальные'}. "
        "(меняется в /settings)"
    )
    if settings["replace_duplicates"]:
        note += "\n♻️ Файлы с таким же именем в папке будут заменены."
    if batch.rotated_count:
        note += (f"\n↻ Повернул {batch.rotated_count} фото в нормальное положение "
                 "(иначе после чистки метаданных они лежали бы на боку).")
    if settings["photo_fix"] in ("auto", "always"):
        note += (f"\n🌗 Подправил яркость: {batch.edited_count} из {len(items)}"
                 if batch.edited_count else
                 "\n🌗 Коррекция включена, но кадры не показались тёмными — не трогал "
                 "(режим «всегда» подсветит любые).")
    return note


def auto_folder_line(folder_name: str) -> str:
    return f"\n\n📁 Папка будет создана автоматически: {folder_name}"


def ask_folder_line() -> str:
    return ("\n\nУкажите имя папки на Google Drive (текстом) или выберите ниже. "
            "Если такой папки нет, она будет создана.")


def queued_line() -> str:
    return ("\n\n⏳ Уже жду имя папки для предыдущей пачки — сначала "
            "ответь на тот вопрос, потом спрошу про эту.")


def nothing_received(skipped: list[tuple[str, str]]) -> str:
    if skipped:
        return skipped_text(skipped) + "\nИсходные сообщения оставил в чате."
    return "Не нашёл файлов для обработки."


def uploading(folder_name: str, done: int, total: int) -> str:
    return f"Загружаю в папку «{folder_name}»… {done}/{total}"


def quota_warning(email: str, free: int, need: int) -> str:
    return (f"⚠️ На аккаунте {email} свободно {fmt_bytes(free)}, "
            f"а пачка весит {fmt_bytes(need)}. Часть файлов может не загрузиться — "
            "можно переключить аккаунт в /accounts и нажать «Повторить неудачные».")


def upload_report(results: list[dict], folder_name: str, link: str, report_entry: dict | None,
                  list_failed: bool, email: str, quota: tuple | None) -> str:
    succeeded = [r for r in results if r["success"]]
    uploaded = [r for r in succeeded if not r.get("duplicate_of")]
    dups = [r for r in succeeded if r.get("duplicate_of")]
    failed = [r for r in results if not r["success"]]
    lines = []
    if uploaded:
        lines.append(f"✅ Загружено ({len(uploaded)}): " + ", ".join(r["upload_name"] for r in uploaded))
    if dups:
        lines.append(f"♻️ Пропущено как дубликаты ({len(dups)}): " + ", ".join(r["upload_name"] for r in dups))
    if failed:
        lines.append(f"❌ Не удалось загрузить ({len(failed)}): " + ", ".join(r["orig_name"] for r in failed))
    if report_entry:
        lines.append(f"📝 Список файлов: {report_entry['name']}")
    elif list_failed:
        lines.append("⚠️ txt-список загрузить не удалось (файлы при этом загружены).")
    if succeeded:
        lines.append(f"\nПапка (доступ на чтение всем, у кого есть ссылка):\n{link}")
        if quota:
            lines.append(f"💾 {email}: занято {quota_label(quota)}")
    return "\n".join(lines)


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
