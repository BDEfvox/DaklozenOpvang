"use strict";

var assert = require("assert");
var fs = require("fs");
var path = require("path");
var vm = require("vm");

var script = fs.readFileSync(path.join(__dirname, "../docs/app.js"), "utf8");
var now = Date.parse("2026-01-01T12:00:00Z");
var old = "2026-01-01T09:00:00Z";
var recent = "2026-01-01T11:00:00Z";
var fresh = "2026-01-01T11:58:00Z";
var refreshInterval = 5 * 60000;

function EventTarget() { this.listeners = {}; }
EventTarget.prototype.addEventListener = function (type, callback) {
  (this.listeners[type] || (this.listeners[type] = [])).push(callback);
};
EventTarget.prototype.fire = function (type) {
  (this.listeners[type] || []).forEach(function (callback) { callback(); });
};

function Element(tag) {
  EventTarget.call(this);
  this.tagName = tag;
  this.children = [];
  this.attributes = {};
  this.hidden = true;
  this.text = "";
  this.style = {};
}
Element.prototype = Object.create(EventTarget.prototype);
Element.prototype.appendChild = function (child) { this.children.push(child); };
Element.prototype.setAttribute = function (name, value) { this.attributes[name] = String(value); };
Element.prototype.removeAttribute = function (name) { delete this.attributes[name]; };
Element.prototype.blur = function () {};
Object.defineProperty(Element.prototype, "textContent", {
  get: function () {
    return this.text + this.children.map(function (child) { return child.textContent; }).join("");
  },
  set: function (value) { this.text = String(value); this.children = []; }
});

function match(persons, price, tier) {
  return { persons: persons, price_pp: price, tier: tier || "ok" };
}

function home(title, matches, firstSeen) {
  return {
    title: title, city: "utrecht", matches: matches, first_seen: firstSeen || recent,
    best_tier: matches.some(function (m) { return m.tier === "great"; }) ? "great" : "ok"
  };
}

function payload(listings, lastRun, source, cities) {
  return {
    listings: listings, last_run: lastRun || recent, cities: cities || { utrecht: "Utrecht" },
    sources: [{ label: source || "Original source", status: "ok" }],
    reply_template: "Interested in {address}."
  };
}

function settle() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}

// `saved` is the filter state left in localStorage by an earlier visit.
function dashboard(responses, saved, storage, extra) {
  var elements = {}, intervals = {}, timeouts = [], calls = [], copied = [], stored = {};
  if (saved) stored["househunter.filters.v1"] = JSON.stringify(saved);
  Object.keys(storage || {}).forEach(function (key) { stored[key] = storage[key]; });
  var clock = now;
  var document = new EventTarget();
  document.hidden = false;
  document.visibilityState = "visible";
  document.getElementById = function (id) {
    return elements[id] || (elements[id] = new Element(id));
  };
  document.createElement = function (tag) { return new Element(tag); };
  document.createElementNS = function (namespace, tag) { return new Element(tag); };
  // Use a separate Date constructor so changing this clock cannot affect another check.
  function TestDate(value) { return new Date(value); }
  TestDate.parse = Date.parse;
  TestDate.now = function () { return clock; };
  var window = {
    document: document,
    Date: TestDate,
    URL: URL,
    localStorage: {
      getItem: function (key) { return stored[key]; },
      setItem: function (key, value) { stored[key] = value; },
      removeItem: function (key) { delete stored[key]; }
    },
    navigator: { clipboard: { writeText: function (value) {
      copied.push(value);
      return Promise.resolve();
    } } },
    fetch: function (url, options) {
      var next = responses.shift();
      calls.push(url);
      assert.strictEqual(options.cache, "no-store");
      assert.ok(next, "Unexpected fetch for " + url);
      assert.strictEqual(url, next[0]);
      return Promise.resolve(next[1]).then(function (body) {
        return { ok: body !== null, status: 503, json: function () { return Promise.resolve(body); } };
      });
    },
    setInterval: function (callback, delay) { intervals[delay] = callback; },
    setTimeout: function (callback) { timeouts.push(callback); return timeouts.length; },
    clearTimeout: function (id) { if (id) timeouts[id - 1] = function () {}; }
  };
  Object.keys(extra || {}).forEach(function (key) { window[key] = extra[key]; });   // e.g. a fake Leaflet as window.L
  window.window = window;
  vm.runInNewContext(script, window, { filename: "docs/app.js" });
  return {
    get: document.getElementById,
    calls: calls,
    copied: copied,
    stored: stored,
    timeouts: timeouts,
    advance: function (ms) { clock += ms; },
    tick: function (delay) {
      assert.ok(intervals[delay], "Missing interval for " + delay + " ms");
      intervals[delay]();
      return settle();
    },
    visibility: function (state) {
      document.visibilityState = state;
      document.hidden = state === "hidden";
      document.fire("visibilitychange");
      return settle();
    },
    change: function (id, value, event) {
      var control = document.getElementById(id);
      control.value = value;
      control.fire(event || "change");
    },
    // Group size is a set of radio buttons: group-2, group-3 and group-any.
    group: function (value) {
      ["any", "2", "3"].forEach(function (g) { document.getElementById("group-" + g).checked = g === value; });
      document.getElementById("group-" + value).fire("change");
    },
    // A "listing says" filter: press its Only or Hide button (pressing the one that is on turns it off).
    attr: function (key, mode) { attrButton(document.getElementById, key, mode).fire("click"); },
    // The filters in use above the list, as text; `remove(i)` presses the i-th chip.
    used: function () { return chips(document.getElementById).map(function (b) { return b.textContent; }); },
    remove: function (i) { chips(document.getElementById)[i].fire("click"); },
    click: function (id) { document.getElementById(id).fire("click"); },
    // List | Map
    mode: function (value) {
      ["list", "map"].forEach(function (v) { document.getElementById("view-" + v).checked = v === value; });
      document.getElementById("view-" + value).fire("change");
    }
  };
}

function attrButton(get, key, mode) {
  var found = descendants(get("attr-sections"), function (node) {
    return node.attributes["data-attr"] === key && node.attributes["data-mode"] === mode;
  });
  assert.strictEqual(found.length, 1, "one " + mode + " button for " + key);
  return found[0];
}
function pressed(view, key, mode) { return attrButton(view.get, key, mode).attributes["aria-pressed"] === "true"; }
function chips(get) {
  return get("applied").children.filter(function (node) { return String(node.className).indexOf("chip") >= 0; });
}
// The rows of "listing says" filters that are on screen: [label, count].
function attrRowsShown(view) {
  var out = [];
  view.get("attr-sections").children.forEach(function (set) {
    if (set.hidden) return;
    set.children[1].children.forEach(function (li) {
      if (!li.hidden) out.push([li.children[0].children[0].textContent, li.children[0].children[1].textContent]);
    });
  });
  return out;
}

function descendants(node, predicate) {
  return node.children.reduce(function (found, child) {
    if (predicate(child)) found.push(child);
    return found.concat(descendants(child, predicate));
  }, []);
}

function titles(view) {
  return descendants(view.get("listings"), function (node) { return node.tagName === "h3"; })
    .map(function (node) { return node.textContent; });
}

function byClass(view, name) {
  return descendants(view.get("listings"), function (node) { return String(node.className).split(" ").indexOf(name) >= 0; });
}

// A city row is li > label > [checkbox, name, count, checkmark].
function cityBox(row) { return row.children[0].children[0]; }
function cityName(row) { return row.children[0].children[1].textContent; }

function check(name, run) {
  return { name: name, run: run };
}

var checks = [
  check("unknown per-person and total price text", async function () {
    var view = dashboard([["data.json", payload([home("Unknown", [match(2, null)])])]]);
    await settle();
    var text = view.get("listings").textContent;
    assert.ok(text.includes("Price unknown"), text);
    assert.ok(text.includes("2 people"), text);
    assert.ok(!text.includes("€0"), text);
    assert.ok(!text.includes("per month"), text);
  }),
  check("cheapest sort follows the selected group", async function () {
    var view = dashboard([["data.json", payload([
      home("Unknown older", [match(3, null)], old),
      home("Low for three", [match(2, 550), match(3, 450)]),
      home("Unknown newer", [match(3, null)], recent),
      home("Low for two", [match(2, 400), match(3, 600)])
    ])]]);
    await settle();
    view.change("sort", "cheapest");
    assert.deepStrictEqual(titles(view).slice(0, 2), ["Low for two", "Low for three"]);
    view.group("3");
    assert.deepStrictEqual(titles(view).slice(0, 2), ["Low for three", "Low for two"]);
  }),
  check("cheapest ties use newest first and unknown prices sort last", async function () {
    var view = dashboard([["data.json", payload([
      home("Unknown older", [match(3, null)], old),
      home("Known older", [match(3, 500)], old),
      home("Unknown newer", [match(3, null)], recent),
      home("Known newer", [match(3, 500)], recent)
    ])]]);
    await settle();
    view.change("sort", "cheapest");
    ["any", "3"].forEach(function (group) {
      view.group(group);
      assert.deepStrictEqual(titles(view), ["Known newer", "Known older", "Unknown newer", "Unknown older"]);
    });
  }),
  check("great price label follows the selected group", async function () {
    var view = dashboard([["data.json", payload([home("Different tiers", [
      match(2, 600), match(3, 400, "great")
    ])])]]);
    await settle();
    function great() { return byClass(view, "tier").map(function (node) { return node.textContent; }); }
    assert.deepStrictEqual(great(), ["Great price"]);
    view.group("2");
    assert.deepStrictEqual(great(), []);
    view.group("3");
    assert.deepStrictEqual(great(), ["Great price"]);
  }),
  check("the lead price is the cheapest match for the selected group", async function () {
    var view = dashboard([["data.json", payload([home("Two prices", [match(2, 600), match(3, 400)])])]]);
    await settle();
    function amounts() { return byClass(view, "amount").map(function (node) { return node.textContent; }); }
    assert.deepStrictEqual(amounts(), ["€400", "€600"]);
    view.group("2");
    assert.deepStrictEqual(amounts(), ["€600"]);
  }),
  check("unknown price stays visible at the slider minimum", async function () {
    var view = dashboard([["data.json", payload([
      home("Unknown", [match(2, null)]), home("Over budget", [match(2, 401)])
    ])]]);
    await settle();
    view.change("maxpp", "400", "input");
    // While the slider moves the numbers follow it and the list waits; letting go settles the list.
    assert.strictEqual(view.get("count").textContent, "1 of 2 listings");
    assert.deepStrictEqual(titles(view), ["Unknown", "Over budget"]);
    view.get("maxpp").fire("change");
    assert.deepStrictEqual(titles(view), ["Unknown"]);
  }),
  check("timer refresh updates stale header, listings, and sources with filters intact", async function () {
    var view = dashboard([
      ["data.json", payload([home("Original", [match(3, 500)])], old)],
      ["data.json", payload([
        home("New for three", [match(3, 500)]), home("New for two", [match(2, 500)])
      ], fresh, "Updated source")]
    ]);
    await settle();
    assert.strictEqual(view.get("stale-warning").hidden, false);
    var row = view.get("city-chips").children[0], box = cityBox(row);
    box.checked = true;
    box.fire("change");
    view.group("3");
    await view.tick(refreshInterval);
    assert.strictEqual(view.get("stale-warning").hidden, true);
    assert.strictEqual(view.get("last-check").textContent, "Checked 2 min ago");
    assert.deepStrictEqual(titles(view), ["New for three"]);
    assert.ok(view.get("sources").textContent.includes("Updated source"));
    assert.strictEqual(view.get("city-chips").children[0], row);
    assert.strictEqual(box.checked, true);
    assert.strictEqual(view.get("group-3").listeners.change.length, 2);   // one filters, one moves the thumb: bound once
    view.advance(60000);
    await view.tick(60000);
    assert.strictEqual(view.get("last-check").textContent, "Checked 3 min ago");
    assert.deepStrictEqual(view.calls, ["data.json", "data.json"]);
  }),
  check("failed and malformed refreshes keep the listings, sources, and old timestamp", async function () {
    var view = dashboard([
      ["data.json", payload([home("Keep me", [match(3, 500)])], recent)],
      ["data.json", null],
      ["data.json", { last_run: fresh }],
      ["data.json", payload([null], fresh)],
      ["data.json", Object.assign(payload([], fresh), { sources: {} })]
    ]);
    await settle();
    view.advance(61 * 60000);
    for (var i = 0; i < 4; i++) {
      await view.tick(refreshInterval);
      await view.tick(60000);
      assert.deepStrictEqual(titles(view), ["Keep me"]);
      assert.ok(view.get("sources").textContent.includes("Original source"));
      assert.strictEqual(view.get("sample-banner").hidden, true);
      assert.strictEqual(view.get("stale-warning").hidden, false);
      assert.strictEqual(view.get("last-check").textContent, "Checked 2 h ago");
    }
    assert.deepStrictEqual(view.calls, ["data.json", "data.json", "data.json", "data.json", "data.json"]);
  }),
  check("visible tab refresh replaces sample data and rebuilds changed city chips", async function () {
    var real = home("Real listing", [match(2, 500)]);
    real.city = "delft";
    var view = dashboard([
      ["data.json", null],
      ["sample-data.json", payload([home("Sample listing", [match(2, 500)])])],
      ["data.json", payload([real], fresh, "Real source", { delft: "Delft" })]
    ]);
    await settle();
    assert.deepStrictEqual(titles(view), ["Sample listing"]);
    assert.strictEqual(view.get("sample-banner").hidden, false);
    await view.visibility("hidden");
    assert.deepStrictEqual(view.calls, ["data.json", "sample-data.json"]);
    await view.visibility("visible");
    assert.deepStrictEqual(titles(view), ["Real listing"]);
    assert.strictEqual(view.get("sample-banner").hidden, true);
    var rows = view.get("city-chips").children;
    assert.deepStrictEqual(rows.map(cityName), ["Delft"]);
    assert.strictEqual(cityBox(rows[0]).listeners.change.length, 1);
    assert.strictEqual(view.get("group-3").listeners.change.length, 2);   // one filters, one moves the thumb: bound once
  }),
  check("copy reply uses title when the address is absent", async function () {
    var view = dashboard([["data.json", payload([home("Canal home", [match(2, 500)])])]]);
    await settle();
    var button = byClass(view, "copy")[0];
    button.fire("click");
    await settle();
    assert.deepStrictEqual(view.copied, ["Interested in Canal home."]);
    assert.strictEqual(button.textContent, "Copied");
    view.timeouts[0]();
    assert.strictEqual(button.textContent, "Copy Reply");
  }),
  check("singular bedroom label, and the number of rooms is not shown", async function () {
    var listing = home("One of each", [match(2, 500)]);
    listing.bedrooms = 1;
    listing.rooms = 1;
    var view = dashboard([["data.json", payload([listing])]]);
    await settle();
    var facts = descendants(view.get("listings"), function (node) { return node.className === "facts"; });
    assert.strictEqual(facts[0].textContent, "1 bedroom");
  }),
  check("refresh without a newer save leaves the cards on screen in place", async function () {
    var same = payload([home("Still here", [match(2, 500)])], recent);
    var view = dashboard([["data.json", same], ["data.json", JSON.parse(JSON.stringify(same))],
                          ["data.json", payload([home("Newer", [match(2, 500)])], fresh)]]);
    await settle();
    var card = view.get("listings").children[0];
    await view.tick(refreshInterval);
    assert.strictEqual(view.get("listings").children[0], card);   // same node: not rebuilt
    await view.tick(refreshInterval);
    assert.deepStrictEqual(titles(view), ["Newer"]);              // a newer save does replace it
  }),
  check("clear filters resets the filters, the search and the controls", async function () {
    var view = dashboard([["data.json", payload([
      home("Cheap for two", [match(2, 450, "great")]), home("Dear for three", [match(3, 700)])
    ])]]);
    await settle();
    assert.strictEqual(view.get("clear-filters").hidden, true);
    assert.strictEqual(view.get("filter-count").hidden, true);
    view.group("2");
    view.attr("housemates", "hide");
    view.change("maxpp", "500", "input");
    view.get("maxpp").fire("change");
    view.change("search", "cheap", "input");
    assert.deepStrictEqual(titles(view), ["Cheap for two"]);
    assert.strictEqual(view.get("count").textContent, "1 of 2 listings");
    assert.strictEqual(view.get("filter-count").textContent, "3");     // the search is not counted: it is on screen
    assert.strictEqual(view.get("filter-count").hidden, false);
    assert.strictEqual(view.get("clear-filters").hidden, false);
    view.click("clear-filters");
    assert.deepStrictEqual(titles(view), ["Cheap for two", "Dear for three"]);
    assert.strictEqual(view.get("count").textContent, "2 listings");
    assert.strictEqual(view.get("group-any").checked, true);
    assert.strictEqual(view.get("group-2").checked, false);
    assert.strictEqual(pressed(view, "housemates", "hide"), false);
    assert.deepStrictEqual(view.used(), []);
    assert.strictEqual(view.get("applied").hidden, true);
    assert.strictEqual(view.get("age").value, "");
    assert.strictEqual(view.get("maxpp").value, 800);
    assert.strictEqual(view.get("search").value, "");
    assert.strictEqual(view.get("filter-count").hidden, true);
  }),
  check("no matches shows the empty state, and its button clears the filters", async function () {
    var view = dashboard([["data.json", payload([home("Only one", [match(2, 500)])])]]);
    await settle();
    assert.strictEqual(view.get("empty").hidden, true);
    view.change("search", "nothing like this", "input");
    assert.strictEqual(view.get("empty").hidden, false);
    assert.strictEqual(view.get("listings").hidden, true);
    view.click("clear-empty");
    assert.strictEqual(view.get("empty").hidden, true);
    assert.deepStrictEqual(titles(view), ["Only one"]);
  }),
  check("cancel in the filter sheet puts back the filters from before it opened", async function () {
    var view = dashboard([["data.json", payload([
      home("For two", [match(2, 500)]), home("For three", [match(3, 500)])
    ])]]);
    await settle();
    view.group("2");
    view.click("open-filters");
    assert.strictEqual(view.get("filters-sheet").attributes.open, "");
    view.group("3");
    view.attr("any_warning", "hide");
    assert.strictEqual(pressed(view, "any_warning", "hide"), true);
    assert.deepStrictEqual(titles(view), ["For three"]);
    view.click("filters-cancel");
    assert.deepStrictEqual(titles(view), ["For two"]);
    assert.strictEqual(view.get("group-2").checked, true);
    assert.strictEqual(pressed(view, "any_warning", "hide"), false);
    assert.strictEqual(view.get("filters-sheet").attributes.open, undefined);
    view.click("open-filters");
    view.group("3");
    view.click("filters-done");                                     // Done keeps the change
    assert.deepStrictEqual(titles(view), ["For three"]);
  }),
  check("the status line counts working sources and the sheet names each status", async function () {
    var data = payload([home("Any", [match(2, 500)])]);
    data.sources = [
      { label: "Fine", status: "ok", listings_last_run: 1 },
      { label: "Half", status: "partial", last_ok: old, error: "city 2 failed" },
      { label: "Walled", status: "blocked" },
      { label: "Unused", status: "disabled" },
      { label: "Odd", status: "something new" }
    ];
    var view = dashboard([["data.json", data]]);
    await settle();
    assert.strictEqual(view.get("sources-summary").textContent, "1 of 4 sources working");   // "disabled" is not counted
    assert.strictEqual(view.get("status-dot").className, "dot warn");
    var rows = view.get("sources").children.map(function (row) { return row.textContent; });
    assert.deepStrictEqual(rows, [
      "Fine1 listing in the last checkWorking",
      "Halflast worked 3 h agocity 2 failedPartly working",
      "Walledhas never workedBlocked",
      "UnusedOff",
      "Oddhas never workedNot working"
    ]);
  }),
  check("all sources working, and a stale check, set the status dot", async function () {
    var view = dashboard([["data.json", payload([home("Any", [match(2, 500)])], recent)]]);
    await settle();
    assert.strictEqual(view.get("sources-summary").textContent, "The source is working");
    assert.strictEqual(view.get("status-dot").className, "dot good");
    view.advance(2 * 3600 * 1000);
    await view.tick(60000);
    assert.strictEqual(view.get("status-dot").className, "dot bad");
    assert.strictEqual(view.get("stale-warning").textContent, "The last check was 3 h ago. The checker may be down.");
  }),
  check("the address line only adds what the title does not say", async function () {
    var inTitle = home("Flat Canal Street, Utrecht", [match(2, 500)]);
    inTitle.address = "Canal Street";
    var partly = home("Flat to rent: Canal Street", [match(2, 500)]);
    partly.address = "Canal Street 12";
    var view = dashboard([["data.json", payload([inTitle, partly])]]);
    await settle();
    assert.deepStrictEqual(byClass(view, "addr").map(function (node) { return node.textContent; }), ["Canal Street 12, Utrecht"]);
  }),
  check("price, availability dates and flags read as plain text", async function () {
    var listing = home("Dated", [match(2, 500)]);
    listing.price_total = 1000;
    listing.price_includes_utilities = false;
    listing.available_from = "2026-03-01";
    listing.available_until = "2027-01-10";
    listing.flags = ["no_students", "max_persons:2", "sharers_ok", "income_requirement"];
    var data = payload([listing]);
    data.flag_labels = { max_persons: "Max {n} persons", sharers_ok: "Sharers welcome" };
    var view = dashboard([["data.json", data]]);
    await settle();
    assert.strictEqual(byClass(view, "facts")[0].textContent, "1 Mar to 10 Jan 2027");
    assert.strictEqual(byClass(view, "pp")[0].textContent, "€500 excl.2 people");   // no search list in this data: the group is named
    // a limit on people is a plain fact, not a warning
    assert.deepStrictEqual(byClass(view, "warn").map(function (node) { return node.textContent; }), ["no students"]);
    assert.deepStrictEqual(byClass(view, "req").map(function (node) { return node.textContent; }), ["Max 2 persons"]);
    assert.deepStrictEqual(byClass(view, "good").map(function (node) { return node.textContent; }), ["Sharers welcome"]);
    assert.strictEqual(byClass(view, "notes")[0].textContent, "income requirement");
  }),
  check("the group thumb sits under the chosen group, from the first moment", async function () {
    var view = dashboard([["data.json", payload([home("For three", [match(3, 500)])])]],
      { cities: [], group: "3", maxpp: 800, onlyGreat: false, hideWarn: false, q: "", sort: "newest" });
    // The choices stand in the order 2 People, 3 People, Any: "Any" is the last one, on the right.
    var thumb = view.get("group-thumb");
    assert.strictEqual(thumb.style.transform, "translateX(100.00%)");   // before the data has arrived
    assert.strictEqual(view.get("group-3").checked, true);
    await settle();
    assert.strictEqual(thumb.style.transform, "translateX(100.00%)");
    view.group("2");
    assert.strictEqual(thumb.style.transform, "translateX(0.00%)");
    view.click("clear-filters");
    assert.strictEqual(thumb.style.transform, "translateX(200.00%)");
    assert.strictEqual(view.get("group-any").checked, true);
  }),
  check("a 'great prices only' choice saved by an older version no longer hides anything", async function () {
    var view = dashboard([["data.json", payload([home("Great", [match(2, 450, "great")]), home("Fine", [match(2, 700)])])]],
      { cities: [], group: "any", maxpp: 800, onlyGreat: true, hideWarn: false, q: "", sort: "newest" });
    await settle();
    assert.deepStrictEqual(titles(view), ["Great", "Fine"]);
    assert.strictEqual(view.get("filter-count").hidden, true);
    assert.deepStrictEqual(byClass(view, "tier").map(function (node) { return node.textContent; }), ["Great price"]);
  }),
  check("each thing a listing says is a filter of its own: Only keeps it, Hide removes it, pressing again turns it off", async function () {
    function flagged(title, flags) { var l = home(title, [match(2, 500)]); l.flags = flags; return l; }
    var view = dashboard([["data.json", payload([
      flagged("Silent", []), flagged("Students only", ["students_only"]), flagged("No students", ["no_students"]),
      flagged("Working only", ["working_only"]), flagged("From 25", ["min_age:25"]), flagged("Up to 27", ["max_age:27"]),
      flagged("No sharers", ["no_sharers"]), flagged("Sharers welcome", ["sharers_ok", "students_ok"]),
      flagged("Housemates", ["housemates", "short_term"])
    ])]]);
    await settle();
    assert.strictEqual(titles(view).length, 9);
    view.attr("students_only", "only");
    assert.deepStrictEqual(titles(view), ["Students only"]);
    assert.strictEqual(pressed(view, "students_only", "only"), true);
    view.attr("students_only", "hide");                               // straight from Only to Hide
    assert.strictEqual(titles(view).length, 8);
    assert.ok(titles(view).indexOf("Students only") < 0);
    assert.strictEqual(pressed(view, "students_only", "only"), false);
    assert.strictEqual(pressed(view, "students_only", "hide"), true);
    view.attr("students_only", "hide");                               // pressing it again turns it off
    assert.strictEqual(titles(view).length, 9);
    assert.strictEqual(pressed(view, "students_only", "hide"), false);
    // several at once: every filter in use must agree
    view.attr("no_students", "hide");
    view.attr("working_only", "hide");
    view.attr("housemates", "hide");
    assert.deepStrictEqual(titles(view), ["Silent", "Students only", "From 25", "Up to 27", "No sharers", "Sharers welcome"]);
    view.attr("sharers_ok", "only");
    assert.deepStrictEqual(titles(view), ["Sharers welcome"]);
    assert.strictEqual(view.get("filter-count").textContent, "4");
    assert.deepStrictEqual(view.used(), ["HideNo Students", "HideWorking People Only", "OnlySharers Welcome", "HideShared with Housemates"]);
    assert.strictEqual(view.get("applied").hidden, false);
    // a filter that is made of two flags, and one that is a group of flags
    view.click("clear-filters");
    view.attr("age_limit", "only");
    assert.deepStrictEqual(titles(view), ["From 25", "Up to 27"]);
    view.attr("age_limit", "only");
    view.attr("any_warning", "hide");
    assert.deepStrictEqual(titles(view), ["Silent", "Students only", "Working only", "From 25", "Up to 27", "Sharers welcome", "Housemates"]);
    view.click("clear-filters");
    // your own age is a number, not a yes or no
    view.change("age", "2", "input");                                // half a number is no age yet
    assert.strictEqual(titles(view).length, 9);
    view.change("age", "23", "input");
    assert.ok(titles(view).indexOf("From 25") < 0 && titles(view).indexOf("Up to 27") >= 0, titles(view).join());
    view.change("age", "30", "input");
    assert.ok(titles(view).indexOf("From 25") >= 0 && titles(view).indexOf("Up to 27") < 0, titles(view).join());
    view.change("age", "27x", "input");                              // only digits count; 27 is still within "up to 27"
    assert.strictEqual(view.get("age").value, "27");
    assert.strictEqual(titles(view).length, 9);
    assert.deepStrictEqual(view.used(), ["Age 27"]);
  }),
  check("a filter in use can be removed on its own from above the list", async function () {
    function flagged(title, flags, city) { var l = home(title, [match(2, 500), match(3, 600)]); l.flags = flags; l.city = city || "utrecht"; return l; }
    var view = dashboard([["data.json", payload([
      flagged("Plain", []), flagged("Short", ["short_term"]), flagged("Leiden students", ["students_ok"], "leiden")
    ], null, null, { utrecht: "Utrecht", leiden: "Leiden" })]]);
    await settle();
    assert.strictEqual(view.get("applied").hidden, true);
    view.group("2");
    view.attr("short_term", "hide");
    view.attr("students_ok", "only");
    view.change("maxpp", "700", "input");
    view.get("maxpp").fire("change");
    assert.deepStrictEqual(view.used(), ["2 People", "Up to €700 per Person", "OnlyStudents Welcome", "HideShort-Term Only"]);
    assert.deepStrictEqual(titles(view), ["Leiden students"]);
    view.remove(2);                                                  // only "Only Students Welcome" goes
    assert.deepStrictEqual(view.used(), ["2 People", "Up to €700 per Person", "HideShort-Term Only"]);
    assert.strictEqual(pressed(view, "students_ok", "only"), false);
    assert.strictEqual(pressed(view, "short_term", "hide"), true);
    assert.deepStrictEqual(titles(view), ["Plain", "Leiden students"]);
    view.remove(0);                                                  // the group size
    assert.strictEqual(view.get("group-any").checked, true);
    view.remove(0);                                                  // the budget
    assert.strictEqual(view.get("maxpp").value, 800);
    assert.deepStrictEqual(view.used(), ["HideShort-Term Only"]);
    assert.strictEqual(view.get("filter-count").textContent, "1");
    view.remove(0);
    assert.strictEqual(view.get("applied").hidden, true);
    assert.strictEqual(titles(view).length, 3);
    // with two or more in use there is one button that clears them all
    view.attr("short_term", "only");
    assert.strictEqual(view.get("applied").children.length, 1);
    view.attr("students_ok", "hide");
    var all = view.get("applied").children[2];
    assert.strictEqual(all.textContent, "Clear All");
    all.fire("click");
    assert.deepStrictEqual(view.used(), []);
    assert.strictEqual(JSON.parse(view.stored["househunter.filters.v1"]).attrs.short_term, undefined);
  }),
  check("every thing a listing can say has a row with its count, also when no listing says it today", async function () {
    function flagged(title, flags, more) { var l = home(title, [match(2, 500)]); l.flags = flags; Object.assign(l, more || {}); return l; }
    var first = payload([
      flagged("A", ["students_ok", "service_costs:40"]), flagged("B", ["students_ok", "rent_for_3:2395"], { furnished: "furnished" }),
      flagged("C", ["max_persons:2"], { price_drop_from: 1600 })
    ], recent);
    var second = payload([flagged("A", ["service_costs:40"]), flagged("New", ["no_sharers"])], fresh);
    var view = dashboard([["data.json", first], ["data.json", second]]);
    await settle();
    var rows = attrRowsShown(view), count = {};
    rows.forEach(function (r) { count[r[0]] = r[1]; });
    assert.strictEqual(rows.length, 31);
    assert.deepStrictEqual(rows.filter(function (r) { return r[1] !== "0"; }), [["Students Welcome", "2"], ["Limit on People", "1"],
      ["Service Costs on Top", "1"], ["Rent Depends on Month or Group", "1"], ["Price Dropped", "1"], ["Furnished", "1"]]);
    assert.strictEqual(count["Any Warning"], "0");                   // a limit on people is a fact, not a warning
    assert.strictEqual(count["No House Sharers"], "0");
    view.attr("no_sharers", "hide");                                 // set before any listing says it
    assert.deepStrictEqual(titles(view), ["A", "B", "C"]);
    await view.tick(refreshInterval);                                // a new listing says it: it never shows
    assert.deepStrictEqual(titles(view), ["A"]);
    rows = attrRowsShown(view);
    assert.strictEqual(rows.length, 31);
    assert.deepStrictEqual(rows.filter(function (r) { return r[1] !== "0"; }), [["No House Sharers", "1"], ["Service Costs on Top", "1"],
      ["Any Warning", "1"]]);
  }),
  check("filters saved by the earlier version come back as the same choices", async function () {
    function flagged(title, flags) { var l = home(title, [match(2, 500)]); l.flags = flags; return l; }
    var listings = [flagged("Silent", []), flagged("Students only", ["students_only"]), flagged("No students", ["no_students"]),
      flagged("Working only", ["working_only"]), flagged("No sharers", ["no_sharers"]), flagged("Sharers welcome", ["sharers_ok"]),
      flagged("Housemates", ["housemates", "sharers_ok"])];
    var view = dashboard([["data.json", payload(listings)]],
      { cities: [], group: "any", maxpp: 800, tenant: "students", age: null, hideNoSharers: true, onlySharersOk: false,
        hideHousemates: false, hideWarn: false, q: "", sort: "newest" });
    await settle();
    assert.deepStrictEqual(titles(view), ["Silent", "Students only", "Sharers welcome", "Housemates"]);
    assert.deepStrictEqual(view.used(), ["HideNo Students", "HideWorking People Only", "HideNo House Sharers"]);
    view = dashboard([["data.json", payload(listings)]],
      { cities: [], group: "any", maxpp: 800, tenant: "working", onlySharersOk: true, hideHousemates: true, hideWarn: true });
    await settle();
    assert.deepStrictEqual(titles(view), ["Sharers welcome"]);
    assert.deepStrictEqual(view.used(), ["HideStudents Only", "OnlySharers Welcome", "HideShared with Housemates", "HideAny Warning"]);
    // what this version saves is read back as it is, and anything it does not know is dropped
    view = dashboard([["data.json", payload(listings)]], { attrs: { housemates: "only", made_up: "only", no_sharers: "sometimes" } });
    await settle();
    assert.deepStrictEqual(titles(view), ["Housemates"]);
    assert.deepStrictEqual(view.used(), ["OnlyShared with Housemates"]);
  }),
  check("a listing's own sentence stands once under its marks, as plain text", async function () {
    var listing = home("Quoted", [match(2, 500)]);
    listing.flags = ["no_students", "working_only", "min_age:25", "income_requirement"];
    listing.evidence = {
      no_students: "Max. 1 WERKEND PERSOON OF STEL (GEEN STUDENTEN)", working_only: "Max. 1 WERKEND PERSOON OF STEL (GEEN STUDENTEN)",
      min_age: "<b>Minimale leeftijd 25</b>", income_requirement: "Inkomenseis 3x de huur", stray: 7
    };
    var plain = home("Plain", [match(2, 500)]);
    plain.flags = ["no_students"];
    plain.evidence = "not an object";
    var data = payload([listing, plain]);
    data.flag_labels = { no_students: "No students", working_only: "Working people only", min_age: "Minimum age {n}" };
    var view = dashboard([["data.json", data]]);
    await settle();
    assert.deepStrictEqual(byClass(view, "quote").map(function (node) { return node.textContent; }),
      ["“Max. 1 WERKEND PERSOON OF STEL (GEEN STUDENTEN)”", "“<b>Minimale leeftijd 25</b>”"]);
    assert.deepStrictEqual(byClass(view, "req").map(function (node) { return node.textContent; }),
      ["Working people only", "Minimum age 25"]);
    assert.deepStrictEqual(byClass(view, "warn").map(function (node) { return node.textContent; }), ["No students", "No students"]);
  }),
  check("two cuts of the same long sentence are shown as one quote", async function () {
    var listing = home("Run-on", [match(3, 600)]);
    listing.flags = ["students_only", "min_age:23", "housemates"];
    listing.evidence = {
      students_only: "…Huurprijs voor 2 personen € 1995,- Ook beschikbaar voor studenten van 23 jaar en ouder…",
      min_age: "…in de woonkamer) Huurprijs voor 2 personen € 1995,- Ook beschikbaar voor studenten van 23 jaar en ouder…",
      housemates: "Je deelt de keuken met twee huisgenoten"
    };
    var view = dashboard([["data.json", payload([listing])]]);
    await settle();
    assert.deepStrictEqual(byClass(view, "quote").map(function (node) { return node.textContent; }),
      ["“…in de woonkamer) Huurprijs voor 2 personen € 1995,- Ook beschikbaar voor studenten van 23 jaar en ouder…”",
        "“Je deelt de keuken met twee huisgenoten”"]);
  }),
  check("the reply text is changed in one place and fills in each listing", async function () {
    var a = home("Canal Street 1", [match(2, 500)]);
    a.address = "Canal Street 1"; a.price_total = 1450; a.bedrooms = 2; a.area_m2 = 70; a.source_label = "Kamernet";
    a.url = "https://example.com/a";
    var b = home("Dam 5", [match(2, 600)]);
    var view = dashboard([["data.json", payload([a, b])]]);
    await settle();
    function copyButtons() { return byClass(view, "copy"); }
    copyButtons()[0].fire("click");
    await settle();
    assert.strictEqual(view.copied[0], "Interested in Canal Street 1.");   // the standard text
    view.click("open-reply");
    var text = view.get("reply-text");
    assert.strictEqual(text.value, "Interested in {address}.");
    assert.strictEqual(view.get("reply-reset").hidden, true);
    text.value = "Hi, {title} in {city}: {price}, {bedrooms} bedrooms, {area}, via {source}. {url} {unknown}";
    text.fire("input");
    assert.strictEqual(view.get("reply-preview").textContent,
      "Hi, Canal Street 1 in Utrecht: €1.450, 2 bedrooms, 70 m², via Kamernet. https://example.com/a {unknown}");
    assert.strictEqual(view.get("reply-reset").hidden, false);
    view.click("reply-cancel");                                       // Cancel keeps the old text
    copyButtons()[0].fire("click");
    await settle();
    assert.strictEqual(view.copied[1], "Interested in Canal Street 1.");
    assert.strictEqual(view.stored["househunter.reply.v1"], undefined);
    view.click("open-reply");
    assert.strictEqual(text.value, "Interested in {address}.");       // the cancelled draft is gone
    text.value = "Hello about {title} ({price})";
    text.fire("input");
    view.click("reply-save");
    assert.strictEqual(view.stored["househunter.reply.v1"], "Hello about {title} ({price})");
    copyButtons()[0].fire("click");
    copyButtons()[1].fire("click");
    await settle();
    assert.strictEqual(view.copied[2], "Hello about Canal Street 1 (€1.450)");
    assert.strictEqual(view.copied[3], "Hello about Dam 5 ()");       // a detail the listing lacks is left out
    view.click("open-reply");
    view.click("reply-reset");                                         // back to the standard text
    assert.strictEqual(text.value, "Interested in {address}.");
    view.click("reply-save");
    assert.strictEqual(view.stored["househunter.reply.v1"], undefined);
  }),
  check("a reply text saved earlier is used from the start", async function () {
    var a = home("Canal Street 1", [match(2, 500)]);
    var saved = dashboard([["data.json", payload([a])]], null, { "househunter.reply.v1": "Mine: {title}" });
    await settle();
    byClass(saved, "copy")[0].fire("click");
    await settle();
    assert.strictEqual(saved.copied[0], "Mine: Canal Street 1");
  }),
  check("a sheet dims the page while open, and gives focus back once when it closes", async function () {
    var view = dashboard([["data.json", payload([home("Home", [match(2, 500)])])]]);
    await settle();
    var focused = 0, sheet = view.get("sources-sheet"), scrim = view.get("scrim");
    view.get("open-sources").focus = function () { focused++; };
    view.click("open-sources");
    assert.strictEqual(sheet.attributes.open, "");
    assert.strictEqual(scrim.hidden, false);
    assert.strictEqual(scrim.style.opacity, "1");
    view.click("sources-close");
    assert.strictEqual(sheet.attributes.open, undefined);
    assert.strictEqual(scrim.hidden, true);
    sheet.fire("close");
    sheet.fire("close");                                            // a late second event must not pull focus back again
    assert.strictEqual(focused, 1);
    view.click("open-filters");                                     // the other sheet uses the same dimming
    assert.strictEqual(view.get("filters-sheet").attributes.open, "");
    assert.strictEqual(scrim.hidden, false);
    view.click("filters-done");
    assert.strictEqual(scrim.hidden, true);
  }),
  check("a listing keeps its photo element when its row is rebuilt", async function () {
    var first = home("Home", [match(2, 500), match(3, 400)]), same = home("Home", [match(2, 500), match(3, 380)]);
    var other = home("Home", [match(2, 500), match(3, 380)]);
    first.uid = same.uid = other.uid = "a";
    first.image_url = same.image_url = "https://example.com/a.jpg";
    other.image_url = "https://example.com/b.jpg";
    var view = dashboard([["data.json", payload([first], old)], ["data.json", payload([same], recent)],
      ["data.json", payload([other], fresh)]]);
    await settle();
    var photo = function () {
      return descendants(view.get("listings"), function (node) { return node.tagName === "img"; })[0];
    };
    var img = photo();
    assert.strictEqual(img.attributes.src, "https://example.com/a.jpg");
    view.group("3");                                                // the row is rebuilt for the new group size
    assert.strictEqual(photo(), img);
    await view.tick(refreshInterval);                               // new data, same listing and photo
    assert.ok(view.get("listings").textContent.includes("€380"));
    assert.strictEqual(photo(), img);
    await view.tick(refreshInterval);                               // the listing got another photo
    assert.notStrictEqual(photo(), img);
    assert.strictEqual(photo().attributes.src, "https://example.com/b.jpg");
  }),
  check("the list also settles when the slider pauses without being let go", async function () {
    var view = dashboard([["data.json", payload([
      home("Cheap", [match(2, 450)]), home("Dear", [match(2, 700)])
    ])]]);
    await settle();
    view.change("maxpp", "600", "input");
    view.change("maxpp", "500", "input");
    assert.strictEqual(view.get("maxpp-out").textContent, "€500");
    assert.deepStrictEqual(titles(view), ["Cheap", "Dear"]);
    view.timeouts[0]();                                             // the first pause timer was replaced: nothing happens
    assert.deepStrictEqual(titles(view), ["Cheap", "Dear"]);
    view.timeouts[1]();                                             // the slider has rested
    assert.deepStrictEqual(titles(view), ["Cheap"]);
  }),
  check("no data at all shows the error state", async function () {
    var view = dashboard([["data.json", null], ["sample-data.json", null]]);
    await settle();
    await settle();
    assert.strictEqual(view.get("load-error").hidden, false);
    assert.strictEqual(view.get("last-check").textContent, "No data");
    assert.strictEqual(view.get("status-dot").className, "dot bad");
  })
];


// ---------- New cards, hiding, the map ----------
var ONE = [{ name: "2 people", persons: 2 }];
function oneSearch(listings, more) { return Object.assign(payload(listings), { searches: ONE }, more || {}); }
function priced(title, pp, extra) {
  return Object.assign(home(title, [match(2, pp)]), extra || {});
}
function ppTexts(view) { return byClass(view, "pp").map(function (node) { return node.textContent; }); }
function hideButtons(view) { return byClass(view, "hide"); }
var STORE_HIDDEN = "househunter.hidden.v1";

// A small stand-in for Leaflet: it records what the map code asks for and lets a check move the map and click markers.
function fakeLeaflet() {
  var seen = { maps: [], markers: [], tiles: [] };
  var L = {
    map: function (node, options) {
      var handlers = {}, map = { options: options, bounds: { s: 51, w: 4, n: 53, e: 6 }, views: [], fits: [] };
      map.on = function (event, fn) { (handlers[event] || (handlers[event] = [])).push(fn); return map; };
      map.fire = function (event) { (handlers[event] || []).forEach(function (fn) { fn(); }); };
      map.setView = function (centre, zoom, o) { map.views.push([centre, zoom, o]); map.fire("moveend"); return map; };
      map.fitBounds = function (points, o) { map.fits.push([points, o]); map.fire("moveend"); return map; };
      map.getZoom = function () { return 7; };
      map.invalidateSize = function () { map.fire("moveend"); };
      map.remove = function () {};
      map.getBounds = function () {
        return { getSouth: function () { return map.bounds.s; }, getWest: function () { return map.bounds.w; },
          getNorth: function () { return map.bounds.n; }, getEast: function () { return map.bounds.e; } };
      };
      seen.maps.push(map);
      return map;
    },
    tileLayer: function (url, options) {
      var layer = { url: url, options: options, addTo: function () { return layer; } };
      seen.tiles.push(layer);
      return layer;
    },
    divIcon: function (options) { return options; },
    marker: function (latlng, options) {
      var click = null, marker = { latlng: latlng, options: options, icon: options.icon, z: 0 };
      marker.on = function (event, fn) { if (event === "click") click = fn; return marker; };
      marker.addTo = function () { seen.markers.push(marker); return marker; };
      marker.remove = function () { seen.markers.splice(seen.markers.indexOf(marker), 1); };
      marker.setIcon = function (icon) { marker.icon = icon; };
      marker.setLatLng = function (ll) { marker.latlng = ll; };
      marker.setZIndexOffset = function (z) { marker.z = z; };
      marker.click = function () { click(); };
      return marker;
    }
  };
  seen.L = L;
  return seen;
}
function located(title, pp, lat, lon, extra) {
  return Object.assign(priced(title, pp, { lat: lat, lon: lon, geo: "address", uid: "u-" + title, home_key: "hk-" + title }), extra || {});
}
function pins(seen) { return seen.markers.map(function (m) { return m.icon.html.textContent; }); }
function pillClass(marker) { return marker.icon.html.className; }

checks.push(
  check("the words the page itself holds: the bar after hiding, the map messages", async function () {
    var html = fs.readFileSync(path.join(__dirname, "../docs/index.html"), "utf8");
    ["Listing hidden", ">Undo<", "Search This Area", "The map could not be loaded", "Hidden Homes", "Show All Again", ">List<", ">Map<",
      "Prices are per person per month"].forEach(function (text) { assert.ok(html.includes(text), text); });
    assert.ok(!html.includes("innerHTML"));
    var script = fs.readFileSync(path.join(__dirname, "../docs/app.js"), "utf8");
    assert.ok(!/innerHTML|insertAdjacentHTML/.test(script.replace(/never innerHTML/, "")), "no innerHTML in app.js");
  }),
  check("a mark's class never collides with another style of the page", async function () {
    // "check" is the tick of a chosen city, which is invisible until chosen: a mark with that class did not show.
    var css = fs.readFileSync(path.join(__dirname, "../docs/style.css"), "utf8");
    var script = fs.readFileSync(path.join(__dirname, "../docs/app.js"), "utf8");
    var marks = /var MARKS = \{([^}]*)\}/.exec(script)[1].match(/\["([a-z]+)", "i-[a-z]+"\]/g).map(function (m) { return /"([a-z]+)"/.exec(m)[1]; });
    assert.deepStrictEqual(marks, ["warn", "good", "req", "ask"]);
    marks.forEach(function (name) {
      var alone = new RegExp("(^|[},\\s])\\." + name + "\\s*[{,]", "m");
      assert.ok(!alone.test(css), "." + name + " is styled on its own in style.css");
    });
  }),
  check("the card keeps what to review; plain facts and the listing's sentences are under Details", async function () {
    var data = oneSearch([
      Object.assign(priced("Review", 700), {
        flags: ["no_registration", "sharing_unclear", "sharers_ok", "income_requirement", "service_costs:85"],
        evidence: { sharing_unclear: "Geschikt voor een alleenstaande of stel.", service_costs: "Servicekosten € 85 per maand" }
      }),
      priced("Bare", 710)
    ], {
      flag_labels: { no_registration: "No registration possible", sharing_unclear: "Sharing policy unclear", sharers_ok: "Sharers welcome",
        income_requirement: "Income requirement", service_costs: "Plus €{n} service costs a month" },
      flag_kinds: { no_registration: "warn", sharing_unclear: "check", sharers_ok: "good", income_requirement: "info", service_costs: "info" }
    });
    var view = dashboard([["data.json", data]]);
    await settle();
    var details = descendants(view.get("listings"), function (n) { return n.tagName === "details"; });
    assert.strictEqual(details.length, 1);                               // a card with nothing more to say has no "Details"
    assert.strictEqual(details[0].children[0].tagName, "summary");
    assert.strictEqual(details[0].children[0].textContent, "Details");
    function inside(name) {
      return descendants(details[0], function (n) { return String(n.className || "").split(" ").indexOf(name) >= 0; })
        .map(function (n) { return n.textContent; });
    }
    assert.deepStrictEqual(inside("req"), ["Income requirement", "Plus €85 service costs a month"]);
    assert.deepStrictEqual(inside("quote"), ["“Geschikt voor een alleenstaande of stel.”", "“Servicekosten € 85 per maand”"]);
    assert.deepStrictEqual(inside("warn").concat(inside("ask"), inside("good")), []);
    function onCard(name) { return byClass(view, name).map(function (n) { return n.textContent; }); }
    assert.deepStrictEqual(onCard("warn"), ["No registration possible"]);
    assert.deepStrictEqual(onCard("ask"), ["Sharing policy unclear"]);
    assert.deepStrictEqual(onCard("good"), ["Sharers welcome"]);
  }),
  check("a listing from an alert email is named without the site", async function () {
    var view = dashboard([["data.json", oneSearch([
      Object.assign(priced("Pararius: Maria Snelplantsoen", 700), { source: "email_alerts", source_label: "Email alerts" }),
      Object.assign(priced("Vesteda: Daltonstraat 4", 710), { source: "vesteda", source_label: "Vesteda" }),
      Object.assign(priced("Te huur: ruim appartement", 720), { source: "kamernet", source_label: "Kamernet" })
    ])]]);
    await settle();
    assert.deepStrictEqual(titles(view), ["Maria Snelplantsoen", "Daltonstraat 4", "Te huur: ruim appartement"]);
  }),
  check("the price each person pays leads, as a short label", async function () {
    var view = dashboard([["data.json", oneSearch([
      Object.assign(priced("Incl", 750), { price_includes_utilities: true }),
      Object.assign(priced("Excl", 750), { price_includes_utilities: false }),
      Object.assign(priced("Plain", 750), { price_includes_utilities: null }),
      Object.assign(home("Listed", [match(2, null)]), { price_total: 1700, price_includes_utilities: true }),
      home("Nothing", [match(2, null)])
    ])]]);
    await settle();
    assert.deepStrictEqual(titles(view), ["Incl", "Excl", "Plain", "Listed", "Nothing"]);
    assert.deepStrictEqual(ppTexts(view), ["€750 incl.", "€750 excl.", "€750", "€1.700 listed", "Price unknown"]);
    assert.ok(!view.get("listings").textContent.includes("per person"));
    assert.ok(!view.get("listings").textContent.includes("per month"));
    // The pipeline's own decision wins: service costs on top make it "excl." even when utilities are included, and
    // a rent for one place (Cruquiusweg 85: € 967 bare rent, € 195 service costs) is shown as it is, never halved.
    var decided = dashboard([["data.json", oneSearch([
      Object.assign(priced("Costs on top", 650), { price_includes_utilities: true, price_basis: "excl" }),
      Object.assign(priced("Not said", 700), { price_includes_utilities: true, price_basis: null }),
      Object.assign(home("One place", [Object.assign(match(2, 967), { extra_pp: 195 })]),
        { price_total: 967, price_basis: "excl", rent_basis: "place", flags: ["price_per_person", "service_costs:195"] })
    ])]]);
    await settle();
    assert.deepStrictEqual(ppTexts(decided), ["€650 excl.", "€700", "€967 excl."]);
    assert.ok(!decided.get("listings").textContent.includes("€483") && !decided.get("listings").textContent.includes("€484"));
    // the green tag stays
    var great = oneSearch([Object.assign(home("Cheap", [match(2, 450, "great")]), { price_includes_utilities: true })]);
    view = dashboard([["data.json", great]]);
    await settle();
    assert.deepStrictEqual(byClass(view, "tier").map(function (n) { return n.textContent; }), ["Great price"]);
    assert.strictEqual(ppTexts(view)[0], "€450 incl.Great price");
  }),
  check("the facts line holds only bedrooms, size, term and Furnished, and leaves out what is missing", async function () {
    function facts(extra) {
      var l = priced("Home", 500, extra);
      return l;
    }
    var view = dashboard([["data.json", oneSearch([
      facts({ bedrooms: 2, area_m2: 78, available_from: "2026-12-01", furnished: "furnished", price_total: 1000, rooms: 4 }),
      facts({ bedrooms: 1, available_from: "2025-12-01" }),
      facts({ area_m2: 40, available_from: "2026-12-20", available_until: "2026-12-31", furnished: "upholstered" }),
      facts({ available_until: "2026-11-30" }),
      facts({ furnished: "unfurnished" })
    ])]]);
    await settle();
    var lines = byClass(view, "facts").map(function (node) { return node.textContent; });
    assert.deepStrictEqual(lines, ["2 bedrooms · 78 m² · from 1 Dec · Furnished", "1 bedroom · now", "40 m² · 20 Dec to 31 Dec", "until 30 Nov"]);
    assert.strictEqual(byClass(view, "facts").length, 4);                 // the last home has nothing to say: no empty line
    var text = view.get("listings").textContent;
    assert.ok(!text.includes("Original source") && !text.includes("ago") && !text.includes("per month"), text);
  }),
  check("one search: no source name, no group size anywhere; several searches keep it", async function () {
    var l = priced("Home", 600, { source_label: "Kamernet", source: "kamernet" });
    var view = dashboard([["data.json", oneSearch([l])]], { cities: [], group: "3", maxpp: 800, q: "", sort: "newest" });
    await settle();
    var text = view.get("listings").textContent;
    assert.ok(!text.includes("Kamernet") && !text.includes("people") && !text.includes("person"), text);
    assert.strictEqual(view.get("group-fieldset").hidden, true);
    assert.deepStrictEqual(titles(view), ["Home"]);                       // an old saved group size hides nothing
    assert.deepStrictEqual(view.used(), []);
    assert.strictEqual(view.get("filter-count").hidden, true);
    assert.strictEqual(byClass(view, "meta").length, 0);
    assert.strictEqual(byClass(view, "out").length, 0);                   // the arrow icon is gone
    // the whole card still opens the listing
    var withUrl = priced("Linked", 600, { url: "https://example.com/a" });
    view = dashboard([["data.json", oneSearch([withUrl])]]);
    await settle();
    var links = byClass(view, "link");
    assert.strictEqual(links.length, 1);
    assert.strictEqual(links[0].attributes.href, "https://example.com/a");
    assert.strictEqual(links[0].attributes.target, "_blank");
    // two searches: today's behaviour
    var two = Object.assign(payload([home("Both", [match(2, 500), match(3, 400)])]),
      { searches: [{ name: "2 people", persons: 2 }, { name: "3 people", persons: 3 }] });
    view = dashboard([["data.json", two]]);
    await settle();
    assert.strictEqual(view.get("group-fieldset").hidden, false);
    assert.ok(view.get("listings").textContent.includes("3 people"));
    assert.ok(view.get("listings").textContent.includes("2 people"));
    view.group("2");
    assert.deepStrictEqual(view.used(), ["2 People"]);
  }),
  check("flag_kinds decides what a mark is; unknown kinds fall back to the built-in lists", async function () {
    var l = priced("Marked", 500, {
      flags: ["max_persons:2", "sharing_unclear", "students_ok", "no_students", "made_up", "price_per_person", "scam_signals"],
      evidence: { sharing_unclear: "Delen met anderen: in overleg" }
    });
    var data = oneSearch([l], {
      flag_labels: { max_persons: "Up to {n} people", sharing_unclear: "Sharing policy unclear", students_ok: "Students welcome", no_students: "No students" },
      flag_kinds: { max_persons: "info", sharing_unclear: "check", students_ok: "warn", price_per_person: "nonsense", scam_signals: 7 }
    });
    var view = dashboard([["data.json", data]]);
    await settle();
    function texts(name) { return byClass(view, name).map(function (n) { return n.textContent; }); }
    assert.deepStrictEqual(texts("ask"), ["Sharing policy unclear"]);
    assert.deepStrictEqual(texts("req"), ["Up to 2 people", "price per person"]);   // "info" and the old list
    assert.deepStrictEqual(texts("warn"), ["Students welcome", "No students", "scam signals"]);
    assert.deepStrictEqual(texts("good"), []);
    assert.deepStrictEqual(texts("notes"), ["made up"]);
    var mark = byClass(view, "ask")[0];
    var use = descendants(mark, function (n) { return n.tagName === "use"; })[0];
    assert.strictEqual(use.attributes.href, "#i-help");                       // its own question-mark symbol
    assert.deepStrictEqual(texts("quote"), ["“Delen met anderen: in overleg”"]);
    // without flag_kinds the lists decide, as before
    view = dashboard([["data.json", oneSearch([Object.assign({}, l, { flags: ["max_persons:2", "sharing_unclear"] })])]]);
    await settle();
    assert.deepStrictEqual(byClass(view, "warn").length, 0);          // "Up to 2 people" is a plain fact there too
    assert.deepStrictEqual(byClass(view, "req").length, 1);
    assert.deepStrictEqual(byClass(view, "ask").length, 0);
  }),
  check("the filters for unclear sharing, a room or shared place, and unclear rent", async function () {
    var view = dashboard([["data.json", oneSearch([
      priced("Sharing?", 500, { flags: ["sharing_unclear"] }), priced("Room", 500, { rent_basis: "place" }),
      priced("Whole home", 500, { rent_basis: "home" }), home("Rent?", [match(2, null), match(3, null)]),
      priced("Unknown basis", 500, { rent_basis: null })
    ])]]);
    await settle();
    var rows = {};
    attrRowsShown(view).forEach(function (r) { rows[r[0]] = r[1]; });
    assert.deepStrictEqual([rows["Sharing Policy Unclear"], rows["Room or Shared Place"], rows["Rent Unclear"]], ["1", "1", "1"]);
    view.attr("sharing_unclear", "only"); assert.deepStrictEqual(titles(view), ["Sharing?"]); view.attr("sharing_unclear", "only");
    view.attr("place_only", "only"); assert.deepStrictEqual(titles(view), ["Room"]); view.attr("place_only", "only");
    view.attr("rent_unclear", "only"); assert.deepStrictEqual(titles(view), ["Rent?"]);
    view.attr("rent_unclear", "hide"); assert.strictEqual(titles(view).length, 4);
  }),
  check("hiding a home: gone at once, counted, Undo brings it back, the bar goes after six seconds", async function () {
    var view = dashboard([["data.json", oneSearch([
      located("Alpha", 500, 52.0, 5.0), located("Beta", 600, 52.1, 5.1), located("Gamma", 700, 52.2, 5.2)
    ])]]);
    await settle();
    assert.strictEqual(view.get("count").textContent, "3 listings");
    assert.strictEqual(view.get("hidden-fieldset").hidden, true);
    assert.strictEqual(view.get("toast").hidden, true);
    hideButtons(view)[0].fire("click");
    assert.deepStrictEqual(titles(view), ["Beta", "Gamma"]);
    assert.strictEqual(view.get("count").textContent, "2 listings");     // hidden homes are not counted
    assert.strictEqual(view.get("toast").hidden, false);
    assert.strictEqual(view.get("hidden-fieldset").hidden, false);
    assert.strictEqual(view.get("hidden-count").textContent, "1");
    var saved = JSON.parse(view.stored[STORE_HIDDEN]);
    assert.deepStrictEqual(Object.keys(saved), ["hk-Alpha"]);
    assert.deepStrictEqual(saved["hk-Alpha"], { t: "Alpha", a: "", at: new Date(now).toISOString(), u: "u-Alpha" });
    view.change("search", "gam", "input");
    assert.strictEqual(view.get("count").textContent, "1 of 2 listings"); // "of" counts only homes that are not hidden
    view.change("search", "", "input");
    view.click("toast-undo");
    assert.deepStrictEqual(titles(view), ["Alpha", "Beta", "Gamma"]);
    assert.strictEqual(view.get("toast").hidden, true);
    assert.deepStrictEqual(JSON.parse(view.stored[STORE_HIDDEN]), {});
    assert.strictEqual(view.get("hidden-fieldset").hidden, true);
    hideButtons(view)[1].fire("click");
    assert.deepStrictEqual(titles(view), ["Alpha", "Gamma"]);
    view.timeouts[view.timeouts.length - 1]();                            // six seconds later
    assert.strictEqual(view.get("toast").hidden, true);
    assert.strictEqual(hideButtons(view)[0].textContent, "Hide Listing");
    assert.strictEqual(hideButtons(view)[0].attributes["aria-label"], "Hide Listing: Alpha");
  }),
  check("a hidden home stays hidden: after a data refresh (also with a new uid), and in a new session", async function () {
    var first = oneSearch([located("Alpha", 500, 52.0, 5.0), located("Beta", 600, 52.1, 5.1)], {});
    first.last_run = old;
    var again = JSON.parse(JSON.stringify(first)); again.last_run = recent;
    var moved = JSON.parse(JSON.stringify(first)); moved.last_run = fresh;
    moved.listings[0].uid = "other-site:123";                              // found again on another site: same home_key
    moved.listings[0].url = "https://example.com/other";
    var view = dashboard([["data.json", first], ["data.json", again], ["data.json", moved]]);
    await settle();
    hideButtons(view)[0].fire("click");
    assert.deepStrictEqual(titles(view), ["Beta"]);
    await view.tick(refreshInterval);
    assert.deepStrictEqual(titles(view), ["Beta"]);
    assert.strictEqual(view.get("count").textContent, "1 listing");
    await view.tick(refreshInterval);
    assert.deepStrictEqual(titles(view), ["Beta"]);
    assert.strictEqual(view.get("hidden-count").textContent, "1");
    var next = dashboard([["data.json", first]], null, { [STORE_HIDDEN]: view.stored[STORE_HIDDEN] });
    await settle();
    assert.deepStrictEqual(titles(next), ["Beta"]);
    assert.strictEqual(next.get("hidden-fieldset").hidden, false);
    // The key changes while the listing stays the same one: first only the street was known ("u:" + its id), then
    // its page gave the house number and with it a key of its own. The listing's id still finds the hidden home.
    var street = oneSearch([priced("Street", 500, { uid: "dw:9", home_key: "u:dw:9" }), priced("Stay", 510, { uid: "dw:10", home_key: "u:dw:10" })]);
    street.last_run = old;
    var numbered = JSON.parse(JSON.stringify(street)); numbered.last_run = recent;
    numbered.listings[0].home_key = "a:den-haag:escamplaan:12";
    var rekeyed = dashboard([["data.json", street], ["data.json", numbered]]);
    await settle();
    hideButtons(rekeyed)[0].fire("click");
    assert.deepStrictEqual(titles(rekeyed), ["Stay"]);
    await rekeyed.tick(refreshInterval);
    assert.deepStrictEqual(titles(rekeyed), ["Stay"]);                    // still hidden under its new key
    assert.strictEqual(rekeyed.get("hidden-count").textContent, "1");
    // a home with no home_key is hidden by its uid
    var plain = priced("Plain", 500, { uid: "x1" });
    var third = dashboard([["data.json", oneSearch([plain, priced("Other", 500, { uid: "x2" })])]]);
    await settle();
    hideButtons(third)[0].fire("click");
    assert.deepStrictEqual(Object.keys(JSON.parse(third.stored[STORE_HIDDEN])), ["x1"]);
  }),
  check("Hidden Homes: the list of them, Show Again, Show All Again", async function () {
    var a = located("Alpha", 500, 52.0, 5.0, { address: "Canal 1" });
    var view = dashboard([["data.json", oneSearch([a, located("Beta", 600, 52.1, 5.1), located("Gamma", 700, 52.2, 5.2)])]]);
    await settle();
    hideButtons(view)[0].fire("click");
    view.advance(1000);
    hideButtons(view)[0].fire("click");
    assert.deepStrictEqual(titles(view), ["Gamma"]);
    view.click("open-hidden");
    assert.strictEqual(view.get("hidden-sheet").attributes.open, "");
    var rowsShown = view.get("hidden-list").children;
    assert.deepStrictEqual(rowsShown.map(function (r) { return r.textContent; }), ["BetaShow Again", "AlphaCanal 1Show Again"]);   // newest first
    descendants(rowsShown[1], function (n) { return n.tagName === "button"; })[0].fire("click");   // Alpha
    assert.deepStrictEqual(titles(view), ["Alpha", "Gamma"]);
    assert.strictEqual(view.get("hidden-count").textContent, "1");
    assert.strictEqual(view.get("hidden-list").children.length, 1);
    hideButtons(view)[0].fire("click");
    view.click("hidden-all");
    assert.deepStrictEqual(titles(view), ["Alpha", "Beta", "Gamma"]);
    assert.strictEqual(view.get("hidden-fieldset").hidden, true);
    assert.deepStrictEqual(JSON.parse(view.stored[STORE_HIDDEN]), {});
    assert.strictEqual(view.get("hidden-sheet").attributes.open, undefined);   // nothing left to show: the sheet closes
  }),
  check("a broken or missing stored value hides nothing; old keys of gone homes are dropped after 60 days", async function () {
    var listings = [located("Alpha", 500, 52.0, 5.0), located("Beta", 600, 52.1, 5.1)];
    var broken = ["{nope", "[1,2]", "\"text\"", "{\"hk-Alpha\":5}", "null", ""];
    for (var i = 0; i < broken.length; i++) {
      var view = dashboard([["data.json", oneSearch(listings)]], null, { [STORE_HIDDEN]: broken[i] });
      await settle();
      assert.deepStrictEqual(titles(view), ["Alpha", "Beta"], broken[i]);
      assert.strictEqual(view.get("hidden-fieldset").hidden, true);
    }
    var kept = dashboard([["data.json", oneSearch(listings)]], null, { [STORE_HIDDEN]: JSON.stringify({
      "hk-Alpha": { t: "Alpha", a: "", at: "2025-01-01T00:00:00Z" },       // old, but still in the data: stays
      "gone-old": { t: "Old", a: "", at: "2025-10-01T00:00:00Z" },          // 92 days: dropped
      "gone-new": { t: "New", a: "Dam 1", at: "2025-12-20T00:00:00Z" },     // 12 days: kept
      "gone-odd": { t: "Odd", a: "", at: "yesterday" }                      // no readable time: dropped
    }) });
    await settle();
    assert.deepStrictEqual(Object.keys(JSON.parse(kept.stored[STORE_HIDDEN])).sort(), ["gone-new", "hk-Alpha"]);
    assert.deepStrictEqual(titles(kept), ["Beta"]);
    assert.strictEqual(kept.get("hidden-count").textContent, "2");
  }),
  check("the map: one marker per located listing, a label for each, markers on one point do not cover each other", async function () {
    var seen = fakeLeaflet();
    var view = dashboard([["data.json", oneSearch([
      located("Alpha", 750, 52.0, 5.0, { bedrooms: 2, address: "Sneeuwbalstraat 48" }),
      located("Same point", 780, 52.0, 5.0),
      located("Third", 700, 52.0, 5.0, { geo: "postcode" }),
      located("Beta", 600, 52.5, 5.5),
      Object.assign(home("Gamma no price", [match(2, null)]), { lat: 52.3, lon: 5.3, uid: "g", home_key: "g" }),
      home("Nowhere", [match(2, 500)])
    ])]], null, null, { L: seen.L });
    await settle();
    assert.strictEqual(seen.maps.length, 0);                              // nothing is loaded for the list
    assert.strictEqual(view.get("map-pane").hidden, true);
    view.mode("map");
    assert.strictEqual(view.get("map-pane").hidden, false);
    assert.strictEqual(seen.maps.length, 1);
    var map = seen.maps[0];
    assert.strictEqual(seen.tiles[0].url, "https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/standaard/EPSG:3857/{z}/{x}/{y}.png");
    assert.strictEqual(seen.tiles[0].options.maxZoom, 19);
    assert.ok(seen.tiles[0].options.attribution.includes("Kaartgegevens © Kadaster") && seen.tiles[0].options.attribution.includes("https://www.pdok.nl"));
    assert.strictEqual(seen.markers.length, 5);                            // not the home without a location
    assert.deepStrictEqual(pins(seen).sort(), ["?", "€600", "€700", "€750", "€780"]);
    assert.strictEqual(map.fits.length, 1);                                // it opens fitted to the markers
    assert.strictEqual(view.get("search-area").hidden, true);              // fitting is not the user moving the map
    assert.strictEqual(view.get("map-note").textContent, "1 listing has no known location and is not on the map");
    assert.strictEqual(view.get("map-note").hidden, false);
    var first = seen.markers.filter(function (m) { return m.options.title.indexOf("Sneeuwbalstraat 48") >= 0; })[0];
    assert.strictEqual(first.options.title, "€750, 2 bedrooms, Sneeuwbalstraat 48");
    assert.strictEqual(first.options.keyboard, true);
    var anchors = seen.markers.filter(function (m) { return m.latlng[0] === 52.0; }).map(function (m) { return JSON.stringify(m.icon.iconAnchor); });
    assert.strictEqual(anchors.length, 3);
    assert.strictEqual(new Set(anchors).size, 3, anchors.join(" "));        // fanned out: all three can be reached
    var approx = seen.markers.filter(function (m) { return m.options.title.indexOf("approximate location") >= 0; });
    assert.strictEqual(approx.length, 1);
    assert.ok(pillClass(approx[0]).indexOf("approx") >= 0);
  }),
  check("Search This Area: moving the map offers it, pressing it filters list and count, the chip removes it", async function () {
    var seen = fakeLeaflet();
    var view = dashboard([["data.json", oneSearch([
      located("Inside A", 500, 52.0, 5.0), located("Inside B", 600, 52.05, 5.05), located("Edge", 650, 52.1, 5.1),
      located("Outside", 700, 53.5, 6.5), home("No place", [match(2, 400)])
    ])]], null, null, { L: seen.L });
    await settle();
    view.mode("map");
    var map = seen.maps[0];
    assert.strictEqual(view.get("search-area").hidden, true);
    map.bounds = { s: 51.9, w: 4.9, n: 52.1, e: 5.1 };                     // the user moves the map; its edges count as inside
    map.fire("moveend");
    assert.strictEqual(view.get("search-area").hidden, false);
    assert.strictEqual(titles(view).length, 5);                            // nothing changes until it is pressed
    view.click("search-area");
    assert.deepStrictEqual(titles(view).sort(), ["Edge", "Inside A", "Inside B"]);   // listings without a place are not inside
    assert.strictEqual(view.get("count").textContent, "3 of 5 listings");
    assert.deepStrictEqual(view.used(), ["Map Area"]);
    assert.strictEqual(view.get("search-area").hidden, true);              // hidden until the map moves again
    assert.strictEqual(seen.markers.length, 4);                            // the markers are the filtered listings, not only the area
    assert.strictEqual(view.get("filter-count").hidden, true);             // it is not a setting of the Filters form
    assert.strictEqual(JSON.parse(view.stored["househunter.filters.v1"]).area, undefined);   // and it is not remembered
    // it is kept while switching between List and Map
    view.mode("list");
    assert.strictEqual(view.get("map-pane").hidden, true);
    assert.deepStrictEqual(titles(view).sort(), ["Edge", "Inside A", "Inside B"]);
    view.mode("map");
    assert.deepStrictEqual(view.used(), ["Map Area"]);
    assert.strictEqual(seen.maps.length, 1);                               // the same map comes back
    assert.strictEqual(view.get("search-area").hidden, true);
    // the next area replaces it
    map.bounds = { s: 53, w: 6, n: 54, e: 7 };
    map.fire("moveend");
    view.click("search-area");
    assert.deepStrictEqual(titles(view), ["Outside"]);
    // removing the chip shows everything again
    view.remove(0);
    assert.strictEqual(titles(view).length, 5);
    assert.strictEqual(view.get("count").textContent, "5 listings");
    // the area is a filter like the others: Clear All takes it too
    map.fire("moveend");
    view.click("search-area");
    view.attr("short_term", "hide");
    assert.deepStrictEqual(view.used(), ["Map Area", "HideShort-Term Only"]);
    var all = view.get("applied").children[2];
    assert.strictEqual(all.textContent, "Clear All");
    all.fire("click");
    assert.deepStrictEqual(view.used(), []);
    assert.strictEqual(titles(view).length, 5);
    // it is not remembered across a page reload
    map.fire("moveend");
    view.click("search-area");
    var reloaded = dashboard([["data.json", oneSearch([located("Inside A", 500, 52.0, 5.0)])]], JSON.parse(view.stored["househunter.filters.v1"]), null, { L: fakeLeaflet().L });
    await settle();
    assert.deepStrictEqual(reloaded.used(), []);
  }),
  check("the map shows what the list would: same filters, search and hidden homes", async function () {
    var seen = fakeLeaflet();
    var view = dashboard([["data.json", oneSearch([
      located("Alpha", 500, 52.0, 5.0), located("Beta", 600, 52.1, 5.1, { flags: ["short_term"] }),
      located("Gamma", 700, 52.2, 5.2), located("Delta", 790, 52.3, 5.3)
    ])]], null, null, { L: seen.L });
    await settle();
    view.mode("map");
    assert.strictEqual(seen.markers.length, 4);
    view.attr("short_term", "hide");
    assert.deepStrictEqual(pins(seen).sort(), ["€500", "€700", "€790"]);
    view.change("maxpp", "750", "input"); view.get("maxpp").fire("change");
    assert.deepStrictEqual(pins(seen).sort(), ["€500", "€700"]);
    view.change("search", "gam", "input");
    assert.deepStrictEqual(pins(seen), ["€700"]);
    view.change("search", "", "input");
    hideButtons(view)[0].fire("click");                                    // Alpha: gone from the list and from the map
    assert.deepStrictEqual(titles(view), ["Gamma"]);
    assert.deepStrictEqual(pins(seen), ["€700"]);
    view.click("toast-undo");
    assert.deepStrictEqual(pins(seen).sort(), ["€500", "€700"]);
    assert.strictEqual(view.get("map-note").hidden, true);
  }),
  check("Show on Map in the list opens the map on that home", async function () {
    var seen = fakeLeaflet();
    var view = dashboard([["data.json", oneSearch([
      located("Alpha", 500, 52.0, 5.0), located("Beta", 600, 52.1, 5.1)
    ])]], null, null, { L: seen.L });
    await settle();
    assert.strictEqual(view.get("map-pane").hidden, true);                 // the list is showing
    byClass(view, "showmap")[1].fire("click");
    await settle();
    assert.strictEqual(view.get("map-pane").hidden, false);
    assert.strictEqual(view.get("view-map").checked, true);
    var map = seen.maps[0], beta = seen.markers.filter(function (m) { return m.latlng[0] === 52.1; })[0];
    assert.strictEqual(JSON.stringify(map.views[map.views.length - 1].slice(0, 2)), "[[52.1,5.1],15]");
    assert.ok(pillClass(beta).indexOf("sel") >= 0);
    assert.strictEqual(view.get("search-area").hidden, true);
  }),
  check("the budget slider and Cheapest First count the costs that come on top of the rent", async function () {
    var view = dashboard([["data.json", oneSearch([
      Object.assign(home("Costly", [Object.assign(match(2, 650), { extra_pp: 100 })]), { first_seen: "2026-11-03T10:00:00Z" }),
      Object.assign(home("Plain", [match(2, 690)]), { first_seen: "2026-11-02T10:00:00Z" }),
      Object.assign(home("Unknown", [match(2, null)]), { price_total: 1300, first_seen: "2026-11-01T10:00:00Z" })
    ])]]);
    await settle();
    assert.deepStrictEqual(titles(view), ["Costly", "Plain", "Unknown"]);
    view.change("maxpp", "700", "input");
    view.get("maxpp").fire("change");
    await settle();
    assert.deepStrictEqual(titles(view), ["Plain", "Unknown"]);           // 650 + 100 is over 700; an unknown rent stays
    view.change("maxpp", "800", "input");
    view.get("maxpp").fire("change");
    view.change("sort", "cheapest");
    await settle();
    assert.deepStrictEqual(titles(view), ["Plain", "Costly", "Unknown"]);  // 690 before 750
  }),
  check("selecting a marker shows its listing; Show on Map centres and selects", async function () {
    var seen = fakeLeaflet();
    var view = dashboard([["data.json", oneSearch([
      located("Alpha", 500, 52.0, 5.0, { url: "https://example.com/alpha", bedrooms: 2, area_m2: 70 }),
      located("Beta", 600, 52.1, 5.1), home("Nowhere", [match(2, 500)])
    ])]], null, null, { L: seen.L });
    await settle();
    assert.strictEqual(byClass(view, "showmap").length, 2);                // only homes with a place get the control
    view.mode("map");
    var map = seen.maps[0], alpha = seen.markers.filter(function (m) { return m.latlng[0] === 52.0; })[0];
    var card = view.get("map-card");
    assert.strictEqual(card.hidden, true);
    alpha.click();
    assert.strictEqual(card.hidden, false);
    assert.ok(pillClass(alpha).indexOf("sel") >= 0);
    assert.strictEqual(alpha.z, 1000);
    assert.ok(card.textContent.includes("Alpha") && card.textContent.includes("€500") && card.textContent.includes("2 bedrooms · 70 m²"), card.textContent);
    var open = descendants(card, function (n) { return n.tagName === "a"; })[0];
    assert.strictEqual(open.textContent, "Open Listing");
    assert.strictEqual(open.attributes.href, "https://example.com/alpha");
    var row = view.get("listings").children[0];
    assert.strictEqual(row.attributes["data-picked"], "");                 // its card in the list is marked
    assert.strictEqual(view.get("listings").children[1].attributes["data-picked"], undefined);
    descendants(card, function (n) { return n.tagName === "button"; })[0].fire("click");   // the close button
    assert.strictEqual(card.hidden, true);
    assert.ok(pillClass(alpha).indexOf("sel") < 0);
    assert.strictEqual(row.attributes["data-picked"], undefined);
    // Show on Map on the second card
    var beta = seen.markers.filter(function (m) { return m.latlng[0] === 52.1; })[0];
    byClass(view, "showmap")[1].fire("click");
    assert.strictEqual(JSON.stringify(map.views[map.views.length - 1].slice(0, 2)), "[[52.1,5.1],15]");
    assert.ok(pillClass(beta).indexOf("sel") >= 0);
    assert.strictEqual(view.get("search-area").hidden, true);              // centring is not the user moving the map
    assert.strictEqual(card.hidden, false);
    map.fire("click");                                                      // a tap on the empty map clears the selection
    assert.strictEqual(card.hidden, true);
    // hiding the selected home clears the selection
    beta.click();
    byClass(view, "hide")[1].fire("click");
    assert.strictEqual(card.hidden, true);
    assert.strictEqual(seen.markers.length, 1);
  }),
  check("the list works when the map cannot be loaded, or breaks", async function () {
    var view = dashboard([["data.json", oneSearch([located("Alpha", 500, 52.0, 5.0)])]]);   // no Leaflet, and no way to load it
    await settle();
    view.mode("map");
    assert.strictEqual(view.get("map-error").hidden, false);
    assert.strictEqual(view.get("map").hidden, true);
    assert.deepStrictEqual(titles(view), ["Alpha"]);
    view.attr("short_term", "hide");                                       // the list keeps working
    assert.deepStrictEqual(titles(view), ["Alpha"]);
    view.mode("list");
    assert.strictEqual(view.get("map-pane").hidden, true);
    var broken = fakeLeaflet();
    broken.L.map = function () { throw new Error("no webgl"); };
    var second = dashboard([["data.json", oneSearch([located("Alpha", 500, 52.0, 5.0)])]], null, null, { L: broken.L });
    await settle();
    second.mode("map");
    assert.strictEqual(second.get("map-error").hidden, false);
    second.change("search", "alp", "input");
    assert.deepStrictEqual(titles(second), ["Alpha"]);
  })
);

async function run() {
  for (var i = 0; i < checks.length; i++) {
    var current = checks[i];
    try {
      await current.run();
      console.log("PASS " + current.name);
    } catch (error) {
      console.error("FAIL " + current.name + "\n" + error.stack);
      process.exitCode = 1;
    }
  }
}

run();
