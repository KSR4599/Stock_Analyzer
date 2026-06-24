from __future__ import annotations

import pytest
import requests

from stock_analyzer.telegram import (
    TelegramConfigError,
    TelegramSendError,
    TelegramSender,
    fetch_recent_chat_ids,
)


class _FakeResponse:
    def __init__(self, ok: bool = True, status_code: int = 200, description: str = "ok") -> None:
        self.ok = ok
        self.status_code = status_code
        self._description = description
        self.payload: dict[str, object] = {"description": description}

    def json(self) -> dict[str, object]:
        return self.payload


def test_dry_run_does_not_require_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    sender = TelegramSender(bot_token=None, chat_id=None, dry_run=True)

    sender.send("hello", message_kind="telegram_test")

    captured = capsys.readouterr()
    assert "[telegram dry-run:telegram_test]" in captured.out
    assert "hello" in captured.out


def test_live_mode_requires_explicit_allowlist() -> None:
    sender = TelegramSender(bot_token="token", chat_id="123", dry_run=False)

    with pytest.raises(TelegramConfigError, match="ALLOWED_TELEGRAM_CHAT_IDS"):
        sender.validate_live_config()


def test_live_mode_rejects_unknown_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_post(*args: object, **kwargs: object) -> None:
        raise AssertionError("network should not be called")

    monkeypatch.setattr("stock_analyzer.telegram.requests.post", fail_post)
    sender = TelegramSender(
        bot_token="token",
        chat_id="999",
        dry_run=False,
        allowed_chat_ids=["123"],
    )

    with pytest.raises(TelegramConfigError, match="not present"):
        sender.send("hello")


def test_live_mode_sends_to_allowlisted_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_post(url: str, json: dict[str, object], timeout: float) -> _FakeResponse:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr("stock_analyzer.telegram.requests.post", fake_post)
    sender = TelegramSender(
        bot_token="token",
        chat_id="123",
        dry_run=False,
        timeout_seconds=7,
        allowed_chat_ids=["123"],
    )

    sender.send("hello", message_kind="telegram_test")

    assert len(calls) == 1
    assert calls[0]["json"] == {
        "chat_id": "123",
        "text": "hello",
        "disable_web_page_preview": True,
    }
    assert calls[0]["timeout"] == 7


def test_telegram_http_errors_do_not_include_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, json: dict[str, object], timeout: float) -> _FakeResponse:
        assert "secret-token" in url
        return _FakeResponse(ok=False, status_code=401, description="Unauthorized")

    monkeypatch.setattr("stock_analyzer.telegram.requests.post", fake_post)
    sender = TelegramSender(
        bot_token="secret-token",
        chat_id="123",
        dry_run=False,
        allowed_chat_ids=["123"],
    )

    with pytest.raises(TelegramSendError) as exc_info:
        sender.send("hello")

    assert "secret-token" not in str(exc_info.value)


def test_telegram_retries_transient_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    def fake_post(url: str, json: dict[str, object], timeout: float) -> _FakeResponse:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise requests.ConnectionError("temporary")
        return _FakeResponse()

    monkeypatch.setattr("stock_analyzer.telegram.requests.post", fake_post)
    monkeypatch.setattr("stock_analyzer.telegram.time.sleep", sleeps.append)
    sender = TelegramSender(
        bot_token="token",
        chat_id="123",
        dry_run=False,
        allowed_chat_ids=["123"],
        max_attempts=3,
        retry_delay_seconds=1,
    )

    sender.send("hello")

    assert attempts == 3
    assert sleeps == [1, 2]


def test_send_document_uses_multipart_and_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_post(
        url: str,
        data: dict[str, object],
        files: dict[str, tuple[str, bytes, str]],
        timeout: float,
    ) -> _FakeResponse:
        calls.append(
            {
                "url": url,
                "data": data,
                "files": files,
                "timeout": timeout,
            }
        )
        return _FakeResponse()

    monkeypatch.setattr("stock_analyzer.telegram.requests.post", fake_post)
    sender = TelegramSender(
        bot_token="token",
        chat_id="123",
        dry_run=False,
        allowed_chat_ids=["123"],
        timeout_seconds=9,
    )

    sender.send_document(
        b"%PDF-1.4 test",
        "portfolio-alert.pdf",
        "Portfolio Alert summary",
        "portfolio_pdf",
    )

    assert calls[0]["url"].endswith("/sendDocument")
    assert calls[0]["data"] == {
        "chat_id": "123",
        "caption": "Portfolio Alert summary",
    }
    assert calls[0]["files"] == {
        "document": (
            "portfolio-alert.pdf",
            b"%PDF-1.4 test",
            "application/pdf",
        )
    }
    assert calls[0]["timeout"] == 9


def test_send_document_dry_run_does_not_require_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender = TelegramSender(None, None, dry_run=True)

    sender.send_document(
        b"%PDF-1.4 test",
        "universe-alert.pdf",
        "Universe summary",
        "universe_pdf",
    )

    captured = capsys.readouterr().out
    assert "[telegram dry-run:universe_pdf]" in captured
    assert "universe-alert.pdf" in captured
    assert "Universe summary" in captured


def test_fetch_recent_chat_ids_requires_token() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_BOT_TOKEN"):
        fetch_recent_chat_ids(None)


def test_fetch_recent_chat_ids_dedupes_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse()
    response.payload = {
        "ok": True,
        "result": [
            {
                "message": {
                    "chat": {
                        "id": 123,
                        "type": "private",
                        "first_name": "Srikar",
                    }
                }
            },
            {
                "edited_message": {
                    "chat": {
                        "id": 123,
                        "type": "private",
                        "first_name": "Srikar",
                    }
                }
            },
            {
                "channel_post": {
                    "chat": {
                        "id": -100456,
                        "type": "channel",
                        "title": "Stock Alerts",
                    }
                }
            },
        ],
    }
    calls: list[dict[str, object]] = []

    def fake_get(url: str, timeout: float) -> _FakeResponse:
        calls.append({"url": url, "timeout": timeout})
        return response

    monkeypatch.setattr("stock_analyzer.telegram.requests.get", fake_get)

    chats = fetch_recent_chat_ids("token", timeout_seconds=4)

    assert [chat.chat_id for chat in chats] == ["123", "-100456"]
    assert chats[0].display_name == "Srikar"
    assert chats[1].display_name == "Stock Alerts"
    assert calls[0]["timeout"] == 4
