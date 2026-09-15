"""Bounded, in-memory aggregates of completed proxy requests; no traffic content."""

import math
import time


class TrafficMetrics:
    window_seconds = 60

    def __init__(self):
        self.buckets = {}
        self.requests = 0
        self.errors = 0
        self.response_bytes = 0
        self.duration_ms = 0.0

    def record(self, entry, now=None):
        """Only allowlisted numeric fields survive access-log parsing."""
        status, duration, size = (entry.get(key) for key in ("status", "duration", "size"))
        if (
            type(status) is not int
            or not 100 <= status <= 599
            or type(duration) not in (int, float)
            or not math.isfinite(duration)
            or duration < 0
            or type(size) is not int
            or size < 0
        ):
            return
        second = int(time.time() if now is None else now)
        self._prune(second)
        bucket = self.buckets.setdefault(second, {"requests": 0, "duration_ms": 0.0})
        bucket["requests"] += 1
        bucket["duration_ms"] += duration * 1000
        self.requests += 1
        self.errors += status >= 400
        self.response_bytes += size
        self.duration_ms += duration * 1000

    def _prune(self, now):
        self.buckets = {
            key: value for key, value in self.buckets.items() if key >= now - self.window_seconds
        }

    def snapshot(self, now=None):
        now = int(time.time() if now is None else now)
        self._prune(now)
        samples = []
        # Exclude the current partial second so request rates are comparable.
        for second in range(now - self.window_seconds, now):
            bucket = self.buckets.get(second, {})
            count = bucket.get("requests", 0)
            samples.append(
                {
                    "time": second,
                    "requests": count,
                    "duration_ms": round(bucket["duration_ms"] / count, 2) if count else None,
                }
            )
        return {
            "window_seconds": self.window_seconds,
            "samples": samples,
            "requests": self.requests,
            "errors": self.errors,
            "response_bytes": self.response_bytes,
            "avg_duration_ms": round(self.duration_ms / self.requests, 2)
            if self.requests
            else None,
        }
