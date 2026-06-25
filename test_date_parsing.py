import importlib.util
import os
import sys
import types
from datetime import datetime, timedelta


os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "")



if "requests" not in sys.modules:
    requests = types.ModuleType("requests")

    class _RequestException(Exception):
        pass

    class _Timeout(_RequestException):
        pass

    class _SSLError(_RequestException):
        pass

    class _ConnectionError(_RequestException):
        pass

    requests.Timeout = _Timeout
    requests.exceptions = types.SimpleNamespace(
        SSLError=_SSLError,
        ConnectionError=_ConnectionError,
        RequestException=_RequestException,
    )
    requests.get = lambda *_args, **_kwargs: None
    requests.post = lambda *_args, **_kwargs: None
    requests.patch = lambda *_args, **_kwargs: None
    sys.modules["requests"] = requests

if "feedparser" not in sys.modules:
    feedparser = types.ModuleType("feedparser")
    feedparser.parse = lambda *_args, **_kwargs: types.SimpleNamespace(entries=[], bozo=False)
    sys.modules["feedparser"] = feedparser


if "bs4" not in sys.modules:
    bs4 = types.ModuleType("bs4")

    class _BeautifulSoup:
        def __init__(self, *_args, **_kwargs):
            pass

        def find(self, *_args, **_kwargs):
            return None

        def find_all(self, *_args, **_kwargs):
            return []

        def select(self, *_args, **_kwargs):
            return []

    bs4.BeautifulSoup = _BeautifulSoup
    sys.modules["bs4"] = bs4


spec = importlib.util.spec_from_file_location("site_monitor", "site_monitor.py")
site_monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_monitor)


def assert_status(label, title, article_time, expected):
    published_time, status = site_monitor.evaluate_publish_freshness(title, article_time)
    assert status == expected, f"{label}: expected {expected}, got {status}, published={published_time}"
    return published_time


def main():
    now = datetime.now(site_monitor.BAKU_TZ)
    fresh = now - timedelta(minutes=10)
    old = now - timedelta(days=14)
    future = now + timedelta(days=1)

    fixtures = [
        ("RSS ISO fresh", "", fresh.isoformat(), "fresh"),
        ("RSS RFC fresh", "", fresh.strftime("%a, %d %b %Y %H:%M:%S +0400"), "fresh"),
        ("Azerbaijani month fresh", f"DIM xəbəri {fresh.day} İyun {fresh.year} {fresh.hour:02d}:{fresh.minute:02d}", "", "fresh"),
        ("DIM numeric fresh", f"{fresh.day:02d}.{fresh.month:02d}.{fresh.year} {fresh.hour:02d}:{fresh.minute:02d}", "", "fresh"),
        ("EDU old", "EDU elan", old.strftime("%d.%m.%Y %H:%M"), "old_news"),
        ("APA future", "APA xəbər", future.strftime("%d.%m.%Y %H:%M"), "future_date"),
        ("Report missing", "Report başlıq", "", "no_date"),
        ("Azertac failed date", "Azertac 32 İyun 2026 xəbər", "", "date_parse_failed"),
        ("University short month", f"Universitet xəbəri İyn {fresh.day}, {fresh.year} | {fresh.hour:02d}:{fresh.minute:02d}", "", "fresh"),
        ("Time-only relative same day", f"{fresh.hour:02d}:{fresh.minute:02d} Tələbə qəbulu ilə bağlı yenilik", "", "fresh"),
    ]

    for label, title, article_time, expected in fixtures:
        assert_status(label, title, article_time, expected)

    assert site_monitor.parse_datetime_to_baku("Sun, 11 Aug 2024 20:00:00 GMT")
    assert site_monitor.parse_datetime_to_baku("12 İyun 2026, 17:41")
    assert site_monitor.parse_datetime_to_baku("2026-06-11T15:50:00+04:00")

    print("date fixtures ok")


if __name__ == "__main__":
    main()
