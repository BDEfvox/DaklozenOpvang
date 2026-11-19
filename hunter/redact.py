"""Keep secrets out of anything that is printed or saved: the Actions log of a public repository is public."""
from __future__ import annotations

import os
import re
from typing import Set
from urllib.parse import quote

_SECRET_ENV = ("TELEGRAM_BOT_TOKEN", "IMAP_PASSWORD", "IMAP_USER", "TELEGRAM_CHAT_ID")
_extra: Set[str] = set()


def add_secret(value: object) -> None:
    """Also hide this value from now on (e.g. a chat id Telegram handed out during the run)."""
    text = str(value or "").strip()
    if len(text) >= 4:
        _extra.add(text)


def _forms(val: str) -> Set[str]:
    """A secret as it can show up in text: raw, URL-encoded, and a negative chat id without its sign
    (some encodings split the minus off: "chat_id=%2D100123...")."""
    forms = {val, quote(val, safe="")}
    digits = val.lstrip("-")
    if digits != val and digits.isdigit() and len(digits) >= 6:
        forms.add(digits)
    return forms


def redact(text: str) -> str:
    """Remove secret values (raw and URL-encoded) from text that may be published or logged."""
    values: Set[str] = set(_extra)
    for key in _SECRET_ENV:
        raw = os.environ.get(key, "")
        # also the cleaned-up forms the code actually uses (an app password without its spaces)
        values |= {raw.strip(), raw.replace("\xa0", " ").strip(), re.sub(r"\s+", "", raw)}
    forms: Set[str] = set()
    for val in values:
        if len(val) >= 4:
            forms |= _forms(val)
    for form in sorted(forms, key=len, reverse=True):  # longest first, so a part never unmasks the rest
        text = text.replace(form, "***")
    return text
