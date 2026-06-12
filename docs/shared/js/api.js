/* api.js — backend client + mode detection.
 *
 * The SAME frontend runs in two modes with no build-time difference:
 *   server  — a FastAPI backend is reachable: real server-side write-ahead logging.
 *   preview — no backend (e.g. GitHub Pages): client-side randomization + the
 *             event log is kept locally and shown in the inspector panel.
 *
 * Detection: if window.NHB_RUNTIME.forcePreview or ?preview=1 -> preview;
 * otherwise probe /api/health with a short timeout.
 */
window.NHB = window.NHB || {};
NHB.api = (function () {
  const runtime = window.NHB_RUNTIME || {};
  let apiBase = runtime.apiBase != null ? runtime.apiBase : '';
  let mode = 'preview';
  let health = null;

  async function init() {
    const url = new URLSearchParams(location.search);
    if (url.get('preview') === '1' || runtime.forcePreview) { mode = 'preview'; return mode; }
    // No backend on GitHub Pages or a file:// open — go straight to preview.
    if (location.protocol === 'file:' || /\.github\.io$/.test(location.hostname)) {
      mode = 'preview'; return mode;
    }
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 1800);
      const r = await fetch(apiBase + '/api/health', { signal: ctrl.signal });
      clearTimeout(t);
      if (r.ok) { health = await r.json(); mode = 'server'; return mode; }
    } catch (e) { /* fall through to preview */ }
    mode = 'preview';
    return mode;
  }

  async function post(path, body) {
    const r = await fetch(apiBase + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    return r.json();
  }

  function beacon(path, body) {
    try {
      const blob = new Blob([JSON.stringify(body)], { type: 'application/json' });
      return navigator.sendBeacon(apiBase + path, blob);
    } catch (e) { return false; }
  }

  return {
    init, post, beacon,
    getMode: () => mode,
    getHealth: () => health,
    get apiBase() { return apiBase; },
  };
})();
