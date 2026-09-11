"""Tests for app.py's weight-scenario simulation."""

import pytest

import app

DAY = 86400


def test_simulate_weighted_performance_does_not_backfill_before_first_price(monkeypatch):
    """An asset with a shorter price history must not get its earliest known
    price smeared backward to days it has no data for — that fabricates a
    baseline the simulation never actually observed, and skews every later
    percent_change computed against it (see docs/rebalancing-simulator.md).
    """
    long_history = [
        {"timestamp": 0, "price": 100.0},
        {"timestamp": DAY, "price": 110.0},
        {"timestamp": 2 * DAY, "price": 121.0},
    ]
    short_history = [
        {"timestamp": DAY, "price": 50.0},
        {"timestamp": 2 * DAY, "price": 60.0},
    ]

    def fake_price_history(price_ref, days):
        return long_history if price_ref == "long" else short_history

    monkeypatch.setattr(app, "cached_get_price_history", fake_price_history)

    weighted_assets = [
        {"asset": "LONG", "weight_pct": 50.0, "price_ref": "long"},
        {"asset": "SHORT", "weight_pct": 50.0, "price_ref": "short"},
    ]

    points, excluded = app.simulate_weighted_performance(weighted_assets, days=3)

    assert excluded == []
    # SHORT has no price for day 0 — the simulation must start where every
    # included asset actually has data, not fabricate a day-0 baseline.
    assert len(points) == 2
    assert points[0]["percent_change"] == pytest.approx(0.0)
    assert points[1]["percent_change"] == pytest.approx(15.0)
