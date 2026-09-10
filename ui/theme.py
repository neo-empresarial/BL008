"""
Shared visual language for the dark UI: color constants, app-level CSS
injection, and a Plotly layout applied to every chart.

Nothing here touches data or adapter logic — this module only styles what
app.py already computes. See `.streamlit/config.toml` for the base
Streamlit theme (backgrounds, primary color) this module builds on top of.

Colors are ported from the reCLAMM frontend's Chakra theme
(`reclamm-monorepo/packages/lib/shared/services/chakra/themes/`), which
forces dark mode always — so these are that theme's `dark` token values,
not a Streamlit-original palette:
- BACKGROUND: `styles.global.body.background` → `background.base` →
  `colors.base.dark` (`base/tokens.ts`).
- SURFACE: `background.level2` (`base/tokens.ts`, dark) — one step lighter
  than the page background, for card-like surfaces.
- FOOTER_BG: `background.level0` — one step darker than BACKGROUND, same
  relationship reclamm's own `Footer` (`background.level0`) has to its
  page body (`background.base`).
- BORDER / GRID: `chartBorder.dark` (`base/colors.ts`).
- TEXT / TEXT_MUTED: `text.primary` / `text.secondary` (dark).
- ACCENT: `primary.500` (`base/colors.ts`).
- NEUTRAL_HIGHLIGHT: `background.level4` — one step lighter than SURFACE.
- COLORWAY: ACCENT plus the theme's green/orange/red/purple accents and the
  scatter-chart "swap" blue (`semantic-tokens.ts`, `chart.pool.scatter`).

`public/background-noise.png` is reclamm's own grain texture asset
(`reclamm-monorepo/apps/reclamm-frontend/public/images/background-noise.png`),
used here the same way reclamm's `Noise` component uses it: tiled full-page,
under a near-opaque layer of the page background color — see
`_NOISE_BACKGROUND_CSS` below. `public/granite-1.jpg` and
`public/favicon-light.png` are staged from the same source for follow-up
work (per-chart granite backgrounds, page favicon) and unused so far.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import streamlit as st

BACKGROUND = "#383E47"
SURFACE = "#3F4650"
BORDER = "#4F5764"
TEXT = "#E5D3BE"
TEXT_MUTED = "#A0AEC0"
ACCENT = "#457dff"
GRID = "#4F5764"
FOOTER_BG = "#31373F"

# Grayscale-only stand-in for the theme's ACCENT, used where a blue accent
# would be too loud — currently just the active weight-scenario tab (see
# the tab-bar CSS below); everything else keeps using ACCENT.
NEUTRAL_HIGHLIGHT = "#4C5561"

# Applied to every series added to a chart, in order — restrained and
# readable rather than a default rainbow, so a 2-3 series chart reads as
# one system.
COLORWAY = [ACCENT, "#00d395", "#fdba74", "#f48975", "#b3aef5", "#6dadf9"]

FONT_FAMILY = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
)
MONO_FONT_FAMILY = "SFMono-Regular, Menlo, Consolas, monospace"

DEFAULT_CHART_HEIGHT = 420

_PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _noise_background_css() -> str:
    """Two-layer `background-image` for `.stApp`, replicating reclamm's
    `Noise` component: the grain PNG tiled full-page, with a near-opaque
    (97%) layer of the page background painted over it — CSS paints the
    first-listed background on top, so a flat 97%-opaque color sits above
    the tiled texture in one rule, no extra wrapper element needed. Returns
    "" (texture skipped, solid BACKGROUND still applies via
    `.streamlit/config.toml`) if the asset is missing.
    """
    noise_path = _PUBLIC_DIR / "background-noise.png"
    if not noise_path.is_file():
        return ""
    b64 = base64.b64encode(noise_path.read_bytes()).decode("ascii")
    r, g, b = _hex_to_rgb(BACKGROUND)
    return f"""
        .stApp {{
            background-image:
                linear-gradient(rgba({r},{g},{b},0.97), rgba({r},{g},{b},0.97)),
                url("data:image/png;base64,{b64}");
            background-repeat: no-repeat, repeat;
        }}
        """


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
    corners actually clip the chart's own background), address/link
    styling, the page-wide grain texture (see `_noise_background_css`), and
    the footer (see `render_footer`) — on top of the base theme from
    `.streamlit/config.toml`. Call once, right after `st.set_page_config`."""
    st.markdown(
        f"""
        <style>
        html, body, [class*="css"] {{
            font-family: {FONT_FAMILY};
        }}
        {_noise_background_css()}
        .block-container {{
            padding-top: 3rem;
            /* No bottom padding: the footer (render_footer, always the
            last section) provides its own via `.app-footer`'s padding.
            Leaving Streamlit's default padding here left a sliver below
            the footer's colored band, at the very bottom of the page,
            showing the plain page background/grain through instead. */
            padding-bottom: 0;
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
            color: #ffffff;
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

        /* Footer (render_footer) — reclamm-style link columns on a
        level0-dark card, sitting below the app content. Its background
        spans the full main content area (sidebar excluded), same as
        reclamm's own `<Box as="footer">` (full-width) wrapping a
        max-width-capped `DefaultPageContainer` — see `.footer-top` etc.
        below for that inner cap. `[data-testid="stMain"]` is what actually
        has that full width (`.block-container` is capped to 1200px and
        centered within it); declaring it a CSS containment context lets
        `.app-footer` size itself against that width via `cqw` container
        query units, escaping the 1200px cap exactly (not an approximation
        — no viewport units, so it isn't thrown off by the sidebar's
        width) — no JS needed (Streamlit strips `<script>` tags from
        `st.markdown`, confirmed empirically).

        Stops at the sidebar rather than passing under it — Streamlit
        renders `stMain` as its own independently-scrolling box, and the
        only ancestor positioned enough to escape past it sits outside
        that scroll context, so an element sized against it stops
        following the page's scroll (confirmed empirically: its on-screen
        position stopped updating while scrolling `stMain`). Full-bleed
        past the sidebar would need that container's live width, which
        needs JS — not available here. */
        [data-testid="stMain"] {{
            container-type: inline-size;
        }}
        .app-footer {{
            width: 100cqw;
            margin-left: calc(50% - 50cqw);
            margin-top: 3rem;
            padding: 2rem 1rem 1.5rem;
            background-color: {FOOTER_BG};
            border-top: 1px solid {BORDER};
        }}
        .footer-top {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            gap: 2rem;
            max-width: 1200px;
            margin: 0 auto;
        }}
        .footer-intro {{
            max-width: 26rem;
        }}
        .footer-title {{
            font-size: 1.1rem;
            font-weight: 700;
            color: {TEXT};
            margin-bottom: 0.4rem;
        }}
        .footer-subtitle {{
            font-size: 0.85rem;
            color: {TEXT_MUTED};
        }}
        .footer-cols {{
            display: flex;
            flex-wrap: wrap;
            gap: 2.5rem;
        }}
        .footer-col-title {{
            font-size: 0.7rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            color: {TEXT_MUTED};
            margin-bottom: 0.6rem;
        }}
        .footer-links {{
            display: flex;
            flex-direction: column;
            gap: 0.45rem;
        }}
        /* !important throughout this footer link/icon block: Streamlit's
        own base stylesheet sets a default anchor color (its usual link
        blue) that otherwise wins the cascade over this injected <style>
        block regardless of selector specificity — same reason the
        tab-bar CSS above needs it on stBaseButton-primary. */
        .footer-link {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            font-size: 0.85rem;
            color: {TEXT} !important;
            text-decoration: none !important;
        }}
        .footer-link:hover {{
            color: {ACCENT} !important;
        }}
        /* The arrow-up-right icon (_ARROW_UP_RIGHT_ICON) after each link
        label — muted like reclamm's own (`color="grayText"` there). */
        .footer-link svg {{
            color: {TEXT_MUTED} !important;
            flex-shrink: 0;
        }}
        .footer-divider {{
            max-width: 1200px;
            margin: 1.5rem auto 1rem;
            border-top: 1px solid {BORDER};
        }}
        .footer-bottom-row {{
            display: flex;
            align-items: center;
            gap: 1rem;
            max-width: 1200px;
            margin: 0 auto;
        }}
        .footer-bottom {{
            font-size: 0.78rem;
            color: {TEXT_MUTED};
        }}
        /* GitHub icon button (_GITHUB_ICON) — a round `background.level2`
        button, same as reclamm's `SocialLinks` icon buttons. */
        .footer-social {{
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            width: 36px;
            height: 36px;
            border-radius: 999px;
            background-color: {SURFACE};
            color: {TEXT_MUTED} !important;
        }}
        .footer-social:hover {{
            color: {TEXT} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# Inline SVGs ported from reclamm's own footer icon set — `currentColor`
# so each takes its CSS `color`, no separate fill constant needed.
# `_ARROW_UP_RIGHT_ICON`: reclamm's Footer marks every external link with
# this arrow (`react-feather`'s ArrowUpRight, size 12) right after the
# label; every link here is external, so every one gets it.
_ARROW_UP_RIGHT_ICON = (
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<line x1="7" y1="17" x2="17" y2="7"></line>'
    '<polyline points="7 7 17 7 17 17"></polyline>'
    "</svg>"
)
# `_GITHUB_ICON`: reclamm's `GithubIcon` (`icons/social/GithubIcon.tsx`),
# used the same way reclamm uses it — as a round social-icon button,
# alongside the disclaimer line, since GitHub is this project's one
# "social" link (this app has no X/Discord/Medium account to mirror
# reclamm's other `SocialLinks` entries).
_GITHUB_ICON = (
    '<svg width="18" height="18" viewBox="0 0 496 512" fill="currentColor">'
    '<path d="M165.9 397.4c0 2-2.3 3.6-5.2 3.6-3.3.3-5.6-1.3-5.6-3.6 0-2 2.3-3.6 5.2-3.6 3-.3 5.6 1.3 5.6 3.6zm-31.1-4.5c-.7 2 1.3 4.3 4.3 4.9 2.6 1 5.6 0 6.2-2s-1.3-4.3-4.3-5.2c-2.6-.7-5.5.3-6.2 2.3zm44.2-1.7c-2.9.7-4.9 2.6-4.6 4.9.3 2 2.9 3.3 5.9 2.6 2.9-.7 4.9-2.6 4.6-4.6-.3-1.9-3-3.2-5.9-2.9zM244.8 8C106.1 8 0 113.3 0 252c0 110.9 69.8 205.8 169.5 239.2 12.8 2.3 17.3-5.6 17.3-12.1 0-6.2-.3-40.4-.3-61.4 0 0-70 15-84.7-29.8 0 0-11.4-29.1-27.8-36.6 0 0-22.9-15.7 1.6-15.4 0 0 24.9 2 38.6 25.8 21.9 38.6 58.6 27.5 72.9 20.9 2.3-16 8.8-27.1 16-33.7-55.9-6.2-112.3-14.3-112.3-110.5 0-27.5 7.6-41.3 23.6-58.9-2.6-6.5-11.1-33.3 2.6-67.9 20.9-6.5 69 27 69 27 20-5.6 41.5-8.5 62.8-8.5s42.8 2.9 62.8 8.5c0 0 48.1-33.6 69-27 13.7 34.7 5.2 61.4 2.6 67.9 16 17.7 25.8 31.5 25.8 58.9 0 96.5-58.9 104.2-114.8 110.5 9.2 7.9 17 22.9 17 46.4 0 33.7-.3 75.4-.3 83.6 0 6.5 4.6 14.4 17.3 12.1C428.2 457.8 496 362.9 496 252 496 113.3 383.5 8 244.8 8zM97.2 352.9c-1.3 1-1 3.3.7 5.2 1.6 1.6 3.9 2.3 5.2 1 1.3-1 1-3.3-.7-5.2-1.6-1.6-3.9-2.3-5.2-1zm-10.8-8.1c-.7 1.3.3 2.9 2.3 3.9 1.6 1 3.6.7 4.3-.7.7-1.3-.3-2.9-2.3-3.9-2-.6-3.6-.3-4.3.7zm32.4 35.6c-1.6 1.3-1 4.3 1.3 6.2 2.3 2.3 5.2 2.6 6.5 1 1.3-1.3.7-4.3-1.3-6.2-2.2-2.3-5.2-2.6-6.5-1zm-11.4-14.7c-1.6 1-1.6 3.6 0 5.9 1.6 2.3 4.3 3.3 5.6 2.3 1.6-1.3 1.6-3.9 0-6.2-1.4-2.3-4-3.3-5.6-2z" />'
    "</svg>"
)

# label -> href, grouped under a section title — mirrors the shape of
# reclamm's own `PROJECT_CONFIG.footer.linkSections` (see
# `reclamm-monorepo/packages/lib/config/getProjectConfig.ts`), adapted to
# this project: no legal-page links here, since this app has none.
FOOTER_LINK_SECTIONS: list[dict[str, Any]] = [
    {
        "title": "DTFs On-Chain",
        "links": [
            {"label": "GitHub", "href": "https://github.com/neo-empresarial/BL008"},
            {
                "label": "Docs",
                "href": "https://github.com/neo-empresarial/BL008/blob/main/docs/rebalancing-simulator.md",
            },
        ],
    },
    {
        "title": "Platforms",
        "links": [
            {"label": "Glider", "href": "https://glider.fi"},
            {"label": "Reserve Protocol", "href": "https://reserve.org"},
            {"label": "QuantAMM / Balancer", "href": "https://balancer.fi"},
        ],
    },
    {
        "title": "Data sources",
        "links": [{"label": "DefiLlama", "href": "https://defillama.com"}],
    },
]


def render_footer() -> None:
    """Reclamm-style footer: project name/subtitle, link columns
    (`FOOTER_LINK_SECTIONS`, each entry marked with `_ARROW_UP_RIGHT_ICON`
    since every link here is external — matching reclamm's own `Footer`),
    a GitHub icon button (`_GITHUB_ICON`, reclamm's `SocialLinks` pattern),
    and a bottom disclaimer line. Call once, at the very end of the page."""
    columns_html = "".join(
        f"""
        <div>
            <div class="footer-col-title">{section['title']}</div>
            <div class="footer-links">
                {"".join(
                    f'<a class="footer-link" href="{link["href"]}" '
                    'target="_blank" rel="noopener noreferrer">'
                    f'{link["label"]}{_ARROW_UP_RIGHT_ICON}</a>'
                    for link in section["links"]
                )}
            </div>
        </div>
        """
        for section in FOOTER_LINK_SECTIONS
    )
    st.markdown(
        f"""
        <div class="app-footer">
            <div class="footer-top">
                <div class="footer-intro">
                    <div class="footer-title">DTFs On-Chain</div>
                    <div class="footer-subtitle">
                        Compares real on-chain basket rebalancing across
                        Glider, Reserve Protocol, and QuantAMM/Balancer.
                    </div>
                </div>
                <div class="footer-cols">{columns_html}</div>
            </div>
            <div class="footer-divider"></div>
            <div class="footer-bottom-row">
                <a class="footer-social" href="https://github.com/neo-empresarial/BL008"
                   target="_blank" rel="noopener noreferrer" aria-label="GitHub">
                    {_GITHUB_ICON}
                </a>
                <div class="footer-bottom">
                    Read-only comparison tool — not affiliated with Glider,
                    Reserve, or Balancer.
                </div>
            </div>
        </div>
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
