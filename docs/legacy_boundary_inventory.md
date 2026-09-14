# AMA-156 Phase 2 legacy boundary inventory

This inventory makes the remaining `toss_trading` surface explicit without
pretending it is production authority.  It is enforced by
`python scripts/check_legacy_boundary.py` in CI.

| Class | Retained surface | Authority rule |
| --- | --- | --- |
| Canonical read-only Toss compatibility adapter | `broker/**`, `contracts/**`, and the Toss client rate limiter | The only canonical import site is `asset_management.broker.toss_read`, and it may import only the reviewed client and response-contract modules. Dynamic imports are prohibited in canonical `asset_management`. The adapter may read and validate raw Toss responses; it cannot promote buying power to cash or NAV, and grants no write authority. |
| Legacy research/runtime | `research/**`, `cli/**`, research-runtime support, packaged `toss-research-*` commands, and the listed research systemd units | Retained temporarily for research reconstruction and historical compatibility. They are not a canonical decision, account, risk, paper, or execution runtime. AMA-168/169 own deployment retirement evidence. |
| Historical reader/CLI | `account/**`, `data/**`, package resource resolution, and the package marker | Readable only while migration/replay consumers are enumerated. They must not become a new executable operational route. |

The machine-readable source, console-entry-point, systemd, and CLI inventory is
[`config/legacy_boundary_inventory.json`](../config/legacy_boundary_inventory.json).
An inventory drift is a CI failure; it is not approval to add a legacy path.
The inventory covers every project entry point targeting `toss_trading`, every
legacy CLI module, and every systemd service or timer, rather than relying on a
name prefix.

This PR does not delete research commands, historical readers, deployed units,
or external resources.  It does not activate canonical production evidence,
Gate D2, M5, or live trading.
