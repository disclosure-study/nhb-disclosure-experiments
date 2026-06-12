/* study3.js — Study 3 custom page modules + bootstrap.
 * Custom modules: storyPage (archive-style work page + label manipulation),
 * choicePage (behavioral click-next), checksPage (story-specific comprehension +
 * manipulation-check recall + suspicion). Questionnaire pages are config-driven. */
(function () {
  const AI_UMBRELLA_TAG = 'Created Using Generative AI'; // AO3 umbrella tag, verbatim
  const REGISTER_TAG = {
    found_family: 'Found Family',
    light_mystery: 'Mystery',
    bittersweet_romance: 'Bittersweet',
  };

  function getStory(ctx) {
    const all = (ctx.stimuli.stories && ctx.stimuli.stories.stories) || [];
    return all.find(s => s.id === ctx.cond.story) || all[0];
  }

  /* ---------------- archive-style story page + label manipulation ---------- */
  function storyPage(ctx) {
    const story = getStory(ctx);
    const arm = ctx.cond.arm;
    const disclosed = arm === 'L1_disclosed';

    // Tag row — both arms have the SAME number of tags so row length is constant;
    // the only difference is whether position 3 is the AI umbrella tag or a filler.
    const tagThird = disclosed
      ? `<span class="tag ai">${AI_UMBRELLA_TAG}</span>`
      : `<span class="tag">No Archive Warnings Apply</span>`;
    const tags =
      `<a class="tag">Original Work</a>` +
      `<a class="tag">Realistic Fiction</a>` +
      tagThird +
      `<a class="tag">${REGISTER_TAG[story.register] || 'Fiction'}</a>` +
      `<a class="tag">Chapter 1</a>`;

    const card = ctx.components.el('div', 'card archive');
    card.innerHTML =
      `<div class="work-head"><div class="work-title">${story.title}</div>` +
      `<div class="byline">by <strong>${story.author_pseudonym}</strong> · ` +
      `${story.word_count_ch1 || '~1,200'} words · Chapter 1/?</div>` +
      `<div class="tagrow">${tags}</div></div>` +
      `<div class="story-body">${story.chapter1_html}</div>`;
    ctx.root.appendChild(card);

    // scroll depth telemetry
    let maxScroll = 0;
    function onScroll() {
      const docH = document.documentElement.scrollHeight - window.innerHeight;
      const pct = docH > 0 ? Math.min(100, Math.round((window.scrollY / docH) * 100)) : 100;
      if (pct > maxScroll) maxScroll = pct;
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    const scrollLogger = setInterval(() => ctx.log('story_scroll', { scroll_max_pct: maxScroll }), 8000);

    const enter = Date.now();
    const row = ctx.navRow(() => {
      window.removeEventListener('scroll', onScroll);
      clearInterval(scrollLogger);
      ctx.log('story_read', {
        story_id: story.id, register: story.register, arm,
        disclosed, dwell_ms: Date.now() - enter, scroll_max_pct: maxScroll,
      });
      ctx.done({ story_id: story.id, arm });
    }, { label: 'I have finished reading →' });
    card.appendChild(row);
    ctx.startDwellGate(row, ctx.page.min_dwell_ms);
    ctx.log('story_shown', { story_id: story.id, arm, disclosed });
  }

  /* ---------------- behavioral click-through choice ------------------------ */
  function choicePage(ctx) {
    const story = getStory(ctx);
    const card = ctx.components.el('div', 'card');
    card.innerHTML =
      `<h1 class="page-title">Before the final questions…</h1>` +
      `<p>Chapter 1 ended on a cliffhanger. The author has posted <strong>Chapter 2</strong>. ` +
      `You can read it now, or skip straight to the last few questions — it's entirely up to you.</p>`;
    const choices = ctx.components.el('div');
    choices.style.display = 'flex'; choices.style.gap = '0.8rem'; choices.style.marginTop = '1.2rem';
    const readBtn = ctx.components.el('button', 'btn', '📖 Read Chapter 2 now');
    const skipBtn = ctx.components.el('button', 'btn secondary', 'Skip to final questions →');
    choices.appendChild(readBtn); choices.appendChild(skipBtn);
    card.appendChild(choices);
    ctx.root.appendChild(card);

    const shown = Date.now();
    function choose(choice) {
      const latency = Date.now() - shown;
      ctx.log('click_next', { choice, choice_latency_ms: latency });
      if (choice === 'read') {
        const t0 = Date.now();
        card.innerHTML =
          `<h1 class="page-title">${story.title} — Chapter 2</h1>` +
          `<div class="story-body">${story.chapter2_html}</div>`;
        const row = ctx.navRow(() => {
          ctx.log('chapter2_read', { dwell_ms: Date.now() - t0 });
          ctx.done({ click_next: 'read', choice_latency_ms: latency });
        }, { label: 'Continue →' });
        card.appendChild(row);
        window.scrollTo(0, 0);
      } else {
        ctx.done({ click_next: 'skip', choice_latency_ms: latency });
      }
    }
    readBtn.onclick = () => choose('read');
    skipBtn.onclick = () => choose('skip');
  }

  /* ---------------- checks (story-specific) -------------------------------- */
  function checksPage(ctx) {
    const story = getStory(ctx);
    const comp = story.comprehension || { question: 'About the story:', options: ['A', 'B'], correct_index: 0 };
    const card = ctx.components.el('div', 'card');
    card.appendChild(ctx.components.el('h1', 'page-title', 'Just to wrap up'));

    const block = ctx.components.renderBlock({
      items: [
        {
          type: 'mcq', id: 'comprehension', text: comp.question,
          options: comp.options, values: comp.options.map((_, i) => i),
        },
        {
          type: 'mcq', id: 'manip_check',
          text: 'According to the page you saw, did the creator use AI to make this story?',
          options: ['No mention of AI', 'It was AI-assisted', 'It was created with AI', "I can't remember"],
          values: ['none', 'assisted', 'created', 'dunno'],
        },
        {
          type: 'text', id: 'suspicion', long: true, required: false,
          text: 'In a sentence or two — what do you think this study was about? (Optional, but it really helps us.)',
        },
      ],
    });
    card.appendChild(block.el);
    const row = ctx.navRow(() => {
      if (!block.validate()) return;
      const v = block.collect();
      const pass = v.comprehension === comp.correct_index;
      ctx.log('responses', { page: 'checks', values: v, comprehension_pass: pass });
      ctx.done({ comprehension_pass: pass });
    }, { label: 'Continue →' });
    card.appendChild(row);
    ctx.root.appendChild(card);
  }

  /* ---------------- bootstrap ---------------------------------------------- */
  async function boot() {
    NHB.runner.registerModule('storyPage', storyPage);
    NHB.runner.registerModule('choicePage', choicePage);
    NHB.runner.registerModule('checksPage', checksPage);
    let cfg;
    try { cfg = await (await fetch('../config/study3.json')).json(); }
    catch (e) {
      document.body.innerHTML = '<div class="wrap"><div class="card">Could not load study config. ' +
        'If you opened this file directly, serve the folder over HTTP instead.</div></div>';
      return;
    }
    NHB.runner.start(cfg);
  }
  boot();
})();
