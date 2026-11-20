"""Interface every site module implements."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from .. import pagereader
from ..utils import clean_text

if TYPE_CHECKING:  # pragma: no cover
    from ..http import Http
    from ..models import Listing


class SourceBlocked(Exception):
    """The site answered with a bot wall, captcha, 403 or 429. Never try to get around it."""


class SourceError(Exception):
    """Anything else: network error, unexpected status, or a page layout we no longer recognise."""


class SourcePartial(SourceError):
    """A later request failed after the source had already fetched usable results."""

    def __init__(self, listings: List[Listing], error: Exception, context: str) -> None:
        super().__init__(f"{context}: {error}")
        self.listings = listings
        self.blocked = isinstance(error, SourceBlocked)


def hourly_deep_read(now: Optional[datetime] = None) -> bool:
    """True in the first ten minutes of each hour: the one run in six that reads a long list to its end."""
    return (now or datetime.now(timezone.utc)).minute < 10


class Source:
    name: str = ""                 # slug; also the prefix of every Listing.uid from this source
    label: str = ""                # human name shown in alerts and on the dashboard
    homepage: str = ""
    cities: Sequence[str] = ("den-haag", "amsterdam", "leiden")
    note: str = ""                 # shown on the dashboard, e.g. "paid account needed to reply"
    # per_city=True: the runner calls fetch(city) once per configured city.
    # per_city=False: the runner calls fetch("*") once; the source returns listings for any city
    # (e.g. one mailbox with alerts for all cities). The runner drops cities that aren't configured.
    per_city: bool = True
    # True: this landlord/agency applies an income test to every home it lets (stated in its own
    # conditions or FAQ), whether or not the individual listing repeats it. Every listing gets the flag.
    income_requirement: bool = False
    # Set by the runner before fetch: this source has never returned a listing yet (see page_limit).
    baseline: bool = False
    # True: the search page shows one amount for whole homes and for single places alike, so only the listing's
    # own page says what the rent buys. Until that page has been read (by today's rules) the rent is not divided.
    rent_basis_from_page: bool = False
    # The settings of this run (config.json), set by the runner before it calls fetch. None when a
    # source is used on its own, e.g. in a test: sources must work without it.
    cfg: Optional[Dict[str, Any]] = None

    @property
    def has_detail_pages(self) -> bool:
        """False for a source that only has its feed (the base fetch_details does nothing). The runner
        then spends no detail budget on it and never treats "nothing new" as a confirmation."""
        return type(self).fetch_details is not Source.fetch_details

    def page_limit(self, usual: int, deep: int) -> int:
        """How many pages of a long list to read this run. A list that is sorted newest first needs its first
        pages only. Two runs read on, to `deep` pages: the source's first run (its silent baseline, so that the
        older homes are known before anything is alerted) and one run an hour (a home the first pages missed,
        or a list that is not sorted the way it seemed, is then found within the hour)."""
        return deep if (self.baseline or hourly_deep_read()) else usual

    def max_rent(self, default: int) -> int:
        """The highest rent any configured search accepts, for sites that filter on price themselves."""
        searches = (self.cfg or {}).get("searches") or []
        limits = [s.get("max_total") for s in searches if isinstance(s, dict)]
        limits = [int(x) for x in limits if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
        return max(limits) if limits else default

    def unavailable_reason(self) -> Optional[str]:
        """None if the source can run; otherwise a short reason ("IMAP not configured").
        The runner then skips it and shows it as 'disabled' on the dashboard, without alerts."""
        return None

    def fetch(self, city: str, http: "Http") -> List["Listing"]:
        """Return the newest listings for one city slug (or "*", see per_city).
        Raise SourceBlocked or SourceError on failure, or SourcePartial with the results already fetched."""
        raise NotImplementedError

    def fetch_details(self, listing: "Listing", http: "Http") -> "Listing":
        """Optional: fill description and missing fields from the detail page. Called only for new matches."""
        return listing

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Source {self.name}>"


def summary_description(listing: "Listing", html: str, site: str) -> "Listing":
    """For a detail page without its usual description block: use the page's own summary (JSON-LD
    or meta description of this very listing, see pagereader.own_description) and mark it, so the
    run log shows that the layout changed. Raises SourceBlocked for a login or captcha page and
    SourceError when the page gives no description at all: never a silent half-read page."""
    page = pagereader.read_page(html)
    text = pagereader.own_description(page, listing.url)
    if text:
        listing.description = clean_text(text)
        listing.extra["description_from_summary"] = True
        return listing
    if page.password_field or page.captcha_frame:
        raise SourceBlocked(f"{site} detail page shows a login or captcha instead of the listing")
    raise SourceError(f"layout changed: {site} detail page has no description")
