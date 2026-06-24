# Stock Analyzer

Telegram-first stock analyzer for high-upside watchlist ideas. The current build combines deterministic market scoring with auditable SEC, Finnhub, Marketaux, Alpha Vantage, and FRED signal interfaces while keeping paid data optional.

This project is for research and personal analysis. It does not place trades and does not provide financial advice.

See [Architecture](docs/ARCHITECTURE.md) for the full system design, topology, roadmap, data sources, and security approach.
See [Intelligence Upgrade Roadmap](docs/INTELLIGENCE_ROADMAP.md) for the
change-detection model and the staged FMP, Reddit, transcript, and ownership
integration plan.

## Why This Shape

- `YFinanceProvider` gives us a free MVP for broad S&P 500 plus hot-name scanning.
- `DataProvider` keeps the market-data boundary swappable for `FMPProvider` later.
- `CatalystProvider` enriches top-ranked names with bounded news, earnings, filing, fundamental, analyst-revision, and macro signals.
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

Dry run with FMP catalyst enrichment after setting `FMP_API_KEY`:

```bash
python -m stock_analyzer.app run-once --dry-run --catalyst-provider fmp --symbols ARM,MRVL,MU,SOUN,SMCI --top-n 5 --catalyst-top-n 5
```

Verify FMP access without sending Telegram messages:

```bash
python -m stock_analyzer.app fmp-test --symbol NVDA
```

Dry run with Finnhub catalyst enrichment after setting `FINNHUB_API_KEY`:

```bash
python -m stock_analyzer.app run-once --dry-run --catalyst-provider finnhub --symbols ARM,MRVL,MU,SOUN,SMCI --top-n 5 --catalyst-top-n 5
```

Verify Finnhub endpoint and plan coverage without sending Telegram messages:

```bash
python -m stock_analyzer.app finnhub-test --symbol ARM
```

Verify optional free-provider keys:

```bash
python -m stock_analyzer.app marketaux-test --symbol ARM
python -m stock_analyzer.app alpha-vantage-test --symbol ARM
python -m stock_analyzer.app fred-test
```

Run the multi-source stack in shadow mode:

```bash
python -m stock_analyzer.app run-once --dry-run \
  --catalyst-provider multi \
  --symbols ARM,MRVL,MU,SOUN,SMCI \
  --top-n 5 --catalyst-top-n 10
```

Inspect or review the seven-day shadow evaluation:

```bash
python -m stock_analyzer.app shadow-status --days 7
python -m stock_analyzer.app market-health --days 7
python -m stock_analyzer.app outcome-status
python -m stock_analyzer.app calibration-status
python -m stock_analyzer.app shadow-review \
  --run-id 123 --symbol ARM --decision approved --notes "Reviewed sources."
```

Run a live Telegram scan:

```bash
python -m stock_analyzer.app run-once --live
```

Completed production scans send a generated **Universe Alert PDF** with a
one-line Telegram caption. Shadow scans remain internal and never send an
actionable Telegram document.

Send exactly one safe Telegram test message:

```bash
python -m stock_analyzer.app telegram-test --live
```

## Personal Portfolio

Create a privacy-minimized preview from a supported Fidelity Positions PDF:

```bash
python -m stock_analyzer.app portfolio-import --pdf "/path/to/positions.pdf"
```

The parser reads the PDF in memory and persists only ticker, quantity, average
cost, statement date, and system metadata. It never stores the source path,
account identifiers, personal information, cash, account totals, gain/loss
columns, pending activity, tax lots, or stock-plan/RSU records.

Review the sanitized diff, then activate it explicitly:

```bash
python -m stock_analyzer.app portfolio-apply --import-id 1
python -m stock_analyzer.app portfolio-show
python -m stock_analyzer.app portfolio-status
python -m stock_analyzer.app portfolio-stability
```

Run one portfolio review:

```bash
python -m stock_analyzer.app portfolio-run --dry-run
```

Completed live reviews send a generated **Portfolio Alert PDF** with a
one-line Telegram caption. PDFs are created in memory, uploaded, and discarded;
they are not archived by the application.

Run the intraday price watcher:

```bash
python -m stock_analyzer.app portfolio-price-watch --dry-run --force
```

Scheduled live price-watch runs execute every 15 minutes during regular market
hours. They use the active WMT-free portfolio, sanity-check quote moves, and
send short Telegram text alerts when a position newly crosses 5%, 10%, or 15%
up/down versus the prior close.

Run the end-of-day portfolio PDF:

```bash
python -m stock_analyzer.app portfolio-eod-report --dry-run --force
```

The EOD report runs after market close and sends a graphical PDF with market
value, day gains, day losses, net dollar result, top gainers/losers, largest
dollar impacts, a full day table, source labels, and degraded-data warnings.

Portfolio actions are review labels only: `HOLD`, `WATCH`,
`BUY-MORE REVIEW`, `TRIM REVIEW`, and `EXIT REVIEW`. No order execution is
implemented. `WMT` is a hard privacy exclusion because those holdings are
RSUs. It is discarded during PDF parsing, rejected by persistence and policy
interfaces, removed from scanner universes and dashboards, and never
contributes to portfolio totals or historical performance.

`portfolio-stability` ignores degraded runs and tightly clustered manual
reruns by default. It reports action streaks and transitions across genuinely
spaced healthy observations so confirmation/hysteresis is not calibrated from
duplicate snapshots.

## Local Decision Cockpit

Start the private read-only dashboard:

```bash
python -m stock_analyzer.app dashboard --port 8765
```

Then open `http://127.0.0.1:8765`. The service binds only to loopback, opens
SQLite in query-only mode, accepts only read requests, performs no telemetry,
and loads no third-party browser assets. It combines portfolio allocation and
history, production and shadow research, per-stock evidence, measured signal
outcomes, provider reliability, scheduler freshness, and Telegram status.

Telegram delivery is tracked separately from portfolio analysis. If Telegram
is temporarily unreachable, a completed assessment remains stored and the
dashboard reports the notification failure without making the scheduled
analysis itself fail.

## Private macOS Runtime

Install or update the owner-only runtime used by the LaunchAgents:

```bash
.venv/bin/python deploy/install_local_runtime.py
```

The installer is safe to rerun. It preserves the runtime `.env` and SQLite
database, creates a timestamped database backup before upgrades, installs the
application into a dedicated virtual environment, and writes owner-only
LaunchAgent files. Runtime state lives under:

```text
~/Library/Application Support/StockAnalyzer/
```

The Desktop checkout remains the development source. Scheduled production,
shadow, portfolio, and dashboard processes do not execute from the protected
Desktop path.

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

For running every 3 hours from this Mac before OCI, see [Local Scheduling](docs/LOCAL_SCHEDULING.md).

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
- XBRL revenue, margin, cash, debt, free-cash-flow, and share-count trends
- 8-K item codes for agreements, earnings, financing, delisting, equity sales, and leadership changes
- Form 4 open-market purchases and sales; grants and tax-withholding transactions are informational

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

FMP should be used as a top-ranked catalyst layer first, not as the broad S&P 500 market-data engine. The free Basic plan is useful for endpoint and sample-symbol testing but may return HTTP 402 for catalyst data on target symbols. Run `fmp-test` against a real target such as `ARM`, not only a sample symbol such as `NVDA`, before enabling it. The app caps FMP enrichment to `STOCK_ANALYZER_FMP_MAX_SYMBOLS_PER_RUN=5` by default. With four enrichment endpoints and eight scheduled runs per day, that is at most 160 calls per day, excluding manual tests.

FMP endpoint access varies by plan. If one optional endpoint is unavailable, the provider keeps any usable news, earnings, grade, or target data from the other endpoints and reports the unavailable source as a risk note.

When `FINNHUB_API_KEY` is configured and `STOCK_ANALYZER_CATALYST_PROVIDER=finnhub`, the app uses a calibrated Finnhub enrichment pass for:

- company news from the configured catalyst lookback window
- recent and upcoming earnings-calendar events
- recommendation-trend changes
- symbol-relevant, deduplicated news with recency decay

Finnhub scans make three calls per symbol: news, earnings calendar, and recommendations. The premium price-target endpoint is checked only by `finnhub-test`. Static bullish consensus is context only; only meaningful month-over-month recommendation changes affect the score.

The app caps Finnhub enrichment to five symbols. It scores no more than three independent news clusters and applies stronger company matching to ambiguous tickers such as `ARM`.

## Multi-Source Shadow Stack

Set any available free keys in the ignored `.env`:

```dotenv
MARKETAUX_API_TOKEN=
ALPHA_VANTAGE_API_KEY=
FRED_API_KEY=
```

`multi` combines:

- SEC for filings, XBRL fundamentals, insider transactions, and dilution
- Finnhub for fast company news, earnings dates, and recommendation changes
- Marketaux for entity match, sentiment, and similar-story grouping
- Alpha Vantage for 24-hour cached fundamentals and earnings-estimate revisions
- FRED plus SPY/QQQ/IWM/SOXX for a downside-only market-regime adjustment

Category caps are enforced centrally: news `-8/+6`, earnings `-6/+4`, filings/insiders `-8/+5`, fundamentals/analyst revisions `-4/+4`, macro `-5/0`, and total enrichment `-15/+10`. Duplicate events contribute once; corroborating sources increase confidence rather than score.

All providers except SEC and dormant FMP are hard-blocked from live Telegram delivery. `STOCK_ANALYZER_CATALYST_PROVIDER=sec` remains the production default until the shadow criteria in [Provider Bake-Off](docs/PROVIDER_BAKEOFF.md) pass.

Market-data downloads retry missing symbols in smaller batches and then
individually. Each run stores requested/received counts, coverage percentage,
and missing symbols. If coverage falls below
`STOCK_ANALYZER_MIN_MARKET_COVERAGE_PCT`, SPY is missing, or no symbols can be
ranked, the scan is marked degraded: catalyst calls are skipped, candidate
allocations are suppressed, and the run does not count toward shadow
activation.

Every normal scan also matures available 1/3/5/10/21-trading-day outcomes for
earlier scores using the already-downloaded adjusted price history. Stored
outcomes include absolute return, SPY-relative return, maximum favorable move,
and maximum adverse move. `outcome-status` summarizes raw scan observations by
horizon and action. Repeated scans of the same symbol are correlated, so these
summaries are calibration evidence rather than an independent-sample backtest.
`calibration-status` collapses contiguous same-action observations into
36-hour signal episodes and reports results by action and score band. This
episode-adjusted view is the required input for later threshold tuning.

New scan records also carry a lightweight episode-adjusted calibration context
in their stored metrics. The dashboard and Universe PDFs label whether
comparable 3-day outcome evidence is `unmeasured`, `thin`, `early`, or
`measured`, plus episode count, win rate, and median return when available.
This does not change the deterministic score; it prevents raw scores from
looking more proven than the measured history supports.

Shadow-source promotion is explicit. The seven-day gate requires at least 20
healthy shadow scans, seven elapsed days, 95%+ non-cache provider success, a
bounded positive contribution p95, zero duplicate scored news stories, and
manual review of every candidate-state transition. Dashboard health exposes
which criteria pass or block promotion.

Stock detail pages include an evidence-only dossier assembled from stored
provider events, score contributions, sourced fundamental snapshots, and
measured outcomes. Missing fundamentals such as market cap or valuation ratios
are shown as unavailable until a provider supplies sourced, timestamped data.
