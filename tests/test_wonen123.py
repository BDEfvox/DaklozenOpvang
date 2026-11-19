from __future__ import annotations

from pathlib import Path

import pytest

from hunter.sources.base import SourceError
from hunter.sources.wonen123 import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def test_parse_skips_rented():
    html = (FIX / "wonen123.html").read_text()
    http = FakeHttp(html)
    out = SOURCE.fetch("amsterdam", http)
    assert http.urls == ["https://www.123wonen.nl/huurwoningen/in/amsterdam/sort/newest"]
    assert len(out) == 3  # fourth card is "Verhuurd"
    first = out[0]
    assert first.uid.startswith("123wonen:") and first.source_id == "3632-14"
    assert first.city == "amsterdam" and first.price_total == 2500 and first.bedrooms == 1
    assert first.area_m2 == 69 and first.property_type == "apartment"
    assert first.available_from == "2026-12-01" and first.posted_at == "2026-11-05"
    assert first.url.startswith("https://www.123wonen.nl/huur/amsterdam/")
    assert first.address == "Rokin"


def test_other_town_is_dropped():
    html = (FIX / "wonen123.html").read_text()
    assert parse_page(html, "leiden") == []


def test_zero_results_is_empty_list():
    html = '<div class="mzlistcontrols"><div>Gevonden <i>0</i></div></div>'
    assert parse_page(html, "den-haag") == []


@pytest.mark.parametrize("html", ["<html><body>Welkom</body></html>", "",
                                  '<div>Gevonden <i>5</i></div><p>geen kaarten</p>'])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html, "amsterdam")


# ---------------------------------------------------------------- audit fixes (pages saved 2026-11-19)
def _cards():
    out = parse_page((FIX / "wonen123_cards.html").read_text(), "amsterdam")
    return {l.source_id: l for l in out}


def _detail(sid):
    return (FIX / ("wonen123_detail_%s.html" % sid)).read_text()


def _fetch(sid, card_id=None):
    listing = _cards()[card_id or sid]
    http = FakeHttp(_detail(sid))
    out = SOURCE.fetch_details(listing, http)
    assert http.urls == [listing.url]  # one request per listing
    return out


def test_card_slogan_kamer_te_huur_is_a_room_not_a_penthouse():
    # card 1002-37: spec "Type Penthouse", slogan "Ruime kamer te huur in stijlvol penthouse in Amsterdam"
    cards = _cards()
    assert cards["1002-37"].property_type == "room"
    assert cards["3629-14"].property_type == "apartment" and cards["3620-14"].property_type == "house"


@pytest.mark.parametrize("slogan,expected", [
    ("Ruime kamer te huur in stijlvol penthouse", "room"),
    ("Nog slechts één kamer beschikbaar!", "room"),
    ("Ruim 3-kamerappartement te huur", "apartment"),
    ("Mooi 3 kamer appartement te huur", "apartment"),
    ("Tweekamerappartement", "apartment"),
])
def test_room_wording_is_specific(slogan, expected):
    html = (FIX / "wonen123_cards.html").read_text().replace(
        "Ruime kamer te huur in stijlvol penthouse in Amsterdam", slogan)
    got = {l.source_id: l for l in parse_page(html, "amsterdam")}["1002-37"]
    assert got.property_type == expected


def test_max_huurperiode_becomes_max_stay_months():
    cards = _cards()
    assert cards["3594-14"].extra["max_stay_months"] == 7        # "Max. huurperiode 7 maanden"
    assert cards["3629-14"].extra["max_stay_months"] == 24       # "24 maanden Minimaal 12 maanden"
    # "Minimaal 12 maanden" under the label is a minimum, and "Voor onbepaalde tijd" has no limit
    assert "max_stay_months" not in cards["3637-14"].extra
    assert "max_stay_months" not in cards["1002-37"].extra


def test_negative_furnished_wording_is_not_furnished():
    html = (FIX / "wonen123_cards.html").read_text()
    assert _cards()["1002-37"].furnished == "furnished"
    assert {l.source_id: l for l in parse_page(html.replace("Gemeubileerd", "Ongemeubileerd"), "amsterdam")}[
        "1002-37"].furnished == "unfurnished"
    assert {l.source_id: l for l in parse_page(html.replace("Gemeubileerd", "Gem. mogelijk"), "amsterdam")}[
        "1002-37"].furnished is None


def test_source_now_has_detail_pages():
    assert SOURCE.has_detail_pages is True


def test_details_room_in_shared_penthouse():
    l = _fetch("1002-37")
    assert l.property_type == "room"
    assert l.furnished == "furnished" and l.price_total == 750
    assert l.price_includes_utilities is False          # "Huur: € 750,- per maand (exclusief gas, water, elektra en internet)"
    assert l.extra.get("site_housemates") is True       # "Je deelt de woning met vier andere werkende professionals"
    assert "service_costs" not in l.extra               # the € 100 is a utilities advance, not service costs
    assert "max_stay_months" not in l.extra
    for phrase in ("Nog slechts één kamer beschikbaar", "Geen studenten", "Uitsluitend beschikbaar voor werkende professionals",
                   "Minimale huurperiode van 6 maanden", "Huisdieren zijn niet toegestaan"):
        assert phrase in l.description
    assert not l.description.startswith("Omschrijving Maria") and "Lees meer" not in l.description
    assert l.rooms == 6 and l.bedrooms == 5 and l.area_m2 == 200
    assert 0 < len(l.extra["site_facts"]) <= 200 and "kamer te huur" in l.extra["site_facts"]


def test_details_room_found_in_headline_when_the_card_had_no_slogan():
    listing = _cards()["1002-37"]
    listing.property_type = "apartment"
    html = _detail("1002-37").replace("Ruime kamer te huur in stijlvol penthouse in Amsterdam", "")
    assert SOURCE.fetch_details(listing, FakeHttp(html)).property_type == "room"  # "Nog slechts één kamer beschikbaar!"


def test_details_costs_schaarbeekstraat():
    l = _fetch("3620-14")
    assert l.price_includes_utilities is False          # "€ 2.500,- per maand exclusief nutsvoorzieningen"
    assert l.extra["service_costs"] == 100              # "Servicekosten: €100,- per maand"
    assert l.extra["service_costs_on_top"] is True and "Servicekosten" in l.extra["service_costs_note"]
    assert l.furnished == "furnished"                   # "Oplevering: volledig gemeubileerd"
    assert l.rooms == 4 and l.property_type == "house" and l.bedrooms == 3
    assert "Niet geschikt" not in l.description and "Roken niet toegestaan" in l.description
    assert "site_housemates" not in l.extra and "mandatory_costs" not in l.extra


def test_details_costs_riouwstraat():
    l = _fetch("3629-14")
    assert l.price_includes_utilities is False          # "€ 2.050,- per maand exclusief nutsvoorzieningen"
    assert l.extra["max_stay_months"] == 24             # "Max. huurperiode 24 maanden Minimaal 12 maanden"
    assert l.furnished == "furnished"                   # line "Volledig gemeubileerd"
    assert "service_costs" not in l.extra and l.rooms == 3


def test_details_costs_nida_senffstraat():
    l = _fetch("3637-14")
    assert l.price_includes_utilities is False          # "Huurprijs: €2500 + €150,- voorschot nutsvoorzieningen"
    assert l.furnished == "furnished"                   # "Oplevering: Volledig gemeubileerd"
    assert "max_stay_months" not in l.extra and "service_costs" not in l.extra
    assert "Niet geschikt voor woningdelers" in l.description and l.rooms == 3


def test_details_layout_change_raises():
    listing = _cards()["3629-14"]
    with pytest.raises(SourceError):
        SOURCE.fetch_details(listing, FakeHttp("<html><body>Welkom</body></html>"))
    with pytest.raises(SourceError):  # description block present, specification block gone
        SOURCE.fetch_details(listing, FakeHttp(_detail("3629-14").replace("pand-specs panddetail-desc", "x")))


# ---- later pages: read only when every card on the page before is still for rent ---------------------------------
def _page_of(cards_html, counter, next_page=None):
    nav = '<a href="/huurwoningen/in/amsterdam/page/%d/sort/newest">%d</a>' % (next_page, next_page) if next_page else ""
    return "<html><body><span>Gevonden</span> <i>%d</i>%s%s</body></html>" % (counter, cards_html, nav)


def _card_html(sid, rented=False):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup((FIX / "wonen123_cards.html").read_text(), "html.parser")
    card = next(c for c in soup.select("div.pandlist-container") if "3629-14" in str(c))
    html = str(card).replace("3629-14", sid)
    if rented:
        html = html.replace('class="pandlist-container', 'class="verhuurd pandlist-container', 1)
    return html


class _Pages:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get_text(self, url, **kw):
        self.asked.append(url)
        page = self.pages[len(self.asked) - 1]
        if isinstance(page, Exception):
            raise page
        return page


def test_page_two_is_read_only_when_every_card_on_page_one_is_for_rent():
    from hunter.sources.wonen123 import SOURCE, more_may_follow
    full = _page_of(_card_html("1-1") + _card_html("2-1"), 30, next_page=2)
    mixed = _page_of(_card_html("3-1") + _card_html("9-9", rented=True), 30, next_page=3)
    assert more_may_follow(full, 1) and not more_may_follow(mixed, 2)
    assert not more_may_follow(_page_of(_card_html("1-1"), 1), 1)            # no link to a next page
    http = _Pages([full, mixed, AssertionError("page 3 must not be asked for")])
    got = SOURCE.fetch("amsterdam", http)
    assert [l.source_id for l in got] == ["1-1", "2-1", "3-1"]
    assert http.asked == ["https://www.123wonen.nl/huurwoningen/in/amsterdam/sort/newest",
                          "https://www.123wonen.nl/huurwoningen/in/amsterdam/page/2/sort/newest"]
    # the usual case: rented homes on page 1, so one request
    http = _Pages([mixed])
    assert [l.source_id for l in SOURCE.fetch("amsterdam", http)] == ["3-1"] and len(http.asked) == 1


def test_a_failing_later_page_keeps_what_page_one_gave_and_three_pages_is_the_most():
    from hunter.sources.base import SourceError, SourcePartial
    from hunter.sources.wonen123 import SOURCE
    full = _page_of(_card_html("1-1"), 30, next_page=2)
    with pytest.raises(SourcePartial) as caught:
        SOURCE.fetch("amsterdam", _Pages([full, SourceError("HTTP 503")]))
    assert [l.source_id for l in caught.value.listings] == ["1-1"]
    pages = [_page_of(_card_html("%d-1" % n), 99, next_page=n + 1) for n in (1, 2, 3, 4)]
    http = _Pages(pages)
    assert len(SOURCE.fetch("amsterdam", http)) == 3 and len(http.asked) == 3


@pytest.mark.parametrize("line, amount", [
    ("- € 30.00 per maand servicekosten  inclusief internet", 30),             # 1713-36, a line of a breakdown
    ("Huurprijs: 2.500,- + €10,- servicekosten exclusief nutsvoorzieningen per maand", 10),   # 3632-14
    ("Servicekosten: €100,- per maand", 100),
])
def test_service_costs_wordings_seen_on_19_nov_2026(line, amount):
    from hunter.sources.wonen123 import _SERVICE_BEFORE_RE, _SERVICE_RE
    m = _SERVICE_RE.search(line) or _SERVICE_BEFORE_RE.search(line)
    assert m and int(m.group(1).replace(".", "")) == amount


@pytest.mark.parametrize("line, on_top", [
    ("- € 30.00 per maand servicekosten  inclusief internet", True),           # the service costs include internet
    ("Huurprijs: 2.500,- + €10,- servicekosten exclusief nutsvoorzieningen per maand", True),
    ("Servicekosten: €100,- per maand", True),
    ("Huurprijs: € 1.575,- inclusief € 50,- servicekosten", False),            # the rent already holds them
    ("Waarvan € 45,- servicekosten", False),
    ("(inclusief servicekosten: € 50 per maand)", False),
])
def test_service_costs_the_rent_already_includes_are_not_added(line, on_top):
    from hunter.sources.wonen123 import _SERVICE_BEFORE_RE, _SERVICE_INCLUDED_RE, _SERVICE_RE
    m = _SERVICE_RE.search(line) or _SERVICE_BEFORE_RE.search(line)
    assert m and (not _SERVICE_INCLUDED_RE.search(line[:m.start()])) is on_top
