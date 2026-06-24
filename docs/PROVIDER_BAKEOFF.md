# Catalyst Provider Bake-Off

Last updated: 2026-06-18 UTC.

## Decision Rule

SEC remains the scheduled production default. Finnhub, Marketaux, Alpha Vantage, FRED, and `multi` are shadow-only and cannot send live Telegram messages.

All comparisons use:

```bash
python -m stock_analyzer.app run-once --dry-run \
  --symbols ARM,MRVL,MU,SOUN,SMCI \
  --top-n 5 \
  --catalyst-top-n 5 \
  --catalyst-provider PROVIDER
```

## Finnhub Official Plan and Endpoint Check

Checked against the official Finnhub pricing and API documentation on 2026-06-18.

Free-plan headline:

- price: $0/month.
- rate limit: 60 API calls/minute.
- license: personal use under Finnhub's terms.
- US company-news coverage: one year plus real-time updates.
- US earnings-calendar coverage: one month plus real-time updates.
- recommendation trends: available on the free plan.
- price targets: marked `Premium required`.

Relevant endpoints:

| Signal | Endpoint | Free-plan status | Provider use |
| --- | --- | --- | --- |
| Company news | `/company-news` | One year plus new updates; marked high usage | Keyword/theme scoring and event headlines |
| Earnings calendar | `/calendar/earnings` | One month plus new updates | Recent surprise and upcoming gap-risk scoring |
| Recommendation trends | `/stock/recommendation` | Available | Latest bullish/bearish analyst mix |
| Price target | `/stock/price-target` | Premium required | Coverage event when available; failure retained as a risk |
| Company profile | `/stock/profile2` | Available | Smoke-test authentication and symbol coverage only |

Official sources:

- https://finnhub.io/pricing
- https://finnhub.io/docs/api
- https://github.com/Finnhub-Stock-API/finnhub-python

## Call Budget

Finnhub enrichment now makes three calls per symbol:

1. company news.
2. earnings calendar.
3. recommendation trends.
Default maximum:

- 5 symbols per scan.
- 15 calls per scan.
- 8 scheduled scans per day.
- 120 calls per day, excluding manual tests.

`finnhub-test` still makes five calls because it also checks company profile and premium price-target access.

## Comparison Snapshot

Dry-run snapshot: 2026-06-17 21:53 PDT. Market scores were identical across the SEC and FMP runs.

| Symbol | Market | SEC delta | SEC final | FMP delta | FMP final | Finnhub delta | Finnhub final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ARM | 90.1 | 0.0 | 90.1 | 0.0 | 90.1 | +18.0 | 100.0 |
| MU | 77.9 | +2.0 | 79.9 | 0.0 | 77.9 | +22.5 | 100.0 |
| MRVL | 71.0 | -4.0 | 67.0 | 0.0 | 71.0 | +19.5 | 90.5 |
| SOUN | 0.0 | -8.0 | 0.0 | 0.0 | 0.0 | +11.0 | 11.0 |
| SMCI | 0.0 | -18.0 | 0.0 | 0.0 | 0.0 | +11.5 | 11.5 |

### SEC

- Coverage: all five symbols returned recent filing events.
- Freshness: filings through 2026-06-17 were observed for MRVL and SOUN.
- Calls: SEC uses CIK mapping plus one submissions request per enriched symbol.
- Score changes: four of five symbols changed; MU crossed the candidate threshold.
- Failures: none observed in this snapshot.

### FMP

- Coverage: profile lookup worked for ARM; all four catalyst endpoints failed.
- Freshness: no usable catalyst data.
- Calls: 20 enrichment calls for five symbols; the separate ARM smoke test used five calls.
- Score changes: none.
- Failures for ARM: stock news, earnings, analyst grades, and price-target summary each returned HTTP 402 plan-access errors.

### Finnhub

- Coverage: all five symbols returned company news and recommendation trends.
- Freshness: four symbols had news on 2026-06-18 UTC; SOUN's newest article was 2026-06-16.
- Earnings: MU returned an upcoming event dated 2026-06-24; the other four returned no event in the tested window.
- Calls: 20 for the five-symbol scan. The ARM/NVDA smoke tests used 10 calls and the detailed coverage pass used 20 more.
- Rate limits: no HTTP 429 response across 50 calls in the validation session.
- Score changes: all five symbols received positive deltas. ARM and MU reached the 100-point cap.
- Endpoint failures: price targets failed with authorization/plan-access errors for every symbol; the other endpoints remained usable.

Detailed live coverage:

| Symbol | News count | Newest news UTC | Earnings | Recommendation period | Ratings counted | Price target |
| --- | ---: | --- | --- | --- | ---: | --- |
| ARM | 23 | 2026-06-18 03:46 | None in window | 2026-06-01 | 44 | Plan blocked |
| MRVL | 53 | 2026-06-18 01:14 | None in window | 2026-06-01 | 49 | Plan blocked |
| MU | 194 | 2026-06-18 01:10 | 2026-06-24 | 2026-06-01 | 55 | Plan blocked |
| SOUN | 3 | 2026-06-16 14:27 | None in window | 2026-06-01 | 15 | Plan blocked |
| SMCI | 36 | 2026-06-18 02:11 | None in window | 2026-06-01 | 26 | Plan blocked |

The provider scores only the six newest articles even when the endpoint returns more.

## Finnhub Recalibration Result

The original scoring above was deliberately retained as bake-off evidence. After adding symbol relevance, special handling for ambiguous `ARM`, similar-headline suppression, three-cluster maximum, recency decay, financing-risk keywords, and recommendation-change scoring, the same live basket produced:

| Symbol | Market | Recalibrated Finnhub delta | Final |
| --- | ---: | ---: | ---: |
| ARM | 90.1 | 0.0 | 90.1 |
| MU | 77.9 | 0.0 | 77.9 |
| MRVL | 71.0 | +0.3 | 71.3 |
| SOUN | 0.0 | +0.1 | 0.1 |
| SMCI | 0.0 | -0.9 | 0.0 |

Static analyst consensus is now context-only. MU’s June 24 earnings date is a gap-risk warning rather than an automatic positive score. The premium price-target endpoint is no longer called during scans.

## Multi-Source Shadow Snapshot

The first SEC plus calibrated-Finnhub `multi` dry run produced:

| Symbol | Market | Multi delta | Final | Action |
| --- | ---: | ---: | ---: | --- |
| ARM | 90.1 | +2.5 | 92.6 | Candidate |
| MU | 77.9 | +1.5 | 79.4 | Candidate |
| MRVL | 71.0 | -3.1 | 67.9 | Skip |
| SOUN | 0.0 | -7.9 | 0.0 | Skip |
| SMCI | 0.0 | -7.4 | 0.0 | Skip |

The shadow database reported one scan, 72 audited calls, 100% success, positive contribution p95 of `+2.0`, and zero duplicate scored news events. This is an implementation check, not the required seven-day evaluation.

The first persistent default-database scan ran on 2026-06-18 at 00:17 PDT after
provider calls were linked to their originating scan. Its score deltas were ARM
`+2.5`, MU `+1.5`, MRVL `-3.3`, SOUN `-7.9`, and SMCI `-7.4`. The corrected
shadow report counts 45 remote calls with 100% success, excludes 27 cache hits
from the rate calculation, reports positive contribution p95 `+2.0`, and finds
zero duplicate scored news contributions. Standalone smoke tests and production
SEC calls are no longer included in shadow activation metrics.

## Pending Free Integrations

### Marketaux

- Implementation: complete.
- Key: configured and authenticated on 2026-06-18.
- Budget: five symbols and one request per symbol per three-hour scan, or 40 requests/day.
- Acceptance: at least 90% manually reviewed symbol relevance, no more than 20% duplicates, useful coverage for at least four target symbols, and no quota failures.
- Massive/Polygon remains conditional and will be tested only if Marketaux fails.
- Live result: the ARM smoke request succeeded with zero articles in its
  72-hour window. The five-symbol scan returned 11 articles across the basket
  with no request failures. Relevance still requires manual review over the
  seven-day window.

### Alpha Vantage

- Implementation: complete.
- Key: configured and authenticated on 2026-06-18.
- Endpoints: `OVERVIEW` and `EARNINGS_ESTIMATES`.
- Budget: maximum 20 remote calls/day with 24-hour cache and stale-cache fallback.
- Static targets and rating mix are context; estimate changes and revision balance may score.
- Live result: rapid requests triggered free-tier throttling after two
  successful calls. A 12.5-second minimum interval was added between remote
  requests. The paced retry completed all eight remaining calls successfully
  and used two cached payloads.

### FRED

- Implementation: complete.
- Key: configured and authenticated on 2026-06-18.
- Series: VIX, 2-year and 10-year Treasury yields, high-yield spread, and effective fed funds.
- Cache: 12 hours.
- Market context also includes SPY, QQQ, IWM, and SOXX trends.
- Macro contribution is restricted to `-5/0`.
- Live result: all five series succeeded. The smoke snapshot returned VIX
  `16.41`, 2-year Treasury `4.05`, 10-year Treasury `4.43`, high-yield spread
  `2.71`, and effective fed funds `3.63`.

## Configured-Provider Shadow Snapshot

The first paced five-provider scan on 2026-06-18 at 00:38 PDT produced:

| Symbol | Market | Multi delta | Final | Action |
| --- | ---: | ---: | ---: | --- |
| ARM | 90.1 | +4.0 | 94.1 | Candidate |
| MU | 77.9 | +4.2 | 82.1 | Candidate |
| MRVL | 71.0 | -1.2 | 69.8 | Watch |
| SOUN | 0.0 | -5.5 | 0.0 | Skip |
| SMCI | 0.0 | -4.8 | 0.0 | Skip |

Current persistent metrics are three valid scans, 73 remote calls, 89.04%
success, positive contribution p95 `+2.0`, zero duplicate scored-news
contributions, and zero unreviewed candidate transitions. The cumulative
success rate includes eight Alpha Vantage throttling failures from the
pre-pacing scan; the paced follow-up had eight remote Alpha Vantage successes
and no failures. One zero-score scan caused by a Yahoo DNS failure is retained
for audit but excluded from activation scan and call metrics.

The separate eight-hour shadow LaunchAgent began recurring runs on 2026-06-18.
Its first automated scan had 100% market-data coverage and completed with exit
code zero. MRVL transitioned to candidate on market momentum while retaining a
`-1.0` catalyst adjustment; its review was recorded as `needs_followup` because
of SEC 144 sale risk, leadership change, earnings decline, extreme volatility,
and chase risk.

Market-data reliability now has its own gate. Missing Yahoo symbols are retried
in smaller batches and individually. Degraded scans skip catalyst calls,
suppress candidate allocations, remain auditable, and do not count toward the
twenty valid scans. The first full-universe production verification recovered
56 initial failures and finished at 514/514 symbols.

Forward outcome measurement is active without additional provider calls. The
first pass matured 160 historical scan observations at one- and
three-trading-day horizons from local runs beginning June 15. Date auditing
confirmed that calculations use only subsequent Yahoo trading bars through
June 18. These observations are preliminary and correlated across repeated
scans of the same symbols; they are not yet a completed backtest.

NewsAPI is rejected for production because its free tier is development-only and delayed. FMP remains dormant. OpenAI/Hermes explanations remain deferred until deterministic shadow results pass.

## Activation Gate

Run:

```bash
python -m stock_analyzer.app run-once --dry-run \
  --catalyst-provider multi \
  --symbols ARM,MRVL,MU,SOUN,SMCI \
  --top-n 5 --catalyst-top-n 10

python -m stock_analyzer.app shadow-status --days 7
```

Activation requires:

- at least seven elapsed days and twenty shadow scans.
- at least 95% provider-call success.
- positive contribution p95 no greater than `+8`.
- zero duplicate scored news contributions.
- manual review of every candidate-state change through `shadow-review`.
- passing tests, compilation, and secret scans.

Threshold changes require additional evidence beyond activation. Use
`calibration-status` to collapse correlated repeated scans into signal episodes
and compare the current candidate threshold with lower and higher score bands.
Do not tune from raw scan counts.

Do not change `STOCK_ANALYZER_CATALYST_PROVIDER=sec` until all gates pass and live activation is explicitly approved.
