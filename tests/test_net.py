"""HTTP client behaviour, with a stubbed session instead of real requests."""

import json

import pytest
import requests

from bibformatter.config import load_config
from bibformatter.net import HttpClient, ResponseCache


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text if text else json.dumps(payload or {})

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Replays a scripted list of responses/exceptions and counts calls."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        item = self.script.pop(0) if self.script else FakeResponse(200, {"ok": True})
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


@pytest.fixture
def client(monkeypatch):
    config = load_config()
    config["network"]["cache_path"] = None
    config["network"]["min_interval"] = 0
    config["network"]["host_intervals"] = {}
    config["network"]["backoff_factor"] = 1.0
    config["network"]["backoff_max"] = 0.001
    # Backoff sleeps are the point of the retry logic, not of these tests.
    monkeypatch.setattr("bibformatter.net.time.sleep", lambda _s: None)
    return HttpClient(config)


class TestRetries:
    def test_returns_payload_on_success(self, client):
        client.session = FakeSession([FakeResponse(200, {"hello": "world"})])
        assert client.get_json("https://example.org/a") == {"hello": "world"}

    def test_retries_on_429_then_succeeds(self, client):
        client.session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(200, {"ok": True}),
        ])
        assert client.get_json("https://example.org/b") == {"ok": True}
        assert client.session.calls == 2
        assert client.stats["retries"] == 1

    def test_retries_on_5xx(self, client):
        client.session = FakeSession([
            FakeResponse(503), FakeResponse(502), FakeResponse(200, {"ok": True})
        ])
        assert client.get_json("https://example.org/c") == {"ok": True}

    def test_retries_on_connection_error(self, client):
        client.session = FakeSession([
            requests.ConnectionError("reset"), FakeResponse(200, {"ok": True})
        ])
        assert client.get_json("https://example.org/d") == {"ok": True}

    def test_retries_on_timeout(self, client):
        client.session = FakeSession([
            requests.Timeout("slow"), FakeResponse(200, {"ok": True})
        ])
        assert client.get_json("https://example.org/e") == {"ok": True}

    def test_gives_up_and_returns_none(self, client):
        client.session = FakeSession([FakeResponse(500)] * 10)
        assert client.get_json("https://example.org/f") is None
        # max_retries + the initial attempt.
        assert client.session.calls == client.max_retries + 1
        assert client.stats["failures"] == 1

    def test_never_raises_on_persistent_failure(self, client):
        client.session = FakeSession([requests.ConnectionError("nope")] * 10)
        assert client.get_json("https://example.org/g") is None

    def test_404_is_not_retried(self, client):
        client.session = FakeSession([FakeResponse(404), FakeResponse(200, {"x": 1})])
        assert client.get_json("https://example.org/h") is None
        assert client.session.calls == 1

    def test_non_json_body_is_handled(self, client):
        client.session = FakeSession([FakeResponse(200, payload=None, text="<html>")])
        assert client.get_json("https://example.org/i") is None

    def test_honours_retry_after_header(self, client, monkeypatch):
        slept = []
        monkeypatch.setattr("bibformatter.net.time.sleep", lambda s: slept.append(s))
        client.backoff_max = 60.0  # the fixture clamps to ~0 for speed
        client.session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(200, {"ok": True}),
        ])
        client.get_json("https://example.org/j")
        assert any(7 <= s <= 8 for s in slept)  # 7s plus jitter

    def test_absurd_retry_after_is_capped(self, client, monkeypatch):
        # A server asking us to wait an hour must not hang the run.
        slept = []
        monkeypatch.setattr("bibformatter.net.time.sleep", lambda s: slept.append(s))
        client.backoff_max = 60.0
        client.session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "3600"}),
            FakeResponse(200, {"ok": True}),
        ])
        client.get_json("https://example.org/l")
        assert all(s <= 66 for s in slept)

    def test_handles_http_date_retry_after(self, client):
        # A non-numeric Retry-After must fall back to our own backoff, not crash.
        client.session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            FakeResponse(200, {"ok": True}),
        ])
        assert client.get_json("https://example.org/k") == {"ok": True}


class TestCircuitBreaker:
    def test_opens_after_repeated_failures(self, client):
        client.circuit_threshold = 2
        client.session = FakeSession([requests.ConnectionError("x")] * 100)
        for path in ("a", "b", "c", "d"):
            client.get_json(f"https://dead.example.org/{path}")
        assert "dead.example.org" in client.dead_hosts

    def test_skips_requests_once_open(self, client):
        client.circuit_threshold = 1
        client.session = FakeSession([requests.ConnectionError("x")] * 100)
        client.get_json("https://dead.example.org/a")
        calls_after_first = client.session.calls
        client.get_json("https://dead.example.org/b")
        # No further requests were attempted.
        assert client.session.calls == calls_after_first
        assert client.stats["skipped"] == 1

    def test_other_hosts_are_unaffected(self, client):
        client.circuit_threshold = 1
        client.session = FakeSession([requests.ConnectionError("x")] * 10)
        client.get_json("https://dead.example.org/a")
        client.session = FakeSession([FakeResponse(200, {"ok": True})])
        assert client.get_json("https://alive.example.org/a") == {"ok": True}

    def test_rate_limiting_does_not_count_as_success(self, client):
        # A 429 is a refusal. If it reset the failure count, a host that
        # throttles us relentlessly would never trip the breaker.
        client.circuit_threshold = 2
        client.session = FakeSession([FakeResponse(429)] * 100)
        client.get_json("https://throttled.example.org/a")
        client.get_json("https://throttled.example.org/b")
        assert "throttled.example.org" in client.dead_hosts

    def test_success_resets_the_failure_count(self, client):
        client.circuit_threshold = 3
        client.session = FakeSession([
            requests.ConnectionError("x"), FakeResponse(200, {"ok": True}),
        ] * 5)
        for path in "abcde":
            client.get_json(f"https://flaky.example.org/{path}")
        assert client.dead_hosts == []


class TestCache:
    def test_caches_successful_responses(self, client):
        client.session = FakeSession([FakeResponse(200, {"v": 1})])
        first = client.get_json("https://example.org/cached")
        second = client.get_json("https://example.org/cached")
        assert first == second == {"v": 1}
        assert client.session.calls == 1
        assert client.stats["cache_hits"] == 1

    def test_caches_misses_so_they_are_not_re_requested(self, client):
        client.session = FakeSession([FakeResponse(404)])
        assert client.get_json("https://example.org/gone") is None
        assert client.get_json("https://example.org/gone") is None
        assert client.session.calls == 1

    def test_different_params_are_cached_separately(self, client):
        client.session = FakeSession([
            FakeResponse(200, {"v": 1}), FakeResponse(200, {"v": 2})
        ])
        a = client.get_json("https://example.org/x", params={"q": "a"})
        b = client.get_json("https://example.org/x", params={"q": "b"})
        assert a != b

    def test_cache_survives_a_round_trip_to_disk(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = ResponseCache(str(path), ttl_days=90)
        cache.set("k", {"v": 1})
        cache.save()
        assert ResponseCache(str(path), ttl_days=90).get("k") == {"v": 1}

    def test_expired_entries_are_ignored(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = ResponseCache(str(path), ttl_days=0)
        cache.set("k", {"v": 1})
        cache.save()
        # ttl_days=0 disables expiry; a negative TTL forces everything stale.
        stale = ResponseCache(str(path), ttl_days=90)
        stale.ttl = -1
        assert stale.get("k") is None

    def test_corrupt_cache_file_is_ignored(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text("{not valid json")
        assert ResponseCache(str(path)).get("anything") is None


class TestOffline:
    def test_disabled_client_makes_no_requests(self):
        config = load_config()
        config["network"]["enabled"] = False
        config["network"]["cache_path"] = None
        client = HttpClient(config)
        client.session = FakeSession([FakeResponse(200, {"v": 1})])
        assert client.get_json("https://example.org/a") is None
        assert client.session.calls == 0


class TestRateLimiting:
    def test_per_host_interval_overrides_the_default(self):
        config = load_config()
        config["network"]["cache_path"] = None
        config["network"]["min_interval"] = 0.25
        config["network"]["host_intervals"] = {"slow.example.org": 3.0}
        client = HttpClient(config)
        assert client._interval_for("slow.example.org") == 3.0
        assert client._interval_for("other.example.org") == 0.25

    def test_subdomains_inherit_the_override(self):
        config = load_config()
        config["network"]["cache_path"] = None
        config["network"]["host_intervals"] = {"example.org": 2.0}
        client = HttpClient(config)
        assert client._interval_for("api.example.org") == 2.0
