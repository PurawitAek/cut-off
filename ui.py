"""Brand chrome for the workbench — header bar, fonts, card styling.

Kept separate from app.py so the LINE BK visual identity lives in one place.
"""
from __future__ import annotations
import base64
import os
import streamlit as st

from config import BRAND_GREEN, BRAND_GREEN_DEEP, BRAND_BLUE, INK, MUTE, APP_BG

_ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")
LOGO_PATH = os.path.join(_ASSET_DIR, "line_bk_logo.png")
MARK_PATH = os.path.join(_ASSET_DIR, "line_bk_mark.png")


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def inject_theme() -> None:
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=Inter:wght@400;500;600&display=swap');

    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}

    .stApp {{ background: {APP_BG}; }}

    h1, h2, h3, h4, .lbk-title {{
        font-family: 'Sora', sans-serif;
        color: {INK};
        letter-spacing: -0.01em;
    }}

    [data-testid="stMetric"] {{
        background: #FFFFFF;
        border: 1px solid #E1ECE3;
        border-left: 3px solid {BRAND_GREEN};
        border-radius: 10px;
        padding: 0.8rem 1rem 0.65rem;
        box-shadow: 0 1px 2px rgba(18,30,20,0.05);
        overflow: hidden;
        min-width: 0;
    }}
    [data-testid="stMetricValue"] {{
        font-feature-settings: 'tnum' 1;
        color: {INK};
        font-size: clamp(0.78rem, 1.3vw, 1.05rem) !important;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    [data-testid="stMetricLabel"] {{
        color: {MUTE};
        font-size: clamp(0.65rem, 0.9vw, 0.8rem) !important;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    [data-testid="stMetricDelta"] {{
        font-size: clamp(0.6rem, 0.8vw, 0.75rem) !important;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    [data-testid="column"] {{ min-width: 0; }}

    [data-testid="stSidebar"] {{
        background: #FFFFFF;
        border-right: 1px solid #E1ECE3;
    }}
    [data-testid="stSidebar"] h2 {{ font-size: 1rem; }}

    .stButton button {{
        border-radius: 8px;
        font-weight: 600;
    }}
    .stButton button[kind="primary"] {{
        background: {BRAND_GREEN_DEEP};
        border-color: {BRAND_GREEN_DEEP};
    }}
    .stButton button[kind="primary"]:hover {{
        background: {BRAND_GREEN};
        border-color: {BRAND_GREEN};
    }}

    [data-testid="stSegmentedControl"] label {{ border-radius: 999px !important; }}

    .lbk-header {{
        display: flex;
        align-items: center;
        gap: 0.85rem;
        padding: 0.2rem 0 0.1rem;
    }}
    .lbk-header img {{ height: 30px; display: block; }}
    .lbk-header-text .lbk-title {{
        font-size: 1.4rem;
        font-weight: 700;
        margin: 0;
        line-height: 1.15;
    }}
    .lbk-header-text .lbk-caption {{
        font-size: 0.85rem;
        color: {MUTE};
        margin: 0.1rem 0 0;
    }}
    .lbk-hairline {{
        height: 3px;
        margin: 0.55rem 0 1.1rem;
        background: linear-gradient(90deg, {BRAND_GREEN} 0%, {BRAND_GREEN} 45%, {BRAND_BLUE} 100%);
        border-radius: 2px;
    }}
    </style>
    """, unsafe_allow_html=True)


def render_header(title: str, caption: str) -> None:
    logo_b64 = _b64(LOGO_PATH)
    st.markdown(f"""
    <div class="lbk-header">
        <img src="data:image/png;base64,{logo_b64}" alt="LINE BK" />
        <div class="lbk-header-text">
            <p class="lbk-title">{title}</p>
            <p class="lbk-caption">{caption}</p>
        </div>
    </div>
    <div class="lbk-hairline"></div>
    """, unsafe_allow_html=True)
