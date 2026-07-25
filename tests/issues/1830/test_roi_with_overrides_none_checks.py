"""Regression tests for issue #1830 — explicit None checks in ROIAssumptions.with_overrides.

``ROIAssumptions.with_overrides`` previously used ``value or self.value`` for
the three float fields. The ``or`` short-circuit treats the legitimate falsy
value ``0.0`` as "no override", silently substituting the default. The float
fields now use ``value if value is not None else self.value`` so that ``0.0``
is preserved.

Note: ``currency`` intentionally keeps its ``or`` short-circuit inside the
``is not None`` branch (an empty currency string is invalid and should fall
back to the default, unlike ``0.0`` labor cost which is a legal value). This
*deliberate difference* between the float fields and the currency field is
asserted here.
"""

from __future__ import annotations

from app.modules.analytics.roi_calculator import ROIAssumptions


def _defaults() -> ROIAssumptions:
    return ROIAssumptions(
        hourly_labor_cost=ROIAssumptions.DEFAULT_HOURLY_LABOR_COST,
        productivity_multiplier=ROIAssumptions.DEFAULT_PRODUCTIVITY_MULTIPLIER,
        avg_time_saved_per_request=ROIAssumptions.DEFAULT_AVG_TIME_SAVED_PER_REQUEST,
        currency=ROIAssumptions.DEFAULT_CURRENCY,
    )


class TestWithOverridesPreservesZeroFloat:
    """Each of the three float fields must keep ``0.0`` instead of falling back."""

    def test_hourly_labor_cost_zero_is_preserved(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides(hourly_labor_cost=0.0)
        assert overridden.hourly_labor_cost == 0.0

    def test_productivity_multiplier_zero_is_preserved(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides(productivity_multiplier=0.0)
        assert overridden.productivity_multiplier == 0.0

    def test_avg_time_saved_per_request_zero_is_preserved(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides(avg_time_saved_per_request=0.0)
        assert overridden.avg_time_saved_per_request == 0.0

    def test_all_three_zero_at_once(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides(
            hourly_labor_cost=0.0,
            productivity_multiplier=0.0,
            avg_time_saved_per_request=0.0,
        )
        assert overridden.hourly_labor_cost == 0.0
        assert overridden.productivity_multiplier == 0.0
        assert overridden.avg_time_saved_per_request == 0.0


class TestWithOverridesRegression:
    """Existing behaviour that must NOT change."""

    def test_no_overrides_returns_equivalent_values(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides()
        assert overridden.hourly_labor_cost == assumptions.hourly_labor_cost
        assert overridden.productivity_multiplier == assumptions.productivity_multiplier
        assert overridden.avg_time_saved_per_request == assumptions.avg_time_saved_per_request
        assert overridden.currency == assumptions.currency

    def test_positive_overrides_apply(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides(
            hourly_labor_cost=120.0,
            productivity_multiplier=4.0,
            avg_time_saved_per_request=15.0,
        )
        assert overridden.hourly_labor_cost == 120.0
        assert overridden.productivity_multiplier == 4.0
        assert overridden.avg_time_saved_per_request == 15.0

    def test_currency_override_applies(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides(currency="cny")
        assert overridden.currency == "CNY"

    def test_currency_empty_string_falls_back(self):
        # Deliberate divergence from the float fields: an empty currency
        # string is invalid, so it falls back to the existing value.
        assumptions = ROIAssumptions(
            hourly_labor_cost=50.0,
            productivity_multiplier=10.0,
            avg_time_saved_per_request=5.0,
            currency="EUR",
        )
        overridden = assumptions.with_overrides(currency="   ")
        assert overridden.currency == "EUR"

    def test_currency_none_keeps_existing(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides(currency=None)
        assert overridden.currency == assumptions.currency

    def test_float_none_keeps_existing(self):
        assumptions = _defaults()
        overridden = assumptions.with_overrides(hourly_labor_cost=None)
        assert overridden.hourly_labor_cost == assumptions.hourly_labor_cost
