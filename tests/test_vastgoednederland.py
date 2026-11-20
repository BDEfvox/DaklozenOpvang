from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from conftest import make_listing, neutral_config

from hunter.models import Listing
from hunter.sources.base import SourceError, SourcePartial
from hunter.sources.vastgoednederland import SOURCE, _price_cap, apply_details, next_page, parse_page

FIX = Path(__file__).parent / "fixtures"


def _page(name):
    return (FIX / name).read_text()


class FakeHttp:
    """Answers each URL from a dict; remembers the requests."""

    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.pages[url] if isinstance(self.pages, dict) else self.pages


def test_amsterdam_page_skips_the_home_rented_under_reservation():
    out = parse_page(_page("vastgoednederland_amsterdam.html"))
    # 6 cards; Ijdoornlaan 1483c is "Verhuurd onder voorbehoud"
    assert [l.source_id for l in out] == ["675195", "674920", "669748", "654905", "654396"]
    a = out[2]
    assert a.uid == "vastgoednederland:669748" and a.city == "amsterdam"
    assert a.url == "https://www.vastgoednederland.nl/huurwoningen/amsterdam/woning-669748-hiraistraat-5b31"
    assert a.title == "Hiraistraat 5B31, Amsterdam" and a.address == "Hiraistraat 5B31"
    assert a.price_total == 1350 and a.area_m2 == 72
    assert a.bedrooms == 2 and a.rooms is None             # the card's bed icon: the page's "Aantal slaapkamers"
    assert a.image_url.startswith("https://d1zsattj8yq64o.cloudfront.net/media/") and "/424x318_crop." in a.image_url
    assert a.postcode is None and a.property_type is None and a.available_from is None
    assert a.extra == {"broker": "Klok Real Estate B.V."}
    assert out[0].price_total == 1650 and out[0].area_m2 == 30


def test_den_haag_names_are_mapped_and_taken_homes_skipped():
    out = parse_page(_page("vastgoednederland_denhaag.html"))
    assert [l.source_id for l in out] == ["677950", "677251", "676538", "676365"]    # 677282 is under reservation
    assert {l.city for l in out} == {"den-haag"}                                       # "'s-Gravenhage"
    assert out[0].title == "Weimarstraat 295, 's-Gravenhage"
    assert [l.price_total for l in out] == [1895, 1750, 1795, 1023]


def test_other_towns_are_dropped_from_a_mixed_page():
    out = parse_page(_page("vastgoednederland_all.html"))
    assert [(l.source_id, l.city) for l in out] == [("677950", "den-haag"), ("677909", "rotterdam"), ("677882", "amsterdam")]
    assert out[1].address == "'s-Gravendijkwal 122H" and out[1].price_total == 2750


def test_option_label_is_skipped():
    html = _page("vastgoednederland_denhaag.html").replace('<div class="label">Nieuw</div>', '<div class="label">Onder optie</div>')
    assert parse_page(html) == []


def test_honestly_empty_page():
    assert parse_page(_page("vastgoednederland_empty.html")) == []                    # "0 huurwoningen gevonden"


@pytest.mark.parametrize("html", [
    "",
    "<html><body>x</body></html>",
    '<div id="titleWrapper"><h1>Huurwoningen</h1></div>',                              # no count
    '<div id="titleWrapper"><h1>12 huurwoningen gevonden</h1></div><div id="propertiesWrapper"></div>',
])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def test_unrecognised_card_link_raises():
    html = _page("vastgoednederland_amsterdam.html").replace("/woning-675195-", "/object-675195-")
    with pytest.raises(SourceError):
        parse_page(html)


def test_price_cap_uses_the_menu_values_of_the_site():
    assert _price_cap(1600) == 2000 and _price_cap(1500) == 1500 and _price_cap(2400) == 2500 and _price_cap(99999) == 6000


def test_fetch_one_request_when_there_is_no_next_page():
    http = FakeHttp(_page("vastgoednederland_amsterdam.html"))
    out = SOURCE.fetch("amsterdam", http)
    assert len(out) == 5 and len(http.urls) == 1
    assert http.urls[0] == "https://www.vastgoednederland.nl/huurwoningen?q=Amsterdam&huurprijs_tot=2500"
    assert SOURCE.per_city is True and SOURCE.fetch("utrecht", FakeHttp("")) == []


def test_fetch_follows_next_link_up_to_two_pages():
    p1 = _page("vastgoednederland_denhaag.html")
    nxt = next_page(p1)
    assert nxt == "https://www.vastgoednederland.nl/huurwoningen?q=Den%20Haag&huurprijs_tot=2000&p=2"
    # page 2 is the "all" fixture, which also links to a next page: the second page is the last one read
    p23 = _page("vastgoednederland_all.html")
    pages = {
        "https://www.vastgoednederland.nl/huurwoningen?q=Den%20Haag&huurprijs_tot=2500": p1,
        nxt: p23,
        "https://www.vastgoednederland.nl/huurwoningen?p=2": p23,
    }
    http = FakeHttp(pages)
    out = SOURCE.fetch("den-haag", http)
    assert len(http.urls) == 2                                   # never more than 2 pages a city
    # the same home on two pages is returned once
    assert [l.source_id for l in out] == ["677950", "677251", "676538", "676365"]


def test_failure_on_a_later_page_keeps_the_first_page():
    p1 = _page("vastgoednederland_denhaag.html")
    http = FakeHttp({"https://www.vastgoednederland.nl/huurwoningen?q=Den%20Haag&huurprijs_tot=2500": p1,
                     next_page(p1): "<html>geen lijst</html>"})
    with pytest.raises(SourcePartial) as err:
        SOURCE.fetch("den-haag", http)
    assert [l.source_id for l in err.value.listings] == ["677950", "677251", "676538", "676365"]


def test_fetch_stops_without_a_next_link_and_keeps_only_the_city():
    http = FakeHttp(_page("vastgoednederland_all.html").replace('rel="next"', ""))
    assert [l.source_id for l in SOURCE.fetch("rotterdam", http)] == ["677909"]
    assert len(http.urls) == 1


def _detail(sid, **kw):
    listing = Listing("vastgoednederland", sid, "https://www.vastgoednederland.nl/huurwoningen/x/woning-%s-y" % sid, "x",
                      "amsterdam", **kw)
    http = FakeHttp(_page("vastgoednederland_detail_%s.html" % sid))
    out = SOURCE.fetch_details(listing, http)
    assert http.urls == [listing.url]
    return out


def test_detail_bedrooms_rooms_and_costs():
    l = _detail("669748", price_total=1350, area_m2=72)
    assert l.rooms == 3 and l.bedrooms == 2 and l.area_m2 == 72
    assert l.property_type == "apartment" and l.posted_at == "2026-10-14"
    assert l.extra["service_costs"] == 150 and l.extra["service_costs_on_top"] is True    # "€ 150.00 p/m"
    assert l.extra["service_costs_note"] == "Prijs € 1.350,- p/m; Servicekosten € 150.00 p/m"
    assert l.description.startswith("Interesse: graag een e-mail. Niet voor delers.\nWonen in SPOT Amsterdam")
    assert "Bijzonderheden\n•" in l.description or "\n•" in l.description               # the listing's own lines
    assert all(isinstance(v, (str, int, bool, type(None))) for v in l.extra.values())


def test_detail_studio_has_no_bedroom_count():
    l = _detail("675195")
    assert l.rooms == 1 and l.bedrooms is None               # "Aantal kamers 1", no "Aantal slaapkamers" row
    assert l.price_total == 1650 and l.area_m2 == 30         # taken from the page when the card had none
    assert l.extra["service_costs"] == 150
    assert "Preference for students" in l.description


def test_detail_house_without_service_costs():
    l = _detail("677938", price_total=3400)
    assert l.rooms == 5 and l.bedrooms == 4 and l.property_type == "house"
    assert "service_costs" not in l.extra and "service_costs_on_top" not in l.extra
    assert l.description.endswith("(based solely on personal income).")


@pytest.mark.parametrize("cut", [
    lambda h: h.replace('id="textDescription"', 'id="iets"'),
    lambda h: h.replace('id="details"', 'id="x"'),
    lambda h: h.replace("<dl", "<div").replace("</dl>", "</div>"),
])
def test_detail_layout_change_raises(cut):
    with pytest.raises(SourceError):
        SOURCE.fetch_details(Listing("vastgoednederland", "1", "u", "x", "amsterdam"),
                             FakeHttp(cut(_page("vastgoednederland_detail_669748.html"))))


def test_a_rental_home_in_a_new_build_project_is_read_like_any_other():
    # Leiden, 20 Nov 2026: 14 of the 17 cards link to /nieuwbouw/leiden/woning-<id>-... The parser took that for
    # a changed layout and the whole city failed.
    out = parse_page(_page("vastgoednederland_leiden.html"))
    assert [l.source_id for l in out] == ["676946", "675781", "675640", "622875", "622874"]
    new_build = out[3]
    assert new_build.url == "https://www.vastgoednederland.nl/nieuwbouw/leiden/woning-622875-rooseveltstraat-161"
    assert (new_build.city, new_build.address, new_build.price_total, new_build.area_m2) == (
        "leiden", "Rooseveltstraat 161", 1950, 66)
    assert out[1].url.startswith("https://www.vastgoednederland.nl/huurwoningen/leiden/woning-675781")


# ----------------------------------------------------------------------------- the bed icon, a page that is gone
def test_the_cards_bed_icon_is_the_pages_number_of_bedrooms():
    """Checked on seven live homes on 20 Nov 2026; here on the three whose pages are saved."""
    cards = {}
    for name in ("vastgoednederland_amsterdam.html", "vastgoednederland_denhaag.html", "vastgoednederland_leiden.html",
                 "vastgoednederland_all.html"):
        for l in parse_page(_page(name)):
            cards[l.source_id] = l
    assert cards["669748"].bedrooms == 2
    page = apply_details(copy.deepcopy(cards["669748"]), _page("vastgoednederland_detail_669748.html"))
    assert page.bedrooms == 2 and page.rooms == 3
    # a home in Delfgauw (outside the five cities, so not a listing): the card says 4, and so does its page
    card = re.search(r'data-property-id="677938".*?icon-bed"></span>\s*(\d+)', _page("vastgoednederland_all.html"), re.S)
    other = apply_details(make_listing(source="vastgoednederland", bedrooms=None, extra={}),
                          _page("vastgoednederland_detail_677938.html"))
    assert card.group(1) == "4" and other.bedrooms == 4
    # "0" behind the icon is a home whose page has no bedrooms row: not known, never "a studio"
    assert cards["675195"].bedrooms is None
    assert apply_details(copy.deepcopy(cards["675195"]), _page("vastgoednederland_detail_675195.html")).bedrooms is None
    assert all(l.bedrooms is None or 1 <= l.bedrooms <= 10 for l in cards.values())
    assert sum(l.bedrooms is not None for l in cards.values()) >= 10


def test_a_card_without_a_bed_icon_or_with_other_text_has_no_bedroom_count():
    html = _page("vastgoednederland_amsterdam.html")
    n = len(parse_page(html))
    no_icon = html.replace("icon icon-bed", "icon icon-other")
    assert no_icon != html
    got = parse_page(no_icon)
    assert len(got) == n and all(l.bedrooms is None for l in got)
    odd = re.sub(r'(icon-bed"></span>\s*)\d+', r"\1veel", html)
    assert odd != html and all(l.bedrooms is None for l in parse_page(odd))


class _Answers:
    def __init__(self, error=None, page=None):
        self.error, self.page, self.urls = error, page, []

    def get_text(self, url, **kw):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.page


def test_a_home_whose_own_link_answers_not_found_is_taken():
    """Three homes in Delft on 20 Nov 2026: still on the search page, their own pages gone. They were alerted
    as great finds with a dead link and "bedrooms unknown"."""
    from hunter.filters import evaluate
    from hunter.sources.base import SourceBlocked
    src = type(SOURCE)()
    url = "https://www.vastgoednederland.nl/huurwoningen/delft/woning-673926-martinus-nijhofflaan-2n11"
    for code in (404, 410):
        l = make_listing(source="vastgoednederland", source_id="673926", city="delft", url=url, bedrooms=2,
                         price_total=1036, area_m2=45, extra={})
        assert evaluate(l, neutral_config()).matched
        got = src.fetch_details(l, _Answers(SourceError("www.vastgoednederland.nl answered HTTP %d for %s" % (code, url))))
        assert got.extra["taken"] is True and got.extra["site_status"] == "page not found"
        assert not evaluate(got, neutral_config()).matched
    # anything else is a failed read, not a taken home: the error passes on and the card stays as it was
    for exc in (SourceError("www.vastgoednederland.nl answered HTTP 500 for %s" % url),
                SourceError("www.vastgoednederland.nl: timed out"), SourceBlocked("www.vastgoednederland.nl answered 403 (wall)")):
        l = make_listing(source="vastgoednederland", city="delft", url=url, extra={})
        with pytest.raises(type(exc)):
            src.fetch_details(l, _Answers(exc))
        assert "taken" not in l.extra
