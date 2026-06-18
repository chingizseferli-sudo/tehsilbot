alter table public.sources
  add column if not exists last_checked_at timestamptz,
  add column if not exists last_success_at timestamptz,
  add column if not exists last_article_found_at timestamptz,
  add column if not exists last_error text,
  add column if not exists consecutive_fail_count integer not null default 0,
  add column if not exists last_result text;

create index if not exists idx_sources_last_checked_at
  on public.sources (last_checked_at desc);

create index if not exists idx_sources_last_success_at
  on public.sources (last_success_at desc);
