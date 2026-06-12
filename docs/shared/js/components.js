/* components.js — reusable questionnaire item renderers.
 * Each renderItem(...) returns { el, id, get, validate, presented } so the runner
 * can render a block, collect a {id: value} map, validate required items, and log
 * the presented order (for randomized blocks). */
window.NHB = window.NHB || {};
NHB.components = (function () {
  const DEFAULT_LIKERT_LABELS = [
    'Strongly disagree', 'Disagree', 'Somewhat disagree',
    'Neither agree nor disagree', 'Somewhat agree', 'Agree', 'Strongly agree',
  ];

  function shuffle(arr, rng) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor((rng ? rng() : Math.random()) * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function likert(item) {
    const scale = item.scale || {};
    const min = scale.min || 1, max = scale.max || 7;
    const hasAnchors = !!(scale.min_label || scale.max_label);
    // Per-point text labels only when the item is an agree-type scale (no custom
    // endpoint anchors). Items with their own anchors — "Very unlikely / Very
    // likely", "Very little / A great deal" — show numbers + endpoint anchors only,
    // so the point labels never contradict the anchors.
    const labels = scale.labels ||
      ((!hasAnchors && max - min + 1 === 7) ? DEFAULT_LIKERT_LABELS : null);
    const wrap = el('div', 'qitem');
    const required = item.required !== false;
    wrap.appendChild(el('p', 'qtext',
      item.text + (required ? ' <span class="req">*</span>' : '')));
    const row = el('div', 'likert');
    let value = null;
    for (let v = min; v <= max; v++) {
      const lab = el('label');
      const labelText = labels ? labels[v - min] : '';
      lab.innerHTML = `<input type="radio" name="${item.id}" value="${v}">` +
        `<span class="num">${v}</span>${labelText ? `<span>${labelText}</span>` : ''}`;
      lab.querySelector('input').addEventListener('change', () => {
        value = v;
        row.querySelectorAll('label').forEach(l => l.classList.remove('sel'));
        lab.classList.add('sel');
        wrap.querySelector('.err')?.remove();
      });
      row.appendChild(lab);
    }
    wrap.appendChild(row);
    // Endpoint anchors only when there are no per-point labels (which already
    // carry their own endpoints) — avoids a redundant second label row.
    if (hasAnchors && !labels) {
      wrap.appendChild(el('div', 'scale-anchor',
        `<span>${scale.min_label || ''}</span><span>${scale.max_label || ''}</span>`));
    }
    return {
      el: wrap, id: item.id,
      get: () => value,
      validate() {
        if (required && value == null) { addErr(wrap, 'Please answer this item.'); return false; }
        return true;
      },
    };
  }

  function attention(item) {
    const base = likert(item);
    base.meta = { kind: 'attention', correct: item.correct };
    const origGet = base.get;
    base.get = () => {
      const v = origGet();
      return v == null ? null : { value: v, pass: v === item.correct };
    };
    return base;
  }

  function mcq(item) {
    const wrap = el('div', 'qitem');
    const required = item.required !== false;
    wrap.appendChild(el('p', 'qtext',
      item.text + (required ? ' <span class="req">*</span>' : '')));
    const box = el('div', 'choices');
    let value = null;
    const opts = item.options.map((o, i) => ({
      label: o, value: item.values ? item.values[i] : o,
    }));
    opts.forEach(o => {
      const lab = el('label');
      lab.innerHTML = `<input type="radio" name="${item.id}"><span>${o.label}</span>`;
      lab.querySelector('input').addEventListener('change', () => {
        value = o.value;
        box.querySelectorAll('label').forEach(l => l.classList.remove('sel'));
        lab.classList.add('sel');
        wrap.querySelector('.err')?.remove();
      });
      box.appendChild(lab);
    });
    wrap.appendChild(box);
    return {
      el: wrap, id: item.id, get: () => value,
      validate() {
        if (required && value == null) { addErr(wrap, 'Please choose an option.'); return false; }
        return true;
      },
    };
  }

  function slider(item) {
    const wrap = el('div', 'qitem');
    const required = item.required !== false;
    wrap.appendChild(el('p', 'qtext', item.text + (required ? ' <span class="req">*</span>' : '')));
    const min = item.min ?? 0, max = item.max ?? 100, start = item.start ?? Math.round((min + max) / 2);
    let touched = false, value = start;
    const sw = el('div', 'slider-wrap');
    sw.innerHTML = `<span class="muted">${item.min_label || min}</span>` +
      `<input type="range" min="${min}" max="${max}" value="${start}" step="${item.step || 1}">` +
      `<span class="muted">${item.max_label || max}</span>` +
      `<span class="slider-val muted">drag to choose</span>`;
    const range = sw.querySelector('input'); const out = sw.querySelector('.slider-val');
    range.addEventListener('input', () => {
      touched = true; value = +range.value;
      out.classList.remove('muted'); out.textContent = value + (item.unit || '');
      wrap.querySelector('.err')?.remove();
    });
    wrap.appendChild(sw);
    return {
      el: wrap, id: item.id, get: () => (touched ? value : (required ? null : start)),
      validate() {
        if (required && !touched) { addErr(wrap, 'Please move the slider to make a choice.'); return false; }
        return true;
      },
    };
  }

  function text(item) {
    const wrap = el('div', 'qitem');
    const required = !!item.required;
    wrap.appendChild(el('p', 'qtext', item.text + (required ? ' <span class="req">*</span>' : '')));
    const input = item.long ? el('textarea') : el('input');
    if (!item.long) input.type = 'text';
    if (item.placeholder) input.placeholder = item.placeholder;
    input.addEventListener('input', () => wrap.querySelector('.err')?.remove());
    wrap.appendChild(input);
    if (item.note) wrap.appendChild(el('div', 'dwell-note', item.note));
    return {
      el: wrap, id: item.id, get: () => input.value.trim() || null,
      validate() {
        const v = input.value.trim();
        if (required && !v) { addErr(wrap, 'Please respond.'); return false; }
        if (item.min_words && v.split(/\s+/).filter(Boolean).length < item.min_words) {
          addErr(wrap, `Please write at least ${item.min_words} words.`); return false;
        }
        return true;
      },
    };
  }

  function statement(item) {
    const wrap = el('div', 'qitem');
    wrap.appendChild(el('div', null, item.text));
    return { el: wrap, id: item.id || null, get: () => null, validate: () => true };
  }

  const RENDERERS = { likert, attention, mcq, slider, text, number: text, statement };

  function renderBlock(block, logFn) {
    const wrap = el('div', 'block');
    if (block.title) wrap.appendChild(el('h2', null, block.title));
    if (block.instructions) wrap.appendChild(el('p', 'block-instr', block.instructions));
    let items = block.items.slice();
    if (block.randomize) items = shuffle(items);
    const presented = items.map(i => i.id).filter(Boolean);
    const handles = items.map(it => {
      const r = (RENDERERS[it.type] || statement)(it);
      wrap.appendChild(r.el);
      return r;
    });
    return {
      el: wrap, presented,
      collect() {
        const out = {};
        handles.forEach(h => { if (h.id) out[h.id] = h.get(); });
        return out;
      },
      validate() {
        let ok = true; let firstBad = null;
        handles.forEach(h => { if (!h.validate() && !firstBad) { firstBad = h.el; ok = false; } });
        if (firstBad) firstBad.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return ok;
      },
    };
  }

  function addErr(wrap, msg) {
    wrap.querySelector('.err')?.remove();
    wrap.appendChild(el('div', 'err', msg));
  }

  return { renderBlock, DEFAULT_LIKERT_LABELS, shuffle, el };
})();
