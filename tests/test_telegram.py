from __future__ import annotations

import pytest

from stock_analyzer.telegram import TelegramConfigError, TelegramSendError, TelegramSender


class _FakeResponse:
    def __init__(self, ok: bool = True, status_code: int = 200, description: str = "ok") -> None:
        self.ok = ok
        self.status_code = status_code
        self._description = description

    def json(self) -> dict[str, object]:
        return {"description": self._description}


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
