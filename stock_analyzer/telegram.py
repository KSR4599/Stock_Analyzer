from __future__ import annotations

import textwrap

import requests


TELEGRAM_LIMIT = 4096


class TelegramSender:
    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        dry_run: bool,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds

    def send(self, message: str) -> None:
        if self.dry_run or not self.bot_token or not self.chat_id:
            print(message)
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        for chunk in _split_message(message):
            response = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()


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
