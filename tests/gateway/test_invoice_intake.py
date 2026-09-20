"""Invoice intake contracts: durable recovery, provenance, and text-only delivery."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.invoice_schema import InvoiceExtractionV1
from gateway.invoice_store import InvoiceStore
from gateway.invoice_worker import RecognitionError, parse_response, process_one
from plugins.platforms.telegram.invoice_intake import deliver_ready, intercept


@pytest.mark.asyncio
async def test_progress_edits_one_persisted_message(tmp_path):
    from plugins.platforms.telegram.invoice_intake import publish_progress
    store = InvoiceStore(tmp_path)
    store.ingest(b"a", ".jpg", source(), {"message_id": "1", "media_group_id": "album"})
    adapter = SimpleNamespace(
        config=SimpleNamespace(extra={"invoice_intake_chats": ["-100123"]}),
        send=AsyncMock(return_value=SimpleNamespace(success=True, message_id="99")),
        edit_message=AsyncMock(return_value=SimpleNamespace(success=True, message_id="99")),
    )
    await publish_progress(adapter, store)
    await publish_progress(adapter, InvoiceStore(tmp_path))
    assert adapter.send.await_count == 1
    assert adapter.edit_message.await_count == 0
    store.ingest(b"b", ".jpg", source(), {"message_id": "2", "media_group_id": "album"})
    process_one(store, lambda *_: extraction())
    await publish_progress(adapter, store)
    assert "1 из 2" in adapter.edit_message.call_args.args[2]
    process_one(store, lambda *_: extraction())
    await publish_progress(adapter, store)
    assert "2 из 2" in adapter.edit_message.call_args.args[2]
    assert adapter.send.await_count == 1


def extraction(**changes):
    return InvoiceExtractionV1.model_validate(dict(
        schema_version=1, document_type="invoice", client="Фабрика Азиза", date="2026-09-20",
        total="1100.00", uncertainties=[], visual_evidence="Рукописная строка получателя", **changes))


def source():
    return {"platform": "telegram", "chat_id": "-100123", "chat_type": "group", "user_id": "7"}


def test_durable_queue_dedup_revisions_and_recovery(tmp_path):
    store = InvoiceStore(tmp_path)
    metadata = {"message_id": "10", "author_id": "7", "media_group_id": "a"}
    doc = store.ingest(b"photo", ".jpg", source(), metadata)
    assert store.ingest(b"photo", ".jpg", source(), metadata) == doc
    store.ingest(b"photo", ".jpg", source(), dict(metadata, message_id="11", author_id="8"))
    assert store.claim()["id"] == doc
    reopened = InvoiceStore(tmp_path)
    reopened.recover()
    assert process_one(reopened, lambda *_: extraction())
    assert len(reopened.card(doc)["sources"]) == 2
    assert reopened.album_cards("-100123:a")["received_images"] == 2
    reopened.review(doc, ["Фабрика Алиса", "Фабрика Азиза"])
    assert process_one(reopened, lambda *_: extraction())
    assert len(reopened.card(doc)["revisions"]) == 2
    assert reopened.card(doc)["revisions"][0]["card"]["client"] == "Фабрика Азиза"
    assert not process_one(reopened)


def test_failure_does_not_block_other_images_and_auth_pauses(tmp_path):
    store = InvoiceStore(tmp_path)
    one = store.ingest(b"one", ".jpg", source(), {"message_id": "1"})
    two = store.ingest(b"two", ".jpg", source(), {"message_id": "2"})
    def fail(*_):
        raise RecognitionError("bad response")
    assert process_one(store, fail)
    assert process_one(store, lambda *_: extraction())
    assert store.card(one)["status"] == "error"
    assert store.card(two)["status"] == "ready"
    store.review(one, ["Азиза"])
    def auth(*_):
        raise RecognitionError("authorization required", auth=True)
    process_one(store, auth)
    assert store.control("paused")
    assert store.claim() is None


def test_schema_only_business_fields_and_ambiguous_client():
    data = extraction().model_dump(mode="json")
    data["client"] = None
    data["uncertainties"] = [{"field": "client", "original": "Алиса", "alternatives": ["Азиза"], "explanation": "л/з"}]
    card = parse_response(json.dumps({"response": json.dumps(data)}))
    assert card.client is None and card.uncertainties[0].alternatives == ["Азиза"]
    with pytest.raises(ValueError):
        parse_response(json.dumps(dict(data, lines=[])))
    with pytest.raises(ValueError):
        parse_response("not JSON")


@pytest.mark.asyncio
async def test_intake_any_author_and_text_only_album_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.session import SessionSource
    from gateway.platforms.event import MessageEvent
    from gateway.config import Platform
    from datetime import datetime, timezone
    adapter = SimpleNamespace(
        config=SimpleNamespace(extra={"invoice_intake_chats": ["-100123"]}),
        _is_own_message=lambda _: False,
        _topic_gates_pass=lambda *a, **k: True,
        _effective_message_thread_id=lambda _: None,
        _build_message_event=lambda msg, *a, **k: MessageEvent(text="", source=SessionSource(
            platform=Platform.TELEGRAM, chat_id="-100123", chat_type="group", user_id=str(msg.from_user.id))),
        send=AsyncMock(),
        _is_user_authorized_from_message=lambda _: True,
    )
    attachment = SimpleNamespace(file_size=5, get_file=AsyncMock(return_value=SimpleNamespace(
        file_path="photo.jpg", download_as_bytearray=AsyncMock(return_value=b"photo"))))
    for author in (988858242, 336717651):
        msg = SimpleNamespace(chat_id=-100123, message_id=author, from_user=SimpleNamespace(id=author, is_bot=False),
                              document=None, photo=[attachment], media_group_id="album", caption=None,
                              date=datetime.now(timezone.utc))
        from plugins.platforms.telegram.adapter import TelegramAdapter
        await TelegramAdapter._handle_media_message(adapter, SimpleNamespace(message=msg, update_id=1), None)
    store = InvoiceStore(tmp_path)
    assert process_one(store, lambda *_: extraction())
    with store.db() as db:
        db.execute("UPDATE albums SET changed=0")
    delivered = []
    async def admit(event):
        delivered.append(event)
        event._gateway_accepted = True
    adapter.handle_message = admit
    await deliver_ready(adapter, store)
    await deliver_ready(adapter, store)
    assert len(delivered) == 1
    event = delivered[0]
    assert not event.media_urls and not event.media_types
    assert event.internal and not event.allow_gateway_control
    assert "Фабрика Азиза" in event.text
    assert "988858242" in event.text and "336717651" in event.text
    assert "image_url" not in event.text and "base64" not in event.text
