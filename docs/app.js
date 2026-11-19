"use strict";
// All data comes from third-party sites: only textContent / setAttribute are used, never innerHTML.
(function () {
  var STORE = "househunter.filters.v1";
  var SVG = "http://www.w3.org/2000/svg";
  var WARN = ["no_sharers", "max_persons", "no_students", "no_registration", "suspicious_price", "scam_signals"];
  var GOOD = ["sharers_ok"];
  var GROUPS = ["any", "2", "3"];
  // Source status: the word shown and the icon that goes with it (never colour alone).
  var STATUS = {
    ok: ["Working", "i-good"], partial: ["Partly working", "i-part"], blocked: ["Blocked", "i-fail"],
    error: ["Not working", "i-fail"], disabled: ["Off", "i-off"]
  };
  var data = null, isSample = false, refreshing = false, working = "";
  var cityInputs = {}, cityCounts = {}, beforeSheet = null, groupControl = null;
  // Each listing keeps its row, so a filter change moves rows instead of rebuilding them (photo and "Copied" stay).
  var rows = typeof WeakMap === "function" ? new WeakMap() : null, moving = [];
  var photos = typeof Map === "function" ? new Map() : null;   // one photo element per listing, kept when its row is rebuilt
  var leaving = null;                                          // the sheet that is animating out, if any
  var f = { cities: [], group: "any", maxpp: 800, onlyGreat: false, hideWarn: false, q: "", sort: "newest" };
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
  function hasWarn(l) { return (l.flags || []).some(function (x) { return WARN.indexOf(flagKey(x)) >= 0; }); }
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
    if (first) bind();
    countCities(); sources(); header(); update();
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
        f.onlyGreat = s.onlyGreat === true; f.hideWarn = s.hideWarn === true;
        if (typeof s.q === "string") f.q = s.q;
        if (s.sort === "cheapest") f.sort = "cheapest";
      }
    } catch (e) {}
  }
  // How many of the filters in the Filters form differ from "show everything".
  function activeFilters() {
    return (f.cities.length ? 1 : 0) + (f.group !== "any" ? 1 : 0) + (f.maxpp < 800 ? 1 : 0) +
      (f.onlyGreat ? 1 : 0) + (f.hideWarn ? 1 : 0);
  }
  function clearFilters() {
    f.cities = []; f.group = "any"; f.maxpp = 800; f.onlyGreat = false; f.hideWarn = false; f.q = "";
    syncControls(); update();
  }
  // Put the filter values back into the controls (after Clear Filters or Cancel).
  function syncControls() {
    GROUPS.forEach(function (g) { $("group-" + g).checked = f.group === g; });
    Object.keys(cityInputs).forEach(function (k) { cityInputs[k].checked = f.cities.indexOf(k) >= 0; });
    $("maxpp").value = f.maxpp; $("only-great").checked = f.onlyGreat; $("hide-warnings").checked = f.hideWarn;
    $("search").value = f.q; $("sort").value = f.sort;
    if (groupControl) groupControl.settle();
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
    if (f.onlyGreat && !ms.some(function (m) { return m.tier === "great"; })) return false;
    if (f.hideWarn && hasWarn(l)) return false;
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

  function copyReply(l, btn) {
    var city = (data.cities && data.cities[l.city]) || l.city || "";
    var txt = String(data.reply_template || "")
      .replace(/\{title\}/g, function () { return l.title || ""; })
      .replace(/\{address\}/g, function () { return l.address || l.title || ""; })
      .replace(/\{city\}/g, function () { return city; })
      .replace(/\{url\}/g, function () { return l.url || ""; });
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
      facts.push(euro(l.price_total) + " per month" + (l.price_includes_utilities === true ? " incl. utilities" :
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
    var marked = el("ul", "flags"), notes = [];
    (l.flags || []).forEach(function (x) {
      var k = flagKey(x), kind = WARN.indexOf(k) >= 0 ? "warn" : GOOD.indexOf(k) >= 0 ? "good" : "";
      if (!kind) { notes.push(flagText(x)); return; }
      var fl = el("li", "mark " + kind);
      fl.appendChild(icon(kind === "warn" ? "i-bad" : "i-good"));
      fl.appendChild(el("span", null, flagText(x)));
      marked.appendChild(fl);
    });
    if (notes.length) b.appendChild(el("p", "notes", notes.join(" · ")));
    if (marked.children.length) b.appendChild(marked);

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
      var t = e.target, own = t && t.closest && t.closest("input[type=range], .segmented");
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

  // Narrow screens: the filter form lives in a sheet. Wide screens: it sits in the side column, always on screen.
  function placeFilters(wide, filters) {
    var form = $("filters");
    if (wide && wide.matches) { filters.hide(); $("filters-pane").appendChild(form); }
    else $("filters-sheet").appendChild(form);
  }

  function bind() {
    var m = $("maxpp"), og = $("only-great"), hw = $("hide-warnings"), q = $("search"), so = $("sort");
    syncControls();
    GROUPS.forEach(function (g) {
      var r = $("group-" + g);
      r.addEventListener("change", function () { if (r.checked) { f.group = g; tick(); update(); } });
    });
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
    og.addEventListener("change", function () { f.onlyGreat = og.checked; tick(); update(); });
    hw.addEventListener("change", function () { f.hideWarn = hw.checked; tick(); update(); });
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

    var wide = window.matchMedia ? window.matchMedia("(min-width: 60em)") : null;
    placeFilters(wide, filters);
    if (wide && wide.addEventListener) wide.addEventListener("change", function () { placeFilters(wide, filters); });
    // iPhones only show pressed states when a touch listener exists somewhere on the page.
    document.addEventListener("touchstart", function () {}, { passive: true });
  }

  loadF();
  // The saved filters show from the first frame, before the data arrives; the thumb starts under the saved group size.
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
