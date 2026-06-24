import unittest

from toss_trading.cli.foundation_snapshot import build_parser


class FoundationCliTest(unittest.TestCase):
    def test_parser_defaults_to_runtime_outputs(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.db, "runtime/foundation_account_state.sqlite")
        self.assertEqual(args.report, "runtime/foundation_account_state_report.txt")
        self.assertTrue(args.include_sellable_quantity)

    def test_parser_can_skip_sellable_quantity(self):
        args = build_parser().parse_args(["--skip-sellable-quantity"])
        self.assertFalse(args.include_sellable_quantity)


if __name__ == "__main__":
    unittest.main()
