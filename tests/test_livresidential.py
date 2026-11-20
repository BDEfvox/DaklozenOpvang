from __future__ import annotations

from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.sources.base import SourceError
from hunter.sources.livresidential import SOURCE, apply_details, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def _fx(name):
    return (FIX / name).read_text()


def test_rotterdam_page_cards_and_exact_fields():
    out = parse_page(_fx("livresidential_rotterdam.html"), "rotterdam")
    assert len(out) == 11 and {l.city for l in out} == {"rotterdam"}
    l = [x for x in out if x.source_id == "karel-doormanstraat-113-3012gd-rotterdam"][0]
    assert l.uid == "livresidential:karel-doormanstraat-113-3012gd-rotterdam"
    assert l.url == "https://livresidential.nl/huurwoningen/rotterdam/rotterdam/karel-doormanstraat-113-3012gd-rotterdam"
    assert (l.price_total, l.rooms, l.area_m2) == (1950, 3, 82)
    assert l.address == "Karel Doormanstraat 113" and l.postcode == "3012GD"
    assert l.bedrooms is None and l.property_type is None      # the card does not say; the page does
    assert l.image_url.startswith("https://cdn.livresidential.nl/")
    # an address with a comma and a postcode only on the second line
    v = [x for x in out if x.source_id == "vierambachtsstraat-28-a-03-rotterdam"][0]
    assert v.address == "Vierambachtsstraat 28-A-03" and v.postcode == "3023AN" and v.price_total == 1425


def test_price_with_thousands_separator_and_small_amounts():
    out = {l.source_id: l for l in parse_page(_fx("livresidential_rotterdam.html"), "rotterdam")}
    assert out["tweede-middellandstraat-12-a1-3021bn-rotterdam"].price_total == 843
    assert out["spoorsingel-70-b1-3033gp-rotterdam"].price_total == 1228


def test_den_haag_card_and_city_mapping():
    out = parse_page(_fx("livresidential_denhaag.html"), "den-haag")
    assert [(l.source_id, l.city, l.price_total, l.rooms, l.area_m2) for l in out] == \
        [("waldorpstraat-479-2521cj-den-haag", "den-haag", 836, 1, 24)]


def test_all_cities_page_keeps_only_the_five_cities():
    out = parse_page(_fx("livresidential_all.html"))
    assert len(out) == 13       # 20 cards: Utrecht, Amstelveen, Alkmaar, Oosterhout, Nuenen are dropped
    assert {l.city for l in out} == {"amsterdam", "rotterdam"}
    # asked for one city, only that city comes back
    assert {l.city for l in parse_page(_fx("livresidential_all.html"), "amsterdam")} == {"amsterdam"}


def test_fetch_uses_one_request_per_city_and_skips_cities_without_a_page():
    http = FakeHttp(_fx("livresidential_rotterdam.html"))
    assert SOURCE.per_city is True
    assert len(SOURCE.fetch("rotterdam", http)) == 11
    assert http.urls == ["https://livresidential.nl/huurwoningen/rotterdam"]
    assert SOURCE.fetch("delft", http) == [] and len(http.urls) == 1


def _studio():
    return parse_page(_fx("livresidential_denhaag.html"), "den-haag")[0]


def test_detail_apartment_fields_and_service_costs():
    l = [x for x in parse_page(_fx("livresidential_rotterdam.html"), "rotterdam")
         if x.source_id == "karel-doormanstraat-113-3012gd-rotterdam"][0]
    http = FakeHttp(_fx("livresidential_detail_apartment.html"))
    l = SOURCE.fetch_details(l, http)
    assert http.urls == [l.url]
    assert (l.property_type, l.bedrooms, l.rooms, l.area_m2, l.price_total) == ("apartment", 2, 3, 82, 1950)
    assert l.available_from == "2026-11-01"
    assert l.extra["service_costs"] == 87 and l.extra["service_costs_on_top"] is True
    assert l.extra["service_costs_note"] == "Servicekosten € 87 per maand"
    assert l.description.startswith("Apartment overview\nAppartement aan Karel Doormanstraat 113")
    assert "taken" not in l.extra


def test_detail_studio_under_option_and_income_limit_text():
    l = apply_details(_studio(), _fx("livresidential_detail_studio_onder_optie.html"))
    assert l.property_type == "studio" and l.bedrooms == 1 and l.rooms == 1
    assert l.extra["taken"] is True and l.extra["site_status"] == "Deze woning is onder optie"
    assert l.description.splitlines()[0] == "Deze woning is onder optie"
    assert "bruto totale huishoudinkomen mag niet hoger zijn dan €50.000" in l.description
    assert l.extra["service_costs"] == 56


@pytest.mark.parametrize("html", ["", "<html><body>x</body></html>", "<html><h3>x</h3></html>"])
def test_unrecognised_page_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def test_detail_without_overview_raises():
    with pytest.raises(SourceError):
        apply_details(_studio(), "<html><body><h2>Kenmerken</h2></body></html>")


def test_honestly_empty_city_page():
    html = ("<html><body><h1>Huurwoningen</h1><div>Prijs Woningtype Stad Aantal kamers</div>"
            "<p>Geen woningen gevonden</p></body></html>")
    assert parse_page(html, "den-haag") == []


def test_page_with_prices_but_no_grid_is_a_layout_change():
    with pytest.raises(SourceError):
        parse_page("<html><body>Woningtype <div>€ 900 per maand</div></body></html>")
