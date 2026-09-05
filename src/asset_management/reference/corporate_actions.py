"""Point-in-time corporate actions; complex transformations require review."""
from enum import StrEnum
from hashlib import sha256
import json
from asset_management.domain.decimal import exact_decimal
from asset_management.domain.errors import DataQualityError, ReconciliationError
from .instruments import InstrumentRepository


class ActionType(StrEnum):
    DIVIDEND = 'DIVIDEND'
    SPLIT = 'SPLIT'
    REVERSE_SPLIT = 'REVERSE_SPLIT'
    MERGER = 'MERGER'
    SPINOFF = 'SPINOFF'
    DELISTING = 'DELISTING'
    TICKER_CHANGE = 'TICKER_CHANGE'


class CorporateActionRepository(InstrumentRepository):
    def record(self, *, action_id, instrument_id, action_type, terms, **history):
        try:
            action_type = ActionType(action_type)
        except ValueError as error:
            raise DataQualityError('CORPORATE_ACTION_TYPE_UNKNOWN') from error
        if not terms:
            raise DataQualityError('CORPORATE_ACTION_TERMS_MISSING')
        if action_type in {ActionType.SPLIT, ActionType.REVERSE_SPLIT}:
            if 'ratio' not in terms:
                raise DataQualityError('CORPORATE_ACTION_RATIO_MISSING')
            ratio = exact_decimal(terms['ratio'])
            if ratio <= 0 or (action_type == ActionType.SPLIT and ratio <= 1) or (
                    action_type == ActionType.REVERSE_SPLIT and ratio >= 1):
                raise DataQualityError('CORPORATE_ACTION_INVALID_RATIO')
            terms = dict(terms, ratio=str(ratio))
        return self.append('ACTION', action_id, dict(instrument_id=instrument_id,
                           action_type=action_type.value, terms=terms), **history)

    def reconcile_split(self, action_id, *, before_quantity, after_quantity,
                        before_price, after_price, context, price_tolerance='0'):
        action = self.active('ACTION', context).get(action_id)
        if action is None:
            raise ReconciliationError('CORPORATE_ACTION_MISSING')
        if action['action_type'] not in {'SPLIT', 'REVERSE_SPLIT'}:
            raise ReconciliationError('CORPORATE_ACTION_MANUAL_REVIEW_REQUIRED')
        ratio = exact_decimal(action['terms']['ratio'])
        q0, q1, p0, p1, tolerance = map(exact_decimal,
            (before_quantity, after_quantity, before_price, after_price, price_tolerance))
        if min(q0, q1, p0, p1, tolerance) < 0:
            raise ReconciliationError('CORPORATE_ACTION_NEGATIVE_VALUE')
        status = 'MATCH' if q0 * ratio == q1 and abs(p0 / ratio - p1) <= tolerance else 'MISMATCH'
        inputs = json.dumps(dict(action=action, before_quantity=str(q0), after_quantity=str(q1),
                            before_price=str(p0), after_price=str(p1), price_tolerance=str(tolerance),
                            run_id=context.run_id, policy_version=context.policy_version,
                            parameter_set_id=context.parameter_set_id, code_revision=context.code_revision),
                            sort_keys=True, separators=(',', ':'))
        values = (action_id, context.as_of_utc.isoformat(),
                  context.information_cutoff_utc.isoformat(), inputs, status)
        digest = sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO am_corporate_action_comparison VALUES (?,?,?,?,?,?)',
                              (digest, *values))
        if status == 'MISMATCH':
            raise ReconciliationError('CORPORATE_ACTION_QUANTITY_PRICE_MISMATCH')
        return 'MATCH'
