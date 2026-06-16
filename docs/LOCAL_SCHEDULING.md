# Local Scheduling

Use this when you want the scanner to run from your Mac every 3 hours before moving it to OCI.

## Topology

```text
macOS LaunchAgent
  -> every 3 hours
  -> project virtualenv Python
  -> stock_analyzer.app run-once --live
  -> local .env for Telegram/SEC config
  -> SQLite under data/
  -> logs under logs/
```

No inbound ports are opened. The scanner only makes outbound HTTPS requests to market data, SEC EDGAR, and Telegram.

## Installed File

The local LaunchAgent is installed outside the repo:

```text
~/Library/LaunchAgents/com.stock-analyzer.local.plist
```

The committed template lives at:

```text
deploy/launchd/com.stock-analyzer.template.plist
```

The installed plist should not contain secrets. It points at this repo and relies on the ignored local `.env` file.

## Verify Schedule

```bash
launchctl print gui/$(id -u)/com.stock-analyzer.local
```

## Run One Scheduled Job Now

```bash
launchctl kickstart -k gui/$(id -u)/com.stock-analyzer.local
```

## Stop The Schedule

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.local.plist
```

## Start The Schedule

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stock-analyzer.local.plist
launchctl enable gui/$(id -u)/com.stock-analyzer.local
```

## Logs

```bash
tail -n 100 logs/stock-analyzer-launchd.out.log
tail -n 100 logs/stock-analyzer-launchd.err.log
```

## Manual Safety Check

Before enabling recurring local scans, a live one-off scan should succeed:

```bash
.venv/bin/python -m stock_analyzer.app run-once --live --symbols ARM,MRVL,MU,SOUN,SMCI --top-n 5
```
