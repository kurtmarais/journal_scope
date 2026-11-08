-- Journal Scope: SCImago's SJR ranking for the phone (SJR quartile per journal).
-- The desktop app loads it with the CREST and SANLiC lists (about weekly).
-- Run in the journal_scope Supabase project, after 20261104090000_reference_lists.sql. Safe to run again.

create table if not exists public.ref_sjr (
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    key         text not null,                 -- SCImago source id
    title       text not null default '',
    title_norm  text not null default '',
    issns       text[] not null default '{}',
    sjr         double precision,
    quartile    text,                          -- best quartile across the journal's categories
    categories  jsonb not null default '[]',   -- [{"name": ..., "quartile": "Q1"}]
    h_index     integer,
    type        text,
    publisher   text,
    country     text,
    coverage    text,
    year        text,
    batch       text not null default '',
    primary key (user_id, key)
);

create index if not exists ref_sjr_issns on public.ref_sjr using gin (issns);
create index if not exists ref_sjr_title on public.ref_sjr (user_id, title_norm);

alter table public.ref_sjr enable row level security;
drop policy if exists "own ref_sjr" on public.ref_sjr;
create policy "own ref_sjr" on public.ref_sjr for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

revoke all on public.ref_sjr from anon;
grant select, insert, update, delete on public.ref_sjr to authenticated;
