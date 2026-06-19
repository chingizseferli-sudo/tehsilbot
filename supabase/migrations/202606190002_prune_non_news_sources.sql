create or replace function public.cleanup_source_domain(value text)
returns text
language sql
immutable
as $$
  select nullif(
    regexp_replace(
      split_part(
        regexp_replace(lower(coalesce(value, '')), '^https?://(www\.)?', ''),
        '/',
        1
      ),
      ':\d+$',
      ''
    ),
    ''
  );
$$;

create table if not exists public.source_cleanup_archive as
select
  s.*,
  now()::timestamptz as archived_at,
  ''::text as cleanup_reason
from public.sources s
where false;

with flagged_sources as (
  select
    s.id,
    coalesce(r.reason, s.notes, s.last_result, 'non_news_or_inactive_source') as cleanup_reason
  from public.sources s
  left join public.rejected_sources r
    on r.domain = public.cleanup_source_domain(coalesce(s.base_url, s.latest_url, s.rss_url))
  where
    coalesce(r.reason, '') ilike any (array[
      '%commercial_site_not_news%',
      '%insufficient_news_activity%',
      '%rejected_commercial%',
      '%rejected_inactive_news%'
    ])
    or coalesce(s.notes, '') ilike any (array[
      '%commercial_site_not_news%',
      '%insufficient_news_activity%',
      '%commercial site signals%',
      '%activity too low%'
    ])
    or coalesce(s.last_result, '') ilike any (array[
      '%commercial_site_not_news%',
      '%insufficient_news_activity%'
    ])
),
archived as (
  insert into public.source_cleanup_archive
  select
    s.*,
    now()::timestamptz as archived_at,
    left(f.cleanup_reason, 1000) as cleanup_reason
  from public.sources s
  join flagged_sources f on f.id = s.id
  returning id
),
deleted as (
  delete from public.sources s
  using flagged_sources f
  where s.id = f.id
    and not exists (
      select 1
      from public.monitored_items item
      where item.source_id = s.id
    )
  returning s.id
),
deactivated as (
  update public.sources s
  set
    status = 'inactive',
    discovery_status = 'rejected',
    notes = left(
      concat_ws(
        '; ',
        nullif(s.notes, ''),
        'cleanup: non-news/inactive source',
        f.cleanup_reason
      ),
      1000
    )
  from flagged_sources f
  where s.id = f.id
    and not exists (select 1 from deleted d where d.id = s.id)
  returning s.id
)
select
  (select count(*) from flagged_sources) as flagged_sources,
  (select count(*) from deleted) as deleted_sources,
  (select count(*) from deactivated) as deactivated_sources;
