"""Source-bound Treasury par quotes, without return or curve authority."""
from dataclasses import asdict
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from .alfred import SOURCE, TREASURY_SERIES, _day, read_vintage_rows
from .immutable import ImmutableDatasetStore, StoredDatasetManifest, canonical
from ..domain.errors import DataQualityError
from ..time.asof import AsOfContext, require_as_of_context

DATASET = "treasury-quote-snapshot"
SCHEMA = "treasury-quote-snapshot-v1"
TENORS = {"DGS1MO": 1, "DGS3MO": 3, "DGS6MO": 6, "DGS1": 12}
POLICY = {"source_timezone": "America/Chicago", "publication_lag_days": 1,
          "maximum_age_calendar_days": 7, "scope": "DATE_VINTAGE_ONLY"}


def _assemble(store, manifests, observation_date, vintage_date, context):
    context = require_as_of_context(context)
    if not re.fullmatch(r"git:[0-9a-f]{7,40}", context.code_revision):
        raise DataQualityError("INVALID_CODE_REVISION")
    if not isinstance(manifests, dict) or set(manifests) != TREASURY_SERIES:
        raise DataQualityError("TREASURY_TENORS_INCOMPLETE")
    observation, vintage = _day(observation_date), _day(vintage_date)
    local_day = context.information_cutoff_utc.astimezone(ZoneInfo(POLICY["source_timezone"])).date()
    if observation > vintage or vintage > local_day - timedelta(days=POLICY["publication_lag_days"]):
        raise DataQualityError("TREASURY_DATE_NOT_ELIGIBLE")
    if (local_day - observation).days > POLICY["maximum_age_calendar_days"]:
        raise DataQualityError("TREASURY_QUOTES_STALE")
    quotes, parents = [], []
    for series in sorted(TREASURY_SERIES):
        rows = read_vintage_rows(store, manifests[series], series, context)
        selected = [row for row in rows if row["observation_date"] == observation_date
                    and row["realtime_start"] <= vintage_date <= row["realtime_end"]]
        if len(selected) != 1:
            raise DataQualityError("TREASURY_VINTAGE_MISSING_OR_CONFLICTING")
        parent = store.read_manifest(manifests[series]); parents.append(parent)
        quotes.append({"series_id": series, "tenor_months": TENORS[series],
                       "parent_manifest_id": parent.manifest_id, "observation": selected[0]})
    if len({parent.license_tag for parent in parents}) != 1:
        raise DataQualityError("TREASURY_SOURCE_LICENSE_CONFLICT")
    context_body = asdict(context)
    for name in ("as_of_utc", "information_cutoff_utc"):
        context_body[name] = context_body[name].isoformat()
    body = {"schema_version": SCHEMA, "mode": "RESEARCH", "semantic_type": "TREASURY_PAR_QUOTES",
            "currency": "USD", "unit": "PERCENT", "quote_basis": "BOND_EQUIVALENT_SIMPLE_ANNUAL",
            "coupon_convention": "SEMIANNUAL", "source_day_count": "ACTUAL_365_OR_366",
            "observation_date": observation_date, "vintage_date": vintage_date,
            "date_selection_policy": POLICY, "context": context_body,
            "official_publication_time": "UNKNOWN", "quotes": quotes,
            "status": "MISSING" if any(q["observation"]["value_status"] == "MISSING" for q in quotes) else "OBSERVED",
            "return_authority": False, "acceptance_evaluated": False}
    request = {"source": SOURCE, "dataset": DATASET, "endpoint": "/derived/treasury-quote-snapshot",
               "http_method": "POST", "request": {"manifests": dict(sorted(manifests.items())),
                 "observation_date": observation_date, "vintage_date": vintage_date, "context": context_body}}
    return body, request, parents


def persist_treasury_quote_snapshot(*, store: ImmutableDatasetStore, manifests: dict,
                                   observation_date: str, vintage_date: str,
                                   context: AsOfContext) -> StoredDatasetManifest:
    if not store.credentials_classified:
        raise DataQualityError("CREDENTIALS_NOT_CLASSIFIED")
    context = require_as_of_context(context)
    if not re.fullmatch(r"git:[0-9a-f]{7,40}", context.code_revision):
        raise DataQualityError("INVALID_CODE_REVISION")
    body, request, parents = _assemble(store, manifests, observation_date, vintage_date, context)
    return store.write(body, layer="gold", source=SOURCE, dataset=DATASET, schema_version=SCHEMA,
        retrieved_at=context.as_of_utc, available_at=context.as_of_utc,
        provider_timestamp=max(datetime.fromisoformat(p.provider_timestamp) for p in parents),
        license_tag=parents[0].license_tag, code_revision=context.code_revision,
        request_hash=store.catalog("requests", request), parent_manifest_ids=tuple(manifests.values()))


def validate_treasury_snapshot_lineage(store, manifest, body):
    request = store.read_catalog("requests", manifest.request_hash)
    value = request.get("request", {})
    ctx = dict(value["context"])
    for name in ("as_of_utc", "information_cutoff_utc"):
        ctx[name] = datetime.fromisoformat(ctx[name])
    context = AsOfContext(**ctx)
    expected, expected_request, parents = _assemble(store, value["manifests"],
        value["observation_date"], value["vintage_date"], context)
    if (canonical(body) != canonical(expected) or canonical(request) != canonical(expected_request)
            or manifest.parent_manifest_ids != tuple(sorted(value["manifests"].values()))
            or manifest.available_at != context.as_of_utc.isoformat()
            or manifest.retrieved_at != context.as_of_utc.isoformat()
            or manifest.code_revision != context.code_revision
            or manifest.license_tag != parents[0].license_tag
            or manifest.provider_timestamp != max(p.provider_timestamp for p in parents)):
        raise DataQualityError("TREASURY_SNAPSHOT_LINEAGE_CONFLICT")
