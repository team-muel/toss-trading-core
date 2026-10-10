"""Read-only immutable lineage inspection; never issues gate or OOS acceptance."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .immutable import ImmutableDatasetStore


def audit_receipts(root: Path) -> dict:
    store = ImmutableDatasetStore(root)
    catalog = root / "catalog" / "manifests"
    findings = []
    counts = Counter()
    manifests = {}
    invalid = set()
    paths = sorted(catalog.glob("*.json"))
    if not catalog.is_dir() or not paths:
        findings.append({"reason": "MANIFEST_CATALOG_MISSING_OR_EMPTY"})
    for path in paths:
        try:
            manifest, _ = store.read(path.stem)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            findings.append({"manifest_id": path.stem, "reason": "MANIFEST_OR_LINEAGE_INVALID"})
            invalid.add(path.stem)
            continue
        manifests[path.stem] = manifest
        counts[manifest.layer] += 1
        # Gold may bind a composite identity. Source request contracts belong
        # to Bronze and are checked there, rather than guessed for derived data.
        if manifest.layer != "bronze":
            continue
        try:
            receipt = store.read_catalog("requests", manifest.request_hash)
        except (OSError, ValueError):
            findings.append({"manifest_id": path.stem, "reason": "REQUEST_RECEIPT_MISSING_OR_CORRUPT"})
            invalid.add(path.stem)
            continue
        if not isinstance(receipt, dict) or (
                receipt.get("source"), receipt.get("dataset")) != (
                manifest.source, manifest.dataset):
            findings.append({"manifest_id": path.stem, "reason": "REQUEST_RECEIPT_SOURCE_MISMATCH"})
            invalid.add(path.stem)
    # A valid content hash does not make a derived dataset replayable when
    # any ancestor's request contract is unavailable. Propagate without edits.
    blocked = set(invalid)
    while True:
        affected = {key for key, manifest in manifests.items()
                    if set(manifest.parent_manifest_ids) & blocked}
        added = affected - blocked
        if not added:
            break
        blocked.update(added)
    descendants = sorted(blocked - invalid)
    for key in descendants:
        findings.append({"manifest_id": key, "reason": "ANCESTOR_RECEIPT_OR_LINEAGE_INVALID"})
    return {"schema_version": "immutable-receipt-audit-v1",
            "scope": "LOCAL_STORAGE_INTEGRITY_ONLY", "acceptance_evaluated": False,
            "manifests_scanned": len(paths), "verified_manifests_by_layer": dict(sorted(counts.items())),
            "blocked_manifests_by_layer": dict(sorted(Counter(
                manifests[key].layer for key in blocked if key in manifests).items())),
            "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    report = audit_receipts(args.root)
    print(json.dumps(report, sort_keys=True))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
