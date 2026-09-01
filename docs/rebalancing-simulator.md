# Rebalancing simulator

This document covers the Streamlit app in `app.py` and its `adapters/`
package: how the platform/basket selection, allocation, rebalance history,
performance, weight-simulation and strategy-comparison flows work today. It
describes the feature as it currently behaves, not how it was built.

## What this is

A read-only Streamlit MVP that compares real on-chain basket rebalancing
across three platforms — Glider, Reserve Protocol, QuantAMM/Balancer — side
by side, lets the user re-simulate performance under different asset
weights against a static HODL baseline, and overlays the real performance
of multiple example strategies across platforms. Invariants:

- Each platform is accessed through an **adapter** (`adapters/glider.py`,
  `adapters/reserve.py`, `adapters/quantamm.py`) that implements the exact
  same function signatures, so `app.py` never branches on platform beyond
  picking which adapter module to call.
- The app never invents data. Any value it can't obtain from a source (a
  missing TVL, a missing historical price, a missing rebalance, a missing
  token symbol) is either omitted, shown as a safe fallback, or raised as a
  readable error — never estimated or guessed.
- The underlying assets and REAL performance curves are **not** comparable
  across platforms (different asset classes, different weight semantics).
  The one metric that is comparable is who decides a rebalance and how
  often it happens — shown in the protocol details section. The comparison
  overlay plots real performance side by side anyway, with a visible note
  that methodologies differ.

## Surface

`app.py` is the entrypoint (`streamlit run app.py`), single page, no
routing, no tabs, **no side-by-side columns for the main flow** — every
section stacks vertically so the page scrolls naturally and the
performance chart gets full page width.

- **Sidebar** — platform picker, basket picker (example dropdown or manual
  `basket_id`), and a compact multi-select to pick strategies for the
  comparison overlay (`<Platform> / <example label>` options built from
  every adapter's `EXAMPLE_BASKETS`).
- **Header** — compact console header (platform badge, basket id).
- **Allocation** — full-width section: methodology expander, reset button,
  one row per asset (label, current %, slider), donut chart, table.
- **Performance** — full-width section. Its subheader and the chart-mode
  control (`st.segmented_control` when the installed Streamlit has it,
  else `st.radio(horizontal=True)`) share one row via `st.columns`, so the
  toggle visually belongs to this chart rather than sitting disconnected at
  the top of the page. A "How to read this chart" expander sits right
  below, explaining the three series (see Rules and behavior). The chart
  itself follows.
- **Strategy comparison** — below Performance; renders only when the
  sidebar multi-select has at least one entry; reuses the same chart-mode
  value.
- **Secondary sections** — `st.expander("Rebalance history")` and
  `st.expander("Protocol details")` (who decides + rebalance frequency),
  collapsed by default, at the bottom of the page.

`.streamlit/config.toml` sets the base dark theme (sans-serif, minimal
palette); `ui/theme.py` layers app-level CSS — extra top padding so the
header isn't clipped under Streamlit's toolbar, rounded card-like
chart/table surfaces with `overflow: hidden` so the rounding actually
clips the chart's own background, address/link styling — and a shared
Plotly layout (colorway, fonts, a `DEFAULT_CHART_HEIGHT` of 420px) applied
to every chart via `theme.apply_chart_theme`. Neither file touches data or
adapter logic.

### Adapter interface (implemented identically by all three adapters)

| Function | Returns |
|---|---|
| `get_current_allocation(basket_id)` | `list[dict]` — see Data shapes below |
| `get_rebalance_history(basket_id)` | `list[{"date": str \| None, "description": str, "weights_after": list[{"asset","weight_pct"}]}]` |
| `get_performance(basket_id)` | `{"method": str \| None, "points": list[{"date": str, "percent_change": float \| None}]}` |
| `get_tvl_usd_defillama()` | `float \| None` — **absent on `glider`**, present on `reserve` and `quantamm` |

Each adapter module also exports:

- `EXAMPLE_BASKETS: dict[str, str]` — label → `basket_id`, used to populate both the sidebar basket dropdown and the comparison multi-select.
- `DECISION_MAKER: str` — one sentence, shown in the protocol details section.
- An exception class (`GliderAPIError`, `ReserveAPIError`, `QuantAMMAPIError`), all subclasses of `Exception` with a readable `str()`.

Glider additionally exposes `discover_strategies(collection="curated", sort=None, limit=50) -> list[dict]` (used only for TVL/wallet-count lookup, not for the basket selector) and the lower-level `get_target_allocation` / `get_version_history` that `get_current_allocation` / `get_rebalance_history` wrap.

`adapters/pricing.py` exposes `get_price_history(price_ref, days=180) -> list[{"timestamp": int, "price": float}]`, `caip19_to_price_ref(asset_id) -> str | None`, `price_ref_to_explorer_url(price_ref) -> str | None`, and `resolve_token_symbol(price_ref) -> str | None` — all shared by the three adapters and/or by `app.py`.

### `basket_id` format per platform

| Platform | Format | Example |
|---|---|---|
| Glider | `strategyId` (ULID) | `01KV68M2Y685X59DWAEVX5D5X3` |
| Reserve Protocol | `<chain>:<DTF address>`, chain ∈ `{mainnet, base, bsc}` | `mainnet:0x323c...` |
| QuantAMM/Balancer | `<CHAIN>:<pool address>`, chain uppercase (Balancer's `GqlChain` enum) | `MAINNET:0x6b61...` |

## Data shapes

`get_current_allocation` rows carry the core fields plus optional display
metadata:

```python
{
    "asset": "WETH",              # stable identifier — never changes meaning
    "weight_pct": 42.5,           # 0-100 float, see below
    "price_ref": "ethereum:0x...",       # or None
    "display_asset": "WETH",             # human-readable label; falls back to a truncated address
    "token_address": "0x...",            # or None
    "chain": "ethereum",                 # DefiLlama chain slug, or None
    "explorer_url": "https://etherscan.io/token/0x...",  # or None
}
```

`asset` is always present and is what internal code (slider keys, weight
lookups, simulation matching) keys off — it never changes shape for
backward compatibility. `display_asset`/`token_address`/`chain`/
`explorer_url` are presentation-only and may be `None`; UI code prefers
`display_asset` when present and falls back to `asset` otherwise (see
`app.py`'s `_label_markup`, and its `display_labels` lookup used for the
donut chart, table, and the excluded-assets caption — every visible label
in the app goes through one of these, never the raw `asset` value).

For Glider, `asset` is the raw CAIP-19 assetId (used as the
simulation/session-state key). `display_asset` there resolves in two
steps: `pricing.resolve_token_symbol(price_ref)` first (a DefiLlama
current-price lookup — see Rules and behavior), and only when that returns
nothing does it fall back to a truncated address (`0x1234…abcd`) — never a
guessed name. For Reserve and QuantAMM, `asset` and `display_asset` are
the same value (the token symbol already returned by their own APIs, or
the raw address when no symbol is available there).

`weight_pct` is always a **0–100 float**, already normalized per basket by
the adapter (never a 0–1 fraction, even though some upstream APIs use one —
e.g. QuantAMM's `poolTokens[].weight` is 0–1 and gets multiplied by 100
before it leaves the adapter).

`price_ref`, when present, is a DefiLlama coin key: `"<chain-slug>:<address>"`
(e.g. `"ethereum:0xabc..."`). It is `None` whenever the asset couldn't be
mapped to a chain/address DefiLlama recognizes — this is expected and
common, not an error. `token_address`/`chain`/`explorer_url` are derived
from the same mapping and are `None` together whenever `price_ref` is.

`get_performance` points use `percent_change` as **accumulated return since
the first point**, not period-over-period return. `date` is an ISO 8601
string; for Reserve and QuantAMM it always carries a UTC offset, for Glider
it's whatever the API returns as-is.

Simulated performance points (built by `app.py`'s
`simulate_weighted_performance`, used for both the HODL and Adjusted
series) use the same `{"date", "percent_change"}` shape so every series can
be concatenated into one chart. `app.py`'s `to_display_series` then derives
a `display_value` column from `percent_change` depending on the active
chart mode (`CHART_MODES`):

| Mode | `display_value` formula |
|---|---|
| "Indexed value" (default) | `100 + percent_change` |
| "Percent return" | `percent_change` unchanged |
| "Growth of $10k" | `10000 * (1 + percent_change / 100)` |

`chart_mode_axis_label(chart_mode)` returns the matching y-axis label,
shared by both the Performance chart and the comparison overlay so they
never drift out of sync with each other.

## Rules and behavior

**Proportional allocation rebalancing.** Each basket's committed weights
are tracked in `st.session_state["prev_w::{platform}::{basket_id}"]`. On
each rerun, `app.py` compares each slider's pending value (from
`st.session_state`) against that basket's last committed weights:

- **0 or 2+ assets differ** (basket/platform just switched, or first
  render) — the pending values are used as-is (i.e. the real weights on
  first load).
- **Exactly 1 asset differs** (the normal case — the user dragged one
  slider) — that asset keeps its new value; every other asset is scaled
  proportionally, by its share of the *previous* total of the other
  assets, to fill the remaining percentage. If the changed asset moves to
  100%, all others become 0%. If it moves to 0% and the others' previous
  total was 0 too (edge case), the remaining percentage is split equally
  among them instead of dividing by zero.

The committed weights are written back into each slider's session-state
entry before the sliders are instantiated, so the UI always displays
already-rebalanced values — there is no separate "raw sum → normalize"
step or caption; the total is 100% by construction after every edit and
after "Reset to real weights".

**Glider token symbol resolution.** `adapters/glider.py`'s `_display_asset`
calls `pricing.resolve_token_symbol(price_ref)`, which hits DefiLlama's
`coins.llama.fi/prices/current/{price_ref}` endpoint (the same provider
family as `get_price_history`, so no new external dependency is
introduced) and reads the `symbol` field of the response. This runs once
per asset per `get_current_allocation` call — itself wrapped by `app.py`'s
`st.cache_data(ttl=300)`, so it isn't repeated on every rerun. Any failure
(network error, non-2xx, missing symbol) returns `None` and the adapter
falls back to a truncated address; it never fabricates a symbol.

**Weighted simulation (shared by HODL and Adjusted).** For each asset with
a `price_ref`, its full daily price history (`SIMULATION_DAYS` = 180 days)
is fetched, forward/backward filled to align dates across assets, and
normalized so day 0 = 1.0. The simulated index is the weight-averaged sum
of the normalized series, using weights renormalized **only across the
included assets** (i.e. an asset with no price data doesn't just get a 0
weight — its weight is redistributed to the rest). If zero assets have a
usable price, simulation is skipped entirely for that series (no chart
series is added; for Adjusted, a caption explains why — HODL fails silently
since it's a secondary series).

**Performance chart series.** Up to three series are plotted together,
built independently and only added when data is available:

| Series constant | Source | Reacts to sliders? |
|---|---|---|
| `SERIES_REAL` = "Real strategy (with rebalancing)" | `get_performance` — the platform's own method/source | No — platform data |
| `SERIES_HODL` = "HODL (real weights, no rebalancing)" | `simulate_weighted_performance` over `real_weights` (the basket's actual current weights, from `get_current_allocation`) | No — always the real weights |
| `SERIES_ADJUSTED` = "Adjusted buy-and-hold (your weights)" | `simulate_weighted_performance` over `edited_weights` (the committed slider weights) | Yes |

With the sliders at their real/reset values, HODL and Adjusted use the
same weights and should track closely (not necessarily identically — HODL
and Adjusted are computed independently, and each can silently exclude a
different asset only if their weight sets differ, which they don't at
reset — in practice they match). Real can differ from both even then,
because Real reflects whatever rebalancing the platform actually performed
over time, while HODL/Adjusted assume the weights were fixed for the whole
`SIMULATION_DAYS` window. The "How to read this chart" expander above the
chart states this in user-facing language.

**Chart mode.** `CHART_MODES = ["Indexed value", "Percent return", "Growth
of $10k"]`. The control (`st.segmented_control` or `st.radio` fallback,
see Surface) drives both the Performance chart and the comparison overlay
— both call the same `to_display_series`/`chart_mode_axis_label` helpers
and share one `chart_mode` value, so switching it updates both charts
identically.

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

**Strategy comparison overlay.** For each selected `<Platform> / <label>`
entry, `app.py` calls that platform's `get_performance` through
`safe_call`. A failure (adapter error, or no `points` returned) renders a
`st.warning` naming that one entry and is skipped — it never prevents the
other selected strategies from rendering. Each successful series keeps its
raw `percent_change` (i.e. its own return since its own first data point);
there is no cross-strategy date alignment or rebasing beyond what
`to_display_series` does per series independently.

**Error isolation.** Every external call in `app.py` goes through
`safe_call`, which catches `Exception` broadly and turns it into
`(None, str(exc))`. This is intentional (see `safe_call`'s own docstring)
so one broken section (e.g. Reserve's Yield DTF composition, or one failed
comparison entry) never takes down the rest of the page.

## What this does NOT do

- No write operations anywhere — no enroll, mint/redeem, vote, or strategy
  creation, on any of the three platforms.
- No cross-platform performance comparison claim — the "Real" curves from
  different platforms are never presented as methodologically equivalent;
  the comparison overlay carries an explicit caption saying so, and the
  one metric framed as directly comparable is who decides a rebalance and
  how often (protocol details section).
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
- No invented token symbols anywhere — Glider's `display_asset` either
  comes from a real DefiLlama lookup or falls back to a truncated address;
  it never guesses a name from context.
- No date alignment across comparison-overlay series — each strategy's
  points are plotted on its own native date range; overlapping ranges are
  a coincidence of the underlying data, not something the app enforces.
- No true historical "what the basket actually held on day X" for HODL —
  it assumes the *current* real weights held constant for the whole
  simulation window, not the weights that were actually in place on each
  past date.
- Historical price gaps are filled (`ffill`/`bfill`) for chart continuity,
  but no price is ever fabricated for an asset that has zero price history
  — that asset is excluded from the simulation, not interpolated into it.

## Gotchas

- **Dragging a slider snaps it to a rebalanced value, not the exact value
  you released it at (unless it's the one you dragged).** Symptom: moving
  asset A's slider visibly moves every other slider too. Cause: proportional
  rebalancing is intentional (see Rules and behavior) — the changed slider
  keeps your value, the rest are recalculated and written back into
  `st.session_state` before being displayed.
- **Switching platform or basket silently resets weights to real, without
  the "0 or 2+ diffs" branch feeling like a reset.** Symptom: no rebalance
  math applied. Cause: `prev_w::{platform}::{basket_id}` is keyed per
  basket, so a new basket has no prior committed state and the diff check
  naturally finds 0 changed assets, short-circuiting straight to "use
  pending/real as-is".
- **An asset can visibly have a real weight but never appear in the
  simulated curves.** Symptom: donut/table show the asset, but it's absent
  (and, for Adjusted, listed under "Excluded") from HODL and/or Adjusted.
  Cause: `price_ref` is `None` (non-EVM asset, or chain outside
  `EIP155_TO_DEFILLAMA`/`CHAIN_TO_DEFILLAMA`) or DefiLlama has no price for
  that exact address. Fix: none from the UI — this is a data-availability
  limit, not a bug.
- **HODL and Adjusted can look identical at reset — that's expected, not a
  bug.** Both use the same real weights and the same simulation path at
  that point; see Rules and behavior for why Real still differs.
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
  `GliderAPIError` in each affected section (including any Glider entry
  picked for comparison, via a `st.warning` there instead); Reserve and
  QuantAMM remain fully usable in the same session. Token symbol
  resolution for Glider doesn't need the key at all — it's a separate,
  unauthenticated DefiLlama call.
- **The comparison overlay's "Indexed value" and "Growth of $10k" modes
  rebase each series to its own first point, not to a shared start date.**
  Two overlaid series with different history lengths will both start at
  100 (or $10,000) even though their real calendar start dates differ —
  read the x-axis dates, not just the visual alignment at the left edge.

## Setup

Requires a `.env` file (copy from `.env.example`) with `GLIDER_API_KEY` —
only needed for the Glider platform; Reserve and QuantAMM use unauthenticated
public sources. See the "How to run" section in the project `README.md`.
