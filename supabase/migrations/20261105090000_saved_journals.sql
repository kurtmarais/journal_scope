-- Journal Scope: saved (starred) journals, kept separately from report history
-- so they survive clearing history, resetting the app or reinstalling the phone app.
-- uid is the journal's key (its first ISSN, or "t:" + normalised title).
-- Run in the journal_scope Supabase project, after 20261103090000_journal_scope.sql.

create table if not exists public.journal_saved (
    uid               text primary key,
    user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
    title             text not null default '',
    issns             jsonb not null default '[]'::jsonb,
    report            jsonb not null default '{}'::jsonb,
    deleted           boolean not null default false,
    created_at        double precision not null default extract(epoch from now()),
    updated_at        double precision not null default extract(epoch from now()),
    server_updated_at timestamptz not null default now()
);

create index if not exists journal_saved_user_sync on public.journal_saved (user_id, server_updated_at);

drop trigger if exists journal_saved_newest_wins on public.journal_saved;
create trigger journal_saved_newest_wins before insert or update on public.journal_saved
    for each row execute function public.journal_scope_newest_wins();

alter table public.journal_saved enable row level security;

drop policy if exists "own saved journals" on public.journal_saved;
create policy "own saved journals" on public.journal_saved for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

revoke all on public.journal_saved from anon;
grant select, insert, update, delete on public.journal_saved to authenticated;
