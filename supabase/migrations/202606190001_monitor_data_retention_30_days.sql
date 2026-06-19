create or replace function public.cleanup_old_monitor_data(
  days_to_keep integer default 30
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  cutoff_time timestamptz := now() - make_interval(days => greatest(coalesce(days_to_keep, 30), 1));
begin
  delete from public.monitor_alerts alert
  using public.monitor_matches match, public.monitored_items item
  where alert.match_id = match.id
    and match.item_id = item.id
    and coalesce(item.detected_at, item.published_at, item.created_at) < cutoff_time;

  delete from public.monitor_matches match
  using public.monitored_items item
  where match.item_id = item.id
    and coalesce(item.detected_at, item.published_at, item.created_at) < cutoff_time;

  delete from public.monitored_items item
  where coalesce(item.detected_at, item.published_at, item.created_at) < cutoff_time;
end;
$$;

grant execute on function public.cleanup_old_monitor_data(integer) to service_role;
