from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from core.platform_utils import get_config_path
from streamlit_bridge import StreamlitBackend, load_config


FACE_ART = {
    "happy": {"emoji": "😀", "label": "Happy", "accent": "#f4c542"},
    "excited": {"emoji": "🤩", "label": "Excited", "accent": "#f59e0b"},
    "love": {"emoji": "🥰", "label": "Love", "accent": "#ec4899"},
    "curious": {"emoji": "🤔", "label": "Curious", "accent": "#22c55e"},
    "thinking": {"emoji": "🧠", "label": "Thinking", "accent": "#3b82f6"},
    "neutral": {"emoji": "🙂", "label": "Neutral", "accent": "#94a3b8"},
    "bored": {"emoji": "😑", "label": "Bored", "accent": "#64748b"},
    "sleepy": {"emoji": "😴", "label": "Sleepy", "accent": "#0f172a"},
    "sad": {"emoji": "😢", "label": "Sad", "accent": "#60a5fa"},
    "nervous": {"emoji": "😬", "label": "Nervous", "accent": "#a855f7"},
    "angry": {"emoji": "😠", "label": "Angry", "accent": "#ef4444"},
    "surprised": {"emoji": "😲", "label": "Surprised", "accent": "#ec4899"},
}


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
    info = FACE_ART.get(mood, FACE_ART["neutral"])
    st.markdown(
        f"""
        <div style="padding: 1.25rem; border-radius: 24px; background: linear-gradient(135deg, {info['accent']}22, #0f172a11); border: 1px solid #1f2937;">
            <div style="font-size: 5rem; line-height: 1; text-align: center;">{info['emoji']}</div>
            <div style="text-align: center; font-size: 1.1rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin-top: 0.5rem;">
                {info['label']}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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

    metric_cols = st.columns(3)
    metric_cols[0].metric("Mood", snapshot.get("mood", "neutral"))
    metric_cols[1].metric("Bond", bond_level)
    metric_cols[2].metric("Interactions", interaction_count)

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
