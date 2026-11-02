-- Journal Scope: reference lists the phone searches directly, so phone lookups
-- don't need the PC to be on. The desktop app reloads them about weekly from:
--   ref_crest   : CREST's DHET-approved journal master list (subsidy status)
--   ref_sanlic  : SANLiC's R&P agreement title list (APC coverage)
--   ref_lists   : when each list was loaded and from which file
-- Rows belong to the signed-in user (row-level security), like the other tables.
-- Run in the journal_scope Supabase project, after 20261103090000_journal_scope.sql.

-- Scopus data comes from the 'scopus' Edge Function; an earlier SCImago list table is removed.
drop table if exists public.ref_scimago;

create table if not exists public.ref_crest (
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    key         text not null,                 -- CREST journal id
    title       text not null default '',
    title_norm  text not null default '',
    variants    text,
    publisher   text,
    issn        text,
    eissn       text,
    status      text,
    reason      text,
    batch       text not null default '',
    primary key (user_id, key)
);

create table if not exists public.ref_sanlic (
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    key         text not null,
    title       text not null default '',
    title_norm  text not null default '',
    issns       text[] not null default '{}',
    oa_type     text,
    included    boolean not null default false,
    explanation text,
    subject     text,
    publisher   text,
    batch       text not null default '',
    primary key (user_id, key)
);

create table if not exists public.ref_lists (
    user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
    key          text not null,                -- 'crest' | 'sanlic'
    version      text,
    source_url   text,
    rows         integer not null default 0,
    refreshed_at timestamptz not null default now(),
    primary key (user_id, key)
);

create index if not exists ref_crest_issn on public.ref_crest (user_id, issn);
create index if not exists ref_crest_eissn on public.ref_crest (user_id, eissn);
create index if not exists ref_crest_title on public.ref_crest (user_id, title_norm);
create index if not exists ref_sanlic_issns on public.ref_sanlic using gin (issns);
create index if not exists ref_sanlic_title on public.ref_sanlic (user_id, title_norm);

alter table public.ref_crest enable row level security;
alter table public.ref_sanlic enable row level security;
alter table public.ref_lists enable row level security;

drop policy if exists "own ref_crest" on public.ref_crest;
create policy "own ref_crest" on public.ref_crest for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
drop policy if exists "own ref_sanlic" on public.ref_sanlic;
create policy "own ref_sanlic" on public.ref_sanlic for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
drop policy if exists "own ref_lists" on public.ref_lists;
create policy "own ref_lists" on public.ref_lists for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

revoke all on public.ref_crest, public.ref_sanlic, public.ref_lists from anon;
grant select, insert, update, delete on public.ref_crest, public.ref_sanlic, public.ref_lists to authenticated;
