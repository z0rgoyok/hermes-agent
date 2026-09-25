"""The Jam archive command starts one fixed service from its configured Telegram group."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli.commands import resolve_command
from hermes_cli.commands_platforms import slack_native_slashes, telegram_bot_commands
from plugins.platforms.telegram.adapter import TelegramAdapter


def _runner(*, groups=("-100123",), chats=("-100123",), topics=()):
    adapter = object.__new__(TelegramAdapter)
    adapter.config = PlatformConfig(enabled=True, token="test", extra={
        "group_allowed_chats": list(groups), "allowed_chats": list(chats),
        "allowed_topics": list(topics),
    })
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: adapter.config})
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = {}
    return runner


def _event(*, platform=Platform.TELEGRAM, chat_id="-100123", chat_type="group",
           thread_id=None, user_id="member", text="/jamarchive"):
    return MessageEvent(
        text=text,
        source=SessionSource(platform=platform, chat_id=chat_id, chat_type=chat_type,
                             thread_id=thread_id, user_id=user_id),
    )


@pytest.mark.asyncio
async def test_group_member_starts_fixed_service_even_while_agent_is_busy(monkeypatch):
    runner = _runner()
    process = AsyncMock()
    process.wait.return_value = 0
    start = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    assert resolve_command("jamarchive").busy_policy == "dispatch"
    assert "jamarchive" in {name for name, _ in telegram_bot_commands(include_plugins=False)}
    assert "jamarchive" not in {name for name, _, _ in slack_native_slashes()}
    assert "jamarchive" in runner._gateway_plain_command_handlers()
    result = await runner._handle_jamarchive_command(_event(user_id="any-member"))

    assert result.startswith("Архив запущен")
    start.assert_awaited_once_with(
        "sudo", "-n", "/usr/bin/systemctl", "start", "--no-block",
        "jam-hermes-archive.service",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("event_kwargs", [
    {"chat_type": "dm"}, {"chat_id": "-100999"}, {"platform": Platform.DISCORD},
    {"chat_type": "channel"}, {"thread_id": "8"},
])
async def test_other_destinations_and_topics_cannot_start_service(monkeypatch, event_kwargs):
    runner = _runner(topics=("9",))
    start = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    result = await runner._handle_jamarchive_command(_event(**event_kwargs))

    assert "Архив доступен только" in result
    start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("groups,chats,topics", [
    ((), ("-100123",), ()),
    (("-100123", "-100999"), ("-100123", "-100999"), ()),
    (("-100123",), (), ()),
    (("-100123",), ("-100123",), ("8", "9")),
])
async def test_ambiguous_or_incomplete_config_fails_closed(monkeypatch, groups, chats, topics):
    runner = _runner(groups=groups, chats=chats, topics=topics)
    start = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    result = await runner._handle_jamarchive_command(_event())

    assert "Архив доступен только" in result
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_accepted_service_start_gets_success_reply(monkeypatch):
    runner = _runner(topics=("9",))
    process = AsyncMock()
    process.wait.return_value = 1
    start = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    result = await runner._handle_jamarchive_command(_event(thread_id="9"))

    assert result == "Не удалось запустить архив."
    start.assert_awaited_once()

    start.side_effect = OSError("systemctl unavailable")
    assert await runner._handle_jamarchive_command(_event(thread_id="9")) == "Не удалось запустить архив."
