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
