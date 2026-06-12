/* runner.js — config-driven page sequencer.
 *
 * A study is a JSON config with an ordered `pages` array. The runner renders
 * each page, logs page_enter / page_exit (with dwell), collects + logs form
 * responses, enforces minimum dwell, drives a progress bar, and dispatches
 * "custom" pages to registered modules (story page, gallery, editor, label step).
 * The same runner powers Study 3 and Study 4 — only the config + a few custom
 * modules differ. */
window.NHB = window.NHB || {};
NHB.runner = (function () {
  let cfg = null, pages = [], idx = 0, cond = null;
  let pageEnterTs = 0, dwellTimer = null;
  const modules = {};
  const data = {};                       // collected per-page payloads
  let appEl = null, progEl = null;

  const C = () => NHB.components;
  const el = (t, c, h) => C().el(t, c, h);

  function registerModule(name, fn) { modules[name] = fn; }

  function buildAssignFn() {
    const conds = cfg.conditions || {};
    const url = new URLSearchParams(location.search);
    return function () {
      const out = {};
      for (const [k, vals] of Object.entries(conds)) {
        // URL override (preview convenience): ?arm=L1_disclosed&story=story_c&regime=R_STIGMA
        const forced = url.get(k);
        out[k] = (forced && vals.includes(forced)) ? forced
          : vals[Math.floor(Math.random() * vals.length)];
      }
      return out;
    };
  }

  async function loadStimuli() {
    NHB.stimuli = NHB.stimuli || {};
    const map = cfg.stimuli || {};
    await Promise.all(Object.entries(map).map(async ([key, url]) => {
      try { NHB.stimuli[key] = await (await fetch(url)).json(); }
      catch (e) { console.error('stimuli load failed', key, url, e); }
    }));
  }

  function mountChrome() {
    document.body.innerHTML = '';
    const bar = el('div', 'topbar');
    bar.innerHTML =
      `<div class="inner"><span class="brand">${cfg.brand || cfg.title || 'Study'}</span>` +
      `<span><span class="modeflag ${NHB.logger.getMode()}">${
        NHB.logger.getMode() === 'preview' ? 'PREVIEW' : 'live'}</span></span></div>` +
      '<div class="progress"><i></i></div>';
    document.body.appendChild(bar);
    progEl = bar.querySelector('.progress > i');
    const wrap = el('div', 'wrap');
    appEl = el('div'); wrap.appendChild(appEl);
    document.body.appendChild(wrap);
  }

  function setProgress() {
    const pct = Math.round((idx) / pages.length * 100);
    if (progEl) progEl.style.width = pct + '%';
  }

  function navRow(onNext, opts) {
    opts = opts || {};
    const row = el('div', 'navrow');
    const left = el('div', 'dwell-note', opts.note || '');
    const btn = el('button', 'btn', opts.label || 'Continue →');
    if (opts.disabled) btn.disabled = true;
    btn.addEventListener('click', onNext);
    row.appendChild(left); row.appendChild(btn);
    row._btn = btn; row._note = left;
    return row;
  }

  function startDwellGate(row, ms) {
    if (!ms || ms <= 0) return;
    row._btn.disabled = true;
    let remain = Math.ceil(ms / 1000);
    const tick = () => {
      row._note.textContent = `Take your time — the button unlocks in ${remain}s.`;
      remain -= 1;
      if (remain < 0) {
        row._btn.disabled = false; row._note.textContent = '';
        clearInterval(dwellTimer); dwellTimer = null;
      }
    };
    tick();
    dwellTimer = setInterval(tick, 1000);
  }

  function log(type, payload, page) {
    return NHB.logger.event(type, payload, page || (pages[idx] && pages[idx].id));
  }

  /* ---------- page renderers ---------- */
  function renderInfo(p) {
    const card = el('div', 'card');
    if (p.title) card.appendChild(el('h1', 'page-title', p.title));
    if (p.html) card.appendChild(el('div', null, p.html));
    const row = navRow(goNext, { label: p.next_label, note: p.dwell_note });
    card.appendChild(row);
    appEl.appendChild(card);
    if (p.min_dwell_ms) startDwellGate(row, p.min_dwell_ms);
  }

  function renderConsent(p) {
    const card = el('div', 'card');
    card.appendChild(el('h1', 'page-title', p.title || 'Consent'));
    card.appendChild(el('div', 'legal', p.html || ''));
    const accept = el('label', 'accept');
    accept.innerHTML =
      `<input type="checkbox"><span>${p.checkbox || 'I have read the above and consent to take part.'}</span>`;
    card.appendChild(accept);
    const cb = accept.querySelector('input');
    const row = navRow(() => {
      if (!cb.checked) { flashErr(card, 'Please tick the box to consent, or close the tab to decline.'); return; }
      log('consent_accept', { version: p.version || 1 });
      goNext();
    }, { label: p.next_label || 'I consent — begin' });
    card.appendChild(row);
    appEl.appendChild(card);
  }

  function renderScreener(p) {
    const card = el('div', 'card');
    card.appendChild(el('h1', 'page-title', p.title || 'A few quick questions'));
    const blocks = (p.blocks || []).map(b => C().renderBlock(b));
    blocks.forEach(b => card.appendChild(b.el));
    const row = navRow(() => {
      if (!blocks.every(b => b.validate())) return;
      const collected = Object.assign({}, ...blocks.map(b => b.collect()));
      log('responses', { page: p.id, values: collected,
        presented: [].concat(...blocks.map(b => b.presented)) });
      // eligibility check
      const fail = (p.eligibility || []).some(rule => !rule.allowed.includes(collected[rule.id]));
      if (fail) {
        log('screened_out', { reason: 'eligibility' });
        renderScreenOut(p.screenout_html);
        return;
      }
      goNext();
    });
    card.appendChild(row);
    appEl.appendChild(card);
  }

  function renderForm(p) {
    const card = el('div', 'card');
    if (p.title) card.appendChild(el('h1', 'page-title', p.title));
    if (p.subtitle) card.appendChild(el('p', 'page-sub', p.subtitle));
    const blocks = (p.blocks || []).map(b => C().renderBlock(b));
    blocks.forEach(b => card.appendChild(b.el));
    const row = navRow(() => {
      if (!blocks.every(b => b.validate())) return;
      const collected = Object.assign({}, ...blocks.map(b => b.collect()));
      data[p.id] = collected;
      log('responses', { page: p.id, values: collected,
        presented: [].concat(...blocks.map(b => b.presented)) });
      goNext();
    }, { label: p.next_label, note: p.dwell_note });
    card.appendChild(row);
    appEl.appendChild(card);
    if (p.min_dwell_ms) startDwellGate(row, p.min_dwell_ms);
  }

  function renderCustom(p) {
    const mod = modules[p.module];
    if (!mod) { renderInfo({ title: 'Missing module', html: p.module }); return; }
    const ctx = {
      root: appEl, page: p, cfg, cond,
      stimuli: NHB.stimuli || {},
      components: C(),
      log: (type, payload) => log(type, payload, p.id),
      navRow, startDwellGate,
      done: (payload) => { if (payload) data[p.id] = payload; goNext(); },
    };
    mod(ctx);
  }

  function renderDebrief(p) {
    const card = el('div', 'card');
    card.appendChild(el('h1', 'page-title', p.title || 'About this study'));
    card.appendChild(el('div', 'legal', p.html || ''));
    let cb = null;
    if (p.reconsent) {
      const accept = el('label', 'accept');
      accept.innerHTML = `<input type="checkbox"><span>${p.reconsent_text ||
        'Having read this debrief, I am happy for my responses to be used.'}</span>`;
      card.appendChild(accept); cb = accept.querySelector('input');
    }
    const row = navRow(() => {
      log('debrief_reconsent', { agreed: cb ? cb.checked : null });
      finish();
    }, { label: p.next_label || 'Finish' });
    card.appendChild(row);
    appEl.appendChild(card);
  }

  function renderScreenOut(html) {
    clearTimers();
    document.querySelector('.wrap').innerHTML =
      `<div class="card"><h1 class="page-title">Thank you</h1><div>${
        html || 'Unfortunately you are not eligible for this study. Thank you for your interest.'
      }</div></div>`;
    if (progEl) progEl.style.width = '100%';
  }

  async function finish() {
    clearTimers();
    const res = await NHB.logger.complete({ pages_seen: idx + 1 });
    const code = (res && res.completion_code) || cfg.completion_code || null;
    const url = cfg.completion_url ||
      (NHB.prolific.isReal() && code ? 'https://app.prolific.com/submissions/complete?cc=' + code : null);
    document.querySelector('.wrap').innerHTML =
      '<div class="center"><div class="big">✓ All done — thank you!</div>' +
      (code ? `<p>Your completion code:</p><div class="code">${code}</div>` : '') +
      (url ? `<p><a class="btn big" href="${url}">Return to Prolific →</a></p>` :
        '<p class="muted">In a live run this screen returns the participant to Prolific with their completion code.</p>') +
      (NHB.logger.getMode() === 'preview' ?
        '<p class="muted" style="margin-top:1.4rem">This was a PREVIEW. No data left your browser. ' +
        'Open the event log (bottom-right) to inspect everything that was recorded, or download it as JSON.</p>' : '') +
      '</div>';
    if (progEl) progEl.style.width = '100%';
  }

  /* ---------- navigation ---------- */
  function goNext() {
    log('page_exit', { dwell_ms: Date.now() - pageEnterTs });
    clearTimers();
    idx += 1;
    if (idx >= pages.length) { finish(); return; }
    renderCurrent();
  }

  function clearTimers() { if (dwellTimer) { clearInterval(dwellTimer); dwellTimer = null; } }

  function renderCurrent() {
    const p = pages[idx];
    NHB.runner.currentPageId = p.id;
    appEl.innerHTML = '';
    window.scrollTo(0, 0);
    pageEnterTs = Date.now();
    setProgress();
    log('page_enter', { type: p.type, index: idx });
    switch (p.type) {
      case 'consent': return renderConsent(p);
      case 'screener': return renderScreener(p);
      case 'form': return renderForm(p);
      case 'custom': return renderCustom(p);
      case 'debrief': return renderDebrief(p);
      case 'complete': return finish();
      default: return renderInfo(p);
    }
  }

  function flashErr(card, msg) {
    card.querySelector('.err')?.remove();
    card.appendChild(el('div', 'err', msg));
  }

  async function start(config) {
    cfg = config;
    pages = config.pages || [];
    await NHB.api.init();
    const res = await NHB.logger.startSession(config.study, { assignFn: buildAssignFn() });
    mountChrome();
    if (!res.ok) {
      appEl.innerHTML = `<div class="card"><h1 class="page-title">Study unavailable</h1><p>${
        res.reason === 'already_participated'
          ? 'Our records show you have already taken part in this study.'
          : 'This study is not currently accepting new participants. Thank you for your interest.'
      }</p></div>`;
      return;
    }
    cond = NHB.logger.getCond();
    await loadStimuli();
    NHB.logger.mountInspector();
    log('study_loaded', { cond, mode: NHB.logger.getMode(),
      platform_version: NHB.platform_version });
    renderCurrent();
  }

  return { start, registerModule, currentPageId: null, getData: () => data, getCond: () => cond };
})();
