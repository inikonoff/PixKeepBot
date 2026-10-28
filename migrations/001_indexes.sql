-- Optional index for per-folder lookups in upload history (comments, rename,
-- delete whole upload). Safe to run more than once.
create index if not exists idx_uploads_folder on uploads(telegram_id, folder_id);
