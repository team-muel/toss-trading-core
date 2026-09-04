from pathlib import Path
from hashlib import sha256
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = {
    "docs/architecture.md",
    "docs/investment_policy.md",
    "docs/risk_policy.md",
    "docs/data_policy.md",
    "docs/temporal_policy.md",
    "docs/execution_policy.md",
    "docs/promotion_policy.md",
    "docs/incident_runbook.md",
}
REQUIRED_ADRS = {
    "0001-source-of-truth.md",
    "0002-time-semantics.md",
    "0003-money-precision.md",
    "0004-storage-separation.md",
    "0005-fail-closed-policy.md",
    "0006-runtime-mode-transitions.md",
    "0007-live-activation-procedure.md",
}
REQUIRED_POLICIES = {"investment", "risk", "data", "temporal", "execution", "promotion", "tax"}


def validate() -> list[str]:
    errors: list[str] = []
    for relative in sorted(REQUIRED_DOCS):
        if not (ROOT / relative).is_file():
            errors.append(f"missing required document: {relative}")
    adr_dir = ROOT / "docs" / "adr"
    actual_adrs = {path.name for path in adr_dir.glob("*.md")} if adr_dir.is_dir() else set()
    for name in sorted(REQUIRED_ADRS - actual_adrs):
        errors.append(f"missing required ADR: docs/adr/{name}")

    registry_path = ROOT / "config" / "policy_registry.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if registry.get("live_trading_enabled") is not False:
        errors.append("policy registry must set live_trading_enabled=false")
    policies = registry.get("policies", {})
    if set(policies) != REQUIRED_POLICIES:
        errors.append("policy registry has missing or unexpected policy kinds")
    versions: list[str] = []
    for kind, record in policies.items():
        version = record.get("version")
        if not isinstance(version, str) or not version.strip():
            errors.append(f"{kind} policy has no version")
        else:
            versions.append(version)
        if record.get("status") not in {"DRAFT", "ACCEPTED", "RETIRED"}:
            errors.append(f"{kind} policy has invalid status")
        document = record.get("document")
        document_path = ROOT / document if isinstance(document, str) else None
        if document_path is None or not document_path.is_file():
            errors.append(f"{kind} policy document does not exist")
        else:
            expected_hash = record.get("document_hash")
            actual_hash = sha256(document_path.read_bytes()).hexdigest()
            if expected_hash != actual_hash:
                errors.append(f"{kind} policy document hash does not match")
        status = record.get("status")
        effective_from = record.get("effective_from")
        approved_by = record.get("approved_by")
        if status == "ACCEPTED" and (effective_from is None or not approved_by):
            errors.append(f"{kind} accepted policy lacks effectivity or approval")
        if status == "DRAFT" and (effective_from is not None or approved_by is not None):
            errors.append(f"{kind} draft policy must not be effective or approved")
    if len(versions) != len(set(versions)):
        errors.append("policy versions must be unique")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("governance checks passed; live trading remains disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
