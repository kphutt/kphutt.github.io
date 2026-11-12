// Tiny view runtime for the storm test. No innerHTML, no eval: the page's markup is static HTML with data-*
// hooks; this file fills them from Component.renderVals() and re-renders after each setState.
//   data-t="path"            text content
//   data-a-NAME="tpl"        attribute (tpl may mix text and {{path}})
//   data-style="tpl"         inline style, applied through the CSSOM (allowed by style-src 'self')
//   data-on-click="path"     click handler; data-on-input likewise
//   <template data-for="path" data-as="x">  one copy of the template per list item
(function () {
  "use strict";
  var TPL = /\{\{\s*([^}]+?)\s*\}\}/g;
  function lookup(scope, path) {
    var parts = path.split("."), v = scope;
    for (var i = 0; i < parts.length; i++) { if (v == null) return undefined; v = v[parts[i]]; }
    return v;
  }
  function fill(tpl, scope) {
    return tpl.replace(TPL, function (_, p) { var v = lookup(scope, p); return v == null ? "" : String(v); });
  }
  function onlyPath(tpl) { var m = /^\{\{\s*([^}]+?)\s*\}\}$/.exec(tpl); return m ? m[1] : null; }

  // Compile a subtree (not descending into nested templates' content) into a list of binders.
  function compile(root, binders, lists) {
    var walker = [root];
    while (walker.length) {
      var el = walker.pop();
      if (el.nodeType !== 1) continue;
      if (el.tagName === "TEMPLATE" && el.hasAttribute("data-for")) { lists.push(makeList(el)); continue; }
      var attrs = el.attributes;
      for (var i = 0; i < attrs.length; i++) {
        var n = attrs[i].name, v = attrs[i].value;
        if (n === "data-t") binders.push(textBinder(el, v));
        else if (n === "data-style") binders.push(styleBinder(el, v));
        else if (n.indexOf("data-a-") === 0) binders.push(attrBinder(el, n.slice(7), v));
        else if (n.indexOf("data-on-") === 0) listen(el, n.slice(8), v, binders);
      }
      for (var c = el.lastElementChild; c; c = c.previousElementSibling) walker.push(c);
    }
  }
  function textBinder(el, path) {
    var last;
    return function (scope) { var v = lookup(scope, path); v = v == null ? "" : String(v); if (v !== last) { el.textContent = v; last = v; } };
  }
  function styleBinder(el, tpl) {
    var last, isStatic = tpl.indexOf("{{") < 0;
    return function (scope) {
      if (isStatic && last !== undefined) return;
      var v = fill(tpl, scope);
      if (v !== last) { el.style.cssText = v; last = v; }
    };
  }
  function attrBinder(el, name, tpl) {
    var last;
    return function (scope) {
      var v = fill(tpl, scope);
      if (v === last) return;
      last = v;
      if (name === "value" && "value" in el) { if (el.value !== v) el.value = v; }
      else el.setAttribute(name, v);
    };
  }
  function listen(el, type, path, binders) {
    var scope = null;
    binders.push(function (s) { scope = s; });
    el.addEventListener(type, function (e) { var f = scope && lookup(scope, path); if (typeof f === "function") f(e); });
  }
  function makeList(tpl) {
    var path = onlyPath("{{" + tpl.getAttribute("data-for") + "}}"), as = tpl.getAttribute("data-as");
    var parent = tpl.parentNode, items = [];
    return function (scope) {
      var list = lookup(scope, path) || [];
      for (var i = 0; i < list.length; i++) {
        if (!items[i]) {
          var frag = tpl.content.cloneNode(true), nodes = Array.prototype.slice.call(frag.childNodes), b = [], l = [];
          nodes.forEach(function (n) { compile(n, b, l); });
          var anchor = i ? items[i - 1].nodes[items[i - 1].nodes.length - 1].nextSibling : tpl.nextSibling;
          parent.insertBefore(frag, anchor);
          items[i] = { nodes: nodes, binders: b, lists: l };
        }
        var s = Object.create(scope); s[as] = list[i];
        items[i].binders.forEach(function (f) { f(s); });
        items[i].lists.forEach(function (f) { f(s); });
      }
      while (items.length > list.length) items.pop().nodes.forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
    };
  }

  function DCLogic() { this.state = {}; }
  var app = null, queued = false;
  DCLogic.prototype.setState = function (patch) {
    Object.assign(this.state, patch);
    if (!queued && app) { queued = true; Promise.resolve().then(render); }
  };
  function render() {
    queued = false;
    var vals = app.comp.renderVals();
    app.binders.forEach(function (f) { f(vals); });
    app.lists.forEach(function (f) { f(vals); });
  }
  window.DCLogic = DCLogic;
  window.__mount = function (Component) {
    var root = document.getElementById("app");
    app = { comp: new Component(), binders: [], lists: [] };
    compile(root, app.binders, app.lists);
    render();
    root.classList.add("ready");
  };
})();

// ---- The storm test's logic, carried over unchanged from the design prototype ----
class Component extends DCLogic {
  renderVals() {
    var st = this.state || {};
    var t = st.t == null ? 0 : st.t;
    var away = !!st.away;
    var prio = st.prio || {};
    var self = this;

    var playing = !!st.playing;
    // Playback pace: one redraw every 250 ms (fewer redraws for phones); steps scale so the storm moves at the same speed.
    var TICK_MS = 250, TICK_HOLD = Math.round(4000 / TICK_MS);
    function stopPlay() { if (self._timer) { clearInterval(self._timer); self._timer = null; } }
    function waitAt(a) {
      var list = (self._events || []).filter(function (e) { return Math.abs(e.a - a) < 1e-6; });
      if (!list.length) return false;
      if (!self.state || self.state.auto !== false) { self._hold = TICK_HOLD; self.setState({ t: a }); return true; }
      self.setState({ t: a });  // "I tap": the storm keeps going; the chore waits for a tap
      return true;
    }
    // Tap every chore that is waiting now (one tap covers all homes). Returns a state patch.
    function tapNow(extra, home) {
      var w = (self._waiting || []).filter(function (c) { return !home || c.home === home; }), tp = Object.assign({}, (self.state && self.state.taps) || {}), now = (self.state && self.state.t) || 0;
      w.forEach(function (c) { tp[c.home] = (tp[c.home] || []).concat([now]); });
      return Object.assign({ taps: tp, clicks: ((self.state && self.state.clicks) || 0) + w.length }, extra || {});
    }
    function freshTaps() { return { taps: {}, tapWin: self.state && self.state.auto === false ? [[0, 1e9]] : [] }; }
    // Move the clock. Jumping ahead, chores skipped over happen on time; jumping back, later taps are undone.
    function jumpTo(nt, extra) {
      var cur = (self.state && self.state.t) || 0, tapOn = !!self.state && self.state.auto === false;
      var wins = ((self.state && self.state.tapWin) || []).map(function (w) { return w.slice(); }), tp = (self.state && self.state.taps) || {};
      if (nt > cur) { wins.forEach(function (w) { if (w[1] > cur) w[1] = cur + 0.001; }); if (tapOn) wins.push([nt, 1e9]); }
      else if (nt < cur) {
        wins = wins.filter(function (w) { return w[0] <= nt; });
        var ntp = {}; Object.keys(tp).forEach(function (h) { ntp[h] = tp[h].filter(function (x) { return x <= nt; }); }); tp = ntp;
        var last = wins[wins.length - 1];
        if (tapOn) { if (!last || last[1] <= nt) wins.push([nt, 1e9]); else last[1] = 1e9; }
        else if (last && last[1] > nt) last[1] = nt + 0.001;
      }
      self.setState(Object.assign({ t: nt, tapWin: wins, taps: tp }, extra || {}));
    }
    function startPlay() {
      stopPlay();
      var start = t >= 336 ? 0 : t;
      self._hold = 0;
      if (start === 0) self.setState(Object.assign({ homeAt: null, leaveAt: self.state && self.state.away ? 0 : null }, freshTaps()));
      self.setState({ t: start, playing: true });
      self._timer = setInterval(function () {
        if (self._hold > 0) { self._hold--; self.setState({ holdLeft: self._hold }); return; }
        var cur = (self.state && self.state.t != null) ? self.state.t : start;
        var ev = self._events || [];
        var busy = ev.some(function (e) { return e.a <= cur + 1 && e.b > cur; });
        // While a chore waits for a tap, the clock slows to 0.1 h a tick (a fridge's 4 hours take about 10 s).
        var step = (self._waiting || []).length ? 0.1 : (busy ? 0.25 : (cur < 48 ? 0.5 : 1.5)) * TICK_MS / 150;
        var next = Math.min(336, cur + step);
        for (var i = 0; i < ev.length; i++) {
          if (ev[i].a > cur && ev[i].a <= next) { waitAt(ev[i].a); return; }
        }
        if (next >= 336) { stopPlay(); self.setState({ t: 336, playing: false }); }
        else self.setState({ t: next });
      }, TICK_MS);
      if (start === 0) waitAt(0);
    }
    var auto = st.auto == null ? true : !!st.auto;
    var clicks = st.clicks || 0;
    var calm = false; try { calm = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (err) {}
    if (!self._started) { self._started = true; if (!calm) setTimeout(function () { if (!self._timer) startPlay(); }, 700); }
    var BATT_H = 8.16, TANK_H = 16, GPH = 5.1 / 16;
    function dur(h) {
      if (h < 1) return Math.max(5, Math.round(h * 60 / 5) * 5) + " minutes";
      if (h < 48) return Math.round(h) + (Math.round(h) === 1 ? " hour" : " hours");
      var d = Math.round(h / 24 * 10) / 10;
      return d + " days";
    }
    var tot = 18 + t, day = Math.floor(tot / 24), hr = tot % 24;
    var mins = Math.round((hr % 1) * 60); if (mins === 60) mins = 0;
    var h12 = ((Math.floor(hr) + 11) % 12) + 1, ap = hr < 12 ? "AM" : "PM";
    var hm = h12 + ":" + (mins < 10 ? "0" : "") + mins + " " + ap;
    var clock = (day ? "Day " + (day + 1) + ", " : "") + hm;
    var night = hr >= 20 || hr < 7, dusk = !night && (hr >= 17 || hr < 8);
    var sky = night ? "#0E1A2B" : (dusk ? "#2C3E58" : "#5F7C9A");
    // Illustrative weather, not a forecast: a windstorm the first night, then a mix of rain, snow and hail.
    // Illustrative weather timeline, not a forecast: uneven spells, short hail bursts, a couple of snow days.
    var TL = [[0, "storm"], [11, "gusty"], [19, "heavysnow"], [34, "flurries"], [41, "freezing"], [50, "gray"], [61, "drizzle"], [75, "rain"], [92, "hail"], [96, "rain"], [107, "gray"], [138, "snow"], [160, "freezing"], [166, "gray"], [190, "drizzle"], [206, "rain"], [231, "gray"], [262, "hail"], [265, "rain"], [280, "gray"], [310, "drizzle"]];
    function wxAt(h) { var w = TL[0][1]; for (var i = 0; i < TL.length; i++) if (h >= TL[i][0]) w = TL[i][1]; return w; }
    function snowyNear(h) { for (var x = h; x >= h - 24; x -= 1) { var w = wxAt(x); if (w === "snow" || w === "heavysnow" || w === "flurries" || w === "freezing") return true; } return false; }
    // Snow on the ground, in inches: heavy snow piles up about a foot, rain melts it, cold days keep most of it.
    var RATE = { heavysnow: 0.8, snow: 0.35, flurries: 0.1, hail: 0.05, rain: -0.3, drizzle: -0.15, freezing: -0.05, gray: -0.03, storm: -0.3, gusty: -0.3 };
    var DEPTH = [0];
    for (var hh = 1; hh <= 400; hh++) DEPTH[hh] = Math.max(0, DEPTH[hh - 1] + RATE[wxAt(hh - 1)]);
    function depthAt(h) { return DEPTH[Math.max(0, Math.min(400, Math.floor(h)))]; }
    var WX = {
      storm: ["Windstorm, heavy rain", "gale", "precip rain", 0.9],
      gusty: ["Gusty, steady rain", "gusty", "precip rain", 0.6],
      rain: ["Cold rain", "gusty", "precip rain light", 0.45],
      snow: ["Snow, icy driveway", "", "precip snow", 0.9],
      hail: ["Hail showers", "gusty", "precip hail", 0.8],
      gray: ["Cold and gray", "", "precip", 0],
      flurries: ["Snow flurries", "", "precip snow", 0.45],
      heavysnow: ["Heavy snow", "gusty", "precip snow heavy", 1],
      freezing: ["Freezing rain: ice on everything", "gusty", "precip rain light", 0.55],
      drizzle: ["Drizzle", "", "precip rain light", 0.3]
    };
    var wxNow = wxAt(t), W = WX[wxNow];
    var weather = W[0], windCls = W[1], rainCls = W[2];
    var gust = 0.5 + 0.25 * Math.sin(t * 1.7) + 0.25 * Math.sin(t * 0.63 + 1);
    var rainOp = Math.round(W[3] * 0.55 * (0.75 + 0.25 * gust) * 100) / 100;
    var icy = wxNow === "hail" || snowyNear(t);
    var iceOp = icy ? "1" : "0";
    var depth = depthAt(t);
    if (icy && ["snow", "heavysnow", "hail", "freezing"].indexOf(wxNow) < 0) weather = weather + ", icy driveway";
    if (depth >= 1) weather = weather + " \u00B7 " + Math.round(depth) + " in of snow";
    var snowH = Math.min(11, depth * 0.9), snowCover = depth >= 1 ? "1" : "0";
    var stormy = wxNow === "storm";
    var cloudCls = "clouds" + (wxNow === "storm" || wxNow === "gusty" ? " fast" : "");
    var cloudOp = { storm: 0.95, gusty: 0.8, rain: 0.7, snow: 0.75, hail: 0.85, gray: 0.8, flurries: 0.6, drizzle: 0.65, heavysnow: 0.95, freezing: 0.8 }[wxNow];
    var flashCls = "flash" + (stormy ? " on" : ""), flashCls2 = "bolt" + (stormy ? " on" : "");
    var boltX = (12 + (Math.floor(t * 3) * 37) % 70) + "%";
    var rainSkew = { storm: "-24deg", gusty: "-16deg", rain: "-8deg", drizzle: "-4deg", freezing: "-10deg", hail: "-14deg" }[wxNow] || "-10deg";
    var debrisCls = "debris";
    var prevWx = wxAt(Math.max(0, t - 3));
    var daytime = !night && !dusk;
    var fogOp = wxNow === "drizzle" ? "0.35" : "0";
    var rainbowOp = false && daytime && (wxNow === "gray" || wxNow === "drizzle") && /rain|hail|gusty|storm/.test(prevWx) ? "1" : "0";
    var clearish = wxNow === "gray" || wxNow === "drizzle" || wxNow === "flurries";
    var orbOp = clearish ? (night ? "0.85" : "0.55") : "0";
    var orbBg = night ? "radial-gradient(circle at 65% 35%, transparent 52%, #F3F0DA 54%)" : "radial-gradient(circle, #FFF4C2 0 55%, rgba(255,244,194,0) 72%)";
    var orbGlow = night ? "none" : "0 0 30px 10px rgba(255,240,180,.35)";
    var puddleOp = depth < 1 && /rain|drizzle|storm|gusty/.test(wxNow) ? "0.7" : "0";
    var SNOWMAN = "none", snowmanOp = depth >= 6 && daytime ? "1" : "0";
    var snowmanFrom = null;
    for (var sh = 0; sh <= t; sh++) { var hr2 = (18 + sh) % 24; if (depthAt(sh) >= 6 && hr2 >= 8 && hr2 < 17) { snowmanFrom = sh; break; } }
    if (snowmanFrom !== null && t >= snowmanFrom && depth >= 3) snowmanOp = "1";
    var groundFill = depth >= 1 || wxNow === "snow" || wxNow === "heavysnow" || wxNow === "flurries" ? "#E8EEF4" : (wxNow === "hail" ? "#B9C6D3" : "#3E6B45");
    function slips(c) {
      if (!c || c.kind === "trip" && false) return false;
      var w = wxAt(c.a), n = Math.floor(c.a * 4);
      if (w === "hail" || snowyNear(c.a)) return n % 2 === 0;
      if (w === "storm" || w === "rain" || w === "gusty") return n % 5 === 2;
      return false;
    }
    var skyInk = "#F3F5F8", skyLine = "rgba(243,245,248,0.45)";

    var defs = [
      { k: "gas", short: "Gasoline portable", how: "Roll out, keep refilling", name: "Gasoline portable", model: "Honda EU7000iS", accent: "#D9711A", installed: 6770, gen: "gas", batt: false, move: "Roll out a 263 lb generator", noise: "52-58 dB" },
      { k: "batt", short: "Battery", how: "Plug in; drains overnight", name: "Battery", model: "Anker SOLIX F3800 Plus", accent: "#0E8F7E", installed: 4340, gen: "none", batt: true, move: "Nothing, if it stays parked by the plug", noise: "Silent" },
      { k: "combo", short: "Battery + gasoline", how: "Plug in, later refill", name: "Battery + gasoline", model: "Anker battery + Honda", accent: "#7A52C2", installed: 10190, gen: "gas", batt: true, move: "A 263 lb generator, once the battery dies", noise: "Silent, then 52-58 dB" },
      { k: "ngp", short: "Nat-gas portable", how: "Roll out; oil every 8 h", name: "Natural-gas portable", model: "Westinghouse WGen9500TFc", accent: "#2F5BD3", installed: 4600, gen: "ngp", batt: false, move: "Roll out a 215 lb generator, hook up gas", noise: "74 dB" },
      { k: "ngs", short: "Nat-gas standby", how: "Starts itself; oil daily", name: "Natural-gas standby", model: "Generac 10 to 14 kW, installed outside", accent: "#1F8A4C", installed: 10800, gen: "ngs", batt: false, move: "Nothing", noise: "61-65 dB" }
    ];

    // Event model. Gasoline: a 5.1 gal tank lasts about 16 h at about 400 W; refilling takes about 15 min
    // with the engine off; 10 gal stored in cans covers two refills, then someone drives out for more.
    var SETUP = { gas: 0.5, batt: 0.25, combo: 0.25, ngp: 0.75 };
    // Vacation window [L, H): nobody home. Anything already running keeps going until it needs a person.
    var L = away ? (st.leaveAt || 0) : (st.homeAt != null ? (st.leaveAt || 0) : 1e9);
    var H = away ? 1e9 : (st.homeAt != null ? st.homeAt : 1e9);
    // Clock: t = 0 is 6 PM. The combo's generator runs 7 AM to 11 PM.
    function comboOn(h) { var c = ((18 + h) % 24 + 24) % 24; return c >= 7 && c < 23; }
    function lastOffStart(h) { return 5 + 24 * Math.floor((h - 5) / 24); }
    var NGP_OIL = 8, SB_OIL = 24;  // oil checks: Westinghouse (related model) every 8 h; Generac every 24 h on multi-day runs
    // One schedule engine for the storm and the sandbox: who must do what, given a vacation window [L, H).
    // G (optional) makes chores wait for a tap: { taps: {home: [tap times, in order]}, wins: [[from, to)], t: now }.
    // A chore due inside a tap window starts when it is tapped; until then it waits and its home pays for it.
    var OIL_GRACE = 6;  // illustrative: hours a skipped oil check keeps running before the low-oil switch shuts it off
    function makePlanner(L, H, G) {
      function present(h) { return !(h >= L && h < H); }
      function nextPresent(h) { return present(h) ? h : H; }
    function plan(d) {
      var dark = [], chores = [], gasRuns = [], battEnd = null, oilDue = null;
      var out = function () { return { dark: dark, chores: chores, gasRuns: gasRuns, battEnd: battEnd, oilDue: oilDue }; };
      var tapList = (G && G.taps && G.taps[d.short]) || [], ti = 0;
      function gated(e) { if (!G || !G.wins) return false; for (var i = 0; i < G.wins.length; i++) if (e >= G.wins[i][0] - 1e-6 && e < G.wins[i][1]) return true; return false; }
      // Schedule a chore due at e that takes dur hours; returns when it actually starts.
      function job(e, dur, kind, who, text, ask, btn, what) {
        var s = e, wait = false;
        if (kind !== "pack" && gated(e) && present(e)) {
          var tp = tapList[ti];
          if (tp != null && tp <= G.t + 1e-6) { ti++; s = Math.max(e, tp); }
          else if (e <= G.t + 1e-6) { s = G.t; wait = true; }
        }
        var dp = Math.round(depthAt(s)), w0 = wxAt(s);
        if (dp >= 4) ask = (kind === "trip" ? "Dig the car out of " + dp + " in of snow first. " : "Shovel a path through " + dp + " in of snow first. ") + ask;
        else if (w0 === "freezing") ask = "Everything is glazed in ice. " + ask;
        chores.push({ a: s, b: s + dur, due: e, wait: wait, kind: kind, who: who, text: text, ask: ask, btn: btn, what: what || kind, home: d.short });
        return s;
      }
      // A skipped oil check: the engine keeps running until the low-oil switch stops it.
      function oilCheck(due, dur, who, text, ask) {
        var s = job(due, dur, "oil", who, text, ask, "Check the oil");
        if (s > due + OIL_GRACE) dark.push([due + OIL_GRACE, s]);
        dark.push([s, s + dur]);
        return s + dur;
      }
      var nOil = 0;
      if (d.gen === "ngs") {
        dark.push([0, 0.01]);
        var x0 = 0.01;
        while (x0 < 400) {
          var due = x0 + SB_OIL;
          if (!present(due)) { oilDue = due; if (H >= 1e9) break; due = H; }
          nOil++;
          x0 = oilCheck(due, 0.5, nOil % 2 ? "Husband" : "Wife", "Daily oil check, off 30 minutes", (due > x0 + SB_OIL ? "Home from vacation; it ran with nobody checking the oil. " : "") + "Every 24 hours: shut it off for 30 minutes, check and top up the oil, restart.");
        }
        return out();
      }
      if (d.k === "combo") return planCombo();
      var SU = { gas: 0.5, batt: 0.25, combo: 0.25, ngp: 0.75 }[d.k];
      var setupGen = { gas: ["Pushing out the 263 lb generator in the storm", "Go out in the storm. Push out the 263 lb generator, plug in, start it."], combo: ["Battery dead: out in the dark with the generator", "Battery's dead. Go out in the dark, push out the 263 lb generator."], ngp: ["Pushing out 215 lb, hooking up gas", "Go out in the storm. Push out the 215 lb generator, hook up gas, start it."] }[d.k];
      var x = nextPresent(0);
      if (x > 0) dark.push([0, x]);
      if (x >= 1e9) return out();
      var back = x > 0 ? "Home from vacation, house dark. " : "";
      if (d.batt) {
        var sb0 = job(x, SU, "setup", "Wife", "Plugging in the battery", back + "Run the cord from the battery to the outside plug and flip the panel switch.", "Plug it in", "batt");
        dark.push([x, sb0 + SU]);
        battEnd = sb0 + SU + BATT_H;
        if (d.gen === "none") { dark.push([battEnd, 1e9]); return out(); }
        x = nextPresent(battEnd);
        if (x > battEnd) dark.push([battEnd, x]);
        if (x >= 1e9) return out();
        back = x > battEnd ? "Home from vacation; the battery died while you were gone. " : "";
        SU = 0.5;
      }
      var k = 0;
      function setup(at, prefix) {
        var s = job(at, SU, "setup", "Husband", setupGen[0], prefix + setupGen[1], "Go outside", "gen");
        dark.push([at, s + SU]);
        return s + SU;
      }
      x = setup(x, back);
      var LL = L;
      while (x < 400) {
        var ev = x + (d.gen === "gas" ? TANK_H : NGP_OIL);
        if (LL < 1e9 && LL < ev && H > x) {  // leaving during this run (or while it was still being set up)
          var P = Math.max(LL, x); LL = 1e9;
          if (d.gen === "gas") gasRuns.push([x, P]);
          job(P, 0.25, "pack", "Husband", "Packing it up before leaving", "Leaving? It can't run with nobody home. Shut it off, let it cool, unhook it, roll it back in.", "Pack it up", "gen");
          dark.push([P, H]);
          if (H >= 1e9) break;
          x = setup(H, "Home from vacation, house dark. ");
          continue;
        }
        if (d.gen === "gas") {
          gasRuns.push([x, ev]); k++;
          var far = k % 4 === 3, ready = 0;
          if (k >= 3 && k % 2 === 1) { var td = far ? 2.5 : 1.5; ready = job(ev - (far ? 4 : 3), td, "trip", "Wife", far ? "Nearest station had no power; driving farther" : "Driving out for gas, if a station has power", "Gas cans are empty. The husband doesn't drive anymore, so the wife goes, in this weather, hoping a station has power.", "Drive to the station") + td; }
          var rs = job(Math.max(ev, ready), 0.25, "refill", k % 2 ? "Husband" : "Wife", "Refilling and checking oil, engine off", "Tank almost empty. Go out, shut it off, let it cool, refill, check the oil.", "Go refill it");
          dark.push([ev, rs + 0.25]);
          x = rs + 0.25;
        } else {
          nOil++;
          x = oilCheck(ev, 0.25, nOil % 2 ? "Husband" : "Wife", "Oil check, engine off", "Every 8 hours: go out, shut it off, check and top up the oil, restart.");
        }
      }
      return out();
      // Battery + gasoline, run the way its supporters describe: the Honda runs 7 AM to 11 PM, powering the
      // house and recharging the battery through Anker's generator input adapter; the battery carries 11 PM to
      // 7 AM. The Honda app starts and stops it (no trip). Refills happen in the morning, by day.
      function planCombo() {
        var x = nextPresent(0);
        if (x > 0) dark.push([0, x]);
        if (x >= 1e9) return out();
        var back = x > 0 ? "Home from vacation, house dark. " : "";
        var s1 = job(x, 0.25, "setup", "Wife", "Plugging in the battery", back + "Run the cord from the battery to the outside plug and flip the panel switch.", "Plug it in", "batt");
        if (s1 > x) dark.push([x, s1 + 0.25]);  // plugged in late: dark until the plug-in is done
        var LL = L;
        var t0 = setupGen(s1 + 0.25, "Push out the 263 lb generator and connect it to the battery. After this, the phone app starts and stops it.", s1 + 0.25 + BATT_H);
        var k = 0;
        while (t0 < 400) {
          var stopAt = (LL < 1e9 && H > t0) ? Math.max(LL, t0) : 400, skip = false;
          for (var n = 0; n < 18; n++) {
            var r = -11 + 24 * n, e0 = Math.min(5 + 24 * n, stopAt);
            if (r >= stopAt) break;
            if (skip) { skip = false; continue; }
            var s0 = Math.max(r, t0);
            if (r > t0) {
              k++;
              var ready = 0;
              if (k >= 3 && k % 2 === 1 && r - 18 > t0) ready = job(r - 18, 1.5, "trip", "Wife", "Driving out for gas, if a station has power", "Gas cans are empty. The husband doesn't drive anymore, so the wife goes, hoping a station has power.", "Drive to the station") + 1.5;
              var rs = job(Math.max(r, ready), 0.25, "refill", k % 2 ? "Husband" : "Wife", "Morning refill and oil check; the battery covers", "Morning: go out, refill the generator and check the oil. The battery keeps the house on meanwhile.", "Go refill it");
              var battDie = r - 8 + BATT_H;  // the battery carried the night from 11 PM
              if (rs > battDie) dark.push([battDie, rs + 0.25]);  // battery ran out before the refill: dark until it's done
              s0 = Math.max(s0, rs);
              if (rs >= e0) {  // refilled after today's run would have ended: it runs straight through the next day
                var e1 = Math.min(e0 + 24, stopAt);
                if (e1 > rs) gasRuns.push([rs, e1]);
                skip = true;
                continue;
              }
            }
            if (e0 > s0) gasRuns.push([s0, e0]);
          }
          if (stopAt >= 400) break;
          job(LL, 0.25, "pack", "Husband", "Packing up the generator before leaving", "Leaving? The generator can't run with nobody home. Shut it off, unhook it, roll it back in. The battery stays plugged in.", "Pack it up", "gen");
          var rem = comboOn(LL) ? BATT_H : Math.max(0, BATT_H - (LL - lastOffStart(LL)));
          var dk = LL + rem;
          if (H >= 1e9) { dark.push([dk, 1e9]); break; }
          if (dk < H) dark.push([dk, H]);
          t0 = setupGen(H, "Home from vacation. Push the generator back out and connect it to the battery.", Math.max(H, dk));
          LL = 1e9;
        }
        return out();
      }
      function setupGen(at, ask, battDeadAt) {
        var s = job(at, 0.5, "setup", "Husband", "Pushing out the 263 lb generator", ask, "Go outside", "gen");
        if (s > battDeadAt) dark.push([battDeadAt, s + 0.5]);
        return s + 0.5;
      }
    }
      return { plan: plan, present: present };
    }
    var G = st.tapWin && st.tapWin.length ? { taps: st.taps || {}, wins: st.tapWin, t: t } : null;
    var planner = makePlanner(L, H, G), plan = planner.plan, present = planner.present;

    function sim(d) {
      var P = plan(d), r = { on: true, covered: t, refills: 0, gal: 0, trips: 0, chore: null, plan: P, waiting: [] };
      r.darkSince = null;
      P.dark.forEach(function (iv) {
        if (iv[0] <= t && t < iv[1]) { r.on = false; r.darkSince = r.darkSince == null ? iv[0] : Math.min(r.darkSince, iv[0]); }
        r.covered -= Math.max(0, Math.min(t, iv[1]) - iv[0]);
      });
      if (t === 0 && d.gen !== "ngs") r.on = false;
      r.covered = Math.max(0, r.covered);
      P.chores.forEach(function (c) {
        if (c.wait) { r.waiting.push(c); return; }
        if (c.a <= t) { r.trips++; if (c.kind === "refill") r.refills++; }
        if (c.a <= t && t < c.b) r.chore = c;
      });
      var gh = 0;
      P.gasRuns.forEach(function (g) { gh += Math.max(0, Math.min(t, g[1]) - g[0]); });
      r.gal = Math.round(gh * GPH);
      return r;
    }
    // Food safety (USDA): a closed fridge keeps food safe about 4 hours without power; a full freezer about 48.
    function darkRuns(dark, upto) {
      var iv = dark.map(function (x) { return [Math.max(0, x[0]), Math.min(upto, x[1])]; }).filter(function (x) { return x[1] - x[0] > 0.02; }).sort(function (a, b) { return a[0] - b[0]; });
      var m = []; iv.forEach(function (x) { var l = m[m.length - 1]; if (l && x[0] <= l[1] + 0.01) l[1] = Math.max(l[1], x[1]); else m.push([x[0], x[1]]); });
      return m;
    }
    function food(dark, upto) {
      var runs = darkRuns(dark, upto), longest = 0, cur = 0;
      runs.forEach(function (x) { longest = Math.max(longest, x[1] - x[0]); if (x[1] >= upto - 0.01) cur = x[1] - x[0]; });
      return { fridgeLost: longest >= 4, freezerLost: longest >= 48, cur: cur, longest: longest };
    }
    var events = [];
    // Each yard has its own tree: [trunk x, y, w, h, canopy path, color, shade path, sway style]. Base at (204.5, 57).
    var TREE = {
      gas: [203.4, 46, 2.2, 11, "M204.5 12L196.5 27H200.5L193 39H198L190.5 50H218.5L211 39H216L208.5 27H212.5Z", "#1F5A36", "M204.5 12L208.5 27H212.5L205 27Z", "v4"],
      batt: [202, 37, 5, 20, "M189 33a8 8 0 0 1 9-10a9 9 0 0 1 15 0a8 8 0 0 1 8 10a7 7 0 0 1-6 8h-20a7 7 0 0 1-6-8Z", "#2C5E38", "M200 41h15a7 7 0 0 0 6-8a8 8 0 0 1-4 5h-17Z", "v3"],
      combo: [202.6, 39, 3.8, 18, "M192.5 30a12 12 0 1 0 24 0a12 12 0 1 0-24 0Z", "#2F6B3F", "M204.5 42a12 12 0 0 0 12-12a10 10 0 0 1-12 9Z", "v1"],
      ngp: [203.5, 43, 2.6, 14, "M198.6 27a6.2 17 0 1 0 12.4 0a6.2 17 0 1 0-12.4 0Z", "#3A7D4B", "M204.8 44a6.2 17 0 0 0 6.2-17a5 15 0 0 1-6.2 14Z", "v2"],
      ngs: [203.2, 45, 3, 12, "M195 38a9.5 7.5 0 1 0 19 0a9.5 7.5 0 1 0-19 0Z", "#4C8A45", "M204.5 45.5a9.5 7.5 0 0 0 9.5-7.5a8 6 0 0 1-9.5 5Z", "v5"]
    };
    var LEAF = { k: "none", at: 1e9 };  // no random tree events: the storm is the same on every play
    var FLY = {};
    var sims = {}, pending = [];
    defs.forEach(function (d) { sims[d.k] = sim(d); pending = pending.concat(sims[d.k].waiting); });
    pending.sort(function (x, y) { return x.due - y.due; });
    self._waiting = pending;
    var opts = defs.map(function (d) {
      var r = sims[d.k];
      r.plan.chores.forEach(function (c) {
        if (c.wait) return;
        events.push(c);
        if (c.kind === "trip") events.push({ a: c.b - 0.5, b: c.b, kind: "tripback", home: d.short, who: "Wife", ask: "Fill the cans, lift about 60 lb of gas into the car, drive home in this weather.", btn: "Drive home" });
      });
      var cover;
      if (t === 0) cover = d.gen === "ngs" ? "Starts by itself in about 30 seconds" : (away ? "Nobody home to set it up" : "Waiting for someone to set it up");
      else if (d.gen !== "ngs" && L === 0 && !present(t)) cover = "Dark: nobody home to set it up";
      else if (r.on) cover = "Power the whole " + dur(t);
      else cover = "Dark for the last " + dur(t - r.covered);
      var refuel;
      if (d.gen === "ngs" || d.gen === "ngp") refuel = "Never: house gas line";
      else if (d.gen === "none") refuel = "Can't: no way to recharge";
      else if (r.gal > 0) refuel = r.refills + (r.refills === 1 ? " refill, " : " refills, ") + r.gal + " gal burned so far";
      else refuel = "Every 6 to 16 hours";
      var c = r.chore, now, nowInk = "#14532D";
      var dk = r.on || r.darkSince == null ? 0 : t - r.darkSince;
      var fd = food(r.plan.dark, t);
      var slipping = !!c && slips(c) && !r.waiting.length && (c.kind !== "trip" || t < c.a + 0.5);
      if (r.waiting.length) {
        var w0 = r.waiting[0], late = t - w0.due;
        now = w0.kind === "oil" && late > OIL_GRACE ? "Oil unchecked " + dur(late) + ": it shut itself off" : "Needs the " + w0.who.toLowerCase() + (late >= 0.25 ? " \u00B7 " + (late < 1 ? Math.round(late * 60) + " min" : dur(late)) + " late" : " outside");
        nowInk = r.on ? "#B4480C" : "#9B1C1C";
      }
      else if (c) { now = slipping ? c.who + " slipped outside; caught " + (c.who === "Wife" ? "herself" : "himself") : c.who + ": " + c.text.charAt(0).toLowerCase() + c.text.slice(1); nowInk = "#B4480C"; }
      else if (!present(t) && !r.on) { now = "Dark: away on vacation"; nowInk = "#9B1C1C"; }
      else if (d.gen === "ngs" && !present(t)) { now = r.plan.oilDue != null && t > r.plan.oilDue ? "Nobody home: oil unchecked " + dur(t - r.plan.oilDue + 24) : "Nobody home: running"; nowInk = r.plan.oilDue != null && t > r.plan.oilDue ? "#B4480C" : "#14532D"; }
      else if (!present(t)) {
        var nd = r.plan.dark.filter(function (iv) { return iv[0] > t; }).map(function (iv) { return iv[0]; }).sort(function (x, y) { return x - y; })[0];
        now = nd != null && nd < 1e9 ? "Nobody home: goes dark in " + dur(nd - t) : "Nobody home";
      }
      else if (d.gen === "ngs") now = t < 0.01 ? "Starting by itself" : "Running on its own";
      else if (!r.on && dk >= 12) { now = "Dark and cold: no heat for " + dur(dk); nowInk = "#9B1C1C"; }
      else if (!r.on && dk >= 4) { now = "Dark and getting cold"; nowInk = "#9B1C1C"; }
      else if (!r.on) { now = "Dark: nothing left to run"; nowInk = "#9B1C1C"; }
      else now = d.k === "combo" && !comboOn(t) ? "Running on the battery overnight" : (d.batt && t < (r.plan.battEnd || 0) ? "Running on the battery" : "Running");
      var doing = c ? ({ trip: "Out for gas", refill: "Refilling", oil: "Oil check", pack: "Packing up" }[c.kind] || "Outside") : null;
      var lastMove = r.plan.chores.filter(function (ch) { return (ch.kind === "setup" && ch.what === "gen" || ch.kind === "pack") && ch.a <= t; }).pop();
      var deployed = !!lastMove && lastMove.kind === "setup";
      var inBatt = d.batt && r.plan.battEnd != null && t < r.plan.battEnd;
      var plugged = d.batt && r.plan.chores.some(function (ch) { return ch.what === "batt" && ch.b <= t; }) && (inBatt || d.gen !== "none");
      var genLive = (d.gen === "gas" || d.gen === "ngp") && deployed && r.on && !inBatt && !(d.k === "combo" && !comboOn(t));
      var nz = !r.on ? 0 : (d.gen === "ngs" ? 2 : (genLive ? (d.gen === "ngp" ? 3 : 1) : 0));
      var waitingHere = r.waiting.length > 0, firstWaiting = waitingHere, waitingBack = false;
      if (waitingHere) c = null;
      var tripPhase = c && c.kind === "trip" ? (t < c.a + 0.5 ? "out" : (t >= c.b - 0.5 && !waitingBack ? "back" : "away")) : "";
      var handsOff = d.gen === "ngs";
      return {
        smokeOp: r.on ? "1" : "0",
        candleOp: !r.on && present(t) && t > 0.3 ? "1" : "0",
        cordOp: genLive ? "1" : "0",
        hoseOp: d.gen === "ngp" && deployed ? "1" : "0",
        battCordOp: d.batt && plugged ? "1" : "0",
        noiseX: d.gen === "ngs" ? "64" : "157",
        n1: nz >= 1 ? "1" : "0", n2: nz >= 2 ? "1" : "0", n3: nz >= 3 ? "1" : "0",
        fridgeTxt: fd.freezerLost ? "Fridge + freezer lost" : (fd.fridgeLost ? "Fridge food lost" : (fd.cur > 0.3 ? "Warming: " + Math.min(4, Math.round(fd.cur)) + " of 4 h" : "Fridge cold")),
        fridgeInk: fd.fridgeLost ? "#FFFFFF" : "#13202E",
        fridgeBg: fd.fridgeLost ? "#9B1C1C" : (fd.cur > 0.3 ? "#FFC94D" : "#BFE3FF"),
        fridgeCard: fd.freezerLost ? "Fridge and freezer food lost (dark " + dur(fd.longest) + ")" : (fd.fridgeLost ? "Fridge food lost (dark " + dur(fd.longest) + "); freezer OK under 48 h" : (fd.cur > 0.3 ? "Warming: safe for about " + dur(Math.max(0, 4 - fd.cur)) + " more" : "Cold and safe")),
        name: d.name, short: d.short, how: d.how, snowmanOp: d.k === SNOWMAN ? snowmanOp : "0", sbOp: d.gen === "ngs" ? "1" : "0", model: d.model, accent: d.accent,
        win: r.on ? "#FFC94D" : (dk >= 12 ? "#7FA6CF" : "#3A4656"),
        outs: (function () {
          var P = 0, N = 0;
          r.plan.chores.forEach(function (ch) {
            var done = !ch.wait && ch.a <= t;
            if (done) { if (ch.who === "Husband") P++; else N++; }
          });
          return "Husband " + P + " \u00B7 Wife " + N;
        })(),
        outsInk: d.gen === "ngs" ? "#8EE3A8" : "#FFB27A",
        who: c ? c.who : "", pColor: c && c.who === "Wife" ? "#F27BC0" : "#FF8A3D",
        trunkX: TREE[d.k][0], trunkY: TREE[d.k][1], trunkW: TREE[d.k][2], trunkH: TREE[d.k][3], treeD: TREE[d.k][4], treeFill: TREE[d.k][5], treeD2: TREE[d.k][6], swayCls: TREE[d.k][7],
        leafOp: d.k === LEAF.k && t >= LEAF.at ? "0" : "1",
        bareOp: d.k === LEAF.k && t >= LEAF.at ? "1" : "0",
        shedCls: d.k === LEAF.k && t >= LEAF.at - 0.5 && t < LEAF.at + 2 ? "shed on" : "shed",
        treeCls: FLY[d.k] != null && t >= FLY[d.k] ? "uproot" : "",
        stumpOp: FLY[d.k] != null && t >= FLY[d.k] ? "1" : "0",
        rowBg: firstWaiting ? "rgba(255,138,61,0.28)" : "rgba(255,255,255,0.07)",
        rowRing: firstWaiting ? "inset 0 0 0 2px #FF8A3D" : "none",
        litWord: waitingHere ? "Needs you" : ((doing ? doing : (d.gen === "ngs" && t < 0.01 ? "Starting" : (r.on ? "On" : "Dark")))),
        aria: d.short + ": " + now,
        now: now, nowInk: nowInk,
        carOp: c && c.kind === "trip" ? "0" : "1",
        tripOp: tripPhase === "away" ? "1" : "0",
        glowOp: r.on && !c ? "0.45" : "0",
        genCls: c && c.kind === "setup" && c.what === "gen" ? "g-push" : (c && c.kind === "pack" ? "g-pack" : ""),
        battCls: "",
        carCls: tripPhase === "out" ? "c-drive" : (tripPhase === "away" ? "c-gone" : (tripPhase === "back" ? "c-back" : "")),
        personCls: !c || waitingHere ? "" : (c.kind === "trip" ? (tripPhase === "back" ? "p-home" : (tripPhase === "out" ? "p-trip" : ""))  : (c.kind === "oil" && d.gen === "ngs" ? "p-batt" : c.kind === "refill" || c.kind === "oil" ? "p-refill" : (c.what === "batt" ? "p-batt" : "p-gen"))),
        battOp: d.batt ? "1" : "0",
        canOp: c && c.kind === "refill" ? "1" : "0",
        slipCls: slipping && c ? "slip" : "",
        whoaOp: slipping && c ? "1" : "0",
        genOp: d.gen === "none" || d.gen === "ngs" ? "0" : ( (c && c.what === "gen") ? "1" : (deployed && !(d.batt && t < (r.plan.battEnd || 0)) ? "1" : "0.15")),
        personOp: c && !waitingHere && tripPhase !== "away" ? "1" : "0",
        ring: r.on ? "#FFC94D" : "#E5484D",
        ringBg: r.on ? "#FFF4D6" : (dk >= 12 ? "#1F3550" : "#2A3442"),
        houseInk: r.on ? "#13202E" : "#9AA6B4",
        wordBg: waitingHere || doing ? "#FF8A3D" : (r.on ? "#FFC94D" : "#E5484D"),
        wordInk: waitingHere || doing ? "#13202E" : (r.on ? "#13202E" : "#FFFFFF"),
        barPct: (t > 0 ? Math.round(r.covered / t * 100) : (r.on ? 100 : 0)) + "%",
        cover: cover,
        move: d.move,
        refuel: refuel,
        trips: r.trips === 0 ? "None" : String(r.trips),
        noise: r.on ? d.noise : "Quiet: nothing running",
        installed: "~$" + d.installed.toLocaleString("en-US"),
        priceNote: { ngp: "installed ($3,500 to $7,300)", ngs: "installed, Seattle-area quotes ($8,500 to $13,000)" }[d.k] || "installed",
        badge: handsOff ? "Starts itself" : "Someone goes outside",
        badgeBg: handsOff ? "#FFF4D6" : "#FF8A3D",
        badgeInk: "#13202E",
        outline: "none",
        wordDisp: waitingHere ? "none" : "inline-block",
        tapDisp: waitingHere ? "inline-block" : "none",
        tapLabel: waitingHere ? r.waiting[0].btn : "",
        tapAria: waitingHere ? d.short + ": " + r.waiting[0].btn : "",
        tap: function () { self.setState(tapNow(null, d.short)); }
      };
    });

    self._events = events.filter(function (e) { return true; }).sort(function (x, y) { return x.a - y.a; });
    var waitHomes = pending.map(function (c) { return c.home; }).filter(function (h, i, a) { return a.indexOf(h) === i; });
    var busyNow = opts.filter(function (o) { return o.personOp === "1" || o.carOp === "0"; });
    var holdLeft = st.holdLeft || 0;
    var holdInfo = "";
    if (!pending.length && playing && auto && holdLeft > 0 && busyNow.length) {
      var secs = Math.max(1, Math.ceil(holdLeft * TICK_MS / 1000));
      holdInfo = (busyNow.length === 1 ? busyNow[0].short + ": " + busyNow[0].now.charAt(0).toLowerCase() + busyNow[0].now.slice(1) : busyNow.length + " homes have someone outside") + ". Continues in " + secs + " s.";
    }
    var caption;
    var flew = defs.filter(function (d) { return FLY[d.k] != null && t >= FLY[d.k] && t < FLY[d.k] + 1.5; })[0];
    if (!busyNow.length && flew) caption = hm + ". The wind just knocked over the tree in the " + flew.short + " yard.";
    else if (busyNow.length === 1) caption = hm + ". " + busyNow[0].short + " home: " + busyNow[0].now.charAt(0).toLowerCase() + busyNow[0].now.slice(1) + (busyNow[0].short === "Nat-gas standby" ? "." : ". The standby: nothing to do right now.");
    else if (busyNow.length > 1) caption = hm + ". " + busyNow.length + " of 5 homes have someone outside in the storm (" + busyNow.map(function (o) { return o.short; }).join(", ") + "). The standby starts by itself.";
    else if (away && L > 0) caption = "On vacation. The portable generators had to be packed up before leaving, and the battery drains on its own. Only the standby keeps the lights on, with nobody checking its oil.";
    else if (away) caption = t === 0 ? "You're on vacation when it hits. Only the standby starts; everything else is still in the garage." : "On vacation for " + dur(t) + ". The standby has kept the house running on its own. Every other home has been dark the whole time.";
    else if (t === 0) caption = "6 PM: the power goes out. The standby starts by itself. Everything else waits for someone to go outside, roll it out and plug it in.";
    else if (t < SETUP.ngp + 0.25) caption = "Every home except the standby needed someone outside in the storm just to get the lights back on.";
    else if (t < BATT_H) caption = "Everything is running. The battery is silent, the generators are not.";
    else if (t < 16) caption = "Overnight, the battery runs out. With battery + gasoline, someone now rolls out the generator in the dark.";
    else if (t < 48) caption = "The next day. The gasoline generators need refilling every 6 to 16 hours, after the engine cools.";
    else if (t < 144) caption = "Day " + (day + 1) + ". In the November 2024 bomb cyclone, many homes here were still out after a week.";
    else caption = "Day " + (day + 1) + ". Gasoline portable: " + Math.round(t * GPH) + " gallons fetched and carried so far. Natural gas: no refills at all.";
    var bt = opts.filter(function (o) { return o.short === "Battery"; })[0];
    var bEnd = BATT_H + 0.25;
    if (!busyNow.length && !flew && !away && t >= bEnd + 4 && t < bEnd + 6) caption = hm + ". The battery house has been dark 4 hours. A closed fridge keeps food safe about 4 hours without power (USDA), and their gas furnace can't run without electricity.";
    else if (!busyNow.length && !flew && !away && t >= bEnd + 12 && t < bEnd + 14) caption = hm + ". 12 hours dark at the battery house: no heat since early morning. The windows are frosting over.";
    if (!busyNow.length && !flew && !away && rainbowOp === "1") caption = hm + ". The rain lets up and a rainbow comes out. The power is still off.";
    else if (false) caption = hm + ". " + Math.round(depth) + " inches of snow. The battery house built a snowman; it has been dark since the first night.";
    var stripped = defs.filter(function (d) { return d.k === LEAF.k; })[0];
    if (!busyNow.length && !flew && !away && t >= LEAF.at && t < LEAF.at + 1.5) caption = hm + ". The wind just stripped every leaf off the tree in the " + stripped.short + " yard.";

    var presets = [["6 PM", 0], ["8 hours", 8], ["1 day", 24], ["2 days", 48], ["1 week", 168], ["2 weeks", 336]].map(function (p) {
      var on = t === p[1];
      return { label: p[0], bg: on ? "#F3F5F8" : "transparent", ink: on ? "#13202E" : skyInk, pick: function () { stopPlay(); jumpTo(p[1], { playing: false }); } };
    });

    // ---- Sandbox: pick a setup, an outage length and who is home; get the totals instantly ----
    var SB = st.sb || { k: "gas", T: 168, home: "home" };
    var HOME_OPTS = { home: [1e9, 1e9, "Home"], start: [0, 1e9, "Away"], mid: [24, 96, "Away days 2\u20134"] };
    function isNightHour(h) { var hr = (18 + h) % 24; return hr >= 20 || hr < 7; }
    function hardAt(h) { return isNightHour(h); }  // compared numbers use the clock only, never the illustrative weather
    function runSandbox(k, T, homeKey) {
      var hw = HOME_OPTS[homeKey], d = defs.filter(function (x) { return x.k === k; })[0];
      var P = makePlanner(hw[0], hw[1]).plan(d);
      var ch = P.chores.filter(function (c) { return c.a < T; });
      var husband = ch.filter(function (c) { return c.who === "Husband"; }).length;
      var wife = ch.length - husband;
      var hard = ch.filter(function (c) { return hardAt(c.a); }).length;
      var runs = ch.filter(function (c) { return c.kind === "trip"; }).length;
      var gh = 0; P.gasRuns.forEach(function (g) { gh += Math.max(0, Math.min(T, g[1]) - g[0]); });
      var iv = P.dark.map(function (x) { return [Math.max(0, x[0]), Math.min(T, x[1])]; }).filter(function (x) { return x[1] - x[0] > 0.02; }).sort(function (a, b) { return a[0] - b[0]; });
      var merged = [];
      iv.forEach(function (x) { var m = merged[merged.length - 1]; if (m && x[0] <= m[1] + 0.01) m[1] = Math.max(m[1], x[1]); else merged.push([x[0], x[1]]); });
      var darkH = 0, longest = 0; merged.forEach(function (x) { darkH += x[1] - x[0]; longest = Math.max(longest, x[1] - x[0]); });
      var spoil = merged.filter(function (x) { return x[1] - x[0] >= 4; }).length;
      return { spoil: spoil, d: d, chores: ch, n: ch.length, husband: husband, wife: wife, hard: hard, runs: runs, gal: Math.round(gh * GPH), darkH: darkH, longest: longest, dark: merged };
    }
    function hrs(h) { return h < 1 ? (h < 0.05 ? "none" : "under 1 h") : (h < 48 ? Math.round(h) + " h" : (Math.round(h / 24 * 10) / 10) + " days"); }
    var R = runSandbox(SB.k, SB.T, SB.home), RS = runSandbox("ngs", SB.T, SB.home);
    var lenLabel = { 24: "1 day", 72: "3 days", 168: "1 week", 336: "2 weeks" }[SB.T];
    var SHORT = { gas: "Gasoline", batt: "Battery", combo: "Battery + gas", ngp: "Nat-gas portable", ngs: "Nat-gas standby" };
    var sbSetups = defs.map(function (d) { var on = SB.k === d.k; return { label: SHORT[d.k], bg: on ? "#13202E" : "#FFFFFF", ink: on ? "#FFFFFF" : "#13202E", pressed: on ? "true" : "false", accent: d.accent,
      pick: function () { self.setState({ sb: Object.assign({}, SB, { k: d.k }) }); } }; });
    var sbLens = [[24, "1 day"], [72, "3 days"], [168, "1 week"], [336, "2 weeks"]].map(function (x) { var on = SB.T === x[0]; return { label: x[1], bg: on ? "#13202E" : "#FFFFFF", ink: on ? "#FFFFFF" : "#13202E", pressed: on ? "true" : "false",
      pick: function () { self.setState({ sb: Object.assign({}, SB, { T: x[0] }) }); } }; });
    var sbHomes = ["home", "start", "mid"].map(function (h) { var on = SB.home === h; return { label: HOME_OPTS[h][2], bg: on ? "#13202E" : "#FFFFFF", ink: on ? "#FFFFFF" : "#13202E", pressed: on ? "true" : "false",
      pick: function () { self.setState({ sb: Object.assign({}, SB, { home: h }) }); } }; });
    var where = SB.home === "home" ? "at home" : (SB.home === "start" ? "away the whole time" : "away days 2 to 4");
    var verdict;
    if (R.d.k === "ngs") verdict = lenLabel + ", " + where + ": the standby is never dark longer than " + (R.longest <= 0.6 ? "30 minutes" : hrs(R.longest)) + ". " + (R.n ? "Its only chore is a short oil check about once a day (" + R.n + " in all)." : (SB.T <= 24 ? "Nothing to do in the first day." : "")) + (SB.home !== "home" && SB.T > 24 ? " While nobody is home, nobody checks the oil; Generac advises a check every 24 hours in long outages." : "");
    else if (R.n === 0) verdict = lenLabel + ", " + where + ": nobody can set it up, so the house is dark the whole time" + (R.spoil ? " and the fridge food is lost" + (R.longest >= 48 ? ", freezer too." : ".") : ".");
    else if (R.d.k !== "ngs" && false) verdict = "";
    else verdict = lenLabel + ", " + where + ": " + R.n + (R.n === 1 ? " trip" : " trips") + " outside" + (R.hard ? ", " + R.hard + " of them at night" : "") + (R.runs ? ", and " + R.runs + " drive" + (R.runs > 1 ? "s" : "") + " for gas by the wife" : "") + (R.darkH < 0.05 ? ". Never dark" : ". Dark for " + hrs(R.darkH) + " in all") + (R.spoil ? ", and the fridge food is lost." : "; the fridge stays cold.");
    var tiles = [
      ["Trips outside", String(R.n), "Husband " + R.husband + " \u00B7 Wife " + R.wife],
      ["At night (8 PM to 7 AM)", String(R.hard), R.n ? Math.round(R.hard / R.n * 100) + "% of trips" : "none"],
      ["Gas runs (wife drives; this storm, illustrative)", String(R.runs), R.gal ? R.gal + " gal burned" : "no gasoline"],
      ["Hours dark", hrs(R.darkH), "of " + lenLabel],
      ["Fridge food", R.spoil ? "Lost" : "Safe", R.spoil ? (R.longest >= 48 ? "freezer too: dark " + hrs(R.longest) + " (USDA: 4 h fridge, 48 h full freezer)" : "dark " + hrs(R.longest) + " at once; USDA says 4 h") : "never dark 4 h at once"],
      ["Longest without heat", hrs(R.longest), R.longest >= 4 ? "gas furnace needs power to run" : "furnace keeps running"],
      ["Installed", "~$" + R.d.installed.toLocaleString("en-US"), { ngp: "range $3,500 to $7,300", ngs: "Seattle-area range $8,500 to $13,000" }[R.d.k] || "electrician hookup included"]
    ].map(function (x) { return { label: x[0], value: String(x[1]).replace("under 1 h", "<1 h"), sub: x[2] }; });
    var dTrips = R.n - RS.n;
    var PRICE = { gas: [6770, 6770], batt: [4340, 4340], combo: [10190, 10190], ngp: [3470, 7350], ngs: [8500, 13000] };
    function r100(v) { return "$" + (Math.round(v / 100) * 100).toLocaleString("en-US"); }
    function gapText(k) { var lo = PRICE.ngs[0] - PRICE[k][1], hi = PRICE.ngs[1] - PRICE[k][0]; if (hi <= 0) return "about " + r100(-hi) + " to " + r100(-lo) + " less"; if (lo >= 0) return "about " + r100(lo) + " to " + r100(hi) + " more"; return "from " + r100(-lo) + " less to " + r100(hi) + " more"; }
    var vsStandby = R.d.k === "ngs" ? "This is the standby." : "Compared with the standby: " + (dTrips >= 0 ? dTrips + " more trips outside" : -dTrips + " fewer trips") + ", " + (R.darkH - RS.darkH < 1 ? "about the same time in the dark" : hrs(R.darkH - RS.darkH) + " more in the dark") + ". The standby costs " + gapText(R.d.k) + " to install.";
    // Timeline strip: amber = power, navy = dark, orange ticks = someone had to go out.
    var stops = [], pos = 0, PCT = function (h) { return (h / SB.T * 100).toFixed(2) + "%"; };
    R.dark.forEach(function (x) { if (x[0] > pos) stops.push("#FFC94D " + PCT(pos) + " " + PCT(x[0])); stops.push("#1C2A3C " + PCT(x[0]) + " " + PCT(x[1])); pos = x[1]; });
    if (pos < SB.T) stops.push("#FFC94D " + PCT(pos) + " 100%");
    var ticks = R.chores.map(function (c) { var p = c.a / SB.T * 100; return "transparent " + Math.max(0, p - 0.25).toFixed(2) + "%, #FF8A3D " + Math.max(0, p - 0.25).toFixed(2) + "% " + (p + 0.25).toFixed(2) + "%, transparent " + (p + 0.25).toFixed(2) + "%"; });
    var timeline = (ticks.length ? "linear-gradient(to right, " + ticks.join(", ") + "), " : "") + "linear-gradient(to right, " + stops.join(", ") + ")";
    // "What matters": computed toggles come from the engine for the current length and who's-home choice;
    // "Nothing to move" and "Quietest" are labeled judgments (quiet is ranked by published dB at light load).
    self._sbCache = self._sbCache || {};
    function cachedRun(k, T, home) { var key = k + "|" + T + "|" + home; if (!self._sbCache[key]) self._sbCache[key] = runSandbox(k, T, home); return self._sbCache[key]; }
    var cur = {}, awayRuns = {}, maxTrips = 1;
    defs.forEach(function (d) { cur[d.k] = cachedRun(d.k, SB.T, SB.home); awayRuns[d.k] = cachedRun(d.k, SB.T, "start"); maxTrips = Math.max(maxTrips, cur[d.k].n); });
    var JUDGE = { move: { gas: 0, batt: 1, combo: 0, ngp: 0, ngs: 1 }, quiet: { batt: 1, gas: 0.75, combo: 0.875, ngs: 0.5, ngp: 0.25 } };
    var score = {
      lights: function (k) { return 1 - Math.min(1, cur[k].darkH / SB.T); },
      fewest: function (k) { return 1 - cur[k].n / maxTrips; },
      refuel: function (k) { return cur[k].chores.some(function (c) { return c.kind === "refill" || c.kind === "trip"; }) ? 0 : 1; },
      away: function (k) { return 1 - Math.min(1, awayRuns[k].darkH / SB.T); },
      move: function (k) { return JUDGE.move[k]; },
      quiet: function (k) { return JUDGE.quiet[k]; }
    };
    var chipDefs = [["lights", "Lights stay on"], ["fewest", "Fewest trips outside"], ["refuel", "No refueling"], ["away", "Works while they're away"], ["move", "Nothing to move"], ["quiet", "Quietest"]];
    var chips = chipDefs.map(function (c) {
      var on = !!prio[c[0]];
      return { label: c[1], pressed: on ? "true" : "false", bg: on ? "#13202E" : "#FFFFFF", ink: on ? "#FFFFFF" : "#13202E",
        pick: function () { var n = Object.assign({}, prio); if (n[c[0]]) delete n[c[0]]; else n[c[0]] = true; self.setState({ prio: n }); } };
    });
    var picked = chipDefs.filter(function (c) { return prio[c[0]]; }).map(function (c) { return c[0]; });
    var byTrips = defs.slice().sort(function (a, b2) { return (cur[a.k].darkH - cur[b2.k].darkH) || (cur[a.k].n - cur[b2.k].n); });
    var fit = "Tap what matters to you to rank the five options.", fitWhy = "For " + lenLabel + ", " + where + ", by time dark, then trips outside: " + byTrips.map(function (d) { return d.name + " (" + (cur[d.k].darkH < 0.05 ? "never dark" : hrs(cur[d.k].darkH) + " dark") + ", " + cur[d.k].n + " trips)"; }).join("  \u00B7  ") + ". Scores use this simulation; \"Nothing to move\" and \"Quietest\" are judgment calls from the specs.";
    if (picked.length) {
      var ranked = defs.map(function (d) {
        var s2 = 0;
        picked.forEach(function (p) { s2 += score[p](d.k); });
        return { d: d, s: s2 / picked.length };
      }).sort(function (a, b2) { return b2.s - a.s; });
      var top = ranked[0], close = ranked[1] && top.s - ranked[1].s < 0.05;
      fit = close ? "Close call: " + top.d.name + " and " + ranked[1].d.name : "Best fit: " + top.d.name;
      fitWhy = ranked.map(function (x) { return x.d.name + " " + Math.round(x.s * 100) + "%"; }).join("  \u00B7  ") + ". Each pick counts equally; scores use this simulation for " + lenLabel + ", " + where + ".";
      opts.forEach(function (o) { if (o.name === top.d.name) o.outline = "3px solid " + top.d.accent; });
    }

    return {
      t: t, clock: clock, outFor: t === 0 ? "The power just went out" : "Power out " + dur(t),
      weather: weather, windCls: windCls, rainCls: rainCls, rainOp: rainOp, groundFill: groundFill, boltX: boltX, rainSkew: rainSkew, debrisCls: debrisCls, fogOp: fogOp, rainbowOp: rainbowOp, orbOp: orbOp, orbBg: orbBg, orbGlow: orbGlow, puddleOp: puddleOp, snowCover: snowCover, snowH: snowH.toFixed(1), snowY: (56 - snowH).toFixed(1), iceOp: iceOp, cloudCls: cloudCls, cloudOp: cloudOp, flashCls: flashCls, flashCls2: flashCls2,
      capDisp: (pending.length || holdInfo) ? "none" : "block", promptDisp: (pending.length || holdInfo) ? "flex" : "none",
      promptBg: pending.length ? "#FF8A3D" : "rgba(243,245,248,0.12)",
      promptInk: pending.length ? "#13202E" : "#F3F5F8",
      promptEdge: pending.length ? "#13202E" : "#F3F5F8",
      promptBtnDisp: pending.length ? "inline-block" : "none",
      promptBtnBg: "#13202E",
      promptBtnInk: pending.length ? "#FFFFFF" : "#13202E",
      promptAct: pending.length ? function () { self.renderVals().goOut(); } : function () { self.renderVals().togglePlay(); },
      prompt: !pending.length ? holdInfo : (waitHomes.length === 1 ? pending[0].home + " \u00B7 " + pending[0].who + ": " + pending[0].ask : waitHomes.length + " homes need someone outside. Each stays dark until you tap; the storm keeps going."),
      promptBtn: !pending.length ? "\u275A\u275A Pause" : (waitHomes.length === 1 ? pending[0].btn : "Do all " + waitHomes.length),
      pending: pending,
      goOut: function () { self.setState(tapNow()); },
      outCount: clicks ? "You've gone out " + clicks + (clicks === 1 ? " time" : " times") + ". Standby: 0" : "",
      sky: sky, skyInk: skyInk, skyLine: skyLine, caption: caption, presets: presets,
      awayLabel: away ? "On vacation \u2014 tap to come home" : (st.homeAt != null ? "Back home \u2014 Reset to try another trip" : "Go on vacation"),
      awayCls: away ? "" : "pulse",
      autoLabel: auto ? "Chores: auto" : "Chores: I tap",
      autoAria: auto ? "Chores happen on their own. Tap to do them yourself." : "Chores wait for your tap while the storm keeps going; a home stays dark until you tap. Tap to let them happen on their own.", autoPressed: auto ? "true" : "false",
      autoBg: "transparent", autoInk: skyInk,
      reset: function () {
        stopPlay();
        self.setState(Object.assign({ t: 0, away: false, leaveAt: null, homeAt: null, clicks: 0, playing: false, fly: null }, freshTaps()));
        setTimeout(function () { var fresh = self.renderVals(); if (!self._timer) fresh.togglePlay(); }, 60);
      },
      toggleAuto: function () {
        var toAuto = self.state.auto === false, now = self.state.t || 0;
        var wins = (self.state.tapWin || []).map(function (w) { return w.slice(); });
        if (toAuto) {  // anything waiting gets done now; chores from here on happen on their own
          wins.forEach(function (w) { if (w[1] > now) w[1] = now + 0.001; });
          self.setState(tapNow({ auto: true, tapWin: wins }));
        } else { wins.push([now, 1e9]); self.setState({ auto: false, tapWin: wins }); }
      }, awayPressed: away ? "true" : "false",
      awayBg: away ? "transparent" : "#FFFFFF", awayInk: away ? skyInk : "#13202E",
      toggleAway: function () {
        var now = self.state.t || 0;
        if (!away && self.state.homeAt != null) return;  // one vacation per playthrough; Reset to try again
        if (!away) {
          self.setState(tapNow({ away: true, leaveAt: now, homeAt: null }));  // waiting chores get done on the way out
          if (now > 0) setTimeout(function () { try { self.renderVals(); } catch (err) {} waitAt(now); }, 60);
          return;
        }
        self.setState({ away: false, homeAt: now > (self.state.leaveAt || 0) ? now : null, leaveAt: now > (self.state.leaveAt || 0) ? (self.state.leaveAt || 0) : null });
        setTimeout(function () { try { self.renderVals(); } catch (err) {} waitAt(now); }, 60);
      },
      setT: function (e) { stopPlay(); jumpTo(Number(e.target.value), { playing: false }); },
      togglePlay: function () { if (playing) { stopPlay(); self.setState({ playing: false }); } else { if (t >= 336) self.setState({ clicks: 0 }); startPlay(); } },
      playLabel: playing ? "\u275A\u275A Pause" : "\u25B6 Play", playPressed: playing ? "true" : "false",
      playAria: playing ? "Pause the outage" : "Play the outage from now to two weeks",
      playBg: "#F3F5F8", playInk: "#13202E",
      opts: opts, chips: chips, fit: fit, fitWhy: fitWhy,
      stageCls: playing ? "" : "paused",
      headline: R.d.k === "ngs" ? "Never dark more than 30 min" : (R.n === 0 ? "Dark the whole time" : R.n + (R.n === 1 ? " trip" : " trips") + " outside"),
      sbSetups: sbSetups, sbLens: sbLens, sbHomes: sbHomes, verdict: verdict, tiles: tiles, vsStandby: vsStandby, timeline: timeline, tlEnd: lenLabel,
      nudgeVacation: function () { try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch (err) {} var v = self.renderVals(); stopPlay(); jumpTo(42, { playing: false }); setTimeout(function () { var v2 = self.renderVals(); if (!self.state.away) v2.toggleAway(); setTimeout(function () { var v3 = self.renderVals(); if (!self._timer) v3.togglePlay(); }, 120); }, 60); },
      nudgeChores: function () { try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch (err) {} stopPlay(); self.setState({ auto: false }); setTimeout(function () { self.renderVals().reset(); }, 30); },
      nudgeWeek: function () { try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch (err) {} stopPlay(); jumpTo(168, { playing: false }); },
      delta: gapText("gas").replace(" more", "")
    };
  }
}
document.addEventListener('DOMContentLoaded', function () { window.__mount(Component); });
