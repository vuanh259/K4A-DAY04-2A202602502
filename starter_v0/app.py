from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from chat import (
    now_iso,
    run_model_tool_loop,
    safe_slug,
    trim_history,
    write_transcript,
)
from env_loader import load_lab_env
from providers import make_provider
from tools import load_tool_declarations, to_openai_tools
from versioning import artifact_version_dict, build_artifact_version


ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = ROOT / "artifacts"
SYSTEM_PROMPT_PATH = ARTIFACTS_DIR / "system_prompt.md"
TOOLS_PATH = ARTIFACTS_DIR / "tools.yaml"
TRANSCRIPTS_DIR = ROOT / "transcripts"

load_lab_env(ROOT)

st.set_page_config(
    page_title="IT Helpdesk Agent",
    page_icon="🛠️",
    layout="wide",
)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def render_assistant_response(text: str | None) -> None:
    """Render valid JSON as structured data, otherwise show raw text."""
    response_text = (text or "").strip()

    if not response_text:
        st.info("Agent không trả về nội dung.")
        return

    try:
        parsed = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        st.markdown(response_text)
        return

    st.json(parsed)


def render_tool_trace(rounds: list[dict[str, Any]]) -> None:
    if not rounds:
        return

    with st.expander("Tool trace", expanded=False):
        for round_record in rounds:
            round_number = round_record.get("round", "?")
            calls = round_record.get("tool_calls") or []
            results = round_record.get("tool_results") or []

            st.markdown(f"#### Round {round_number}")

            round_text = round_record.get("assistant_text")
            if round_text:
                st.caption("Assistant text trong round")
                st.code(round_text)

            if not calls:
                st.caption("Không có tool call.")
                continue

            for index, call in enumerate(calls):
                tool_name = call.get("name", "unknown_tool")
                args = call.get("args") or {}

                st.markdown(f"**Tool {index + 1}: `{tool_name}`**")
                st.caption("Arguments")
                st.json(args)

                if index >= len(results):
                    st.warning("Không có tool result tương ứng.")
                    continue

                event = results[index]
                result = event.get("result")

                if isinstance(result, dict) and result.get("error"):
                    st.error(
                        f"Tool error: {result.get('error')}"
                    )

                st.caption("Result")
                st.json(result if result is not None else event)


def create_session(
    *,
    provider_name: str,
    model_name: str | None,
    version_label: str,
    artifact_version: Any,
    history_window: int,
    max_tool_rounds: int,
    config_signature: tuple[Any, ...],
) -> None:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    transcript_id = "_".join(
        [
            safe_slug(version_label),
            safe_slug(provider_name),
            timestamp,
        ]
    )

    transcript_path = (
        TRANSCRIPTS_DIR / f"{transcript_id}.transcript.json"
    )

    transcript = {
        "transcript_id": transcript_id,
        **artifact_version_dict(artifact_version),
        "provider": provider_name,
        "model": model_name,
        "system_prompt": str(SYSTEM_PROMPT_PATH),
        "tools": str(TOOLS_PATH),
        "history_window": history_window,
        "max_tool_rounds": max_tool_rounds,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "turns": [],
    }

    st.session_state.config_signature = config_signature
    st.session_state.history = []
    st.session_state.transcript = transcript
    st.session_state.transcript_path = transcript_path


def reset_session() -> None:
    for key in (
        "config_signature",
        "history",
        "transcript",
        "transcript_path",
    ):
        st.session_state.pop(key, None)


# -------------------------------------------------------------------
# Sidebar configuration
# -------------------------------------------------------------------

with st.sidebar:
    st.header("Cấu hình")

    provider_name = st.selectbox(
        "Provider",
        options=["openrouter", "openai", "anthropic", "gemini"],
        index=0,
    )

    model_input = st.text_input(
        "Model override",
        value="",
        help="Để trống để dùng model mặc định của provider.",
    )
    model_name = model_input.strip() or None

    version_label = "v3"

    st.text_input(
        "Artifact version",
        value=version_label,
        disabled=True,
        help="UI sử dụng artifact cuối trong system_prompt.md và tools.yaml.",
    )

    history_window = st.number_input(
        "History window",
        min_value=1,
        max_value=20,
        value=5,
        step=1,
        help="Số cặp user/assistant gần nhất được đưa vào context.",
    )

    max_tool_rounds = st.number_input(
        "Max tool rounds",
        min_value=1,
        max_value=10,
        value=4,
        step=1,
    )


# -------------------------------------------------------------------
# Load the current artifacts dynamically
# -------------------------------------------------------------------

try:
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    tool_declarations = load_tool_declarations(TOOLS_PATH)
    openai_tools = to_openai_tools(tool_declarations)

    artifact_version = build_artifact_version(
        version_label,
        SYSTEM_PROMPT_PATH,
        TOOLS_PATH,
    )
except Exception as exc:
    st.error(
        f"Không thể tải artifact: {type(exc).__name__}: {exc}"
    )
    st.stop()


config_signature = (
    provider_name,
    model_name,
    artifact_version.artifact_version,
    int(history_window),
    int(max_tool_rounds),
)

if st.session_state.get("config_signature") != config_signature:
    create_session(
        provider_name=provider_name,
        model_name=model_name,
        version_label=version_label,
        artifact_version=artifact_version,
        history_window=int(history_window),
        max_tool_rounds=int(max_tool_rounds),
        config_signature=config_signature,
    )


with st.sidebar:
    st.divider()
    st.subheader("Artifact")

    st.code(artifact_version.artifact_version)
    st.caption(f"Prompt hash: {artifact_version.prompt_hash}")
    st.caption(f"Tools hash: {artifact_version.tools_hash}")

    st.subheader("Tools đã khai báo")
    for declaration in tool_declarations:
        st.write(f"- `{declaration.get('name', 'unknown')}`")

    st.divider()

    if st.button("Cuộc hội thoại mới", use_container_width=True):
        reset_session()
        st.rerun()

    transcript_path: Path = st.session_state.transcript_path
    st.caption(f"Transcript: {transcript_path}")

    transcript_download = json_text(
        st.session_state.transcript
    ).encode("utf-8")

    st.download_button(
        "Tải transcript",
        data=transcript_download,
        file_name=transcript_path.name,
        mime="application/json",
        use_container_width=True,
    )


# -------------------------------------------------------------------
# Main chat area
# -------------------------------------------------------------------

st.title("🛠️ IT Helpdesk Agent")
st.caption(
    "Live chat với tool trace, arguments, results, status và artifact version."
)

st.info(
    f"Đang sử dụng `{artifact_version.artifact_version}` "
    f"với provider `{provider_name}`."
)

transcript = st.session_state.transcript

for turn in transcript.get("turns", []):
    with st.chat_message("user"):
        st.markdown(turn.get("user", ""))

    with st.chat_message("assistant"):
        status = turn.get("status", "unknown")

        if status == "provider_error":
            st.error(turn.get("error", "Provider error"))
        else:
            render_assistant_response(turn.get("assistant_text"))

        st.caption(f"Status: `{status}`")
        render_tool_trace(turn.get("rounds") or [])


# -------------------------------------------------------------------
# Handle new message
# -------------------------------------------------------------------

user_text = st.chat_input("Nhập yêu cầu IT helpdesk...")

if user_text:
    turn_index = len(transcript.get("turns", [])) + 1

    turn_record: dict[str, Any] = {
        "turn_index": turn_index,
        "started_at": now_iso(),
        "user": user_text,
        "status": "started",
        "assistant_text": None,
        "rounds": [],
        "tool_events": [],
    }

    try:
        provider = make_provider(provider_name)
        selected_model = (
            model_name
            or getattr(provider, "default_model", None)
        )

        transcript["model"] = selected_model

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            *trim_history(
                st.session_state.history,
                int(history_window),
            ),
            {
                "role": "user",
                "content": user_text,
            },
        ]

        with st.spinner("Agent đang xử lý..."):
            result = run_model_tool_loop(
                provider=provider,
                messages=messages,
                tools=openai_tools,
                model=model_name,
                max_tool_rounds=int(max_tool_rounds),
            )

        turn_record.update(result)

        assistant_text = result.get("assistant_text") or ""

        st.session_state.history.append(
            {
                "role": "user",
                "content": user_text,
            }
        )
        st.session_state.history.append(
            {
                "role": "assistant",
                "content": assistant_text,
            }
        )

    except Exception as exc:
        turn_record.update(
            {
                "status": "provider_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )

    turn_record["ended_at"] = now_iso()
    transcript.setdefault("turns", []).append(turn_record)

    write_transcript(
        st.session_state.transcript_path,
        transcript,
    )

    st.rerun()
