"""
Shared visual language for the dark research-console UI: color constants,
app-level CSS injection, and a Plotly layout applied to every chart.

Nothing here touches data or adapter logic — this module only styles what
app.py already computes. See `.streamlit/config.toml` for the base
Streamlit theme (backgrounds, primary color) this module builds on top of.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

BACKGROUND = "#0b0e11"
SURFACE = "#12161b"
BORDER = "#232830"
TEXT = "#d7dde3"
TEXT_MUTED = "#8b95a1"
ACCENT = "#00d18f"
GRID = "#1c2129"

# Applied to every series added to a chart, in order — kept restrained
# (accent green first, then a handful of muted, distinguishable hues) so a
# 2-3 series chart reads as one system rather than a rainbow.
COLORWAY = [ACCENT, "#4fa3ff", "#f2b134", "#ff6b6b", "#9d7bff", "#31c7c7"]

FONT_FAMILY = "JetBrains Mono, SFMono-Regular, Menlo, Consolas, monospace"

PLOTLY_LAYOUT: dict[str, Any] = {
    "paper_bgcolor": SURFACE,
    "plot_bgcolor": SURFACE,
    "font": {"family": FONT_FAMILY, "color": TEXT, "size": 12},
    "colorway": COLORWAY,
    "legend": {"bgcolor": "rgba(0,0,0,0)"},
    "margin": {"l": 40, "r": 20, "t": 40, "b": 30},
    "title": {"font": {"size": 14, "color": TEXT_MUTED}},
}


def apply_chart_theme(fig):
    """Applies the shared dark layout/colorway to a Plotly figure in place
    and returns it, so calls can stay inline: `st.plotly_chart(apply_chart_theme(fig))`."""
    fig.update_layout(**PLOTLY_LAYOUT)
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=BORDER)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=BORDER)
    return fig


def inject_css() -> None:
    """Injects app-level CSS: tighter spacing and a console-style header/badge
    look on top of the base theme from `.streamlit/config.toml`. Call once,
    right after `st.set_page_config`."""
    st.markdown(
        f"""
        <style>
        .block-container {{
            padding-top: 1.5rem;
            padding-bottom: 2rem;
            max-width: 1200px;
        }}
        [data-testid="stSidebar"] {{
            border-right: 1px solid {BORDER};
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
            font-size: 1.1rem;
            font-weight: 700;
            letter-spacing: 0.08em;
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
            padding: 0.15rem 0.55rem;
            border-radius: 999px;
            border: 1px solid {ACCENT};
            color: {ACCENT};
            font-size: 0.75rem;
            letter-spacing: 0.03em;
        }}
        .basket-id {{
            font-family: {FONT_FAMILY};
            color: {TEXT_MUTED};
        }}
        [data-testid="stMetricValue"] {{
            font-family: {FONT_FAMILY};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(title: str, platform_name: str, basket_id: str) -> None:
    """Compact console-style header: product name, platform badge, basket id."""
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
