from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PLACEHOLDERS = (
    "__PROJECT_ID__",
    "__PROJECT_NUMBER__",
    "__INSTANCE_ID__",
    "__DATASET_ID__",
)


def render_dashboard(
    *,
    source: Path,
    output: Path,
    project_id: str,
    project_number: str,
    instance_id: str,
    dataset_id: str,
    etag: str | None = None,
) -> dict[str, Any]:
    if not project_id or not project_number.isdigit() or not instance_id.isdigit():
        raise ValueError("project ID, numeric project number and instance ID are required")
    text = source.read_text(encoding="utf-8")
    for placeholder in PLACEHOLDERS:
        if placeholder not in text:
            raise ValueError(f"dashboard template lacks {placeholder}")
    replacements = {
        "__PROJECT_ID__": project_id,
        "__PROJECT_NUMBER__": project_number,
        "__INSTANCE_ID__": instance_id,
        "__DATASET_ID__": dataset_id,
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    payload = json.loads(text)
    if not isinstance(payload, dict) or not payload.get("gridLayout"):
        raise ValueError("dashboard template is not a grid dashboard")
    if etag:
        payload["etag"] = etag
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="deploy/monitoring-dashboard/research-visual-report.json",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-number", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--etag")
    args = parser.parse_args(argv)
    render_dashboard(
        source=Path(args.source),
        output=Path(args.output),
        project_id=args.project_id,
        project_number=args.project_number,
        instance_id=args.instance_id,
        dataset_id=args.dataset_id,
        etag=args.etag,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
