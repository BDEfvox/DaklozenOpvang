"use strict";
// All data comes from third-party sites: only textContent / setAttribute are used, never innerHTML.
(function () {
  var STORE = "househunter.filters.v1";
  var WARN = ["no_sharers", "max_persons", "no_students", "no_registration", "suspicious_price", "scam_signals"];
  var GOOD = ["sharers_ok"];
  var data = null, isSample = false;
  var f = { cities: [], group: "any", maxpp: 800, onlyGreat: false, hideWarn: false, q: "", sort: "newest" };
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
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
  function euro(n) { return "€" + Math.round(n).toLocaleString("nl-NL"); }
  function flagKey(fl) { return String(fl).split(":")[0]; }
  function hasWarn(l) { return (l.flags || []).some(function (x) { return WARN.indexOf(flagKey(x)) >= 0; }); }
  function minPP(l) {
    var v = (l.matches || []).map(function (m) { return m.price_pp; }).filter(function (x) { return typeof x === "number"; });
    return v.length ? Math.min.apply(null, v) : Infinity;
  }

  function load() {
    return fetch("data.json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { if (!d || !Array.isArray(d.listings)) throw new Error("bad"); return d; })
      .catch(function () {
        isSample = true;
        return fetch("sample-data.json", { cache: "no-store" }).then(function (r) { return r.json(); });
      });
  }

  function saveF() { try { localStorage.setItem(STORE, JSON.stringify(f)); } catch (e) {} }
  function loadF() {
    try {
      var s = JSON.parse(localStorage.getItem(STORE) || "null");
      if (s && typeof s === "object") {
        if (Array.isArray(s.cities)) f.cities = s.cities.filter(function (x) { return typeof x === "string"; });
        if (["any", "2", "3"].indexOf(s.group) >= 0) f.group = s.group;
        if (typeof s.maxpp === "number") f.maxpp = Math.min(800, Math.max(400, s.maxpp));
        f.onlyGreat = s.onlyGreat === true; f.hideWarn = s.hideWarn === true;
        if (typeof s.q === "string") f.q = s.q;
        if (s.sort === "cheapest") f.sort = "cheapest";
      }
    } catch (e) {}
  }

  function header() {
    var lc = $("last-check");
    lc.textContent = "Last check: " + rel(data.last_run);
    lc.title = exact(data.last_run);
    var t = ts(data.last_run), w = $("stale-warning");
    if (!isSample && (t == null || Date.now() - t > 2 * 3600 * 1000)) {
      w.textContent = "Warning: the last check was " + rel(data.last_run) + ". The checker may be down.";
      w.hidden = false;
    } else w.hidden = true;
    $("sample-banner").hidden = !isSample;
  }

  function buildCityChips() {
    var box = $("city-chips"); box.textContent = "";
    Object.keys(data.cities || {}).forEach(function (k) {
      var b = el("button", "chip", data.cities[k]);
      b.type = "button";
      b.setAttribute("aria-pressed", f.cities.indexOf(k) >= 0 ? "true" : "false");
      b.addEventListener("click", function () {
        var i = f.cities.indexOf(k);
        if (i >= 0) f.cities.splice(i, 1); else f.cities.push(k);
        b.setAttribute("aria-pressed", i >= 0 ? "false" : "true");
        update();
      });
      box.appendChild(b);
    });
  }

  function matchesFor(l) {
    return (l.matches || []).filter(function (m) { return f.group === "any" || String(m.persons) === f.group; });
  }
  function visible(l) {
    if (f.cities.length && f.cities.indexOf(l.city) < 0) return false;
    var ms = matchesFor(l);
    if (!ms.length) return false;
    if (!ms.some(function (m) { return m.price_pp <= f.maxpp; })) return false;
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
      .replace(/\{address\}/g, function () { return l.address || ""; })
      .replace(/\{city\}/g, function () { return city; })
      .replace(/\{url\}/g, function () { return l.url || ""; });
    var done = function () { btn.textContent = "Copied!"; setTimeout(function () { btn.textContent = "Copy reply"; }, 1800); };
    var fallback = function () {
      var ta = document.createElement("textarea");
      ta.value = txt; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { btn.textContent = "Copy failed"; }
      document.body.removeChild(ta);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, fallback);
    else fallback();
  }

  function card(l) {
    var c = el("article", "card");
    var src = safeUrl(l.image_url);
    if (src) {
      var img = document.createElement("img");
      img.setAttribute("loading", "lazy");
      img.setAttribute("referrerpolicy", "no-referrer");
      img.setAttribute("alt", "");
      img.addEventListener("error", function () { img.remove(); });
      img.setAttribute("src", src);
      c.appendChild(img);
    }
    var b = el("div", "body"); c.appendChild(b);

    var badges = el("div", "badges");
    var tier = l.best_tier === "great" ? "great" : "ok";
    badges.appendChild(el("span", "badge " + tier, tier));
    var fs = ts(l.first_seen);
    if (fs != null && Date.now() - fs < 24 * 3600 * 1000) badges.appendChild(el("span", "badge new", "NEW"));
    if (l.price_drop_from) badges.appendChild(el("span", "badge drop", "Price drop, was " + euro(l.price_drop_from)));
    b.appendChild(badges);

    b.appendChild(el("h3", null, l.title || "(no title)"));
    var city = (data.cities && data.cities[l.city]) || l.city || "";
    b.appendChild(el("p", "addr", [l.address, city].filter(Boolean).join(", ")));

    var p = el("p", "price", l.price_total != null ? euro(l.price_total) + " / month" : "Price unknown");
    if (l.price_includes_utilities === true) p.appendChild(el("small", null, " incl. utilities"));
    else if (l.price_includes_utilities === false) p.appendChild(el("small", null, " excl. utilities"));
    b.appendChild(p);

    matchesFor(l).forEach(function (m) {
      b.appendChild(el("p", "pp", euro(m.price_pp) + " pp (" + m.persons + " people)"));
    });

    var facts = [];
    if (l.bedrooms != null) facts.push(l.bedrooms + " bedrooms");
    if (l.rooms != null) facts.push(l.rooms + " rooms");
    if (l.area_m2 != null) facts.push(l.area_m2 + " m²");
    if (l.furnished) facts.push(l.furnished);
    if (l.available_from || l.available_until)
      facts.push("available " + (l.available_from || "?") + (l.available_until ? " to " + l.available_until : ""));
    if (facts.length) b.appendChild(el("p", "facts", facts.join(" · ")));

    if (l.flags && l.flags.length) {
      var fl = el("div", "flags");
      l.flags.forEach(function (x) {
        var k = flagKey(x);
        var cls = WARN.indexOf(k) >= 0 ? "warn" : GOOD.indexOf(k) >= 0 ? "good" : "";
        fl.appendChild(el("span", "flag " + cls, flagText(x)));
      });
      b.appendChild(fl);
    }

    var meta = el("p", "meta", (l.source_label || l.source || "") + " · first seen " + rel(l.first_seen));
    meta.title = exact(l.first_seen);
    b.appendChild(meta);

    var act = el("div", "actions");
    var href = safeUrl(l.url);
    if (href) {
      var a = el("a", "btn", "Open listing");
      a.setAttribute("href", href); a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer");
      act.appendChild(a);
    }
    var cb = el("button", "btn secondary", "Copy reply"); cb.type = "button";
    cb.addEventListener("click", function () { copyReply(l, cb); });
    act.appendChild(cb);
    b.appendChild(act);
    return c;
  }

  function update() {
    saveF();
    $("maxpp-out").textContent = euro(f.maxpp);
    var all = data.listings || [];
    var list = all.filter(visible);
    list.sort(f.sort === "cheapest"
      ? function (a, b) { return minPP(a) - minPP(b); }
      : function (a, b) { return (ts(b.first_seen) || 0) - (ts(a.first_seen) || 0); });
    var box = $("listings"); box.textContent = "";
    list.forEach(function (l) { box.appendChild(card(l)); });
    $("count").textContent = list.length + " of " + all.length + " listings";
    $("empty").hidden = list.length > 0;
  }

  function sources() {
    var tb = document.querySelector("#sources tbody"); tb.textContent = "";
    (data.sources || []).forEach(function (s) {
      var tr = document.createElement("tr");
      var n = el("td"), h = safeUrl(s.homepage);
      if (h) { var a = el("a", null, s.label || s.name); a.setAttribute("href", h); a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer"); n.appendChild(a); }
      else n.textContent = s.label || s.name || "";
      tr.appendChild(n);
      var st = el("td"), status = ["ok", "partial", "blocked", "error", "disabled"].indexOf(s.status) >= 0 ? s.status : "error";
      st.appendChild(el("span", "badge " + (status === "ok" ? "ok-status" : status), status));
      tr.appendChild(st);
      var lo = el("td", null, s.last_ok ? rel(s.last_ok) : "never"); if (s.last_ok) lo.title = exact(s.last_ok);
      tr.appendChild(lo);
      tr.appendChild(el("td", null, s.listings_last_run != null ? String(s.listings_last_run) : ""));
      tr.appendChild(el("td", null, s.error || ""));
      tr.appendChild(el("td", null, s.note || ""));
      tb.appendChild(tr);
    });
  }

  function bind() {
    var g = $("group"), m = $("maxpp"), og = $("only-great"), hw = $("hide-warnings"), q = $("search"), so = $("sort");
    g.value = f.group; m.value = f.maxpp; og.checked = f.onlyGreat; hw.checked = f.hideWarn; q.value = f.q; so.value = f.sort;
    g.addEventListener("change", function () { f.group = g.value; update(); });
    m.addEventListener("input", function () { f.maxpp = Number(m.value); update(); });
    og.addEventListener("change", function () { f.onlyGreat = og.checked; update(); });
    hw.addEventListener("change", function () { f.hideWarn = hw.checked; update(); });
    q.addEventListener("input", function () { f.q = q.value.trim(); update(); });
    so.addEventListener("change", function () { f.sort = so.value; update(); });
  }

  loadF();
  load().then(function (d) {
    data = d; header(); buildCityChips(); bind(); sources(); update();
    setInterval(header, 60000);
  }).catch(function () {
    $("last-check").textContent = "Could not load any data.";
  });
})();
