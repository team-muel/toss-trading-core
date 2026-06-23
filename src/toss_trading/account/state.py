from dataclasses import dataclass


@dataclass(frozen=True)
class AccountState:
    broker_cash_buying_power_constraint: float
    estimated_cash_balance: float
    pending_settlement_cash: float
    reserved_cash_open_orders: float
    net_liquidation_value: float
    estimated_fees: float = 0.0
    estimated_tax_reserve: float = 0.0
    liquidity_buffer: float = 0.0
    rolling_20d_avg_nav: float | None = None

    @property
    def available_cash(self) -> float:
        return (
            self.broker_cash_buying_power_constraint
            - self.reserved_cash_open_orders
            - self.estimated_fees
            - self.estimated_tax_reserve
            - self.liquidity_buffer
        )

    @property
    def risk_nav(self) -> float:
        if self.rolling_20d_avg_nav is not None:
            return min(self.net_liquidation_value, self.rolling_20d_avg_nav)
        return self.net_liquidation_value
