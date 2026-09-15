import json

import pytest

from portside.metrics import TrafficMetrics


def test_rates_durations_and_error_totals_use_completed_seconds():
    metrics = TrafficMetrics()
    metrics.record({"status": 200, "duration": 0.1, "size": 1024}, now=100)
    metrics.record({"status": 502, "duration": 0.3, "size": 100}, now=100)
    metrics.record({"status": 404, "duration": 0.05, "size": 10}, now=102)
    snapshot = metrics.snapshot(now=102)
    assert len(snapshot["samples"]) == 60
    assert snapshot["samples"][-2] == {"time": 100, "requests": 2, "duration_ms": 200}
    assert snapshot["samples"][-1] == {"time": 101, "requests": 0, "duration_ms": None}
    assert snapshot["requests"] == 3
    assert snapshot["errors"] == 2
    assert snapshot["response_bytes"] == 1134
    assert snapshot["avg_duration_ms"] == 150


def test_history_is_bounded_and_expired_buckets_do_not_appear():
    metrics = TrafficMetrics()
    for second in range(1000):
        metrics.record({"status": 200, "duration": 0.1, "size": 1}, now=second)
    assert len(metrics.buckets) <= 61
    snapshot = metrics.snapshot(now=1100)
    assert metrics.buckets == {}
    assert snapshot["requests"] == 1000
    assert all(
        point["requests"] == 0 and point["duration_ms"] is None for point in snapshot["samples"]
    )


def test_content_and_headers_never_survive_aggregation():
    metrics = TrafficMetrics()
    metrics.record(
        {
            "status": 200,
            "duration": 0.05,
            "size": 30,
            "request": {"uri": "/private", "headers": {"Cookie": "secret"}},
            "resp_headers": {"Set-Cookie": "secret"},
        },
        now=100,
    )
    result = json.dumps(metrics.snapshot(now=101))
    assert "secret" not in result and "private" not in result and "headers" not in result


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"status": True, "duration": 1, "size": 1},
        {"status": 200, "duration": float("nan"), "size": 0},
        {"status": 200, "duration": float("inf"), "size": 0},
        {"status": 200, "duration": -1, "size": 0},
        {"status": 200, "duration": 0, "size": -1},
    ],
)
def test_invalid_metrics_are_ignored(entry):
    metrics = TrafficMetrics()
    metrics.record(entry)
    assert metrics.snapshot()["requests"] == 0
