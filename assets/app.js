/* Daily Byte renderer — vanilla JS, no deps.
 *
 * Reads /data/latest.json (or a ?date=YYYY-MM-DD override), then renders
 * every section + story into the DOM. Designed so that the static
 * index.html shell never needs to be edited again — all copy comes from
 * the JSON digest.
 *
 * The same module also handles /archive/index.html (reads archive.json).
 *
 * v2: appendChild-safe footer rewrite.
 */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };
  var ce = function (tag, attrs, kids) {
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") el.className = attrs[k];
      else if (k === "text") el.textContent = attrs[k];
      else if (k === "html") el.innerHTML = attrs[k];
      else el.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (k) { if (k) el.appendChild(k); });
    return el;
  };

  function qs(name) {
    var m = window.location.search.match(new RegExp("[?&]" + name + "=([^&]*)"));
    return m ? decodeURIComponent(m[1]) : null;
  }

  /* -------- date formatting -------- */
  var MONTHS = ["January","February","March","April","May","June",
                "July","August","September","October","November","December"];
  var WDAYS  = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
  function fmtLabel(iso) {
    var d = new Date(iso + "T00:00:00Z");
    return WDAYS[d.getUTCDay()] + ", " + MONTHS[d.getUTCMonth()] +
           " " + d.getUTCDate() + ", " + d.getUTCFullYear();
  }

  /* -------- main render -------- */
  function renderDigest(d) {
    document.title = (d.site && d.site.name ? d.site.name : "Daily Byte") +
                     " — " + d.date;
    var meta = $('meta[name="description"]');
    if (meta) meta.setAttribute("content",
      (d.site && d.site.tagline ? d.site.tagline : "") +
      " · " + d.date + " · " + d.total + " stories");

    /* masthead */
    var name = (d.site && d.site.name) || "Daily Byte";
    var tagline = (d.site && d.site.tagline) || "";
    var baseUrl = (d.site && d.site.base_url) || "";
    var repo = (d.site && d.site.repo) || "#";

    var h1 = $(".mast h1");
    if (h1) h1.innerHTML = name.replace(/\./g, '<span class="dot">.</span>');
    var tag = $(".mast .tag");
    if (tag) tag.textContent = tagline;
    var ed = $(".mast .edition");
    if (ed) ed.textContent = d.label + " · Edition " + d.date;

    /* chrome nav */
    var navWrap = $(".chrome .nav");
    if (navWrap) {
      navWrap.innerHTML = "";
      if (d.prev_day) {
        var prevA = ce("a", { class: "nav-arrow", href: baseUrl + "/archive/" + d.prev_day + ".html",
                              title: d.prev_day, "aria-label": "Previous edition" }, []);
        prevA.textContent = "‹";
        navWrap.appendChild(prevA);
      } else {
        var prevSp = ce("span", { class: "nav-arrow disabled", "aria-label": "No previous edition" }, []);
        prevSp.textContent = "‹";
        navWrap.appendChild(prevSp);
      }
      navWrap.appendChild(ce("span", { class: "d" }, []));
      navWrap.lastChild.textContent = d.label.split(",")[0] + " " + d.date;
      if (d.next_day) {
        var nextA = ce("a", { class: "nav-arrow", href: baseUrl + "/archive/" + d.next_day + ".html",
                              title: d.next_day, "aria-label": "Next edition" }, []);
        nextA.textContent = "›";
        navWrap.appendChild(nextA);
      } else {
        var nextSp = ce("span", { class: "nav-arrow disabled", "aria-label": "No newer edition" }, []);
        nextSp.textContent = "›";
        navWrap.appendChild(nextSp);
      }
    }

    /* count */
    var cnt = $(".chrome .count");
    if (cnt) cnt.textContent = d.total + " STORIES";

    /* sections */
    var main = $("main");
    if (!main) return;
    main.innerHTML = "";
    (d.sections || []).forEach(function (sec) {
      if (!sec.stories || !sec.stories.length) return;
      var s = ce("section", { class: "sec", id: "sec-" + sec.name }, []);
      var np = ce("div", { class: "nameplate" }, []);
      var lbl = ce("span", {}, []);
      lbl.textContent = sec.label || sec.name;
      np.appendChild(lbl);
      s.appendChild(np);
      if (sec.intro) {
        var lead = ce("p", { class: "lead" }, []);
        lead.textContent = sec.intro;
        s.appendChild(lead);
      }
      sec.stories.forEach(function (story) { s.appendChild(renderStory(story)); });
      main.appendChild(s);
    });

    /* footer — only rewrite if we got a real digest, not the loading shell */
    var foot = $(".foot");
    if (foot) {
      foot.innerHTML = "";
      foot.appendChild(document.createTextNode("Curated daily by your "));
      var strong = ce("strong", {}, []);
      strong.textContent = "Hermes";
      foot.appendChild(strong);
      foot.appendChild(document.createTextNode(" agent · "));
      var repoA = ce("a", { href: repo, target: "_blank", rel: "noopener" }, []);
      repoA.textContent = "open source";
      foot.appendChild(repoA);
      foot.appendChild(document.createTextNode(" · served from " + baseUrl));
      var rssDiv = ce("div", { style: "margin-top:8px" }, []);
      var rssA = ce("a", { href: baseUrl + "/rss.xml" }, []);
      rssA.textContent = "Subscribe via RSS";
      rssDiv.appendChild(rssA);
      foot.appendChild(rssDiv);
    }
  }

  function renderStory(s) {
    var a = ce("article", { class: "story", id: s.id || "" }, []);
    var meta = ce("div", { class: "meta" }, []);
    var chip = ce("span", { class: "src", "data-kind": s.source_kind || "blog" }, []);
    chip.textContent = (s.source_kind || "story").toUpperCase();
    meta.appendChild(chip);
    if (s.source) {
      var by = ce("span", { class: "by" }, []);
      by.textContent = s.source;
      meta.appendChild(by);
    }
    a.appendChild(meta);

    var body = ce("div", {}, []);
    var h2 = ce("h2", {}, []);
    var link = ce("a", { href: s.url, target: "_blank", rel: "noopener" }, []);
    link.textContent = s.title || "(untitled)";
    h2.appendChild(link);
    body.appendChild(h2);

    var sum = ce("div", { class: "sum" }, []);
    var p = ce("p", {}, []);
    p.textContent = (s.summary && s.summary.trim()) || (s.snippet && s.snippet.trim()) || "";
    sum.appendChild(p);
    body.appendChild(sum);

    if (s.published_at) {
      var sm = ce("div", { class: "smeta" }, []);
      try {
        var dt = new Date(s.published_at);
        sm.textContent = "Published " + dt.toUTCString().replace(/^[A-Za-z]+, /, "");
      } catch (e) { /* ignore */ }
      body.appendChild(sm);
    }
    a.appendChild(body);
    return a;
  }

  function showError(msg) {
    var main = $("main");
    if (!main) {
      document.body.appendChild(ce("div", { class: "error" }, [])).textContent = msg;
      return;
    }
    main.innerHTML = "";
    main.appendChild(ce("div", { class: "error" }, [])).textContent = msg;
  }

  function showLoading() {
    var main = $("main");
    if (!main) return;
    main.innerHTML = "";
    main.appendChild(ce("div", { class: "loading" }, [])).textContent = "Loading today's edition…";
  }

  /* -------- bootstrap --------
   *
   * Path semantics:
   *   index.html (/)         → fetches data/latest.json
   *   archive/index.html    → fetches archive.json (no digest render)
   *   archive/<DATE>.html   → reads inlined #digest-data (no fetch)
   *
   * The archive pages set data-page="archive" on <body> so we can branch.
   */
  var pageKind = document.body.getAttribute("data-page") || "today";

  function bootToday() {
    var date = qs("date");
    var url = date ? ("data/" + date + ".json") : "data/latest.json";
    showLoading();
    fetch(url, { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status + " fetching " + url);
        return r.json();
      })
      .then(renderDigest)
      .catch(function (e) { showError("Could not load today's edition: " + e.message); });
  }

  function boot() {
    /* Archive list pages: the inline <script> in archive/index.html
       handles its own render. Just stop here. */
    if (pageKind === "archive") return;

    /* If the page inlined a digest (per-day snapshot), use it. */
    var inlined = $("#digest-data");
    if (inlined) {
      try {
        renderDigest(JSON.parse(inlined.textContent));
        return;
      } catch (e) {
        showError("Inlined digest is malformed: " + e.message);
        return;
      }
    }
    bootToday();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();