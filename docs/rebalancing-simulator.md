# Rebalancing simulator

This document covers the Streamlit app in `app.py` and its `adapters/`
package: how the platform/basket selection, allocation, rebalance history,
performance and weight-simulation flows work today. It describes the
feature as it currently behaves, not how it was built.

## What this is

A read-only Streamlit MVP that compares real on-chain basket rebalancing
across three platforms — Glider, Reserve Protocol, QuantAMM/Balancer — side
by side, and lets the user re-simulate performance under different asset
weights. Invariants:

- Each platform is accessed through an **adapter** (`adapters/glider.py`,
  `adapters/reserve.py`, `adapters/quantamm.py`) that implements the exact
  same function signatures, so `app.py` never branches on platform beyond
  picking which adapter module to call.
- The app never invents data. Any value it can't obtain from a source (a
  missing TVL, a missing historical price, a missing rebalance) is either
  omitted, shown as `—`, or raised as a readable error — never estimated or
  faked.
- The underlying assets and REAL performance curves are **not** comparable
  across platforms (different asset classes, different weight semantics).
  The one metric that is comparable is who decides a rebalance and how
  often it happens — shown in a dedicated card.

## Surface

`app.py` is the entrypoint (`streamlit run app.py`), single page, no
routing. Its behavior is driven entirely by sidebar selections. The sidebar
acts as the control panel (platform and basket pickers); the main area
shows a compact console header (platform badge, basket id) followed by four
tabs — Allocation, History, Performance, Protocol — one per data block
described below. `.streamlit/config.toml` sets the base dark theme;
`ui/theme.py` layers app-level CSS (header, badges, spacing) and a shared
Plotly layout/colorway applied to every chart via `theme.apply_chart_theme`.
Neither file touches data or adapter logic.

### Adapter interface (implemented identically by all three adapters)

| Function | Returns |
|---|---|
| `get_current_allocation(basket_id)` | `list[{"asset": str, "weight_pct": float, "price_ref": str \| None}]` |
| `get_rebalance_history(basket_id)` | `list[{"date": str \| None, "description": str, "weights_after": list[{"asset","weight_pct"}]}]` |
| `get_performance(basket_id)` | `{"method": str \| None, "points": list[{"date": str, "percent_change": float \| None}]}` |
| `get_tvl_usd_defillama()` | `float \| None` — **absent on `glider`**, present on `reserve` and `quantamm` |

Each adapter module also exports:

- `EXAMPLE_BASKETS: dict[str, str]` — label → `basket_id`, used to populate the sidebar dropdown.
- `DECISION_MAKER: str` — one sentence, shown in the "who decides" card.
- An exception class (`GliderAPIError`, `ReserveAPIError`, `QuantAMMAPIError`), all subclasses of `Exception` with a readable `str()`.

Glider additionally exposes `discover_strategies(collection="curated", sort=None, limit=50) -> list[dict]` (used only for TVL/wallet-count lookup, not for the basket selector) and the lower-level `get_target_allocation` / `get_version_history` that `get_current_allocation` / `get_rebalance_history` wrap.

`adapters/pricing.py` exposes `get_price_history(price_ref, days=180) -> list[{"timestamp": int, "price": float}]` and `caip19_to_price_ref(asset_id) -> str | None`, shared by all three adapters and by `app.py`'s simulation.

### `basket_id` format per platform

| Platform | Format | Example |
|---|---|---|
| Glider | `strategyId` (ULID) | `01KV68M2Y685X59DWAEVX5D5X3` |
| Reserve Protocol | `<chain>:<DTF address>`, chain ∈ `{mainnet, base, bsc}` | `mainnet:0x323c...` |
| QuantAMM/Balancer | `<CHAIN>:<pool address>`, chain uppercase (Balancer's `GqlChain` enum) | `MAINNET:0x6b61...` |

## Data shapes

`weight_pct` is always a **0–100 float**, already normalized per basket by
the adapter (never a 0–1 fraction, even though some upstream APIs use one —
e.g. QuantAMM's `poolTokens[].weight` is 0–1 and gets multiplied by 100
before it leaves the adapter).

`price_ref`, when present, is a DefiLlama coin key: `"<chain-slug>:<address>"`
(e.g. `"ethereum:0xabc..."`). It is `None` whenever the asset couldn't be
mapped to a chain/address DefiLlama recognizes — this is expected and
common, not an error.

`get_performance` points use `percent_change` as **accumulated return since
the first point**, not period-over-period return. `date` is an ISO 8601
string; for Reserve and QuantAMM it always carries a UTC offset, for Glider
it's whatever the API returns as-is.

Simulated performance points (built by `app.py`'s
`simulate_weighted_performance`) use the same `{"date", "percent_change"}`
shape so both series can be concatenated into one chart.

## Rules and behavior

**Allocation sliders.** On first render for a given (platform, basket,
asset) combination, the slider is seeded from the real `weight_pct`
(rounded to 1 decimal) and cached in `st.session_state` under key
`w::{platform}::{basket_id}::{asset}` — moving other sliders doesn't reset
it. "Reset to real weights" overwrites every slider's session-state entry
back to the real values. Whatever the raw slider values sum to, they are
renormalized to sum to 100% before being used anywhere else (chart, table,
simulation); if every slider is at 0, normalization is skipped and the raw
(all-zero) weights are used as-is.

**Weighted simulation.** For each asset with a `price_ref`, its full daily
price history (`SIMULATION_DAYS` = 180 days) is fetched, forward/backward
filled to align dates across assets, and normalized so day 0 = 1.0. The
simulated index is the weight-averaged sum of the normalized series, using
weights renormalized **only across the included assets** (i.e. an asset
with no price data doesn't just get a 0 weight — its weight is redistributed
to the rest). If zero assets have a usable price, simulation is skipped
entirely (no chart series is added, only a caption explaining why).

**TVL display.**
- Glider: comes from `discover_strategies("curated")` cross-referenced by
  `strategy_id` — if the basket isn't in the "curated" collection, no TVL
  is shown, no error either (this is a silent miss, not a failure).
- Reserve / QuantAMM: `get_tvl_usd_defillama()` returns the **entire
  protocol's** TVL, not the basket's. Always labeled as a "cross-check" in
  the UI, never presented as if it were basket-specific.

**Rebalance frequency.** `estimate_monthly_frequency` needs at least 2
dated history events to compute anything; it divides event count by
`(date span in days) / 30`. A single event, or a zero/negative span
(duplicate timestamps), returns `None` → UI shows "insufficient data".

**Error isolation.** Every external call in `app.py` goes through
`safe_call`, which catches `Exception` broadly and turns it into
`(None, str(exc))`. This is intentional (see `safe_call`'s own docstring)
so one broken section (e.g. Reserve's Yield DTF composition) never takes
down the rest of the page.

## What this does NOT do

- No write operations anywhere — no enroll, mint/redeem, vote, or strategy
  creation, on any of the three platforms.
- No cross-platform performance comparison claim — the "Real" curves from
  different platforms are never presented as equivalent; only the
  decision-maker/frequency card is framed as comparable.
- No per-basket TVL for Reserve or QuantAMM — only protocol-wide TVL via
  DefiLlama exists for those two (see Rules and behavior above).
- No governance-proposal linkage for Reserve rebalances — the subgraph has
  no FK between a `Rebalance` and the proposal that approved it, so
  `description` never names a specific proposal (documented TODO in
  `adapters/reserve.py`).
- No composition or rebalance history for Reserve **Yield DTFs** (e.g.
  eUSD) — `get_current_allocation`/`get_rebalance_history` raise
  `ReserveAPIError` for these instead of returning partial or guessed data.
- No free-exploration UI for Glider's `discover_strategies` — the function
  exists and is used internally for TVL lookup, but there's no dropdown to
  browse the full curated collection.
- Historical price gaps are filled (`ffill`/`bfill`) for chart continuity,
  but no price is ever fabricated for an asset that has zero price history
  — that asset is excluded from the simulation, not interpolated into it.

## Gotchas

- **A basket with all sliders at 0% shows a simulation with no
  normalization applied.** Symptom: the pie chart and table show 0% for
  everything instead of an error. Cause: `total_raw <= 0` skips
  renormalization by design (see Rules and behavior). Fix: not a bug —
  drag at least one slider above 0.
- **An asset can visibly have a real weight but never appear in the
  simulated curve.** Symptom: pie/table show the asset, but it's absent
  (and listed under "Excluded") from the "Simulated" performance line.
  Cause: `price_ref` is `None` (non-EVM asset, or chain outside
  `EIP155_TO_DEFILLAMA`/`CHAIN_TO_DEFILLAMA`) or DefiLlama has no price for
  that exact address. Fix: none from the UI — this is a data-availability
  limit, not a bug.
- **Reserve weights are by token quantity, not USD value.**
  `weight_pct` for Reserve is `weightSpotLimit` normalized by its own sum —
  it does **not** account for each token's price. Two DTFs holding the same
  quantity ratio but very different token prices will show the same
  `weight_pct` despite very different USD allocations.
- **QuantAMM's rebalance history is filtered, not raw.** Only snapshots
  where the largest weight shift exceeds `MIN_WEIGHT_SHIFT_PCT` (1.0
  percentage point) become a row. The pool's underlying weight curve is
  continuous and changes far more often than the table implies — the table
  is a display sampling, not the full signal.
- **Glider TVL silently disappears for non-curated strategies.** If
  `basket_id` isn't returned by `discover_strategies("curated")`, both
  metric columns render `—` with no error shown, even though the strategy
  itself may load fine elsewhere on the page.
- **`GLIDER_API_KEY` missing doesn't crash the app.** Selecting Glider
  without the key configured shows a contained `st.error` from
  `GliderAPIError` in each affected section; Reserve and QuantAMM remain
  fully usable in the same session.

## Setup

Requires a `.env` file (copy from `.env.example`) with `GLIDER_API_KEY` —
only needed for the Glider platform; Reserve and QuantAMM use unauthenticated
public sources. See the "How to run" section in the project `README.md`.
