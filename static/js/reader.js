/* ─────────────────────────────────────────────────────────────────────────
   Lextor reader. Renders a lextor/1 document in three modes:
     read      flowing original text; hover/tap a fragment → card with gloss + note
     parallel  original | translation aligned per block (wide) or interlinear (narrow)
     study     one card per sentence with its glossary
   Used by the web app, share/embed pages and the MCP widget (no dependencies).
   ───────────────────────────────────────────────────────────────────────── */
(function (global) {
  "use strict";

  const LANGS = {
    es: ["Español", 0], en: ["English", 0], fr: ["Français", 0], de: ["Deutsch", 0], it: ["Italiano", 0],
    pt: ["Português", 0], ca: ["Català", 0], gl: ["Galego", 0], eu: ["Euskara", 0], nl: ["Nederlands", 0],
    sv: ["Svenska", 0], da: ["Dansk", 0], no: ["Norsk", 0], fi: ["Suomi", 0], pl: ["Polski", 0], cs: ["Čeština", 0],
    ro: ["Română", 0], hu: ["Magyar", 0], tr: ["Türkçe", 0], vi: ["Tiếng Việt", 0], id: ["Bahasa Indonesia", 0],
    la: ["Latina", 0], ru: ["Русский", 0], uk: ["Українська", 0], bg: ["Български", 0], el: ["Ελληνικά", 0],
    ar: ["العربية", 2], fa: ["فارسی", 2], he: ["עברית", 2], hi: ["हिन्दी", 0], zh: ["中文", 1], ja: ["日本語", 1],
    ko: ["한국어", 0], th: ["ไทย", 1],
  };
  const UNSPACED = (l) => (LANGS[l] || [])[1] === 1;
  const RTL = (l) => (LANGS[l] || [])[1] === 2;
  const LANG_NAME = (l) => (LANGS[l] || [l || "?"])[0];
  const FONT_FAMILIES = {
    ja: "Noto+Serif+JP:wght@400;600", zh: "Noto+Serif+SC:wght@400;600", ko: "Noto+Serif+KR:wght@400;600",
    ar: "Noto+Naskh+Arabic:wght@400;600", fa: "Noto+Naskh+Arabic:wght@400;600", he: "Noto+Serif+Hebrew:wght@400;600",
    hi: "Noto+Serif+Devanagari:wght@400;600", th: "Noto+Serif+Thai:wght@400;600",
  };

  const DEFAULTS = { scale: 1, leading: 1.8, width: "normal", font: "serif", ruby: null, hints: true, paper: false };
  const STR = {
    read: "Lectura", parallel: "Paralelo", study: "Estudio",
    sentence: "Frase", literal: "lit.", listen: "Escuchar", close: "Cerrar", save: "Guardar",
    note: "Nota", addNote: "Añadir nota", yourNote: "Tu nota", copy: "Copiar",
    hintPin: "Clic para fijar · ←/→ para moverte · Esc para cerrar",
  };

  // ── utils ────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function svg(path) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
    s.innerHTML = path; // static, trusted icon paths only
    return s;
  }
  const ICON_SPEAK = '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>';
  const ICON_X = '<path d="M18 6 6 18M6 6l12 12"/>';

  function store(key, val) {
    try {
      if (val === undefined) return JSON.parse(localStorage.getItem(key) || "null");
      localStorage.setItem(key, JSON.stringify(val));
    } catch (e) { return null; }
  }

  function segment(sentence) {
    const parts = [];
    const text = sentence.text || "";
    let pos = 0;
    (sentence.chunks || []).forEach((c, ci) => {
      const i = text.indexOf(c.text, pos);
      if (i === -1) { parts.push({ chunk: c, ci }); return; }
      if (i > pos) parts.push({ gap: text.slice(pos, i) });
      parts.push({ chunk: c, ci });
      pos = i + c.text.length;
    });
    if (pos < text.length) parts.push({ gap: text.slice(pos) });
    return parts;
  }

  // Align a kana reading over the kanji runs only (降っています/ふっています → 降[ふ]っています).
  const KANJI = /[\p{Script=Han}々〆ヵヶ]/u;
  const PUNCT = /[\p{P}\p{S}\s]/gu;
  const toHira = (x) => x.replace(/[\u30a1-\u30f6]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60));
  const reEsc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  function furigana(text, reading) {
    const runs = [];
    let cur = "", isK = null;
    for (const ch of text) {
      const k = KANJI.test(ch);
      if (isK === null || k === isK) cur += ch; else { runs.push([cur, isK]); cur = ch; }
      isK = k;
    }
    if (cur) runs.push([cur, isK]);
    if (!runs.some((r) => r[1])) return null;              // kana only: no ruby needed
    const pattern = "^" + runs.map(([t, k]) => (k ? "(.+?)" : reEsc(toHira(t).replace(PUNCT, "")))).join("") + "$";
    let m = null;
    try { m = new RegExp(pattern, "u").exec(toHira(reading).replace(PUNCT, "")); } catch (e) { m = null; }
    if (!m) return [[text, reading]];
    let gi = 1;
    return runs.map(([t, k]) => (k ? [t, m[gi++]] : [t, null]));
  }

  let fontsRequested = {};
  function ensureFonts(lang) {
    const fam = FONT_FAMILIES[lang];
    const base = "Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;1,7..72,400&family=Inter:wght@400;500;600;700&family=Atkinson+Hyperlegible:wght@400;700";
    const key = fam || "base";
    if (fontsRequested[key]) return;
    fontsRequested[key] = true;
    try {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "https://fonts.googleapis.com/css2?family=" + (fam ? fam + "&family=" : "") + base + "&display=swap";
      document.head.appendChild(link);
    } catch (e) { /* fonts are optional */ }
  }

  // ── speech ───────────────────────────────────────────────────────────
  const Speech = {
    ok: typeof window !== "undefined" && "speechSynthesis" in window,
    speak(text, lang) {
      if (!this.ok || !text) return;
      try {
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = lang;
        const voices = window.speechSynthesis.getVoices() || [];
        const v = voices.find((x) => x.lang && x.lang.toLowerCase().startsWith(lang));
        if (v) u.voice = v;
        u.rate = 0.92;
        window.speechSynthesis.speak(u);
      } catch (e) { /* ignore */ }
    },
  };

  // ── Reader ───────────────────────────────────────────────────────────
  class Reader {
    constructor(root, doc, opts) {
      this.root = root;
      this.opts = Object.assign({
        mode: "read", editable: false, onAnnotate: null, storageKey: "lextor.reader", showHeader: true,
        popoverContainer: document.body, onModeChange: null, narrowAt: 760,
      }, opts || {});
      this.doc = doc;
      this.ann = Object.assign({}, doc.annotations || {});
      this.lang = (doc.meta && doc.meta.text_lang) || "en";
      this.notesLang = (doc.meta && doc.meta.notes_lang) || "es";
      const saved = store(this.opts.storageKey) || {};
      this.settings = Object.assign({}, DEFAULTS, saved);
      if (this.settings.ruby === null) this.settings.ruby = ["ja", "zh"].includes(this.lang);
      this.mode = this.opts.mode || saved.mode || "read";
      this.index = new Map();     // chunk id → {chunk, sentence, bi, si, ci}
      this.order = [];            // chunk ids in reading order
      this.pinned = null;
      this.peekTimer = null;
      this._build();
      this._bind();
    }

    // public API ──────────────────────────────────────────────────────
    setMode(mode) {
      if (!["read", "parallel", "study"].includes(mode) || mode === this.mode) return;
      this.mode = mode;
      this._save();
      const run = () => this.render();
      if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
        document.startViewTransition(run);
      } else run();
      if (this.opts.onModeChange) this.opts.onModeChange(mode);
    }
    setSettings(patch) {
      Object.assign(this.settings, patch);
      this._save();
      this._applySettings();
      if ("ruby" in patch) this.render();
    }
    resetSettings() {
      this.settings = Object.assign({}, DEFAULTS, { ruby: ["ja", "zh"].includes(this.lang) });
      this._save();
      this._applySettings();
      this.render();
    }
    destroy() {
      this.closePop();
      if (this.pop) this.pop.remove();
      if (this._ro) this._ro.disconnect();
      document.removeEventListener("keydown", this._onKey);
      document.removeEventListener("pointerdown", this._onDocDown, true);
      this.root.innerHTML = "";
    }

    // build ───────────────────────────────────────────────────────────
    _build() {
      ensureFonts(this.lang);
      this.root.classList.add("lx-reader");
      this.root.setAttribute("lang", this.lang);
      this.page = el("div", "lx-page");
      this.root.innerHTML = "";
      this.root.appendChild(this.page);
      this.pop = el("div", "lx-pop");
      this.pop.setAttribute("role", "dialog");
      this.pop.setAttribute("aria-live", "polite");
      this.opts.popoverContainer.appendChild(this.pop);
      this.doc.blocks.forEach((b, bi) => b.sentences.forEach((s, si) => s.chunks.forEach((c, ci) => {
        const id = `b${bi}.s${si}.c${ci}`;
        this.index.set(id, { chunk: c, sentence: s, block: b, bi, si, ci, id });
        this.order.push(id);
      })));
      this._applySettings();
      this.narrow = this.root.clientWidth < this.opts.narrowAt;
      this.render();
      if (typeof ResizeObserver !== "undefined") {
        this._ro = new ResizeObserver(() => {
          const n = this.root.clientWidth < this.opts.narrowAt;
          if (n !== this.narrow) {
            this.narrow = n;
            if (this.mode === "parallel") this.render();
          }
        });
        this._ro.observe(this.root);
      }
    }

    _applySettings() {
      const s = this.settings, r = this.root;
      r.style.setProperty("--lx-scale", s.scale);
      r.style.setProperty("--lx-leading", s.leading);
      r.classList.toggle("lx-font-sans", s.font === "sans");
      r.classList.toggle("lx-font-legible", s.font === "legible");
      r.classList.toggle("lx-width-narrow", s.width === "narrow");
      r.classList.toggle("lx-width-wide", s.width === "wide");
      r.classList.toggle("lx-hints", !!s.hints);
      r.classList.toggle("lx-ruby", !!s.ruby);
      r.classList.toggle("lx-paper", !!s.paper);
    }

    _save() { store(this.opts.storageKey, Object.assign({}, this.settings, { mode: this.mode })); }

    _dupTitle() {
      const b = this.doc.blocks[0], t = (this.doc.meta || {}).title || "";
      const norm = (x) => x.replace(/[\s\p{P}]/gu, "").toLowerCase();
      return !!(b && b.type === "heading" && b.sentences.length === 1 && norm(b.sentences[0].text) === norm(t));
    }

    render() {
      this.closePop();
      this.root.dataset.mode = this.mode;
      this.page.innerHTML = "";
      this.dup = this.opts.showHeader && this._dupTitle();
      this.root.classList.toggle("lx-dup-title", this.dup);
      if (this.opts.showHeader) this.page.appendChild(this._header());
      const body = el("div", "lx-body");
      if (this.mode === "read") this._renderRead(body);
      else if (this.mode === "parallel") (this.narrow ? this._renderInterlinear(body) : this._renderParallel(body));
      else this._renderStudy(body);
      this.page.appendChild(body);
    }

    _header() {
      const m = this.doc.meta || {};
      const h = el("header", "lx-head");
      const k = el("div", "lx-kicker");
      k.appendChild(el("span", "lx-chip lx-chip-accent", `${LANG_NAME(m.text_lang)} → ${LANG_NAME(m.notes_lang)}`));
      if (m.level) k.appendChild(el("span", "lx-chip", m.level));
      const words = this._wordCount();
      k.appendChild(el("span", "lx-chip", `${Math.max(1, Math.round(words / (UNSPACED(this.lang) ? 300 : 150)))} min`));
      h.appendChild(k);
      if (!this.dup) {
        const t = el("h1", "lx-title", m.title || "");
        if (RTL(this.lang)) t.dir = "rtl";
        h.appendChild(t);
        if (m.title_translation) h.appendChild(el("p", "lx-title-tr", m.title_translation));
        if (m.summary) h.appendChild(el("p", "lx-summary", m.summary));
      } else if (this.mode === "study" && m.summary) {
        h.appendChild(el("p", "lx-summary", m.summary));
      }
      return h;
    }

    _afterTitle(body) {
      // When the first heading *is* the title, show translation + summary right below it.
      const m = this.doc.meta || {};
      const wrap = el("div", "lx-head-after");
      if (m.title_translation && this.mode === "read") wrap.appendChild(el("p", "lx-title-tr", m.title_translation));
      if (m.summary) wrap.appendChild(el("p", "lx-summary", m.summary));
      if (wrap.childNodes.length) body.appendChild(wrap);
    }

    _wordCount() {
      let n = 0;
      this.doc.blocks.forEach((b) => b.sentences.forEach((s) => {
        n += UNSPACED(this.lang) ? s.text.replace(/\s|\p{P}/gu, "").length : (s.text.match(/[\p{L}\p{N}]+/gu) || []).length;
      }));
      return n;
    }

    _sentenceSpan(s, bi, si) {
      const span = el("span", "lx-s");
      span.dataset.s = `b${bi}.s${si}`;
      segment(s).forEach((p) => {
        if (p.gap != null) {
          if (/^\s+$/.test(p.gap)) span.appendChild(document.createTextNode(" "));
          else span.appendChild(el("span", "lx-gap", p.gap));
          return;
        }
        span.appendChild(this._chunkEl(p.chunk, `b${bi}.s${si}.c${p.ci}`));
      });
      return span;
    }

    _chunkEl(c, id) {
      const e = el("span", "lx-c");
      e.dataset.id = id;
      e.tabIndex = 0;
      e.setAttribute("role", "button");
      e.setAttribute("aria-label", `${c.text}: ${c.gloss || ""}`);
      const parts = this.settings.ruby
        ? (c.reading ? furigana(c.text, c.reading) : (c.translit ? [[c.text, c.translit]] : null))
        : null;
      if (parts) {
        parts.forEach(([base, rt]) => {
          if (!rt) { e.appendChild(document.createTextNode(base)); return; }
          const r = el("ruby");
          r.appendChild(document.createTextNode(base));
          r.appendChild(el("rt", null, rt));
          e.appendChild(r);
        });
      } else {
        e.textContent = c.text;
      }
      this._decorate(e, id);
      return e;
    }

    _decorate(e, id) {
      const a = this.ann[id];
      if (a && a.color) e.dataset.ann = a.color; else delete e.dataset.ann;
      e.classList.toggle("has-comment", !!(a && a.comment));
    }

    _joiner() { return UNSPACED(this.lang) ? "" : " "; }

    _blockEl(b, bi, cls) {
      let wrap;
      if (b.type === "heading") {
        const lvl = Math.min(3, Math.max(1, b.level || 1));
        wrap = el(lvl === 1 ? "h2" : lvl === 2 ? "h3" : "h4", `lx-h lx-h${lvl}`);
      } else if (b.type === "verse") wrap = el("div", "lx-verse");
      else wrap = el("p", "lx-p");
      if (cls) wrap.classList.add(cls);
      if (RTL(this.lang)) wrap.dir = "rtl";
      b.sentences.forEach((s, si) => {
        const span = this._sentenceSpan(s, bi, si);
        if (b.type === "verse") {
          const line = el("span", "lx-line");
          line.appendChild(span);
          wrap.appendChild(line);
        } else {
          if (si > 0) wrap.appendChild(document.createTextNode(this._joiner()));
          wrap.appendChild(span);
        }
      });
      return wrap;
    }

    _renderRead(body) {
      this.doc.blocks.forEach((b, bi) => {
        const blk = el("div", "lx-block");
        blk.appendChild(this._blockEl(b, bi, bi === 0 && this.dup ? "lx-doc-title" : null));
        body.appendChild(blk);
        if (bi === 0 && this.dup) this._afterTitle(body);
      });
    }

    _trBlock(b, bi) {
      let wrap;
      if (b.type === "heading") {
        const lvl = Math.min(3, Math.max(1, b.level || 1));
        wrap = el("div", `lx-h lx-h${lvl} lx-tr-text`);
      } else wrap = el(b.type === "verse" ? "div" : "p", (b.type === "verse" ? "lx-verse" : "lx-p") + " lx-tr-text");
      if (RTL(this.notesLang)) wrap.dir = "rtl";
      b.sentences.forEach((s, si) => {
        const span = el("span", "lx-s", s.translation || "");
        span.dataset.s = `b${bi}.s${si}`;
        if (b.type === "verse") { const l = el("span", "lx-line"); l.appendChild(span); wrap.appendChild(l); }
        else { if (si > 0) wrap.appendChild(document.createTextNode(UNSPACED(this.notesLang) ? "" : " ")); wrap.appendChild(span); }
      });
      return wrap;
    }

    _renderParallel(body) {
      const head = el("div", "lx-par-head");
      head.appendChild(el("div", null, LANG_NAME(this.lang)));
      head.appendChild(el("div", null, LANG_NAME(this.notesLang)));
      body.appendChild(head);
      this.doc.blocks.forEach((b, bi) => {
        if (b.type === "verse") {
          // one row per line keeps verses aligned
          b.sentences.forEach((s, si) => {
            const row = el("div", "lx-row lx-row-line");
            const L = el("div"), R = el("div", "lx-tr-col lx-tr-text");
            if (RTL(this.lang)) L.dir = "rtl";
            L.appendChild(this._sentenceSpan(s, bi, si));
            const t = el("span", "lx-s", s.translation || "");
            t.dataset.s = `b${bi}.s${si}`;
            R.appendChild(t);
            row.appendChild(L); row.appendChild(R);
            body.appendChild(row);
          });
          body.appendChild(el("div", "lx-row lx-row-gap"));
          return;
        }
        const row = el("div", "lx-row");
        const L = el("div"), R = el("div", "lx-tr-col");
        const isTitle = bi === 0 && this.dup;
        L.appendChild(this._blockEl(b, bi, isTitle ? "lx-doc-title" : null));
        const tr = this._trBlock(b, bi);
        if (isTitle) tr.classList.add("lx-doc-title");
        R.appendChild(tr);
        row.appendChild(L); row.appendChild(R);
        body.appendChild(row);
        if (isTitle) this._afterTitle(body);
      });
    }

    _renderInterlinear(body) {
      this.doc.blocks.forEach((b, bi) => {
        const blk = el("div", "lx-il-block" + (b.type === "verse" ? " is-verse" : ""));
        b.sentences.forEach((s, si) => {
          let wrap;
          if (b.type === "heading") {
            const lvl = Math.min(3, Math.max(1, b.level || 1));
            wrap = el("div", `lx-h lx-h${lvl} lx-il`);
          } else wrap = el("div", "lx-il");
          const src = el("div");
          if (RTL(this.lang)) src.dir = "rtl";
          src.appendChild(this._sentenceSpan(s, bi, si));
          wrap.appendChild(src);
          const tr = el("span", "lx-il-tr", s.translation || "");
          if (RTL(this.notesLang)) tr.dir = "rtl";
          wrap.appendChild(tr);
          if (bi === 0 && this.dup) wrap.classList.add("lx-doc-title");
          blk.appendChild(wrap);
        });
        body.appendChild(blk);
        if (bi === 0 && this.dup) this._afterTitle(body);
      });
    }

    _renderStudy(body) {
      this.doc.blocks.forEach((b, bi) => b.sentences.forEach((s, si) => {
        const card = el("article", "lx-card");
        const h = el("div", "lx-card-h");
        const src = el("div", "lx-card-src");
        if (RTL(this.lang)) src.dir = "rtl";
        src.appendChild(this._sentenceSpan(s, bi, si));
        h.appendChild(src);
        if (Speech.ok) {
          const btn = el("button", "lx-icon-btn");
          btn.type = "button";
          btn.title = STR.listen;
          btn.setAttribute("aria-label", STR.listen);
          btn.appendChild(svg(ICON_SPEAK));
          btn.addEventListener("click", () => Speech.speak(s.text, this.lang));
          h.appendChild(btn);
        }
        card.appendChild(h);
        card.appendChild(el("p", "lx-card-tr", s.translation || ""));
        if (s.note) card.appendChild(el("p", "lx-card-note", s.note));
        const gl = el("div", "lx-glossary");
        s.chunks.forEach((c, ci) => {
          const id = `b${bi}.s${si}.c${ci}`;
          const row = el("div", "lx-gl");
          const a = this.ann[id];
          if (a && a.color) row.dataset.ann = a.color;
          const left = el("div", "lx-gl-src", c.text);
          left.dataset.id = id;
          if (c.translit || c.reading) left.appendChild(el("small", null, [c.reading, c.translit].filter(Boolean).join(" · ")));
          left.addEventListener("click", () => this.openPop(id, true));
          const right = el("div");
          const g = el("span", "lx-gl-gloss", c.gloss || "");
          right.appendChild(g);
          if (c.grammar) right.appendChild(el("span", "lx-gl-gram", c.grammar));
          if (c.note) right.appendChild(el("div", "lx-gl-note", c.note));
          if (a && a.comment) right.appendChild(el("div", "lx-pop-user", a.comment));
          row.appendChild(left); row.appendChild(right);
          gl.appendChild(row);
        });
        card.appendChild(gl);
        body.appendChild(card);
      }));
    }

    // interaction ─────────────────────────────────────────────────────
    _isTouch() { return matchMedia("(hover: none), (pointer: coarse)").matches; }
    _useSheet() { return this._isTouch() || window.innerWidth < 640; }

    _bind() {
      const root = this.root;
      root.addEventListener("mouseover", (e) => {
        if (this._isTouch()) return;
        const c = e.target.closest(".lx-c");
        const s = e.target.closest(".lx-s");
        this._activateSentence(s ? s.dataset.s : null);
        if (!c || this.pinned) return;
        clearTimeout(this.peekTimer);
        this.peekTimer = setTimeout(() => this.openPop(c.dataset.id, false), 70);
      });
      root.addEventListener("mouseout", (e) => {
        if (this._isTouch()) return;
        const to = e.relatedTarget;
        if (to && root.contains(to) && to.closest(".lx-c")) return;
        clearTimeout(this.peekTimer);
        if (!this.pinned) this.closePop();
        if (!to || !root.contains(to)) this._activateSentence(null);
      });
      root.addEventListener("click", (e) => {
        const c = e.target.closest(".lx-c");
        if (!c) return;
        e.preventDefault();
        if (this.pinned === c.dataset.id) { this.closePop(); return; }
        this.openPop(c.dataset.id, true);
      });
      root.addEventListener("keydown", (e) => {
        const c = e.target.closest && e.target.closest(".lx-c");
        if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); this.openPop(c.dataset.id, true); }
      });
      root.addEventListener("focusin", (e) => {
        const c = e.target.closest && e.target.closest(".lx-c");
        if (c && !this.pinned && !this._isTouch()) this.openPop(c.dataset.id, false);
      });
      this._onKey = (e) => {
        if (!this.pinned && !this.peekId) return;
        if (e.target && /^(TEXTAREA|INPUT)$/.test(e.target.tagName)) {
          if (e.key === "Escape") { this.closePop(); }
          return;
        }
        if (e.key === "Escape") { this.closePop(); return; }
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          const cur = this.pinned || this.peekId;
          const i = this.order.indexOf(cur);
          const next = this.order[i + (e.key === "ArrowRight" ? 1 : -1)];
          if (next) { e.preventDefault(); this.openPop(next, true, true); }
        }
      };
      document.addEventListener("keydown", this._onKey);
      this._onDocDown = (e) => {
        if (!this.pinned) return;
        if (this.pop.contains(e.target) || (e.target.closest && e.target.closest(".lx-c, .lx-gl-src"))) return;
        this.closePop();
      };
      document.addEventListener("pointerdown", this._onDocDown, true);
      window.addEventListener("scroll", () => { if (this.peekId && !this.pinned) this.closePop(); else this._position(); }, true);
      window.addEventListener("resize", () => this._position());
    }

    _activateSentence(sid) {
      if (sid === this._activeS) return;
      this.root.querySelectorAll(".lx-s.is-active").forEach((x) => x.classList.remove("is-active"));
      this._activeS = sid;
      if (sid && this.mode === "parallel") {
        this.root.querySelectorAll(`.lx-s[data-s="${sid}"]`).forEach((x) => x.classList.add("is-active"));
      }
    }

    _chunkNode(id) { return this.root.querySelector(`.lx-c[data-id="${id}"]`) || this.root.querySelector(`.lx-gl-src[data-id="${id}"]`); }

    openPop(id, pin, scroll) {
      const info = this.index.get(id);
      if (!info) return;
      this.root.querySelectorAll(".lx-c.is-peek, .lx-c.is-pinned, .lx-c.is-group").forEach((x) => x.classList.remove("is-peek", "is-pinned", "is-group"));
      const node = this._chunkNode(id);
      if (node && node.classList.contains("lx-c")) node.classList.add(pin ? "is-pinned" : "is-peek");
      if (info.chunk.group) {
        info.sentence.chunks.forEach((c, ci) => {
          if (c.group === info.chunk.group && ci !== info.ci) {
            const g = this.root.querySelector(`.lx-c[data-id="b${info.bi}.s${info.si}.c${ci}"]`);
            if (g) g.classList.add("is-group");
          }
        });
      }
      this.peekId = id;
      this.pinned = pin ? id : null;
      this._fillPop(info, pin);
      this.pop.classList.toggle("is-sheet", this._useSheet());
      this.pop.classList.toggle("is-pinned", !!pin);
      this.anchor = node;
      if (scroll && node && node.scrollIntoView) node.scrollIntoView({ block: "nearest", behavior: "smooth" });
      this._position();
      requestAnimationFrame(() => this.pop.classList.add("is-open"));
    }

    closePop() {
      clearTimeout(this.peekTimer);
      if (!this.pop) return;
      this.pop.classList.remove("is-open", "is-pinned");
      this.root.querySelectorAll(".lx-c.is-peek, .lx-c.is-pinned, .lx-c.is-group").forEach((x) => x.classList.remove("is-peek", "is-pinned", "is-group"));
      this.pinned = null;
      this.peekId = null;
      this.anchor = null;
    }

    _fillPop(info, pin) {
      const c = info.chunk, s = info.sentence;
      const inner = el("div", "lx-pop-inner");
      const top = el("div", "lx-pop-top");
      const src = el("div", "lx-pop-src");
      const w = el("div", "lx-pop-word", c.text);
      if (RTL(this.lang)) w.dir = "rtl";
      src.appendChild(w);
      const tl = [c.reading, c.translit].filter(Boolean).join(" · ");
      if (tl) src.appendChild(el("div", "lx-pop-translit", tl));
      top.appendChild(src);
      if (Speech.ok) {
        const b = el("button", "lx-icon-btn");
        b.type = "button"; b.title = STR.listen; b.setAttribute("aria-label", STR.listen);
        b.appendChild(svg(ICON_SPEAK));
        b.addEventListener("click", (e) => { e.stopPropagation(); Speech.speak(c.text, this.lang); });
        top.appendChild(b);
      }
      const x = el("button", "lx-icon-btn lx-pop-close");
      x.type = "button"; x.setAttribute("aria-label", STR.close);
      x.appendChild(svg(ICON_X));
      x.addEventListener("click", () => this.closePop());
      top.appendChild(x);
      inner.appendChild(top);

      inner.appendChild(el("div", "lx-pop-gloss", c.gloss || ""));
      if (c.literal) inner.appendChild(el("div", "lx-pop-lit", `${STR.literal} ${c.literal}`));
      if (c.lemma || c.grammar) {
        const tags = el("div", "lx-pop-tags");
        if (c.lemma && c.lemma !== c.text) tags.appendChild(el("span", "lx-tag lx-tag-lemma", c.lemma));
        if (c.grammar) c.grammar.split(/\s*[·•|]\s*/).forEach((g) => g && tags.appendChild(el("span", "lx-tag", g)));
        inner.appendChild(tags);
      }
      if (c.note) inner.appendChild(el("p", "lx-pop-note", c.note));
      const a = this.ann[info.id] || {};
      if (a.comment) inner.appendChild(el("div", "lx-pop-user", a.comment));
      if (pin || this._useSheet()) {
        if (this.mode !== "parallel" && s.translation) {
          const box = el("div", "lx-pop-sent");
          box.appendChild(el("b", null, STR.sentence));
          box.appendChild(el("span", null, s.translation));
          if (s.note) box.appendChild(el("div", null, s.note));
          inner.appendChild(box);
        }
        if (this.opts.editable && this.opts.onAnnotate) inner.appendChild(this._annotateUI(info.id, a));
      } else if (!this._useSheet()) {
        inner.appendChild(el("div", "lx-pop-hint", STR.hintPin));
      }
      this.pop.innerHTML = "";
      this.pop.appendChild(inner);
    }

    _annotateUI(id, a) {
      const box = el("div");
      const row = el("div", "lx-pop-actions");
      ["yellow", "green", "blue", "pink", ""].forEach((col) => {
        const sw = el("button", "lx-swatch" + ((a.color || "") === col ? " is-on" : ""));
        sw.type = "button"; sw.dataset.c = col;
        sw.title = col ? "Resaltar" : "Sin color";
        sw.setAttribute("aria-label", sw.title + (col ? " " + col : ""));
        sw.addEventListener("click", () => this._annotate(id, Object.assign({}, this.ann[id] || {}, { color: col || null })));
        row.appendChild(sw);
      });
      const noteBtn = el("button", "lx-btn-sm", a.comment ? STR.note + " ✎" : STR.addNote);
      noteBtn.type = "button";
      row.appendChild(noteBtn);
      box.appendChild(row);
      noteBtn.addEventListener("click", () => {
        if (box.querySelector("textarea")) return;
        const ta = el("textarea");
        ta.placeholder = STR.yourNote;
        ta.value = (this.ann[id] || {}).comment || "";
        const save = el("button", "lx-btn-sm", STR.save);
        save.type = "button";
        save.style.marginTop = "8px";
        save.addEventListener("click", () => this._annotate(id, Object.assign({}, this.ann[id] || {}, { comment: ta.value.trim() || null })));
        box.appendChild(ta); box.appendChild(save);
        ta.focus();
        this._position();
      });
      return box;
    }

    async _annotate(id, ann) {
      const clean = {};
      if (ann.color) clean.color = ann.color;
      if (ann.comment) clean.comment = ann.comment;
      const prev = this.ann[id];
      if (Object.keys(clean).length) this.ann[id] = clean; else delete this.ann[id];
      this.root.querySelectorAll(`.lx-c[data-id="${id}"]`).forEach((n) => this._decorate(n, id));
      try {
        await this.opts.onAnnotate(id, Object.keys(clean).length ? clean : null);
      } catch (e) {
        if (prev) this.ann[id] = prev; else delete this.ann[id];
        this.root.querySelectorAll(`.lx-c[data-id="${id}"]`).forEach((n) => this._decorate(n, id));
      }
      if (this.mode === "study") this.render();
      this.openPop(id, true);
    }

    _position() {
      if (!this.pop || !this.pop.classList.contains("is-open") && !this.peekId) return;
      if (this.pop.classList.contains("is-sheet")) return;
      const a = this.anchor;
      if (!a) return;
      const r = a.getBoundingClientRect();
      const pw = this.pop.offsetWidth, ph = this.pop.offsetHeight;
      const vw = window.innerWidth, vh = window.innerHeight;
      let left = Math.min(Math.max(12, r.left + r.width / 2 - pw / 2), vw - pw - 12);
      let top = r.bottom + 10;
      if (top + ph > vh - 12 && r.top - ph - 10 > 12) top = r.top - ph - 10;
      top = Math.max(12, Math.min(top, vh - ph - 12));
      this.pop.style.left = left + "px";
      this.pop.style.top = top + "px";
    }
  }

  global.LextorReader = {
    mount(root, doc, opts) { return new Reader(root, doc, opts); },
    langName: LANG_NAME,
    speech: Speech,
    STR,
  };
})(typeof window !== "undefined" ? window : this);
