from __future__ import annotations

from email.message import EmailMessage
from typing import Dict, List, Optional, Tuple

import pytest

from hunter.sources import email_alerts as ea
from hunter.sources.base import SourceError
from hunter.sources.email_alerts import EmailAlerts, imap_date, match_listing_url


def make_msg(sender: str, subject: str, html: Optional[str] = None, text: Optional[str] = None) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "alerts@example.test"
    m["Subject"] = subject
    m["Date"] = "Mon, 09 Nov 2026 10:15:00 +0100"
    m.set_content(text or "Your alert (plain text part)")
    if html:
        m.add_alternative(html, subtype="html")
    return m.as_bytes()


PARARIUS_HTML = """
<html><body><h1>3 nieuwe woningen</h1>
<table><tr><td>
  <a href="https://www.pararius.nl/appartement-te-huur/den-haag/ab12cd34/laan-van-meerdervoort?utm_source=alert">
    <img src="x.jpg" alt="foto"></a>
  <a href="https://www.pararius.nl/appartement-te-huur/den-haag/ab12cd34/laan-van-meerdervoort?utm_source=alert">Laan van Meerdervoort 12</a>
  <p>2511 AB Den Haag</p><p>&euro; 1.850 per maand</p><p>75 m&sup2; &middot; 3 kamers &middot; 2 slaapkamers</p>
</td></tr><tr><td>
  <a href="https://www.pararius.nl/huis-te-huur/leiden/ef56ab78/rapenburg">Rapenburg 5</a>
  <p>&euro;2.300 per maand</p><p>110 m&sup2; &middot; 5 kamers</p>
</td></tr><tr><td>
  <a href="https://www.pararius.nl/kamer-te-huur/amsterdam/zz99yy88/een-kamer">Kamer</a><p>&euro; 700</p>
</td></tr></table>
<a href="https://www.pararius.nl/unsubscribe?x=1">Afmelden</a>
<a href="https://www.pararius.nl/appartement-te-huur/den-haag">Alle resultaten</a>
</body></html>"""

FUNDA_HTML = """
<html><body>
<div><a href="https://click.funda-mail.test/track/aaa111?u=1">Prinsengracht 100 A</a>
<span>&euro; 2.250 /mnd</span><span>90 m&sup2;</span><span>4 kamers</span><span>3 slaapkamers</span></div>
<div><a href="https://click.funda-mail.test/track/bbb222?u=2">Bekijk</a><span>&euro; 1.400 /mnd</span>
<span>55 m&sup2;</span></div>
<div><a href="https://click.funda-mail.test/track/ccc333?u=3">Ergens anders</a></div>
<a href="https://click.funda-mail.test/unsub?u=9">Uitschrijven</a>
</body></html>"""

TEXT_ONLY = """Nieuwe woningen voor je zoekopdracht

Zonnebloemstraat 4, Leiden
EUR 1.600 per maand, 70 m2, 3 kamers, 2 slaapkamers
https://www.huurwoningen.nl/huren/leiden/4455667/zonnebloemstraat/

Een link zonder woning: https://www.huurwoningen.nl/huren/leiden/
"""


class FakeIMAP:
    def __init__(self, messages: Dict[bytes, bytes]) -> None:
        self.messages = messages
        self.calls: List[Tuple] = []
        self.readonly: Optional[bool] = None

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        self.readonly = readonly
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.calls.append(("search",) + criteria)
        return "OK", [b" ".join(self.messages)]

    def fetch(self, mid, spec):
        self.calls.append(("fetch", mid, spec))
        return "OK", [(b"1 (BODY[] {10}", self.messages[mid]), b")"]

    def logout(self):
        self.calls.append(("logout",))


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setenv("IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("IMAP_USER", "u")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    src = EmailAlerts()
    redirects = {
        "https://click.funda-mail.test/track/aaa111?u=1":
            "https://www.funda.nl/detail/huur/amsterdam/appartement-43201234-prinsengracht-100-a/?ref=mail",
        "https://click.funda-mail.test/track/bbb222?u=2":
            "https://www.funda.nl/detail/huur/den-haag/huis-43999999-zeestraat-1/",
        "https://click.funda-mail.test/track/ccc333?u=3": "https://www.funda.nl/zoeken/huur?selected_area=x",
    }
    src.resolved: List[str] = []

    def resolver(url):
        src.resolved.append(url)
        return redirects.get(url)

    src._resolver = resolver
    msgs = {
        b"1": make_msg("Pararius <alerts@mail.pararius.nl>", "Nieuwe woningen", html=PARARIUS_HTML),
        b"2": make_msg("Funda <noreply@funda.nl>", "Nieuw aanbod", html=FUNDA_HTML),
        b"3": make_msg("Huurwoningen.nl <info@huurwoningen.nl>", "Nieuw", text=TEXT_ONLY),
        b"4": make_msg("Someone <spam@evil.test>", "x", html='<a href="https://www.pararius.nl/huis-te-huur/leiden/aa11bb22/x">x</a>'),
    }
    fake = FakeIMAP(msgs)
    monkeypatch.setattr(src, "_connect", lambda: fake)
    src.fake = fake
    return src


def by_id(listings):
    return {l.source_id: l for l in listings}


def test_unavailable_reason(monkeypatch):
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert EmailAlerts().unavailable_reason() == "Email alerts not configured"
    monkeypatch.setenv("IMAP_HOST", "h")
    monkeypatch.setenv("IMAP_USER", "u")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    assert EmailAlerts().unavailable_reason() is None


def test_fetch_parses_three_layouts(source):
    out = by_id(source.fetch("*", None))
    assert set(out) == {"pararius-ab12cd34", "pararius-ef56ab78", "funda-43201234", "funda-43999999",
                        "huurwoningen-4455667"}  # room, search links, spam sender, unsubscribe all dropped
    p = out["pararius-ab12cd34"]
    assert p.source == "email_alerts" and p.uid == "email_alerts:pararius-ab12cd34"
    assert p.city == "den-haag" and p.price_total == 1850 and p.area_m2 == 75
    assert p.rooms == 3 and p.bedrooms == 2 and p.property_type == "apartment"
    assert p.title.startswith("Pararius: Laan van Meerdervoort 12")
    assert p.url == "https://www.pararius.nl/appartement-te-huur/den-haag/ab12cd34/laan-van-meerdervoort"
    assert p.extra["site_prefiltered"] is True and p.extra["site"] == "pararius"
    assert p.posted_at == "2026-11-09T09:15:00Z"
    h = out["pararius-ef56ab78"]
    assert h.city == "leiden" and h.property_type == "house" and h.price_total == 2300 and h.rooms == 5


def test_funda_tracking_links_resolved_once_each(source):
    out = by_id(source.fetch("*", None))
    f = out["funda-43201234"]
    assert f.city == "amsterdam" and f.price_total == 2250 and f.bedrooms == 3 and f.area_m2 == 90
    assert f.url == "https://www.funda.nl/detail/huur/amsterdam/appartement-43201234-prinsengracht-100-a"
    assert f.title == "Funda: Prinsengracht 100 A"
    g = out["funda-43999999"]  # generic anchor text "Bekijk" -> title from slug
    assert g.title == "Funda: Zeestraat 1" and g.price_total == 1400 and g.property_type == "house"
    assert len(source.resolved) == 3 and len(set(source.resolved)) == 3
    assert not any("funda.nl" in u or "pararius.nl" in u for u in source.resolved)  # never the sites themselves


def test_plain_text_email(source):
    t = by_id(source.fetch("*", None))["huurwoningen-4455667"]
    assert t.city == "leiden" and t.price_total == 1600 and t.area_m2 == 70 and t.bedrooms == 2
    assert "Zonnebloemstraat" in t.title


def test_mailbox_is_read_only_and_searched_with_since(source):
    source.fetch("*", None)
    assert source.fake.readonly is True
    searches = [c for c in source.fake.calls if c[0] == "search"]
    assert len(searches) == len(ea.SENDER_KEYWORDS)
    assert all(c[1] == "SINCE" and c[3] == "FROM" for c in searches)
    assert all("BODY.PEEK" in c[2] for c in source.fake.calls if c[0] == "fetch")
    assert source.fake.calls[-1] == ("logout",)


def test_resolve_budget(source):
    html = "".join('<div><a href="https://t.test/r/%d">Straat %d</a></div>' % (i, i) for i in range(50))
    msg = make_msg("Funda <noreply@funda.nl>", "many", html=html)
    source.fake.messages = {b"1": msg}
    source.fetch("*", None)
    assert len(source.resolved) == ea.MAX_RESOLVE


def test_embedded_target_needs_no_request(source):
    html = ('<a href="https://t.test/r?url=https%3A%2F%2Fwww.funda.nl%2Fdetail%2Fhuur%2Fleiden%2F'
            'appartement-12345678-x-1%2F">X 1</a>')
    source.fake.messages = {b"1": make_msg("Funda <noreply@funda.nl>", "e", html=html)}
    out = source.fetch("*", None)
    assert [l.source_id for l in out] == ["funda-12345678"] and source.resolved == []


def test_missing_config_raises(monkeypatch):
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SourceError):
        EmailAlerts().fetch("*", None)


def test_helpers():
    assert imap_date(__import__("datetime").datetime(2026, 11, 5)) == "05-Nov-2026"
    assert match_listing_url("https://www.pararius.nl/appartement-te-huur/den-haag") is None
    assert match_listing_url("https://www.huurwoningen.nl/huren/amsterdam/") is None
    assert match_listing_url("https://www.funda.nl/zoeken/huur?x=1") is None
    assert match_listing_url("https://www.funda.nl/huur/den-haag/appartement-1234-x/")["id"] == "1234"


def test_pasted_app_password_loses_its_invisible_spaces(monkeypatch):
    from hunter.sources.email_alerts import imap_credentials
    monkeypatch.setenv("IMAP_HOST", " imap.gmail.com\n")
    monkeypatch.setenv("IMAP_USER", "alerts@example.test\xa0")
    monkeypatch.setenv("IMAP_PASSWORD", "abcd\xa0efgh\xa0ijkl\xa0mnop")  # how Google's page copies it
    assert imap_credentials() == ("imap.gmail.com", "alerts@example.test", "abcdefghijklmnop")
    monkeypatch.setenv("IMAP_PASSWORD", "abcd efgh ijkl mnop\n")
    assert imap_credentials()[2] == "abcdefghijklmnop"
    monkeypatch.setenv("IMAP_PASSWORD", "my own pass phrase")  # not an app password: inner spaces stay
    assert imap_credentials()[2] == "my own pass phrase"
    "abcdefghijklmnop".encode("ascii")  # what imaplib needs to be able to do


GREETING_TEXT = """Beste Voornaam Achternaam,

https://www.pararius.nl/appartement-te-huur/den-haag/ab12cd34/laan-van-meerdervoort

EUR 1.850 per maand, 75 m2, 3 kamers, 2 slaapkamers. Geen woningdelers.

Deze e-mail is verzonden naar mijn.postvak@example.test
"""

GREETING_HTML = """<html><body><p>Beste Voornaam Achternaam,</p>
<div><a href="https://www.pararius.nl/huis-te-huur/leiden/ef56ab78/rapenburg">mijn.postvak@example.test</a>
<p>&euro; 2.300 per maand</p></div>
<p>Verzonden naar mijn.postvak@example.test</p></body></html>"""


def test_titles_never_come_from_the_text_around_a_link():
    src = EmailAlerts()
    src._resolver = lambda url: None
    import email as _email
    msg = _email.message_from_bytes(make_msg("Pararius <alerts@mail.pararius.nl>", "Nieuw", text=GREETING_TEXT))
    out = src.parse_message(msg, [5])
    assert len(out) == 1
    l = out[0]
    assert l.title == "Pararius: Laan Van Meerdervoort"  # from the URL, not "Beste Voornaam Achternaam,"
    assert "Voornaam" not in l.title and "@" not in l.title and "Voornaam" not in (l.address or "")
    # (a URL alone in its paragraph takes the paragraph before it as context: here, the greeting)
    assert "Voornaam" in (l.description or "") and l.extra["private_text"] is True  # in memory only
    # an HTML link whose own text is an email address is not used as a title either
    msg = _email.message_from_bytes(make_msg("Pararius <alerts@mail.pararius.nl>", "Nieuw", html=GREETING_HTML))
    l = src.parse_message(msg, [5])[0]
    assert l.title == "Pararius: Rapenburg" and l.price_total == 2300


def test_html_link_text_is_still_used_as_title(source):
    out = by_id(source.fetch("*", object()))
    assert out["pararius-ab12cd34"].title == "Pararius: Laan van Meerdervoort 12"
    assert all(l.extra.get("private_text") for l in out.values())
