/* Lextor app helpers — window.Lextor
   api(path, opts)      fetch JSON with base path; throws Error(detail) on !ok (err.status, err.body)
   toast(msg, kind)     kind: "ok" | "err" | undefined
   copy(text, label)    clipboard + toast
   icon(name, cls)      inline SVG string (same set as Jinja icon())
   openModal(id) / closeModal(id)   for <dialog class="modal">; [data-open="id"], [data-close] work declaratively
   confirm(opts)        promise<boolean> with a styled dialog {title, body, ok, danger}
   esc(str)             HTML-escape
   Menus: <div class="menu"><button data-menu>…</button><div class="menu-list">…</div></div>
   Theme: [data-theme-toggle] cycles system → light → dark */
(function () {
  "use strict";
  const BP = window.__BP__ || "";
  const ICONS = window.__ICONS__ || {};

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function icon(name, cls) {
    return `<svg class="${cls || ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.info || ""}</svg>`;
  }

  async function api(path, opts) {
    opts = Object.assign({}, opts || {});
    if (opts.json !== undefined) {
      opts.body = JSON.stringify(opts.json);
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
      delete opts.json;
    }
    const res = await fetch(BP + path, opts);
    let body = null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) body = await res.json().catch(() => null);
    if (!res.ok) {
      const err = new Error((body && (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail))) || res.statusText || "Error");
      err.status = res.status; err.body = body;
      throw err;
    }
    return body;
  }

  // ── toasts ───────────────────────────────────────────────────────────
  let toastBox;
  function toast(msg, kind) {
    if (!toastBox) { toastBox = document.createElement("div"); toastBox.className = "toasts"; toastBox.setAttribute("role", "status"); document.body.appendChild(toastBox); }
    const t = document.createElement("div");
    t.className = "toast" + (kind ? " toast-" + kind : "");
    t.innerHTML = (kind === "ok" ? icon("check") : kind === "err" ? icon("alert") : "") + `<span>${esc(msg)}</span>`;
    toastBox.appendChild(t);
    setTimeout(() => { t.classList.add("is-leaving"); setTimeout(() => t.remove(), 260); }, kind === "err" ? 4200 : 2600);
  }

  async function copy(text, label) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e2) {} ta.remove();
    }
    toast(label || "Copiado", "ok");
  }

  // ── modals ───────────────────────────────────────────────────────────
  function openModal(id) {
    const d = typeof id === "string" ? document.getElementById(id) : id;
    if (!d) return;
    if (!d.open) d.showModal();
    const f = d.querySelector("[autofocus], input:not([type=hidden]), select, textarea");
    if (f && !matchMedia("(pointer: coarse)").matches) setTimeout(() => f.focus(), 40);
    return d;
  }
  function closeModal(id) {
    const d = typeof id === "string" ? document.getElementById(id) : id;
    if (d && d.open) d.close();
  }
  function confirmDialog(o) {
    o = Object.assign({ title: "¿Seguro?", body: "", ok: "Confirmar", cancel: "Cancelar", danger: false }, o || {});
    return new Promise((resolve) => {
      const d = document.createElement("dialog");
      d.className = "modal";
      d.innerHTML = `<div class="modal-head"><h2 class="h3">${esc(o.title)}</h2></div>
        <div class="modal-body"><p class="muted" style="margin:0">${esc(o.body)}</p></div>
        <div class="modal-foot"><button class="btn" data-v="0">${esc(o.cancel)}</button>
        <button class="btn ${o.danger ? "btn-danger" : "btn-primary"}" data-v="1">${esc(o.ok)}</button></div>`;
      document.body.appendChild(d);
      let val = false;
      d.addEventListener("click", (e) => {
        const b = e.target.closest("[data-v]");
        if (b) { val = b.dataset.v === "1"; d.close(); }
        else if (e.target === d) d.close();
      });
      d.addEventListener("close", () => { d.remove(); resolve(val); });
      d.showModal();
      d.querySelector('[data-v="1"]').focus();
    });
  }

  // ── theme ────────────────────────────────────────────────────────────
  const THEMES = ["system", "light", "dark"];
  function getTheme() { try { return localStorage.getItem("lextor.theme") || "system"; } catch (e) { return "system"; } }
  function setTheme(t) {
    try { localStorage.setItem("lextor.theme", t); } catch (e) {}
    const apply = () => {
      if (t === "system") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.dataset.theme = t;
      syncThemeButtons();
    };
    if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) document.startViewTransition(apply);
    else apply();
  }
  function syncThemeButtons() {
    const t = getTheme();
    const name = t === "light" ? "sun" : t === "dark" ? "moon" : "monitor";
    const label = t === "light" ? "Tema claro" : t === "dark" ? "Tema oscuro" : "Tema del sistema";
    document.querySelectorAll("[data-theme-toggle]").forEach((b) => {
      b.innerHTML = icon(name) + (b.dataset.themeToggle === "label" ? `<span>${label}</span>` : "");
      b.title = label + " (cambiar)";
      b.setAttribute("aria-label", label + ". Pulsa para cambiar");
    });
  }

  // ── wiring ───────────────────────────────────────────────────────────
  document.addEventListener("click", (e) => {
    const t = e.target;
    const opener = t.closest("[data-open]");
    if (opener) {
      e.preventDefault();
      const m = opener.closest(".menu"); if (m) m.classList.remove("is-open");
      openModal(opener.dataset.open); return;
    }
    const closer = t.closest("[data-close]");
    if (closer) { e.preventDefault(); closeModal(closer.closest("dialog")); return; }
    if (t.tagName === "DIALOG" && t.classList.contains("modal")) { t.close(); return; } // backdrop click
    const themeBtn = t.closest("[data-theme-toggle]");
    if (themeBtn) { setTheme(THEMES[(THEMES.indexOf(getTheme()) + 1) % 3]); return; }
    const copyBtn = t.closest("[data-copy]");
    if (copyBtn) { copy(copyBtn.dataset.copy, copyBtn.dataset.copyLabel); return; }
    const copyTarget = t.closest("[data-copy-target]");
    if (copyTarget) { const src = document.querySelector(copyTarget.dataset.copyTarget); if (src) copy(src.value != null && src.tagName !== "PRE" && src.tagName !== "CODE" ? src.value : src.textContent, copyTarget.dataset.copyLabel); return; }
    const menuBtn = t.closest("[data-menu]");
    document.querySelectorAll(".menu.is-open").forEach((m) => { if (!menuBtn || m !== menuBtn.closest(".menu")) { m.classList.remove("is-open"); const b = m.querySelector("[data-menu]"); if (b) b.setAttribute("aria-expanded", "false"); } });
    if (menuBtn) {
      e.preventDefault();
      const m = menuBtn.closest(".menu");
      const open = m.classList.toggle("is-open");
      menuBtn.setAttribute("aria-expanded", String(open));
      return;
    }
    if (t.closest(".menu-list > button, .menu-list > a")) {
      const m = t.closest(".menu"); if (m) m.classList.remove("is-open");
    }
    const burger = t.closest("[data-mobile-nav]");
    if (burger) { document.getElementById("mobile-nav").classList.toggle("is-open"); return; }
    if (t.closest(".mobile-nav-backdrop")) document.getElementById("mobile-nav").classList.remove("is-open");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".menu.is-open").forEach((m) => m.classList.remove("is-open"));
      const mn = document.getElementById("mobile-nav"); if (mn) mn.classList.remove("is-open");
    }
  });

  function initReveal() {
    const els = document.querySelectorAll(".reveal");
    if (!("IntersectionObserver" in window)) { els.forEach((x) => x.classList.add("is-in")); return; }
    const io = new IntersectionObserver((entries) => entries.forEach((en) => {
      if (en.isIntersecting) { en.target.classList.add("is-in"); io.unobserve(en.target); }
    }), { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    els.forEach((x) => io.observe(x));
  }

  document.addEventListener("DOMContentLoaded", () => {
    syncThemeButtons();
    initReveal();
    const nav = document.querySelector(".nav");
    if (nav) {
      const on = () => nav.classList.toggle("is-scrolled", window.scrollY > 4);
      on(); window.addEventListener("scroll", on, { passive: true });
    }
  });

  window.Lextor = { api, toast, copy, icon, esc, openModal, closeModal, confirm: confirmDialog, setTheme, getTheme, BP };
})();
