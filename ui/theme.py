"""
Shared visual language for the minimal dark UI: color constants, app-level
CSS injection, and a Plotly layout applied to every chart.

Nothing here touches data or adapter logic — this module only styles what
app.py already computes. See `.streamlit/config.toml` for the base
Streamlit theme (backgrounds, primary color) this module builds on top of.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

BACKGROUND = "#111318"
SURFACE = "#1a1d24"
BORDER = "#2a2e37"
TEXT = "#e4e6eb"
TEXT_MUTED = "#9299a6"
ACCENT = "#6ea8fe"
GRID = "#242832"

# Grayscale-only stand-in for the theme's ACCENT, used where a blue accent
# would be too loud — currently just the active weight-scenario tab (see
# the tab-bar CSS below); everything else keeps using ACCENT.
NEUTRAL_HIGHLIGHT = "#454b57"

# Applied to every series added to a chart, in order — restrained and
# readable rather than a default rainbow, so a 2-3 series chart reads as
# one system.
COLORWAY = [ACCENT, "#f2b134", "#7ee08c", "#ff8fa3", "#c792ea", "#5fd4d4"]

FONT_FAMILY = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
)
MONO_FONT_FAMILY = "SFMono-Regular, Menlo, Consolas, monospace"

DEFAULT_CHART_HEIGHT = 420

PLOTLY_LAYOUT: dict[str, Any] = {
    "paper_bgcolor": SURFACE,
    "plot_bgcolor": SURFACE,
    "font": {"family": FONT_FAMILY, "color": TEXT, "size": 13},
    "colorway": COLORWAY,
    "legend": {"bgcolor": "rgba(0,0,0,0)"},
    "margin": {"l": 40, "r": 20, "t": 40, "b": 30},
    "title": {"font": {"size": 14, "color": TEXT_MUTED}},
    "height": DEFAULT_CHART_HEIGHT,
}


def apply_chart_theme(fig):
    """Applies the shared dark layout/colorway/height to a Plotly figure in
    place and returns it, so calls can stay inline:
    `st.plotly_chart(apply_chart_theme(fig))`. Pass `height=` in a
    subsequent `fig.update_layout` call to override `DEFAULT_CHART_HEIGHT`
    for a specific chart."""
    fig.update_layout(**PLOTLY_LAYOUT)
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=BORDER)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=BORDER)
    return fig


def inject_css() -> None:
    """Injects app-level CSS: readable sans-serif typography, extra top
    padding so the header isn't clipped under Streamlit's toolbar, rounded
    card-like chart/table surfaces (with `overflow: hidden` so the rounded
    corners actually clip the chart's own background), and address/link
    styling — on top of the base theme from `.streamlit/config.toml`. Call
    once, right after `st.set_page_config`."""
    st.markdown(
        f"""
        <style>
        html, body, [class*="css"] {{
            font-family: {FONT_FAMILY};
        }}
        .block-container {{
            padding-top: 3rem;
            padding-bottom: 2rem;
            max-width: 1200px;
        }}
        [data-testid="stSidebar"] {{
            border-right: 1px solid {BORDER};
        }}
        [data-testid="stPlotlyChart"], [data-testid="stDataFrame"] {{
            border: 1px solid {BORDER};
            border-radius: 12px;
            padding: 0.5rem;
            background-color: {SURFACE};
            overflow: hidden;
        }}
        .console-header {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 0.5rem 1rem;
            padding-bottom: 0.75rem;
            margin-bottom: 1rem;
            border-bottom: 1px solid {BORDER};
        }}
        .console-title {{
            font-size: 1.25rem;
            font-weight: 700;
            color: {TEXT};
        }}
        .console-meta {{
            display: flex;
            align-items: center;
            gap: 0.6rem;
            font-size: 0.85rem;
            color: {TEXT_MUTED};
        }}
        .badge {{
            display: inline-block;
            padding: 0.15rem 0.6rem;
            border-radius: 999px;
            background-color: {ACCENT};
            color: {BACKGROUND};
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .basket-id {{
            font-family: {MONO_FONT_FAMILY};
            color: {TEXT_MUTED};
        }}
        .asset-address {{
            font-family: {MONO_FONT_FAMILY};
            color: {TEXT_MUTED};
        }}
        .asset-address a {{
            color: {ACCENT};
            text-decoration: none;
        }}
        [data-testid="stMetricValue"] {{
            font-family: {MONO_FONT_FAMILY};
        }}

        /* Weight-scenario tab bar (app.py's render_tab_bar) — a hand-rolled
        row of buttons styled to read as browser-like tabs, since st.tabs
        can't host a per-tab close control or a trailing "+". Active vs.
        inactive is Streamlit's own primary/secondary button styling (see
        render_tab_bar); this CSS only handles spacing/shape and the row's
        baseline. Selectors key off the `st-key-<key>` class Streamlit adds
        to a widget's wrapper when it's given an explicit `key=` — the
        frontend sanitizes the key for CSS by replacing every character
        that isn't `[a-zA-Z0-9_-]` with `-`, so our `::`-separated keys
        (e.g. `tabsel::Glider::...`) become `st-key-tabsel--Glider--...`;
        matching on `st-key-tabsel--` (not `::`) is what actually hits. */
        [class*="st-key-tabsel--"] button {{
            width: 100%;
            border-radius: 8px 8px 0 0;
        }}
        /* Active tab: a neutral gray fill instead of the theme's blue
        primary color, so the tab bar reads as grayscale, not an accent
        color. `stBaseButton-primary` is the data-testid Streamlit puts on
        a button's `type="primary"` variant (see render_tab_bar). */
        [class*="st-key-tabsel--"] [data-testid="stBaseButton-primary"] {{
            background-color: {NEUTRAL_HIGHLIGHT} !important;
            border-color: {NEUTRAL_HIGHLIGHT} !important;
            color: {TEXT} !important;
        }}
        [class*="st-key-tabsel--"] [data-testid="stBaseButton-primary"]:hover {{
            border-color: {TEXT_MUTED} !important;
        }}
        [class*="st-key-tabclose--"] button {{
            padding-left: 0.4rem;
            padding-right: 0.4rem;
            opacity: 0.7;
        }}
        [class*="st-key-tabclose--"] button:hover {{
            opacity: 1;
        }}
        /* "+" gets a visible outline rather than tertiary's borderless
        look — it's a distinct action (open a tab), not a minor affordance
        like "✕", so it should stand out at a glance. */
        [class*="st-key-tabadd--"] button {{
            padding-left: 0.6rem;
            padding-right: 0.6rem;
            border: 1px solid {TEXT_MUTED} !important;
            color: {TEXT} !important;
        }}
        [class*="st-key-tabadd--"] button:hover {{
            border-color: {TEXT} !important;
            background-color: {SURFACE} !important;
        }}
        [data-testid="stHorizontalBlock"]:has([class*="st-key-tabsel--"]) {{
            align-items: flex-end;
            border-bottom: 1px solid {BORDER};
            margin-bottom: 0.75rem;
            gap: 0.15rem !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(title: str, platform_name: str, basket_id: str) -> None:
    """Compact header: product name, platform badge, basket id."""
    st.markdown(
        f"""
        <div class="console-header">
            <div class="console-title">{title}</div>
            <div class="console-meta">
                <span class="badge">{platform_name}</span>
                <span class="basket-id">{basket_id}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
