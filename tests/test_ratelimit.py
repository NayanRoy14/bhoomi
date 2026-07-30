"""Rate limiting tests.

The security-relevant case is X-Forwarded-For: honouring a client-supplied
header would let anyone reset their own limit, so most of these tests are about
when the header is *not* trusted.
"""

import time

import pytest
from fastapi.testclient import TestClient

from backend.api import ratelimit
from backend.api.deps import get_catalogue
from backend.api.main import app
from tests.test_api import StubCatalogue, body


@pytest.fixture(autouse=True)
def restore_limiter():
    original = ratelimit.get_limiter()
    original_hops = ratelimit.TRUSTED_PROXY_HOPS
    yield
    ratelimit.set_limiter(original)
    ratelimit.TRUSTED_PROXY_HOPS = original_hops
    original.reset()
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    app.dependency_overrides[get_catalogue] = lambda: StubCatalogue([])
    return TestClient(app)


class TestSlidingWindow:
    def test_allows_up_to_the_limit(self):
        limiter = ratelimit.SlidingWindowLimiter(limit=3, window=60)
        assert [limiter.check("a")[0] for _ in range(3)] == [True, True, True]

    def test_blocks_beyond_the_limit(self):
        limiter = ratelimit.SlidingWindowLimiter(limit=2, window=60)
        limiter.check("a")
        limiter.check("a")
        allowed, retry_after = limiter.check("a")
        assert allowed is False
        assert 0 < retry_after <= 61

    def test_keys_are_independent(self):
        limiter = ratelimit.SlidingWindowLimiter(limit=1, window=60)
        assert limiter.check("a")[0] is True
        assert limiter.check("b")[0] is True
        assert limiter.check("a")[0] is False

    def test_window_expiry_frees_the_budget(self):
        limiter = ratelimit.SlidingWindowLimiter(limit=1, window=1)
        assert limiter.check("a")[0] is True
        assert limiter.check("a")[0] is False
        time.sleep(1.1)
        assert limiter.check("a")[0] is True

    def test_no_burst_across_a_boundary(self):
        """A fixed window would allow 2x the limit at the boundary; a sliding
        window log does not."""
        limiter = ratelimit.SlidingWindowLimiter(limit=2, window=2)
        assert limiter.check("a")[0] is True
        assert limiter.check("a")[0] is True
        time.sleep(1.0)
        # Still inside the 2 s window of both earlier hits.
        assert limiter.check("a")[0] is False

    def test_idle_keys_are_pruned(self):
        limiter = ratelimit.SlidingWindowLimiter(limit=5, window=1)
        for i in range(50):
            limiter.check(f"ip-{i}")
        assert len(limiter._hits) == 50
        time.sleep(1.1)
        limiter.check("trigger-prune")
        assert len(limiter._hits) == 1, "idle keys should not accumulate forever"

    def test_reset(self):
        limiter = ratelimit.SlidingWindowLimiter(limit=1, window=60)
        limiter.check("a")
        limiter.reset()
        assert limiter.check("a")[0] is True


class TestClientIdentification:
    def _request(self, headers=None, peer="203.0.113.9"):
        class FakeClient:
            host = peer

        class FakeRequest:
            def __init__(self):
                self.headers = headers or {}
                self.client = FakeClient()

        return FakeRequest()

    def test_uses_socket_peer_by_default(self, monkeypatch):
        monkeypatch.setattr(ratelimit, "TRUSTED_PROXY_HOPS", 0)
        request = self._request({"x-forwarded-for": "1.2.3.4"})
        assert ratelimit.client_key(request) == "203.0.113.9"

    def test_spoofed_forwarded_header_is_ignored_without_a_proxy(self, monkeypatch):
        """Otherwise a caller resets their own limit with one header."""
        monkeypatch.setattr(ratelimit, "TRUSTED_PROXY_HOPS", 0)
        keys = {
            ratelimit.client_key(self._request({"x-forwarded-for": f"10.0.0.{i}"}))
            for i in range(5)
        }
        assert keys == {"203.0.113.9"}

    def test_honours_forwarded_header_behind_one_trusted_proxy(self, monkeypatch):
        monkeypatch.setattr(ratelimit, "TRUSTED_PROXY_HOPS", 1)
        request = self._request({"x-forwarded-for": "198.51.100.7"})
        assert ratelimit.client_key(request) == "198.51.100.7"

    def test_takes_the_rightmost_entry_a_proxy_appended(self, monkeypatch):
        """Entries to the left are client-supplied and untrustworthy."""
        monkeypatch.setattr(ratelimit, "TRUSTED_PROXY_HOPS", 1)
        request = self._request({"x-forwarded-for": "evil-spoof, 198.51.100.7"})
        assert ratelimit.client_key(request) == "198.51.100.7"

    def test_falls_back_when_the_chain_is_shorter_than_expected(self, monkeypatch):
        monkeypatch.setattr(ratelimit, "TRUSTED_PROXY_HOPS", 2)
        request = self._request({"x-forwarded-for": "198.51.100.7"})
        assert ratelimit.client_key(request) == "203.0.113.9"


class TestMiddleware:
    def test_returns_429_with_retry_after(self, client):
        ratelimit.set_limiter(ratelimit.SlidingWindowLimiter(limit=2, window=60))
        client.post("/api/v1/scenes/search", json=body())
        client.post("/api/v1/scenes/search", json=body())
        response = client.post("/api/v1/scenes/search", json=body())

        assert response.status_code == 429
        assert response.json()["code"] == "rate_limited"
        assert int(response.headers["Retry-After"]) > 0

    def test_health_is_never_throttled(self, client):
        """A health check returning 429 reads as an outage."""
        ratelimit.set_limiter(ratelimit.SlidingWindowLimiter(limit=1, window=60))
        for _ in range(5):
            assert client.get("/health").status_code == 200

    def test_docs_are_not_throttled(self, client):
        ratelimit.set_limiter(ratelimit.SlidingWindowLimiter(limit=1, window=60))
        client.get("/openapi.json")
        assert client.get("/openapi.json").status_code == 200

    def test_normal_use_is_unaffected(self, client):
        ratelimit.set_limiter(ratelimit.SlidingWindowLimiter(limit=120, window=3600))
        for _ in range(10):
            assert client.post("/api/v1/scenes/search", json=body()).status_code == 200
