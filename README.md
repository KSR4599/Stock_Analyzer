# Stock Analyzer

Telegram-first stock analyzer for high-upside watchlist ideas. The MVP uses Python, yfinance, SQLite, and a clean provider interface so paid providers such as FMP can be added later without rewriting the scoring engine.

This project is for research and personal analysis. It does not place trades and does not provide financial advice.

See [Architecture](docs/ARCHITECTURE.md) for the full system design, topology, roadmap, data sources, and security approach.

## Why This Shape

- `YFinanceProvider` gives us a free MVP for broad S&P 500 plus hot-name scanning.
- `DataProvider` keeps the market-data boundary swappable for `FMPProvider` later.
- `CatalystProvider` enriches top-ranked names with news, earnings, analyst, and price-target signals when configured.
- SQLite keeps every scan auditable without needing a server database.
- The scoring engine is deterministic, so a future LLM/Hermes layer explains decisions instead of inventing them.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]" -c constraints.txt
cp .env.example .env
```

Set Telegram values in `.env` or export them in your shell:

```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export ALLOWED_TELEGRAM_CHAT_IDS="..."
```

For the MVP, `ALLOWED_TELEGRAM_CHAT_IDS` should usually match `TELEGRAM_CHAT_ID` exactly. The app automatically reads a local `.env` file when present, but `.env` must never be committed.

## Commands

Dry run a scan and print the Telegram message:

```bash
python -m stock_analyzer.app run-once --dry-run --max-symbols 40
```

Dry run a targeted basket:

```bash
python -m stock_analyzer.app run-once --dry-run --symbols ARM,MRVL,MU,SOUN,SMCI --top-n 5
```

Dry run without catalyst enrichment:

```bash
python -m stock_analyzer.app run-once --dry-run --no-catalysts
```

Run a live Telegram scan:

```bash
python -m stock_analyzer.app run-once --live
```

Send exactly one safe Telegram test message:

```bash
python -m stock_analyzer.app telegram-test --live
```

Retrieve your Telegram chat ID after you message the bot:

```bash
python -m stock_analyzer.app telegram-chat-id
```

Run every 3 hours:

```bash
python -m stock_analyzer.app schedule --live
```

Initialize only the database:

```bash
python -m stock_analyzer.app init-db
```

For OCI deployment with `systemd`, see [Deployment](docs/DEPLOYMENT.md).

## Scoring

The first score is a market-data "Moonshot Score" that favors rapid-upside setups:

- multi-horizon momentum across 1 week, 1 month, 3 months, and 6 months
- relative strength against `SPY`
- breakout proximity to 20-day, 55-day, and 1-year highs
- volume expansion, up/down volume, and OBV-style accumulation
- trend quality across EMA 10/21/50/150/200
- momentum acceleration
- enough liquidity for a small starter position
- controlled but meaningful volatility

It penalizes illiquidity, broken downtrends, severe drawdowns, extreme volatility, and recent one-day pump risk. When a score clears `STOCK_ANALYZER_ALERT_SCORE_THRESHOLD` and passes liquidity, strength, trend, and risk gates, the report marks it as a `$250 candidate`.

The configured alert budget is capped at `$250` in code for the MVP, even if a larger value is provided through environment variables or CLI flags.

By default, the app uses free SEC EDGAR filing enrichment for the top market-ranked names. SEC enrichment considers:

- fresh `8-K` / `6-K` event filings
- recent `10-Q`, `10-K`, `20-F`, and `40-F` financial filings
- ownership filings such as `SC 13D` / `SC 13G`
- offering/financing-style filings such as `S-1`, `S-3`, and `424B*` as risk signals

Set a descriptive SEC user-agent before scheduled deployment:

```bash
export SEC_USER_AGENT="stock-analyzer/0.1 personal research your-email@example.com"
```

When `FMP_API_KEY` is configured and `STOCK_ANALYZER_CATALYST_PROVIDER=fmp`, the app enriches the top market-ranked names with a bounded FMP catalyst score. The FMP catalyst layer considers:

- fresh stock news and press-like headlines
- AI/chip/data-center/quantum/space/defense theme keywords
- partnership, contract, launch, approval, upgrade, and guidance keywords
- negative events such as downgrades, dilution, lawsuits, probes, recalls, and guidance cuts
- near-term or recent earnings events
- analyst grade changes
- analyst price-target upside/downside when available

The catalyst layer can lift a strong `watch` name into a `candidate`, but it cannot turn a weak `skip` into an automatic `$250 candidate` by itself.

## Future Provider

Add FMP by implementing `stock_analyzer.providers.base.DataProvider`:

```python
class FMPProvider(DataProvider):
    name = "fmp"

    def get_history(self, symbols, period, interval):
        ...
```

Then wire it in `stock_analyzer.app.build_provider`.
