"""Review fixes for the shared page reader (a byte-identical copy lives in mac-mini-hunter)."""
from __future__ import annotations

from hunter import pagereader

KA = "https://www.kleinanzeigen.de/s-anzeige/mac-mini-m4/3575424258-228-2433"


def page(html):
    return pagereader.read_page(html)


def canonical(url):
    return '<link rel="canonical" href="%s">' % url


def ld(obj_json):
    return '<script type="application/ld+json">%s</script>' % obj_json


def test_overlapping_listing_number_is_another_page():
    longer = page(canonical("https://www.kleinanzeigen.de/s-anzeige/x/1234567-228-1"))
    assert not pagereader.same_page(longer, "https://www.kleinanzeigen.de/s-anzeige/y/123456-228-1")
    shorter = page(canonical("https://www.kleinanzeigen.de/s-anzeige/x/123456-228-1"))
    assert not pagereader.same_page(shorter, "https://www.kleinanzeigen.de/s-anzeige/y/1234567-228-1")


def test_same_number_with_an_edited_title_is_the_same_page():
    edited = page(canonical("https://www.kleinanzeigen.de/s-anzeige/neuer-titel/3575424258-228-2433"))
    assert pagereader.same_page(edited, KA)


def test_relative_and_other_host_urls_resolve_to_the_same_page():
    for own in ("/s-anzeige/mac-mini-m4/3575424258-228-2433", "3575424258-228-2433",
                "https://m.kleinanzeigen.de/s-anzeige/mac-mini-m4/3575424258-228-2433/",
                "//www.kleinanzeigen.de/s-anzeige/mac-mini-m4/3575424258-228-2433?utm=1"):
        assert pagereader.same_page(page(canonical(own)), KA), own
    assert not pagereader.same_page(page(canonical("/s-pcs/mac-mini/k0c228")), KA)
    assert not pagereader.same_page(page("<p>no url of its own</p>"), KA)


def test_json_ld_that_names_another_page_is_left_out():
    other = "Another seller's Mac mini with 24GB. " * 10
    html = (canonical("https://example.com/listing/555555")
            + '<meta name="description" content="My own ad: 16GB, 256GB.">'
            + ld('{"@type":"Product","url":"https://example.com/listing/999999","description":"%s"}' % other))
    text = pagereader.own_description(page(html), "https://example.com/listing/555555")
    assert text == "My own ad: 16GB, 256GB."


def test_children_of_an_object_that_names_another_page_are_left_out():
    html = (canonical("https://example.com/listing/555555")
            + '<meta name="description" content="Own ad.">'
            + ld('{"@type":"Product","mainEntityOfPage":{"@id":"https://example.com/listing/999999"},'
                 '"offers":{"@type":"Offer","description":"Offer text of the other listing, which is much longer."}}'))
    assert pagereader.own_description(page(html), "https://example.com/listing/555555") == "Own ad."


def test_own_json_ld_still_wins_when_it_is_the_longest():
    own = "The full text of this very listing, much longer than the meta description. 16GB, 256GB."
    for attribution in ('"url":"https://example.com/listing/555555",', '"url":"/listing/555555",', ""):
        html = (canonical("https://example.com/listing/555555")
                + '<meta name="description" content="Short.">'
                + ld('{"@type":"Product",%s"description":"%s"}' % (attribution, own)))
        assert pagereader.own_description(page(html), "https://example.com/listing/555555") == own


def test_photo_descriptions_are_kept_whatever_their_url():
    text = "Mac mini M4 2024 16GB RAM, 256 GB SSD, Rechnung vorhanden"
    html = (canonical(KA) + ld('{"@type":"ImageObject","url":"https://img.kleinanzeigen.de/api/v1/x/1.jpg",'
                               '"contentUrl":"https://img.kleinanzeigen.de/api/v1/x/1.jpg","description":"%s"}' % text))
    assert pagereader.own_description(page(html), KA) == text
