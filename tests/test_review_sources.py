from pathlib import Path
from urllib.parse import urlencode

import pytest

from hunter.runner import fetch_source
from hunter.sources.base import SourceBlocked, SourceError
from hunter.sources.directwonen import _listing_url
from hunter.sources.heimstaden import SOURCE as HEIMSTADEN
from hunter.sources.ikwilhuren import SOURCE as MVGM
from hunter.sources.ikwilhuren import parse_page as parse_mvgm_page
from tests.conftest import make_listing
from tests.test_email_alerts import _card as pararius_card
from tests.test_email_alerts import make_msg, source  # noqa: F401 (pytest fixture)
from tests.test_heimstaden import FakeHttp
from tests.test_housinganywhere import page as ha_page

FIXTURES = Path(__file__).parent / "fixtures"


def _pararius_mail(cards):
    return "<html><body>%s</body></html>" % "".join(cards)


@pytest.mark.parametrize("order", ["cheap_first", "dear_first"])
def test_HH04_two_pararius_cards_with_one_title_get_one_rent(source, order):  # noqa: F811
    """R1 HH#4. Two listings both titled 'Plesmanweg' in one Pararius mail."""
    cheap = pararius_card("2e44aefc-b779-5042-84ed-51c41037a529", "Plesmanweg",
                          "2597JG Den Haag (Duttendel) &euro;1,550 per month 71 m&sup2; &middot; 3 rooms")
    dear = pararius_card("c0ffee00-1111-2222-3333-444455556666", "Plesmanweg",
                         "2597JG Den Haag (Duttendel) &euro;2,300 per month 110 m&sup2; &middot; 5 rooms")
    cards = [cheap, dear] if order == "cheap_first" else [dear, cheap]
    source.fake.messages = {b"1": make_msg("Pararius <noreply@pararius.nl>", "New homes", html=_pararius_mail(cards))}
    source.attach_link_cache({}, "2026-11-11")
    out = {l.source_id: l for l in source.fetch("*", None)}
    assert (out["pararius-2e44aefc"].price_total, out["pararius-2e44aefc"].rooms) == (1550, 3)
    assert (out["pararius-c0ffee00"].price_total, out["pararius-c0ffee00"].rooms) == (2300, 5)


def test_pararius_photo_of_a_town_that_is_not_searched_is_never_given_to_a_later_link(source):
    other_town = pararius_card(
        "2e44aefc-b779-5042-84ed-51c41037a529", "Herenstraat",
        "2282BX Rijswijk (Oud Rijswijk) &euro;1,500 per month 3 rooms",
    )
    home_without_photo = """
    <table><tr><td>
      <a href="https://url2220.pararius.nl/ls/click?upn=LISTING">Plesmanweg</a>
      <div>2597JG Den Haag (Duttendel) &euro;1,550 per month 4 rooms</div>
    </td></tr></table>
    """
    source.fake.messages = {b"1": make_msg(
        "Pararius <noreply@pararius.nl>", "New homes", html=_pararius_mail([other_town, home_without_photo]),
    )}
    assert source.fetch("*", None) == []  # the Rijswijk home's id must not be attached to the Den Haag text


def test_pararius_link_without_a_postcode_line_does_not_use_up_the_photo(source):
    card = pararius_card(
        "2e44aefc-b779-5042-84ed-51c41037a529", "Plesmanweg",
        "2597JG Den Haag (Duttendel) &euro;1,550 per month 71 m&sup2; &middot; 3 rooms",
    )
    label = '<div><a href="https://url2220.pararius.nl/ls/click?upn=LABEL">Nieuw aanbod voor jou</a></div>'
    card = card.replace("<div><a href=\"https://url2220.pararius.nl/ls/click?upn=TITLE-", label + "<div><a href=\"https://url2220.pararius.nl/ls/click?upn=TITLE-")
    assert "LABEL" in card
    source.fake.messages = {b"1": make_msg("Pararius <noreply@pararius.nl>", "New homes", html=_pararius_mail([card]))}
    out = source.fetch("*", None)
    assert [(l.source_id, l.city, l.price_total, l.rooms) for l in out] == [("pararius-2e44aefc", "den-haag", 1550, 3)]
    assert "Plesmanweg" in out[0].title


def test_HH06_mvgm_page_two_failure_drops_page_one(cfg):
    """R1 HH#6 / R2 2.8.5b."""
    page1 = (FIXTURES / "ikwilhuren.html").read_text()

    class Http:
        def get_text(self, url, **kw):
            if "page=2" in url:
                raise SourceError("timeout")
            return page1

    res = fetch_source(MVGM, cfg, Http())
    assert res.status == "partial" and len(res.listings) > 0, (res.status, res.error)


@pytest.mark.parametrize("error_type", [SourceError, SourceBlocked])
def test_mvgm_first_page_raises_original_error(error_type):
    error = error_type("first request failed")

    class Http:
        def get_text(self, url, **kw):
            raise error

    with pytest.raises(error_type) as caught:
        MVGM.fetch("*", Http())
    assert caught.value is error


@pytest.mark.parametrize(("label", "property_type"), [
    ("Woning", "house"),
    ("Vrijstaande woning", "house"),
    ("Twee-onder-een-kapwoning", "house"),
    ("Geschakelde woning", "house"),
    ("Bungalow", "house"),
    ("Flat", "apartment"),
    ("Portiekflat", "apartment"),
    ("Galerijflat", "apartment"),
    ("Kamer", "room"),
    ("Studio", "studio"),
    ("Parkeerplaats", "parking"),
    ("Garage", "parking"),
    ("Bedrijfsruimte", "other"),
])
def test_mvgm_type_comes_from_title_prefix(label, property_type):
    html = f"""
    <div class="card-woning">
      <a class="stretched-link" href="/object/test-0123456789abcdef0123456789abcdef">
        {label} Villastraat 12
      </a>
      <div class="card-body"><span>2511 AB Den Haag</span></div>
    </div>
    """
    listing, = parse_mvgm_page(html)
    assert (listing.property_type, listing.address) == (property_type, "Villastraat 12")


def test_mvgm_title_without_a_type_word_is_an_address_not_a_type():
    # "Schipholweg 386" (19 Nov 2026): no type in front. The first word is the street; the detail page gives the type.
    html = """
    <div class="card-woning">
      <a class="stretched-link" href="/object/test-0123456789abcdef0123456789abcdef">Schipholweg 386</a>
      <div class="card-body"><span>2316 XD Leiden</span></div>
    </div>
    """
    listing, = parse_mvgm_page(html)
    assert (listing.property_type, listing.address) == (None, "Schipholweg 386")


def test_HH09_housinganywhere_unit_type_path_only(cfg):
    """R1 HH#9. parse_listing accepts unitTypePath; parse_search then rejects the whole page."""
    from hunter.sources.housinganywhere import parse_search
    item = {"unitTypePath": "/room/ut1234567/nl/Den-Haag/x", "city": "Den Haag", "propertyType": "APARTMENT",
            "priceEUR": 1400, "street": "Teststraat"}
    assert len(parse_search(ha_page([item]), "den-haag")) == 1


def test_R2_2_8_email_price_after_two_spaces():
    from hunter.sources.email_alerts import _extract_facts
    assert _extract_facts("€  1.550 per maand")["price"] == 1550


def test_R2_2_8_email_first_out_of_range_amount_hides_the_rent():
    from hunter.sources.email_alerts import _extract_facts
    assert _extract_facts("€ 100 korting op de eerste maand. € 1.550 per maand")["price"] == 1550


@pytest.mark.parametrize(("description", "before", "after"), [
    ("De woning heeft 2 slaapkamers.", None, 2),
    ("De woning heeft twee slaapkamers.", None, 2),
    ("Appartement met 1 slaapkamer.", None, 1),
    ("De woning is per direct beschikbaar.", 4, 4),              # nothing said: a known value stays
    ("De woningen hebben 1 tot 3 slaapkamers.", None, None),     # a range is not one home's count
    ("Keuze uit 2-3 slaapkamers.", None, None),
    ("Met 2 of 3 slaapkamers.", None, None),
    ("Een slaapkamer aan de voorzijde en een slaapkamer aan de achterzijde.", None, None),  # "een" = article
])
def test_heimstaden_bedroom_count(description, before, after):
    listing = make_listing(source="heimstaden", bedrooms=before)
    details = HEIMSTADEN.fetch_details(listing, FakeHttp(f'<div class="project-intro">{description}</div>'))
    assert details.bedrooms == after


@pytest.mark.parametrize(("return_url", "expected"), [
    ("/huurwoningen-huren/den-haag/woning-123", "https://directwonen.nl/huurwoningen-huren/den-haag/woning-123"),
    ("https://directwonen.nl/huurwoningen-huren/den-haag/woning-123",
     "https://directwonen.nl/huurwoningen-huren/den-haag/woning-123"),
])
def test_directwonen_payment_link_resolves_return_url(return_url, expected):
    query = urlencode({"returnUrl": return_url})
    assert _listing_url(f"/premiumaccountpayment?{query}") == expected
