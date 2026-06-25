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


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class FakeRequests:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def configure(fake_requests):
    site_monitor.BOT_TOKEN = "token"
    site_monitor.CHAT_ID = "100"
    site_monitor.requests = fake_requests
    site_monitor.time.sleep = lambda _seconds: None


def main():
    parsed = site_monitor.parse_telegram_response(
        FakeResponse(400, {"description": "Bad Request: chat not found"})
    )
    assert parsed["reason"] == "chat_not_found"

    parsed = site_monitor.parse_telegram_response(
        FakeResponse(400, {"parameters": {"migrate_to_chat_id": "-1002"}, "description": "migrate_to_chat_id"})
    )
    assert parsed["reason"] == "chat_migrated"
    assert parsed["migrate_to_chat_id"] == "-1002"

    fake = FakeRequests([
        FakeResponse(429, {"parameters": {"retry_after": 1}, "description": "Too Many Requests"}),
        FakeResponse(200, {"ok": True}),
    ])
    configure(fake)
    assert site_monitor.send_telegram("message", chat_id="100") is True
    assert fake.calls == 2
    assert site_monitor.TELEGRAM_LAST_ERROR == ""

    fake = FakeRequests([
        FakeResponse(403, {"description": "Forbidden: bot was blocked by the user"}),
    ])
    configure(fake)
    assert site_monitor.send_telegram("message", chat_id="100") is False
    assert fake.calls == 1
    assert site_monitor.TELEGRAM_LAST_ERROR == "bot_blocked"

    print("telegram delivery fixtures ok")


if __name__ == "__main__":
    main()
