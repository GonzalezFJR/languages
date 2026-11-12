"""Builds the self-contained MCP Apps widget (ui://lextor/reader-*.html) from the shared reader assets."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_STATIC = Path(__file__).resolve().parent.parent.parent / "static"

_WIDGET_CSS = r"""
html, body { margin: 0; padding: 0; background: var(--lx-bg); color: var(--lx-text); }
body { font-family: var(--lx-font-sans); }
.w-shell { display: flex; flex-direction: column; border-radius: 16px; overflow: hidden; background: var(--lx-bg); }
.w-bar {
  display: flex; align-items: center; gap: 8px; padding: 10px 12px; flex-wrap: wrap;
  border-bottom: 1px solid var(--lx-line); background: var(--lx-surface);
  position: sticky; top: 0; z-index: 5;
}
.w-brand { display: flex; align-items: center; gap: 8px; font: 700 14px/1 var(--lx-font-sans); letter-spacing: -.01em; margin-right: auto; min-width: 0; }
.w-brand svg { width: 22px; height: 22px; flex: none; }
.w-brand span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.w-seg { display: inline-flex; background: var(--lx-surface-2); border-radius: 999px; padding: 3px; }
.w-seg button {
  border: 0; background: transparent; color: var(--lx-muted); cursor: pointer;
  font: 600 13px/1 var(--lx-font-sans); padding: 8px 12px; border-radius: 999px; min-height: 34px;
}
.w-seg button[aria-pressed="true"] { background: var(--lx-surface); color: var(--lx-text); box-shadow: 0 1px 3px rgba(0,0,0,.12); }
.w-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-width: 36px; height: 36px; padding: 0 10px;
  border-radius: 999px; border: 1px solid var(--lx-line); background: var(--lx-surface); color: var(--lx-text);
  font: 600 13px/1 var(--lx-font-sans); cursor: pointer; text-decoration: none;
}
.w-btn:hover { border-color: var(--lx-accent); color: var(--lx-accent-text); }
.w-btn.w-primary { background: var(--lx-accent); border-color: var(--lx-accent); color: #fff; }
.w-btn.w-primary:hover { filter: brightness(1.08); color: #fff; }
.w-btn svg { width: 16px; height: 16px; }
.w-scroll { overflow-y: auto; max-height: 620px; padding: 28px 22px 40px; overscroll-behavior: contain; }
.w-full .w-scroll { max-height: none; height: calc(100vh - 58px); }
.w-state { padding: 40px 24px; text-align: center; color: var(--lx-muted); font: 500 15px/1.5 var(--lx-font-sans); }
.w-skel { height: 14px; border-radius: 7px; margin: 12px auto; background: linear-gradient(90deg, var(--lx-surface-2), var(--lx-line), var(--lx-surface-2)); background-size: 200% 100%; animation: wsk 1.2s infinite linear; }
@keyframes wsk { from { background-position: 200% 0 } to { background-position: -200% 0 } }
.w-err { color: #c2410c; white-space: pre-wrap; text-align: left; font-size: 13px; }
@media (max-width: 520px) {
  .w-scroll { padding: 20px 16px 32px; }
  .w-hide-sm { display: none; }
}
"""

_WIDGET_JS = r"""
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  let reader = null, links = {}, ctx = {}, rendered = null, rid = 0;
  const pend = new Map();
  const post = (m) => { try { window.parent.postMessage(Object.assign({ jsonrpc: "2.0" }, m), "*"); } catch (e) {} };
  const req = (method, params) => new Promise((ok, no) => { const i = ++rid; pend.set(i, [ok, no]); post({ id: i, method, params }); setTimeout(() => { if (pend.has(i)) { pend.delete(i); no(new Error("timeout")); } }, 8000); });
  const note = (method, params) => post({ method, params });

  function applyTheme() {
    const t = ctx.theme || (window.openai && window.openai.theme);
    if (t === "dark" || t === "light") document.documentElement.dataset.theme = t;
  }

  function state(html) { $("#w-body").innerHTML = html; }

  function render(result) {
    if (!result) return;
    const meta = result._meta || {};
    const sc = result.structuredContent || {};
    if (result.isError) {
      const txt = (result.content || []).map((c) => c.text || "").join("\n");
      state('<div class="w-state"><b>No se pudo crear el texto.</b><div class="w-err"></div></div>');
      $(".w-err").textContent = txt.slice(0, 1500);
      return;
    }
    const doc = meta["lextor/document"];
    links = meta["lextor/links"] || (sc.url ? { url: sc.url } : {});
    $("#w-open").hidden = !links.url;
    if (!doc) {
      state('<div class="w-state">El texto es demasiado largo para la vista previa.<br>Ábrelo en Lextor para leerlo completo.</div>');
      return;
    }
    const key = JSON.stringify([doc.meta, doc.blocks.length, (doc.meta || {}).updated_at]);
    if (rendered === key) return;
    rendered = key;
    $("#w-title").textContent = (doc.meta && doc.meta.title) || "Lextor";
    const host = document.createElement("div");
    $("#w-body").innerHTML = "";
    $("#w-body").appendChild(host);
    if (reader) reader.destroy();
    reader = LextorReader.mount(host, doc, { storageKey: "lextor.widget", narrowAt: 640,
      onModeChange: syncSeg });
    syncSeg(reader.mode);
  }

  function syncSeg(mode) {
    document.querySelectorAll(".w-seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  }

  function openLink(url) {
    if (!url) return;
    if (window.openai && window.openai.openExternal) { try { window.openai.openExternal({ href: url }); return; } catch (e) {} }
    req("ui/open-link", { url }).catch(() => { try { window.open(url, "_blank", "noopener"); } catch (e) {} });
  }

  async function toggleFull() {
    const full = !document.body.classList.contains("w-full");
    const mode = full ? "fullscreen" : "inline";
    try {
      if (window.openai && window.openai.requestDisplayMode) await window.openai.requestDisplayMode({ mode });
      else await req("ui/request-display-mode", { mode });
    } catch (e) {}
    document.body.classList.toggle("w-full", full);
  }

  window.addEventListener("message", (e) => {
    if (e.source !== window.parent) return;
    const m = e.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id != null && !m.method) {
      const p = pend.get(m.id);
      if (p) { pend.delete(m.id); m.error ? p[1](m.error) : p[0](m.result); }
      return;
    }
    switch (m.method) {
      case "ui/notifications/tool-input": if (!rendered) state('<div class="w-state">Preparando el texto bilingüe…<div class="w-skel" style="width:70%"></div><div class="w-skel" style="width:85%"></div><div class="w-skel" style="width:60%"></div></div>'); break;
      case "ui/notifications/tool-result": render(m.params); break;
      case "ui/notifications/tool-cancelled": if (!rendered) state('<div class="w-state">Cancelado.</div>'); break;
      case "ui/notifications/host-context-changed":
        Object.assign(ctx, m.params || {}); applyTheme();
        if (ctx.displayMode) document.body.classList.toggle("w-full", ctx.displayMode === "fullscreen");
        break;
      case "ui/resource-teardown": case "ping": post({ id: m.id, result: {} }); break;
      default: if (m.id != null) post({ id: m.id, error: { code: -32601, message: "Method not found" } });
    }
  });

  // ChatGPT globals (also updated through openai:set_globals)
  function fromOpenAI() {
    const o = window.openai;
    if (!o) return;
    applyTheme();
    if (o.toolResponseMetadata || o.toolOutput) render({ structuredContent: o.toolOutput, _meta: o.toolResponseMetadata });
  }
  window.addEventListener("openai:set_globals", fromOpenAI);

  document.addEventListener("DOMContentLoaded", async () => {
    document.querySelectorAll(".w-seg button").forEach((b) => b.addEventListener("click", () => reader && reader.setMode(b.dataset.mode)));
    $("#w-open").addEventListener("click", () => openLink(links.url));
    $("#w-full").addEventListener("click", toggleFull);
    $("#w-smaller").addEventListener("click", () => reader && reader.setSettings({ scale: Math.max(.8, +(reader.settings.scale - .1).toFixed(2)) }));
    $("#w-bigger").addEventListener("click", () => reader && reader.setSettings({ scale: Math.min(1.6, +(reader.settings.scale + .1).toFixed(2)) }));
    fromOpenAI();
    let lh = 0;
    const watchSize = () => {
      const ro = new ResizeObserver(() => {
        const h = Math.ceil(document.documentElement.getBoundingClientRect().height);
        if (h !== lh) { lh = h; note("ui/notifications/size-changed", { width: Math.ceil(innerWidth), height: h }); }
      });
      ro.observe(document.body);
    };
    try {
      const r = await req("ui/initialize", { protocolVersion: "2026-01-26", appInfo: { name: "lextor-reader", version: "3.0.0" },
        appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] } });
      ctx = (r && r.hostContext) || {};
      applyTheme();
      const modes = ctx.availableDisplayModes;
      if (Array.isArray(modes) && !modes.includes("fullscreen")) $("#w-full").hidden = true;
      note("ui/notifications/initialized", {});
      watchSize();
    } catch (e) { /* not an MCP Apps host (e.g. ChatGPT legacy bridge) */ }
  });
})();
"""

_LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="wg" x1="0" y1="0" x2="1" y2="1">'
         '<stop offset="0" stop-color="#7c6cff"/><stop offset="1" stop-color="#4b3be0"/></linearGradient></defs>'
         '<rect width="32" height="32" rx="9" fill="url(#wg)"/>'
         '<path d="M10 8v16h6" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
         '<path d="M18.5 13.5h5M18.5 18h3.5" stroke="#ffd56a" stroke-width="2.6" stroke-linecap="round"/></svg>')

_ICON_OPEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>'
_ICON_FULL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>'


@lru_cache(maxsize=1)
def widget_html() -> str:
    css = (_STATIC / "css" / "reader.css").read_text(encoding="utf-8")
    js = (_STATIC / "js" / "reader.js").read_text(encoding="utf-8")
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lextor</title>
<style>{css}{_WIDGET_CSS}</style>
</head>
<body>
<div class="w-shell">
  <div class="w-bar">
    <div class="w-brand">{_LOGO}<span id="w-title">Lextor</span></div>
    <div class="w-seg" role="group" aria-label="Modo de lectura">
      <button type="button" data-mode="read" aria-pressed="true">Lectura</button>
      <button type="button" data-mode="parallel" aria-pressed="false">Paralelo</button>
      <button type="button" data-mode="study" aria-pressed="false">Estudio</button>
    </div>
    <button type="button" class="w-btn w-hide-sm" id="w-smaller" aria-label="Texto más pequeño">A−</button>
    <button type="button" class="w-btn w-hide-sm" id="w-bigger" aria-label="Texto más grande">A+</button>
    <button type="button" class="w-btn" id="w-full" aria-label="Pantalla completa" title="Pantalla completa">{_ICON_FULL}</button>
    <button type="button" class="w-btn w-primary" id="w-open" hidden>{_ICON_OPEN}<span class="w-hide-sm">Abrir en Lextor</span></button>
  </div>
  <div class="w-scroll"><div id="w-body"><div class="w-state">Esperando el texto…<div class="w-skel" style="width:70%"></div><div class="w-skel" style="width:85%"></div></div></div></div>
</div>
<script>{js}</script>
<script>{_WIDGET_JS}</script>
</body>
</html>"""
