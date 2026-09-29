from .ashby import AshbyAdapter
from .generic import GenericAdapter
from .greenhouse import GreenhouseAdapter
from .icims import ICIMSAdapter
from .indeed import IndeedAdapter
from .lever import LeverAdapter
from .linkedin import LinkedInAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .taleo import TaleoAdapter
from .teamtailor import TeamtailorAdapter
from .workable import WorkableAdapter
from .workday import WorkdayAdapter

ADAPTERS = [GreenhouseAdapter(), LeverAdapter(), AshbyAdapter(), WorkdayAdapter(),
            SmartRecruitersAdapter(), WorkableAdapter(), TeamtailorAdapter(), ICIMSAdapter(),
            TaleoAdapter(), LinkedInAdapter(), IndeedAdapter()]


def get_adapter(url: str, ats: str | None = None):
    # Never let a manually supplied ATS name bypass a restricted source domain.
    detected = next((a for a in ADAPTERS if a.matches(url)), None)
    if detected:
        return detected
    return next((a for a in ADAPTERS if a.name == (ats or "").lower()), GenericAdapter())


def capabilities():
    return [a.describe() for a in [*ADAPTERS, GenericAdapter()]]
