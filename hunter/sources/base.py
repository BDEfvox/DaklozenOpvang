"""Interface every site module implements."""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from ..http import Http
    from ..models import Listing


class SourceBlocked(Exception):
    """The site answered with a bot wall, captcha, 403 or 429. Never try to get around it."""


class SourceError(Exception):
    """Anything else: network error, unexpected status, or a page layout we no longer recognise."""


class Source:
    name: str = ""                 # slug; also the prefix of every Listing.uid from this source
    label: str = ""                # human name shown in alerts and on the dashboard
    homepage: str = ""
    cities: Sequence[str] = ("den-haag", "amsterdam", "leiden")
    enabled_by_default: bool = True
    note: str = ""                 # shown on the dashboard, e.g. "paid account needed to reply"
    # per_city=True: the runner calls fetch(city) once per configured city.
    # per_city=False: the runner calls fetch("*") once; the source returns listings for any city
    # (e.g. one mailbox with alerts for all cities). The runner drops cities that aren't configured.
    per_city: bool = True

    def unavailable_reason(self) -> Optional[str]:
        """None if the source can run; otherwise a short reason ("IMAP not configured").
        The runner then skips it and shows it as 'disabled' on the dashboard, without alerts."""
        return None

    def fetch(self, city: str, http: "Http") -> List["Listing"]:
        """Return the newest listings for one city slug (or "*", see per_city).
        Raise SourceBlocked / SourceError on failure."""
        raise NotImplementedError

    def fetch_details(self, listing: "Listing", http: "Http") -> "Listing":
        """Optional: fill description and missing fields from the detail page. Called only for new matches."""
        return listing

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Source {self.name}>"
