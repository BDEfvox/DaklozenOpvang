"""What every source shares: text read in the right encoding, how far a long list is read, and a home that its
own page calls taken."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import requests

import hunter.sources.base as base
from conftest import FakeNotifier, FakeSource, make_listing, neutral_config
from hunter.filters import evaluate
from hunter.http import Http
from hunter.runner import run_once
from hunter.sources import all_sources
from hunter.sources.base import Source, hourly_deep_read
from hunter.state import State

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 20, 12, 30, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------- encoding
def _response(body: bytes, content_type: str) -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r._content = body
    r.headers["content-type"] = content_type
    r.encoding = requests.utils.get_encoding_from_headers(r.headers)
    return r


def _get(monkeypatch, body: bytes, content_type: str) -> str:
    http = Http(min_delay=0)
    monkeypatch.setattr(http.session, "request", lambda *a, **kw: _response(body, content_type))
    return http.get_text("https://example.com/x")


def test_a_page_without_a_charset_is_read_as_utf8(monkeypatch):
    # requests reads text/html without a charset as Latin-1: "€ 1.250" became "â\x82¬ 1.250" on two new sites
    text = "<p>Huurprijs € 1.250 per maand, café om de hoek</p>"
    assert _get(monkeypatch, text.encode("utf-8"), "text/html") == text


def test_a_page_that_names_its_charset_or_is_really_latin1_is_left_alone(monkeypatch):
    text = "<p>Huurprijs 1.250 per maand, café om de hoek</p>"
    assert _get(monkeypatch, text.encode("latin-1"), "text/html") == text                   # not valid UTF-8
    assert _get(monkeypatch, text.encode("latin-1"), "text/html; charset=ISO-8859-1") == text
    assert _get(monkeypatch, "€".encode("utf-8"), "text/html; charset=utf-8") == "€"
    assert _get(monkeypatch, "€ 5".encode("utf-8"), "application/json") == "€ 5"


def test_the_sources_own_repair_leaves_correct_text_alone():
    # vbtverhuur and rotterdamrentalservice undo the Latin-1 misreading themselves; with the client fixed they
    # get correct text, which must pass through untouched (also text with "â" or "Ã" of its own)
    from hunter.sources import vbtverhuur
    for text in ("Huur € 1.250, château, São Paulo", "Ã la carte â‚¬", "plain"):
        assert vbtverhuur._fix(text) == text or "€" in vbtverhuur._fix(text)
    assert vbtverhuur._fix("Huur € 1.250, café".encode("utf-8").decode("latin-1")) == "Huur € 1.250, café"
    assert vbtverhuur._fix("Huur € 1.250, château") == "Huur € 1.250, château"


# ----------------------------------------------------------------------------- how far a long list is read
def test_a_long_list_is_read_to_its_end_on_the_first_run_and_once_an_hour(monkeypatch):
    src = Source()
    assert src.page_limit(3, 14) == 3                                    # an ordinary run (see conftest)
    src.baseline = True
    assert src.page_limit(3, 14) == 14                                   # the source's first run
    src.baseline = False
    monkeypatch.setattr(base, "hourly_deep_read", lambda now=None: True)
    assert src.page_limit(3, 14) == 14                                   # the run in the first ten minutes of an hour
    assert hourly_deep_read(datetime(2026, 11, 20, 9, 4, tzinfo=timezone.utc)) is True
    assert hourly_deep_read(datetime(2026, 11, 20, 9, 10, tzinfo=timezone.utc)) is False
    assert hourly_deep_read(datetime(2026, 11, 20, 9, 59, tzinfo=timezone.utc)) is False


class _Pages:
    def __init__(self, page):
        self.page, self.urls = page, []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.page


def test_vbtverhuur_reads_three_pages_on_an_ordinary_run_and_on_to_the_end_on_a_deep_one(monkeypatch):
    from hunter.sources.vbtverhuur import DEEP_PAGES, MAX_PAGES, VbtVerhuur
    page = (FIX / "vbtverhuur_list.html").read_text(encoding="utf-8")
    # a deep run follows the page navigation as far as it leads, and never past DEEP_PAGES
    import hunter.sources.vbtverhuur as mod
    numbers = iter(range(2, 100))
    monkeypatch.setattr(mod, "parse_page", lambda html: ([], next(numbers)))
    src, http = VbtVerhuur(), _Pages(page)
    src.baseline = True
    src.fetch("*", http)
    assert len(http.urls) == DEEP_PAGES and http.urls[-1].endswith("/woningen/%d" % DEEP_PAGES)
    numbers = iter(range(2, 100))
    src, http = VbtVerhuur(), _Pages(page)
    src.fetch("*", http)
    assert len(http.urls) == MAX_PAGES


def test_the_runner_tells_a_source_that_it_has_never_returned_anything(tmp_path):
    cfg = neutral_config()
    seen = []

    class Remembers(FakeSource):
        def fetch(self, city, http):
            seen.append(self.baseline)
            return super().fetch(city, http)

    def run(src):
        st = State(str(tmp_path / "data"), cfg)
        run_once(cfg, st, {"fake": src}, FakeNotifier(), http=None, now=NOW, docs_dir=str(tmp_path / "docs"))

    run(Remembers("fake", [make_listing("1")]))
    assert seen and all(seen)                                            # nothing known yet: the baseline run
    del seen[:]
    run(Remembers("fake", [make_listing("1")]))
    assert seen and not any(seen)


# ----------------------------------------------------------------------------- a home that is taken
def test_a_home_its_own_page_calls_taken_is_no_match():
    cfg = neutral_config()
    assert evaluate(make_listing(), cfg).matched
    assert not evaluate(make_listing(extra={"taken": True}), cfg).matched
    assert evaluate(make_listing(extra={"taken": False}), cfg).matched
    assert evaluate(make_listing(extra={"taken": "ja"}), cfg).matched    # only the source's own True counts


def test_liv_home_under_option_is_dropped_after_its_page_is_read():
    from hunter.sources.livresidential import apply_details
    l = make_listing(source="livresidential", bedrooms=None, price_total=900, area_m2=60,
                     url="https://livresidential.nl/huurwoningen/den-haag/den-haag/waldorpstraat-1-2521cb-den-haag")
    apply_details(l, (FIX / "livresidential_detail_studio_onder_optie.html").read_text(encoding="utf-8"))
    assert l.extra["taken"] is True and not evaluate(l, neutral_config()).matched


# ----------------------------------------------------------------------------- all sources
NEW = ("livresidential", "nederwoon", "rivarentals", "rotterdamrentalservice", "vastgoednederland", "vbtverhuur",
       "viadaan", "vva")


def test_the_new_sources_are_found_and_named():
    sources = all_sources()
    cities = set(neutral_config()["cities"])
    for name in NEW:
        src = sources[name]
        assert src.name == name and src.label and src.homepage.startswith("https://"), name
        assert src.has_detail_pages and set(src.cities) <= cities and src.cities, name
        assert src.baseline is False, name


def test_a_home_found_taken_when_its_page_is_read_is_never_alerted_and_gets_no_card(tmp_path):
    """Run level: on a source's first run and on a later one, with the page read again every time the home is
    still shown (it may come back)."""
    import json
    cfg = neutral_config()

    class Gone(FakeSource):
        has_detail_pages = True

        def fetch_details(self, listing, http):
            self.detail_calls += 1
            if listing.source_id.startswith("gone"):
                listing.extra["taken"] = True
            return listing

    def run(src, when):
        st = State(str(tmp_path / "data"), cfg)
        n = FakeNotifier()
        run_once(cfg, st, {"fake": src}, n, http=None, now=when, docs_dir=str(tmp_path / "docs"))
        cards = json.loads((tmp_path / "docs" / "data.json").read_text())["listings"]
        return n.messages, [c["uid"] for c in cards]

    first = [make_listing("gone1", title="Weg 1"), make_listing("open1", title="Open 1")]
    msgs, cards = run(Gone("fake", first), NOW)
    assert not any("Weg 1" in m for m in msgs) and any("Open 1" in m for m in msgs)
    assert cards == ["fake:open1"]
    src = Gone("fake", first + [make_listing("gone2", title="Weg 2"), make_listing("open2", title="Open 2")])
    msgs, cards = run(src, NOW.replace(hour=13))
    assert not any("Weg" in m for m in msgs) and any("Open 2" in m for m in msgs)
    assert sorted(cards) == ["fake:open1", "fake:open2"]


# ----------------------------------------------------------------------------- "taken" is not for ever
class _Page(FakeSource):
    """A source whose pages say what `says[source_id]` holds: "taken", "open" or an exception. Its listings are
    made afresh for every run, as a real source's are."""
    has_detail_pages = True

    def __init__(self, ids, says, **facts):
        super().__init__("fake", [make_listing(i, title="Huis %s" % i, **facts.get(i, {})) for i in ids])
        self.says = says
        self.read = []

    def fetch_details(self, listing, http):
        self.read.append(listing.source_id)
        word = self.says.get(listing.source_id, "open")
        if isinstance(word, Exception):
            raise word
        if word == "taken":
            listing.extra["taken"] = True
        return listing


def _runs(tmp_path, cfg):
    import json
    from datetime import timedelta

    def run(src, hours):
        st = State(str(tmp_path / "data"), cfg)
        n = FakeNotifier()
        run_once(cfg, st, {"fake": src}, n, http=None, now=NOW + timedelta(hours=hours), docs_dir=str(tmp_path / "docs"))
        cards = json.loads((tmp_path / "docs" / "data.json").read_text())["listings"]
        seen = json.loads((tmp_path / "data" / "seen.json").read_text())
        return [m for m in n.messages if "Huis " in m], sorted(c["uid"] for c in cards), seen

    return run


def test_a_taken_home_is_looked_at_again_and_announced_when_it_is_on_offer_again(tmp_path):
    from hunter.sources.base import SourceError
    run = _runs(tmp_path, neutral_config())
    run(_Page(["old"], {}), 0)                                            # the source's first run
    ids, says = ["old", "h1"], {"h1": "taken"}
    src = _Page(ids, says)
    msgs, cards, seen = run(src, 1)
    assert src.read == ["h1"] and not msgs and cards == ["fake:old"]
    assert seen["fake:h1"]["taken_checked"] and "taken_tries" not in seen["fake:h1"]

    src = _Page(ids, says)                                                # 5 hours later: too soon to look again
    msgs, cards, _ = run(src, 6)
    assert src.read == [] and not msgs

    src = _Page(ids, says)                                                # 6 hours after the first look: still taken
    msgs, cards, seen = run(src, 7.1)
    assert src.read == ["h1"] and not msgs and cards == ["fake:old"] and seen["fake:h1"]["taken_tries"] == 1

    src = _Page(ids, says)                                                # the second wait is 12 hours
    assert run(src, 14)[0] == [] and src.read == []

    says["h1"] = SourceError("fake.example answered HTTP 500")            # a look that fails: nothing is announced
    src = _Page(ids, says)
    msgs, cards, seen = run(src, 19.2)
    assert src.read == ["h1"] and not msgs and cards == ["fake:old"]
    assert seen["fake:h1"]["taken_tries"] == 2 and seen["fake:h1"]["details"]["extra"]["taken"] is True
    assert seen["fake:h1"]["matched"] == []

    says["h1"] = "open"                                                   # a day later the option has lapsed
    src = _Page(ids, says)
    msgs, cards, seen = run(src, 44)
    assert src.read == ["h1"] and len(msgs) == 1 and "Huis h1" in msgs[0]
    assert cards == ["fake:h1", "fake:old"]
    assert "taken_checked" not in seen["fake:h1"] and "taken_tries" not in seen["fake:h1"]
    assert "taken" not in seen["fake:h1"]["details"].get("extra", {})

    src = _Page(ids, says)                                                # and it is announced once
    msgs, cards, _ = run(src, 45)
    assert not msgs and cards == ["fake:h1", "fake:old"]


def test_a_taken_home_is_given_up_after_ten_looks_and_never_read_without_need(tmp_path):
    import hunter.runner as runner
    run = _runs(tmp_path, neutral_config())
    run(_Page(["old"], {}), 0)
    # one that fits and one that would not fit anyway (too expensive): only the first is worth a request
    ids, says = ["old", "h1", "dear"], {"h1": "taken", "dear": "taken"}
    facts = {"dear": {"price_total": 9000}}
    first = _Page(ids, says, **facts)
    run(first, 1)
    assert first.read == ["h1"]                    # "dear" never fitted: its page was never read at all
    looks, hours = 0, 1
    for _ in range(40):
        hours += 25
        src = _Page(ids, says, **facts)
        msgs, _, seen = run(src, hours)
        assert not msgs and "dear" not in src.read
        looks += len(src.read)
    assert looks == runner.TAKEN_MAX_TRIES and seen["fake:h1"]["taken_tries"] == runner.TAKEN_MAX_TRIES


def test_a_look_at_a_taken_home_waits_when_the_run_has_no_page_reads_left(tmp_path):
    cfg = neutral_config()
    cfg["detail_fetch_max_per_run"] = 1
    run = _runs(tmp_path, cfg)
    run(_Page(["old"], {}), 0)
    says = {"h1": "taken"}
    run(_Page(["old", "h1"], says), 1)
    says["h1"] = "open"
    src = _Page(["old", "h1", "new"], says)                               # a new home takes the run's only read
    msgs, cards, seen = run(src, 10)
    assert src.read == ["new"] and len(msgs) == 1 and "Huis new" in msgs[0] and "fake:h1" not in cards
    assert "taken_tries" not in seen["fake:h1"]                           # no look, no try
    src = _Page(["old", "h1", "new"], says)
    msgs, cards, _ = run(src, 10.2)
    assert src.read == ["h1"] and len(msgs) == 1 and "Huis h1" in msgs[0] and "fake:h1" in cards


# ----------------------------------------------------------------------------- a first run announces checked homes only
def test_a_first_run_does_not_announce_homes_whose_page_it_could_not_read(tmp_path):
    cfg = neutral_config()
    cfg["first_run_detail_max"] = 3
    run = _runs(tmp_path, cfg)
    ids = ["h%d" % i for i in range(1, 9)]
    src = _Page(ids, {"h1": "taken", "h2": "taken"}, **{i: {"price_total": 1000 + int(i[1:])} for i in ids})
    msgs, cards, _ = run(src, 0)
    assert src.read == ["h1", "h2", "h3"]                                 # the budget: three pages
    assert len(msgs) == 1 and "Huis h3" in msgs[0]                        # h4..h8 were not read: not announced
    assert cards == ["fake:h%d" % i for i in range(3, 9)]                 # but they are on the dashboard
    # a source without pages of its own needs no read, so nothing holds its homes back
    plain = FakeSource("plain", [make_listing("p%d" % i, source="plain", title="Feed %d" % i, address="Feedweg %d" % i)
                                 for i in range(1, 4)])
    st = State(str(tmp_path / "data2"), cfg)
    n = FakeNotifier()
    run_once(cfg, st, {"plain": plain}, n, http=None, now=NOW, docs_dir=str(tmp_path / "docs2"))
    assert sum("Feed" in m for m in n.messages) >= 3


def test_a_home_on_offer_again_is_not_announced_when_another_site_already_showed_it(tmp_path):
    """Site aa calls the home taken; site bb then lists the same home and it is announced. When aa's page shows
    it on offer again, that is the same home: no second alert, no second card."""
    import json
    from datetime import timedelta
    cfg = neutral_config()

    class Site(FakeSource):
        has_detail_pages = True

        def __init__(self, name, ids, says):
            super().__init__(name, [make_listing(i, source=name, title="Huis %s" % i, address="Laan 5",
                                                 postcode="2511AB") if i != "old" else
                                    make_listing(i, source=name, title="Oud %s" % name, address="Oudeweg %d" % len(name),
                                                 price_total=1300 + len(name))
                                    for i in ids])
            self.says, self.read = says, []

        def fetch_details(self, listing, http):
            self.read.append(listing.source_id)
            if self.says.get(listing.source_id) == "taken":
                listing.extra["taken"] = True
            return listing

    def run(sources, hours):
        st = State(str(tmp_path / "data"), cfg)
        n = FakeNotifier()
        run_once(cfg, st, sources, n, http=None, now=NOW + timedelta(hours=hours), docs_dir=str(tmp_path / "docs"))
        cards = json.loads((tmp_path / "docs" / "data.json").read_text())["listings"]
        return [m for m in n.messages if "Huis " in m], sorted(c["uid"] for c in cards)

    says = {"x": "taken"}
    run({"aa": Site("aa", ["old"], says), "bbb": Site("bbb", ["old"], {})}, 0)          # both sites' first runs
    msgs, cards = run({"aa": Site("aa", ["old", "x"], says), "bbb": Site("bbb", ["old"], {})}, 1)
    assert not msgs and "aa:x" not in cards
    msgs, cards = run({"aa": Site("aa", ["old", "x"], says), "bbb": Site("bbb", ["old", "y"], {})}, 2)
    assert len(msgs) == 1 and "Huis y" in msgs[0] and "bbb:y" in cards
    says["x"] = "open"
    aa = Site("aa", ["old", "x"], says)
    msgs, cards = run({"aa": aa, "bbb": Site("bbb", ["old", "y"], {})}, 8)
    assert aa.read == ["x"]                                                              # it was looked at again
    assert not msgs and "aa:x" not in cards and "bbb:y" in cards
