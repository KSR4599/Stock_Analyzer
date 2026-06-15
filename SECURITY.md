# Security Policy

This project is a research assistant. It must not contain production secrets, broker credentials, or private portfolio exports in source control.

## Current Safety Boundaries

- No broker integration is implemented.
- No automatic trading is implemented.
- Dry-run mode is enabled by default.
- Live Telegram mode requires `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `ALLOWED_TELEGRAM_CHAT_IDS`.
- Telegram sends are refused when `TELEGRAM_CHAT_ID` is not in the allowlist.
- Secrets are loaded from environment variables or a local `.env` file.
- `.env`, SQLite databases, virtualenvs, caches, and generated artifacts are ignored by git.

## Do Not Commit

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `FMP_API_KEY`
- broker API keys
- OAuth tokens
- private portfolio exports
- SQLite runtime databases
- logs that may contain alert recipients or tokenized URLs

## Deployment Expectations

- Use environment-managed secrets or OCI Vault for deployed credentials.
- Run the app as a least-privilege OS user.
- Keep the service outbound-only unless a reviewed API surface is added.
- Allow Telegram commands only from an explicit chat/user allowlist.
- Keep automatic trade execution disabled until paper-trading, approval workflows, and audit controls are proven.

## Repository Guardrails

- Keep GitHub secret scanning and push protection enabled for the repository.
- Keep Dependabot enabled for Python and GitHub Actions updates.
- Review dependency updates before deploying them to OCI.
- Never paste Telegram, FMP, OpenAI, broker, or OCI secrets into issues, pull requests, logs, or screenshots.
- Treat generated reports and SQLite backups as private research data.

## Future Production Controls

- Store server secrets in OCI Vault or an equivalent managed secret store.
- Add structured audit logs for every alert, portfolio input, and future action request.
- Add an explicit human approval step before any broker integration can place an order.
- Add alert rate limits and duplicate suppression so a bad data feed cannot spam Telegram.
- Add portfolio-file validation before manual holdings imports are accepted.
- Add CI secret scanning and dependency vulnerability checks before every deploy.

## Reporting Issues

Open a private issue or contact the repository owner if you find a secret exposure or security-sensitive behavior.
