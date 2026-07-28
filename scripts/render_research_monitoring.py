from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def render(
    *,
    source_dir: Path,
    output_dir: Path,
    instance_id: str,
    notification_channel: str,
) -> list[Path]:
    if not instance_id.isdigit():
        raise ValueError("instance_id must be the numeric GCE instance id")
    if (
        not notification_channel.startswith("projects/")
        or "/notificationChannels/" not in notification_channel
    ):
        raise ValueError(
            "notification_channel must be a full Monitoring resource name"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    for source in sorted(source_dir.glob("research-*.yaml")):
        text = source.read_text(encoding="utf-8")
        if (
            "__INSTANCE_ID__" not in text
            or "__NOTIFICATION_CHANNEL__" not in text
        ):
            raise ValueError(
                f"monitoring template has no required placeholders: {source}"
            )
        text = text.replace("__INSTANCE_ID__", instance_id).replace(
            "__NOTIFICATION_CHANNEL__",
            notification_channel,
        )
        payload = yaml.safe_load(text)
        if not isinstance(payload, dict) or not payload.get("conditions"):
            raise ValueError(f"invalid monitoring policy: {source}")
        destination = output_dir / source.name
        destination.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
        )
        rendered.append(destination)
    if len(rendered) != 6:
        raise ValueError(
            f"expected six research monitoring policies, found {len(rendered)}"
        )
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        default="deploy/monitoring-research",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--notification-channel", required=True)
    args = parser.parse_args(argv)
    for path in render(
        source_dir=Path(args.source_dir),
        output_dir=Path(args.output_dir),
        instance_id=args.instance_id,
        notification_channel=args.notification_channel,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
