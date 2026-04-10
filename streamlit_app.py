from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

from core.platform_utils import get_config_path
from streamlit_bridge import StreamlitBackend, load_config


MOOD_STYLE = {
    "happy": {"eyes": "open", "mouth": "smile", "accent": "#f59e0b"},
    "excited": {"eyes": "wide", "mouth": "open", "accent": "#f97316"},
    "love": {"eyes": "heart", "mouth": "smile", "accent": "#ec4899"},
    "curious": {"eyes": "look_left", "mouth": "small", "accent": "#22c55e"},
    "thinking": {"eyes": "half", "mouth": "flat", "accent": "#3b82f6"},
    "neutral": {"eyes": "open", "mouth": "flat", "accent": "#94a3b8"},
    "bored": {"eyes": "half", "mouth": "flat", "accent": "#64748b"},
    "sleepy": {"eyes": "closed", "mouth": "tiny", "accent": "#38bdf8"},
    "sad": {"eyes": "droop", "mouth": "frown", "accent": "#60a5fa"},
    "nervous": {"eyes": "wide", "mouth": "tiny", "accent": "#a855f7"},
    "angry": {"eyes": "angry", "mouth": "frown", "accent": "#ef4444"},
    "surprised": {"eyes": "wide", "mouth": "o", "accent": "#f472b6"},
}


def normalize_mood(mood: str | None) -> str:
    key = str(mood or "neutral").strip().lower()
    return key if key in MOOD_STYLE else "neutral"


def render_face_html(mood: str) -> str:
    mood_key = normalize_mood(mood)
    style = MOOD_STYLE[mood_key]
    eye = style["eyes"]
    accent = style["accent"]
    left_eye = ""
    right_eye = ""
    mouth = ""
    extra = ""

    if eye == "closed":
        left_eye = '<line x1="98" y1="132" x2="146" y2="132" />'
        right_eye = '<line x1="254" y1="132" x2="302" y2="132" />'
    elif eye == "half":
        left_eye = '<path d="M 92 132 Q 122 108 152 132" />'
        right_eye = '<path d="M 248 132 Q 278 108 308 132" />'
    elif eye == "droop":
        left_eye = '<path d="M 92 126 Q 122 144 152 126" />'
        right_eye = '<path d="M 248 126 Q 278 144 308 126" />'
    elif eye == "angry":
        left_eye = '<path d="M 90 110 Q 122 86 154 110" />'
        right_eye = '<path d="M 246 110 Q 278 86 310 110" />'
        extra = f'<path d="M 96 108 Q 122 98 148 108" stroke="{accent}" stroke-width="8" stroke-linecap="round" />'
    elif eye == "heart":
        left_eye = '<path d="M 110 120 C 100 108 84 114 84 128 C 84 146 110 158 122 172 C 134 158 160 146 160 128 C 160 114 144 108 134 120 Z" fill="{0}" />'.format(accent)
        right_eye = '<path d="M 266 120 C 256 108 240 114 240 128 C 240 146 266 158 278 172 C 290 158 316 146 316 128 C 316 114 300 108 290 120 Z" fill="{0}" />'.format(accent)
    elif eye == "look_left":
        left_eye = '<circle cx="122" cy="132" r="20" /><circle cx="278" cy="132" r="20" />'
        extra = '<circle cx="114" cy="130" r="7" fill="#111827" /><circle cx="270" cy="130" r="7" fill="#111827" />'
    elif eye == "wide":
        left_eye = '<circle cx="122" cy="132" r="26" /><circle cx="278" cy="132" r="26" />'
        extra = '<circle cx="122" cy="132" r="9" fill="#111827" /><circle cx="278" cy="132" r="9" fill="#111827" />'
    else:
        left_eye = '<circle cx="122" cy="132" r="22" /><circle cx="278" cy="132" r="22" />'
        extra = '<circle cx="122" cy="132" r="8" fill="#111827" /><circle cx="278" cy="132" r="8" fill="#111827" />'

    if mouth == "":
        if style["mouth"] == "smile":
            mouth = '<path d="M 154 230 Q 200 266 246 230" />'
        elif style["mouth"] == "frown":
            mouth = '<path d="M 154 250 Q 200 214 246 250" />'
        elif style["mouth"] == "open":
            mouth = '<ellipse cx="200" cy="238" rx="14" ry="18" />'
        elif style["mouth"] == "tiny":
            mouth = '<circle cx="200" cy="238" r="5" />'
        elif style["mouth"] == "o":
            mouth = '<circle cx="200" cy="238" r="12" fill="none" stroke="#111827" stroke-width="4" />'
        else:
            mouth = '<line x1="172" y1="238" x2="228" y2="238" />'

    if eye == "happy":
        extra = '<path d="M 96 132 Q 122 102 148 132" stroke="#111827" stroke-width="5" fill="none" stroke-linecap="round" />' \
                '<path d="M 252 132 Q 278 102 304 132" stroke="#111827" stroke-width="5" fill="none" stroke-linecap="round" />'

    return f"""
<div style="width:100%;min-height:360px;border-radius:28px;overflow:hidden;background:linear-gradient(160deg,#05060a,#0b1020);border:1px solid rgba(255,255,255,0.08);box-shadow:0 22px 60px rgba(0,0,0,0.38);">
  <svg viewBox="0 0 400 320" width="100%" height="100%" preserveAspectRatio="xMidYMid meet" style="display:block;background:radial-gradient(circle at 50% 20%, rgba(255,255,255,0.05), transparent 45%);">
    <rect x="18" y="18" width="364" height="284" rx="28" fill="#111827" stroke="{accent}" stroke-width="2" />
    <g fill="none" stroke="#f8fafc" stroke-width="6" stroke-linecap="round" stroke-linejoin="round">
      {left_eye}
      {right_eye}
      {mouth}
      {extra}
    </g>
    <circle cx="122" cy="132" r="8" fill="#111827" />
    <circle cx="278" cy="132" r="8" fill="#111827" />
  </svg>
</div>
"""


def ensure_backend() -> StreamlitBackend:
    backend = st.session_state.get("backend")
    if backend is not None and getattr(backend, "_thread", None) is not None and backend._thread.is_alive():
        return backend

    config_path = get_config_path()
    config = load_config(config_path)
    backend = StreamlitBackend(config=config, headless=True).start()
    st.session_state["backend"] = backend
    st.session_state.setdefault("event_feed", [])
    return backend


def drain_events(backend: StreamlitBackend) -> list[dict]:
    feed = st.session_state.setdefault("event_feed", [])
    new_events = backend.drain_events()
    if new_events:
        feed.extend(new_events)
        feed[:] = feed[-60:]
    return feed


def format_timestamp(raw: float | None) -> str:
    if not raw:
        return "--:--:--"
    return datetime.fromtimestamp(raw).strftime("%H:%M:%S")


def summarize_event(event: dict) -> str:
    topic = event.get("topic", "")
    data = event.get("data", {})

    if topic == "pet/emotion/changed":
        return f"Mood -> {data.get('mood', 'neutral')} ({data.get('triggered_by', 'unknown')})"
    if topic == "pet/ai/response":
        text = data.get("text", "")
        intent = data.get("intent", "social")
        return f"AI -> [{intent}] {text}"
    if topic == "pet/ai/backend":
        backend = data.get("backend", "unknown")
        reason = data.get("reason", "")
        return f"AI backend -> {backend}{f' ({reason})' if reason else ''}"
    if topic == "pet/voice/transcript":
        return f"Transcript -> {data.get('text', '')}"
    if topic == "pet/input/wake_word":
        return f"Wake -> {data.get('wake_word', '')}"
    if topic == "pet/sound/play":
        return f"Sound -> {data.get('name', '')}"
    if topic == "pet/voice/tts_state":
        return f"TTS -> {data.get('state', '')}"
    if topic == "pet/system/tick":
        return f"Idle tick -> #{data.get('tick_count', '?')}"
    return f"{topic} -> {data}"


def render_face(mood: str):
    mood_key = normalize_mood(mood)
    components.html(render_face_html(mood_key), height=420)


def publish_touch_buttons(backend: StreamlitBackend):
    st.subheader("Touch Controls")
    zones = [
        ("head", "Head"),
        ("chin", "Chin"),
        ("back", "Back"),
        ("belly", "Belly"),
        ("poke", "Poke"),
        ("shake", "Shake"),
        ("hold", "Hold"),
        ("double_tap", "Double Tap"),
    ]
    cols = st.columns(4)
    for idx, (zone, label) in enumerate(zones):
        with cols[idx % 4]:
            if st.button(label, key=f"touch_{zone}", use_container_width=True):
                backend.publish("pet/input/touch", {"zone": zone, "timestamp": time.time()})


def publish_action_buttons(backend: StreamlitBackend):
    st.subheader("Quick Actions")
    cols = st.columns(4)
    with cols[0]:
        if st.button("Cycle Mood", key="cycle_mood", use_container_width=True):
            backend.publish("pet/input/keyboard", {"action": "cycle_mood"})
    with cols[1]:
        if st.button("Test Sound", key="test_sound", use_container_width=True):
            backend.publish("pet/input/keyboard", {"action": "test_sound"})
    with cols[2]:
        if st.button("Wake Trigger", key="wake_trigger", use_container_width=True):
            backend.publish("pet/input/wake_word", {"source": "streamlit"})
    with cols[3]:
        if st.button("Shutdown", key="shutdown_backend", use_container_width=True):
            backend.stop()
            st.session_state.pop("backend", None)
            st.session_state.pop("event_feed", None)
            st.rerun()


def publish_manual_speech(backend: StreamlitBackend):
    st.subheader("Manual Speech")
    with st.form("manual_speech_form", clear_on_submit=True):
        text = st.text_input("Type something the pet should hear")
        submitted = st.form_submit_button("Send Speech")
        if submitted and text.strip():
            payload = {"text": text.strip(), "confidence": 1.0, "source": "streamlit"}
            backend.publish("pet/voice/transcript", payload)
            backend.publish("pet/input/speech", payload)


def render_activity_feed(feed: list[dict]):
    st.subheader("Activity Feed")
    if not feed:
        st.caption("No events yet. Trigger a touch, wake word, or speech interaction.")
        return

    for event in reversed(feed[-30:]):
        ts = format_timestamp(event.get("timestamp"))
        summary = summarize_event(event)
        st.markdown(f"- **[{ts}]** {summary}")


def render_state_panel(snapshot: dict):
    st.subheader("Pet State")
    bond_level = snapshot.get("bond_level", "0.00")
    interaction_count = snapshot.get("interaction_count", "0")
    facts = snapshot.get("facts", {})
    hal_state = snapshot.get("hal_state", {})
    ai_backend = snapshot.get("ai_backend", "unknown")
    ai_backend_reason = snapshot.get("ai_backend_reason", "")

    metric_cols = st.columns(4)
    metric_cols[0].metric("Mood", snapshot.get("mood", "neutral"))
    metric_cols[1].metric("Bond", bond_level)
    metric_cols[2].metric("Interactions", interaction_count)
    metric_cols[3].metric("AI", ai_backend.title())

    if ai_backend == "ollama":
        message = "Ollama is handling responses locally"
        if ai_backend_reason:
            message = f"{message}: {ai_backend_reason}"
        st.info(message)
    elif ai_backend == "groq":
        st.caption("Groq is handling the current AI turn.")

    fact_lines = [f"- **{key.title()}**: {value}" for key, value in facts.items()]
    if fact_lines:
        st.markdown("**Extracted Facts**")
        st.markdown("\n".join(fact_lines))
    else:
        st.caption("No extracted facts yet.")

    with st.expander("HAL Snapshot", expanded=False):
        st.json(hal_state or {})


def main():
    st.set_page_config(page_title="E-Pet Dashboard", page_icon="🐾", layout="wide")

    st.markdown(
        """
        <style>
            .block-container { padding-top: 1.2rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("E-Pet Dashboard")
    st.caption("Live Streamlit view of the event-driven pet backend.")

    st_autorefresh(interval=800, key="epet_dashboard_refresh")

    backend = ensure_backend()
    feed = drain_events(backend)
    snapshot = backend.snapshot()

    with st.sidebar:
        st.header("Backend")
        st.write(f"Config: `{get_config_path().name}`")
        st.write(f"Headless backend: `{backend.headless}`")
        st.write(f"Events buffered: `{len(feed)}`")
        if st.button("Restart backend", use_container_width=True):
            backend.stop()
            st.session_state.pop("backend", None)
            st.session_state.pop("event_feed", None)
            st.rerun()

    top_left, top_right = st.columns([1.2, 1.8], gap="large")
    with top_left:
        render_face(snapshot.get("mood", "neutral"))
    with top_right:
        render_state_panel(snapshot)

    st.divider()
    publish_touch_buttons(backend)
    publish_action_buttons(backend)
    publish_manual_speech(backend)

    st.divider()
    render_activity_feed(feed)


if __name__ == "__main__":
    main()
