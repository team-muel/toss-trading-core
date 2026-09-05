from datetime import datetime, timezone

from alpha_management import Alpha, AlphaSimulationSettings, RepositoryDataFields, simulate_cross_section
from alpha_management import operators as ops
from alpha_management.metrics import fitness, is_os_split
from asset_management.time.asof import AsOfContext


class FakeSource:
    def cross_section(self, field, *, universe, context):
        assert field == "close"
        assert universe == "TEST"
        return {"i-1": "100", "i-2": "120", "i-3": "80"}

    def time_series(self, field, *, instrument_id, context):
        assert field == "close"
        return ["100", "105", "110"]


def asof():
    now = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
    return AsOfContext(
        run_id="alpha-test",
        as_of_utc=now,
        information_cutoff_utc=now,
        policy_version="test",
        parameter_set_id="test",
        code_revision="test",
    )


def test_repository_datafields_require_explicit_asof_and_convert_values():
    fields = RepositoryDataFields(FakeSource())
    assert fields.cross_section("close", universe="TEST", context=asof()) == {
        "i-1": 100.0,
        "i-2": 120.0,
        "i-3": 80.0,
    }
    assert fields.time_series("close", instrument_id="i-1", context=asof()) == [100.0, 105.0, 110.0]


def test_brain_operator_vocabulary_is_available():
    values = {"a": 1.0, "b": 2.0, "c": 3.0}
    assert ops.rank(values) == {"a": 0.0, "b": 0.5, "c": 1.0}
    assert ops.ts_delta([1.0, 2.0, 5.0], 1) == [None, 1.0, 3.0]
    assert ops.ts_mean([1.0, 2.0, 3.0], 2) == [None, 1.5, 2.5]


def test_alpha_simulation_stops_at_research_positions():
    alpha = Alpha("rank_close", lambda ctx: ops.rank(ctx["close"]))
    settings = AlphaSimulationSettings(
        universe="TEST",
        book_size=1.0,
        neutralization="none",
        truncation=0.5,
        long_only=True,
    )
    result = simulate_cross_section(alpha, {"close": {"a": 1.0, "b": 2.0, "c": 3.0}}, settings)
    assert result.alpha == "rank_close"
    assert set(result.weights) == {"a", "b", "c"}
    assert sum(abs(value) for value in result.weights.values()) <= 1.0 + 1e-12
    assert max(abs(value) for value in result.weights.values()) <= 0.5 + 1e-12


def test_brain_fitness_and_is_os_split():
    assert fitness(2.0, 0.10, 0.25) > 0
    insample, outsample = is_os_split(8, 0.25)
    assert list(insample) == [0, 1, 2, 3, 4, 5]
    assert list(outsample) == [6, 7]
