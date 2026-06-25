import importlib.util
import os
import sys
import types


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


class _FakeResponse:
    status_code = 200
    text = "[]"

    def json(self):
        return []


class _FakeRequests:
    def get(self, *_args, **_kwargs):
        return _FakeResponse()


def assert_same(left, right):
    assert site_monitor.normalize_link(left) == site_monitor.normalize_link(right), (
        left,
        right,
        site_monitor.normalize_link(left),
        site_monitor.normalize_link(right),
    )


def assert_different(left, right):
    assert site_monitor.normalize_link(left) != site_monitor.normalize_link(right), (
        left,
        right,
        site_monitor.normalize_link(left),
        site_monitor.normalize_link(right),
    )


def main():
    assert_same("HTTPS://WWW.Example.COM/News/Item", "https://example.com/News/Item")
    assert_same("https://example.com/News/Item/", "https://example.com/News/Item")
    assert_same("http://example.com:80/a", "http://example.com/a")
    assert_same("https://example.com:443/a", "https://example.com/a")
    assert_same(
        "https://example.com/news/item?utm_source=x&fbclid=y&id=5",
        "https://example.com/news/item?id=5",
    )
    assert_same("https://example.com/news/item#section", "https://example.com/news/item")

    assert_different("https://example.com/News/Item", "https://example.com/news/item")
    assert_different("https://example.com/news?id=ABC", "https://example.com/news?id=abc")
    assert_different("https://example.com/news?id=5", "https://example.com/news?id=6")

    site_monitor.supabase_ready = lambda: True
    site_monitor.SUPABASE_URL = "https://supabase.test"
    site_monitor.SUPABASE_SERVICE_ROLE_KEY = "service-key"
    site_monitor.requests = _FakeRequests()
    assert site_monitor.exists("https://example.com/news/a", "Repeated title") is False
    assert site_monitor.exists("https://example.com/news/b", "Repeated title") is False

    print("url dedup fixtures ok")


if __name__ == "__main__":
    main()
