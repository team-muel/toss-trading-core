from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.time.clock import FrozenClock
from asset_management.time.asof import AsOfContext
from asset_management.reference.instruments import InstrumentRepository
from asset_management.reference.aliases import AliasRepository
from asset_management.reference.universe import UniverseRepository
from asset_management.reference.calendars import SessionRepository
from asset_management.reference.corporate_actions import CorporateActionRepository
from asset_management.data.prices import PriceObservationStore, require_price_basis
from asset_management.data.prices import calculate_price_return
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import DataQualityError, ReconciliationError

ROOT = Path(__file__).parents[1]
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ctx(day):
    t = T + timedelta(days=day)
    return AsOfContext('run', t, t, 'policy', 'params', 'revision')


def hist(start=0, end=None, known=0):
    return dict(effective_from=T + timedelta(days=start),
                effective_to=T + timedelta(days=end) if end is not None else None,
                available_at=T + timedelta(days=known), source='fixture:exchange-notice')


@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    Migrator(conn, FrozenClock(T)).migrate(load_migration_catalog(ROOT / 'schemas'))
    return conn


def instrument(db, **dates):
    return InstrumentRepository(db).register(ticker='OLD', toss_symbol='OLD',
        vendor_symbol='OLD.X', cik=None, mic='XNYS', asset_class='EQUITY',
        currency='USD', timezone='America/New_York', **hist(**dates))


def test_rename_keeps_id_and_does_not_leak_new_alias(db):
    iid = instrument(db)
    aliases = AliasRepository(db)
    aliases.add(alias_id='old', instrument_id=iid, alias_type='ticker', alias_value='OLD', **hist(end=10))
    aliases.add(alias_id='new', instrument_id=iid, alias_type='ticker', alias_value='NEW', **hist(start=10, known=9))
    assert aliases.resolve('ticker', 'OLD', ctx(5)) == iid
    assert aliases.resolve('ticker', 'NEW', ctx(10)) == iid
    with pytest.raises(DataQualityError):
        aliases.resolve('ticker', 'NEW', ctx(5))
    with pytest.raises(DataQualityError):
        aliases.resolve('ticker', 'OLD', ctx(10))
    aliases.add(alias_id='collision', instrument_id=instrument(db, known=1),
                alias_type='ticker', alias_value='NEW', **hist(start=10, known=9))
    with pytest.raises(DataQualityError, match='AMBIGUOUS'):
        aliases.resolve('ticker', 'NEW', ctx(10))


def test_historical_universe_and_delisting(db):
    iid = instrument(db, start=3, end=10)
    universe = UniverseRepository(db)
    universe.include(membership_id='member', universe_id='u', instrument_id=iid,
                     inclusion_reason='approved eligibility', **hist())
    assert universe.members('u', ctx(2)) == ()
    assert universe.members('u', ctx(5)) == (iid,)
    assert universe.members('u', ctx(10)) == ()
    assert universe.members('u', ctx(5)) == (iid,)
    with pytest.raises(DataQualityError, match='HISTORY_MISSING'):
        universe.members('unknown', ctx(5))


def test_revision_knowledge_time_and_immutability(db):
    iid = instrument(db)
    repo = InstrumentRepository(db)
    old = repo.get(iid, ctx(1))
    repo.register(**old, **hist(end=10, known=8))
    assert repo.get(iid, ctx(7)) == old
    with pytest.raises(DataQualityError, match='NOT_LISTED'):
        repo.get(iid, ctx(10))
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        db.execute("DELETE FROM am_reference_record")


def test_known_future_version_does_not_hide_current_version(db):
    iid = instrument(db)
    current = InstrumentRepository(db).get(iid, ctx(9))
    future = dict(current, ticker='NEW', toss_symbol='NEW', vendor_symbol='NEW.X')
    InstrumentRepository(db).register(**future, **hist(start=10, known=8))
    assert InstrumentRepository(db).get(iid, ctx(9))['ticker'] == 'OLD'
    assert InstrumentRepository(db).get(iid, ctx(10))['ticker'] == 'NEW'


def test_prelisting_price_rejected_and_bases_separated(db):
    iid = instrument(db, start=3)
    prices = PriceObservationStore(db)
    with pytest.raises(DataQualityError, match='OUTSIDE_LISTING'):
        prices.append(instrument_id=iid, basis='raw', price='10', context=ctx(5),
                      event_time=T, available_at=T)
    assert require_price_basis(['raw', 'raw'], ledger=True) == 'raw'
    for bases, kwargs in [(['raw', 'total_return'], {}),
                          (['total_return'], {'cash_dividends': True}),
                          (['split_adjusted'], {'ledger': True})]:
        with pytest.raises(DataQualityError):
            require_price_basis(bases, **kwargs)


def test_split_and_reverse_split_reconciliation(db):
    iid = instrument(db)
    repo = CorporateActionRepository(db)
    for action, kind, ratio, qty, price in [('split', 'SPLIT', '2', '20', '50'),
                                          ('reverse', 'REVERSE_SPLIT', '0.5', '5', '200')]:
        repo.record(action_id=action, instrument_id=iid, action_type=kind,
                    terms={'ratio': ratio}, **hist())
        assert repo.reconcile_split(action, before_quantity='10', after_quantity=qty,
                before_price='100', after_price=price, context=ctx(1)) == 'MATCH'
        with pytest.raises(ReconciliationError, match='MISMATCH'):
            repo.reconcile_split(action, before_quantity='10', after_quantity='999',
                                 before_price='100', after_price=price, context=ctx(1))


def test_calendar_dst_early_close_and_unknown_session(db):
    repo = SessionRepository(db)
    for day, opening, closing in [('2026-03-06', 14, 21), ('2026-03-09', 13, 20)]:
        base = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        repo.record(exchange='XNYS', local_date=day, timezone='America/New_York',
                    session_status='OPEN', regular_open=base + timedelta(hours=opening, minutes=30),
                    regular_close=base + timedelta(hours=closing), **hist())
        assert repo.session('XNYS', day, ctx(70))['regular_close'].endswith(f'{closing}:00:00+00:00')
    repo.record(exchange='XNYS', local_date='2026-11-27', timezone='America/New_York',
                session_status='OPEN', early_close=True,
                regular_open=datetime(2026,11,27,14,30,tzinfo=timezone.utc),
                regular_close=datetime(2026,11,27,18,tzinfo=timezone.utc), **hist())
    assert repo.session('XNYS', '2026-11-27', ctx(340))['early_close'] is True
    repo.record(exchange='XNYS', local_date='2026-12-25', timezone='America/New_York',
                session_status='CLOSED', **hist())
    assert repo.session('XNYS', '2026-12-25', ctx(360))['session_status'] == 'CLOSED'
    with pytest.raises(DataQualityError, match='MISSING'):
        repo.session('XNYS', '2026-03-07', ctx(70))
    with pytest.raises(DataQualityError, match='HOURS_MISSING'):
        repo.record(exchange='XNYS', local_date='2026-03-07', timezone='America/New_York',
                    session_status='OPEN', **hist())


def test_price_storage_return_and_delisting_action(db):
    iid = instrument(db)
    store = PriceObservationStore(db)
    def price(day, basis, value):
        when = T + timedelta(days=day)
        raw = SQLiteRawResponseStore(db).append(source='fixture', endpoint='/price',
            http_method='GET', request_payload={}, status_code=200, body={'price': value},
            requested_at=when, received_at=when, account_id=None, schema_version='1')
        return store.append(instrument_id=iid, basis=basis, price=value, context=ctx(5),
            reference_period=when.isoformat(), event_time=when, scheduled_release_at=None,
            official_release_at=None, source_timestamp=when, received_at=when,
            available_at=when, ingested_at=when, revised_at=None, source_timezone='UTC',
            schema_version='1', raw_response_id=raw)
    first, second = price(1, 'raw', '100'), price(2, 'raw', '110')
    assert calculate_price_return(first, second, context=ctx(5)) == Decimal('0.1')
    adjusted = price(2, 'total_return', '111')
    with pytest.raises(DataQualityError, match='MIXED'):
        calculate_price_return(first, adjusted, context=ctx(5))
    total_start = price(1, 'total_return', '100')
    with pytest.raises(DataQualityError, match='DOUBLE_COUNT'):
        calculate_price_return(total_start, adjusted, context=ctx(5), cash_dividend='1')
    u = UniverseRepository(db)
    u.include(membership_id='m', universe_id='u', instrument_id=iid,
              inclusion_reason='eligible', **hist())
    CorporateActionRepository(db).record(action_id='delist', instrument_id=iid,
        action_type='DELISTING', terms={'reason': 'exchange notice'}, **hist(start=3, known=2))
    assert u.members('u', ctx(2)) == (iid,)
    assert u.members('u', ctx(3)) == ()
    with pytest.raises(DataQualityError, match='AFTER_DELISTING'):
        price(4, 'raw', '1')


def test_orphan_reference_and_naive_time_are_blocked(db):
    with pytest.raises(DataQualityError, match='INSTRUMENT_MISSING'):
        AliasRepository(db).add(alias_id='orphan', instrument_id='not-registered',
                               alias_type='ticker', alias_value='X', **hist())
    from asset_management.domain.errors import TemporalViolation
    with pytest.raises(TemporalViolation):
        InstrumentRepository(db).append(
            'INSTRUMENT', 'x', {}, effective_from=datetime(2026,1,1),
            available_at=T, source='fixture')
    iid = instrument(db)
    with pytest.raises(DataQualityError, match='TYPE_UNKNOWN'):
        CorporateActionRepository(db).record(
            action_id='bad', instrument_id=iid, action_type='UNKNOWN', terms={'x': '1'}, **hist()
        )
    with pytest.raises(DataQualityError, match='BASIS_UNKNOWN'):
        require_price_basis(['mystery'])


def test_action_comparisons_are_idempotent_and_auditable(db):
    iid = instrument(db)
    repo = CorporateActionRepository(db)
    repo.record(action_id='split', instrument_id=iid, action_type='SPLIT',
                terms={'ratio': '2'}, **hist())
    kwargs = dict(before_quantity='10', after_quantity='20', before_price='100',
                  after_price='50', context=ctx(1))
    repo.reconcile_split('split', **kwargs)
    repo.reconcile_split('split', **kwargs)
    assert db.execute('SELECT COUNT(*) FROM am_corporate_action_comparison').fetchone()[0] == 1
    with pytest.raises(ReconciliationError):
        repo.reconcile_split('split', **dict(kwargs, after_quantity='19'))
    assert db.execute("SELECT COUNT(*) FROM am_corporate_action_comparison WHERE status='MISMATCH'").fetchone()[0] == 1

