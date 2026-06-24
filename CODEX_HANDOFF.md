# Stock Analyzer Handoff

## Current State

The privacy-first Stock Decision Cockpit, portfolio monitor, production SEC
scanner, and multi-source shadow scanner are operational from a private macOS
runtime:

```text
~/Library/Application Support/StockAnalyzer
```

The protected Desktop checkout remains the development source. All four
LaunchAgents use the runtime virtual environment, runtime `.env`, runtime
SQLite database, and owner-only runtime logs.

## Live Verification — June 22, 2026

- Production universe run `75` completed with exit code `0`, 100% market
  coverage, and Telegram status `delivered / pdf`.
- Portfolio run `22` completed with exit code `0`, 100% market coverage, and
  Telegram status `delivered / pdf`.
- Shadow run `76` completed with exit code `0` and
  `not_applicable / none`; shadow runs do not send Telegram PDFs.
- Dashboard LaunchAgent is continuously running on `127.0.0.1:8765`.
- Production, portfolio, and shadow interval agents are loaded and correctly
  idle after successful runs.
- Dashboard health reports every monitored service as healthy, including both
  PDF delivery channels and scheduler state.
- Runtime portfolio storage contains 22 positions and zero WMT rows.
- Runtime database, `.env`, logs, and installed plists are owner-only.

## PDF Alerts

Every completed production universe scan sends a **Universe Alert PDF** with a
short caption. Every completed portfolio review sends a **Portfolio Alert PDF**
with a short caption. Shadow scans remain internal.

PDFs are generated in memory with ReportLab, structurally checked with pypdf,
sent through Telegram `sendDocument`, and discarded. Text reports remain
available for CLI/debugging and concise fallback errors.

The delivery model persists:

- analysis status
- notification status
- notification format
- sanitized failure detail
- completion timestamp

This keeps analysis success distinct from PDF generation or Telegram delivery
failure.

## Portfolio Price Alerts And EOD PDF

The development checkout now includes two tactical portfolio alert products:

- `portfolio-price-watch --live`: 15-minute regular-market-hours watcher for
  5%, 10%, and 15% up/down moves versus prior close. It sends short Telegram
  text alerts, deduped per symbol, direction, threshold, and trade date.
- `portfolio-eod-report --live`: after-close graphical PDF with market value,
  day gains, day losses, net dollar result, top gainers/losers, largest dollar
  impacts, complete day table, source labels, and degraded-data warnings.

The new tables are `portfolio_price_snapshots`, `portfolio_price_alerts`, and
`portfolio_eod_reports`. They inherit the WMT exclusion cleanup. Quote moves
are sanity-checked so split/adjustment-like jumps do not trigger alerts.

Two new LaunchAgents are templated and installed by the runtime updater:

- `com.stock-analyzer.portfolio-price-watch`
- `com.stock-analyzer.portfolio-eod`

## Dashboard Health Semantics

- **Operational issue detected**: failed/degraded analysis, failed
  notification, or nonzero scheduler exit.
- **Attention - data stale**: an expected run is overdue without a recorded
  failure.
- **All monitored systems healthy**: fresh successful analysis and delivery;
  idle interval LaunchAgents with exit code `0` are healthy.

## Privacy And Exclusions

- WMT is a global RSU exclusion.
- WMT is rejected during parsing, persistence, policy evaluation, provider
  requests, scanner universes, dashboards, PDFs, captions, outcomes, and
  portfolio totals.
- Raw portfolio PDFs, account identifiers, personal information, cash,
  statement totals, tax lots, grants, vesting records, source paths, secrets,
  and raw logs are not exposed by reports or dashboard APIs.
- Portfolio PDFs are parsed in memory; only the permitted normalized fields are
  stored.

## Runtime Update

From the development checkout:

```bash
.venv/bin/python deploy/install_local_runtime.py
```

The installer is safe to rerun. It preserves the runtime `.env` and database,
creates a timestamped rollback backup, installs dependencies/application code,
and refreshes all owner-only LaunchAgent plists.

## Verification Commands

```bash
pytest -q
python -m compileall stock_analyzer tests deploy
git diff --check
plutil -lint deploy/launchd/*.plist
launchctl print gui/$(id -u)/com.stock-analyzer.local
launchctl print gui/$(id -u)/com.stock-analyzer.shadow
launchctl print gui/$(id -u)/com.stock-analyzer.portfolio
launchctl print gui/$(id -u)/com.stock-analyzer.portfolio-price-watch
launchctl print gui/$(id -u)/com.stock-analyzer.portfolio-eod
launchctl print gui/$(id -u)/com.stock-analyzer.dashboard
```

## Remaining Priorities

1. Complete the multi-source shadow activation gate. It still requires the
   intended 20 scans across seven days and an explicit MRVL transition review.
2. Continue 5/10/21-day forward calibration as outcomes mature.
3. Review the large uncommitted implementation as one intentional change set,
   then publish it safely.
4. Later: sourced fundamentals/dossiers and evidence-only LLM summaries.
5. OCI and paper-trading guardrails remain optional future work. Broker
   automation remains out of scope.

## Intelligence And Dashboard Upgrade

The latest development change makes comparable-run movement first-class:

- score and rank deltas
- new/lost candidate transitions
- upgrades and downgrades
- newly observed reasons and risks
- resolved risks
- production-versus-shadow disagreement
- evidence-coverage indicators
- per-symbol production score trajectories

Universe text/PDF reports now include signal movement. The dashboard now has a
decision pulse, signal movers, source-agreement view, richer idea cards, fresh
insight callouts, and score trajectories.

FMP was tested for the multi-source shadow stack but was not left enabled. A
June 22 NVDA smoke test showed profile, earnings, analyst grades, and
price-target summary available, while stock news returned HTTP 402. The actual
ARM/MRVL/MU/SOUN/SMCI basket then produced 20/20 plan-limited calls. A plan
upgrade and a fresh target-basket bake-off are required. Reddit should be official-API,
aggregate-only, and shadow-only. Seeking Alpha must not be scraped; keep it
manual/link-only unless licensed access is available. See
`docs/INTELLIGENCE_ROADMAP.md`.

Live verification on June 22, 2026:

- shadow run `77` completed at 100% coverage with exit code `0`
- production run `78` completed at 100% coverage with exit code `0`
- run `78` delivered the upgraded Universe PDF
- movement metadata is present in stored score JSON
- the immediate rerun correctly reported a flat comparable signal rather than
  inventing a change
- the dashboard read model returns evidence coverage and production/shadow
  agreement

## Latest Development Verification

- Added intraday price alert state, EOD report state, two CLIs, EOD PDF
  generation, LaunchAgent templates, runtime installer support, dashboard
  provider/price/EOD health visibility, and tests.
- Added robustness pass:
  - current-session price freshness gate for portfolio swing/EOD math
  - split/adjustment-like jump rejection remains active before alerts
  - score-level 3-day calibration context by action and score band
  - dashboard and Universe PDF expose calibration sample/win/median context
  - provider rows expose role and activation state, including FMP plan limits
  - dashboard change feed handles brand-new candidates without a score delta
- Added sanitized Fidelity CSV portfolio imports through the same preview/apply
  path as PDFs. CSV imports ignore account metadata, cash, pending activity,
  WMT/RSU rows, and store only symbol, quantity, average cost, and
  classification.
- Dashboard price-watch health is market-hours aware, so ordinary overnight
  idleness is healthy when the last watch snapshot itself was usable.
- Calibration shown on scores/PDFs/dashboard is now episode-adjusted, not raw
  repeated observations, so repeated same-symbol scans do not overstate sample
  strength.
- Shadow provider promotion is a first-class gate in `shadow-status` and
  dashboard health. Passing API access alone is not enough; blockers include
  insufficient elapsed days/scans, provider reliability, contribution caps,
  duplicate scored news, plan limits, and unreviewed candidate transitions.
- Stock detail drawers now expose sourced-only evidence dossiers from stored
  fundamental snapshots, normalized events, score contributions, and measured
  outcomes. Missing market cap or valuation ratios are explicitly unavailable
  until a sourced provider supplies them.
- Focused gate/dossier/calibration regression: `22 passed`.
- Full test suite: `119 passed`.
- Runtime deployment on June 23, 2026 created backup
  `runtime-before-update-20260623-214750.sqlite3`.
- Fresh post-deploy production run `86`: SEC, 100% coverage,
  `delivered / pdf`.
- Fresh post-deploy shadow run `85`: multi-source, 100% coverage,
  `not_applicable / none`.
- Fresh post-deploy portfolio run `26`: 100% coverage, `delivered / pdf`.
- Dashboard health API is green: all schedulers have exit code `0`, dashboard is
  running, and WMT-free portfolio total is `$37,215.80`.
- EOD report `1` for 2026-06-23: 100% coverage, net `-$2,450.26`,
  `delivered / pdf`.
- Dashboard health read model reports `degraded False`; production, shadow,
  portfolio monitor, price watch, EOD, PDF delivery, and schedulers are
  healthy.

## June 24, 2026 Portfolio CSV Update

- Imported `/Users/srikar_reddy/Downloads/Portfolio_Positions_Jun-24-2026.csv`
  via sanitized CSV preview `2`.
- Created direct pre-import runtime DB backup
  `pre-csv-import-20260624-011506.sqlite3`.
- Applied active import `2`: statement date `2026-06-24`, parser
  `fidelity-positions-csv-v1`, 25 permitted positions, zero WMT rows.
- New symbols added by the CSV snapshot: `AAOI`, `CRWV`, `LRCX`.
- Fresh portfolio run `27`: import `2`, 100% coverage, `delivered / pdf`,
  WMT-free total `$41,053.93`.
- Final runtime deployment backup:
  `runtime-before-update-20260624-011837.sqlite3`.
- Live dashboard health reports `degraded False`; price watch is healthy as
  idle outside market hours, and all scheduler/PDF delivery services are
  healthy.

## June 24, 2026 Robustness And Publish Pass

- Runtime deployment backup:
  `runtime-before-update-20260624-013120.sqlite3`.
- Fresh production run `89`: completed, 100% coverage, `delivered / pdf`,
  top symbol `WDC`.
- Fresh shadow run `88`: completed, internal `not_applicable / none`.
- Runtime DB now has `fundamental_snapshots`; live stock detail for `WDC`
  exposes one sourced fundamental snapshot and marks market cap / valuation
  ratio unavailable.
- Live health reports `degraded False`.
- Shadow gate is correctly `not_ready` with current blockers: `6.05 / 7`
  elapsed days, `16 / 20` scans, `86.92% / 95%` provider success, one
  unreviewed candidate transition, and FMP plan-limited calls.
- Safe publish audit found no runtime DB/log/env files in the candidate set.
  Secret scan hits were placeholders/docs/tests only.
