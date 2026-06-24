# Local Scheduling

Use this when you want the scanner to run from your Mac every 3 hours before moving it to OCI.

## Topology

```text
macOS LaunchAgent
  -> every 3 hours
  -> private runtime virtualenv Python
  -> stock_analyzer.app run-once --live
  -> owner-only runtime .env
  -> owner-only runtime SQLite and logs
```

No inbound ports are opened. The scanner only makes outbound HTTPS requests to market data, SEC EDGAR, configured optional data providers, and Telegram.

The installed live LaunchAgent remains on
`STOCK_ANALYZER_CATALYST_PROVIDER=sec`. A separate LaunchAgent runs the
five-symbol `multi` stack in dry-run shadow mode every eight hours.

Production universe and portfolio jobs send transient PDF documents to
Telegram. Shadow jobs do not send Telegram documents.

## Install Or Update

From the development checkout:

```bash
.venv/bin/python deploy/install_local_runtime.py
```

This creates or updates:

```text
~/Library/Application Support/StockAnalyzer/.venv/
~/Library/Application Support/StockAnalyzer/.env
~/Library/Application Support/StockAnalyzer/data/stock_analyzer.sqlite3
~/Library/Application Support/StockAnalyzer/logs/
~/Library/Application Support/StockAnalyzer/tmp/pdfs/
~/Library/Application Support/StockAnalyzer/backups/
```

The installer preserves `.env` and the runtime database on normal upgrades and
creates a timestamped database backup. Runtime files and directories are
owner-only.

## Installed Files

LaunchAgents are installed outside the repo:

```text
~/Library/LaunchAgents/com.stock-analyzer.local.plist
~/Library/LaunchAgents/com.stock-analyzer.shadow.plist
~/Library/LaunchAgents/com.stock-analyzer.portfolio.plist
~/Library/LaunchAgents/com.stock-analyzer.portfolio-price-watch.plist
~/Library/LaunchAgents/com.stock-analyzer.portfolio-eod.plist
~/Library/LaunchAgents/com.stock-analyzer.dashboard.plist
```

The committed template lives at:

```text
deploy/launchd/com.stock-analyzer.template.plist
deploy/launchd/com.stock-analyzer.shadow.template.plist
deploy/launchd/com.stock-analyzer.portfolio.template.plist
deploy/launchd/com.stock-analyzer.dashboard.template.plist
```

The installed plists contain no secrets. They point at the private runtime,
which reads its owner-only `.env`.

## Verify Schedule

```bash
launchctl print gui/$(id -u)/com.stock-analyzer.local
launchctl print gui/$(id -u)/com.stock-analyzer.shadow
launchctl print gui/$(id -u)/com.stock-analyzer.portfolio
launchctl print gui/$(id -u)/com.stock-analyzer.portfolio-price-watch
launchctl print gui/$(id -u)/com.stock-analyzer.portfolio-eod
launchctl print gui/$(id -u)/com.stock-analyzer.dashboard
```

## Run One Scheduled Job Now

```bash
launchctl kickstart -k gui/$(id -u)/com.stock-analyzer.local
launchctl kickstart -k gui/$(id -u)/com.stock-analyzer.shadow
launchctl kickstart -k gui/$(id -u)/com.stock-analyzer.portfolio
launchctl kickstart -k gui/$(id -u)/com.stock-analyzer.portfolio-price-watch
launchctl kickstart -k gui/$(id -u)/com.stock-analyzer.portfolio-eod
launchctl kickstart -k gui/$(id -u)/com.stock-analyzer.dashboard
```

## Stop The Schedule

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.local.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.shadow.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.portfolio.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.portfolio-price-watch.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.portfolio-eod.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.dashboard.plist
```

## Start The Schedule

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.local.plist
launchctl enable gui/$(id -u)/com.stock-analyzer.local
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.shadow.plist
launchctl enable gui/$(id -u)/com.stock-analyzer.shadow
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.portfolio.plist
launchctl enable gui/$(id -u)/com.stock-analyzer.portfolio
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.portfolio-price-watch.plist
launchctl enable gui/$(id -u)/com.stock-analyzer.portfolio-price-watch
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.portfolio-eod.plist
launchctl enable gui/$(id -u)/com.stock-analyzer.portfolio-eod
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.dashboard.plist
launchctl enable gui/$(id -u)/com.stock-analyzer.dashboard
```

## Logs

```bash
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-launchd.out.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-launchd.err.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-shadow.out.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-shadow.err.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-portfolio.out.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-portfolio.err.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-price-watch.out.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-price-watch.err.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-eod.out.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-eod.err.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-dashboard.out.log
tail -n 100 ~/Library/Application\ Support/StockAnalyzer/logs/stock-analyzer-dashboard.err.log
```

## Health Status

```bash
.venv/bin/python -m stock_analyzer.app market-health --days 7
.venv/bin/python -m stock_analyzer.app shadow-status --days 7
.venv/bin/python -m stock_analyzer.app outcome-status
.venv/bin/python -m stock_analyzer.app portfolio-status
.venv/bin/python -m stock_analyzer.app portfolio-stability
```

The stability report defaults to a two-hour minimum gap between eligible
healthy observations. This prevents manual reruns from being mistaken for
independent scheduled evidence.

The portfolio LaunchAgent is installed with umask `077`, runs every three
hours, and must not be loaded until a sanitized preview has been explicitly
applied.

The portfolio price-watch LaunchAgent runs every 15 minutes, but the command
exits quietly outside regular market hours. The EOD LaunchAgent runs at 1:15
PM local time and the command internally skips weekends and exchange holidays.

The dashboard LaunchAgent is owner-only and continuously serves
`http://127.0.0.1:8765`, does not open a LAN or internet listener, and never
writes through its SQLite connection.

## Manual Safety Check

Before enabling recurring local scans, a live one-off scan should succeed:

```bash
.venv/bin/python -m stock_analyzer.app run-once --live --symbols ARM,MRVL,MU,SOUN,SMCI --top-n 5
```
