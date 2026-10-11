"""Read-only immutable lineage inspection; never issues gate or OOS acceptance."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path

from .immutable import ImmutableDatasetStore


def audit_receipts(root: Path, *, secrets: tuple[str, ...] | None = None) -> dict:
    """None means unclassified; an explicit empty tuple asserts no credentials."""
    classified = (isinstance(secrets, tuple)
                  and all(isinstance(secret, str) and secret.strip() for secret in secrets))
    store = ImmutableDatasetStore(root, secrets=secrets if classified else (),
                                  credentials_classified=classified)
    catalog = root / "catalog" / "manifests"
    findings = []
    if not classified:
        findings.append({"reason": "CREDENTIAL_CLASSIFICATION_REQUIRED"})
    manifests = {}
    invalid = set()
    paths = sorted(catalog.glob("*.json"))
    if not catalog.is_dir() or not paths:
        findings.append({"reason": "MANIFEST_CATALOG_MISSING_OR_EMPTY"})
    for path in paths:
        try:
            manifest = store.read_manifest(path.stem)
            manifests[path.stem] = manifest
            if not classified:
                invalid.add(path.stem)
                continue
            store.read(path.stem)
        except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            findings.append({"manifest_id": path.stem, "reason": "MANIFEST_OR_LINEAGE_INVALID"})
            invalid.add(path.stem)
            continue
        # Gold may bind a composite identity. Source request contracts belong
        # to Bronze and are checked there, rather than guessed for derived data.
        if manifest.layer != "bronze":
            continue
        try:
            receipt = store.read_catalog("requests", manifest.request_hash)
        except (OSError, ValueError, TypeError, RecursionError, OverflowError):
            findings.append({"manifest_id": path.stem, "reason": "REQUEST_RECEIPT_MISSING_OR_CORRUPT"})
            invalid.add(path.stem)
            continue
        if not isinstance(receipt, dict) or not (
                all(isinstance(receipt.get(key), str) and receipt[key].strip()
                    for key in ("source", "dataset"))
                and isinstance(receipt.get("endpoint"), str)
                and receipt["endpoint"].startswith("/")
                and isinstance(receipt.get("http_method"), str)
                and receipt["http_method"] in {"GET", "POST"}
                and isinstance(receipt.get("request"), dict)):
            findings.append({"manifest_id": path.stem, "reason": "REQUEST_RECEIPT_CONTRACT_INCOMPLETE"})
            invalid.add(path.stem)
            continue
        if (receipt.get("source"), receipt.get("dataset")) != (
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
            "manifests_scanned": len(paths), "verified_manifests_by_layer": dict(sorted(Counter(
                manifest.layer for key, manifest in manifests.items() if key not in blocked).items())),
            "blocked_manifests_by_layer": dict(sorted(Counter(
                manifests[key].layer for key in blocked if key in manifests).items())),
            "blocked_manifests_with_unverified_layer": len(blocked - set(manifests)),
            "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    classification = parser.add_mutually_exclusive_group()
    classification.add_argument("--secret-env", action="append", metavar="NAME",
                                help="Environment variable for each classified credential; repeat as needed")
    classification.add_argument("--no-credentials", action="store_true",
                                help="Assert the audited sources use no credentials")
    args = parser.parse_args()
    secrets = None
    if args.no_credentials:
        secrets = ()
    elif args.secret_env:
        values = tuple(os.environ.get(name, "") for name in args.secret_env)
        if all(value.strip() for value in values):
            secrets = values
    report = audit_receipts(args.root, secrets=secrets)
    print(json.dumps(report, sort_keys=True))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
