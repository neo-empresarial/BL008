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
- **Reserve** — Reserve's official discovery API (`GET /discover/dtfs`), filtered to active Index DTFs across mainnet/base/bsc. **Index DTFs only** — Yield DTFs (e.g. eUSD) are filtered out here, even though one is pinned as an example to show the composition-unavailable limitation.
- **QuantAMM** — the Balancer GraphQL API, `poolTypeIn: [QUANT_AMM_WEIGHTED]`, across every chain in `CHAIN_TO_DEFILLAMA`.

If discovery fails or returns nothing for a platform, the select falls back
to that platform's pinned `EXAMPLE_BASKETS` with a warning explaining why —
manual `basket_id` entry always stays available regardless.

## What's missing (out of scope for this MVP)

- Composition and history of Reserve's **Yield DTFs** (e.g. eUSD) — the
  public subgraph only covers Index DTFs; Yield DTFs would require direct
  RPC reads of the contract (`BackingManager`), not implemented here. The
  adapter raises a clear error instead of simulating this data (see
  `adapters/reserve.py`).
- Direct link between a Reserve `Rebalance` and the governance proposal
  that approved it (there's no FK between the two entities in the public
  subgraph — see TODO in `adapters/reserve.py`).
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
  (`MAINNET:0x6b61d8680c4f9e560c8306807908553f95c749c5`).

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

- Doesn't have a ready-made B2B API like Glider's. This adapter combines
  three public, no-key sources:
  - `https://api.reserve.org/discover/dtfs` — Reserve's official discovery
    API, the same catalog the Reserve site itself lists, used here for
    `discover_baskets()` (filtered to active Index DTFs; gives `marketCap`
    as per-basket TVL).
  - Goldsky subgraphs (The Graph-compatible), one per chain
    (mainnet/base/bsc), indexing the Folio contract of each Index DTF —
    the `Rebalance` entity is the real rebalance log (endpoint and schema
    confirmed in the `reserve-protocol/register` and
    `reserve-protocol/dtf-index-subgraph` repos), still used for
    `get_current_allocation`/`get_rebalance_history`.
  - `https://api.llama.fi` (DefiLlama) — historical price per token
    (`coins.llama.fi/chart`), used here as a performance proxy.
- Weights come as a raw on-chain target quantity (`weightSpotLimit`), not a
  ready-made % — we normalize by the sum to approximate relative weight by
  quantity (not by USD value). See full caveats in `adapters/reserve.py`.
- Rebalancing is via Dutch auction, approved beforehand by governance —
  but there's no direct link (FK) between `Rebalance` and the proposal that
  approved it in the public schema.

### QuantAMM / Balancer

- QuantAMM pools are native Balancer v3 pools (type
  `QUANT_AMM_WEIGHTED`) — no separate API of their own.
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
    reserve.py             # Reserve discovery API + Goldsky subgraph + DefiLlama (implemented)
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
