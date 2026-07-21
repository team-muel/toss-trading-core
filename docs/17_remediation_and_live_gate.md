# Remediation And Live Gate

## Current rule

The repository is read-only Foundation software. Paper, shadow, and live order
submission remain blocked until the latest complete snapshot run proves the
approved OpenAPI contract, account binding, reconciliation, and recovery path.

## Required evidence

- clean-checkout CI imports the runtime package and passes tests
- OpenAPI 1.2.4 SHA-256
  `7000d89ea3d783b0fa36d32e31750e85e139098306dbfce53a75fc4891019f1b`
  is explicitly approved, while ambiguous CLOSED listing remains disabled
- one complete run contains only 2xx broker evidence from one account
- v1 uses an order ID captured while OPEN, then validates that exact order
  detail after the manual app action
- amounts retain decimal text and are reported per currency, never summed
  across KRW and USD without an FX source and timestamp
- backup/restore and IP allowlist checks have passed in the real GCP runtime

## Completed prerequisites

- GCP static external IP registered in the Toss Open API allowlist on 2026-07-21

## Scope

The first strategy, once enabled, is US-listed USD broad ETF momentum only.
Relative-value, distribution/NAV/ROC, options, short, margin, and Korean/FX
multi-currency strategies remain research-only until their independent data
and risk gates exist.
