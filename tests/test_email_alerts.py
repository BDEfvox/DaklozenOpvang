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
    html = "".join('<div><a href="https://t.test/r/%d">Straat %d</a></div>' % (i, i) for i in range(100))
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


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8788/x", "http://localhost/admin", "http://[::1]/x", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/", "ftp://example.com/x", "file:///etc/passwd", "http:///nohost",
])
def test_tracking_links_to_local_or_private_addresses_are_never_requested(url, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("must not be requested: %s" % (a,))
    monkeypatch.setattr(ea.requests, "get", boom)
    assert ea._public_http_url(url) is False
    assert EmailAlerts()._resolve(url) is None


def test_public_tracking_link_is_still_resolved(monkeypatch):
    monkeypatch.setattr(ea.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 443))])

    class Resp:
        headers = {"Location": "https://www.funda.nl/detail/huur/den-haag/huis-43999999-zeestraat-1/"}
        def close(self): pass  # noqa: E704
    calls = []
    monkeypatch.setattr(ea.requests, "get", lambda url, **kw: calls.append((url, kw["allow_redirects"])) or Resp())
    assert EmailAlerts()._resolve("https://click.example-mail.test/track/1") == Resp.headers["Location"]
    assert calls == [("https://click.example-mail.test/track/1", False)]
    # a public name that resolves to a private address is refused too
    monkeypatch.setattr(ea.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("10.1.2.3", 443))])
    assert EmailAlerts()._resolve("https://click.example-mail.test/track/2") is None and len(calls) == 1


# ---- what the real mails look like (found with `python -m hunter email-debug`, 2026-11-11) -------------
def test_funda_detail_url_with_id_after_the_street():
    m = match_listing_url("https://www.funda.nl/detail/huur/den-haag/appartement-kikkerstraat-5-a/43123456/?utm=x")
    assert m == {"site": "funda", "id": "43123456", "city": "den-haag", "slug": "kikkerstraat-5-a",
                 "kind": "appartement", "clean": "https://www.funda.nl/detail/huur/den-haag/appartement-kikkerstraat-5-a/43123456"}
    assert match_listing_url("https://www.funda.nl/detail/koop/den-haag/appartement-kikkerstraat-5-a/43123456/") is None
    assert match_listing_url("https://www.funda.nl/zoeken/bewaarde-zoekopdracht/12345678") is None


def test_tracking_hosts():
    assert ea._tracking_host("url2220.pararius.nl") and ea._tracking_host("links.funda.nl")
    for host in ("www.pararius.nl", "pararius.nl", "help.funda.nl", "www.funda.nl", "click.other.test"):
        assert not ea._tracking_host(host)
    assert ea._resolvable_host("url2220.pararius.nl") and ea._resolvable_host("click.other.test")
    assert not ea._resolvable_host("www.pararius.nl") and not ea._resolvable_host("help.funda.nl")


SENDGRID_HTML = """
<html><body>
<a href="https://url2220.pararius.nl/ls/click?upn=LOGO">Pararius</a>
<table><tr><td>
  <a href="https://url2220.pararius.nl/ls/click?upn=AAA"><img src="x.jpg"></a>
  <a href="https://url2220.pararius.nl/ls/click?upn=AAA">Appartement Laan van Meerdervoort</a>
  <p>2511 AB Den Haag</p><p>&euro; 1.850 per maand</p><p>75 m&sup2; &middot; 3 kamers</p>
</td></tr></table>
<a href="https://www.pararius.nl/mijn-account">Account</a>
<a href="https://help.funda.nl/s/">Help</a>
</body></html>"""


def test_site_tracking_subdomain_is_resolved_and_remembered(source):
    target = "https://www.pararius.nl/appartement-te-huur/den-haag/ab12cd34/laan-van-meerdervoort?utm_source=alert"
    answers = {"https://url2220.pararius.nl/ls/click?upn=AAA": target,
               "https://url2220.pararius.nl/ls/click?upn=LOGO": "https://www.pararius.nl/"}
    source._resolver = lambda url: (source.resolved.append(url), answers.get(url))[1]
    # the text part repeats the links: those must not be requested a second time
    text = "Appartement\nhttps://url2220.pararius.nl/ls/click?upn=TEXTCOPY\n"
    source.fake.messages = {b"1": make_msg("Pararius <noreply@pararius.nl>", "Nieuw", html=SENDGRID_HTML, text=text)}
    cache: Dict[str, dict] = {}
    source.attach_link_cache(cache, "2026-11-11")

    out = source.fetch("*", None)
    assert [l.source_id for l in out] == ["pararius-ab12cd34"]
    assert out[0].price_total == 1850 and out[0].area_m2 == 75 and out[0].city == "den-haag"
    assert out[0].url == "https://www.pararius.nl/appartement-te-huur/den-haag/ab12cd34/laan-van-meerdervoort"
    assert sorted(source.resolved) == ["https://url2220.pararius.nl/ls/click?upn=AAA",
                                       "https://url2220.pararius.nl/ls/click?upn=LOGO"]  # once each; never www/help
    assert len(cache) == 2 and "upn" not in repr(cache) and "AAA" not in repr(cache)  # no token is stored

    source.resolved.clear()  # next run, same mail: nothing is requested again
    again = source.fetch("*", None)
    assert [l.source_id for l in again] == ["pararius-ab12cd34"] and again[0].price_total == 1850
    assert source.resolved == []


def test_link_without_redirect_is_retried_a_few_runs_then_dropped(source):
    html = '<div><a href="https://url2220.pararius.nl/ls/click?upn=DEAD">Straat 1</a></div>'
    source.fake.messages = {b"1": make_msg("Pararius <noreply@pararius.nl>", "Nieuw", html=html)}
    source.attach_link_cache({}, "2026-11-11")
    for _ in range(ea.LINK_TRIES + 2):
        assert source.fetch("*", None) == []
    assert len(source.resolved) == ea.LINK_TRIES


def test_link_cache_forgets_old_entries(source):
    cache = {"old": {"to": "", "tries": 3, "at": "2026-10-20"}, "new": {"to": "", "tries": 3, "at": "2026-11-09"},
             "junk": "x"}
    source.attach_link_cache(cache, "2026-11-11")
    assert list(cache) == ["new"] and source.link_cache is cache


# The layout of a real Pararius alert (2026-11): every link is a tracking link behind a bot check, the
# photo is the background of a cell, the street is the first link with text after it.
def _card(uuid, street, line, extra=""):
    return """
<table><tbody><tr><td>
  <table><tbody><tr>
    <td background="https://casco-media-prod.global.ssl.fastly.net/listings/api/media/%s/11111111-2222-3333-4444-555555555555?auto=webp&amp;width=600&amp;sig=abc"
        style="background-image:url('https://casco-media-prod.global.ssl.fastly.net/listings/api/media/%s/11111111-2222-3333-4444-555555555555?auto=webp')">
      <a href="https://url2220.pararius.nl/ls/click?upn=IMG-%s"></a></td>
  </tr><tr><td>
    <div><a href="https://url2220.pararius.nl/ls/click?upn=TITLE-%s">%s</a></div>
    <div>%s</div>%s
  </td></tr></tbody></table>
</td></tr></tbody></table>""" % (uuid, uuid, uuid, uuid, street, line, extra)


PARARIUS_REAL = ("""<html><body>
<div><div>Dear Jan Jansen, <a href="https://url2220.pararius.nl/ls/click?upn=SEARCH">Your search Den Haag+ has new homes in your search</a></div></div>
<table><tbody><tr><td><a href="https://url2220.pararius.nl/ls/click?upn=LOGO">Logo of Pararius+</a></td></tr></tbody></table>"""
    + _card("2e44aefc-b779-5042-84ed-51c41037a529", "Plesmanweg",
            "2597JG Den Haag (Duttendel) &euro;1,550 per month 71 m&sup2; &middot; 3 rooms &middot; Upholstered &middot; Construction year 2021")
    + _card("b1328b18-d00d-5558-8505-febed115b912", "De la Reyweg",
            "2571EE Den Haag (Transvaalkwartier-Zuid) &euro;2,300 per month 110 m&sup2; &middot; 5 rooms &middot; Furnished")
    + _card("4dd80120-aaaa-bbbb-cccc-0123456789ab", "Studio Oude Haagweg", "2552GG Den Haag (Rosenburg) &euro;950 per month 30 m&sup2; &middot; 1 room")
    + _card("99999999-aaaa-bbbb-cccc-0123456789ab", "Herenstraat", "2282BX Rijswijk (Oud Rijswijk) &euro;1,500 per month 80 m&sup2; &middot; 3 rooms")
    + """<table><tbody><tr><td><a href="https://url2220.pararius.nl/ls/click?upn=ALL">View all properties</a></td></tr></tbody></table>
<div><div><a href="https://url2220.pararius.nl/ls/click?upn=UNSUB">change your preferences</a>
<a href="https://url2220.pararius.nl/ls/click?upn=PRIV">Unsubscribe</a></div></div>
<img src="https://url2220.pararius.nl/wf/open?upn=PIXEL"></body></html>""")


def test_pararius_listing_is_built_from_the_photo_id_without_any_request(source):
    text = "Plesmanweg\nhttps://url2220.pararius.nl/ls/click?upn=TEXT\n"
    source.fake.messages = {b"1": make_msg("Pararius <noreply@pararius.nl>", "New homes", html=PARARIUS_REAL, text=text)}
    cache: Dict[str, dict] = {}
    source.attach_link_cache(cache, "2026-11-11")
    out = by_id(source.fetch("*", None))
    assert set(out) == {"pararius-2e44aefc", "pararius-b1328b18", "pararius-4dd80120"}  # Rijswijk is not searched
    p = out["pararius-2e44aefc"]
    assert p.url == "https://www.pararius.nl/appartement-te-huur/den-haag/2e44aefc/plesmanweg"
    assert p.title == "Pararius: Plesmanweg" and p.address == "Plesmanweg" and p.city == "den-haag"
    assert p.price_total == 1550 and p.area_m2 == 71 and p.rooms == 3 and p.posted_at == "2026-11-09T09:15:00Z"
    h = out["pararius-b1328b18"]
    assert h.url.endswith("/den-haag/b1328b18/de-la-reyweg") and h.price_total == 2300 and h.rooms == 5
    assert p.property_type is None  # the mail does not say; "appartement" in the link is only a guess
    assert out["pararius-4dd80120"].property_type == "studio"
    assert out["pararius-4dd80120"].url == "https://www.pararius.nl/studio-te-huur/den-haag/4dd80120/oude-haagweg"
    assert source.resolved == [] and cache == {}  # no tracking link was requested
    assert "Jansen" not in repr([(l.title, l.address, l.url) for l in out.values()])


def test_pararius_card_parser_ignores_mails_without_photos():
    assert ea._pararius_cards(PARARIUS_HTML) == [] and ea._pararius_cards(SENDGRID_HTML) == []
    assert ea._slug("Burgemeester De Monchyplein é 12-A") == "burgemeester-de-monchyplein-e-12-a"


def test_tracking_host_with_bot_check_is_left_alone_for_the_day(source, monkeypatch):
    html = "".join('<div><a href="https://url2220.pararius.nl/ls/click?upn=%d">Straat %d</a></div>' % (i, i) for i in range(5))
    html += '<div><a href="https://click.funda-mail.test/track/aaa111?u=1">Prinsengracht 100 A</a></div>'
    source.fake.messages = {b"1": make_msg("Pararius <noreply@pararius.nl>", "Nieuw", html=html)}
    asked: List[str] = []

    def blocked(url):
        asked.append(url)
        if "pararius" in url:
            source.last_status = "403"
            return None
        source.last_status = "302"
        return "https://www.funda.nl/detail/huur/amsterdam/appartement-prinsengracht-100-a/43201234/"

    source._resolver = blocked
    cache: Dict[str, dict] = {}
    source.attach_link_cache(cache, ea.datetime.now(ea.timezone.utc).strftime("%Y-%m-%d"))
    for _ in range(3):
        assert [l.source_id for l in source.fetch("*", None)] == ["funda-43201234"]
    assert [u for u in asked if "pararius" in u] == ["https://url2220.pararius.nl/ls/click?upn=0"]  # once, not 15 times
    assert len([u for u in asked if "funda" in u]) == 1
