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


def _prices(day_values: list[float]) -> list[dict]:
    return [{"timestamp": i * DAY, "price": v} for i, v in enumerate(day_values)]


def _iso(day: int) -> str:
    return f"1970-01-{day + 1:02d}T00:00:00+00:00"


def test_simulate_rebalanced_scenario_falls_back_to_buy_and_hold_without_history(monkeypatch):
    """A basket with no rebalance events yet has nothing to schedule
    against, so the scenario is plain buy-and-hold — same as HODL's math."""
    monkeypatch.setattr(
        app,
        "cached_get_price_history",
        lambda price_ref, days: _prices([100.0, 110.0]) if price_ref == "A" else _prices([100.0, 90.0]),
    )

    scenario_assets = [
        {"asset": "A", "weight_pct": 80.0, "price_ref": "A"},
        {"asset": "B", "weight_pct": 20.0, "price_ref": "B"},
    ]
    current_assets = [
        {"asset": "A", "weight_pct": 50.0, "price_ref": "A"},
        {"asset": "B", "weight_pct": 50.0, "price_ref": "B"},
    ]

    points, excluded = app.simulate_rebalanced_scenario(scenario_assets, current_assets, [])
    buy_and_hold, _ = app.simulate_weighted_performance(scenario_assets)

    assert excluded == []
    assert points == buy_and_hold


def test_simulate_rebalanced_scenario_reweights_at_each_real_rebalance(monkeypatch):
    """The scenario's sliders are a tilt on the current real weights (here,
    2x on A); that tilt must be re-applied at every real historical
    rebalance, not just held fixed from the first day — a basket that
    really shifted from 50/50 to 30/70 must be simulated shifting too,
    scaled by the tilt, instead of the scenario just buying and holding.
    """
    monkeypatch.setattr(
        app,
        "cached_get_price_history",
        lambda price_ref, days: (
            _prices([100.0, 110.0, 121.0, 133.1, 146.41])
            if price_ref == "A"
            else _prices([100.0, 90.0, 81.0, 72.9, 65.61])
        ),
    )

    current_assets = [
        {"asset": "A", "weight_pct": 50.0, "price_ref": "A"},
        {"asset": "B", "weight_pct": 50.0, "price_ref": "B"},
    ]
    scenario_assets = [
        {"asset": "A", "weight_pct": 80.0, "price_ref": "A"},
        {"asset": "B", "weight_pct": 20.0, "price_ref": "B"},
    ]
    rebalance_history = [
        {
            "date": _iso(0),
            "weights_after": [
                {"asset": "A", "weight_pct": 50.0, "price_ref": "A"},
                {"asset": "B", "weight_pct": 50.0, "price_ref": "B"},
            ],
        },
        {
            "date": _iso(2),
            "weights_after": [
                {"asset": "A", "weight_pct": 30.0, "price_ref": "A"},
                {"asset": "B", "weight_pct": 70.0, "price_ref": "B"},
            ],
        },
    ]

    points, excluded = app.simulate_rebalanced_scenario(
        scenario_assets, current_assets, rebalance_history, days=5
    )

    assert excluded == []
    by_date = {p["date"][:10]: p["percent_change"] for p in points}
    assert by_date["1970-01-01"] == pytest.approx(0.0)
    # First segment: tilted 80/20 (from the real 50/50 rebalance) — matches
    # a plain 80/20 buy-and-hold over these two days.
    assert by_date["1970-01-02"] == pytest.approx(6.0)
    # Second segment kicks in on day 2: the real weights shifted to 30/70,
    # tilted to ~63.2/36.8 — a different mix than the still-80/20 scenario
    # sliders alone would imply, chained onto the first segment's result.
    assert by_date["1970-01-04"] == pytest.approx(8.789473684, rel=1e-6)
