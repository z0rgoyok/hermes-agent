"""Same-conversation repair and provider fallback keep durable document identity."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gateway.invoice_store import InvoiceStore
from gateway import invoice_worker as worker


def test_worker_and_grok_share_one_recognition_error_type():
    from gateway.invoice_schema import RecognitionError
    from gateway.invoice_grok import RecognitionError as GrokRecognitionError
    assert worker.RecognitionError is RecognitionError is GrokRecognitionError


def setup_job(tmp_path):
    store = InvoiceStore(tmp_path)
    identifier = store.ingest(b"image", ".jpg", {"chat_id": "1"}, {"message_id": "2"}, batch="batch")
    job = store.claim()
    card = dict(document_type="invoice", client="Азиза", date="2026-09-20", total="1100",
                uncertainties=[], visual_evidence="Рукопись")
    valid = json.dumps({"documents": [{"document_id": identifier, "card": card}]})
    return store, job, valid


@pytest.mark.parametrize("success", [True, False])
def test_gemini_repairs_same_conversation_then_falls_back(tmp_path, monkeypatch, success):
    store, job, valid = setup_job(tmp_path)
    commands = []
    def popen(command, **kwargs):
        commands.append(command)
        output = valid if success and len(commands) == 4 else '{"documents":[]}'
        return SimpleNamespace(returncode=0, communicate=lambda **_: (json.dumps(
            {"conversation_id": "isolated-session", "response": output}), ""))
    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    from gateway import invoice_grok
    fallback = Mock(return_value=worker.parse_batch_response(valid, [job]))
    monkeypatch.setattr(invoice_grok, "recognize_grok_batch", fallback)
    cards = worker.recognize_batch(store, [job])
    assert cards[job["id"]].client == "Азиза"
    assert len(commands) == 4
    assert "--conversation" not in commands[0]
    for command in commands[1:]:
        assert command[command.index("--conversation") + 1] == "isolated-session"
        assert job["id"] in command[command.index("--print") + 1]
    assert fallback.call_count == int(not success)


@pytest.mark.parametrize("success", [True, False])
def test_grok_repairs_with_history_and_retry_preserves_done(tmp_path, monkeypatch, success):
    from gateway.invoice_grok import recognize_grok_batch
    from hermes_cli import auth_xai
    import openai
    store, job, valid = setup_job(tmp_path)
    monkeypatch.setattr(auth_xai, "resolve_xai_oauth_runtime_credentials",
                        lambda: {"api_key": "test", "base_url": "https://example.invalid/v1"})
    calls = []
    def create(**kwargs):
        calls.append(json.loads(json.dumps(kwargs)))
        return SimpleNamespace(output_text=valid if success and len(calls) == 4 else '{"documents":[]}')
    client = Mock()
    client.responses.create = create
    factory = Mock()
    factory.__enter__ = Mock(return_value=client)
    factory.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(openai, "OpenAI", lambda **_: factory)
    if success:
        card = recognize_grok_batch(store, [job])[job["id"]]
        store.finish(job["id"], card.model_dump(mode="json"), [])
    else:
        with pytest.raises(worker.RecognitionError):
            recognize_grok_batch(store, [job])
        store.fail(job["id"], "invalid format")
    assert [len(call["input"]) for call in calls] == [1, 3, 5, 7]
    assert all(call["store"] is False and "tools" not in call for call in calls)
    assert all(call["input"][0] == calls[0]["input"][0] for call in calls)
    assert store.retry_errors("batch") == int(not success)
    assert len(store.card(job["id"])["revisions"]) == int(success)
    assert len(store.card(job["id"])["recognition_attempts"]) == 4


def test_ambiguous_gemini_card_gets_independent_grok_result(tmp_path, monkeypatch):
    store, job, _ = setup_job(tmp_path)
    gemini = worker.InvoiceExtractionV1.model_validate(dict(
        document_type="invoice", client="Алиса", date="2026-09-20", total="1100",
        uncertainties=[{"field": "client", "original": "Алиса", "alternatives": ["Азиза"],
                        "explanation": "неоднозначная рукописная буква"}], visual_evidence="рукопись"))
    grok = worker.InvoiceExtractionV1.model_validate(dict(
        document_type="invoice", client="Азиза", date="2026-09-20", total="1100",
        uncertainties=[], visual_evidence="видны буквы А-з-и-з-а"))
    monkeypatch.setattr(worker, "recognize_gemini_batch", Mock(return_value={job["id"]: gemini}))
    from gateway import invoice_grok
    second = Mock(return_value={job["id"]: grok})
    monkeypatch.setattr(invoice_grok, "recognize_grok_batch", second)
    cards = worker.recognize_batch(store, [job])
    assert cards[job["id"]].client == "Алиса"
    second.assert_called_once_with(store, [job])


def test_failed_second_opinion_keeps_gemini_card_available(tmp_path, monkeypatch):
    store, job, _ = setup_job(tmp_path)
    ambiguous = worker.InvoiceExtractionV1.model_validate(dict(
        document_type="invoice", client=None, date="2026-09-20", total="1100",
        uncertainties=[{"field": "client", "original": "Алиса", "alternatives": ["Азиза"],
                        "explanation": "неоднозначная рукописная буква"}], visual_evidence="рукопись"))
    monkeypatch.setattr(worker, "recognize_gemini_batch", Mock(return_value={job["id"]: ambiguous}))
    from gateway import invoice_grok
    monkeypatch.setattr(invoice_grok, "recognize_grok_batch",
                        Mock(side_effect=worker.RecognitionError("Grok authorization required", auth=True)))
    cards = worker.recognize_batch(store, [job])
    assert cards[job["id"]].client is None
    assert store.control("paused") == "Grok authorization required"
