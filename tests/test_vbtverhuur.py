from __future__ import annotations

from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.sources import vbtverhuur
from hunter.sources.base import SourceBlocked, SourceError, SourcePartial
from hunter.sources.vbtverhuur import SOURCE, apply_details, parse_page

FIX = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIX / name).read_text(encoding="utf-8")


def _mojibake(text):
    """What requests gives for these pages: UTF-8 bytes read as Latin-1."""
    return text.encode("utf-8").decode("latin-1")


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_text(self, url, **kw):
        self.calls.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def test_page_cards_of_the_five_cities():
    out, nxt = parse_page(_read("vbtverhuur_list.html"))
    assert nxt == 2
    # 12 cards: Eindhoven x1, 's-Hertogenbosch x2 and Diemen x2 are dropped
    assert [l.source_id for l in out] == [
        "amsterdam-hiraistraat-6a205", "amsterdam-hiraistraat-9b63", "delft-theodora-eigemanpad-36",
        "amsterdam-fregelaan-32", "amsterdam-wittgensteinlaan-53", "amsterdam-spadinalaan-261",
        "rotterdam-hanoistraat-185"]
    first = out[0]
    assert first.uid == "vbtverhuur:amsterdam-hiraistraat-6a205"
    assert first.url == "https://vbtverhuurmakelaars.nl/woning/amsterdam-hiraistraat-6a205"
    assert first.city == "amsterdam" and first.price_total == 2020 and first.address == "Hiraistraat 6 A205"
    assert first.rooms == 3 and first.area_m2 == 85 and first.property_type == "apartment"
    assert first.available_from == "2026-12-01" and first.bedrooms is None
    assert first.extra["service_costs"] == 125
    assert first.image_url == "https://vbtverhuurmakelaars.nl/images/5c9fc7-w300-s-fwj/hiraistraat-6-a205"
    assert "friends woning" in first.description
    delft = out[2]
    assert delft.city == "delft" and delft.price_total == 1320 and delft.rooms == 3 and delft.area_m2 == 65
    assert delft.available_from == "2026-12-16" and "parkeerplaats een mogelijkheid" in delft.description
    assert out[-1].city == "rotterdam"


def test_mojibake_page_gives_the_same_homes():
    good, _ = parse_page(_read("vbtverhuur_list.html"))
    bad, _ = parse_page(_mojibake(_read("vbtverhuur_list.html")))
    assert [(l.source_id, l.price_total, l.area_m2, l.description) for l in good] == \
           [(l.source_id, l.price_total, l.area_m2, l.description) for l in bad]


def test_taken_and_non_home_cards_are_skipped():
    html = _read("vbtverhuur_list.html")
    rented = html.replace('<span class="status available svelte-16bhc06">\n                Beschikbaar',
                          '<span class="status rented svelte-16bhc06">\n                Verhuurd', 1)
    assert rented != html
    out, _ = parse_page(rented)
    assert "amsterdam-hiraistraat-6a205" in [l.source_id for l in out]  # first card was Eindhoven: not ours anyway
    # make the Delft card a parking place and the Rotterdam card rented
    parking = html.replace("<td>Appartement\n", "<td>Parkeerplaats\n")
    assert parse_page(parking)[0] == []
    html2 = html
    idx = html2.index("rotterdam-hanoistraat-185")
    cut = html2.index('<span class="status available', idx)
    html2 = html2[:cut] + html2[cut:].replace("status available", "status rented", 1).replace("Beschikbaar", "Verhuurd", 1)
    assert "rotterdam-hanoistraat-185" not in [l.source_id for l in parse_page(html2)[0]]


def _numbered(page, here, last):
    """The saved page as page `here` of `last`: the site marks the current page and links the others by number;
    from page 2 on a "Vorige" arrow stands before "Volgende"."""
    start = page.index('<div class="pageNav">')
    end = page.index("</div>", start)
    links = "".join('<a href="/woningen/%d" rel="prefetch"%s>%d</a>' % (i, ' class="currentpage"' if i == here else "", i)
                    for i in range(1, last + 1))
    arrows = ""
    if here > 1:
        arrows += '<a class="shiftpage" href="/woningen/%d" rel="prefetch">Vorige</a>' % (here - 1)
    if here < last:
        arrows += ' <a class="shiftpage" href="/woningen/%d" rel="prefetch">Volgende</a>' % (here + 1)
    return page[:start] + '<div class="pageNav"> ' + arrows.strip() + links + page[end:]


def test_the_next_page_is_read_from_the_page_numbers_not_from_the_first_arrow():
    page = _read("vbtverhuur_list.html")
    assert parse_page(page)[1] == 2
    assert parse_page(_numbered(page, 2, 10))[1] == 3          # "Vorige" comes first there: it once ended the read
    assert parse_page(_numbered(page, 9, 10))[1] == 10
    assert parse_page(_numbered(page, 10, 10))[1] is None
    assert parse_page(_numbered(page, 1, 1))[1] is None


def test_fetch_one_request_per_page_and_city_filter():
    page = _read("vbtverhuur_list.html")
    http = FakeHttp({"https://vbtverhuurmakelaars.nl/woningen": _numbered(page, 1, 2),
                     "https://vbtverhuurmakelaars.nl/woningen/2": _numbered(page, 2, 2)})
    out = SOURCE.fetch("delft", http)
    assert [l.source_id for l in out] == ["delft-theodora-eigemanpad-36"]
    assert http.calls == ["https://vbtverhuurmakelaars.nl/woningen", "https://vbtverhuurmakelaars.nl/woningen/2"]


def test_pagination_stops_at_max_pages():
    page = _read("vbtverhuur_list.html")
    n = vbtverhuur.MAX_PAGES

    pages = {"https://vbtverhuurmakelaars.nl/woningen": _numbered(page, 1, 12)}
    for i in range(2, 13):
        pages["https://vbtverhuurmakelaars.nl/woningen/%d" % i] = _numbered(page, i, 12)
    http = FakeHttp(pages)
    out = SOURCE.fetch("*", http)
    assert len(http.calls) == n
    assert len(out) == 7          # the same page again adds nothing: ids are unique


def test_later_page_failure_keeps_first_page():
    page = _read("vbtverhuur_list.html")
    http = FakeHttp({"https://vbtverhuurmakelaars.nl/woningen": page,
                     "https://vbtverhuurmakelaars.nl/woningen/2": SourceError("boom")})
    with pytest.raises(SourcePartial) as e:
        SOURCE.fetch("*", http)
    assert len(e.value.listings) == 7
    blocked = FakeHttp({"https://vbtverhuurmakelaars.nl/woningen": page,
                        "https://vbtverhuurmakelaars.nl/woningen/2": SourceBlocked("wall")})
    with pytest.raises(SourceBlocked):
        SOURCE.fetch("*", blocked)


def test_layout_change_raises():
    for bad in ("<html><body><main><h1>Oops</h1></main></body></html>", "<html></html>"):
        with pytest.raises(SourceError):
            parse_page(bad)
    html = _read("vbtverhuur_list.html").replace("itemviewer houses", "other")
    with pytest.raises(SourceError):
        parse_page(html)
    with pytest.raises(SourceError):
        SOURCE.fetch("delft", FakeHttp({"https://vbtverhuurmakelaars.nl/woningen": "<html></html>"}))


def test_empty_is_fine():
    html = "<html><body><main><h1>Ons woningaanbod</h1><div></div></main></body></html>"
    assert parse_page(html) == ([], None)


def _card(name):
    out, _ = parse_page(_read("vbtverhuur_list.html"))
    return next(l for l in out if l.source_id == name)


def test_detail_delft():
    l = apply_details(_card("delft-theodora-eigemanpad-36"), _read("vbtverhuur_detail_delft.html"))
    assert l.postcode == "2625DZ" and l.available_from == "2026-12-16"
    assert l.extra["service_costs"] == 60 and l.extra["service_costs_on_top"] is True
    assert l.extra["min_stay_months"] == 12
    assert l.description.startswith("Modern 3-kamerappartement nabij het centrum van Delft\n")
    assert "twee goed ingedeelde slaapkamers" in l.description
    assert "parkeerplaats een mogelijkheid" in l.description
    assert "mandatory_costs" not in l.extra          # optional parking place
    assert "site_sharers_ok" not in l.extra          # no friends text on this one
    assert "\nWoonkamer\nDe lichte woonkamer" in l.description and "Verberg" not in l.description        # the listing's own line breaks are kept


def test_detail_amsterdam_friends_home():
    l = apply_details(_card("amsterdam-hiraistraat-6a205"), _read("vbtverhuur_detail_amsterdam.html"))
    assert l.extra["service_costs"] == 125 and l.available_from == "2026-12-01"
    assert l.description.count("friends woning") >= 1
    assert l.extra["site_sharers_ok"] is True
    assert l.extra["site_facts"].startswith("Toewijzingscriteria: Dit is een friends woning")
    assert len(l.extra["site_facts"]) <= 200
    assert "twee ruime slaapkamers" in l.description


def test_detail_mandatory_parking_with_one_amount():
    html = _read("vbtverhuur_detail_delft.html").replace(
        "het huren van een parkeerplaats een mogelijkheid (€94)",
        "het huren van een parkeerplaats à € 80,- p/m een vereiste.")
    l = apply_details(_card("delft-theodora-eigemanpad-36"), html)
    assert l.extra["mandatory_costs"] == 80 and "vereiste" in l.extra["mandatory_costs_note"]


def test_detail_layout_change_raises():
    with pytest.raises(SourceError):
        apply_details(_card("delft-theodora-eigemanpad-36"), "<html><body><main>nope</main></body></html>")


def test_detail_mojibake():
    l = apply_details(_card("delft-theodora-eigemanpad-36"), _mojibake(_read("vbtverhuur_detail_delft.html")))
    assert l.extra["service_costs"] == 60 and "geïnteresseerd" in l.description


def test_unknown_status_is_kept_taken_ones_are_not():
    html = _read("vbtverhuur_list.html")
    marker = '<span class="status available svelte-16bhc06">\n                Beschikbaar'
    # first card is Eindhoven; use the 2nd (Amsterdam Hiraistraat 6 A205) and 3rd cards
    parts = html.split(marker)
    assert len(parts) == 13
    def with_status(a, b):
        new = ['<span class="status %s svelte-16bhc06">\n                %s' % x for x in ((marker and a), (b))]
        return parts[0] + marker + parts[1] + new[0] + parts[2] + new[1] + parts[3] + marker + marker.join(parts[4:])
    out = parse_page(with_status(("inschrijving", "Inschrijving gestart"), ("rented", "Verhuurd")))[0]
    ids = [l.source_id for l in out]
    assert "amsterdam-hiraistraat-6a205" in ids and "amsterdam-hiraistraat-9b63" not in ids
    kept = next(l for l in out if l.source_id == "amsterdam-hiraistraat-6a205")
    assert kept.extra["status"] == "inschrijving gestart"
    out = parse_page(with_status(("optie", "Onder optie"), ("available", "Beschikbaar")))[0]
    assert "amsterdam-hiraistraat-6a205" not in [l.source_id for l in out]
