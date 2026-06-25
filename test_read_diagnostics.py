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


def main():
    assert site_monitor.empty_reason_for_method("rss") == "rss_empty"
    assert site_monitor.empty_reason_for_method("sitemap") == "sitemap_empty"
    assert site_monitor.empty_reason_for_method("selector") == "selector_empty"
    assert site_monitor.empty_reason_for_method("xpath") == "xpath_empty"
    assert site_monitor.empty_reason_for_method("latest_page") == "latest_page_empty"
    assert site_monitor.empty_reason_for_method("homepage") == "homepage_empty"
    assert site_monitor.empty_reason_for_method("fallback") == "fallback_empty"

    site = {}
    site_monitor.set_read_diagnostic(site, "selector_empty", "selector")
    assert site["_read_failure_reason"] == "selector_empty"
    assert site["_method_attempted"] == ["selector"]

    site_monitor.mark_read_success(site, "fallback", fallback_used=True)
    assert "_read_failure_reason" not in site
    assert site["_method_succeeded"] == "fallback"
    assert site["_fallback_used"] is True

    notes = site_monitor.merge_bot_diagnostic_notes(
        "",
        "sent",
        "fallback",
        True,
        attempted_methods=["selector", "fallback"],
        succeeded_method="fallback",
    )
    assert "method_attempted=selector,fallback" in notes
    assert "method_succeeded=fallback" in notes
    assert "fallback_used=true" in notes

    print("read diagnostics fixtures ok")


if __name__ == "__main__":
    main()
