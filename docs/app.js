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
    ok: ["Working", "i-check-circle"], partial: ["Partly working", "i-alert"], blocked: ["Blocked", "i-x-circle"],
    error: ["Not working", "i-x-circle"], disabled: ["Off", "i-minus-circle"]
  };
  var data = null, isSample = false, refreshing = false, working = "";
  var cityInputs = {}, cityCounts = {}, beforeSheet = null;
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
        update();
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
    var done = function () { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy Reply"; }, 1800); };
    var fallback = function () {
      var ta = document.createElement("textarea");
      ta.value = txt; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { btn.textContent = "Copy Failed"; }
      document.body.removeChild(ta);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, fallback);
    else fallback();
  }

  // One listing: a row in the list. The price per person leads, because that is what the group decides on.
  function item(l) {
    var li = el("li", "item");
    var thumb = el("div", "thumb"), src = safeUrl(l.image_url);
    var placeholder = function () { thumb.textContent = ""; thumb.appendChild(icon("i-house")); };
    if (src) {
      var img = document.createElement("img");
      img.setAttribute("loading", "lazy");
      img.setAttribute("referrerpolicy", "no-referrer");
      img.setAttribute("alt", "");
      img.addEventListener("error", placeholder);
      img.setAttribute("src", src);
      thumb.appendChild(img);
    } else placeholder();
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
      var drop = el("p", "drop");
      drop.appendChild(icon("i-down"));
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
      var fl = el("li", "flag " + kind);
      fl.appendChild(icon(kind === "warn" ? "i-warn" : "i-check"));
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

  function update() {
    saveF();
    $("maxpp-out").textContent = euro(f.maxpp);
    var all = data.listings || [];
    var list = all.filter(visible);
    list.sort(f.sort === "cheapest" ? cheapestFirst : newestFirst);
    var box = $("listings"); box.textContent = "";
    list.forEach(function (l) { box.appendChild(item(l)); });
    box.hidden = list.length === 0;
    $("count").textContent = list.length === all.length ? plural(all.length, "listing", "listings")
      : list.length + " of " + plural(all.length, "listing", "listings");
    $("empty").hidden = list.length > 0;
    $("load-error").hidden = true;
    var n = activeFilters(), badge = $("filter-count");
    badge.textContent = String(n);
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

  // Sheets are native dialogs: focus stays inside, Escape closes, and focus returns to the button that opened it.
  function openSheet(d) { if (d.showModal) d.showModal(); else d.setAttribute("open", ""); }
  function closeSheet(d) { if (d.close) d.close(); else d.removeAttribute("open"); }
  function sheet(d, opener, closers) {
    opener.addEventListener("click", function () { openSheet(d); });
    d.addEventListener("close", function () { if (opener.focus) opener.focus(); });
    closers.forEach(function (b) { b.addEventListener("click", function () { closeSheet(d); }); });
    d.addEventListener("click", function (e) {   // a tap outside the sheet (on the dimmed page) closes it
      if (!e || e.target !== d || !d.getBoundingClientRect) return;
      var r = d.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeSheet(d);
    });
  }
  // Narrow screens: the filter form lives in a sheet. Wide screens: it sits in the side column, always on screen.
  function placeFilters(wide) {
    var form = $("filters"), d = $("filters-sheet");
    if (wide && wide.matches) { if (d.open) closeSheet(d); $("filters-pane").appendChild(form); }
    else d.appendChild(form);
  }

  function bind() {
    var m = $("maxpp"), og = $("only-great"), hw = $("hide-warnings"), q = $("search"), so = $("sort");
    syncControls();
    GROUPS.forEach(function (g) {
      var r = $("group-" + g);
      r.addEventListener("change", function () { if (r.checked) { f.group = g; update(); } });
    });
    m.addEventListener("input", function () { f.maxpp = Number(m.value); update(); });
    og.addEventListener("change", function () { f.onlyGreat = og.checked; update(); });
    hw.addEventListener("change", function () { f.hideWarn = hw.checked; update(); });
    q.addEventListener("input", function () { f.q = q.value.trim(); update(); });
    so.addEventListener("change", function () { f.sort = so.value; update(); });
    $("search-form").addEventListener("submit", function (e) { if (e) e.preventDefault(); q.blur(); });
    $("filters").addEventListener("submit", function (e) { if (e) e.preventDefault(); });
    $("clear-filters").addEventListener("click", clearFilters);
    $("clear-empty").addEventListener("click", clearFilters);

    // Filters apply as you change them. Done keeps them; Cancel puts back what was set when the sheet opened.
    var fd = $("filters-sheet");
    sheet(fd, $("open-filters"), [$("filters-done")]);
    $("open-filters").addEventListener("click", function () { beforeSheet = JSON.stringify(f); });
    $("filters-cancel").addEventListener("click", function () {
      if (beforeSheet) { f = JSON.parse(beforeSheet); syncControls(); update(); }
      closeSheet(fd);
    });
    sheet($("sources-sheet"), $("open-sources"), [$("sources-close")]);

    var wide = window.matchMedia ? window.matchMedia("(min-width: 60em)") : null;
    placeFilters(wide);
    if (wide && wide.addEventListener) wide.addEventListener("change", function () { placeFilters(wide); });
  }

  loadF();
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
