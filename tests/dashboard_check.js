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
}
Element.prototype = Object.create(EventTarget.prototype);
Element.prototype.appendChild = function (child) { this.children.push(child); };
Element.prototype.setAttribute = function (name, value) { this.attributes[name] = String(value); };
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

function dashboard(responses) {
  var elements = {}, intervals = {}, timeouts = [], calls = [], copied = [], stored = {};
  var clock = now;
  var document = new EventTarget();
  var table = new Element("tbody");
  document.hidden = false;
  document.visibilityState = "visible";
  document.getElementById = function (id) {
    return elements[id] || (elements[id] = new Element(id));
  };
  document.createElement = function (tag) { return new Element(tag); };
  document.querySelector = function (selector) {
    assert.strictEqual(selector, "#sources tbody");
    return table;
  };
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
      setItem: function (key, value) { stored[key] = value; }
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
    setTimeout: function (callback) { timeouts.push(callback); }
  };
  window.window = window;
  vm.runInNewContext(script, window, { filename: "docs/app.js" });
  return {
    get: document.getElementById,
    table: table,
    calls: calls,
    copied: copied,
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
    }
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

function check(name, run) {
  return { name: name, run: run };
}

var checks = [
  check("unknown per-person and total price text", async function () {
    var view = dashboard([["data.json", payload([home("Unknown", [match(2, null)])])]]);
    await settle();
    var text = view.get("listings").textContent;
    assert.ok(text.includes("Price unknown"), text);
    assert.ok(text.includes("Price per person unknown (2 people)"), text);
    assert.ok(!text.includes("€0"), text);
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
    view.change("group", "3");
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
      view.change("group", group);
      assert.deepStrictEqual(titles(view), ["Known newer", "Known older", "Unknown newer", "Unknown older"]);
    });
  }),
  check("tier badge follows the selected group", async function () {
    var view = dashboard([["data.json", payload([home("Different tiers", [
      match(2, 600), match(3, 400, "great")
    ])])]]);
    await settle();
    function tier() { return view.get("listings").children[0].children[0].children[0].children[0].textContent; }
    assert.strictEqual(tier(), "great");
    view.change("group", "2");
    assert.strictEqual(tier(), "ok");
    view.change("group", "3");
    assert.strictEqual(tier(), "great");
  }),
  check("unknown price stays visible at the slider minimum", async function () {
    var view = dashboard([["data.json", payload([
      home("Unknown", [match(2, null)]), home("Over budget", [match(2, 401)])
    ])]]);
    await settle();
    view.change("maxpp", "400", "input");
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
    var chip = view.get("city-chips").children[0];
    chip.fire("click");
    view.change("group", "3");
    await view.tick(refreshInterval);
    assert.strictEqual(view.get("stale-warning").hidden, true);
    assert.strictEqual(view.get("last-check").textContent, "Last check: 2 min ago");
    assert.deepStrictEqual(titles(view), ["New for three"]);
    assert.ok(view.table.textContent.includes("Updated source"));
    assert.strictEqual(view.get("city-chips").children[0], chip);
    assert.strictEqual(chip.attributes["aria-pressed"], "true");
    assert.strictEqual(view.get("group").listeners.change.length, 1);
    view.advance(60000);
    await view.tick(60000);
    assert.strictEqual(view.get("last-check").textContent, "Last check: 3 min ago");
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
      assert.ok(view.table.textContent.includes("Original source"));
      assert.strictEqual(view.get("sample-banner").hidden, true);
      assert.strictEqual(view.get("stale-warning").hidden, false);
      assert.strictEqual(view.get("last-check").textContent, "Last check: 2 h ago");
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
    var chips = view.get("city-chips").children;
    assert.deepStrictEqual(chips.map(function (chip) { return chip.textContent; }), ["Delft"]);
    assert.strictEqual(chips[0].listeners.click.length, 1);
    assert.strictEqual(view.get("group").listeners.change.length, 1);
  }),
  check("copy reply uses title when the address is absent", async function () {
    var view = dashboard([["data.json", payload([home("Canal home", [match(2, 500)])])]]);
    await settle();
    var button = descendants(view.get("listings"), function (node) { return node.tagName === "button"; })[0];
    button.fire("click");
    await settle();
    assert.deepStrictEqual(view.copied, ["Interested in Canal home."]);
    assert.strictEqual(button.textContent, "Copied!");
    view.timeouts[0]();
    assert.strictEqual(button.textContent, "Copy reply");
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
