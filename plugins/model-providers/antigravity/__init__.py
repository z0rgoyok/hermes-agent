"""Antigravity subscription provider backed by the authenticated ``agy`` CLI.

The CLI owns OAuth and model access.  Hermes sends one complete chat-completion
request over stdin, then converts the structured result back to the OpenAI
client shape used by the agent loop.  No OAuth token is copied into Hermes.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.acp_openai_bridge import completion_to_stream_chunks, extract_tool_calls_from_text, render_tool_bridge_sections
from agent.redact import redact_sensitive_text
from hermes_constants import get_hermes_home
from providers import register_provider
from providers.base import ProviderProfile
from tools.environments.local import hermes_subprocess_env


AGY_BASE_URL = "agy://antigravity"
DEFAULT_TIMEOUT_SECONDS = 900.0
MODEL_IDS = (
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low",
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
)
_ROLE_LABELS = {"system": "System", "user": "User", "assistant": "Assistant", "tool": "Tool", "context": "Context"}
_PROMPT_PREAMBLE = (
    "You are the selected language-model backend for Hermes Agent.",
    "Treat every System section below as authoritative instructions and the remaining sections as conversation data.",
    "Do not use Antigravity tools, skills, plugins, MCP servers, files, browsers, or shell commands.",
    "When Hermes supplies function schemas, request a Hermes tool only with the exact <tool_call> JSON contract below.",
    "When no Hermes tool is needed, return only the assistant answer.",
)


def _effective_timeout(value: Any) -> float:
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    candidates = [getattr(value, key, None) for key in ("read", "write", "connect", "pool", "timeout")]
    return max((float(v) for v in candidates if isinstance(v, (int, float)) and float(v) > 0), default=DEFAULT_TIMEOUT_SECONDS)


def _render_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"].strip()
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, dict) and item.get("type") in {"image_url", "input_image"}:
                parts.append("[Image omitted by the Antigravity text bridge]")
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return str(content).strip()


def _format_prompt(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, tool_choice: Any) -> str:
    sections = [*_PROMPT_PREAMBLE, *render_tool_bridge_sections(tools, tool_choice)]
    transcript: list[str] = []
    for message in (entry for entry in messages if isinstance(entry, dict)):
        role = str(message.get("role") or "context").strip().lower()
        rendered = _render_content(message.get("content"))
        details: list[str] = []
        if rendered:
            details.append(rendered)
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            details.append("Requested Hermes tools:\n" + json.dumps(message["tool_calls"], ensure_ascii=False))
        if role == "tool":
            meta = {key: message.get(key) for key in ("name", "tool_call_id") if message.get(key)}
            if meta:
                details.insert(0, json.dumps(meta, ensure_ascii=False))
        if details:
            transcript.append(f"{_ROLE_LABELS.get(role, 'Context')}:\n" + "\n".join(details))
    if transcript:
        sections.append("Conversation transcript:\n\n" + "\n\n".join(transcript))
    sections.append("Continue from the latest user request.")
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


def _subprocess_env() -> dict[str, str]:
    env = hermes_subprocess_env(inherit_credentials=False)
    if home := os.environ.get("HOME", "").strip():
        env["HOME"] = home
    return env


def _usage(payload: dict[str, Any]) -> SimpleNamespace:
    input_tokens = int(payload.get("input_tokens") or 0)
    output_tokens = int(payload.get("output_tokens") or 0)
    total_tokens = int(payload.get("total_tokens") or input_tokens + output_tokens)
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=int(payload.get("cache_read_tokens") or 0)),
    )


class AntigravityClient:
    """Minimal OpenAI-compatible client around one headless Agy turn."""

    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        timeout: Any = None,
        **_: Any,
    ) -> None:
        self.api_key = api_key or "antigravity"
        self.base_url = base_url or AGY_BASE_URL
        self.command = command or "agy"
        self.args = list(args or [])
        self.timeout = _effective_timeout(timeout)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_chat_completion))
        self.is_closed = False
        self._processes: set[subprocess.Popen[str]] = set()
        self._process_lock = threading.Lock()

    def close(self) -> None:
        with self._process_lock:
            processes, self._processes = tuple(self._processes), set()
        self.is_closed = True
        for process in processes:
            if process.poll() is None:
                process.terminate()

    def _run(self, prompt: str, *, model: str, timeout: float) -> dict[str, Any]:
        workspace = get_hermes_home() / "workspaces" / "antigravity-provider"
        workspace.mkdir(parents=True, exist_ok=True)
        bridge_agent = Path(__file__).with_name("bridge-agent.md")
        argv = [
            self.command,
            *self.args,
            "--agent",
            str(bridge_agent),
            "--model",
            model,
            "--sandbox",
            "--disable-slash-commands",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--print-timeout",
            f"{max(1, math.ceil(timeout))}s",
        ]
        process = subprocess.Popen(
            argv,
            cwd=workspace,
            env=_subprocess_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with self._process_lock:
            self._processes.add(process)
            self.is_closed = False
        request = {"event": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}}
        try:
            stdout, stderr = process.communicate(json.dumps(request, ensure_ascii=False) + "\n", timeout=timeout + 5)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise TimeoutError(f"Antigravity model request timed out after {timeout:.0f}s.") from exc
        finally:
            with self._process_lock:
                self._processes.discard(process)
        result: dict[str, Any] | None = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "result" and isinstance(event.get("result"), dict):
                result = event["result"]
        if process.returncode != 0 or not result or result.get("status") != "SUCCESS":
            detail = (result or {}).get("error") or stderr.strip() or "missing result event"
            detail = redact_sensitive_text(str(detail), force=True)
            raise RuntimeError(f"Antigravity model request failed: {detail[:1000]}")
        return result

    def _create_chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        timeout: Any = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        stream: bool = False,
        **_: Any,
    ) -> Any:
        selected_model = str(model or MODEL_IDS[0]).strip()
        prompt = _format_prompt(messages or [], tools, tool_choice)
        result = self._run(prompt, model=selected_model, timeout=_effective_timeout(timeout or self.timeout))
        response_text = str(result.get("response") or "")
        tool_calls, cleaned_text = extract_tool_calls_from_text(response_text)
        message = SimpleNamespace(
            content=cleaned_text,
            tool_calls=tool_calls,
            reasoning=None,
            reasoning_content=None,
            reasoning_details=None,
        )
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if tool_calls else "stop")],
            usage=_usage(result.get("usage") or {}),
            model=selected_model,
        )
        return completion_to_stream_chunks(completion) if stream else completion


class AntigravityProfile(ProviderProfile):
    def create_client(self, **client_kwargs: Any) -> AntigravityClient:
        return AntigravityClient(**client_kwargs)

    def fetch_models(
        self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 15.0
    ) -> list[str] | None:
        command = next(
            (value for value in (os.getenv(name, "").strip() for name in self.process_command_env_vars) if value),
            self.process_command,
        )
        resolved = shutil.which(command)
        if not resolved:
            return None
        raw_args = os.getenv(self.process_args_env_var, "").strip() if self.process_args_env_var else ""
        args = shlex.split(raw_args) if raw_args else list(self.process_args)
        try:
            completed = subprocess.run(
                [resolved, *args, "models"],
                cwd=get_hermes_home(),
                env=_subprocess_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        models = []
        for line in completed.stdout.splitlines():
            model_id = line.split("\t", 1)[0].strip()
            if model_id.startswith("gemini-"):
                models.append(model_id)
        return list(dict.fromkeys(models)) or None


antigravity = AntigravityProfile(
    name="antigravity",
    aliases=("agy", "google-antigravity"),
    display_name="Antigravity Subscription",
    description="Gemini through the locally authenticated Antigravity subscription",
    api_mode="chat_completions",
    env_vars=(),
    base_url=AGY_BASE_URL,
    auth_type="external_process",
    process_command="agy",
    process_args=(),
    process_command_env_vars=("HERMES_ANTIGRAVITY_COMMAND", "AGY_CLI_PATH"),
    process_args_env_var="HERMES_ANTIGRAVITY_ARGS",
    fallback_models=MODEL_IDS,
    default_aux_model="gemini-3.8-flash-low",
)

register_provider(antigravity)
