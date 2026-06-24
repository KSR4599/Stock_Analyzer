# Intelligence Upgrade Roadmap

## Objective

Make the analyzer responsive to change, evidence breadth, and disagreement
instead of repeatedly presenting the same snapshot as a new insight.

## Implemented Foundation

Every production and shadow scan now compares itself only with the previous
healthy run from the same evidence class. The stored score record includes:

- score and rank movement
- new, lost, upgraded, downgraded, or steady signal state
- previous action
- newly observed reasons and risks
- risks no longer present

The dashboard adds:

- decision pulse counts
- largest score/rank movers
- production-versus-shadow disagreement
- evidence-coverage indicators
- fresh-insight callouts
- per-stock production score trajectories

These fields are factual comparisons, not model confidence.

Every new score is also annotated with a measured 3-day, episode-adjusted
calibration context by action and score band when matured outcomes exist. The
label is intentionally plain (`unmeasured`, `thin`, `early`, or `measured`) so
raw scores do not imply precision that repeated scans have not earned.

Portfolio swing and EOD price logic now rejects stale current-session bars and
split/adjustment-like jumps before alerting or contributing to EOD totals.

## Source Priority

### 1. FMP — recommended after plan upgrade

The configured key passes profile, earnings, analyst-grade, and
price-target-summary checks for NVDA, while stock news returns HTTP 402.
However, the actual ARM/MRVL/MU/SOUN/SMCI shadow basket returned plan-limited
responses for all 20 endpoint calls. FMP is therefore not enabled in the
recurring multi-source job.

Use and evaluate:

- earnings-call transcripts and transcript dates
- analyst revenue/EPS estimate revisions
- upgrades, downgrades, and consensus changes
- price-target dispersion, never as an independent recommendation
- press releases and ticker-linked news
- institutional ownership and 13F position changes
- sector and industry performance context

Activation requires an FMP plan that passes the target-basket endpoints,
followed by measured incremental value. The manual provider and endpoint audit
path remain available for future plan tests.

### 2. Direct SEC expansion — free and production-safe

Add direct 13F trend extraction, filing-language change detection, and
quarter-over-quarter XBRL surprise/deceleration features. This remains the most
auditable source, though it is slower and less timely than news/transcripts.

### 3. Reddit official API — shadow attention signal only

Use OAuth and official API terms. Measure:

- ticker mention velocity versus its own baseline
- unique-author and unique-subreddit breadth
- engagement acceleration
- link-domain quality
- spam concentration and repeated-text penalties

Do not treat crowd sentiment as fundamental evidence. Do not retain usernames
or full post bodies. Store only aggregated, timestamped metrics and source URLs
needed for audit. Reddit can move an attention/context component in shadow
mode, but cannot create a production candidate or portfolio action.

### 4. Earnings transcripts

Prefer licensed FMP transcripts or company investor-relations sources.
Extract deterministic changes such as:

- guidance raised, maintained, withdrawn, or lowered
- demand/backlog/capacity language change
- margin and cash-flow inflection
- customer concentration
- management uncertainty and risk-language expansion

An eventual LLM may summarize cited passages but cannot assign the score.

### 5. Seeking Alpha

Do not scrape pages, bypass authentication, or store paid article text. Until a
licensed API or explicitly permitted feed is available, use Seeking Alpha only
as a user-opened research link or manually supplied document. Its ratings must
not be copied into the deterministic score without a permitted data contract
and calibration.

## Additional High-Value Inputs

- official company press releases and investor-relations calendars
- FINRA short-interest and short-volume context
- options-implied volatility/skew from a licensed provider
- sector/industry relative strength and breadth
- insider buying clusters, separated from grants and tax withholding
- institutional ownership acceleration, with quarterly-lag warnings
- earnings-date proximity and post-earnings drift

## Activation Gate

Every new source starts in shadow mode and must demonstrate:

1. stable access and explicit source terms
2. symbol/entity precision
3. timestamp and URL provenance
4. no privacy-sensitive storage
5. at least 20 scans across seven days
6. measured incremental value versus the production baseline
7. bounded influence and documented failure behavior

The implemented gate now exposes these criteria directly in `shadow-status`
and dashboard health. A source is blocked until it has at least 20 healthy
shadow scans across seven elapsed days, 95%+ non-cache provider success, p95
positive contribution no higher than +8, zero duplicate scored news stories,
and manual review of every candidate-state transition. Passing API access alone
does not promote a provider.

## Evidence Dossiers

Stock detail dossiers are evidence-only. They may summarize:

- sourced fundamental snapshots such as SEC revenue growth, margins, cash,
  debt, net cash, free cash flow, share-count growth, and provider/as-of time;
- filing-backed catalysts from normalized events;
- scored contribution records with provider/source/category;
- measured forward outcomes by horizon.

Unavailable market cap, valuation ratios, analyst upside, or price targets are
shown as unavailable rather than inferred. Any later LLM layer may summarize
this dossier, but it cannot independently change scores or actions.

No social, analyst, transcript, or LLM signal may independently change a
portfolio action.
