from __future__ import annotations

from stock_analyzer.config import MAX_ALERT_BUDGET, load_settings


def test_chat_id_becomes_default_telegram_allowlist(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.delenv("ALLOWED_TELEGRAM_CHAT_IDS", raising=False)

    settings = load_settings()

    assert settings.telegram_chat_id == "123"
    assert settings.allowed_telegram_chat_ids == ["123"]


def test_explicit_telegram_allowlist_is_not_uppercased(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@myChannel")
    monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "@myChannel,-100123")

    settings = load_settings()

    assert settings.allowed_telegram_chat_ids == ["@myChannel", "-100123"]


def test_alert_budget_is_capped_at_250(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STOCK_ANALYZER_ALERT_BUDGET", "1000")

    settings = load_settings()

    assert settings.alert_budget == MAX_ALERT_BUDGET


def test_fmp_symbol_cap_defaults_to_five(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STOCK_ANALYZER_FMP_MAX_SYMBOLS_PER_RUN", raising=False)

    settings = load_settings()

    assert settings.fmp_max_symbols_per_run == 5
