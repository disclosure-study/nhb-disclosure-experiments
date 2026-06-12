/* study4.js — Study 4 custom modules + bootstrap.
 * galleryPage   — simulated gallery; per-regime like rendering (the regime carrier).
 * editorPage    — writing editor + embedded, fully-logged AI assistant + telemetry.
 * labelPage     — regime script (revealed AFTER writing) + truthful-label choice.
 * publishingPage— "publishing…" interstitial (piece is never really shown to others).
 * In preview mode the assistant runs an offline deterministic suggester. */
(function () {
  const MIN_WORDS = 120;
  const ASSIST_CAP = 20;

  /* ---------------- offline assistant (preview / fallback) ----------------- */
  const OFFLINE_OPENINGS = [
    'The envelope had no stamp, only my name in a hand I almost recognised.',
    'By the time the streetlights buzzed on, the shop was the only window still gold.',
    'She found the letter tucked under the doormat, soft from the rain.',
    'The bell over the door had not rung in hours, and still he waited.',
    'Nobody wrote letters any more, which was exactly why this one frightened her.',
  ];
  const OFFLINE_CONTINUATIONS = [
    ' She read it twice, then a third time, as if the words might rearrange themselves into something kinder.',
    ' He set down his pen and listened to the building settle around him, every creak a small confession.',
    ' Outside, the rain kept its own counsel, and the room seemed to lean in to hear what came next.',
  ];
  function hashIdx(s, n) {
    let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return h % n;
  }
  function offlineAssistant(aff, draft, selected, theme, msg) {
    const key = aff + '|' + theme + '|' + draft.slice(-60) + '|' + selected.slice(0, 60) + '|' + msg.slice(0, 60);
    if (aff === 'suggest_opening') return OFFLINE_OPENINGS[hashIdx(key, OFFLINE_OPENINGS.length)];
    if (aff === 'continue') return OFFLINE_CONTINUATIONS[hashIdx(key, OFFLINE_CONTINUATIONS.length)];
    if (aff === 'polish') {
      const base = (selected || draft).trim().replace(/\s+/g, ' ');
      return base ? (/[.!?]$/.test(base) ? base : base + '.') : '(nothing selected to polish)';
    }
    return OFFLINE_CONTINUATIONS[hashIdx(key, OFFLINE_CONTINUATIONS.length)];
  }

  /* ---------------- gallery (regime carrier) ------------------------------- */
  function median(arr) {
    const a = arr.slice().sort((x, y) => x - y); const m = Math.floor(a.length / 2);
    return a.length % 2 ? a[m] : Math.round((a[m - 1] + a[m]) / 2);
  }
  function galleryPage(ctx) {
    const pieces = ctx.stimuli.gallery.pieces;
    const regime = ctx.cond.regime;
    const unlMedian = median(pieces.filter(p => !p.ai_labeled).map(p => p.likes_base));
    const aiIds = pieces.filter(p => p.ai_labeled).map(p => p.id);
    const showcaseIds = regime === 'R_BENEFIT' ? aiIds.slice(0, 2) : [];
    const targetIds = ['g03', 'g08'];   // the two pieces the participant rates (shown in full)
    const displayLikes = {};

    function view(p, i) {
      let likes = p.likes_base, badge = false, label = null;
      if (p.ai_labeled) {
        label = 'Created with AI';
        if (regime === 'R_STIGMA') likes = Math.max(1, Math.round(0.4 * unlMedian * (0.85 + (i % 5) * 0.06)));
        if (regime === 'R_BENEFIT') badge = true;
      }
      displayLikes[p.id] = likes;
      return { likes, badge, label };
    }
    function card(p, i) {
      const v = view(p, i);
      const isTarget = targetIds.includes(p.id);
      return `<div class="gpiece${isTarget ? ' gtarget' : ''}">` +
        (v.badge ? '<div class="gbadge-row"><span class="vbadge">✓ Verified-Transparent</span></div>' : '') +
        (isTarget ? '<div class="rate-tag">★ You’ll rate this piece below</div>' : '') +
        `<div class="gtitle">${p.title}</div><div class="gauthor">by ${p.author}` +
        `${v.label ? ` · <span class="ailabel">${v.label}</span>` : ''}</div>` +
        `<div class="gbody${isTarget ? ' full' : ''}">${p.body_html}</div>` +
        `<div class="glikes">♥ ${v.likes} likes</div></div>`;
    }

    const wrap = ctx.components.el('div');
    let html = `<div class="card"><h1 class="page-title">This week's gallery</h1>` +
      `<p class="page-sub">Browse the pieces other writers have shared. Then rate two of them below to learn the rating format, before writing your own.</p>`;
    if (showcaseIds.length) {
      html += `<div class="showcase"><div class="showcase-h">★ Transparency Showcase</div><div class="gwrap">` +
        showcaseIds.map((id) => card(pieces.find(p => p.id === id), pieces.findIndex(p => p.id === id))).join('') +
        `</div></div>`;
    }
    // Main feed excludes the showcased pieces so they are not shown verbatim twice.
    const gridItems = pieces.map((p, i) => ({ p, i })).filter(x => !showcaseIds.includes(x.p.id));
    html += `<div class="gwrap">` + gridItems.map(x => card(x.p, x.i)).join('') + `</div></div>`;
    wrap.innerHTML = html;

    // rating task — two fixed unlabeled pieces (cover task)
    const targets = targetIds.map(id => pieces.find(p => p.id === id)).filter(Boolean);
    const rateBlock = ctx.components.renderBlock({
      title: 'Rate these two pieces',
      items: targets.map(p => ({
        type: 'likert', id: 'rate_' + p.id,
        text: 'How much did you enjoy “' + p.title + '” by ' + p.author + '?',
        scale: { min: 1, max: 7, min_label: 'Not at all', max_label: 'Very much' },
      })),
    });
    const rc = ctx.components.el('div', 'card'); rc.appendChild(rateBlock.el); wrap.appendChild(rc);
    ctx.root.appendChild(wrap);

    ctx.log('gallery_view', { regime, unlabeled_median: unlMedian, ai_display_likes: displayLikes, showcase: showcaseIds });
    const enter = Date.now();
    const row = ctx.navRow(() => {
      if (!rateBlock.validate()) return;
      const ratings = rateBlock.collect();
      Object.entries(ratings).forEach(([k, v]) => ctx.log('gallery_rate', { piece: k.replace('rate_', ''), rating: v }));
      ctx.log('gallery_browse', { regime, dwell_ms: Date.now() - enter });
      ctx.done({ regime, ratings });
    }, { label: 'Start writing my piece →' });
    rc.appendChild(row);
    ctx.startDwellGate(row, ctx.page.min_dwell_ms);
  }

  /* ---------------- editor + assistant ------------------------------------- */
  function editorPage(ctx) {
    const prompt = ctx.stimuli.gallery.prompts.find(p => p.id === ctx.cond.prompt) ||
      ctx.stimuli.gallery.prompts[0];
    const mode = NHB.logger.getMode();
    let used = 0, lastCopied = '', keystrokes = 0;
    const start = Date.now();

    const card = ctx.components.el('div', 'card');
    card.innerHTML =
      `<h1 class="page-title">Write your piece</h1>` +
      `<div class="notice info"><strong>This week's theme:</strong> ${prompt.title}. ` +
      `${prompt.text} <em>(at least ${MIN_WORDS} words to continue)</em></div>` +
      `<div class="editor-grid">` +
        `<div class="editor-col">` +
          `<textarea id="draft" placeholder="Start writing here…" spellcheck="true"></textarea>` +
          `<div class="ed-status"><span id="wc">0 words</span><span id="saved" class="muted"></span></div>` +
        `</div>` +
        `<div class="assist" id="assist">` +
          `<div class="assist-h"><span>✍ Writing Assistant</span><button id="collapse" class="mini">hide</button></div>` +
          `<div class="assist-body" id="abody">` +
            `<p class="muted" style="font-size:.8rem">Optional. Anything you use is yours to keep or change. ` +
            `<span id="provnote"></span></p>` +
            `<div class="assist-btns">` +
              `<button class="chip" data-aff="suggest_opening">Suggest an opening</button>` +
              `<button class="chip" data-aff="continue">Continue my draft</button>` +
              `<button class="chip" data-aff="polish">Polish selected text</button>` +
            `</div>` +
            `<div class="assist-ask"><input id="ask" type="text" placeholder="Ask the assistant…"><button id="asksend" class="mini">Ask</button></div>` +
            `<div id="responses"></div>` +
            `<div class="muted" id="cap" style="font-size:.78rem;margin-top:.4rem"></div>` +
          `</div>` +
        `</div>` +
      `</div>`;
    ctx.root.appendChild(card);

    const ta = card.querySelector('#draft');
    const wc = card.querySelector('#wc');
    const savedEl = card.querySelector('#saved');
    const responses = card.querySelector('#responses');
    const capEl = card.querySelector('#cap');
    card.querySelector('#provnote').textContent =
      mode === 'server' ? '' : 'Running offline in preview mode.';

    function words() { return ta.value.trim() ? ta.value.trim().split(/\s+/).length : 0; }
    function updateWC() {
      const n = words(); wc.textContent = n + ' word' + (n === 1 ? '' : 's');
      row._btn.disabled = n < MIN_WORDS;
      row._note.textContent = n < MIN_WORDS ? `${MIN_WORDS - n} more words to continue.` : '';
    }
    function insertAtCaret(text) {
      const s = ta.selectionStart, e = ta.selectionEnd;
      ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
      ta.selectionStart = ta.selectionEnd = s + text.length;
      ta.focus(); updateWC();
      return s;
    }

    ta.addEventListener('keydown', (e) => { if (e.key.length === 1 || e.key === 'Backspace' || e.key === 'Enter') keystrokes++; });
    ta.addEventListener('input', updateWC);
    ta.addEventListener('focus', () => ctx.log('focus_change', { state: 'focus' }));
    ta.addEventListener('blur', () => ctx.log('focus_change', { state: 'blur' }));
    window.addEventListener('blur', () => ctx.log('focus_change', { state: 'window_blur' }));
    ta.addEventListener('paste', (e) => {
      const text = (e.clipboardData || window.clipboardData).getData('text') || '';
      const isAssistant = lastCopied && text.trim() && lastCopied.includes(text.trim().slice(0, 40));
      ctx.log('paste', { chars: text.length, flag: isAssistant ? 'assistant_copy' : 'external' });
      if (isAssistant && text.length >= 20) ctx.log('any_use_true', { via: 'copy_paste' });
    });

    // assistant calls
    async function callAssistant(aff, userMsg) {
      if (used >= ASSIST_CAP) { capEl.textContent = 'Assistant request limit reached for this session.'; return; }
      const selected = ta.value.slice(ta.selectionStart, ta.selectionEnd);
      let text, source, model, ok = true;
      if (mode === 'server') {
        const r = await NHB.api.post('/api/s4/assistant', {
          token: NHB.logger.getToken(), affordance: aff, draft: ta.value,
          selected, prompt_theme: prompt.title, user_message: userMsg || '',
        });
        ok = r.ok; text = r.text; source = r.source; model = r.model;
        if (r.remaining != null) used = ASSIST_CAP - r.remaining;
      } else {
        used++;
        ctx.log('assistant_request', { affordance: aff, request_no: used, draft_len: ta.value.length, selected_text: selected, user_message: userMsg || '' });
        text = offlineAssistant(aff, ta.value, selected, prompt.title, userMsg || '');
        source = 'offline'; model = 'offline-canned';
        ctx.log('assistant_response', { affordance: aff, request_no: used, response_text: text, source, model });
      }
      capEl.textContent = `${used} / ${ASSIST_CAP} assistant requests used`;
      if (!ok) { renderNotice(text); return; }
      renderResponse(aff, text, source);
    }
    function renderNotice(text) {
      const n = ctx.components.el('div', 'notice warn'); n.textContent = text; responses.prepend(n);
    }
    function renderResponse(aff, text, source) {
      const r = ctx.components.el('div', 'aresp');
      r.innerHTML = `<div class="atext">${text.replace(/</g, '&lt;')}</div>` +
        `<div class="aacts"><button class="mini ins">Insert</button>` +
        `<button class="mini cp">Copy</button><button class="mini dz">Dismiss</button></div>`;
      r.querySelector('.ins').onclick = () => {
        const off = insertAtCaret((/[.!?]$/.test(ta.value.trim()) || !ta.value.trim() ? '' : ' ') + text + ' ');
        ctx.log('assistant_insert', { affordance: aff, chars_inserted: text.length, insertion_offset: off });
      };
      r.querySelector('.cp').onclick = () => {
        lastCopied = text;
        try { navigator.clipboard.writeText(text); } catch (e) { /* ignore */ }
        ctx.log('assistant_copy', { affordance: aff, chars: text.length });
      };
      r.querySelector('.dz').onclick = () => { r.remove(); ctx.log('assistant_dismiss', { affordance: aff }); };
      responses.prepend(r);
    }

    card.querySelectorAll('.chip').forEach(b => b.onclick = () => callAssistant(b.dataset.aff));
    card.querySelector('#asksend').onclick = () => {
      const v = card.querySelector('#ask').value.trim();
      if (v) { callAssistant('ask', v); card.querySelector('#ask').value = ''; }
    };
    const collapseBtn = card.querySelector('#collapse');
    collapseBtn.onclick = () => {
      const body = card.querySelector('#abody');
      const hidden = body.style.display === 'none';
      body.style.display = hidden ? '' : 'none';
      collapseBtn.textContent = hidden ? 'hide' : 'show';
      ctx.log(hidden ? 'assistant_expand' : 'assistant_collapse', {});
    };

    // autosave
    const autosave = setInterval(() => {
      ctx.log('writing_autosave', { word_count: words(), text: ta.value });
      savedEl.textContent = 'saved'; setTimeout(() => savedEl.textContent = '', 1200);
    }, 20000);

    const row = ctx.navRow(() => {
      clearInterval(autosave);
      ctx.log('writing_final', {
        final_text: ta.value, word_count: words(),
        writing_ms: Date.now() - start, keystroke_count: keystrokes,
        assistant_requests: used,
      });
      ctx.done({ final_text: ta.value, word_count: words() });
    }, { label: 'Done — preview my piece →', disabled: true });
    card.appendChild(row);
    updateWC();
    ctx.log('writing_started', { prompt_id: prompt.id, prompt_title: prompt.title });
  }

  /* ---------------- label step (regime revealed here) ---------------------- */
  function labelPage(ctx) {
    const regime = ctx.cond.regime;
    const script = (ctx.cfg.regime_scripts || {})[regime] || 'Choose a label for your piece.';
    const menu = ctx.cfg.label_menu || [];
    const writing = (NHB.runner.getData().writing) || {};
    const text = writing.final_text || '(your piece)';

    const card = ctx.components.el('div', 'card');
    card.innerHTML =
      `<h1 class="page-title">Add your piece to the gallery</h1>` +
      `<div class="piece-preview"><div class="pp-h">Your piece — preview</div>` +
      `<div class="pp-body">${text.replace(/</g, '&lt;').replace(/\n/g, '<br>')}</div></div>` +
      `<div class="notice info" style="font-size:1rem">${script}</div>` +
      (regime === 'R_BENEFIT' ? `<p class="muted" style="font-size:.85rem">Disclosing AI use adds a ✓ Verified-Transparent badge.</p>` : '') +
      `<div class="choices" id="labelmenu">` +
      menu.map(m => `<label><input type="radio" name="lbl" value="${m.value}"><span>${m.label}</span></label>`).join('') +
      `</div>`;
    ctx.root.appendChild(card);

    const shown = Date.now();
    let firstTs = null;
    const toggles = [];
    let choice = null;
    card.querySelectorAll('input[name=lbl]').forEach(inp => inp.addEventListener('change', () => {
      choice = inp.value;
      if (!firstTs) firstTs = Date.now();
      toggles.push({ value: choice, t_ms: Date.now() - shown });
      card.querySelectorAll('#labelmenu label').forEach(l => l.classList.remove('sel'));
      inp.closest('label').classList.add('sel');
      card.querySelector('.err')?.remove();
    }));

    const row = ctx.navRow(() => {
      if (!choice) {
        const e = ctx.components.el('div', 'err', 'Please choose one label to continue.');
        card.querySelector('#labelmenu').after(e); return;
      }
      ctx.log('label_choice', {
        regime, label_choice: choice,
        decision_latency_ms: Date.now() - shown,
        time_to_first_ms: firstTs ? firstTs - shown : null,
        toggle_sequence: toggles, n_toggles: toggles.length,
      });
      ctx.done({ label_choice: choice, regime });
    }, { label: 'Add to gallery →' });
    card.appendChild(row);
    ctx.log('label_step_shown', { regime });
  }

  /* ---------------- publishing interstitial -------------------------------- */
  function publishingPage(ctx) {
    const card = ctx.components.el('div', 'card');
    card.innerHTML = `<div class="center" style="margin:3vh auto"><div class="spinner"></div>` +
      `<div class="big" style="margin-top:1rem">Publishing your piece to the gallery…</div></div>`;
    ctx.root.appendChild(card);
    ctx.log('publishing_shown', {});
    setTimeout(() => ctx.done({}), 1900);
  }

  /* ---------------- bootstrap ---------------------------------------------- */
  async function boot() {
    NHB.runner.registerModule('galleryPage', galleryPage);
    NHB.runner.registerModule('editorPage', editorPage);
    NHB.runner.registerModule('labelPage', labelPage);
    NHB.runner.registerModule('publishingPage', publishingPage);
    let cfg;
    try { cfg = await (await fetch('../config/study4.json')).json(); }
    catch (e) {
      document.body.innerHTML = '<div class="wrap"><div class="card">Could not load study config. ' +
        'Serve the folder over HTTP rather than opening the file directly.</div></div>';
      return;
    }
    NHB.runner.start(cfg);
  }
  boot();
})();
