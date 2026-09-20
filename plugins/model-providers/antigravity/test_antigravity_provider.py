from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from providers import get_provider_profile


@pytest.fixture
def fake_agy(tmp_path: Path) -> Path:
    executable = tmp_path / "agy"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if len(sys.argv) > 1 and sys.argv[-1] == 'models':\n"
        "    print('gemini-3.8-flash-high\\tGemini 3.8 Flash (High)')\n"
        "    print('gemini-3.8-flash-low\\tGemini 3.8 Flash (Low)')\n"
        "    raise SystemExit(0)\n"
        "request = json.loads(sys.stdin.readline())\n"
        "prompt = request['message']['content'][0]['text']\n"
        "response = '<tool_call>{\"id\":\"call_1\",\"type\":\"function\",\"function\":{\"name\":\"lookup\",\"arguments\":\"{}\"}}</tool_call>' if 'lookup' in prompt else 'provider ok'\n"
        "result = {'conversation_id':'c1','status':'SUCCESS','response':response,'usage':{'input_tokens':12,'output_tokens':3,'total_tokens':15,'cache_read_tokens':2}}\n"
        "print(json.dumps({'event':'result','result':result}))\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IEXEC)
    return executable


def test_profile_discovers_agy_models(fake_agy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGY_CLI_PATH", str(fake_agy))
    profile = get_provider_profile("antigravity")
    assert profile is not None
    assert profile.fetch_models() == ["gemini-3.8-flash-high", "gemini-3.8-flash-low"]


def test_client_returns_openai_shape_and_usage(fake_agy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    profile = get_provider_profile("antigravity")
    client = profile.create_client(command=str(fake_agy), base_url="agy://antigravity")
    completion = client.chat.completions.create(
        model="gemini-3.8-flash-high",
        messages=[{"role": "user", "content": "hello"}],
    )
    assert completion.choices[0].message.content == "provider ok"
    assert completion.usage.total_tokens == 15
    assert completion.usage.prompt_tokens_details.cached_tokens == 2


def test_client_bridges_hermes_tool_calls(fake_agy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    profile = get_provider_profile("agy")
    client = profile.create_client(command=str(fake_agy), base_url="agy://antigravity")
    completion = client.chat.completions.create(
        model="gemini-3.8-flash-high",
        messages=[{"role": "user", "content": "lookup the client"}],
        tools=[{"type": "function", "function": {"name": "lookup", "description": "Lookup", "parameters": {"type": "object"}}}],
    )
    assert completion.choices[0].finish_reason == "tool_calls"
    assert completion.choices[0].message.tool_calls[0].function.name == "lookup"


def test_provider_is_an_external_process_route(fake_agy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGY_CLI_PATH", str(fake_agy))
    from hermes_cli.auth import resolve_external_process_provider_credentials
    from hermes_cli.runtime_provider import resolve_runtime_provider

    credentials = resolve_external_process_provider_credentials("agy")
    assert credentials["command"] == str(fake_agy)
    assert credentials["base_url"] == "agy://antigravity"
    runtime = resolve_runtime_provider(requested="agy", target_model="gemini-3.8-flash-high")
    assert runtime["provider"] == "antigravity"
