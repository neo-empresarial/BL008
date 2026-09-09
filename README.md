# BL008 — Rebalancing Simulator (MVP)

Streamlit MVP to compare real rebalancing behavior across on-chain basket
(index basket) platforms: **Glider**, **Reserve Protocol** and
**QuantAMM/Balancer**. All three are implemented and plugged into a
platform selector that swaps which adapter is used, keeping the same
layout to make side-by-side comparison easier.

## What the MVP does

- Lets you choose the platform (Glider / Reserve Protocol / QuantAMM-Balancer)
  and a basket/strategy from a live-discovered list for that platform (a few
  curated examples pinned at the top, plus every other basket the platform's
  discovery API/subgraph returns — see "Discovery" below), or paste another
  `basket_id` manually.
- Shows the current target allocation (pie chart + table, weight per asset)
  with a **slider per asset to simulate "what if I weighted it
  differently"** — e.g. changing Mag7 from equal-weight (100/7 each) to any
  other concentration. Sliders start at the real weights and are normalized
  to sum to 100%; a button resets to the real weights.
  - You can open **multiple independent weight tabs** for the same basket,
    browser-style: click **＋** next to the tabs to open one, click a tab to
    switch to it, click the tab you're already on again to rename it, **✕**
    to close it. Each tab keeps its own sliders, donut chart and table, and
    every tab's simulated curve — even ones you're not currently looking
    at — is overlaid together on the performance chart below so you can
    compare them directly.
- Shows the **rebalance history** — the real log of when and how each
  basket's weight changed. The nature of this log is quite different per
  platform (see "About each platform" below):
  - Glider: textual `changeLog` per version.
  - Reserve: one Dutch auction per `Rebalance` (nonce, tx hash), approved
    beforehand via governance.
  - QuantAMM: weight snapshots sampled from a continuous curve driven by an
    ML signal — no recorded human decision.
- Shows the performance curve (accumulated return x date): the **Real**
  curve (each platform's native method/source, clearly labeled — not
  directly comparable across platforms) and a **Simulated** curve, which
  recombines each asset's historical price (via DefiLlama) using the
  weights you adjusted on the sliders. An asset with no historical price
  available is excluded from the simulation and listed as such — never
  invented.
- Shows TVL when available: per-strategy on Glider, per-basket on Reserve
  (market cap from its official discovery API), and per-pool on QuantAMM —
  all from each platform's own discovery source.
- Comparative card across all three platforms: **who decides** a rebalance
  (governance / provider API key / autonomous ML signal) and the estimated
  **rebalance frequency** (events/month) — this is the metric that's
  actually comparable side by side, unlike the price curve or the
  underlying assets (tokenized stocks vs. crypto vs. crypto pools).

## Discovery

Every basket select (main picker and the comparison multi-select) is
populated from each platform's own discovery source, cached for an hour:

- **Glider** — `discover_strategies` on the `"curated"` collection (requires `GLIDER_API_KEY`).
- **Reserve** — Reserve's official discovery API (`GET /discover/dtfs`), filtered to active Index DTFs across mainnet/base/bsc. **Index DTFs only** — Yield DTFs (e.g. eUSD) are filtered out here, even though one is pinned as an example to show the composition-unavailable limitation. Allocation/history/performance use the same API family (see Data sources below), not discovery alone.
- **QuantAMM** — the Balancer GraphQL API, allowlisted to the QuantAMM site's three featured BTFs (Safe Haven, Base Macro, Sonic Macro) — there's no curated discovery API of its own, so the raw `poolTypeIn: [QUANT_AMM_WEIGHTED]` list (which also includes test pools and a near-zero-TVL duplicate Safe Haven deployment) is filtered down to `FEATURED_BTF_POOLS`.

If discovery fails or returns nothing for a platform, the select falls back
to that platform's pinned `EXAMPLE_BASKETS` with a warning explaining why —
manual `basket_id` entry always stays available regardless.

## What's missing (out of scope for this MVP)

- Composition of Reserve's **Yield DTFs** (e.g. eUSD) — Index discovery /
  rebalance routes do not expose them; Yield DTFs would require direct
  RPC reads of the contract (`BackingManager`), not implemented here. The
  adapter raises a clear error instead of simulating this data (see
  `adapters/reserve.py`). Empty Index rebalance history is valid and
  returns `[]` (not an error).
- Direct link between a Reserve rebalance and the governance proposal
  that approved it (no FK in the public surfaces used here — see TODO in
  `adapters/reserve.py`).
- Search/filter on the basket select — with dozens of discovered baskets
  on some platforms, the list is long and plain.
- Any write operation (create strategy, enroll, withdraw, vote) — this app
  is read-only across all three platforms.

## How to run

1. Python 3.10+ recommended.
2. Create a virtualenv and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and paste your Glider API key (only Glider
   needs a key — Reserve and QuantAMM use public, unauthenticated sources):

   ```bash
   cp .env.example .env
   # edit .env and fill in GLIDER_API_KEY=...
   ```

   Request a key at <https://console.glidercloud.dev/>. Without the key,
   the Glider platform shows a clear `st.error` explaining what's missing
   — it doesn't break with a raw traceback, and the other two platforms
   keep working normally.

4. Run:

   ```bash
   streamlit run app.py
   ```

## Example baskets

- **Glider** — Mag7 (`01KV68M2Y685X59DWAEVX5D5X3`), Nancy Pelosi Tracker
  (`01KWJ0Q9GXP1HBN2Z53RCHCHQW`).
- **Reserve Protocol** — OPEN, Index DTF on mainnet
  (`mainnet:0x323c03c48660fe31186fa82c289b0766d331ce21`); LCAP, Index DTF on
  base (`base:0x4dA9A0f397dB1397902070f93a4D6ddBC0E0E6e8`); eUSD, Yield DTF
  on mainnet (`mainnet:0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F` —
  composition unavailable in this MVP, see above).
- **QuantAMM/Balancer** — Safe Haven BTC:PAXG:USDC on mainnet
  (`MAINNET:0x6b61d8680c4f9e560c8306807908553f95c749c5`); Base Macro on base
  (`BASE:0xb4161aea25bd6c5c8590ad50deb4ca752532f05d`); Sonic Macro on sonic
  (`SONIC:0x74dc857d5567a3b087e79b96b91cdc8099b2fa34`).

## About each platform

### Glider

- Base URL: `https://api.glider.fi/v2`. Docs:
  `https://api.glider.fi/v2/llms.txt` / `https://docs.glider.fi/llms.txt`.
  OpenAPI: `https://api.glider.fi/v2/openapi.json`.
- Authentication via the `x-api-key` header (env var `GLIDER_API_KEY`).
- `{"success": true, "data": {...}}` or `{"success": false, "error": {...}}`
  envelope — the adapter handles both cases and raises `GliderAPIError` with
  a readable message.
- Monetary values and weights come as decimal strings (e.g. `"60.00"`) —
  converted with `Decimal`/`float()`, never with direct string arithmetic.
- `allocation.weight` is on a 0–100 scale.

### Reserve Protocol

- Public, no-key sources combined in `adapters/reserve.py`:
  - `https://api.reserve.org/discover/dtfs` — active Index DTF catalog for
    `discover_baskets()` (`marketCap` → per-basket TVL) and basket-weight
    fallback (`basket[].weight`, 0–100) when a DTF has no rebalance events.
  - `https://api.reserve.org/dtf/rebalance` — canonical rebalance event
    list (same path as the official CLI / SDK `fetchRebalanceHistory`);
    optional `&nonce=N` detail supplies USD-approximate weights when
    Goldsky has no `weightSpotLimit` for that nonce.
  - `https://api.reserve.org/historical/dtf` — NAV-style price series used
    as performance fallback when DefiLlama has no chart.
  - Goldsky Index DTF subgraphs (mainnet/base/bsc) — best-effort
    `weightSpotLimit` by nonce, and a fallback event list if the Reserve
    API returns nothing.
  - DefiLlama (`coins.llama.fi/chart`) — primary performance proxy.
- Rebalance-derived weights from Goldsky are quantity-normalized
  (`weightSpotLimit`); detail-API weights are USD-approximate
  (units × prices). Discover fallback weights are already 0–100. See
  caveats in `adapters/reserve.py`.
- Rebalancing is via Dutch auction, approved beforehand by governance —
  but there's no direct link between a rebalance and the proposal that
  approved it in the public surfaces used here.

### QuantAMM / Balancer

- QuantAMM pools are native Balancer v3 pools (type
  `QUANT_AMM_WEIGHTED`) — no separate API of their own, and no curated
  discovery endpoint either; `discover_baskets()` allowlists to the site's
  three featured BTFs (`FEATURED_BTF_POOLS` in `adapters/quantamm.py`).
- Source: Balancer's public, no-key GraphQL API
  (`https://api-v3.balancer.fi/graphql`, confirmed by introspection).
  `poolTokens[].weight` gives the current weight; `weightSnapshots` gives a
  real time series of weights (sampled hourly) — this becomes the
  "rebalance history"; `poolGetSnapshots.sharePrice` becomes the
  performance curve.
- 100% programmatic: each weight change is the result of an ML signal
  applied automatically on-chain, with no human approval — which is why
  each history entry is labeled `"signal-driven"`, without pretending
  there was a recorded decision like on Reserve.

## Structure

```
BL008/
  app.py                  # Streamlit entrypoint — platform selector
  adapters/
    __init__.py
    glider.py              # Glider API (implemented)
    reserve.py             # Reserve API (discover/rebalance/historical) + Goldsky + DefiLlama
    quantamm.py             # Balancer GraphQL API (implemented)
    pricing.py             # historical price per asset (DefiLlama), used by the weight simulator
  ui/
    __init__.py
    theme.py                # dark console CSS + shared Plotly theme
  .streamlit/
    config.toml             # base dark Streamlit theme
  .env.example
  requirements.txt
  README.md
```

Each adapter exposes the same common interface — `get_current_allocation`,
`get_rebalance_history`, `get_performance`, plus `EXAMPLE_BASKETS` and
`DECISION_MAKER` — to let app.py treat all three platforms generically (see
each file's docstring in `adapters/` for details and each data source's
specific approximations/limitations).
