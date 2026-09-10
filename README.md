# BL008 — DTFs On-Chain Monitoring

Streamlit app that compares real on-chain rebalancing behavior across three
basket platforms — Glider, Reserve Protocol and QuantAMM/Balancer — side by
side.

## What it does

Lets you pick a platform and a basket, see its current allocation, and drag
sliders to simulate a different weighting against the platform's real
rebalance history and performance. A comparison view overlays several
baskets' real performance across platforms, alongside who decides each
platform's rebalances (governance, provider API, or an autonomous ML
signal) and how often. The app is read-only — it never writes to any
platform.

## Running it locally

Requires Python 3.10+.

1. Create a virtualenv and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in `GLIDER_API_KEY`:

   ```bash
   cp .env.example .env
   ```

   Request a key at <https://console.glidercloud.dev/>. Only the Glider
   platform needs it — Reserve and QuantAMM use public, unauthenticated
   sources. Without the key, Glider shows a contained error and the other
   two platforms keep working.

3. Run:

   ```bash
   streamlit run app.py
   ```

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `GLIDER_API_KEY` | Only for the Glider platform | Auth for the Glider B2B API v2. See `.env.example`. |

## Layout

```
app.py                  # Streamlit entrypoint — platform selector and page
adapters/                # one module per platform, same function signatures
  glider.py
  reserve.py
  quantamm.py
  pricing.py             # historical asset prices (DefiLlama), used by the simulator
ui/
  theme.py               # dark theme CSS + shared Plotly theme
docs/
  rebalancing-simulator.md  # how the app behaves: data shapes, rules, gotchas
```

`docs/rebalancing-simulator.md` is the reference for how the app actually
behaves — adapter interface, per-platform data shapes, simulation rules, and
known gotchas. Read it before changing `app.py` or any adapter.
