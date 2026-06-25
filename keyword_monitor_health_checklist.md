# Keyword Monitor Release Health Checklist

Read-only verification for Visual Monitor Release 1 / Stage 2.

## Run

```powershell
python keyword_monitor_health_report.py
```

Required env:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Optional env:

- `HEALTH_REPORT_RECENT_DAYS=7`
- `HEALTH_REPORT_STALE_HOURS=24`
- `HEALTH_REPORT_NO_ARTICLE_DAYS=7`
- `HEALTH_REPORT_TOP_LIMIT=15`

## Read-only SQL

### Source failure distribution

```sql
select last_result, count(*) as count
from sources
where status = 'active'
group by last_result
order by count desc;
```

### Last error distribution

```sql
select last_error, count(*) as count
from sources
where status = 'active'
group by last_error
order by count desc;
```

### Consecutive failure buckets

```sql
select
  case
    when coalesce(consecutive_fail_count, 0) = 0 then '0'
    when consecutive_fail_count between 1 and 2 then '1-2'
    when consecutive_fail_count between 3 and 4 then '3-4'
    else '5+'
  end as fail_bucket,
  count(*) as count
from sources
where status = 'active'
group by fail_bucket
order by fail_bucket;
```

### Old news / date skips

```sql
select last_result, count(*) as count
from sources
where status = 'active'
and last_result in ('old_news', 'no_date', 'date_parse_failed', 'future_date')
group by last_result
order by count desc;
```

### URL dedup diagnostics

```sql
select last_result, count(*) as count
from sources
where status = 'active'
and last_result in ('duplicate_url', 'db_dedup_conflict', 'duplicate')
group by last_result
order by count desc;
```

### Telegram delivery failures

```sql
select last_result, count(*) as count
from sources
where status = 'active'
and last_result in (
  'telegram_error',
  'telegram_429',
  'forbidden',
  'chat_not_found',
  'bot_blocked',
  'bad_request',
  'network_error',
  'chat_migrated'
)
group by last_result
order by count desc;
```

### Top problematic reading methods

```sql
select monitor_method, last_result, last_error, count(*) as count
from sources
where status = 'active'
and (
  last_result in (
    'rss_empty', 'invalid_xml', 'selector_empty', 'xpath_empty',
    'sitemap_empty', 'homepage_empty', 'latest_page_empty', 'fallback_empty',
    'http_403', 'http_404', 'http_429', 'timeout', 'dns_failure',
    'ssl_failure', 'unsafe_url', 'site_error'
  )
  or last_error is not null
)
group by monitor_method, last_result, last_error
order by count desc;
```

### Sources not checked recently

```sql
select id, name, base_url, latest_url, monitor_method, last_checked_at, last_result, last_error
from sources
where status = 'active'
and (last_checked_at is null or last_checked_at < now() - interval '24 hours')
order by last_checked_at nulls first;
```

### Sources with no article found recently

```sql
select id, name, base_url, latest_url, monitor_method, last_article_found_at, last_result, last_error
from sources
where status = 'active'
and (last_article_found_at is null or last_article_found_at < now() - interval '7 days')
order by last_article_found_at nulls first;
```

### Sources using fallback

```sql
select id, name, base_url, latest_url, monitor_method, last_result, notes
from sources
where status = 'active'
and notes ilike '%fallback_used=true%'
order by last_checked_at desc nulls last;
```

### Blocked / 403 sources

```sql
select id, name, base_url, latest_url, rss_url, monitor_method, last_result, last_error, consecutive_fail_count
from sources
where status = 'active'
and (last_result in ('http_403', 'blocked') or last_error in ('http_403', 'blocked'))
order by consecutive_fail_count desc nulls last;
```

## Release interpretation

- `db_dedup_conflict` should be zero. If not, inspect `sent_news` unique constraints.
- `telegram_429` can happen, but repeated growth means send rate or chat grouping needs review.
- `chat_migrated` means Telegram chat ID must be updated manually.
- `selector_empty`, `xpath_empty`, `rss_empty`, and `sitemap_empty` are repair candidates.
- Real scheduler skipping remains deferred for Release 1 unless request volume becomes unsafe.
