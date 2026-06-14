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
  let appEl = null, progEl = null, stepEl = null;

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
      `<span style="display:flex;align-items:center;gap:.75rem"><span class="step-label"></span>` +
      `<span class="modeflag ${NHB.logger.getMode()}">${
        NHB.logger.getMode() === 'preview' ? 'PREVIEW' : 'live'}</span></span></div>` +
      '<div class="progress"><i></i></div>';
    document.body.appendChild(bar);
    progEl = bar.querySelector('.progress > i');
    stepEl = bar.querySelector('.step-label');
    const wrap = el('div', 'wrap');
    appEl = el('div'); wrap.appendChild(appEl);
    document.body.appendChild(wrap);
  }

  function setProgress() {
    const pct = Math.round((idx) / pages.length * 100);
    if (progEl) progEl.style.width = pct + '%';
    if (stepEl) stepEl.textContent = 'Step ' + (idx + 1) + ' of ' + pages.length;
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
    const realServer = NHB.logger.getMode() === 'server' && !NHB.logger.getIsTest();
    document.querySelector('.wrap').innerHTML =
      '<div class="center"><div class="check-circle">✓</div><div class="big">All done — thank you!</div>' +
      (code ? '<p>Your completion code — please enter it on the recruitment platform to confirm your participation:</p>' +
        `<div class="code">${code}</div>` : '') +
      (realServer ? paymentFormHtml() : '') +
      (NHB.logger.getMode() === 'preview' ?
        '<p class="muted" style="margin-top:1.4rem">This was a PREVIEW. No data left your browser. ' +
        'Open the event log (bottom-right) to inspect everything that was recorded, or download it as JSON.</p>' : '') +
      '</div>';
    if (realServer) wirePaymentForm();
    if (progEl) progEl.style.width = '100%';
  }

  function paymentFormHtml() {
    return '<div class="subform" style="text-align:left;max-width:460px;margin:1.6rem auto 0">' +
      '<h2>Receiving your payment · 收款方式</h2>' +
      '<p class="muted" style="font-size:.85rem">If the research team is paying you directly (not through a ' +
      'recruitment platform), please tell us how to send it. Stored securely and used only to pay you. ' +
      '<span lang="zh">如果由研究团队直接向您付款（非通过招募平台），请提供收款方式。信息将被安全保存，仅用于向您付款。</span></p>' +
      '<select id="payMethod"><option value="alipay">Alipay 支付宝</option>' +
      '<option value="wechat">WeChat 微信</option><option value="bank">Bank 银行</option>' +
      '<option value="other">Other 其他</option></select>' +
      '<input id="payAccount" type="text" placeholder="Account / ID · 账号（如支付宝账号、手机号）">' +
      '<input id="payName" type="text" placeholder="Account holder name · 收款人姓名">' +
      '<p class="muted" style="font-size:.85rem;margin:.7rem 0 .1rem">Or upload your payment QR code · 或上传收款二维码：</p>' +
      '<input id="payQr" type="file" accept="image/png,image/jpeg,image/gif,image/webp">' +
      '<textarea id="payNote" placeholder="Note (optional) · 备注（选填）"></textarea>' +
      '<div id="payActions"></div><div class="dwell-note" id="payResult"></div></div>';
  }

  function wirePaymentForm() {
    const wrap = document.querySelector('.wrap');
    const btn = el('button', 'btn', 'Submit payment details · 提交收款信息');
    wrap.querySelector('#payActions').appendChild(btn);
    btn.addEventListener('click', async () => {
      const out = wrap.querySelector('#payResult');
      const account = wrap.querySelector('#payAccount').value.trim();
      const note = wrap.querySelector('#payNote').value.trim();
      const file = wrap.querySelector('#payQr').files[0];
      if (!account && !note && !file) {
        out.textContent = 'Please give an account, a note, or a QR image. 请填写账号、备注或上传二维码。'; return;
      }
      if (file && file.size > 3 * 1024 * 1024) {
        out.textContent = 'Image too large (max 3 MB). 图片过大（上限 3MB）。'; return;
      }
      btn.disabled = true;
      const fd = new FormData();
      fd.append('token', NHB.logger.getToken()); fd.append('study', cfg.study);
      fd.append('method', wrap.querySelector('#payMethod').value);
      fd.append('account', account); fd.append('name', wrap.querySelector('#payName').value.trim());
      fd.append('note', note);
      if (file) fd.append('qr', file);
      let r = null;
      try { r = await (await fetch((NHB.api.apiBase || '') + '/api/payment', { method: 'POST', body: fd })).json(); }
      catch (e) { /* network */ }
      if (r && r.ok) {
        out.textContent = 'Thank you — your payment details have been received. 已收到您的收款信息，谢谢。';
        btn.textContent = 'Submitted ✓ · 已提交';
      } else {
        out.textContent = (r && r.reason === 'too_large') ? 'Image too large. 图片过大。'
          : (r && r.reason === 'bad_image_type') ? 'Please upload an image (PNG / JPG). 请上传图片（PNG/JPG）。'
          : 'Sorry, please try again. 抱歉，请重试。';
        btn.disabled = false;
      }
    });
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
    appEl.classList.remove('page-anim'); void appEl.offsetWidth; appEl.classList.add('page-anim');
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
    mountChrome();
    const health = NHB.api.getHealth() || {};
    // Experiment finished -> show the "data collection complete" page (apply form
    // + a demonstration-code field). Only relevant in server mode.
    if (NHB.api.getMode() === 'server' && health.experiment_status === 'closed') {
      renderClosedPage(); return;
    }
    // Server mode: gate on an invitation code (unless the server disables it).
    // Preview mode: no gate (no data is collected anyway).
    const needInvite = NHB.api.getMode() === 'server' && health.invite_required !== false;
    if (needInvite) renderInviteGate(null);
    else beginSession(null);
  }

  function gateError(reason) {
    return ({
      bad_invite: 'Invalid invitation code. 邀请码无效。',
      invite_used: 'This code has already been used. 此邀请码已被使用。',
      invite_required: 'Please enter your invitation code. 请输入您的邀请码。',
      inactive: 'This code is no longer active. 此邀请码已停用。',
    })[reason] || 'Invalid invitation code. 邀请码无效。';
  }

  function renderInviteGate(errReason) {
    appEl.innerHTML = '';
    const card = el('div', 'card gate');
    card.innerHTML =
      `<h1 class="page-title">${cfg.brand || cfg.title || 'Study'}</h1>` +
      (cfg.summary_en ? `<p>${cfg.summary_en}</p>` : '') +
      (cfg.summary_zh ? `<p class="muted" lang="zh">${cfg.summary_zh}</p>` : '') +
      `<div class="notice info">All content is in <strong>English</strong> — please continue only if you ` +
      `read and write English comfortably.<br><span lang="zh">本研究全部内容为<strong>英文</strong>，` +
      `请仅在您能顺畅读写英文的情况下继续。</span></div>` +
      `<label class="gate-label" for="inviteInput">Invitation code · 邀请码</label>` +
      `<input id="inviteInput" type="text" autocomplete="off" spellcheck="false" placeholder="Enter your code · 输入邀请码">` +
      (errReason ? `<div class="err">${gateError(errReason)}</div>` : '');
    const row = navRow(() => {
      const code = card.querySelector('#inviteInput').value.trim();
      if (!code) { renderInviteGate('invite_required'); return; }
      row._btn.disabled = true;
      beginSession(code);
    }, { label: 'Begin · 开始' });
    card.appendChild(row);
    appEl.appendChild(card);
    const inp = card.querySelector('#inviteInput');
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') row._btn.click(); });
    inp.focus();
  }

  function showTestBanner() {
    const f = document.querySelector('.modeflag');
    if (f) { f.className = 'modeflag test'; f.textContent = '● TEST — not saved · 测试'; }
  }

  async function beginSession(invite) {
    const res = await NHB.logger.startSession(cfg.study, { assignFn: buildAssignFn(), invite });
    if (!res.ok) {
      if (['bad_invite', 'invite_used', 'invite_required', 'inactive'].includes(res.reason)) {
        renderInviteGate(res.reason); return;
      }
      if (res.reason === 'experiment_closed') { renderClosedPage(); return; }
      appEl.innerHTML = `<div class="card"><h1 class="page-title">Study unavailable</h1><p>${
        res.reason === 'already_participated'
          ? 'Our records show you have already taken part in this study.'
          : 'This study is not currently accepting new participants. Thank you for your interest.'
      }</p></div>`;
      return;
    }
    proceedSession(res);
  }

  async function proceedSession(res) {
    if (res.test) {
      alert('TEST MODE — this is a test run. Your responses will NOT be saved.\n\n' +
        '测试模式 — 这是测试运行，您的回答不会被保存。');
      showTestBanner();
    }
    cond = NHB.logger.getCond();
    await loadStimuli();
    NHB.logger.mountInspector(!!res.test);
    log('study_loaded', { cond, mode: NHB.logger.getMode(), test: !!res.test,
      platform_version: NHB.platform_version });
    renderCurrent();
  }

  function renderClosedPage(demoErr) {
    appEl.innerHTML = '';
    const card = el('div', 'card');
    card.innerHTML =
      '<h1 class="page-title">This study has finished</h1>' +
      '<p>Thank you for your interest. Data collection for this study is complete, so it is no longer ' +
      'accepting participants.</p>' +
      '<p class="muted" lang="zh">感谢您的关注。本研究的数据收集已结束，目前不再接受新的参与者。</p>' +
      '<div class="subform"><h2>Interested in taking part or learning more?</h2>' +
      '<p class="muted">Leave a message and the research team may be in touch. ' +
      '<span lang="zh">留下您的留言，研究团队可能会与您联系。</span></p>' +
      '<p class="muted" style="font-size:.82rem">Optional — anything you enter is stored only so the ' +
      'research team can contact you, and is seen only by them. ' +
      '<span lang="zh">选填 — 您填写的信息仅用于研究团队与您联系，且仅研究团队可见。</span></p>' +
      '<input id="apName" type="text" placeholder="Name (optional) · 姓名（可选）">' +
      '<input id="apContact" type="text" placeholder="Email or contact (optional) · 邮箱 / 联系方式（可选）">' +
      '<textarea id="apMsg" placeholder="Your message · 您的留言"></textarea>' +
      '<div id="apActions"></div><div class="dwell-note" id="apResult"></div></div>' +
      '<div class="subform"><h2>Just want to explore the study?</h2>' +
      '<p class="muted">Enter the demonstration code to try it — nothing is saved. ' +
      '<span lang="zh">输入演示码即可试用，不会保存任何数据。</span></p>' +
      '<input id="demoCode" type="text" autocomplete="off" spellcheck="false" placeholder="Demonstration code · 演示码">' +
      '<div id="demoActions"></div>' + (demoErr ? `<div class="err">${demoErr}</div>` : '') + '</div>';
    appEl.appendChild(card);

    const applyBtn = el('button', 'btn', 'Send message · 发送');
    card.querySelector('#apActions').appendChild(applyBtn);
    applyBtn.addEventListener('click', async () => {
      const message = card.querySelector('#apMsg').value.trim();
      const out = card.querySelector('#apResult');
      if (!message) { out.textContent = 'Please write a message. 请填写留言。'; return; }
      applyBtn.disabled = true;
      let res = null;
      try {
        res = await NHB.api.post('/api/apply', {
          name: card.querySelector('#apName').value,
          contact: card.querySelector('#apContact').value, message });
      } catch (e) { /* network */ }
      if (res && res.ok) {
        out.textContent = 'Thank you — your message has been received. 已收到您的留言，谢谢。';
        card.querySelector('#apMsg').value = '';
        card.querySelector('#apName').value = '';
        card.querySelector('#apContact').value = '';
      } else { out.textContent = 'Sorry, please try again later. 抱歉，请稍后再试。'; applyBtn.disabled = false; }
    });

    const demoBtn = el('button', 'btn secondary', 'Try the demo · 试用');
    card.querySelector('#demoActions').appendChild(demoBtn);
    async function tryDemo() {
      const code = card.querySelector('#demoCode').value.trim();
      if (!code) return;
      demoBtn.disabled = true;
      const res = await NHB.logger.startSession(cfg.study, { assignFn: buildAssignFn(), invite: code });
      if (!res.ok) {
        demoBtn.disabled = false;
        let e = card.querySelector('#demoErrLine');
        if (!e) { e = el('div', 'err'); e.id = 'demoErrLine'; card.querySelector('#demoActions').after(e); }
        e.textContent = res.reason === 'experiment_closed'
          ? 'Only the demonstration code works here. 仅演示码可用。' : gateError(res.reason);
        return;
      }
      proceedSession(res);
    }
    demoBtn.addEventListener('click', tryDemo);
    card.querySelector('#demoCode').addEventListener('keydown', e => { if (e.key === 'Enter') tryDemo(); });
  }

  return { start, registerModule, currentPageId: null, getData: () => data, getCond: () => cond };
})();
