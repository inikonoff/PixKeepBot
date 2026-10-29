-- v5.32: parent folder per Google account (folder ids differ between
-- accounts). Run once in the Supabase SQL Editor; running it again is safe.
-- Until it is run the bot keeps working, only the parent folder setting is hidden.
alter table google_accounts add column if not exists parent_folder_id text;
alter table google_accounts add column if not exists parent_folder_name text;
