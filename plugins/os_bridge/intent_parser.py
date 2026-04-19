from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedIntent:
    matched: bool
    actions: list[dict]
    response_text: str


PATTERNS = [
    (r"set volume to (\d+)", lambda m: [{"type": "set_volume", "level": int(m.group(1))}]),
    (r"volume (up|down)", lambda m: [{"type": "set_volume", "level": 70 if m.group(1) == "up" else 30}]),
    (r"^(mute|unmute|silence)$", lambda m: [{"type": "mute" if m.group(1) in {"mute", "silence"} else "unmute"}]),
    (r"set brightness to (\d+)", lambda m: [{"type": "set_brightness", "level": int(m.group(1))}]),
    (
        r"^(?:open|visit|go to)\s+(youtube|google|gmail|github|reddit|twitter|x|linkedin|netflix|spotify|amazon|wikipedia|stack overflow|chatgpt|claude|notion|figma|vercel|heroku|aws console|google cloud|azure)\s*$",
        lambda m: [{"type": "open_website", "name": m.group(1).strip()}],
    ),
    (
        r"^(?:search|look up)(?:\s+for)?\s+(.+?)\s+on\s+(youtube|google)\s*$",
        lambda m: [{"type": f"{m.group(2).strip().lower()}_search", "query": m.group(1).strip()}],
    ),
    (
        r"^(?:play|watch|find|show me)\s+(.+?)\s+(?:on\s+)?youtube\s*$",
        lambda m: [{"type": "youtube_search", "query": m.group(1).strip()}],
    ),
    (
        r"^(?:play|watch|find|show me)\s+(.+?)\s+video\s*$",
        lambda m: [{"type": "youtube_search", "query": m.group(1).strip()}],
    ),
    (
        r"^(?:read|say|tell me|paste|type)\s+.*\bscreen\b.*\b(?:text ?edit|textit|text it|text editor)\b.*$",
        lambda m: [
            {"type": "read_screen"},
            {"type": "open_app", "target": "TextEdit"},
            {"type": "type_text", "text": "{{step_1.result.text}}", "method": "clipboard"},
        ],
    ),
    (
        r"^(?:read|say|tell me)\s+.*\b(?:screen|display)\b.*$",
        lambda m: [{"type": "read_screen"}],
    ),
    (r"^(open|launch|start) (.+)$", lambda m: [{"type": "open_app", "target": m.group(2).strip()}]),
    (r"^(close|quit) (.+)$", lambda m: [{"type": "quit_app", "target": m.group(2).strip()}]),
    (
        r"^(?:go to|open|visit|browse to) (.+?)(?: website| site)?$",
        lambda m: [{"type": "open_url", "url": m.group(1)}],
    ),
    (
        r"^(?:open|launch|start)\s+(youtube|google|gmail|github|reddit|twitter|x|linkedin|netflix|spotify|amazon|wikipedia|stack overflow|chatgpt|claude|notion|figma|vercel|heroku|aws console|google cloud|azure)\s*$",
        lambda m: [{"type": "open_website", "name": m.group(1).strip()}],
    ),
    (r"^search (?:for |)(.+?) on google$", lambda m: [{"type": "google_search", "query": m.group(1)}]),
    (r"^(?:youtube|play on youtube)\s+(.+)$", lambda m: [{"type": "youtube_search", "query": m.group(1)}]),
    (r"take a screenshot", lambda m: [{"type": "screenshot"}]),
    (r"lock (?:the |my |)(?:screen|computer|mac|pc)", lambda m: [{"type": "lock_screen"}]),
    (r"empty (?:the |)trash", lambda m: [{"type": "empty_trash"}]),
    (r"(?:what(?:'s| is) (?:my |the |)|check (?:my |the |))battery", lambda m: [{"type": "get_battery_status"}]),
    (r"(?:what(?:'s| is) (?:my |the |)|check (?:the |))(?:wifi|wi-fi|network|internet)", lambda m: [{"type": "check_internet_connection"}]),
    (r"(?:what(?:'s| is) the |check |)weather", lambda m: [{"type": "get_weather"}]),
    (r"^(?:play|pause|resume)$", lambda m: [{"type": "play_pause_media"}]),
    (r"^(?:next|skip) (?:song|track)$", lambda m: [{"type": "next_track"}]),
    (r"^(?:previous|last|back) (?:song|track)$", lambda m: [{"type": "previous_track"}]),
    (r"^play (.+?) by (.+)$", lambda m: [{"type": "spotify_play", "song": m.group(1), "artist": m.group(2)}]),
    (r"^play (.+?) on spotify$", lambda m: [{"type": "spotify_play", "song": m.group(1)}]),
    (r"^(?:press |hit |use |)copy$", lambda m: [{"type": "named_hotkey", "name": "copy"}]),
    (r"^(?:press |hit |use |)paste$", lambda m: [{"type": "named_hotkey", "name": "paste"}]),
    (r"^(?:press |hit |use |)undo$", lambda m: [{"type": "named_hotkey", "name": "undo"}]),
    (r"^(?:press |hit |use |)save$", lambda m: [{"type": "named_hotkey", "name": "save"}]),
    (r"^(?:press |use |)(?:spotlight|cmd space|command space)$", lambda m: [{"type": "named_hotkey", "name": "spotlight"}]),
    (r"^type [\"'](.+?)[\"']$", lambda m: [{"type": "type_text", "text": m.group(1)}]),
    (r"^type (.+)$", lambda m: [{"type": "type_text", "text": m.group(1)}]),
]


def _clean_target(value: str) -> str:
    return str(value or "").strip().strip(" .,!?:;\"'")


def parse_command(text: str) -> ParsedIntent:
    cleaned = text.strip().rstrip(" .,!?:;")
    for pattern, action_builder in PATTERNS:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            try:
                actions = action_builder(match)
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if action.get("type") in {"open_app", "quit_app", "focus_app"}:
                        if "target" in action:
                            action["target"] = _clean_target(action["target"])
                    elif action.get("type") == "open_website":
                        if "name" in action:
                            action["name"] = _clean_target(action["name"])
                    elif action.get("type") in {"open_url", "google_search", "youtube_search"}:
                        if "url" in action:
                            action["url"] = _clean_target(action["url"])
                        if "query" in action:
                            action["query"] = _clean_target(action["query"])
                response = ""
                if actions:
                    primary = actions[0].get("type", "")
                    if primary == "open_app":
                        response = f"Opening {actions[0].get('target', '')}."
                    elif primary == "open_website":
                        response = f"Opening {actions[0].get('name', '')}."
                    elif primary == "youtube_search":
                        query = actions[0].get("query", "")
                        response = f"Searching YouTube for {query}." if query else "Opening YouTube."
                    elif primary == "quit_app":
                        response = f"Closing {actions[0].get('target', '')}."
                    elif primary in {"set_volume", "set_brightness"}:
                        response = "Done."
                    elif primary == "named_hotkey":
                        response = f"Using {actions[0].get('name', '')}."
                    else:
                        response = "Done."
                return ParsedIntent(matched=True, actions=actions, response_text=response)
            except Exception:
                continue
    fallback = cleaned
    fallback = re.sub(r"\bfor you(?:\s+now)?\b", "", fallback, flags=re.IGNORECASE).strip()
    fallback = re.sub(r"\bplease\b", "", fallback, flags=re.IGNORECASE).strip()
    fallback = re.sub(r"\bnow\b$", "", fallback, flags=re.IGNORECASE).strip()
    if fallback != cleaned:
        for pattern, action_builder in PATTERNS:
            match = re.search(pattern, fallback, re.IGNORECASE)
            if match:
                try:
                    actions = action_builder(match)
                    for action in actions:
                        if not isinstance(action, dict):
                            continue
                        if action.get("type") in {"open_app", "quit_app", "focus_app"}:
                            if "target" in action:
                                action["target"] = _clean_target(action["target"])
                        elif action.get("type") == "open_website":
                            if "name" in action:
                                action["name"] = _clean_target(action["name"])
                        elif action.get("type") in {"open_url", "google_search", "youtube_search"}:
                            if "url" in action:
                                action["url"] = _clean_target(action["url"])
                            if "query" in action:
                                action["query"] = _clean_target(action["query"])
                    response = ""
                    if actions:
                        primary = actions[0].get("type", "")
                        if primary == "open_app":
                            response = f"Opening {actions[0].get('target', '')}."
                        elif primary == "open_website":
                            response = f"Opening {actions[0].get('name', '')}."
                        elif primary == "youtube_search":
                            query = actions[0].get("query", "")
                            response = f"Searching YouTube for {query}." if query else "Opening YouTube."
                        elif primary == "quit_app":
                            response = f"Closing {actions[0].get('target', '')}."
                        elif primary in {"set_volume", "set_brightness"}:
                            response = "Done."
                        elif primary == "named_hotkey":
                            response = f"Using {actions[0].get('name', '')}."
                        else:
                            response = "Done."
                    return ParsedIntent(matched=True, actions=actions, response_text=response)
                except Exception:
                    continue
    return ParsedIntent(matched=False, actions=[], response_text="")
