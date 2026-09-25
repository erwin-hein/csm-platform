// Schema explorer (/admin/schema). Plain JS, no dependencies, like the rest of the app.
// Data: #sx-data, built server-side from the live database + CLAUDE.md §3 (app/web/schema.py).
(function () {
  const data = JSON.parse(document.getElementById('sx-data').textContent);
  const viewport = document.getElementById('sx-viewport');
  const world = document.getElementById('sx-world');
  const cardsEl = document.getElementById('sx-cards');
  const sectionsEl = document.getElementById('sx-sections');
  const edgeLayer = document.getElementById('sx-edge-layer');
  const panel = document.getElementById('sx-panel');
  const $ = id => document.getElementById(id);

  const CARD_W = 250, GAP = 22, PAD = 16, TITLE_H = 30, SECTION_GAP = 44, ROW_MAX = 2250;
  const HUES = [217, 162, 280, 24, 340, 190, 45, 120, 255, 0, 300, 90, 205, 60, 150, 320];
  const byName = Object.fromEntries(data.tables.map(t => [t.name, t]));
  const sectionHue = Object.fromEntries(data.sections.map((s, i) => [s, HUES[i % HUES.length]]));
  const decisionsFor = name => data.decisions.filter(d => d.table === name);

  const store = {
    get(k, d) { try { const v = localStorage.getItem('sx:' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('sx:' + k, JSON.stringify(v)); } catch (e) {} },
  };
  let moved = store.get('moved', {});            // table -> {x, y}, only for tables the viewer dragged
  let pos = {};                                  // table -> {x, y, w, h}
  let view = {x: 20, y: 20, k: 0.8};
  let selected = null, query = '';
  const opts = {planned: store.get('planned', false), compact: store.get('compact', false)};
  $('sx-planned').checked = opts.planned;
  $('sx-compact').checked = opts.compact;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const visible = () => data.tables.filter(t => t.built || opts.planned);

  // ------------------------------------------------------------------ cards

  const STATUS_LABEL = {spec_only: 'in the spec, not built yet', superseded: 'superseded in the spec', poc_addition: 'PoC addition, not in the spec', planned: 'planned'};

  function columnRow(t, c) {
    const key = c.pk ? '<span class="k pk" title="primary key">PK</span>' : c.fk ? '<span class="k fk" title="references ' + esc(c.fk) + '">FK</span>' : '<span class="k"></span>';
    const cls = ['sx-col', 'st-' + c.status, c.pk ? 'is-pk' : '', c.fk ? 'is-fk' : ''].join(' ');
    return '<div class="' + cls + '" data-col="' + esc(c.name) + '"' + (c.fk ? ' data-fk="' + esc(c.fk) + '"' : '') +
      ' title="' + esc((STATUS_LABEL[c.status] && c.status !== 'planned' ? STATUS_LABEL[c.status] + ' · ' : '') + (c.comment || c.type)) + '">' +
      key + '<span class="n">' + esc(c.name) + '</span><span class="t">' + esc(c.type.replace(/\(.*\)/, '')) + '</span></div>';
  }

  function renderCards() {
    cardsEl.innerHTML = visible().map(t => {
      const cols = opts.compact ? t.columns.filter(c => c.pk || c.fk) : t.columns;
      const hidden = t.columns.length - cols.length;
      return '<div class="sx-card' + (t.built ? '' : ' planned') + '" data-table="' + esc(t.name) + '" style="--h:' + sectionHue[t.section] + '">' +
        '<div class="sx-head"><span class="sx-name">' + esc(t.name) + '</span>' +
        (t.built ? '<span class="sx-rows" title="rows right now">' + t.rows + '</span>' : '<span class="sx-planned-tag">planned</span>') + '</div>' +
        '<div class="sx-cols">' + cols.map(c => columnRow(t, c)).join('') +
        (hidden ? '<div class="sx-more">+ ' + hidden + ' more column' + (hidden === 1 ? '' : 's') + '</div>' : '') + '</div></div>';
    }).join('');
  }

  // ------------------------------------------------------------------ layout

  function layout() {
    const cards = {};
    cardsEl.querySelectorAll('.sx-card').forEach(el => { cards[el.dataset.table] = el; });
    const groups = [];
    data.sections.forEach(s => {
      const ts = visible().filter(t => t.section === s);
      if (ts.length) groups.push({section: s, tables: ts});
    });
    let x = 0, y = 0, rowH = 0;
    pos = {};
    groups.forEach(g => {
      const n = g.tables.length;
      const ncol = n <= 2 ? n : n <= 4 ? 2 : 3;
      const colH = new Array(ncol).fill(0);
      const place = [];
      g.tables.forEach(t => {
        const i = colH.indexOf(Math.min(...colH));
        const h = cards[t.name].offsetHeight;
        place.push({t, cx: i * (CARD_W + GAP), cy: colH[i], h});
        colH[i] += h + GAP;
      });
      const w = ncol * CARD_W + (ncol - 1) * GAP + 2 * PAD;
      const h = Math.max(...colH) - GAP + TITLE_H + 2 * PAD;
      if (x > 0 && x + w > ROW_MAX) { x = 0; y += rowH + SECTION_GAP; rowH = 0; }
      place.forEach(p => {
        pos[p.t.name] = {x: x + PAD + p.cx, y: y + TITLE_H + PAD + p.cy, w: CARD_W, h: p.h};
      });
      x += w + SECTION_GAP;
      rowH = Math.max(rowH, h);
    });
    Object.entries(moved).forEach(([name, p]) => { if (pos[name]) Object.assign(pos[name], p); });
    Object.entries(pos).forEach(([name, p]) => { cards[name].style.transform = 'translate(' + p.x + 'px,' + p.y + 'px)'; });
    drawSections(groups);
  }

  function drawSections(groups) {
    groups = groups || data.sections.map(s => ({section: s, tables: visible().filter(t => t.section === s)})).filter(g => g.tables.length);
    sectionsEl.innerHTML = groups.map(g => {
      const ps = g.tables.map(t => pos[t.name]).filter(Boolean);
      const x0 = Math.min(...ps.map(p => p.x)) - PAD, y0 = Math.min(...ps.map(p => p.y)) - PAD - TITLE_H;
      const x1 = Math.max(...ps.map(p => p.x + p.w)) + PAD, y1 = Math.max(...ps.map(p => p.y + p.h)) + PAD;
      const built = g.tables.some(t => t.built);
      return '<div class="sx-section' + (built ? '' : ' planned') + '" data-section="' + esc(g.section) + '" style="--h:' + sectionHue[g.section] +
        ';left:' + x0 + 'px;top:' + y0 + 'px;width:' + (x1 - x0) + 'px;height:' + (y1 - y0) + 'px"><span>' + esc(g.section) + '</span></div>';
    }).join('');
  }

  // ------------------------------------------------------------------ edges

  function rowY(tableName, colName) {
    const card = cardsEl.querySelector('.sx-card[data-table="' + CSS.escape(tableName) + '"]');
    const row = colName && card && card.querySelector('.sx-col[data-col="' + CSS.escape(colName) + '"]');
    const p = pos[tableName];
    if (!row) return p.y + 16;
    return p.y + row.offsetTop + row.offsetHeight / 2;
  }

  function drawEdges() {
    const vis = new Set(visible().map(t => t.name));
    const parts = [];
    data.edges.forEach((e, i) => {
      if (!vis.has(e.from) || !vis.has(e.to) || !pos[e.from] || !pos[e.to]) return;
      const a = pos[e.from], b = pos[e.to];
      const y1 = rowY(e.from, e.cols[0]), y2 = rowY(e.to, e.to_cols[0]);
      let d;
      if (e.from === e.to) {
        const x = a.x + a.w;
        d = 'M' + x + ',' + y1 + ' C' + (x + 46) + ',' + y1 + ' ' + (x + 46) + ',' + y2 + ' ' + x + ',' + (y2 + 0.1);
      } else {
        const aC = a.x + a.w / 2, bC = b.x + b.w / 2;
        let x1, x2, s1, s2;
        if (b.x > a.x + a.w + 10) { x1 = a.x + a.w; x2 = b.x; s1 = 1; s2 = -1; }
        else if (a.x > b.x + b.w + 10) { x1 = a.x; x2 = b.x + b.w; s1 = -1; s2 = 1; }
        else { const right = aC >= bC ? false : true; x1 = right ? a.x + a.w : a.x; x2 = right ? b.x + b.w : b.x; s1 = s2 = right ? 1 : -1; }
        const dx = Math.max(50, Math.abs(x2 - x1) / 2);
        d = 'M' + x1 + ',' + y1 + ' C' + (x1 + s1 * dx) + ',' + y1 + ' ' + (x2 + s2 * dx) + ',' + y2 + ' ' + x2 + ',' + y2;
      }
      const hot = selected && (e.from === selected || e.to === selected);
      const cls = 'sx-edge' + (e.planned ? ' planned' : '') + (hot ? ' hot' : '') + (selected && !hot ? ' dim' : '');
      parts.push('<path class="' + cls + '" data-i="' + i + '" data-from="' + esc(e.from) + '" data-to="' + esc(e.to) + '" data-col="' + esc(e.cols[0]) +
        '" d="' + d + '" marker-end="url(#' + (hot ? 'sx-arrow-hot' : 'sx-arrow') + ')"/>');
    });
    edgeLayer.innerHTML = parts.join('');
  }

  // ------------------------------------------------------------------ highlight, search

  function neighbours(name) {
    const n = new Set([name]);
    data.edges.forEach(e => { if (e.from === name) n.add(e.to); if (e.to === name) n.add(e.from); });
    return n;
  }

  function matches(t) {
    if (!query) return true;
    return t.name.includes(query) || t.columns.some(c => c.name.includes(query));
  }

  function paintState() {
    // While searching, the search decides what's lit; otherwise the selection and its neighbours.
    const near = selected && !query ? neighbours(selected) : null;
    cardsEl.querySelectorAll('.sx-card').forEach(el => {
      const t = byName[el.dataset.table];
      el.classList.toggle('sel', el.dataset.table === selected);
      el.classList.toggle('dim', (near && !near.has(t.name)) || (!!query && !matches(t)));
      el.classList.toggle('hit', !!query && matches(t));
      el.querySelectorAll('.sx-col').forEach(r => r.classList.toggle('hit', !!query && r.dataset.col.includes(query)));
    });
    drawEdges();
  }

  // ------------------------------------------------------------------ view (pan / zoom)

  function applyView() { world.style.transform = 'translate(' + view.x + 'px,' + view.y + 'px) scale(' + view.k + ')'; }

  function fit() {
    const ps = Object.values(pos);
    if (!ps.length) return;
    const x0 = Math.min(...ps.map(p => p.x)) - 60, y0 = Math.min(...ps.map(p => p.y)) - 70;
    const x1 = Math.max(...ps.map(p => p.x + p.w)) + 60, y1 = Math.max(...ps.map(p => p.y + p.h)) + 40;
    const r = viewport.getBoundingClientRect();
    view.k = Math.min(1.1, r.width / (x1 - x0), r.height / (y1 - y0));
    view.x = (r.width - (x1 - x0) * view.k) / 2 - x0 * view.k;
    view.y = (r.height - (y1 - y0) * view.k) / 2 - y0 * view.k;
    applyView();
  }

  // Zoom to a table and everything it's related to, so every one of its relationships is on screen.
  function centerOn(name) {
    const ps = [...neighbours(name)].map(n => pos[n]).filter(Boolean);
    if (!ps.length) return;
    const x0 = Math.min(...ps.map(p => p.x)) - 50, y0 = Math.min(...ps.map(p => p.y)) - 60;
    const x1 = Math.max(...ps.map(p => p.x + p.w)) + 70, y1 = Math.max(...ps.map(p => p.y + p.h)) + 50;
    const r = viewport.getBoundingClientRect();
    view.k = Math.max(0.3, Math.min(1.05, r.width / (x1 - x0), r.height / (y1 - y0)));
    view.x = (r.width - (x1 - x0) * view.k) / 2 - x0 * view.k;
    view.y = (r.height - (y1 - y0) * view.k) / 2 - y0 * view.k;
    world.classList.add('glide');
    applyView();
    setTimeout(() => world.classList.remove('glide'), 350);
  }

  viewport.addEventListener('wheel', ev => {
    ev.preventDefault();
    const r = viewport.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    const k = Math.min(2, Math.max(0.2, view.k * Math.exp(-ev.deltaY * 0.0015)));
    view.x = mx - (mx - view.x) * (k / view.k);
    view.y = my - (my - view.y) * (k / view.k);
    view.k = k;
    applyView();
  }, {passive: false});

  let drag = null;   // {mode: 'pan'|'card', ...}
  viewport.addEventListener('pointerdown', ev => {
    if (ev.button !== 0) return;
    const head = ev.target.closest('.sx-head');
    const card = ev.target.closest('.sx-card');
    if (head) {
      const name = card.dataset.table;
      drag = {mode: 'card', name, sx: ev.clientX, sy: ev.clientY, ox: pos[name].x, oy: pos[name].y, moved: false};
    } else if (card) {
      drag = {mode: 'click', name: card.dataset.table, sx: ev.clientX, sy: ev.clientY};
    } else {
      drag = {mode: 'pan', sx: ev.clientX, sy: ev.clientY, ox: view.x, oy: view.y, moved: false};
      viewport.classList.add('panning');
    }
    viewport.setPointerCapture(ev.pointerId);
  });
  let frame = 0;
  viewport.addEventListener('pointermove', ev => {
    if (!drag) return;
    const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    if (drag.mode === 'pan') { view.x = drag.ox + dx; view.y = drag.oy + dy; applyView(); }
    if (drag.mode === 'card' && drag.moved) {
      const p = pos[drag.name];
      p.x = drag.ox + dx / view.k; p.y = drag.oy + dy / view.k;
      cardsEl.querySelector('.sx-card[data-table="' + CSS.escape(drag.name) + '"]').style.transform = 'translate(' + p.x + 'px,' + p.y + 'px)';
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; drawSections(); drawEdges(); });
    }
  });
  viewport.addEventListener('pointerup', ev => {
    if (!drag) return;
    const d = drag;
    drag = null;
    viewport.classList.remove('panning');
    if (d.mode === 'card' && d.moved) {
      moved[d.name] = {x: pos[d.name].x, y: pos[d.name].y};
      store.set('moved', moved);
    } else if (d.mode === 'card' || d.mode === 'click') {
      select(d.name);
    } else if (d.mode === 'pan' && !d.moved) {
      select(null);
    }
  });

  // Hovering a foreign-key row lights up the table it points at.
  cardsEl.addEventListener('mouseover', ev => {
    const row = ev.target.closest('.sx-col[data-fk]');
    cardsEl.querySelectorAll('.sx-card.target').forEach(el => el.classList.remove('target'));
    edgeLayer.querySelectorAll('.sx-edge.peek').forEach(el => el.classList.remove('peek'));
    if (!row) return;
    const from = row.closest('.sx-card').dataset.table;
    const target = cardsEl.querySelector('.sx-card[data-table="' + CSS.escape(row.dataset.fk) + '"]');
    if (target) target.classList.add('target');
    edgeLayer.querySelectorAll('.sx-edge[data-from="' + CSS.escape(from) + '"][data-col="' + CSS.escape(row.dataset.col) + '"]').forEach(el => el.classList.add('peek'));
  });

  // ------------------------------------------------------------------ side panel

  function chip(name) {
    const t = byName[name];
    if (!t) return esc(name);
    return '<button type="button" class="sx-chip' + (t.built ? '' : ' planned') + '" data-go="' + esc(name) + '" style="--h:' + sectionHue[t.section] + '">' + esc(name) + '</button>';
  }

  function renderOverview() {
    const built = data.tables.filter(t => t.built), planned = data.tables.filter(t => !t.built);
    const gaps = built.flatMap(t => t.columns.filter(c => c.status === 'spec_only').map(c => t.name + '.' + c.name));
    const additions = built.flatMap(t => t.columns.filter(c => c.status === 'poc_addition').map(c => t.name + '.' + c.name));
    panel.innerHTML =
      '<h2>Design decisions</h2><p class="small muted">The schema is where most of the design went. Click one to see the table it lives in.</p>' +
      '<ol class="sx-decisions">' + data.decisions.map(d => {
        const t = byName[d.table];
        return '<li class="' + (t.built ? '' : 'planned') + '"><button type="button" data-go="' + esc(d.table) + '"><b>' + esc(d.title) + '</b>' +
          '<span class="small muted">' + esc(d.table) + (t.built ? '' : ' · planned') + '</span></button></li>';
      }).join('') + '</ol>' +
      '<h3>Spec vs. database</h3><dl class="sx-kv small">' +
      '<dt>Built</dt><dd>' + built.length + ' tables, ' + built.reduce((n, t) => n + (t.rows || 0), 0) + ' rows of demo data</dd>' +
      '<dt>Designed, not built</dt><dd>' + planned.length + ' tables (turn on <i>Show planned</i>)</dd>' +
      '<dt>Spec columns not built</dt><dd>' + (gaps.length ? gaps.map(esc).join(', ') : 'none') + '</dd>' +
      '<dt>Built, not in the spec</dt><dd>' + (additions.length ? additions.map(esc).join(', ') : 'none') + '</dd></dl>';
  }

  function renderTable(name) {
    const t = byName[name];
    const out = data.edges.filter(e => e.from === name && e.to !== name);
    const inc = data.edges.filter(e => e.to === name && e.from !== name);
    const self = data.edges.filter(e => e.from === name && e.to === name);
    const decs = decisionsFor(name);
    const cols = t.columns.map(c => {
      const targets = (c.fks && c.fks.length ? c.fks : c.fk ? [c.fk] : []);
      return '<tr class="st-' + c.status + '"><td><code class="cn">' + esc(c.name) + '</code>' +
        (c.pk ? ' <span class="k pk">PK</span>' : '') + (targets.length ? ' <span class="k fk">FK</span> → ' + targets.map(chip).join(' ') : '') +
        (STATUS_LABEL[c.status] && c.status !== 'planned' ? '<div class="sx-status">' + esc(STATUS_LABEL[c.status]) + '</div>' : '') +
        (c.comment ? '<div class="sx-comment">' + esc(c.comment) + '</div>' : '') + '</td>' +
        '<td class="small"><code>' + esc(c.type) + '</code>' + (t.built ? (c.nullable ? '' : '<div class="muted">not null</div>') : '') +
        (c.default ? '<div class="muted">default ' + esc(c.default) + '</div>' : '') + '</td></tr>';
    }).join('');
    const constraints = [].concat(
      t.checks.map(c => '<li><span class="muted">check</span> <code>' + esc(c) + '</code></li>'),
      t.uniques.map(u => '<li><span class="muted">unique</span> <code>(' + esc(u.join(', ')) + ')</code></li>'),
      t.indexes.filter(i => i.unique || i.where).map(i => '<li><span class="muted">' + (i.unique ? 'unique index' : 'index') + '</span> <code>(' + esc(i.columns.join(', ')) + ')' + (i.where ? ' WHERE ' + esc(i.where) : '') + '</code></li>'),
      t.triggers.map(g => '<li><span class="muted">trigger</span> <code>' + esc(g) + '</code></li>'));
    panel.innerHTML =
      '<button type="button" class="btn small link sx-back">← All decisions</button>' +
      '<h2 class="sx-title" style="--h:' + sectionHue[t.section] + '"><code>' + esc(t.name) + '</code></h2>' +
      '<div class="small muted">' + esc(t.section) + ' · ' + (t.built ? 'built · ' + t.rows + ' row' + (t.rows === 1 ? '' : 's') + ' now' : '<b>designed in CLAUDE.md, not built yet</b>') + '</div>' +
      decs.map(d => '<div class="sx-why"><b>' + esc(d.title) + '</b><p>' + esc(d.text) + '</p><div class="small muted">' + esc(d.ref) + '</div></div>').join('') +
      (t.notes.length ? '<div class="sx-notes"><div class="small muted">From the spec</div>' + t.notes.map(n => '<p>' + esc(n) + '</p>').join('') + '</div>' : '') +
      '<h3>Columns</h3><table class="sx-coltable">' + cols + '</table>' +
      (constraints.length ? '<h3>Enforced in the database</h3><ul class="sx-list small">' + constraints.join('') + '</ul>' : '') +
      '<h3>Relationships</h3>' +
      '<div class="small"><div class="muted">References</div>' + (out.length ? out.map(e => '<code>' + esc(e.cols.join(', ')) + '</code> → ' + chip(e.to)).join('<br>') : '—') +
      (self.length ? '<br><code>' + esc(self.map(e => e.cols.join(', ')).join('; ')) + '</code> → itself' : '') + '</div>' +
      '<div class="small" style="margin-top:8px"><div class="muted">Referenced by</div>' + (inc.length ? [...new Set(inc.map(e => e.from))].map(chip).join(' ') : '—') + '</div>';
    if (!t.built && !opts.planned) {
      panel.insertAdjacentHTML('afterbegin', '<div class="sx-flash small">This table is only designed. Turn on <i>Show planned</i> to see it on the diagram.</div>');
    }
  }

  panel.addEventListener('click', ev => {
    const go = ev.target.closest('[data-go]');
    if (go) {
      const name = go.dataset.go;
      if (!byName[name].built && !opts.planned) { $('sx-planned').checked = true; setPlanned(true); }
      select(name, true);
    }
    if (ev.target.closest('.sx-back')) select(null);
  });

  function select(name, center) {
    selected = name;
    if (name) renderTable(name); else renderOverview();
    paintState();
    if (name && center) centerOn(name);
  }

  // ------------------------------------------------------------------ controls

  function rebuild() {
    renderCards();
    layout();
    paintState();
  }
  function setPlanned(on) { opts.planned = on; store.set('planned', on); rebuild(); fit(); }
  $('sx-planned').addEventListener('change', ev => setPlanned(ev.target.checked));
  $('sx-compact').addEventListener('change', ev => { opts.compact = ev.target.checked; store.set('compact', opts.compact); moved = {}; store.set('moved', moved); rebuild(); });
  $('sx-fit').addEventListener('click', fit);
  $('sx-reset').addEventListener('click', () => { moved = {}; store.set('moved', moved); rebuild(); fit(); });
  $('sx-search').addEventListener('input', ev => { query = ev.target.value.trim().toLowerCase(); paintState(); });
  $('sx-search').addEventListener('search', ev => { query = ev.target.value.trim().toLowerCase(); paintState(); });
  $('sx-search').addEventListener('keydown', ev => {
    if (ev.key !== 'Enter') return;
    const hit = visible().find(t => t.name === query) || visible().find(matches);
    if (hit) select(hit.name, true);
  });
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Escape') { $('sx-search').value = ''; query = ''; select(null); }
  });
  window.addEventListener('resize', () => fit());

  rebuild();
  fit();
  renderOverview();
})();
