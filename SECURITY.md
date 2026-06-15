# Security Policy

This project is a research assistant. It must not contain production secrets, broker credentials, or private portfolio exports in source control.

## Current Safety Boundaries

- No broker integration is implemented.
- No automatic trading is implemented.
- Dry-run mode is enabled by default.
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

## Reporting Issues

Open a private issue or contact the repository owner if you find a secret exposure or security-sensitive behavior.
