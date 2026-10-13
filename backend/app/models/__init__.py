"""Import every model so Alembic and `Base.metadata` see the full schema."""
from app.models.admin import AdminAudit, RuntimeSetting  # noqa: F401
from app.models.catalog import ApiCache, Hotel, Place, Restaurant, WebSource  # noqa: F401
from app.models.circuit import ProviderCircuit  # noqa: F401
from app.models.counters import DailyCounter  # noqa: F401
from app.models.expense import Expense, ExpenseSplit  # noqa: F401
from app.models.rail import (  # noqa: F401
    RailCityRoute,
    RailDailyStat,
    RailPairResult,
    RailStationLookup,
    RailTimetable,
    RailTrainReport,
)
from app.models.plan_job import PlanJob, PlanRefund  # noqa: F401
from app.models.referral import Referral  # noqa: F401
from app.models.share import ShareLink  # noqa: F401
from app.models.trip import (  # noqa: F401
                             Alternative,
                             Itinerary,
                             ItineraryItem,
                             TransportOption,
                             Trip,
                             TripMember,
                             TripPreferences,
)
from app.models.user import LLMUsage, Subscription, User, VerificationCode  # noqa: F401