import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.render_foundation_monitoring import render


class MonitoringTemplateTest(unittest.TestCase):
    def test_renderer_binds_all_policies_to_instance_and_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = render(
                source_dir=Path("deploy/monitoring"),
                output_dir=Path(tmp),
                instance_id="123456789",
                notification_channel=(
                    "projects/test-project/notificationChannels/channel-1"
                ),
            )
            self.assertEqual(len(paths), 6)
            for path in paths:
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("__INSTANCE_ID__", text)
                self.assertNotIn("__NOTIFICATION_CHANNEL__", text)
                payload = yaml.safe_load(text)
                self.assertEqual(
                    payload["notificationChannels"],
                    ["projects/test-project/notificationChannels/channel-1"],
                )
                self.assertIn("123456789", text)


if __name__ == "__main__":
    unittest.main()
