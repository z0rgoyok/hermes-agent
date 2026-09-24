"""Tests for Telegram model picker thread fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


class TestTelegramModelPicker:
    @pytest.mark.asyncio
    async def test_picker_switch_uses_normalized_group_source(self):
        runner = object.__new__(GatewayRunner)
        adapter = _make_adapter()
        runner.adapters = {Platform.TELEGRAM: adapter}
        runner._perform_model_switch = AsyncMock(return_value=(object(), None))
        runner._commit_model_switch = AsyncMock(return_value="switched")

        raw_source = SessionSource(
            platform=Platform.TELEGRAM, chat_id="-100", chat_type="group", user_id="111",
        )
        shared_source = adapter._telegram_group_observe_shared_source(raw_source)
        event = MessageEvent(text="/model", source=raw_source)
        ctx = SimpleNamespace(
            source=shared_source, session_key="shared", current_provider="deepseek",
            current_base_url="https://api.deepseek.com/v1", current_model="deepseek-flash",
            user_provs={}, custom_provs={}, excluded_provs=[],
        )

        async def choose(_event, _source, _adapter, _session_key, _listing_kwargs, callback):
            assert await callback("-100", "deepseek-flash", "deepseek") == "switched"
            return True

        runner._send_model_picker = AsyncMock(side_effect=choose)
        assert await runner._model_listing_reply(event, ctx, None) is None
        assert runner._perform_model_switch.await_args.args[-1] is shared_source
        assert runner._commit_model_switch.await_args.kwargs["source"] is shared_source

    @pytest.mark.asyncio
    async def test_send_model_picker_escapes_dynamic_provider_label(self):
        adapter = _make_adapter()
        sent = {}

        async def mock_send_message(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(message_id=101)

        adapter._bot.send_message = AsyncMock(side_effect=mock_send_message)

        result = await adapter.send_model_picker(
            chat_id="12345",
            providers=[
                {"slug": "provider_one", "name": "Provider One", "total_models": 1, "is_current": True}
            ],
            current_model="model_1",
            current_provider="provider_one",
            session_key="s",
            on_model_selected=AsyncMock(),
            metadata={"thread_id": "99999"},
        )

        assert result.success is True
        assert "MARKDOWN_V2" in repr(sent["parse_mode"])
        assert "provider\\_one" in sent["text"]
        assert "`model_1`" in sent["text"]

    @pytest.mark.asyncio
    async def test_back_button_escapes_dynamic_provider_label(self):
        adapter = _make_adapter()
        adapter._model_picker_state["12345"] = {
            "providers": [{"slug": "provider_one", "name": "Provider One", "total_models": 1, "is_current": True}],
            "current_model": "model_1",
            "current_provider": "provider_one",
            "session_key": "s",
            "on_model_selected": AsyncMock(),
            "msg_id": 42,
        }

        query = AsyncMock()
        query.data = "mb"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await adapter._handle_model_picker_callback(query, "mb", "12345")

        edit_kwargs = query.edit_message_text.call_args[1]
        assert "MARKDOWN_V2" in repr(edit_kwargs["parse_mode"])
        assert "provider\\_one" in edit_kwargs["text"]
        assert "`model_1`" in edit_kwargs["text"]
