"""Tests for adapters/reserve.py's performance source selection."""

import pytest

import adapters.reserve as reserve

BASKET_ID = "base:0xcef8db49e456f872e288e1c042f916e9ced7c781"


def test_get_performance_prefers_richer_source(monkeypatch):
    """A DTF too young for DefiLlama's index shouldn't lose to Reserve's own,
    much longer, historical series just because DefiLlama returned *something*.
    """
    sparse_defillama = [{"date": "2026-09-03T00:00:00+00:00", "percent_change": 0.0}] * 9
    rich_reserve = [{"date": "2026-07-08T00:00:00+00:00", "percent_change": 0.0}] * 181

    monkeypatch.setattr(reserve, "_performance_from_defillama", lambda *_: sparse_defillama)
    monkeypatch.setattr(
        reserve, "_performance_from_reserve_historical", lambda *_: rich_reserve
    )

    result = reserve.get_performance(BASKET_ID)

    assert result["points"] == rich_reserve
    assert "Reserve historical" in result["method"]


def test_get_performance_falls_back_when_defillama_empty(monkeypatch):
    reserve_points = [{"date": "2026-07-08T00:00:00+00:00", "percent_change": 0.0}]

    monkeypatch.setattr(reserve, "_performance_from_defillama", lambda *_: None)
    monkeypatch.setattr(
        reserve, "_performance_from_reserve_historical", lambda *_: reserve_points
    )

    result = reserve.get_performance(BASKET_ID)

    assert result["points"] == reserve_points
    assert result["method"] == "Token price (NAV proxy, via Reserve historical API)"


def test_get_performance_raises_when_both_empty(monkeypatch):
    monkeypatch.setattr(reserve, "_performance_from_defillama", lambda *_: None)
    monkeypatch.setattr(reserve, "_performance_from_reserve_historical", lambda *_: None)

    with pytest.raises(reserve.ReserveAPIError):
        reserve.get_performance(BASKET_ID)
