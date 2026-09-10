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
routing, **no side-by-side columns for the main flow** — every section
stacks vertically so the page scrolls naturally and the performance chart
gets full page width. The one exception is the Allocation section, which
has a browser-style tab strip for its weight scenarios (see below).

- **Login gate** — `require_login()`, called right after `theme.inject_css()`,
  before anything else: a centered form (NEO/Balancer logos, username,
  password) that blocks the rest of the page until it succeeds. See
  "Login gate" under Setup below.
- **Sidebar** — platform picker; basket picker built from that platform's
  live `discover_baskets()` (curated `EXAMPLE_BASKETS` pinned at the top,
  falling back to them alone if discovery fails) or a manual `basket_id`;
  and a compact multi-select, across all three platforms' discovered
  baskets, to pick strategies for the comparison overlay (`<Platform> /
  <label>` options) — see Basket discovery below.
- **Header** — NEO/Balancer logos (small, when present) above a compact
  console header (platform badge, basket id).
- **Allocation** — full-width section: methodology expander, then a
  hand-rolled tab strip (one button per weight scenario plus a "✕", and a
  trailing ＋), and below it only the *active* scenario's editor — reset
  button, one row per asset (label, current %, slider), donut chart, and
  table. See Weight scenario tabs below.
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
- **Footer** — `theme.render_footer()`, at the very end of the page:
  project name/subtitle, `FOOTER_LINK_SECTIONS` (GitHub/docs, the three
  compared platforms, data sources) in columns, and a bottom disclaimer
  line.

`.streamlit/config.toml` sets the base dark theme; `ui/theme.py` layers
app-level CSS — extra top padding so the header isn't clipped under
Streamlit's toolbar, rounded card-like chart/table surfaces with
`overflow: hidden` so the rounding actually clips the chart's own
background, address/link styling, a page-wide grain texture, the footer —
and a shared Plotly layout (colorway, fonts, a `DEFAULT_CHART_HEIGHT` of
420px) applied to every chart via `theme.apply_chart_theme`. Neither file
touches data or adapter logic.

The palette (both files) is ported from the `reclamm-monorepo` frontend's
Chakra theme — see the constants' docstring in `ui/theme.py` for the exact
token each color comes from. That frontend forces dark mode always, so
this is that theme's `dark` token set, not a Streamlit-original palette;
re-syncing after a reclamm palette change means re-reading those same
token files and updating the hex constants here by hand (no shared
package/build step ties the two).

`public/` holds three assets ported from the same frontend
(`reclamm-monorepo/apps/reclamm-frontend/public/images/`):
`background-noise.png` is tiled full-page by `theme._noise_background_css`
under a 97%-opaque layer of the page background — replicating reclamm's
`Noise` component's two-layer approach in a single CSS `background-image`
rule (no extra wrapper element, since Streamlit's DOM isn't ours to nest
arbitrarily). `favicon-light.png` (the Balancer stacked-stones mark, dark
variant) is the browser-tab favicon via `theme.page_icon()`, passed to
`st.set_page_config(page_icon=...)` — `None` (Streamlit's own default
favicon) if the file's missing. `granite-1.jpg` is still staged but
unused — reserved for a follow-up (per-chart granite backgrounds).
`public/balancer-logo.png` (the Balancer mark, white variant — cropped to
its visible content by `theme._logo_img_tag`, see below) is shipped;
`public/neo-logo.png` is **not** (no source to port it from) — add it
locally to also brand the login screen and the console header with NEO's
mark alongside Balancer's (see
`theme.render_login_logos()`/`theme.render_header()` and "Login gate"
under Setup below); the app runs fine without either, both spots just
show no logos.

### Adapter interface (implemented identically by all three adapters)

| Function | Returns |
|---|---|
| `get_current_allocation(basket_id)` | `list[dict]` — see Data shapes below |
| `get_rebalance_history(basket_id)` | `list[{"date": str \| None, "description": str, "weights_after": list[{"asset","weight_pct"}]}]` |
| `get_performance(basket_id)` | `{"method": str \| None, "points": list[{"date": str, "percent_change": float \| None}]}` |
| `get_tvl_usd_defillama()` | `float \| None` — **absent on `glider`**, present on `reserve` and `quantamm` |
| `discover_baskets()` | `list[{"basket_id": str, "name": str, "tvl_usd": float \| None, "chain": str \| None}]` — see Basket discovery below |

Each adapter module also exports:

- `EXAMPLE_BASKETS: dict[str, str]` — label → `basket_id`; a handful of curated, hand-picked baskets pinned at the top of the sidebar/comparison selects, no longer the only source (see Basket discovery).
- `DECISION_MAKER: str` — one sentence, shown in the protocol details section.
- An exception class (`GliderAPIError`, `ReserveAPIError`, `QuantAMMAPIError`), all subclasses of `Exception` with a readable `str()`.

Glider additionally exposes `discover_strategies(collection="curated", sort=None, limit=50) -> list[dict]` (each item carrying `strategy_id`, `name`, `tvl_usd`, `portfolio_count`, `assets` — the richer shape `discover_baskets()` wraps, plus `portfolio_count`, which the shared shape doesn't carry) and the lower-level `get_target_allocation` / `get_version_history` that `get_current_allocation` / `get_rebalance_history` wrap.

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

**Basket discovery.** `app.py`'s `build_basket_options(platform_name)` calls
`cached_discover_baskets(platform_name)` (`st.cache_data(ttl=3600)` — much
longer than the 5-minute `CACHE_TTL_SECONDS` used elsewhere, since discovery
paginates a whole subgraph/API and shouldn't repeat every rerun) and merges
the result with that platform's pinned `EXAMPLE_BASKETS`:

- Pinned examples always appear first, keeping their curated labels.
- A discovered row is skipped if its `basket_id` (case-insensitively)
  already matches a pinned one — no duplicate entry for the same basket.
- A discovered row whose `name` collides with another row, or with a
  pinned label, gets a disambiguating suffix: `"{name} — {chain} ({last 8
  chars of basket_id})"`.
- If discovery fails or returns nothing, the select falls back to
  `EXAMPLE_BASKETS` alone, with a `st.sidebar.warning` naming the error —
  the select is never empty and manual `basket_id` entry always stays
  available regardless of discovery's outcome.

Per-adapter discovery source and scope:

| Adapter | Source | Scope |
|---|---|---|
| `glider.discover_baskets` | `discover_strategies(collection="curated")`, default page size (the API rejects `limit` above 50 — confirmed empirically) | Curated collection only; no broader collection is documented |
| `reserve.discover_baskets` | Reserve's official discovery API, `GET /discover/dtfs` (single call, no pagination) | **Active Index DTFs only** — rows are filtered to `type == "index"` and `status == "active"`, and to chain ids `{1, 8453, 56}` (mainnet/base/bsc); Yield DTFs (e.g. eUSD) never appear here even though one is pinned as an example. Allocation/history use `GET /dtf/rebalance` (Goldsky weights by nonce; discover `basket` fallback); performance uses DefiLlama then `GET /historical/dtf` |
| `quantamm.discover_baskets` | Balancer `poolGetPools` (`poolTypeIn: [QUANT_AMM_WEIGHTED]`, `protocolVersionIn: [3]`, `tagNotIn: [EXCLUDED_POOL_TAG]`), paginated with `skip`, across every chain in `CHAIN_TO_DEFILLAMA` | **Excludes `BLACK_LISTED` pools** — QuantAMM has no curated discovery API, but Balancer itself tags test pools and duplicate deployments (e.g. a second Safe Haven) `BLACK_LISTED`; excluding that tag server-side currently leaves Safe Haven, Base Macro and Sonic Macro |

The comparison multi-select in the sidebar calls `build_basket_options` for
every platform (so switching the main platform picker doesn't limit what
can be compared) and flattens them into `"{platform} / {label}"` options —
this can be a long list; there is no search/filter on top of it yet.

**Weight scenario tabs.** Each basket can hold several independent weight
scenarios ("tabs"), so different what-if allocations can be tweaked
separately and compared on the same performance chart. `get_scenarios`
stores the tab list at `st.session_state["scenarios::{platform}::{basket_id}"]`
as `list[{"id", "label"}]`, seeded with one `"Scenario 1"` tab the first
time a basket is opened.

`render_tab_bar` draws the strip itself and owns which tab is *active*
(`st.session_state["active_scenario::{platform}::{basket_id}"]`) — `st.tabs`
isn't used here because it can't host a per-tab close control or a
trailing "+", so the strip is hand-rolled from one row of `st.columns`
instead (styled to read as tabs by `ui/theme.py`'s CSS, keyed off the
`st-key-<key>` class Streamlit gives a widget with an explicit `key=`).
Per scenario it renders a select button (`type="primary"` when active, so
Streamlit's own accent styling *is* the "active tab" look) plus a small
"✕" (`type="tertiary"`, disabled once only one tab remains). Streamlit has
no double-click event to bind a rename to, so clicking a tab that's
*already* active is the stand-in: instead of switching (there's nowhere
else to switch to) or doing nothing, it swaps that tab's select button for
an inline `text_input` prefilled with the current label. Renaming commits
via the `text_input`'s `on_change` callback (`_commit_rename`) rather than
a separate confirm button — Streamlit calls it the instant the value
changes (Enter or losing focus) and reruns the script right after, so
typing a name and pressing Enter (or clicking elsewhere) is enough; an
emptied-out value is ignored rather than leaving a tab unnamed. A trailing
"＋" (also `type="tertiary"`) appends a new scenario (`uuid4().hex[:8]`
id, default label `"Scenario {n}"`) and makes it active.

Because the row's column widths are computed from the *current* scenario
list/active id at the top of the function, a button click that changes
either (switch tabs, enter rename mode, add, or close) calls `st.rerun()`
before returning — otherwise the strip would only reflect the change one
interaction later, since writing to `st.session_state` alone doesn't
trigger a second rerun by itself. A rename *commit* doesn't need this: its
`on_change` callback already runs (and already updated `scenario["label"]`
and cleared the renaming flag in `st.session_state`) before `render_tab_bar`
is even called again on the rerun the callback itself triggers.

Only the *active* scenario's editor is rendered below the strip each run —
like a browser tab, every other scenario's content stays hidden.
`render_scenario_editor` is that editor (the direct successor of what used
to be the single, un-tabbed allocation editor); it takes one `scenario`
dict and returns its committed weights. A scenario that isn't active this
run keeps its last committed weights in `st.session_state` regardless
(see below) — nothing is lost by switching away from it, and the
Performance section (further down) reads every scenario's weights back
from `st.session_state` directly rather than depending on it having been
rendered this run.

**Proportional allocation rebalancing.** Each tab's committed weights are
tracked in
`st.session_state["prev_w::{platform}::{basket_id}::{scenario_id}"]` —
scoped by scenario id in addition to platform/basket, so tabs never share
slider state and switching platform/basket starts every tab fresh rather
than reusing another basket's scenarios. On each rerun, `render_scenario_editor`
compares each of that tab's slider pending values (from `st.session_state`)
against that tab's last committed weights:

- **0 or 2+ assets differ** (basket/platform just switched, this tab was
  just created, or first render) — the pending values are used as-is (i.e.
  the real weights on first load).
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

**Performance chart series.** Two fixed series plus one Adjusted series
per open weight-scenario tab are plotted together, built independently and
only added when data is available:

| Series | Source | Reacts to sliders? |
|---|---|---|
| `SERIES_REAL` = "Real strategy (with rebalancing)" | `get_performance` — the platform's own method/source | No — platform data |
| `SERIES_HODL` = "HODL (real weights, no rebalancing)" | `simulate_weighted_performance` over `real_weights` (the basket's actual current weights, from `get_current_allocation`) | No — always the real weights |
| `adjusted_series_label(scenario["label"])` = "Adjusted buy-and-hold ({tab name})", one per tab | `simulate_weighted_performance` over that tab's committed weights (`scenario_edited_weights[scenario["id"]]`) | Yes — that tab's sliders only |

`app.py` loops every scenario in the tab list and reads its committed
weights back from `st.session_state["prev_w::{platform}::{basket_id}::{scenario_id}"]`
(falling back to `real_weights` for a tab that was added but never
rendered/edited yet), then calls `simulate_weighted_performance` once per
tab — so adding a tab adds one more Adjusted line to the chart rather than
replacing the existing one, and a tab that isn't the active one this run
still contributes its line (see Weight scenario tabs above). This is what
makes tab-to-tab comparison possible. Excluded assets are unioned across
every tab's simulation into a single caption (exclusion depends only on
price availability, not on a tab's weights, so in practice every tab
excludes the same assets).

With a tab's sliders at their real/reset values, HODL and that tab's
Adjusted line use the same weights and should track closely (not
necessarily identically — HODL and Adjusted are computed independently,
and each can silently exclude a different asset only if their weight sets
differ, which they don't at reset — in practice they match). Real can
differ from both even then, because Real reflects whatever rebalancing the
platform actually performed over time, while HODL/Adjusted assume the
weights were fixed for the whole `SIMULATION_DAYS` window. The "How to
read this chart" expander above the chart states this in user-facing
language.

**Chart mode.** `CHART_MODES = ["Indexed value", "Percent return", "Growth
of $10k"]`. The control (`st.segmented_control` or `st.radio` fallback,
see Surface) drives both the Performance chart and the comparison overlay
— both call the same `to_display_series`/`chart_mode_axis_label` helpers
and share one `chart_mode` value, so switching it updates both charts
identically.

**TVL display.** `app.py` first looks up the selected `basket_id` in that
platform's already-fetched `cached_discover_baskets` rows (`basket_match`)
for a per-basket `tvl_usd`:
- **Glider**: shows a `TVL (USD)` / `Nº of wallets` metric pair always —
  `"—"` for either value that's unavailable, never blank. Wallet count
  needs a separate `discover_strategies("curated")` call (`portfolio_count`
  isn't in the shared discovery shape) cross-referenced by `strategy_id`.
  If both TVL and wallet count are unavailable, a caption names why (a
  discovery error, or "this strategy isn't in the curated discovery
  collection").
- **QuantAMM**: `basket_match["tvl_usd"]` is real per-pool TVL
  (`dynamicData.totalLiquidity`), shown directly as a metric when present.
- **Reserve**: `basket_match["tvl_usd"]` is the discovery API's `marketCap`,
  shown directly as a metric when present. It falls back to
  `get_tvl_usd_defillama()` — the **entire protocol's** TVL — shown as a
  caption explicitly labeled "cross-check" only when the basket isn't in
  the discovered rows (e.g. a manually-pasted, off-catalog `basket_id`). If
  even that fails, a plain "TVL unavailable" caption is shown.

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
- No governance-proposal linkage for Reserve rebalances — the public
  surfaces used here have no FK between a rebalance and the proposal that
  approved it, so `description` never names a specific proposal
  (documented TODO in `adapters/reserve.py`).
- No composition for Reserve **Yield DTFs** (e.g. eUSD) —
  `get_current_allocation` raises `ReserveAPIError` instead of returning
  partial or guessed data. Index DTFs with no on-chain rebalances yet
  return `[]` from `get_rebalance_history` and take allocation from
  discover `basket[].weight`.
- No search/filter on the basket selects — with dozens of discovered
  baskets on some platforms, the plain select can be long; search/filter is
  future work.
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
- **Reserve weight semantics depend on the source.** Goldsky-backed
  rebalance weights are `weightSpotLimit` normalized by quantity (not USD).
  Weights from `/dtf/rebalance&nonce=` detail are USD-approximate
  (proposal units × prices). Discover fallback weights are already 0–100
  from `basket[].weight`. Two quantity-normalized rows with the same ratio
  can still imply different USD allocations.
- **Reserve performance may come from DefiLlama or `/historical/dtf`.**
  DefiLlama is tried first; young or thinly listed DTF tokens often only
  resolve via the Reserve historical price series.
- **QuantAMM's rebalance history is filtered, not raw.** Only snapshots
  where the largest weight shift exceeds `MIN_WEIGHT_SHIFT_PCT` (1.0
  percentage point) become a row. The pool's underlying weight curve is
  continuous and changes far more often than the table implies — the table
  is a display sampling, not the full signal.
- **A basket can load fine everywhere else on the page while its TVL/wallet
  metrics show `—`.** Cause: the basket isn't in Glider's curated discovery
  collection (or, for Reserve/QuantAMM, isn't in that platform's
  `discover_baskets()` results — e.g. it was entered manually). This is
  expected, and a caption explains it rather than leaving it silent.
- **A manually-pasted `basket_id` can still pick up a TVL/wallet-count
  match.** The `basket_match` lookup compares `basket_id` strings
  (case-insensitively) against that platform's discovered rows regardless
  of whether the id came from the select or the manual text input — so
  pasting an id that happens to be in the discovered set behaves exactly
  like picking it from the dropdown. It only shows `—` when the basket is
  genuinely outside discovery's scope (e.g. a Reserve Yield DTF, or a
  non-curated Glider strategy).
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

**Login gate.** The same `.env` file also carries `APP_USERNAME` /
`APP_PASSWORD` — the whole app (`require_login` in `app.py`) is gated
behind this single shared login, checked before anything else renders.
Leaving either blank blocks every visitor with a setup message rather than
silently letting everyone in. On Streamlit Community Cloud, set both in
the app's **Secrets** panel instead of committing a `.env` file — Cloud
exposes secrets as environment variables too, so `os.environ.get` (the
same mechanism `GLIDER_API_KEY` already uses) works unchanged in both
places. A successful login is remembered only in that browser tab's
`st.session_state` — refreshing keeps it, but a new session/device asks
again; there's no "remember me," multi-user accounts, or lockout after
failed attempts (single shared credential, single unauthenticated
attempt-count — acceptable for this MVP's threat model, not a general
auth system). `theme.render_login_logos()` shows the NEO and Balancer
logos above the form, and `theme.render_header()` shows the same two
logos (smaller, left-aligned) above the console header on every page once
logged in — both read from `public/neo-logo.png` / `public/balancer-logo.png`
(shared by `theme._logos_html`) when present, skipping either (or the
whole row) rather than showing a broken image if a logo hasn't been added
to the repo yet.

Getting two unrelated logo files to actually look "the same size" next to
each other took two fixes in `theme._logo_img_tag`, not one — matching
just one still left them mismatched:
- Each logo is embedded as a base64 `<img>` fixed to a set *height* with
  `width: auto`, rather than `st.image`'s `width="stretch"` (which
  matches *column* width instead). With two logos of very different
  source aspect ratios — NEO's is roughly square, Balancer's is a wide
  wordmark — matching width alone renders them at very different heights.
- Fixing height on the *raw* files still wasn't enough: NEO's artwork
  fills nearly its entire canvas, while Balancer's actual glyph sits in a
  small block surrounded by a wide transparent margin (empirically, only
  ~17% of that file's canvas height is actual visible content) — scaling
  the whole padded canvas to a fixed height scales the padding right
  along with it, so Balancer's logo still looked tiny. `_logo_img_tag`
  now opens each file with Pillow and crops to `Image.getbbox()` (the
  bounding box of non-transparent pixels) before embedding, so `height_px`
  sizes each logo's actual visible mark, not however much blank canvas
  happens to surround it in the source file.
