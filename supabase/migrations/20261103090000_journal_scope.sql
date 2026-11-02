-- Journal Scope phone sync.
--   journal_reports : reports produced by the desktop app (read and deleted on the phone)
--   journal_requests: lookups asked for on the phone, picked up by the desktop app
-- Every row belongs to one Supabase Auth user; row-level security means a
-- signed-in user can only see or change their own rows. Table names are
-- prefixed so this can share a project with other apps (e.g. Sidebar).
-- Times are Unix epoch seconds written by the clients (newest edit wins);
-- server_updated_at is set by the database and is what clients sync from.

create table if not exists public.journal_reports (
    uid               text primary key,
    user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
    query             text not null default '',
    title             text not null default '',
    issns             jsonb not null default '[]'::jsonb,
    report            jsonb not null default '{}'::jsonb,
    deleted           boolean not null default false,
    created_at        double precision not null default extract(epoch from now()),
    updated_at        double precision not null default extract(epoch from now()),
    server_updated_at timestamptz not null default now()
);

create table if not exists public.journal_requests (
    uid               text primary key,
    user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
    query             text not null default '',
    sid               text,
    site              text,
    status            text not null default 'pending'
                      check (status in ('pending', 'running', 'done', 'error')),
    report_uid        text,
    candidates        jsonb,                    -- set when a title matched several journals
    error             text,
    deleted           boolean not null default false,
    created_at        double precision not null default extract(epoch from now()),
    updated_at        double precision not null default extract(epoch from now()),
    server_updated_at timestamptz not null default now()
);

create index if not exists journal_reports_user_sync on public.journal_reports (user_id, server_updated_at);
create index if not exists journal_requests_user_sync on public.journal_requests (user_id, server_updated_at);

-- Newest edit wins: an upload carrying an older updated_at than the stored
-- row is ignored. Accepted writes get a fresh server_updated_at.
create or replace function public.journal_scope_newest_wins()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        new.user_id := old.user_id;                 -- rows can never change owner
        if new.updated_at < old.updated_at then
            return old;
        end if;
    end if;
    new.server_updated_at := clock_timestamp();
    return new;
end;
$$;

drop trigger if exists journal_reports_newest_wins on public.journal_reports;
create trigger journal_reports_newest_wins before insert or update on public.journal_reports
    for each row execute function public.journal_scope_newest_wins();

drop trigger if exists journal_requests_newest_wins on public.journal_requests;
create trigger journal_requests_newest_wins before insert or update on public.journal_requests
    for each row execute function public.journal_scope_newest_wins();

alter table public.journal_reports enable row level security;
alter table public.journal_requests enable row level security;

drop policy if exists "own journal reports" on public.journal_reports;
create policy "own journal reports" on public.journal_reports for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "own journal requests" on public.journal_requests;
create policy "own journal requests" on public.journal_requests for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

-- Anonymous (signed-out) requests get nothing.
revoke all on public.journal_reports, public.journal_requests from anon;
grant select, insert, update, delete on public.journal_reports, public.journal_requests to authenticated;
