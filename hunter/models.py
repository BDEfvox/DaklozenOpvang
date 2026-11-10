"""The Listing record every source returns."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional

# City slugs used everywhere in the code, with their display names.
CITIES: Dict[str, str] = {
    "den-haag": "Den Haag",
    "amsterdam": "Amsterdam",
    "leiden": "Leiden",
}


@dataclass
class Listing:
    source: str                     # source slug, e.g. "kamernet"
    source_id: str                  # stable id on that site
    url: str
    title: str
    city: str                       # city slug: den-haag | amsterdam | leiden
    price_total: Optional[int] = None            # monthly rent in whole euros, as listed
    price_includes_utilities: Optional[bool] = None
    bedrooms: Optional[int] = None               # "slaapkamers"
    rooms: Optional[int] = None                  # "kamers" (includes the living room)
    area_m2: Optional[int] = None
    property_type: Optional[str] = None          # apartment | house | room | studio | parking | other
    furnished: Optional[str] = None              # furnished | upholstered | unfurnished
    address: Optional[str] = None
    postcode: Optional[str] = None
    available_from: Optional[str] = None         # ISO date (YYYY-MM-DD)
    available_until: Optional[str] = None        # ISO date, for fixed-term rentals
    description: Optional[str] = None            # free text, used for flag detection (keep <= 4000 chars)
    image_url: Optional[str] = None
    posted_at: Optional[str] = None              # ISO datetime if the site gives it
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["uid"] = self.uid
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Listing":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})
