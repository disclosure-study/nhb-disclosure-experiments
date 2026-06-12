/* logger.js — the heart of "reflect all actions".
 *
 * Owns the session token and a monotonically increasing event sequence. Every
 * participant action funnels through NHB.logger.event(type, payload, page):
 *   - server mode: POSTs to /api/event; failures are buffered and flushed via
 *     sendBeacon on pagehide (the zero-loss rule).
 *   - preview mode: appended to localStorage and downloadable as JSON.
 * In both modes the event is mirrored into the on-screen inspector panel so the
 * action stream is visible.
 */
window.NHB = window.NHB || {};
NHB.logger = (function () {
  let study = null, token = null, cond = null, mode = 'preview';
  let seq = 0;
  const localEvents = [];
  const retry = [];
  let inspector = null, inspectorList = null;

  const uuid = () =>
    'e_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  const now = () => Date.now();

  function persistLocal() {
    try {
      localStorage.setItem('nhb_' + study + '_events', JSON.stringify(localEvents));
      localStorage.setItem('nhb_' + study + '_session',
        JSON.stringify({ token, cond, ts: now() }));
    } catch (e) { /* storage full / disabled — ignore */ }
  }

  async function startSession(studyId, opts) {
    opts = opts || {};
    study = studyId;
    mode = NHB.api.getMode();
    if (mode === 'server') {
      const res = await NHB.api.post('/api/session/start', {
        study: studyId,
        prolific: NHB.prolific.all(),
        screen: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio },
        user_agent: navigator.userAgent,
      });
      if (!res.ok) return res;          // intake_closed / already_participated
      token = res.token; cond = res.cond;
      NHB.platform_version = res.platform_version;
      NHB.config_version = res.config_version;
      return res;
    }
    // preview: assign locally
    token = 'preview_' + uuid();
    cond = opts.assignFn ? opts.assignFn() : {};
    NHB.platform_version = 'preview';
    NHB.config_version = 'preview';
    persistLocal();
    return { ok: true, token, cond, platform_version: 'preview' };
  }

  function event(type, payload, page) {
    if (!token) return null;
    seq += 1;
    const ev = {
      token, study, type,
      page: page || (NHB.runner && NHB.runner.currentPageId) || null,
      payload: payload || {},
      client_ts: now(), seq, client_event_id: uuid(),
    };
    renderRow(ev);
    if (mode === 'server') {
      NHB.api.post('/api/event', ev).catch(() => { retry.push(ev); });
      // side effects the server also computes, mirrored client-side for inspector:
    } else {
      localEvents.push(ev);
      persistLocal();
    }
    return ev;
  }

  function flush() {
    if (mode === 'server' && retry.length) {
      NHB.api.beacon('/api/events/batch', { events: retry.splice(0) });
    }
  }
  window.addEventListener('pagehide', flush);
  window.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush();
  });

  async function complete(summary) {
    if (mode === 'server') {
      return NHB.api.post('/api/session/complete', { token, study, summary: summary || {} });
    }
    event('session_complete', summary || {}, 'done');
    return { ok: true, completion_code: (NHB.prolific.isReal() ? null : 'PREVIEW-NO-CODE') };
  }

  /* ----- inspector panel ----- */
  function mountInspector(force) {
    const show = force || mode === 'preview' ||
      new URLSearchParams(location.search).get('debug') === '1';
    if (!show || inspector) return;
    inspector = document.createElement('div');
    inspector.id = 'inspector';
    inspector.innerHTML =
      '<button class="tab">▣ event log (0)</button>' +
      '<div class="panel"><div class="head"><span>live event stream — ' +
      (mode === 'preview' ? 'PREVIEW (stored locally)' : 'server logging') + '</span>' +
      '<span><button id="dlEvents">download JSON</button>' +
      '<button id="clrEvents">clear view</button></span></div>' +
      '<div id="evlist"></div></div>';
    document.body.appendChild(inspector);
    inspectorList = inspector.querySelector('#evlist');
    const tab = inspector.querySelector('.tab');
    tab.onclick = () => inspector.classList.toggle('open');
    inspector.querySelector('#dlEvents').onclick = downloadLocal;
    inspector.querySelector('#clrEvents').onclick = () => { inspectorList.innerHTML = ''; };
  }
  function renderRow(ev) {
    if (!inspectorList) return;
    const d = new Date(ev.client_ts);
    const hh = d.toTimeString().slice(0, 8);
    const div = document.createElement('div');
    div.className = 'row';
    const pl = JSON.stringify(ev.payload);
    div.innerHTML = `<span class="t">${hh}</span> #${ev.seq} ` +
      `<span class="ty">${ev.type}</span> <span style="color:#9ab">${ev.page || ''}</span> ` +
      `<span style="color:#789">${pl.length > 90 ? pl.slice(0, 90) + '…' : pl}</span>`;
    inspectorList.prepend(div);
    const tab = inspector.querySelector('.tab');
    if (tab) tab.textContent = '▣ event log (' + ev.seq + ')';
  }
  function downloadLocal() {
    const data = {
      study, token, cond, mode,
      platform_version: NHB.platform_version,
      exported_at: new Date().toISOString(),
      events: mode === 'preview' ? localEvents : '(server mode — see server logs)',
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = study + '_preview_data.json';
    a.click();
  }

  return {
    startSession, event, complete, mountInspector,
    getToken: () => token, getCond: () => cond, getMode: () => mode,
    getLocalEvents: () => localEvents,
  };
})();
