"""Broker field inclusion-aware NAV reconciliation, separate from buying limits."""
from dataclasses import dataclass
from enum import StrEnum

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import ReconciliationError
from .accounting import MoneyTranslation


class NavComponentKind(StrEnum):
    CASH = 'CASH'
    UNSETTLED_CASH = 'UNSETTLED_CASH'
    SECURITIES = 'SECURITIES'
    SETTLEMENT_RECEIVABLE = 'SETTLEMENT_RECEIVABLE'
    SETTLEMENT_PAYABLE = 'SETTLEMENT_PAYABLE'
    OTHER_NET_ASSETS = 'OTHER_NET_ASSETS'
    BROKER_BUYING_POWER = 'BROKER_BUYING_POWER'


@dataclass(frozen=True, slots=True)
class NavComponent:
    field_id: str
    kind: NavComponentKind
    money: MoneyTranslation
    included_in_field: str | None
    inclusion_evidence_id: str

    def __post_init__(self):
        if (not isinstance(self.field_id, str) or not self.field_id.strip() or
                not isinstance(self.kind, NavComponentKind) or not isinstance(self.money, MoneyTranslation) or
                not isinstance(self.inclusion_evidence_id, str) or not self.inclusion_evidence_id.strip() or
                (self.included_in_field is not None and
                 (not isinstance(self.included_in_field, str) or not self.included_in_field.strip()))):
            raise ReconciliationError('NAV_FIELD_SEMANTICS_UNKNOWN')
        if ((self.kind is NavComponentKind.SETTLEMENT_PAYABLE and self.money.amount_native > 0) or
                (self.kind is NavComponentKind.SETTLEMENT_RECEIVABLE and self.money.amount_native < 0)):
            raise ReconciliationError('NAV_SETTLEMENT_SIGN_INVALID')


def reconcile_accounting_nav(components, *, reported_nav, formula_version):
    """Explicit roots are counted once; buying power is always a constraint."""
    if (not isinstance(components, tuple) or not components or
            any(not isinstance(c, NavComponent) for c in components) or
            not isinstance(reported_nav, MoneyTranslation) or
            not isinstance(formula_version, str) or not formula_version.strip()):
        raise ReconciliationError('NAV_EVIDENCE_REQUIRED')
    fields = {c.field_id: c for c in components}
    if len(fields) != len(components):
        raise ReconciliationError('NAV_DUPLICATE_FIELD')
    total = reported_nav.amount_reporting * 0
    evidence = []
    for c in sorted(components, key=lambda v: v.field_id):
        if c.money.reporting_currency != reported_nav.reporting_currency:
            raise ReconciliationError('NAV_CURRENCY_CONFLICT')
        parent = fields.get(c.included_in_field)
        if c.included_in_field is not None:
            if (parent is None or parent is c or parent.included_in_field is not None or
                    parent.kind is NavComponentKind.BROKER_BUYING_POWER or
                    c.kind is NavComponentKind.BROKER_BUYING_POWER):
                raise ReconciliationError('NAV_INCLUSION_UNVERIFIABLE')
            treatment = 'ALREADY_INCLUDED'
        elif c.kind is NavComponentKind.BROKER_BUYING_POWER:
            treatment = 'EXTERNAL_CONSTRAINT_EXCLUDED'
        else:
            treatment = 'COUNTED'
            total += c.money.amount_reporting
        evidence.append(dict(field_id=c.field_id, kind=c.kind.value,
            native_amount=str(c.money.amount_native), native_currency=c.money.native_currency,
            fx_to_reporting=str(c.money.fx_to_reporting), amount_reporting=str(c.money.amount_reporting),
            included_in_field=c.included_in_field, inclusion_evidence_id=c.inclusion_evidence_id, treatment=treatment))
    if not any(e['treatment'] == 'COUNTED' for e in evidence):
        raise ReconciliationError('NAV_ACCOUNTING_FIELDS_MISSING')
    if total != reported_nav.amount_reporting:
        raise ReconciliationError('NAV_BROKER_RECONCILIATION_MISMATCH')
    body = dict(schema_version='accounting-nav-basis@1', semantic_type='ACCOUNTING_NAV',
        reporting_currency=reported_nav.reporting_currency, unit='MONEY', value=str(total),
        formula_version=formula_version, components=evidence)
    return body | {'content_hash':digest(canonical(body))}
