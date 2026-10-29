-- v5.30: deferred (scheduled) upload. Run once in the Supabase SQL Editor
-- BEFORE deploying v5.30; running it again is safe. Until it is run, the bot
-- keeps working, only the scheduled mode is unavailable.
alter table user_settings add column if not exists deferred boolean default false;
alter table user_settings add column if not exists deferred_min int default 10;
alter table user_settings add column if not exists deferred_max int default 15;
alter table user_settings add column if not exists deferred_deadline text default '14:00';

-- one row per waiting batch: only Telegram file receipts, no file data
create table if not exists upload_queue (
  id bigserial primary key,
  telegram_id bigint not null,
  chat_id bigint not null,
  folder_name text,                 -- null = name it by capture date at upload time
  files jsonb not null,             -- MediaRef dicts (file_id, name, size, message_id, ...)
  attempts int not null default 0,  -- retried batches go first
  created_at timestamptz default now()
);
create index if not exists idx_upload_queue_user on upload_queue(telegram_id, attempts desc, id);

-- scheduler state per user, so a restart continues the same schedule
create table if not exists upload_queue_state (
  telegram_id bigint primary key,
  chat_id bigint not null,
  next_tick_at timestamptz,
  flush boolean not null default false,   -- next tick uploads everything left
  paused boolean not null default false,  -- Google access revoked
  status_message_id bigint,
  summary jsonb not null default '[]'     -- finished batches, for the final report
);
