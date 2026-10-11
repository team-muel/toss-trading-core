# Toss REST current contract baseline review — 2026-10-10

## Scope and evidence

Official source: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
Version 1.2.24, raw SHA-256 `7e753c21dc4b938d8f9610ce3efd6542783018977cb030e39db5a006f5b59d40`.
The observation JSON records canonical hashes of all 41 operation objects and 101 schemas. All local schema references resolve. Every operation is classified; no endpoint is enabled by this review.

The historical full 1.2.17 document could not be recovered. This is a review of the current read-only baseline, not proof of exhaustive compatibility with that unavailable document. Previous approval metadata remains historical in review_notes and Git history.

## Reviewed boundaries

- OAuth client credentials, tokenUrl /oauth2/token, Bearer authorization. Token issuance alone has empty security; other operations use oauth2ClientCredentials. Malformed token/token_type/expiry is rejected before caching; refresh failure clears cached authority.
- Account context uses X-Tossinvest-Account with accountSeq. Canonical TossReadAdapter delegates to the reviewed client and requires immutable raw-response evidence. AccountSeq/accountNo remain redacted in stored evidence.
- Account response envelopes and seven account parsers are exercised by 19 official documented, redacted synthetic examples. Missing result envelopes are rejected. These fixtures are parser tests, not actual broker, research, or OOS evidence.
- OPEN/CLOSED order groups and bounded cursor pagination remain supported. The ten documented order-status codes match repository known statuses. Unknown broker states fail closed. Buying power remains a constraint, not an inferred opening cash ledger balance.
- Candle intervals 1m/1d and count 1..200, ranking count 1..100 and durations, indicator candle count 1..200, indicator investor-trading KOSPI/KOSDAQ intervals/count match existing client query boundaries. Currency values KRW/USD and calendar markets KR/US remain unchanged in client policy.
- X-RateLimit-Limit/Remaining and Retry-After normal values remain honored. Nonfinite/negative malformed values cannot contaminate either independent rate limiter. GET 429 retries remain bounded; writes are not retried or enabled.
- Five sector GET endpoints, conditional-order reads and all standard/conditional order writes are documented but disabled. Runtime mode, broker capabilities and live_trading_enabled are unchanged.

## Verification and limits

Previous implementation head bfdebf4 received an independent GitHub Codex review with no major findings. Canonical rate-header follow-up head 1375255 passed 1489 tests on both Python 3.11 and 3.12; 24 focused canonical/boundary tests passed. The final approval/configuration head requires fresh independent review, full tests, live official verifier, CI matrix and isolated wheel import before merge.

No live broker request or order was made. Provider documentation review does not prove production provider readiness, data completeness, matured real-data OOS, or any trading gate acceptance. Subsequent version/hash/operation drift remains an error in the unchanged verifier.
