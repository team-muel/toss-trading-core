from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from toss_trading.cli.research_upload_gcs import _parse_gs_uri, upload_tree


class _Response:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}
        self.text = ""

    def json(self) -> dict[str, str]:
        return self._payload


class _Session:
    instances: list["_Session"] = []

    def __init__(self, credentials: object) -> None:
        self.credentials = credentials
        self.posts: list[dict[str, object]] = []
        self.puts: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    def post(self, url: str, **kwargs: object) -> _Response:
        self.posts.append({"url": url, **kwargs})
        return _Response(
            200,
            headers={"Location": f"https://upload.example/{len(self.posts)}"},
        )

    def put(self, url: str, **kwargs: object) -> _Response:
        data = kwargs["data"]
        body = data.read()
        self.puts.append({"url": url, "body": body, **kwargs})
        return _Response(200, payload={"generation": str(len(self.puts))})


class ResearchUploadGcsTests(unittest.TestCase):
    def test_parse_uri_rejects_non_gcs_destination(self) -> None:
        with self.assertRaisesRegex(ValueError, "gs://"):
            _parse_gs_uri("https://storage.googleapis.com/bucket/object")

    def test_upload_tree_uses_create_only_precondition_and_aliases(self) -> None:
        _Session.instances.clear()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "nested").mkdir()
            (root / "nested" / "data.bin").write_bytes(b"market-data")
            report = root / "report.json"
            report.write_text('{"ready":true}', encoding="utf-8")

            with (
                patch(
                    "toss_trading.cli.research_upload_gcs.google.auth.default",
                    return_value=(object(), None),
                ),
                patch(
                    "toss_trading.cli.research_upload_gcs.AuthorizedSession",
                    _Session,
                ),
            ):
                result = upload_tree(
                    source_dir=root,
                    destination_uri="gs://research-bucket/runs/run-1",
                    aliases=[
                        f"{report}=gs://research-bucket/reports/run-1.json"
                    ],
                    workers=1,
                )

        session = _Session.instances[-1]
        self.assertEqual(result["file_count"], 3)
        self.assertEqual(len(session.posts), 3)
        self.assertEqual(len(session.puts), 3)
        self.assertTrue(
            all(
                request["params"]["ifGenerationMatch"] == "0"
                for request in session.posts
            )
        )
        object_names = [request["params"]["name"] for request in session.posts]
        self.assertIn("runs/run-1/nested/data.bin", object_names)
        self.assertIn("reports/run-1.json", object_names)

    def test_alias_cannot_escape_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "inside.txt").write_text("inside", encoding="utf-8")
            outside = root.parent / "outside-upload.txt"
            outside.write_text("outside", encoding="utf-8")
            try:
                with (
                    patch(
                        "toss_trading.cli.research_upload_gcs.google.auth.default",
                        return_value=(object(), None),
                    ),
                    patch(
                        "toss_trading.cli.research_upload_gcs.AuthorizedSession",
                        _Session,
                    ),
                ):
                    with self.assertRaisesRegex(ValueError, "inside source-dir"):
                        upload_tree(
                            source_dir=root,
                            destination_uri="gs://bucket/runs/run-1",
                            aliases=[f"{outside}=gs://bucket/reports/outside.txt"],
                            workers=1,
                        )
            finally:
                outside.unlink(missing_ok=True)

    def test_worker_limit_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "between 1 and 32"):
                upload_tree(
                    source_dir=Path(temp_dir),
                    destination_uri="gs://bucket/runs/run-1",
                    aliases=[],
                    workers=33,
                )


if __name__ == "__main__":
    unittest.main()
