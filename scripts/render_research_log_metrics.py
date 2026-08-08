from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def render_log_metrics(
    *,
    source: Path,
    output_dir: Path,
) -> list[Path]:
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("log metric definition must be an object")
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []

    for item in payload.get("metrics", []):
        config: dict[str, Any] = {
            "name": item["name"],
            "description": (
                f"Toss research automation {item['event']} events"
            ),
            "filter": (
                'resource.type="gce_instance" '
                f'AND jsonPayload.event="{item["event"]}"'
            ),
            "metricDescriptor": {
                "metricKind": "DELTA",
                "valueType": "INT64",
                "unit": "1",
            },
        }
        destination = output_dir / f"{item['name']}.json"
        destination.write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )
        rendered.append(destination)

    for item in payload.get("distributions", []):
        filter_text = (
            'resource.type="gce_instance" '
            f'AND jsonPayload.event="{item["event"]}"'
        )
        required_state = item.get("required_strategy_state")
        if required_state:
            filter_text += (
                f' AND jsonPayload.strategy_state="{required_state}"'
            )
        config = {
            "name": item["name"],
            "description": item["description"],
            "filter": filter_text,
            "valueExtractor": f'EXTRACT(jsonPayload.{item["field"]})',
            "bucketOptions": {
                "explicitBuckets": {
                    "bounds": item["explicit_bounds"],
                }
            },
            "metricDescriptor": {
                "metricKind": "DELTA",
                "valueType": "DISTRIBUTION",
                "unit": item["unit"],
            },
        }
        destination = output_dir / f"{item['name']}.json"
        destination.write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )
        rendered.append(destination)

    names = [path.stem for path in rendered]
    if len(names) != len(set(names)):
        raise ValueError("duplicate log metric name")
    if len(rendered) != 24:
        raise ValueError(
            f"expected 24 research log metrics, found {len(rendered)}"
        )
    return sorted(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="deploy/monitoring-research/log-metrics.yaml",
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    for path in render_log_metrics(
        source=Path(args.source),
        output_dir=Path(args.output_dir),
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
