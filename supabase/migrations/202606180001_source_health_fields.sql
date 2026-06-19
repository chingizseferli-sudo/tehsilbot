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

create or replace function public.increment_source_fail(
  p_source_id uuid,
  p_reason text default 'site_error'
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.sources
  set
    last_checked_at = now(),
    last_result = coalesce(nullif(p_reason, ''), 'site_error'),
    last_error = coalesce(nullif(p_reason, ''), 'site_error'),
    consecutive_fail_count = coalesce(consecutive_fail_count, 0) + 1
  where id = p_source_id;
end;
$$;

grant execute on function public.increment_source_fail(uuid, text) to service_role;
