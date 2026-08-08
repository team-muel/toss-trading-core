import unittest
from datetime import datetime, timedelta, timezone

from toss_trading.account import AccountLedger
from toss_trading.engines import Signal
from toss_trading.execution import OrderPlanner
from toss_trading.risk import OrderIntent, RiskDecision


def signal(symbol: str = "SPY") -> Signal:
    return Signal(
        engine="broad_momentum",
        symbol_or_pair=symbol,
        side="BUY",
        raw_score=0.1,
        adjusted_score=0.1,
        target_weight=1.0,
        expected_max_loss=10.0,
        reason_code="top_positive_momentum",
    )


def approved(
    proposed_signal: Signal,
    sizing: dict,
    *,
    account_seq: str = "1",
) -> RiskDecision:
    intent = OrderIntent.create(
        proposed_signal,
        sizing,
        account_seq=account_seq,
        snapshot_run_id="run-1",
        policy_hash="policy-1",
        currency="USD",
        reference_price=500,
    )
    return RiskDecision(
        True,
        "approved",
        intent_hash=intent.intent_hash,
        account_seq=intent.account_seq,
        snapshot_run_id=intent.snapshot_run_id,
        policy_hash=intent.policy_hash,
        approved_notional_decimal=intent.notional_decimal,
        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
    )


class OrderPlannerTest(unittest.TestCase):
    def setUp(self):
        self.ledger = AccountLedger()
        self.ledger.init_schema()
        self.planner = OrderPlanner()

    def tearDown(self):
        self.ledger.close()

    def test_plan_requires_an_approved_risk_decision(self):
        with self.assertRaisesRegex(ValueError, "risk decision rejected"):
            self.planner.create_plan(
                signal(),
                {"client_order_id": "cid-1", "qty": 1},
                risk_decision=RiskDecision(False, "reconciliation blocked"),
                ledger=self.ledger,
                account_seq="1",
                allowed_symbols={"SPY"},
                snapshot_run_id="run-1",
                policy_hash="policy-1",
                currency="USD",
                reference_price=500,
            )

    def test_plan_requires_approved_universe_and_reserves_id(self):
        qqq = signal("QQQ")
        qqq_sizing = {"client_order_id": "cid-2", "qty": 1}
        with self.assertRaisesRegex(ValueError, "approved universe"):
            self.planner.create_plan(
                qqq,
                qqq_sizing,
                risk_decision=approved(qqq, qqq_sizing),
                ledger=self.ledger,
                account_seq="1",
                allowed_symbols={"SPY"},
                snapshot_run_id="run-1",
                policy_hash="policy-1",
                currency="USD",
                reference_price=500,
            )
        proposed_signal = signal()
        sizing = {"client_order_id": "cid-3", "qty": 1}
        plan = self.planner.create_plan(
            proposed_signal,
            sizing,
            risk_decision=approved(proposed_signal, sizing),
            ledger=self.ledger,
            account_seq="1",
            allowed_symbols={"SPY"},
            snapshot_run_id="run-1",
            policy_hash="policy-1",
            currency="USD",
            reference_price=500,
        )
        self.assertEqual(plan.client_order_id, "cid-3")
        self.assertEqual(plan.currency, "USD")
        self.assertEqual(plan.quantity_decimal, "1")
        self.assertEqual(plan.notional_decimal, "500")
        count = self.ledger.conn.execute(
            "SELECT COUNT(*) FROM client_order_id_registry"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_client_order_id_conflict_across_accounts_is_explicit(self):
        proposed_signal = signal()
        sizing = {"client_order_id": "global-id", "qty": 1}
        self.planner.create_plan(
            proposed_signal,
            sizing,
            risk_decision=approved(proposed_signal, sizing),
            ledger=self.ledger,
            account_seq="1",
            allowed_symbols={"SPY"},
            snapshot_run_id="run-1",
            policy_hash="policy-1",
            currency="USD",
            reference_price=500,
        )
        with self.assertRaisesRegex(ValueError, "different account"):
            self.planner.create_plan(
                proposed_signal,
                sizing,
                risk_decision=approved(proposed_signal, sizing, account_seq="2"),
                ledger=self.ledger,
                account_seq="2",
                allowed_symbols={"SPY"},
                snapshot_run_id="run-1",
                policy_hash="policy-1",
                currency="USD",
                reference_price=500,
            )

    def test_plan_rejects_modified_or_expired_approval(self):
        proposed_signal = signal()
        approved_sizing = {"client_order_id": "cid-bound", "qty": 1}
        decision = approved(proposed_signal, approved_sizing)
        with self.assertRaisesRegex(ValueError, "exact order intent"):
            self.planner.create_plan(
                proposed_signal,
                {"client_order_id": "cid-bound", "qty": 2},
                risk_decision=decision,
                ledger=self.ledger,
                account_seq="1",
                allowed_symbols={"SPY"},
                snapshot_run_id="run-1",
                policy_hash="policy-1",
                currency="USD",
                reference_price=500,
            )

        expired = RiskDecision(
            **{
                **decision.__dict__,
                "expires_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat(),
            }
        )
        with self.assertRaisesRegex(ValueError, "expired"):
            self.planner.create_plan(
                proposed_signal,
                approved_sizing,
                risk_decision=expired,
                ledger=self.ledger,
                account_seq="1",
                allowed_symbols={"SPY"},
                snapshot_run_id="run-1",
                policy_hash="policy-1",
                currency="USD",
                reference_price=500,
            )


if __name__ == "__main__":
    unittest.main()
