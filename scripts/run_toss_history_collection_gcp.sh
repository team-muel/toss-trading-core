#!/usr/bin/env bash
# Retired under AMA-156/167: do not reactivate a parallel cloud application.
set -Eeuo pipefail
echo "Standalone research runtime retired. Use the canonical Asset Management OS." >&2
echo "For reviewed host/cloud retirement: python -m asset_management.cli.legacy_retirement --help" >&2
exit 78
