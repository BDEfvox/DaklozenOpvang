"""pagereader and the detail readers that fall back on it."""
from __future__ import annotations

from pathlib import Path

import pytest
import requests

from hunter import pagereader
from hunter.http import Http
from hunter.models import Listing
from hunter.sources.base import SourceBlocked, SourceError
from hunter.sources.heimstaden import SOURCE as HEIMSTADEN
from hunter.sources.ikwilhuren import SOURCE as IKWILHUREN
from hunter.sources.rotsvast import SOURCE as ROTSVAST

FIX = Path(__file__).parent / "fixtures"
URL = "https://www.example-rentals.nl/huren/daltonstraat-den-haag-h1025410133/"

CLOUDFLARE = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head>'
              '<body><div id="cf-chl-widget">Verify you are human</div></body></html>')
DATADOME = '<html><head><script src="https://ct.captcha-delivery.com/c.js"></script></head><body></body></html>'
LOGIN = ('<html><head><title>Inloggen</title></head><body><form action="/login">'
         '<input type="email" name="u"><input type="password" name="p"></form></body></html>')


def listing_page(own_url=URL, ld='', meta_desc="Ruim 4-kamer appartement, geen woningdelers."):
    return ('<html><head><title>Daltonstraat, Den Haag</title>'
            f'<meta name="description" content="{meta_desc}">'
            f'<meta property="og:url" content="{own_url}">'
            f'<link rel="canonical" href="{own_url}">{ld}</head>'
            '<body><main><h1>Daltonstraat</h1><aside>Ook interessant: 2 slaapkamers, studenten welkom</aside>'
            '</main></body></html>')


def ld(obj):
    return '<script type="application/ld+json">%s</script>' % obj


# --------------------------------------------------------------------------- walls
@pytest.mark.parametrize("status,page,reason", [
    (200, CLOUDFLARE, "Cloudflare check"),
    (200, DATADOME, "DataDome check"),
    (200, "<html><head><title>Access Denied</title></head></html>", "access denied"),
    (403, "<html>ok</html>", "HTTP 403, access denied"),
    (429, "", "HTTP 429, rate limited"),
    (402, "", "HTTP 402, payment required"),
])
def test_walls_are_recognised(status, page, reason):
    assert pagereader.wall_reason(status, page) == reason


def test_no_wall_on_real_pages_or_server_faults():
    for f in FIX.glob("*.html"):
        assert pagereader.wall_reason(200, f.read_text()) is None, f.name
    assert pagereader.wall_reason(503, "<html>Service unavailable</html>") is None  # left to the caller
    # wall text far down a real page (e.g. in an article) is not a wall
    assert pagereader.wall_reason(200, "<html>" + "x" * 7000 + "verify you are human</html>") is None


def test_http_raises_blocked_with_the_reason(monkeypatch):
    def fake(status, text):
        r = requests.Response()
        r.status_code, r._content, r.encoding = status, text.encode(), "utf-8"
        return r

    http = Http(min_delay=0)
    monkeypatch.setattr(http.session, "request", lambda *a, **kw: fake(200, CLOUDFLARE))
    with pytest.raises(SourceBlocked, match=r"answered 200 \(Cloudflare check\)"):
        http.get_text("https://site.example/x")
    monkeypatch.setattr(http.session, "request", lambda *a, **kw: fake(503, "down"))
    with pytest.raises(SourceError) as e:
        http.get_text("https://site.example/x")
    assert not isinstance(e.value, SourceBlocked)


# --------------------------------------------------------------------------- reading
def test_read_page_collects_metadata_json_ld_and_login_signs():
    page = pagereader.read_page(
        '<html><head><title> Daltonstraat &amp; co </title><meta property="og:description" content="A &amp; B">'
        + ld('{"@context": "https://schema.org", "@graph": [{"@type": "WebSite"}, '
             '{"@type": "Product", "offers": {"@type": "Offer", "price": "1450"}}]}')
        + ld('{not json') + '</head><body><svg><title>icon</title></svg>'
        '<iframe src="https://geo.captcha-delivery.com/x"></iframe><input type="password"></body></html>')
    assert page.title == "Daltonstraat & co"
    assert page.meta["og:description"] == "A & B"
    assert [o.get("@type") for o in page.json_ld] == [None, "WebSite", "Product", "Offer"]  # bad JSON skipped
    assert page.password_field and page.captcha_frame


def test_same_page_by_path_or_listing_number():
    page = pagereader.read_page(listing_page())
    assert pagereader.same_page(page, URL)
    assert pagereader.same_page(page, URL.rstrip("/"))
    assert pagereader.same_page(page, "https://www.example-rentals.nl/huren/new-title-h1025410133/")  # slug edited
    assert not pagereader.same_page(page, "https://www.example-rentals.nl/huren/other-h1025419999/")
    assert not pagereader.same_page(pagereader.read_page("<html><title>x</title></html>"), URL)  # names no URL
    hs = "https://heimstaden.com/nl/huurwoningen/AMSTERDAM-85m²-edb08d5e/"
    assert pagereader.same_page(pagereader.read_page(listing_page(own_url=hs)),
                                "https://heimstaden.com/nl/huurwoningen/AMSTERDAM-85m%C2%B2-edb08d5e/")


def test_own_description_skips_site_blurbs_and_other_pages():
    generic = ld('[{"@type": "WebSite", "description": "Huurwoningen in heel Nederland bij de beste verhuurder van het land"},'
                 ' {"@type": "WebPage", "description": "Wij zijn een professionele verhuurder en beheerder van woningen"}]')
    assert pagereader.own_description(pagereader.read_page(listing_page(ld=generic)), URL) == \
        "Ruim 4-kamer appartement, geen woningdelers."
    full = ld('{"@type": "Apartment", "description": "Ruim 4-kamer appartement<br>met 3 slaapkamers. Geen woningdelers."}')
    assert pagereader.own_description(pagereader.read_page(listing_page(ld=full)), URL) == \
        "Ruim 4-kamer appartement met 3 slaapkamers. Geen woningdelers."
    # a taken-down listing that redirected to a search page: its JSON-LD is about other homes
    search = listing_page(own_url="https://www.example-rentals.nl/huren/?search=Den+Haag", ld=full)
    assert pagereader.own_description(pagereader.read_page(search), URL) == ""


# --------------------------------------------------------------------------- detail readers
def mk(source, url=URL):
    return Listing(source=source, source_id="x", url=url, title="x", city="den-haag")


class FakeHttp:
    def __init__(self, text):
        self.text = text

    def get_text(self, url, **kw):
        return self.text


@pytest.mark.parametrize("source", [ROTSVAST, HEIMSTADEN, IKWILHUREN])
def test_detail_reader_falls_back_to_the_page_summary(source):
    out = source.fetch_details(mk(source.name), FakeHttp(listing_page()))
    assert out.description == "Ruim 4-kamer appartement, geen woningdelers."
    assert out.extra["description_from_summary"] is True
    if source is ROTSVAST:  # its usual reading of the text still runs on the summary
        assert out.rooms == 4 and out.property_type == "apartment"


@pytest.mark.parametrize("source", [ROTSVAST, HEIMSTADEN, IKWILHUREN])
def test_detail_reader_never_returns_a_half_read_page(source):
    with pytest.raises(SourceBlocked):
        source.fetch_details(mk(source.name), FakeHttp(LOGIN))
    with pytest.raises(SourceError, match="layout changed"):
        source.fetch_details(mk(source.name), FakeHttp("<html><body><p>Er ging iets mis</p></body></html>"))
    # the summary of some other page (e.g. a redirect to the search page) is not used
    other = listing_page(own_url="https://www.example-rentals.nl/huren/")
    with pytest.raises(SourceError, match="layout changed"):
        source.fetch_details(mk(source.name), FakeHttp(other))


def test_detail_reader_prefers_its_own_block():
    out = ROTSVAST.fetch_details(mk("rotsvast"), FakeHttp((FIX / "rotsvast_detail.html").read_text()))
    assert out.description and "description_from_summary" not in out.extra
