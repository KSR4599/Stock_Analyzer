from __future__ import annotations

from dataclasses import dataclass
import textwrap
import time

import requests


TELEGRAM_LIMIT = 4096


class TelegramConfigError(RuntimeError):
    """Raised when live Telegram sending is not safely configured."""


class TelegramSendError(RuntimeError):
    """Raised when Telegram rejects or cannot receive a message."""


@dataclass(frozen=True)
class TelegramChat:
    chat_id: str
    chat_type: str
    display_name: str


class TelegramSender:
    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        dry_run: bool,
        timeout_seconds: float = 20.0,
        allowed_chat_ids: list[str] | None = None,
        max_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.bot_token = bot_token.strip() if bot_token else None
        self.chat_id = chat_id.strip() if chat_id else None
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
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
            self._send_chunk(url, chunk)

    def send_document(
        self,
        document: bytes,
        filename: str,
        caption: str,
        document_kind: str = "document",
    ) -> None:
        if not document.startswith(b"%PDF-"):
            raise ValueError("Telegram document is not a PDF.")
        if self.dry_run:
            print(
                f"[telegram dry-run:{document_kind}] "
                f"{filename} ({len(document)} bytes)"
            )
            print(caption)
            return

        self.validate_live_config()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        last_error = "Telegram document send failed"
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.post(
                    url,
                    data={
                        "chat_id": self.chat_id,
                        "caption": caption[:1024],
                    },
                    files={
                        "document": (filename, document, "application/pdf"),
                    },
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                last_error = (
                    f"Telegram document send failed: {type(exc).__name__}"
                )
            else:
                if response.ok:
                    return
                last_error = _telegram_error_message(response)
                if response.status_code < 500 and response.status_code != 429:
                    raise TelegramSendError(last_error)

            if attempt < self.max_attempts:
                time.sleep(self.retry_delay_seconds * attempt)
        raise TelegramSendError(last_error)

    def _send_chunk(self, url: str, chunk: str) -> None:
        last_error = "Telegram send failed"
        for attempt in range(1, self.max_attempts + 1):
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
                last_error = f"Telegram send failed: {type(exc).__name__}"
            else:
                if response.ok:
                    return
                last_error = _telegram_error_message(response)
                if response.status_code < 500 and response.status_code != 429:
                    raise TelegramSendError(last_error)

            if attempt < self.max_attempts:
                time.sleep(self.retry_delay_seconds * attempt)
        raise TelegramSendError(last_error)

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


def fetch_recent_chat_ids(bot_token: str | None, timeout_seconds: float = 20.0) -> list[TelegramChat]:
    token = bot_token.strip() if bot_token else None
    if not token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN is required to retrieve Telegram chat IDs")

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = requests.get(url, timeout=timeout_seconds)
    except requests.RequestException as exc:
        raise TelegramSendError(f"Telegram getUpdates failed: {type(exc).__name__}") from None

    if not response.ok:
        raise TelegramSendError(_telegram_error_message(response))

    try:
        payload = response.json()
    except ValueError:
        raise TelegramSendError("Telegram getUpdates returned invalid JSON") from None

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise TelegramSendError("Telegram getUpdates returned an unsuccessful response")

    return _extract_chats(payload.get("result"))


def _extract_chats(updates: object) -> list[TelegramChat]:
    if not isinstance(updates, list):
        return []

    chats: list[TelegramChat] = []
    seen: set[str] = set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict):
            continue
        raw_chat_id = chat.get("id")
        if raw_chat_id is None:
            continue
        chat_id = str(raw_chat_id)
        if chat_id in seen:
            continue
        seen.add(chat_id)
        chats.append(
            TelegramChat(
                chat_id=chat_id,
                chat_type=str(chat.get("type") or "unknown"),
                display_name=_chat_display_name(chat),
            )
        )
    return chats


def _chat_display_name(chat: dict[str, object]) -> str:
    title = chat.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    username = chat.get("username")
    if isinstance(username, str) and username.strip():
        return f"@{username.strip().lstrip('@')}"

    parts = []
    for key in ("first_name", "last_name"):
        value = chat.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " ".join(parts) if parts else "unknown"


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
        split_at = chunk.rfind("\n\n")
        if split_at >= TELEGRAM_LIMIT // 2:
            split_at += 1
        else:
            split_at = chunk.rfind("\n")
        if split_at < TELEGRAM_LIMIT // 2:
            split_at = TELEGRAM_LIMIT
        chunks.append(remaining[:split_at])
        remaining = textwrap.dedent(remaining[split_at:]).lstrip()
    return chunks
