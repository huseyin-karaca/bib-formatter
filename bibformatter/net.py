"""Resilient HTTP client.

Design rule: a network failure must never abort a run. Every request either
returns a payload or returns None after exhausting retries, and the caller
falls through to the next provider.

Handles: exponential backoff with jitter, 429 with Retry-After, 5xx, connection
errors and timeouts, per-host rate limiting, and an on-disk response cache so
re-runs cost nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import tempfile
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from bibformatter import __version__

log = logging.getLogger(__name__)

# Status codes worth trying again.
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class ResponseCache:
    """A tiny JSON-file cache of raw responses, keyed by request signature."""

    def __init__(self, path: Optional[str], ttl_days: int = 90):
        self.path = path
        self.ttl = ttl_days * 86400
        self._data: Dict[str, Any] = {}
        self._dirty = False
        self._writes = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                self._data = json.load(handle)
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable cache %s: %s", self.path, exc)
            self._data = {}

    @staticmethod
    def key(method: str, url: str, params: Optional[Dict[str, Any]]) -> str:
        blob = json.dumps([method, url, params or {}], sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
        if not entry:
            return None
        if self.ttl and time.time() - entry.get("ts", 0) > self.ttl:
            return None
        return entry.get("value")

    # Flush to disk every N writes so an interrupted run doesn't throw away
    # everything it learned; the next run resumes from the cache.
    FLUSH_EVERY = 25

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = {"ts": time.time(), "value": value}
            self._dirty = True
            self._writes += 1
            due = self._writes % self.FLUSH_EVERY == 0
        if due:
            self.save()

    def save(self) -> None:
        """Write atomically so an interrupted run can't corrupt the cache."""
        with self._lock:
            if not self.path or not self._dirty:
                return
            snapshot = dict(self._data)
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle)
            os.replace(tmp, self.path)
            with self._lock:
                self._dirty = False
        except OSError as exc:
            log.warning("could not save cache %s: %s", self.path, exc)

    def __len__(self) -> int:
        return len(self._data)


class HttpClient:
    """Rate-limited, retrying, caching HTTP GET client."""

    def __init__(self, config: Dict[str, Any]):
        net = config["network"]
        self.enabled: bool = net["enabled"]
        self.timeout: float = net["timeout"]
        self.max_retries: int = net["max_retries"]
        self.backoff_factor: float = net["backoff_factor"]
        self.backoff_max: float = net["backoff_max"]
        self.min_interval: float = net["min_interval"]
        self.host_intervals: Dict[str, float] = net.get("host_intervals") or {}
        self.mailto: Optional[str] = net.get("mailto")

        self.cache = ResponseCache(net.get("cache_path"), net.get("cache_ttl_days", 90))
        # Rate limiting is per host, with a per-host lock so a slow host can't
        # block requests to a different one.
        self._last_request: Dict[str, float] = {}
        self._host_locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._stats_lock = threading.Lock()

        # Circuit breaker: a host that keeps failing is skipped for the rest of
        # the run. Without this, one unreachable API stalls every worker behind
        # its retry backoff and a run can hang for tens of minutes.
        self.circuit_threshold: int = net.get("circuit_threshold", 5)
        self._consecutive_failures: Dict[str, int] = {}
        self._open_circuits: set = set()

        contact = f" (mailto:{self.mailto})" if self.mailto else ""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": f"bib-formatter/{__version__}{contact}",
                "Accept": "application/json",
            }
        )

        self.stats = {
            "requests": 0,
            "cache_hits": 0,
            "retries": 0,
            "failures": 0,
            "skipped": 0,
            "circuits_opened": 0,
        }

    # -- rate limiting -------------------------------------------------------

    def _lock_for(self, host: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._host_locks.get(host)
            if lock is None:
                lock = threading.Lock()
                self._host_locks[host] = lock
            return lock

    def _interval_for(self, host: str) -> float:
        for pattern, interval in self.host_intervals.items():
            if host == pattern or host.endswith("." + pattern):
                return float(interval)
        return self.min_interval

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        interval = self._interval_for(host)
        with self._lock_for(host):
            last = self._last_request.get(host)
            if last is not None:
                wait = interval - (time.monotonic() - last)
                if wait > 0:
                    time.sleep(wait)
            self._last_request[host] = time.monotonic()

    def _bump(self, name: str) -> None:
        with self._stats_lock:
            self.stats[name] += 1

    # -- circuit breaker -----------------------------------------------------

    def _circuit_open(self, host: str) -> bool:
        with self._stats_lock:
            return host in self._open_circuits

    def _record_failure(self, host: str) -> None:
        with self._stats_lock:
            count = self._consecutive_failures.get(host, 0) + 1
            self._consecutive_failures[host] = count
            if count >= self.circuit_threshold and host not in self._open_circuits:
                self._open_circuits.add(host)
                self.stats["circuits_opened"] += 1
                log.warning(
                    "%s failed %d times in a row; skipping it for the rest of "
                    "this run", host, count,
                )

    def _record_success(self, host: str) -> None:
        with self._stats_lock:
            if self._consecutive_failures.get(host):
                self._consecutive_failures[host] = 0

    @property
    def dead_hosts(self) -> list:
        with self._stats_lock:
            return sorted(self._open_circuits)

    def _sleep_for_retry(self, attempt: int, retry_after: Optional[str]) -> None:
        """Honour Retry-After when the server sends it, else exponential backoff."""
        delay = None
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = None  # HTTP-date form; fall back to our own backoff
        if delay is None:
            delay = self.backoff_factor ** attempt
        delay = min(delay, self.backoff_max)
        delay += random.uniform(0, delay * 0.1)  # jitter, avoids lockstep retries
        log.debug("backing off %.1fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)

    # -- requests ------------------------------------------------------------

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Any]:
        payload = self._get(url, params, headers, want="json")
        return payload

    def get_text(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        return self._get(url, params, headers, want="text")

    def _get(
        self,
        url: str,
        params: Optional[Dict[str, Any]],
        headers: Optional[Dict[str, str]],
        want: str,
    ) -> Optional[Any]:
        # The cache is consulted before any gate, so a previously fetched answer
        # is still served when requests are switched off. That makes a cached
        # re-run possible after a slow first pass.
        cache_key = ResponseCache.key(f"GET:{want}", url, params)
        cached = self.cache.get(cache_key)
        if cached is not None:
            self._bump("cache_hits")
            # A cached miss is stored as the sentinel below, not as None.
            return None if cached == {"__miss__": True} else cached

        if not self.enabled:
            return None

        host = urlparse(url).netloc
        if self._circuit_open(host):
            self._bump("skipped")
            return None

        for attempt in range(self.max_retries + 1):
            self._throttle(url)
            try:
                self._bump("requests")
                response = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                log.debug("%s: %s", url, exc)
                if attempt < self.max_retries:
                    self._bump("retries")
                    self._sleep_for_retry(attempt, None)
                    continue
                self._bump("failures")
                self._record_failure(host)
                log.warning("giving up on %s after %d attempts: %s", url, attempt + 1, exc)
                return None
            except requests.RequestException as exc:  # malformed URL, too many redirects
                self._bump("failures")
                log.warning("request error for %s: %s", url, exc)
                return None

            if response.status_code == 404:
                # A definitive "not here" from a healthy host — cache it so we
                # never ask twice.
                self._record_success(host)
                self.cache.set(cache_key, {"__miss__": True})
                return None

            if response.status_code in RETRY_STATUS:
                if attempt < self.max_retries:
                    self._bump("retries")
                    if response.status_code == 429:
                        log.debug("rate limited by %s", urlparse(url).netloc)
                    self._sleep_for_retry(attempt, response.headers.get("Retry-After"))
                    continue
                self._bump("failures")
                self._record_failure(host)
                log.warning(
                    "giving up on %s: HTTP %s after %d attempts",
                    url,
                    response.status_code,
                    attempt + 1,
                )
                return None

            # Only a response we can actually use counts as the host being
            # healthy. A 429 is a refusal, not a success: crediting it here
            # would reset the failure count and the breaker would never trip
            # for a host that is rate-limiting us into the ground.
            self._record_success(host)

            if not response.ok:
                log.debug("%s -> HTTP %s", url, response.status_code)
                self.cache.set(cache_key, {"__miss__": True})
                return None

            if want == "json":
                try:
                    value = response.json()
                except ValueError:
                    log.debug("%s returned non-JSON body", url)
                    self.cache.set(cache_key, {"__miss__": True})
                    return None
            else:
                value = response.text

            self.cache.set(cache_key, value)
            return value

        return None

    def close(self) -> None:
        self.cache.save()
        self.session.close()
