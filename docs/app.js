"use strict";
// All data comes from third-party sites: only textContent / setAttribute are used, never innerHTML.
(function () {
  var STORE = "househunter.filters.v1", REPLY = "househunter.reply.v1";
  var SVG = "http://www.w3.org/2000/svg";
  var WARN = ["no_sharers", "max_persons", "no_students", "no_registration", "suspicious_price", "scam_signals"];
  var GOOD = ["sharers_ok", "students_ok"];
  // Who may rent. Not a warning in itself: whether it matters depends on who you are, which the filters ask.
  var REQ = ["students_only", "working_only", "women_only", "men_only", "min_age", "max_age", "housemates",
    "price_per_person", "mandatory_costs", "service_costs", "rent_for_2", "rent_for_3", "rent_for_4"];
  // In the order the choices stand in the control: the thumb's position is the index. "Any" comes last.
  var GROUPS = ["2", "3", "any"];
  // Everything a listing can say about itself is a filter of its own: "only" keeps the listings that say it, "hide"
  // removes them, and any number of them can be in use at once. [key, label, what makes a listing "say it"]: the
  // flags that mean it (default: the flag named like the key), or a function for facts that are not flags.
  var ATTR_GROUPS = [
    ["Who May Rent", [
      ["students_only", "Students Only"], ["no_students", "No Students"], ["students_ok", "Students Welcome"],
      ["working_only", "Working People Only"], ["women_only", "Women Only"], ["men_only", "Men Only"],
      ["age_limit", "Age Limit", ["min_age", "max_age"]]]],
    ["House Sharing", [
      ["no_sharers", "No House Sharers"], ["sharers_ok", "Sharers Welcome"], ["housemates", "Shared with Housemates"],
      ["max_persons", "Limit on People"]]],
    ["Rent and Costs", [
      ["utilities_included", "Utilities Included"], ["service_costs", "Service Costs on Top"],
      ["mandatory_costs", "Compulsory Extra Costs"], ["price_per_person", "Rent per Person"],
      ["price_varies", "Rent Depends on Month or Group", ["price_varies", "rent_for_2", "rent_for_3", "rent_for_4"]],
      ["price_drop", "Price Dropped", function (l) { return typeof l.price_drop_from === "number"; }]]],
    ["Home and Contract", [
      ["furnished", "Furnished", function (l) { return l.furnished === "furnished"; }],
      ["short_term", "Short-Term Only"], ["no_registration", "No Registration"],
      ["income_requirement", "Income Requirement"]]],
    ["Warnings", [
      ["any_warning", "Any Warning", WARN], ["suspicious_price", "Suspiciously Cheap"], ["scam_signals", "Possible Scam"],
      ["bedrooms_unsure", "Bedrooms Not Confirmed", ["bedrooms_unknown", "bedrooms_unconfirmed"]],
      ["price_unknown", "Price Unknown"]]]
  ];
  var ATTR = {}, MODES = { only: "Only", hide: "Hide" };
  ATTR_GROUPS.forEach(function (g) {
    g[1].forEach(function (a) { ATTR[a[0]] = { label: a[1], says: a[2] || [a[0]] }; });
  });
  // Details of a listing that the reply text can hold: {title}, {address}, ...
  var FIELDS = [["title", "Title"], ["address", "Address"], ["city", "City"], ["price", "Rent"], ["bedrooms", "Bedrooms"],
    ["area", "Size"], ["source", "Site"], ["url", "Link"]];
  // Source status: the word shown and the icon that goes with it (never colour alone).
  var STATUS = {
    ok: ["Working", "i-good"], partial: ["Partly working", "i-part"], blocked: ["Blocked", "i-fail"],
    error: ["Not working", "i-fail"], disabled: ["Off", "i-off"]
  };
  var data = null, isSample = false, refreshing = false, working = "";
  var cityInputs = {}, cityCounts = {}, beforeSheet = null, groupControl = null;
  var attrRows = {};                                           // the row of each "listing says" filter
  var shownList = [];                                          // the listings on screen now, in order
  var reply = null;                                            // the user's own reply text; null = the standard one
  // Each listing keeps its row, so a filter change moves rows instead of rebuilding them (photo and "Copied" stay).
  var rows = typeof WeakMap === "function" ? new WeakMap() : null, moving = [];
  var photos = typeof Map === "function" ? new Map() : null;   // one photo element per listing, kept when its row is rebuilt
  var leaving = null;                                          // the sheet that is animating out, if any
  // attrs: the "listing says" filters in use, by key: "only" or "hide". A key that is not there is not in use.
  var f = { cities: [], group: "any", maxpp: 800, age: null, attrs: {}, q: "", sort: "newest" };
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(name, cls) {
    var s = document.createElementNS(SVG, "svg"), u = document.createElementNS(SVG, "use");
    s.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("focusable", "false");
    u.setAttribute("href", "#" + name);
    s.appendChild(u);
    return s;
  }

  // ---------- Motion ----------
  var raf = window.requestAnimationFrame ? function (cb) { return window.requestAnimationFrame(cb); } : null;
  var reduce = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  function calm() { return !!(reduce && reduce.matches); }           // Reduce Motion is on
  function clock() { return window.performance && window.performance.now ? window.performance.now() : Date.now(); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  // Pulling past an edge: the further you pull, the less it gives, up to `limit`.
  function band(d, limit) { return (1 - 1 / (d * 0.55 / limit + 1)) * limit; }
  // Speed at the end of a drag, from the last tenth of a second of finger positions ([time, position] pairs).
  function speed(hist) {
    var last = hist[hist.length - 1], i = 0;
    while (i < hist.length - 1 && last[0] - hist[i][0] > 100) i++;
    var dt = (last[0] - hist[i][0]) / 1000;
    return dt > 0.008 ? (last[1] - hist[i][1]) / dt : 0;
  }
  // A spring, set the way Apple sets them: `duration` is the pace and `bounce` the character (0 = no overshoot).
  // It can start with the speed of a finger, and it can be stopped or replaced at any moment without a jump.
  function spring(o) {
    var w = 2 * Math.PI / o.duration, z = 1 - (o.bounce || 0), d0 = o.from - o.to, v0 = o.velocity || 0;
    var rest = o.rest || 0.5, t0 = clock(), live = true, value = o.from, vel = v0;
    function gap(t) {
      if (z >= 1) return (d0 + (v0 + w * d0) * t) * Math.exp(-w * t);
      var wd = w * Math.sqrt(1 - z * z);
      return Math.exp(-z * w * t) * (d0 * Math.cos(wd * t) + ((v0 + z * w * d0) / wd) * Math.sin(wd * t));
    }
    function finish() { live = false; value = o.to; vel = 0; o.step(o.to); if (o.done) o.done(); }
    function frame() {
      if (!live) return;
      var t = (clock() - t0) / 1000, d = gap(t);
      vel = (gap(t + 0.001) - d) * 1000;
      value = o.to + d;
      if (t > 3 || (Math.abs(d) < rest && Math.abs(vel) < rest * 10)) { finish(); return; }
      o.step(value);
      raf(frame);
    }
    if (!raf) finish(); else { o.step(o.from); raf(frame); }
    return { stop: function () { live = false; }, velocity: function () { return live ? vel : 0; } };
  }
  // The CSS spring curve, for animations the browser runs itself (rows moving in the list).
  function curve(name, fallback) {
    try { return window.getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback; }
    catch (e) { return fallback; }
  }
  function animate(node, frames, timing) {
    try { var a = node.animate(frames, timing); moving.push(a); return a; }
    catch (e) { try { timing.easing = "ease-out"; var b = node.animate(frames, timing); moving.push(b); return b; } catch (e2) { return null; } }
  }
  // A light tap on phones that can give one. iPhones have no vibration API, but Safari gives the system tick when a
  // switch-style checkbox is toggled through its label, so a hidden one is used for that. Elsewhere nothing happens.
  function tick() {
    try {
      if (navigator.vibrate) { navigator.vibrate(8); return; }
      var host = (document.querySelector && document.querySelector("dialog[open]:not([inert])")) || document.head;
      if (!host) return;
      var label = document.createElement("label"), input = document.createElement("input");
      input.type = "checkbox"; input.setAttribute("switch", "");
      label.style.display = "none"; label.setAttribute("aria-hidden", "true");
      label.appendChild(input); host.appendChild(label);
      label.click();
      host.removeChild(label);
    } catch (e) {}
  }
  // Swap a short text with a small roll, the way numbers change in the system. `quiet` swaps without it.
  function setText(node, text, quiet) {
    if (node.textContent === text) return;
    if (quiet) { node.textContent = text; return; }
    node.textContent = "";
    node.appendChild(el("span", "roll", text));
  }

  function safeUrl(u) {
    try { var p = new URL(String(u)); return (p.protocol === "http:" || p.protocol === "https:") ? p.href : null; }
    catch (e) { return null; }
  }
  function ts(s) { var t = Date.parse(s); return isNaN(t) ? null : t; }
  function rel(s) {
    var t = ts(s); if (t == null) return "unknown";
    var m = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (m < 1) return "just now";
    if (m < 60) return m + " min ago";
    var h = Math.floor(m / 60);
    if (h < 48) return h + " h ago";
    return Math.floor(h / 24) + " days ago";
  }
  function exact(s) { var t = ts(s); return t == null ? "" : new Date(t).toLocaleString(); }
  // "2026-12-15" becomes "15 Dec" (with the year when it is not this year). Anything else is shown as it is.
  function day(s) {
    var t = /^\d{4}-\d{2}-\d{2}$/.test(String(s)) ? ts(s + "T00:00:00Z") : null;
    if (t == null) return String(s);
    var d = new Date(t), o = { day: "numeric", month: "short", timeZone: "UTC" };
    if (d.getUTCFullYear() !== new Date(Date.now()).getFullYear()) o.year = "numeric";
    return d.toLocaleDateString("en-GB", o);
  }
  function euro(n) { return "€" + Math.round(n).toLocaleString("nl-NL"); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function flagKey(fl) { return String(fl).split(":")[0]; }
  // A listing's flags by name: {no_students: "", min_age: "25"}.
  function flagsOf(l) {
    var o = {};
    (l.flags || []).forEach(function (x) { var t = String(x), i = t.indexOf(":"); o[i < 0 ? t : t.slice(0, i)] = i < 0 ? "" : t.slice(i + 1); });
    return o;
  }
  // Two quotes cut from the same passage of a listing: one holds the other, or they share a long run of text.
  function samePassage(a, b) {
    a = a.replace(/…/g, "").trim(); b = b.replace(/…/g, "").trim();
    if (!a || !b) return false;
    var s = a.length <= b.length ? a : b, l = s === a ? b : a;
    if (l.indexOf(s) >= 0) return true;
    for (var i = 0; i + 40 <= s.length; i++) if (l.indexOf(s.substr(i, 40)) >= 0) return true;
    return false;
  }
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  // Does the listing say this? `fl` is flagsOf(l).
  function says(key, l, fl) {
    var what = ATTR[key].says;
    return typeof what === "function" ? !!what(l) : what.some(function (k) { return has(fl, k); });
  }
  function minPP(l) {
    var v = matchesFor(l).map(function (m) { return m.price_pp; }).filter(function (x) { return typeof x === "number"; });
    return v.length ? Math.min.apply(null, v) : Infinity;
  }
  function newestFirst(a, b) { return (ts(b.first_seen) || 0) - (ts(a.first_seen) || 0); }
  function cheapestFirst(a, b) {
    var pa = minPP(a), pb = minPP(b);
    return pa === pb ? newestFirst(a, b) : pa - pb;
  }

  function record(x) { return x != null && typeof x === "object" && !Array.isArray(x); }
  function records(xs) { return Array.isArray(xs) && xs.every(record); }
  function load(url) {
    return fetch(url, { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        if (!record(d) || !records(d.listings) ||
            (d.cities != null && !record(d.cities)) ||
            (d.sources != null && !records(d.sources)) ||
            (d.flag_labels != null && (!record(d.flag_labels) || Object.keys(d.flag_labels).some(function (k) {
              return typeof d.flag_labels[k] !== "string";
            }))) ||
            d.listings.some(function (l) {
              return (l.matches != null && !records(l.matches)) || (l.flags != null && !Array.isArray(l.flags));
            })) throw new Error("Invalid dashboard data");
        return d;
      });
  }

  function render(d, sample) {
    var previousCities = Object.keys(data && data.cities || {});
    var nextCities = Object.keys(d.cities || {});
    var first = data == null;
    data = d;
    isSample = sample;
    if (first || previousCities.length !== nextCities.length || nextCities.some(function (k) {
      return previousCities.indexOf(k) < 0;
    })) buildCities();
    if (first) { buildAttrs(); bind(); }
    countCities(); countAttrs(); sources(); header(); update();
    if (photos) {                                           // forget photos of listings that are gone
      var now = new Map();
      (d.listings || []).forEach(function (l) { var k = keyOf(l); if (photos.has(k)) now.set(k, photos.get(k)); });
      photos = now;
    }
  }

  function refresh() {
    if (refreshing) return;
    refreshing = true;
    load("data.json").then(function (d) {
      if (typeof d.last_run !== "string" || ts(d.last_run) == null) throw new Error("Invalid dashboard timestamp");
      // Nothing was saved since the data on screen: leave the rows alone (scroll position, "Copied").
      if (data && !isSample && d.last_run === data.last_run) { header(); return; }
      render(d, false);
    }).catch(function () {
      // Keep the last successful data, including its timestamp.
    }).then(function () { refreshing = false; });
  }

  function saveF() { try { localStorage.setItem(STORE, JSON.stringify(f)); } catch (e) {} }
  function loadF() {
    try {
      var s = JSON.parse(localStorage.getItem(STORE) || "null");
      if (s && typeof s === "object") {
        if (Array.isArray(s.cities)) f.cities = s.cities.filter(function (x) { return typeof x === "string"; });
        if (GROUPS.indexOf(s.group) >= 0) f.group = s.group;
        if (typeof s.maxpp === "number") f.maxpp = Math.min(800, Math.max(400, s.maxpp));
        if (typeof s.age === "number" && s.age >= 16 && s.age <= 99) f.age = Math.round(s.age);
        if (record(s.attrs)) {
          Object.keys(s.attrs).forEach(function (k) { if (has(ATTR, k) && has(MODES, s.attrs[k])) f.attrs[k] = s.attrs[k]; });
        } else {
          // Saved by the version with "Who's Renting" and the sharing switches: the same choices, as filters of their own.
          if (s.tenant === "students") { f.attrs.no_students = "hide"; f.attrs.working_only = "hide"; }
          if (s.tenant === "working") f.attrs.students_only = "hide";
          if (s.hideNoSharers === true) f.attrs.no_sharers = "hide";
          if (s.onlySharersOk === true) f.attrs.sharers_ok = "only";
          if (s.hideHousemates === true) f.attrs.housemates = "hide";
          if (s.hideWarn === true) f.attrs.any_warning = "hide";
        }
        if (typeof s.q === "string") f.q = s.q;
        if (s.sort === "cheapest") f.sort = "cheapest";
      }
    } catch (e) {}
  }
  // How many of the filters in the Filters form differ from "show everything".
  function activeFilters() {
    return (f.cities.length ? 1 : 0) + (f.group !== "any" ? 1 : 0) + (f.maxpp < 800 ? 1 : 0) + (f.age != null ? 1 : 0) +
      Object.keys(f.attrs).length;
  }
  function clearFilters() {
    f.cities = []; f.group = "any"; f.maxpp = 800; f.age = null; f.attrs = {}; f.q = "";
    syncControls(); update();
  }
  // One "listing says" filter: `mode` is "only" or "hide"; choosing the mode it already has turns it off.
  function setAttr(key, mode) {
    if (f.attrs[key] === mode || !mode) delete f.attrs[key]; else f.attrs[key] = mode;
    tick(); syncAttrs(); update();
  }
  // Put the filter values back into the controls (after Clear Filters or Cancel).
  function syncControls() {
    GROUPS.forEach(function (g) { $("group-" + g).checked = f.group === g; });
    Object.keys(cityInputs).forEach(function (k) { cityInputs[k].checked = f.cities.indexOf(k) >= 0; });
    $("maxpp").value = f.maxpp; $("age").value = f.age != null ? String(f.age) : "";
    $("search").value = f.q; $("sort").value = f.sort;
    if (groupControl) groupControl.settle();
    syncAttrs();
  }

  // The "listing says" rows: a name, how many listings say it, and its two buttons.
  function buildAttrs() {
    var box = $("attr-sections"); box.textContent = "";
    attrRows = {};
    ATTR_GROUPS.forEach(function (g) {
      var set = el("fieldset"), list = el("ul", "list");
      set.appendChild(el("legend", null, g[0]));
      g[1].forEach(function (a) {
        var key = a[0], li = el("li"), row = el("div", "row attr"), pair = el("div", "pair"), count = el("span", "value");
        var btns = {};
        pair.setAttribute("role", "group");
        pair.setAttribute("aria-label", a[1]);
        Object.keys(MODES).forEach(function (mode) {
          var b = el("button", "opt " + mode, MODES[mode]);
          b.type = "button";
          b.setAttribute("aria-pressed", "false");
          b.setAttribute("data-attr", key);
          b.setAttribute("data-mode", mode);
          b.addEventListener("click", function () { setAttr(key, mode); });
          btns[mode] = b;
          pair.appendChild(b);
        });
        row.appendChild(el("span", "label", a[1]));
        row.appendChild(count);
        row.appendChild(pair);
        li.appendChild(row);
        list.appendChild(li);
        li.hidden = false;
        attrRows[key] = { count: count, btns: btns };
      });
      set.appendChild(list);
      set.hidden = false;
      box.appendChild(set);
    });
    box.hidden = false;
  }
  // How many listings say each thing. A thing no listing says today keeps its row: listings change every few
  // minutes, and a filter set now also holds for the ones that come later.
  function countAttrs() {
    var n = {};
    (data && data.listings || []).forEach(function (l) {
      var fl = flagsOf(l);
      Object.keys(ATTR).forEach(function (k) { if (says(k, l, fl)) n[k] = (n[k] || 0) + 1; });
    });
    Object.keys(attrRows).forEach(function (k) { attrRows[k].count.textContent = String(n[k] || 0); });
  }
  function syncAttrs() {
    Object.keys(attrRows).forEach(function (k) {
      var r = attrRows[k], mode = f.attrs[k] || "";
      Object.keys(r.btns).forEach(function (m) { r.btns[m].setAttribute("aria-pressed", m === mode ? "true" : "false"); });
    });
  }

  function header() {
    var t = ts(data.last_run), stale = !isSample && (t == null || Date.now() - t > 2 * 3600 * 1000);
    var lc = $("last-check"), w = $("stale-warning");
    lc.textContent = t == null ? "Last check unknown" : "Checked " + rel(data.last_run);
    $("open-sources").title = exact(data.last_run);
    if (stale) w.textContent = "The last check was " + rel(data.last_run) + ". The checker may be down.";
    w.hidden = !stale;
    $("sample-banner").hidden = !isSample;
    $("status-dot").className = "dot " + (stale ? "bad" : working === "all" ? "good" : "warn");
  }

  function buildCities() {
    var box = $("city-chips"); box.textContent = "";
    cityInputs = {}; cityCounts = {};
    Object.keys(data.cities || {}).forEach(function (k) {
      var li = el("li"), row = el("label", "row"), input = el("input", "pick");
      input.type = "checkbox";
      input.checked = f.cities.indexOf(k) >= 0;
      input.addEventListener("change", function () {
        var i = f.cities.indexOf(k);
        if (input.checked && i < 0) f.cities.push(k);
        if (!input.checked && i >= 0) f.cities.splice(i, 1);
        tick(); update();
      });
      cityInputs[k] = input;
      cityCounts[k] = el("span", "value");
      row.appendChild(input);
      row.appendChild(el("span", "label", data.cities[k]));
      row.appendChild(cityCounts[k]);
      row.appendChild(icon("i-check", "check"));
      li.appendChild(row);
      box.appendChild(li);
    });
  }
  function countCities() {
    var n = {};
    (data.listings || []).forEach(function (l) { n[l.city] = (n[l.city] || 0) + 1; });
    Object.keys(cityCounts).forEach(function (k) { cityCounts[k].textContent = String(n[k] || 0); });
  }

  // The matches for the chosen group size, cheapest per person first (unknown prices last).
  function matchesFor(l) {
    return (l.matches || []).filter(function (m) { return f.group === "any" || String(m.persons) === f.group; })
      .sort(function (a, b) {
        return (typeof a.price_pp === "number" ? a.price_pp : Infinity) - (typeof b.price_pp === "number" ? b.price_pp : Infinity);
      });
  }
  function visible(l) {
    if (f.cities.length && f.cities.indexOf(l.city) < 0) return false;
    var ms = matchesFor(l);
    if (!ms.length) return false;
    // The price slider also keeps listings with unknown prices.
    if (!ms.some(function (m) { return m.price_pp == null || m.price_pp <= f.maxpp; })) return false;
    // Who may rent. Only what the listing states counts: a listing that says nothing stays.
    var fl = flagsOf(l);
    if (f.age != null) {
      var lo = Number(fl.min_age), hi = Number(fl.max_age);
      if (has(fl, "min_age") && lo > f.age) return false;
      if (has(fl, "max_age") && hi < f.age) return false;
    }
    // Every "listing says" filter in use must agree: "only" needs the listing to say it, "hide" needs it not to.
    var keys = Object.keys(f.attrs);
    for (var i = 0; i < keys.length; i++) {
      if (says(keys[i], l, fl) !== (f.attrs[keys[i]] === "only")) return false;
    }
    if (f.q) {
      var h = ((l.title || "") + " " + (l.address || "")).toLowerCase();
      if (h.indexOf(f.q.toLowerCase()) < 0) return false;
    }
    return true;
  }

  function flagText(fl) {
    var s = String(fl), i = s.indexOf(":"), k = i < 0 ? s : s.slice(0, i), v = i < 0 ? "" : s.slice(i + 1);
    var lab = (data.flag_labels && data.flag_labels[k]) || k.replace(/_/g, " ");
    return lab.replace(/\{n\}/g, v);
  }

  // The reply text: the user's own (kept in this browser only), or else the standard one that comes with the data.
  function standardReply() { return String((data && data.reply_template) || ""); }
  function template() { return reply != null ? reply : standardReply(); }
  function loadReply() {
    try { var s = localStorage.getItem(REPLY); if (typeof s === "string" && s.trim()) reply = s.slice(0, 5000); } catch (e) {}
  }
  function saveReply(text) {
    reply = text.trim() && text !== standardReply() ? text.slice(0, 5000) : null;
    try { if (reply == null) localStorage.removeItem(REPLY); else localStorage.setItem(REPLY, reply); } catch (e) {}
  }
  // Fill in a listing's details. A name in braces that is not a detail stays as it was typed.
  function fill(tpl, l) {
    var v = {
      title: l.title || "", address: l.address || l.title || "", city: (data.cities && data.cities[l.city]) || l.city || "",
      url: l.url || "", price: typeof l.price_total === "number" ? euro(l.price_total) : "",
      bedrooms: l.bedrooms != null ? String(l.bedrooms) : "", area: l.area_m2 != null ? l.area_m2 + " m²" : "",
      source: l.source_label || l.source || ""
    };
    return String(tpl).replace(/\{([a-z_]+)\}/g, function (m, k) { return has(v, k) ? v[k] : m; });
  }

  function copyReply(l, btn) {
    var txt = fill(template(), l);
    var label = function (text, mark) {
      btn.textContent = "";
      if (mark) btn.appendChild(icon("i-check"));
      btn.appendChild(el("span", "roll", text));
    };
    var done = function () {
      if (btn.offsetWidth) btn.style.minWidth = btn.offsetWidth + "px";   // the button keeps its width while the label changes
      label("Copied", true); tick();
      setTimeout(function () { label("Copy Reply"); }, 1800);
    };
    var fallback = function () {
      var ta = document.createElement("textarea");
      ta.value = txt; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { label("Copy Failed"); }
      document.body.removeChild(ta);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, fallback);
    else fallback();
  }

  // One listing: a row in the list. The price per person leads, because that is what the group decides on.
  function keyOf(l) { return l.uid != null ? "uid:" + l.uid : l; }
  function item(l) {
    var li = el("li", "item");
    var src = safeUrl(l.image_url), kept = photos && photos.get(keyOf(l));
    // A row is rebuilt when the group size or the data changes. Its photo is carried over, so it does not blink.
    var thumb = kept && kept.src === src ? kept.node : el("div", "thumb-box");
    var placeholder = function () { thumb.textContent = ""; thumb.appendChild(icon("i-house")); };
    if (kept && kept.src === src) { /* already built */ }
    else if (src) {
      var img = document.createElement("img");
      img.setAttribute("loading", "lazy");
      img.setAttribute("referrerpolicy", "no-referrer");
      img.setAttribute("alt", "");
      img.addEventListener("error", placeholder);
      img.addEventListener("load", function () { img.className = "loaded"; });   // photos fade in instead of popping in
      img.setAttribute("src", src);
      if (img.complete && img.naturalWidth) img.className = "loaded";
      thumb.appendChild(img);
    } else placeholder();
    if (photos) photos.set(keyOf(l), { src: src, node: thumb });
    li.appendChild(thumb);
    var b = el("div", "body"); li.appendChild(b);

    matchesFor(l).forEach(function (m, i) {
      var p = el("p", i ? "pp more" : "pp");
      if (m.price_pp == null) p.appendChild(el("span", "amount unknown", "Price unknown"));
      else {
        p.appendChild(el("span", "amount", euro(m.price_pp)));
        p.appendChild(el("span", null, "per person"));
      }
      var sub = el("span", "sub");
      sub.appendChild(el("span", null, plural(m.persons, "person", "people")));
      if (m.tier === "great") sub.appendChild(el("span", "tier", "Great price"));
      p.appendChild(sub);
      b.appendChild(p);
    });
    if (l.price_drop_from) {
      var drop = el("p", "mark drop");
      drop.appendChild(icon("i-drop"));
      drop.appendChild(el("span", null, "Price dropped, was " + euro(l.price_drop_from)));
      b.appendChild(drop);
    }

    var fs = ts(l.first_seen), isNew = fs != null && Date.now() - fs < 24 * 3600 * 1000;
    var title = l.title || "(no title)", h = el("h3", "title"), href = safeUrl(l.url);
    if (isNew) h.appendChild(el("span", "new-dot"));
    if (href) {
      var a = el("a", "link", title);
      a.setAttribute("href", href); a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer");
      h.appendChild(a);
      li.appendChild(icon("i-out", "out"));
    } else h.appendChild(el("span", null, title));
    b.appendChild(h);

    // Most titles already hold the street or the city: only add what the title does not say.
    var city = (data.cities && data.cities[l.city]) || l.city || "", inTitle = title.toLowerCase();
    var where = [l.address, city].filter(function (x) { return x && inTitle.indexOf(String(x).toLowerCase()) < 0; });
    if (where.length) b.appendChild(el("p", "addr", where.join(", ")));

    var facts = [];
    if (l.price_total != null) {
      // A listing that states its rent per person: say so, or the amount reads as the rent of the whole home.
      facts.push(euro(l.price_total) + (has(flagsOf(l), "price_per_person") ? " per person" : "") + " per month" +
        (l.price_includes_utilities === true ? " incl. utilities" :
        l.price_includes_utilities === false ? " excl. utilities" : ""));
    }
    if (l.bedrooms != null) facts.push(plural(l.bedrooms, "bedroom", "bedrooms"));
    if (l.rooms != null) facts.push(plural(l.rooms, "room", "rooms"));
    if (l.area_m2 != null) facts.push(l.area_m2 + " m²");
    if (l.furnished) facts.push(l.furnished);
    if (l.available_from && l.available_until) facts.push(day(l.available_from) + " to " + day(l.available_until));
    else if (l.available_from) facts.push("from " + day(l.available_from));
    else if (l.available_until) facts.push("until " + day(l.available_until));
    if (facts.length) b.appendChild(el("p", "facts", facts.join(" · ")));

    // Warnings and good signs each get their own line with an icon. Everything else is one quiet line.
    var marked = el("ul", "flags"), notes = [], quotes = [];
    (l.flags || []).forEach(function (x) {
      var k = flagKey(x), kind = WARN.indexOf(k) >= 0 ? "warn" : GOOD.indexOf(k) >= 0 ? "good" : REQ.indexOf(k) >= 0 ? "req" : "";
      if (!kind) { notes.push(flagText(x)); return; }
      var fl = el("li", "mark " + kind);
      fl.appendChild(icon(kind === "warn" ? "i-bad" : kind === "good" ? "i-good" : "i-note"));
      fl.appendChild(el("span", null, flagText(x)));
      marked.appendChild(fl);
      // The sentence of the listing that says so. One sentence often carries several marks: it is shown once.
      var q = record(l.evidence) ? l.evidence[k] : null;
      if (typeof q !== "string" || !q) return;
      for (var i = 0; i < quotes.length; i++) if (samePassage(quotes[i], q)) { if (q.length > quotes[i].length) quotes[i] = q; return; }
      if (quotes.length < 3) quotes.push(q);
    });
    if (notes.length) b.appendChild(el("p", "notes", notes.join(" · ")));
    if (marked.children.length) b.appendChild(marked);
    quotes.forEach(function (q) { b.appendChild(el("p", "quote", "“" + q + "”")); });

    var foot = el("div", "foot"), meta = el("p", "meta");
    if (isNew) meta.appendChild(el("span", "sr", "New. "));
    meta.appendChild(el("span", null, (l.source_label || l.source || "") + " · " + rel(l.first_seen)));
    meta.title = exact(l.first_seen);
    foot.appendChild(meta);
    var cb = el("button", "btn gray small copy", "Copy Reply"); cb.type = "button";
    cb.addEventListener("click", function () { copyReply(l, cb); });
    foot.appendChild(cb);
    b.appendChild(foot);
    return li;
  }

  function rowFor(l) {
    var c = rows && rows.get(l);
    if (!c || c.group !== f.group) {
      c = { group: f.group, node: item(l) };
      c.node.__key = keyOf(l);                            // the same listing keeps its place across a data refresh
      if (rows) rows.set(l, c);
    }
    c.node.className = "item";                            // it may have been fading out as a ghost
    if (c.node.style) c.node.style.top = "";
    return c.node;
  }

  // Stop every list animation that is still running (or holding its last frame).
  function still() {
    moving.forEach(function (a) { try { a.cancel(); } catch (e) {} });
    moving = [];
  }
  // Before the list changes: where is every row near the screen right now (also mid-animation)?
  function snapshot(box) {
    if (!box.getBoundingClientRect || !box.animate || calm() || document.hidden) return null;
    var vh = window.innerHeight || 800, list = box.getBoundingClientRect(), seen = new Map(), kids = box.children;
    for (var i = 0; i < kids.length; i++) {
      var r = kids[i].getBoundingClientRect();
      if (r.bottom < -vh || r.top > vh * 2 || kids[i].className !== "item") continue;
      seen.set(kids[i].__key, { node: kids[i], top: r.top, off: r.top - list.top, shown: r.bottom > 0 && r.top < vh });
    }
    still();
    return { rows: seen, height: list.height, bottom: list.bottom };
  }
  // After the list changed: rows that stayed slide to their new place, new rows fade in, rows that left fade out
  // where they were. Only rows on or near the screen are animated, and the next change can interrupt it.
  function flip(box, before) {
    var vh = window.innerHeight || 800, kids = box.children, kept = new Map();
    var move = { duration: 514, easing: curve("--spring", "ease-out") }, i;
    for (i = 0; i < kids.length; i++) {
      var node = kids[i], was = before.rows.get(node.__key), r = node.getBoundingClientRect(), shown = r.bottom > 0 && r.top < vh;
      if (was) {
        kept.set(node.__key, true);
        if ((was.shown || shown) && Math.abs(was.top - r.top) > 0.5)
          animate(node, [{ transform: "translateY(" + (was.top - r.top) + "px)" }, { transform: "none" }], move);
      } else if (shown) {
        animate(node, [{ opacity: 0, transform: "scale(.97)" }, { opacity: 1, transform: "none" }], { duration: 300, easing: "ease-out" });
      }
    }
    before.rows.forEach(function (was, key) {
      if (kept.has(key) || !was.shown) return;
      var g = was.node;
      g.className = "item ghost"; g.style.top = was.off + "px";
      box.appendChild(g);
      var a = animate(g, [{ opacity: 1 }, { opacity: 0, transform: "scale(.97)" }], { duration: 200, easing: "ease-in", fill: "forwards" });
      if (a) a.onfinish = function () { if (g.className === "item ghost" && g.parentNode === box) box.removeChild(g); };
      else box.removeChild(g);
    });
    var after = box.getBoundingClientRect();
    if (!box.hidden && Math.abs(after.height - before.height) > 1 && Math.min(before.bottom, after.bottom) < vh + 200)
      animate(box, [{ height: before.height + "px" }, { height: after.height + "px" }], move);
  }

  // `quiet` is true for changes that follow the keyboard (typing a search): no animation. It is "hold" while the
  // budget slider is moving: the amount and the count follow the slider, and the list waits until it rests.
  function update(quiet) {
    saveF();
    $("maxpp-out").textContent = euro(f.maxpp);
    var all = data.listings || [];
    var list = all.filter(visible);
    list.sort(f.sort === "cheapest" ? cheapestFirst : newestFirst);
    if (quiet !== "hold") {
      shownList = list;
      var box = $("listings"), before = quiet === true ? null : snapshot(box);
      if (!before) still();
      box.textContent = "";
      list.forEach(function (l) { box.appendChild(rowFor(l)); });
      box.hidden = list.length === 0;
      if (before) flip(box, before);
      $("empty").hidden = list.length > 0;
      $("load-error").hidden = true;
    }
    setText($("count"), list.length === all.length ? plural(all.length, "listing", "listings")
      : list.length + " of " + plural(all.length, "listing", "listings"), !!quiet);
    var n = activeFilters(), badge = $("filter-count");
    if (badge.textContent !== String(n)) { badge.textContent = ""; badge.appendChild(el("span", null, String(n))); }
    badge.hidden = n === 0;
    $("open-filters").setAttribute("aria-label", n ? "Filters, " + n + " active" : "Filters");
    $("clear-filters").hidden = n === 0;
    applied();
  }

  // The filters in use, above the list: each is a button that removes that one filter.
  function applied() {
    var box = $("applied"), chips = [];
    function chip(how, text, remove) {
      var b = el("button", "chip used");
      b.type = "button";
      if (how) b.appendChild(el("span", "how", how));
      b.appendChild(el("span", null, text));
      b.appendChild(icon("i-x"));
      b.setAttribute("aria-label", "Remove filter: " + (how ? how + " " : "") + text);
      b.addEventListener("click", function () { remove(); tick(); syncControls(); update(); });
      chips.push(b);
    }
    if (f.group !== "any") chip("", f.group + " People", function () { f.group = "any"; });
    f.cities.slice().forEach(function (k) {
      chip("", (data && data.cities && data.cities[k]) || k, function () { f.cities.splice(f.cities.indexOf(k), 1); });
    });
    if (f.maxpp < 800) chip("", "Up to " + euro(f.maxpp) + " per Person", function () { f.maxpp = 800; });
    if (f.age != null) chip("", "Age " + f.age, function () { f.age = null; });
    ATTR_GROUPS.forEach(function (g) {
      g[1].forEach(function (a) {
        if (f.attrs[a[0]]) chip(MODES[f.attrs[a[0]]], a[1], function () { delete f.attrs[a[0]]; });
      });
    });
    box.textContent = "";
    chips.forEach(function (b) { box.appendChild(b); });
    if (chips.length > 1) {
      var all = el("button", "btn plain small", "Clear All");
      all.type = "button";
      all.addEventListener("click", clearFilters);
      box.appendChild(all);
    }
    box.hidden = chips.length === 0;
  }

  function sources() {
    var box = $("sources"), ok = 0, total = 0; box.textContent = "";
    (data.sources || []).forEach(function (s) {
      var status = STATUS[s.status] ? s.status : "error";
      if (status !== "disabled") { total++; if (status === "ok") ok++; }
      var li = el("li", "source");
      li.appendChild(icon(STATUS[status][1], "st-" + status));
      var b = el("div", "body"), name = el("p", "name"), h = safeUrl(s.homepage);
      if (h) {
        var a = el("a", null, s.label || s.name);
        a.setAttribute("href", h); a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer");
        name.appendChild(a);
      } else name.textContent = s.label || s.name || "";
      b.appendChild(name);
      var detail = [];
      if (s.listings_last_run != null) detail.push(plural(s.listings_last_run, "listing", "listings") + " in the last check");
      if (status !== "ok" && status !== "disabled") detail.push(s.last_ok ? "last worked " + rel(s.last_ok) : "has never worked");
      if (detail.length) {
        var d = el("p", "detail", detail.join(" · "));
        if (s.last_ok) d.title = exact(s.last_ok);
        b.appendChild(d);
      }
      if (s.error) b.appendChild(el("p", "err", s.error));
      if (s.note) b.appendChild(el("p", "src-note", s.note));
      li.appendChild(b);
      li.appendChild(el("span", "state", STATUS[status][0]));
      box.appendChild(li);
    });
    working = total > 0 && ok === total ? "all" : "some";
    $("sources-summary").textContent = !total ? "" :
      ok === total ? (total === 1 ? "The source is working" : "All " + total + " sources working")
        : ok + " of " + plural(total, "source", "sources") + " working";
  }

  // Sheets are native dialogs: focus stays inside and returns to the button that opened them. This code moves them.
  // A spring brings a sheet up; a finger can pull it down, and a flick carries its speed into the closing spring.
  // On a wide screen the sheet is a centred panel that scales and fades instead.
  function sheet(d, top, opener) {
    var scrim = $("scrim"), root = document.documentElement, p = 0, h = 1, shown = false, anim = null, drag = null, touch = null, block = 0;
    function height() { return d.offsetHeight || (d.getBoundingClientRect && d.getBoundingClientRect().height) || h || 1; }
    function modal(on) { if (root && root.classList) root.classList[on ? "add" : "remove"]("modal"); }
    // Take the sheet off the screen at once, wherever it is.
    function gone() {
      if (anim) anim.stop();
      anim = null; p = 0;
      if (d.removeAttribute) d.removeAttribute("open");
      d.inert = false; d.style.transform = ""; d.style.opacity = "";
    }
    var me = { finish: gone }, refocus = false;
    function atBottom() { return !window.matchMedia || !window.matchMedia("(min-width: 36em)").matches; }
    // p runs from 0 (away) to 1 (in place); a little over 1 while it is pulled upwards.
    function paint(v) {
      p = v;
      var c = clamp(v, 0, 1), st = d.style;
      if (calm()) { st.transform = ""; st.opacity = String(c); }
      else if (atBottom()) { st.transform = "translateY(" + ((1 - v) * h).toFixed(1) + "px)"; st.opacity = ""; }
      else { st.transform = "translateY(" + ((1 - v) * 14).toFixed(1) + "px) scale(" + (0.96 + 0.04 * v).toFixed(4) + ")"; st.opacity = String(c); }
      scrim.style.opacity = String(c);
    }
    function go(to, velocity, duration, bounce, done) {
      if (anim) anim.stop();
      anim = spring({ from: p, to: to, velocity: velocity || 0, duration: duration, bounce: bounce || 0, rest: 0.001, step: paint, done: done });
    }
    function show() {
      if (shown) return;
      shown = true;
      if (leaving && leaving !== me) leaving.finish();        // the other sheet was still on its way out
      leaving = null;
      if (anim) anim.stop();
      if (d.removeAttribute) d.removeAttribute("open");       // this one may still be on its way out: carry on from there
      d.inert = false;
      if (d.showModal) d.showModal(); else d.setAttribute("open", "");
      modal(true);
      scrim.hidden = false;
      h = height();
      go(1, 0, calm() ? 0.2 : 0.46, 0);
    }
    function hide(velocity) {
      if (!shown) return;
      shown = false; drag = null; touch = null; refocus = true;
      if (leaving && leaving !== me) leaving.finish();
      leaving = me;
      h = height();
      // Hand the page back at once; the sheet finishes leaving without blocking what is behind it.
      if (d.close) { d.close(); d.setAttribute("open", ""); }
      d.inert = true;                                         // while it leaves it cannot be tapped or tabbed into
      modal(false);
      go(0, velocity, calm() ? 0.2 : 0.36, 0, function () {
        gone();
        if (leaving === me) { leaving = null; scrim.hidden = true; }
      });
    }

    // Dragging. `offset` is how far the sheet is below its resting place, in pixels.
    function begin(y) {
      if (!shown || !atBottom() || calm()) return false;      // with Reduce Motion the sheet fades and is not dragged
      if (anim) anim.stop();
      h = height();
      drag = { y0: y - (1 - p) * h, hist: [[clock(), (1 - p) * h]] };
      return true;
    }
    function pull(y) {
      var offset = y - drag.y0;
      if (offset < 0) offset = -band(-offset, 60);
      paint(1 - offset / h);
      drag.hist.push([clock(), offset]);                     // the sheet's own position: past the top it moves less than the finger
      if (drag.hist.length > 8) drag.hist.shift();
    }
    function release() {
      var offset = (1 - p) * h;
      drag.hist.push([clock(), offset]);                     // a pause before letting go counts: no speed left
      var v = speed(drag.hist);
      drag = null;
      block = clock();
      // Where would it come to rest if it kept sliding? Far enough down, or flicked hard enough: it closes.
      if (offset + v * 0.4 > h * 0.4 || v > 900) hide(-v / h);
      else go(1, -v / h, 0.4, 0.12);
    }
    // Touch: from the top bar, or from anywhere in the sheet when it is scrolled to its top and the finger moves down.
    d.addEventListener("touchstart", function (e) {
      if (!e || !e.touches || e.touches.length !== 1 || !shown || !atBottom()) {
        if (drag && drag.pointer == null) release();           // a second finger ends the drag
        touch = null; return;
      }
      var t = e.target, own = t && t.closest && t.closest("input[type=range], .segmented, textarea");
      touch = own ? null : { x: e.touches[0].clientX, y: e.touches[0].clientY, top: top.contains(t), free: d.scrollTop <= 0 };
    }, { passive: true });
    d.addEventListener("touchmove", function (e) {
      if (!touch || !e || !e.touches) return;
      var x = e.touches[0].clientX, y = e.touches[0].clientY, dx = x - touch.x, dy = y - touch.y;
      if (!drag) {
        var ours = !calm() && (touch.top || (touch.free && dy > 0 && Math.abs(dy) > Math.abs(dx)));
        if (Math.abs(dx) < 6 && Math.abs(dy) < 6) {           // too early to tell, but keep the page from starting to scroll
          if (ours && e.cancelable) e.preventDefault();
          return;
        }
        if (!ours || !begin(touch.y)) { touch = null; return; }
      }
      if (e.cancelable) e.preventDefault();
      pull(y);
    }, { passive: false });
    function touchEnd() {
      if (drag && drag.pointer == null) release();
      touch = null;
    }
    d.addEventListener("touchend", touchEnd);
    d.addEventListener("touchcancel", touchEnd);
    // Mouse and pen: from the top bar.
    top.addEventListener("pointerdown", function (e) {
      if (!e || e.pointerType === "touch" || e.button || (e.target && e.target.closest && e.target.closest("button"))) return;
      if (begin(e.clientY)) { drag.pointer = e.pointerId; try { top.setPointerCapture(e.pointerId); } catch (err) {} }
    });
    top.addEventListener("pointermove", function (e) { if (drag && e && drag.pointer === e.pointerId) pull(e.clientY); });
    function pointerEnd(e) { if (drag && e && drag.pointer === e.pointerId) release(); }
    top.addEventListener("pointerup", pointerEnd);
    top.addEventListener("pointercancel", pointerEnd);

    d.addEventListener("click", function (e) {
      if (!e) return;
      if (clock() - block < 350) { e.preventDefault(); e.stopPropagation(); return; }   // the tap that ends a drag
      if (e.target !== d || !d.getBoundingClientRect) return;
      var r = d.getBoundingClientRect();                     // a tap outside the sheet, on the dimmed page, closes it
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) hide();
    }, true);
    d.addEventListener("cancel", function (e) { if (e) e.preventDefault(); hide(); });   // Escape
    d.addEventListener("close", function () {
      if (shown && !d.open) {                                // closed by the browser itself: tidy up without animation
        shown = false; drag = null; touch = null; refocus = true;
        gone(); modal(false);
        scrim.hidden = true;
      }
      // Give focus back to the button that opened the sheet: once per closing, and not when another sheet has it by now.
      var a = document.activeElement, other = a && a.closest && a.closest("dialog[open]");
      if (refocus && opener.focus && !(other && other !== d)) opener.focus();
      refocus = false;
    });
    return { show: show, hide: hide, shown: function () { return shown; } };
  }

  // Group size: one thumb slides between the choices on a spring. It heads for a choice the moment it is touched,
  // follows a dragging finger, and settles on the choice it was flicked towards. The radio buttons underneath keep
  // the control working with a keyboard and a screen reader.
  function segmented(box, thumb, radios) {
    var n = radios.length, x = 0, anim = null, drag = null, block = 0;
    function checked() { for (var i = 0; i < n; i++) if (radios[i].checked) return i; return 0; }
    // Moved with `translate` where it exists, so the thumb's growth while held (CSS `scale`) does not shift it.
    var byTranslate = !!thumb.style && "translate" in thumb.style;
    function paint(v) {
      x = v;
      var t = (v * 100).toFixed(2) + "%";
      if (byTranslate) thumb.style.translate = t + " 0"; else thumb.style.transform = "translateX(" + t + ")";
    }
    function go(to, velocity) {
      var v = velocity != null ? velocity : (anim ? anim.velocity() : 0);
      if (anim) anim.stop();
      if (calm()) { paint(to); return; }
      anim = spring({ from: x, to: to, velocity: v, duration: 0.3, bounce: 0.2, rest: 0.002, step: paint });
    }
    function settle() { if (!drag) go(checked()); }
    function held(on) { if (box.classList) box.classList[on ? "add" : "remove"]("held"); }
    paint(checked());
    radios.forEach(function (r) { r.addEventListener("change", settle); });

    box.addEventListener("pointerdown", function (e) {
      if (!e || e.button || !box.getBoundingClientRect) return;
      var r = box.getBoundingClientRect(), w = (r.width - 4) / n;
      var to = clamp(Math.floor((e.clientX - r.left - 2) / w), 0, n - 1);
      var dg = drag = { id: e.pointerId, x0: e.clientX, w: w, moved: false, from: 0, hist: [], timer: 0 };
      var press = function () { if (drag === dg && !dg.moved) { held(true); go(to, 0); } };
      // A finger may be about to scroll the page instead, so a touch waits a moment; a mouse reacts at once.
      if (e.pointerType === "touch") dg.timer = setTimeout(press, 110); else press();
    });
    box.addEventListener("pointermove", function (e) {
      if (!drag || !e || e.pointerId !== drag.id) return;
      if (!drag.moved) {
        if (Math.abs(e.clientX - drag.x0) < 5) return;
        drag.moved = true; drag.from = x; drag.x0 = e.clientX; drag.hist = [[clock(), x]];
        clearTimeout(drag.timer);
        held(true);
        if (anim) anim.stop();
        try { box.setPointerCapture(drag.id); } catch (err) {}
      }
      var v = drag.from + (e.clientX - drag.x0) / drag.w;
      if (v < 0) v = -band(-v, 0.22); else if (v > n - 1) v = n - 1 + band(v - (n - 1), 0.22);
      paint(v);
      drag.hist.push([clock(), x]);                          // the thumb's own position: past an edge it moves less than the finger
      if (drag.hist.length > 8) drag.hist.shift();
    });
    function end(e) {
      if (!drag || !e || e.pointerId !== drag.id) return;
      var dg = drag;
      drag = null;
      clearTimeout(dg.timer);
      held(false);
      if (!dg.moved || e.type === "pointercancel") { setTimeout(settle, 120); return; }   // a tap: the click picks the choice
      dg.hist.push([clock(), x]);                            // a pause before letting go counts: no speed left
      var v = speed(dg.hist), to = clamp(Math.round(x + v * 0.2), 0, n - 1);
      block = clock();
      if (!radios[to].checked) {
        radios[to].checked = true;
        radios[to].dispatchEvent(new Event("change", { bubbles: true }));
      }
      go(to, v);
    }
    document.addEventListener("pointerup", end);
    document.addEventListener("pointercancel", end);
    // The click that ends a drag must not pick whatever choice happens to be under the finger.
    box.addEventListener("click", function (e) { if (e && clock() - block < 350) { e.preventDefault(); e.stopPropagation(); } }, true);
    return { settle: settle };
  }

  // The reply text, edited in one place. The preview shows it filled in for the first listing on screen, and follows
  // every keystroke. Save keeps it; Cancel drops the changes. Closing the sheet any other way (pulling it down,
  // Escape) keeps what was typed for the next time it opens, so nothing typed is lost by accident.
  function replyEditor() {
    var sh = sheet($("reply-sheet"), $("reply-top"), $("open-reply"));
    var ta = $("reply-text"), box = $("reply-fields"), pv = $("reply-preview"), who = $("reply-for"), reset = $("reply-reset");
    var sample = null, draft = null;
    function preview() {
      draft = ta.value;
      pv.textContent = sample ? fill(ta.value, sample) : ta.value;
      reset.hidden = ta.value === standardReply();
    }
    function insert(text) {
      var a = ta.selectionStart, b = ta.selectionEnd, v = ta.value;
      if (typeof a !== "number" || typeof b !== "number") a = b = v.length;
      ta.value = v.slice(0, a) + text + v.slice(b);
      if (ta.focus) ta.focus();
      try { ta.setSelectionRange(a + text.length, a + text.length); } catch (e) {}
      preview(); tick();
    }
    FIELDS.forEach(function (fd) {
      var chip = el("button", "chip", fd[1]);
      chip.type = "button";
      chip.addEventListener("click", function () { insert("{" + fd[0] + "}"); });
      box.appendChild(chip);
    });
    ta.addEventListener("input", preview);
    $("reply-form").addEventListener("submit", function (e) { if (e) e.preventDefault(); });
    $("open-reply").addEventListener("click", function () {
      sample = shownList[0] || (data.listings || [])[0] || null;
      ta.value = draft != null ? draft : template();
      who.textContent = sample ? "Filled in for “" + (sample.title || "this listing") + "”." : "";
      preview();
      sh.show();
    });
    $("reply-cancel").addEventListener("click", function () { draft = null; sh.hide(); });
    $("reply-save").addEventListener("click", function () { saveReply(ta.value); draft = null; tick(); sh.hide(); });
    reset.addEventListener("click", function () { ta.value = standardReply(); preview(); });
  }

  // Narrow screens: the filter form lives in a sheet. Wide screens: it sits in the side column, always on screen.
  function placeFilters(wide, filters) {
    var form = $("filters");
    if (wide && wide.matches) { filters.hide(); $("filters-pane").appendChild(form); }
    else $("filters-sheet").appendChild(form);
  }

  function bind() {
    var m = $("maxpp"), q = $("search"), so = $("sort"), age = $("age");
    syncControls();
    GROUPS.forEach(function (g) {
      var r = $("group-" + g);
      r.addEventListener("change", function () { if (r.checked) { f.group = g; tick(); update(); } });
    });
    // Age: two digits make an age. Anything shorter means "any", so the list changes once per finished number.
    age.addEventListener("input", function () {
      var v = age.value.replace(/\D/g, "").slice(0, 2);
      if (age.value !== v) age.value = v;
      var n = v.length === 2 && Number(v) >= 16 ? Number(v) : null;
      if (n !== f.age) { f.age = n; update(); }
    });
    age.addEventListener("change", function () { if (f.age == null) age.value = ""; });
    // The budget slider: while it moves only the numbers follow. The list animates once, when the slider pauses
    // for a moment or is let go, so a drag across many steps is one calm change instead of many.
    var rest = 0;
    var settleList = function () { if (rest) { clearTimeout(rest); rest = 0; update(); } };
    m.addEventListener("input", function () {
      f.maxpp = Number(m.value);
      update("hold");
      if (rest) clearTimeout(rest);
      rest = setTimeout(settleList, 120);
    });
    m.addEventListener("change", settleList);
    q.addEventListener("input", function () { f.q = q.value.trim(); update(true); });
    so.addEventListener("change", function () { f.sort = so.value; update(); });
    $("search-form").addEventListener("submit", function (e) { if (e) e.preventDefault(); q.blur(); });
    $("filters").addEventListener("submit", function (e) { if (e) e.preventDefault(); });
    $("clear-filters").addEventListener("click", clearFilters);
    $("clear-empty").addEventListener("click", clearFilters);

    // Filters apply as you change them. Done (or pulling the sheet down) keeps them; Cancel puts back what was set
    // when the sheet opened.
    var filters = sheet($("filters-sheet"), $("filters-top"), $("open-filters"));
    $("open-filters").addEventListener("click", function () { beforeSheet = JSON.stringify(f); filters.show(); });
    $("filters-done").addEventListener("click", function () { filters.hide(); });
    $("filters-cancel").addEventListener("click", function () {
      if (beforeSheet && beforeSheet !== JSON.stringify(f)) { f = JSON.parse(beforeSheet); syncControls(); update(); }
      filters.hide();
    });
    var sources = sheet($("sources-sheet"), $("sources-top"), $("open-sources"));
    $("open-sources").addEventListener("click", function () { sources.show(); });
    $("sources-close").addEventListener("click", function () { sources.hide(); });
    replyEditor();

    var wide = window.matchMedia ? window.matchMedia("(min-width: 60em)") : null;
    placeFilters(wide, filters);
    if (wide && wide.addEventListener) wide.addEventListener("change", function () { placeFilters(wide, filters); });
    // iPhones only show pressed states when a touch listener exists somewhere on the page.
    document.addEventListener("touchstart", function () {}, { passive: true });
  }

  loadF();
  loadReply();
  // The saved filters show from the first frame, before the data arrives; each thumb starts under the saved choice.
  syncControls();
  groupControl = segmented($("group-control"), $("group-thumb"), GROUPS.map(function (g) { return $("group-" + g); }));
  $("retry").addEventListener("click", function () { window.location.reload(); });
  load("data.json").then(function (d) {
    render(d, false);
  }, function () {
    return load("sample-data.json").then(function (d) { render(d, true); });
  }).catch(function () {
    $("last-check").textContent = "No data";
    $("status-dot").className = "dot bad";
    $("load-error").hidden = false;
  }).then(function () {
    setInterval(function () { if (data) header(); }, 60000);
    setInterval(refresh, 5 * 60000);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") refresh();
    });
  });
})();
