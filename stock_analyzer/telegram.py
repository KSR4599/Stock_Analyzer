from __future__ import annotations

import textwrap

import requests


TELEGRAM_LIMIT = 4096


class TelegramConfigError(RuntimeError):
    """Raised when live Telegram sending is not safely configured."""


class TelegramSendError(RuntimeError):
    """Raised when Telegram rejects or cannot receive a message."""


class TelegramSender:
    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        dry_run: bool,
        timeout_seconds: float = 20.0,
        allowed_chat_ids: list[str] | None = None,
    ) -> None:
        self.bot_token = bot_token.strip() if bot_token else None
        self.chat_id = chat_id.strip() if chat_id else None
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds
        self.allowed_chat_ids = {
            allowed_chat_id.strip()
            for allowed_chat_id in allowed_chat_ids or []
            if allowed_chat_id.strip()
        }

    def send(self, message: str, message_kind: str = "message") -> None:
        if self.dry_run:
            print(f"[telegram dry-run:{message_kind}]")
            print(message)
            return

        self.validate_live_config()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        for chunk in _split_message(message):
            try:
                response = requests.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    },
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                raise TelegramSendError(f"Telegram send failed: {type(exc).__name__}") from None

            if not response.ok:
                raise TelegramSendError(_telegram_error_message(response))

    def validate_live_config(self) -> None:
        if self.dry_run:
            return

        missing = []
        if not self.bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if not self.allowed_chat_ids:
            missing.append("ALLOWED_TELEGRAM_CHAT_IDS")
        if missing:
            joined = ", ".join(missing)
            raise TelegramConfigError(f"Live Telegram mode requires: {joined}")

        if self.chat_id not in self.allowed_chat_ids:
            raise TelegramConfigError("TELEGRAM_CHAT_ID is not present in ALLOWED_TELEGRAM_CHAT_IDS")


def _telegram_error_message(response: requests.Response) -> str:
    description = ""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        raw_description = payload.get("description")
        if isinstance(raw_description, str):
            description = f": {raw_description[:200]}"
    return f"Telegram send failed with HTTP {response.status_code}{description}"


def _split_message(message: str) -> list[str]:
    if len(message) <= TELEGRAM_LIMIT:
        return [message]

    chunks: list[str] = []
    remaining = message
    while remaining:
        chunk = remaining[:TELEGRAM_LIMIT]
        split_at = chunk.rfind("\n")
        if split_at < TELEGRAM_LIMIT // 2:
            split_at = TELEGRAM_LIMIT
        chunks.append(remaining[:split_at])
        remaining = textwrap.dedent(remaining[split_at:]).lstrip()
    return chunks
