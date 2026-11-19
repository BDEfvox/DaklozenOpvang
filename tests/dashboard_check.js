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
function dashboard(responses, saved, storage) {
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
    // Who is renting: tenant-students, tenant-working and tenant-any.
    tenant: function (value) {
      ["students", "working", "any"].forEach(function (t) { document.getElementById("tenant-" + t).checked = t === value; });
      document.getElementById("tenant-" + value).fire("change");
    },
    toggle: function (id, on) {
      var control = document.getElementById(id);
      control.checked = on;
      control.fire("change");
    },
    click: function (id) { document.getElementById(id).fire("click"); }
  };
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
    var button = descendants(view.get("listings"), function (node) { return node.tagName === "button"; })[0];
    button.fire("click");
    await settle();
    assert.deepStrictEqual(view.copied, ["Interested in Canal home."]);
    assert.strictEqual(button.textContent, "Copied");
    view.timeouts[0]();
    assert.strictEqual(button.textContent, "Copy Reply");
  }),
  check("singular bedroom and room labels", async function () {
    var listing = home("One of each", [match(2, 500)]);
    listing.bedrooms = 1;
    listing.rooms = 1;
    var view = dashboard([["data.json", payload([listing])]]);
    await settle();
    var facts = descendants(view.get("listings"), function (node) { return node.className === "facts"; });
    assert.strictEqual(facts[0].textContent, "1 bedroom · 1 room");
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
    view.toggle("hide-housemates", true);
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
    assert.strictEqual(view.get("hide-housemates").checked, false);
    assert.strictEqual(view.get("tenant-any").checked, true);
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
    view.toggle("hide-warnings", true);
    assert.deepStrictEqual(titles(view), ["For three"]);
    view.click("filters-cancel");
    assert.deepStrictEqual(titles(view), ["For two"]);
    assert.strictEqual(view.get("group-2").checked, true);
    assert.strictEqual(view.get("hide-warnings").checked, false);
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
    assert.strictEqual(byClass(view, "facts")[0].textContent, "€1.000 per month excl. utilities · 1 Mar to 10 Jan 2027");
    assert.deepStrictEqual(byClass(view, "warn").map(function (node) { return node.textContent; }), ["no students", "Max 2 persons"]);
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
    assert.strictEqual(view.get("tenant-thumb").style.transform, "translateX(200.00%)");   // "Any" is last there too
  }),
  check("a 'great prices only' choice saved by an older version no longer hides anything", async function () {
    var view = dashboard([["data.json", payload([home("Great", [match(2, 450, "great")]), home("Fine", [match(2, 700)])])]],
      { cities: [], group: "any", maxpp: 800, onlyGreat: true, hideWarn: false, q: "", sort: "newest" });
    await settle();
    assert.deepStrictEqual(titles(view), ["Great", "Fine"]);
    assert.strictEqual(view.get("filter-count").hidden, true);
    assert.deepStrictEqual(byClass(view, "tier").map(function (node) { return node.textContent; }), ["Great price"]);
  }),
  check("who is renting, age and sharing filters act only on what a listing states", async function () {
    function flagged(title, flags) { var l = home(title, [match(2, 500)]); l.flags = flags; return l; }
    var view = dashboard([["data.json", payload([
      flagged("Silent", []), flagged("Students only", ["students_only"]), flagged("No students", ["no_students"]),
      flagged("Working only", ["working_only"]), flagged("From 25", ["min_age:25"]), flagged("Up to 27", ["max_age:27"]),
      flagged("No sharers", ["no_sharers"]), flagged("Sharers welcome", ["sharers_ok"]), flagged("Housemates", ["housemates"])
    ])]]);
    await settle();
    assert.strictEqual(titles(view).length, 9);
    view.tenant("students");
    assert.deepStrictEqual(titles(view), ["Silent", "Students only", "From 25", "Up to 27", "No sharers", "Sharers welcome", "Housemates"]);
    view.tenant("working");
    assert.deepStrictEqual(titles(view), ["Silent", "No students", "Working only", "From 25", "Up to 27", "No sharers", "Sharers welcome", "Housemates"]);
    view.tenant("any");
    view.change("age", "2", "input");                                // half a number is no age yet
    assert.strictEqual(titles(view).length, 9);
    view.change("age", "23", "input");
    assert.ok(titles(view).indexOf("From 25") < 0 && titles(view).indexOf("Up to 27") >= 0, titles(view).join());
    view.change("age", "30", "input");
    assert.ok(titles(view).indexOf("From 25") >= 0 && titles(view).indexOf("Up to 27") < 0, titles(view).join());
    view.change("age", "27x", "input");                              // only digits count; 27 is still within "up to 27"
    assert.strictEqual(view.get("age").value, "27");
    assert.strictEqual(titles(view).length, 9);
    view.change("age", "", "input");
    view.toggle("hide-no-sharers", true);
    assert.ok(titles(view).indexOf("No sharers") < 0 && titles(view).length === 8, titles(view).join());
    view.toggle("hide-no-sharers", false);
    view.toggle("only-sharers-ok", true);
    assert.deepStrictEqual(titles(view), ["Sharers welcome"]);
    view.toggle("only-sharers-ok", false);
    view.toggle("hide-housemates", true);
    assert.ok(titles(view).indexOf("Housemates") < 0 && titles(view).length === 8, titles(view).join());
    assert.strictEqual(view.get("filter-count").textContent, "1");
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
