"""Company state; registered but inactive for the ETF-first release."""

from .engine import StateEngine
from .models import StateType

COMPANY_COMPONENTS = ("growth", "profitability", "cash_quality", "leverage",
                      "relative_growth", "estimates", "valuation", "shareholder_yield")


class CompanyStateEngine(StateEngine):
    def __init__(self) -> None:
        super().__init__(state_type=StateType.COMPANY, component_names=COMPANY_COMPONENTS)
