"""Period accounting bound to reconciled opening and closing broker NAV evidence."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import ReconciliationError
from .accounting import MoneyTranslation, PositionMark, _account_period_from_nav
from .nav_basis import NavComponent, NavComponentKind, reconcile_accounting_nav


@dataclass(frozen=True, slots=True)
class AccountingNavSnapshot:
    account_id: str
    snapshot_id: str
    as_of: datetime
    provider_contract_version: str
    components: tuple[NavComponent, ...]
    reported_nav: MoneyTranslation
    formula_version: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() for value in (
                self.account_id, self.snapshot_id, self.provider_contract_version)):
            raise ReconciliationError('ACCOUNTING_NAV_SOURCE_REQUIRED')
        if (not isinstance(self.as_of, datetime) or self.as_of.tzinfo is None or
                self.as_of.utcoffset() is None):
            raise ReconciliationError('ACCOUNTING_NAV_TIME_INVALID')
        object.__setattr__(self, 'as_of', self.as_of.astimezone(timezone.utc))
        reconcile_accounting_nav(self.components, reported_nav=self.reported_nav,
                                 formula_version=self.formula_version)

    def payload(self):
        body = dict(account_id=self.account_id, snapshot_id=self.snapshot_id,
                    as_of=self.as_of.isoformat(), provider_contract_version=self.provider_contract_version,
                    reported_nav={key: str(value) for key, value in asdict(self.reported_nav).items()},
                    nav_basis=reconcile_accounting_nav(self.components, reported_nav=self.reported_nav,
                                                       formula_version=self.formula_version))
        return body | {'content_hash': digest(canonical(body))}


def _validate_position_basis(snapshot, positions):
    """Match native securities marks and FX, not just a coincidentally equal total."""
    expected, observed = {}, {}
    for position in positions:
        if not isinstance(position, PositionMark):
            raise ReconciliationError('ACCOUNTING_NAV_POSITION_INVALID')
        key = (position.native_currency, position.reporting_currency, position.current_fx)
        expected[key] = expected.get(key, Decimal(0)) + position.quantity * position.market_price_native
    for component in snapshot.components:
        if component.kind is NavComponentKind.SECURITIES:
            # A securities subtotal and its breakdown cannot both be matched to marks.
            parent = next((c for c in snapshot.components if c.field_id == component.included_in_field), None)
            if parent is not None and parent.kind is NavComponentKind.SECURITIES:
                raise ReconciliationError('ACCOUNTING_NAV_SECURITIES_BASIS_AMBIGUOUS')
            money = component.money
            key = (money.native_currency, money.reporting_currency, money.fx_to_reporting)
            observed[key] = observed.get(key, Decimal(0)) + money.amount_native
    if {k: v for k, v in expected.items() if v} != {k: v for k, v in observed.items() if v}:
        raise ReconciliationError('ACCOUNTING_NAV_POSITION_MISMATCH')


def account_period_with_nav(*, opening: AccountingNavSnapshot, closing: AccountingNavSnapshot,
                            positions, realized_lots, external_flows, performance_periods,
                            dividends=(), interest=(), fees=(), taxes=()):
    """Compute v2 P&L/TWR from evidence; settlement principal is never income or flow."""
    if not isinstance(opening, AccountingNavSnapshot) or not isinstance(closing, AccountingNavSnapshot):
        raise ReconciliationError('ACCOUNTING_NAV_SNAPSHOTS_REQUIRED')
    if opening.account_id != closing.account_id or opening.reported_nav.reporting_currency != closing.reported_nav.reporting_currency:
        raise ReconciliationError('ACCOUNTING_NAV_CONTEXT_MISMATCH')
    if opening.as_of >= closing.as_of or opening.snapshot_id == closing.snapshot_id:
        raise ReconciliationError('ACCOUNTING_NAV_PERIOD_INVALID')
    positions = tuple(positions)
    _validate_position_basis(closing, positions)
    opening_payload, closing_payload = opening.payload(), closing.payload()
    result = _account_period_from_nav(
        reporting_currency=closing.reported_nav.reporting_currency,
        beginning_nav=Decimal(opening_payload['nav_basis']['value']),
        ending_nav=Decimal(closing_payload['nav_basis']['value']),
        positions=positions, realized_lots=realized_lots, external_flows=external_flows,
        dividends=dividends, interest=interest, fees=fees, taxes=taxes,
        performance_periods=performance_periods)
    body = dict(schema_version='portfolio-accounting@2', formula_version='period-accounting@2',
                opening=opening_payload, closing=closing_payload,
                accounting={key: str(value) for key, value in asdict(result).items()})
    return body | {'content_hash': digest(canonical(body))}
