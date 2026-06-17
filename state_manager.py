"""Persist workbench state across page refreshes.

On first load of a fresh browser session, restores session_state from
.app_state.json. At the end of every run, serialises the same keys back.

Design note — data editors
--------------------------
st.data_editor stores internal edit-tracking state (not a plain DataFrame)
under its widget key.  We cannot set those keys via session_state, and we
cannot safely serialise them.  Instead, sidebar.py writes the *derived*
values (PD list, E31 list, ECON dict, …) into plain "_save_*" keys after
each editor call.  Those JSON-native values are what we persist here.
"""
from __future__ import annotations
import json
import os
import streamlit as st

_HERE = os.path.dirname(__file__)
STATE_FILE = os.path.join(_HERE, ".app_state.json")

_PERSIST_EXACT: frozenset[str] = frozenset({
    # Column mapping (selectbox — safe to restore via session_state)
    "score_col", "grade_col", "seg_col", "prod_col",
    # Segment override pickers (multiselect — safe)
    "pd_seg_pick", "e31_seg_pick", "econ_seg_pick",
    # AQI number inputs (number_input — safe)
    "aqi_cc", "aqi_lgd", "aqi_pd", "aqi_lc",
    # Cutoff controls (radio / segmented_control — safe)
    "cutoff_mode", "opt_target", "active_section",
    # Derived assumption values staged by sidebar.py (plain Python, not widget keys)
    "_save_PD", "_save_E31", "_save_grade_bands", "_save_bands_df",
    "_save_ECON", "_save_PD_SEG", "_save_E31_SEG", "_save_ECON_SEG",
})
# Prefix-matched: per-segment cutoff sliders, separated by mode so grade values
# (1–10) and score values (300–900) never overwrite each other when mode changes.
_PERSIST_PREFIXES: tuple[str, ...] = ("cut_grade_", "cut_score_")


def _should_persist(key: str) -> bool:
    return key in _PERSIST_EXACT or any(key.startswith(p) for p in _PERSIST_PREFIXES)


def save_state() -> None:
    """Write all persistable session_state keys to STATE_FILE."""
    data: dict = {}
    for k, v in st.session_state.items():
        if not _should_persist(k):
            continue
        try:
            json.dumps(v)       # verify JSON-serialisable
            data[k] = v
        except (TypeError, ValueError):
            pass
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def load_state() -> None:
    """Restore session_state from STATE_FILE on the first run of a fresh session."""
    if "_state_loaded" in st.session_state:
        return
    st.session_state["_state_loaded"] = True
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        for k, v in data.items():
            if _should_persist(k) and k not in st.session_state:
                st.session_state[k] = v     # all values are JSON-native; set directly
    except Exception:
        pass                                # corrupt file — silently ignore


def reset_state() -> None:
    """Delete STATE_FILE and clear all persisted keys from session_state."""
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except OSError:
        pass
    for k in [k for k in list(st.session_state) if _should_persist(k)]:
        del st.session_state[k]
