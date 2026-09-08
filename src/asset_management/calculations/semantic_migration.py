"""Explicit, source-hash-bound migration of legacy economic numbers."""
from decimal import Decimal, InvalidOperation

from asset_management.data.immutable import canonical, digest
from asset_management.domain.economics import (
    EconomicValue, ReturnSemanticType as R, ReturnMetricStatus, RETURN_UNITS,
)
from asset_management.domain.errors import DataQualityError


_LEGACY_FIELDS = {
    ('pricing-result@1', 'required_return'): R.PRICING_BASELINE_RETURN,
    ('alpha-estimate@1', 'alpha'): R.MODEL_RELATIVE_ALPHA,
    ('alpha-estimate@1', 'required_return'): R.PRICING_BASELINE_RETURN,
    ('alpha-estimate@1', 'net_expected_return'): R.FORECAST_TOTAL_RETURN_NET,
    ('expected-return@1', 'net_expected_return'): R.FORECAST_TOTAL_RETURN_NET,
    ('expected-return@1', 'gross_expected_return'): R.FORECAST_TOTAL_RETURN_GROSS,
}


def migrate_legacy_return(raw, *, source_hash, legacy_contract, field,
                          currency, currency_basis, formula_version, reference_version):
    """Returns a new record; never edits the stored source or grants trading scope."""
    if not isinstance(raw, dict) or digest(canonical(raw)) != source_hash:
        raise DataQualityError('SEMANTIC_MIGRATION_SOURCE_HASH_MISMATCH')
    semantic = _LEGACY_FIELDS.get((legacy_contract, field))
    if semantic is None:
        raise DataQualityError('SEMANTIC_MIGRATION_UNSUPPORTED_LEGACY_MEANING')
    if 'schema_version' in raw and raw['schema_version'] != legacy_contract:
        raise DataQualityError('SEMANTIC_MIGRATION_VERSION_CONFLICT')
    if legacy_contract == 'pricing-result@1':
        body = {k:v for k,v in raw.items() if k != 'output_hash'}
        if raw.get('output_hash') != digest(canonical(body)):
            raise DataQualityError('SEMANTIC_MIGRATION_PRICING_HASH_MISMATCH')
        if raw.get('model_key') != reference_version:
            raise DataQualityError('SEMANTIC_MIGRATION_REFERENCE_CONFLICT')
    if legacy_contract == 'alpha-estimate@1':
        try:
            if any(not isinstance(raw[k], str) for k in ('alpha', 'net_expected_return', 'required_return')):
                raise ValueError
            alpha, net, baseline = (Decimal(raw[k]) for k in ('alpha', 'net_expected_return', 'required_return'))
            if any(not v.is_finite() for v in (alpha, net, baseline)) or alpha != net-baseline:
                raise ValueError
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise DataQualityError('SEMANTIC_MIGRATION_ALPHA_IDENTITY_INVALID') from exc
    if not isinstance(raw.get(field), str):
        raise DataQualityError('SEMANTIC_MIGRATION_DECIMAL_STRING_REQUIRED')
    try:
        value = Decimal(raw[field])
    except InvalidOperation as exc:
        raise DataQualityError('SEMANTIC_MIGRATION_INVALID_NUMBER') from exc
    result = EconomicValue(semantic, value, ReturnMetricStatus.AVAILABLE, currency,
                           currency_basis, raw.get('horizon'), RETURN_UNITS[semantic],
                           formula_version, reference_version)
    body = dict(migration_version='economic-migration@1', source_hash=source_hash,
                legacy_contract=legacy_contract, deprecated_field=field,
                economic_value=result.payload())
    return body | {'content_hash': digest(canonical(body))}
