# OCI Deployment

This MVP can run on an OCI Always Free Ubuntu VM as an outbound-only scheduled job. No public inbound ports are required because Telegram delivery uses HTTPS outbound requests.

## Target Topology

- OCI Ubuntu VM, preferably ARM Ampere if available.
- System user: `stock-analyzer`.
- App directory: `/opt/stock-analyzer`.
- Secret environment file: `/etc/stock-analyzer/stock-analyzer.env`.
- SQLite database: `/opt/stock-analyzer/data/stock_analyzer.sqlite3`.
- Scheduler: `systemd` timer every 3 hours.
- Logs: `journalctl` plus optional `/var/log/stock-analyzer`.

## Server Setup

```bash
sudo adduser --system --group --home /opt/stock-analyzer stock-analyzer
sudo mkdir -p /opt/stock-analyzer /etc/stock-analyzer /var/log/stock-analyzer
sudo chown -R stock-analyzer:stock-analyzer /opt/stock-analyzer /var/log/stock-analyzer
sudo chmod 750 /opt/stock-analyzer /var/log/stock-analyzer
```

Clone or copy the repository into `/opt/stock-analyzer`, then install dependencies:

```bash
cd /opt/stock-analyzer
sudo -u stock-analyzer python3 -m venv .venv
sudo -u stock-analyzer .venv/bin/python -m pip install --upgrade pip
sudo -u stock-analyzer .venv/bin/python -m pip install -e ".[dev]" -c constraints.txt
```

## Secrets

Copy the template and edit it on the server:

```bash
sudo cp deploy/stock-analyzer.env.example /etc/stock-analyzer/stock-analyzer.env
sudo nano /etc/stock-analyzer/stock-analyzer.env
sudo chown root:stock-analyzer /etc/stock-analyzer/stock-analyzer.env
sudo chmod 640 /etc/stock-analyzer/stock-analyzer.env
```

Set these values before live mode:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ALLOWED_TELEGRAM_CHAT_IDS=...
SEC_USER_AGENT=stock-analyzer/0.1 personal research your-real-email@example.com
```

For the MVP, keep `ALLOWED_TELEGRAM_CHAT_IDS` equal to `TELEGRAM_CHAT_ID`. Unknown chat IDs are rejected before any live send.

## Smoke Tests

Run a dry scan first:

```bash
sudo -u stock-analyzer /opt/stock-analyzer/.venv/bin/python -m stock_analyzer.app run-once --dry-run --symbols ARM,MRVL,MU,SOUN,SMCI --top-n 5
```

Then send exactly one Telegram test message:

```bash
sudo -u stock-analyzer /opt/stock-analyzer/.venv/bin/python -m stock_analyzer.app telegram-test --live
```

## Systemd Timer

Install the service and timer:

```bash
sudo cp deploy/systemd/stock-analyzer.service /etc/systemd/system/stock-analyzer.service
sudo cp deploy/systemd/stock-analyzer.timer /etc/systemd/system/stock-analyzer.timer
sudo systemctl daemon-reload
sudo systemctl enable --now stock-analyzer.timer
```

Check status and logs:

```bash
systemctl list-timers stock-analyzer.timer
systemctl status stock-analyzer.service
journalctl -u stock-analyzer.service -n 100 --no-pager
```

Trigger one manual scheduled run:

```bash
sudo systemctl start stock-analyzer.service
```

## SQLite Backup

SQLite is enough for this MVP because only one scheduled process writes locally every 3 hours. Back up with the SQLite online backup command:

```bash
sudo -u stock-analyzer sqlite3 /opt/stock-analyzer/data/stock_analyzer.sqlite3 ".backup '/opt/stock-analyzer/data/stock_analyzer-$(date +%Y%m%d-%H%M%S).sqlite3'"
```

Copy backups to OCI Object Storage or another private location if you want off-VM recovery. Do not commit database backups to GitHub.

## Network Posture

- No inbound ports are needed.
- Keep the VM firewall closed for this service.
- Outbound HTTPS is required for yfinance, SEC EDGAR, optional FMP, and Telegram.
- Do not add a webhook listener until it has authentication, allowlisting, rate limits, and audit logs.
