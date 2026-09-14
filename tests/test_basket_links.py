"""Tests for each adapter's basket_platform_url/basket_explorer_url."""

from adapters import glider, quantamm, reserve


def test_glider_platform_url_uses_the_strategy_id_as_is():
    url = glider.basket_platform_url("01KV68M2Y685X59DWAEVX5D5X3")
    assert url == "https://glider.fi/strategy/01KV68M2Y685X59DWAEVX5D5X3"


def test_glider_explorer_url_is_always_none():
    """A Glider strategy_id is an opaque ULID, not an on-chain address —
    there's no block explorer page for it."""
    assert glider.basket_explorer_url("01KV68M2Y685X59DWAEVX5D5X3") is None


def test_reserve_platform_url_maps_mainnet_to_ethereum():
    """Reserve's own app routes by "ethereum", not the "mainnet" key
    basket_id carries — confirmed against the live site."""
    url = reserve.basket_platform_url(
        "mainnet:0x323c03c48660fe31186fa82c289b0766d331ce21"
    )
    assert url == (
        "https://app.reserve.org/ethereum/index-dtf/"
        "0x323c03c48660fe31186fa82c289b0766d331ce21/overview"
    )


def test_reserve_platform_url_keeps_base_chain_name():
    url = reserve.basket_platform_url("base:0x4da9a0f397db1397902070f93a4d6ddbc0e0e6e8")
    assert url == (
        "https://app.reserve.org/base/index-dtf/"
        "0x4da9a0f397db1397902070f93a4d6ddbc0e0e6e8/overview"
    )


def test_reserve_explorer_url_points_to_the_dtf_contract():
    url = reserve.basket_explorer_url(
        "mainnet:0x323c03c48660fe31186fa82c289b0766d331ce21"
    )
    assert url == "https://etherscan.io/token/0x323c03c48660fe31186fa82c289b0766d331ce21"


def test_reserve_urls_are_none_for_an_unparseable_basket_id():
    assert reserve.basket_platform_url("not-a-valid-id") is None
    assert reserve.basket_explorer_url("not-a-valid-id") is None


def test_quantamm_platform_url_lowercases_the_chain_for_balancer():
    """basket_id carries an uppercase chain ("MAINNET"); Balancer's own
    site uses lowercase DefiLlama-style slugs."""
    url = quantamm.basket_platform_url(
        "MAINNET:0x6b61d8680c4f9e560c8306807908553f95c749c5"
    )
    assert url == "https://balancer.fi/pools/ethereum/v3/0x6b61d8680c4f9e560c8306807908553f95c749c5"


def test_quantamm_platform_url_supports_sonic():
    url = quantamm.basket_platform_url(
        "SONIC:0x74dc857d5567a3b087e79b96b91cdc8099b2fa34"
    )
    assert url == "https://balancer.fi/pools/sonic/v3/0x74dc857d5567a3b087e79b96b91cdc8099b2fa34"


def test_quantamm_explorer_url_points_to_the_pool_contract():
    url = quantamm.basket_explorer_url(
        "SONIC:0x74dc857d5567a3b087e79b96b91cdc8099b2fa34"
    )
    assert url == "https://sonicscan.org/token/0x74dc857d5567a3b087e79b96b91cdc8099b2fa34"


def test_quantamm_urls_are_none_for_an_unparseable_basket_id():
    assert quantamm.basket_platform_url("not-a-valid-id") is None
    assert quantamm.basket_explorer_url("not-a-valid-id") is None
