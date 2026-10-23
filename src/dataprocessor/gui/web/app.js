'use strict';

// ── helpers ───────────────────────────────────────────────────────────────────
const api = () => window.pywebview.api;
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (n) => (n == null || n === '' ? '' : Number(n).toLocaleString());
const pad = (n) => String(n).padStart(2, '0');
const hms = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`; };
const isAbs = (p) => /^(\/|~|[A-Za-z]:[\\/]|\\\\)/.test(p || '');
const sep = (p) => ((p || '').includes('\\') && !(p || '').includes('/') ? '\\' : '/');
const dirname = (p) => { p = (p || '').replace(/[\\/]+$/, ''); const i = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\')); return i > 0 ? p.slice(0, i) : (i === 0 ? '/' : ''); };
const join = (a, b) => (a ? a.replace(/[\\/]+$/, '') + sep(a) + b : b);
const unq = (s) => String(s ?? '').trim().replace(/^['"]|['"]$/g, '');
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const NTEE = [
  ['1', '1 · Arts, Culture & Humanities'], ['2', '2 · Education'], ['3', '3 · Environment & Animals'],
  ['4', '4 · Health'], ['5', '5 · Human Services'], ['6', '6 · International, Foreign Affairs'],
  ['7', '7 · Public, Societal Benefit'], ['8', '8 · Religion Related'], ['9', '9 · Mutual/Membership Benefit'],
  ['10', '10 · Unknown, Unclassified'],
];

const llmCell = (v) => (v === 'OK' ? ['ok', 't-ok'] : v === 'ERR' ? ['ERR', 't-err t-bold'] : [v || '—', 't-dark']);
const secsCell = (s) => [s == null ? '' : `${Number(s).toFixed(1)}s`, 't-dim'];

// ── tool definitions ──────────────────────────────────────────────────────────
// fields → the setup table; cols/row → the live results table.
// Spider and Wayback both produce manifests: one results row per org.
const MANIFEST_COLS = [{ h: 'ORG', w: '1fr' }, { h: 'DOMAIN', w: 'minmax(14ch, 28ch)' }, { h: 'DOCS', w: '6ch', r: 1 }, { h: 'YEARS', w: '11ch' }, { h: 'FORMATS', w: 'minmax(10ch, 24ch)' }];
const CRAWL_ACTIVE_COLS = [{ h: 'ROW', w: '7ch', r: 1 }, { h: 'S', w: '1ch' }, { h: 'LANE', w: '9ch' }, { h: 'ORG', w: '1fr' }];
const manifestRow = (r) => [[r.org, 't-br'], [r.domain, 't-dim'], [r.docs, 't-ok t-bold'], [r.years, 't-cyan'], [r.formats, 't-mag']];

const TOOLS = {
  filter: {
    tab: 'Filter',
    title: 'Filter data',
    blurb: 'Classify each URL as an annual / impact / presidential report (1) or not (0).',
    unit: 'rows',
    fields: [
      { key: 'input', label: 'Input CSV', type: 'csv', required: true, hint: 'Usually a spider / wayback manifest' },
      { key: 'output', label: 'Output CSV', type: 'save', required: true, def: 'filtered.csv', hint: 'Keeps org, source, year, url, domain + is_annual_report' },
      { key: 'mode', label: 'Method', type: 'select', def: 'llm', options: [['llm', 'DeepSeek LLM (reads PDF text, 100 at a time)'], ['pattern', 'Pattern match (no API, 10 at a time)']] },
      { key: 'column', label: 'URL column', type: 'column', def: 'url', guess: /url/i },
      { key: 'start_row', label: 'Start row', type: 'int', def: '0' },
      { key: 'end_row', label: 'End row', type: 'int', auto: 'rows', hint: 'Blank = end of file' },
    ],
    cols: [{ h: 'ROW', w: '7ch', r: 1 }, { h: 'RESULT', w: '8ch' }, { h: 'TIME', w: '7ch', r: 1 }, { h: 'ORG', w: 'minmax(12ch, 28ch)' }, { h: 'URL', w: '1fr' }],
    row: (r) => [
      [r.row, 't-dim'],
      r.result === 'annual' ? ['REPORT', 't-ok t-bold'] : r.result === 'not' ? ['not', 't-dim'] : ['ERROR', 't-err t-bold'],
      secsCell(r.secs),
      [r.org, 't-br'],
      [r.url, ''],
    ],
  },

  analysis: {
    tab: 'Analysis',
    title: 'LLM Analysis',
    blurb: 'Extract each PDF, then score it with DeepSeek, Gemini (batch) and GPT (batch) side by side.',
    unit: 'rows',
    keys: ['DeepSeek_key', 'Gemini_key', 'GPT_key'],
    fields: [
      { key: 'input', label: 'Input CSV', type: 'csv', required: true, hint: 'org, ein, domain, year are carried over' },
      { key: 'output', label: 'Output CSV', type: 'save', required: true, def: 'analysis.csv', hint: 'A folder → <input>_analysis.csv · appends' },
      { key: 'column', label: 'URL column', type: 'column', def: 'url', guess: /url/i },
      { key: 'batch_size', label: 'Batch size', type: 'int', def: '10', hint: 'Rows sent to the models per batch' },
      { key: 'start_row', label: 'Start row', type: 'int', def: '0' },
      { key: 'end_row', label: 'End row', type: 'int', auto: 'rows', hint: 'Blank = end of file' },
    ],
    cols: [{ h: 'ROW', w: '7ch', r: 1 }, { h: 'DS', w: '3ch' }, { h: 'GM', w: '3ch' }, { h: 'GPT', w: '3ch' },
      { h: 'ORG', w: 'minmax(12ch, 26ch)' }, { h: 'YEAR', w: '5ch' }, { h: 'PRIMARY ACTIVITY', w: 'minmax(14ch, 30ch)' },
      { h: 'INTENSITY ds/gm/gpt', w: '20ch' }, { h: 'URL', w: '1fr' }],
    activeCols: [{ h: 'BATCH', w: '7ch', r: 1 }, { h: 'S', w: '1ch' }, { h: 'MODEL', w: '9ch' }, { h: 'STATUS', w: '1fr' }],
    row: (r) => [[r.row, 't-dim'], llmCell(r.ds), llmCell(r.gm), llmCell(r.gpt),
      [r.org, 't-br'], [r.year, 't-cyan'], r.no_text ? ['no text extracted — skipped', 't-warn'] : [r.activity, ''], [r.intensity, 't-mag'], [r.url, 't-dim']],
  },

  propublica: {
    tab: 'ProPublica',
    title: 'ProPublica API – Org Finder',
    blurb: 'Search ProPublica by NTEE category and state; keep orgs ruled in or before 2000.',
    unit: 'states',
    fields: [
      { key: 'output', label: 'Output CSV', type: 'save', required: true, def: '', hint: 'New or existing CSV (appends)', ph: 'choose where to save…' },
      { key: 'ntee', label: 'NTEE category', type: 'select', def: '1', options: NTEE },
      { key: 'num_pages', label: 'Pages per state', type: 'int', def: '500', hint: 'Stops early when a state runs out' },
      { key: 'start_state', label: 'From state', type: 'state', def: '0' },
      { key: 'end_state', label: 'To state', type: 'state', def: '56', hint: 'Inclusive' },
    ],
    cols: [{ h: 'NAME', w: '1fr' }, { h: 'EIN', w: '10ch' }, { h: 'NTEE', w: '5ch' }, { h: 'RULING', w: '7ch' },
      { h: 'CITY', w: '16ch' }, { h: 'ST', w: '3ch' }, { h: 'REVENUE', w: '14ch', r: 1 }, { h: 'ASSETS', w: '14ch', r: 1 }],
    row: (r) => [[r.name, 't-br'], [r.ein, 't-dim'], [r.ntee_code, 't-mag'], [r.ruling_date, 't-cyan'], [r.city, ''], [r.state, ''],
      [num(r.revenue), 't-ok'], [num(r.asset_amount), '']],
  },

  domain: {
    tab: 'Domains',
    title: 'ProPublica – Domain Finder',
    blurb: 'Read each org’s Form 990 filings and pull out its website domain.',
    unit: 'orgs',
    fields: [
      { key: 'input', label: 'Input CSV', type: 'csv', required: true, hint: 'CSV containing EINs' },
      { key: 'output', label: 'Output CSV', type: 'save', required: true, def: 'domains.csv', hint: 'Appends if the file exists' },
      { key: 'column', label: 'EIN column', type: 'column', def: 'ein', guess: /ein/i },
      { key: 'start_row', label: 'Start row', type: 'int', def: '0' },
      { key: 'end_row', label: 'End row', type: 'int', auto: 'rows', hint: 'Blank = end of file' },
      { key: 'max_filings', label: 'Filings per org', type: 'int', def: '3', hint: 'Form 990s tried before giving up' },
    ],
    cols: [{ h: 'ROW', w: '7ch', r: 1 }, { h: 'EIN', w: '11ch' }, { h: 'DOMAIN', w: 'minmax(18ch, 32ch)' }, { h: 'TIME', w: '7ch', r: 1 }, { h: 'NAME', w: '1fr' }],
    row: (r) => [[r.row, 't-dim'], [r.ein, 't-mag'], r.domain ? [r.domain, 't-ok t-bold'] : ['— none on file', 't-dark'], secsCell(r.secs), [r.name, '']],
  },

  spider: {
    tab: 'Spider',
    title: 'Spider – live site crawler',
    blurb: 'Crawl each org’s live website for report documents and pages → manifest_live.csv.',
    unit: 'orgs',
    fields: [
      { key: 'input', label: 'Org list CSV', type: 'csv', required: true, hint: 'Columns: name, domain, ein' },
      { key: 'out_dir', label: 'Output folder', type: 'folder', def: '', ph: '<csv folder>/reports', hint: 'manifest_live.csv is written here' },
      { key: 'depth', label: 'Crawl depth', type: 'int', def: '3', hint: 'How many links deep to follow (0–10)' },
      { key: 'start_row', label: 'Start row', type: 'int', def: '0' },
      { key: 'end_row', label: 'End row', type: 'int', auto: 'rows', hint: 'Blank = end of file' },
    ],
    cols: MANIFEST_COLS,
    activeCols: CRAWL_ACTIVE_COLS,
    row: manifestRow,
  },

  wayback: {
    tab: 'Wayback',
    title: 'Wayback – archive sweep',
    blurb: 'Find reports the Internet Archive kept that the live sites no longer serve → manifest_wayback.csv.',
    unit: 'orgs',
    fields: [
      { key: 'input', label: 'Org list CSV', type: 'csv', required: true, hint: 'Columns: name, domain, ein' },
      { key: 'out_dir', label: 'Output folder', type: 'folder', def: '', ph: '<csv folder>/reports', hint: 'manifest_wayback.csv is written here' },
      { key: 'years', label: 'Years of lookback', type: 'int', def: '0', hint: '0 = whole archive, 1996–now' },
      { key: 'rpm', label: 'Archive rate', type: 'select', def: '50', options: () => (INFO.rpm_choices || []).map(([v, l]) => [v, `${l} req/min`]) },
      { key: 'start_row', label: 'Start row', type: 'int', def: '0' },
      { key: 'end_row', label: 'End row', type: 'int', auto: 'rows', hint: 'Blank = end of file' },
      { key: 'download', label: 'Mode', type: 'select', def: 'no', options: [['no', 'Record URLs only (fast)'], ['yes', 'Download the files too']] },
    ],
    cols: MANIFEST_COLS,
    activeCols: CRAWL_ACTIVE_COLS,
    row: manifestRow,
  },

  links: {
    tab: 'Links',
    title: 'Link Checker',
    blurb: 'Check every live / wayback URL in a manifest and flag the dead ones (check_dead).',
    unit: 'links',
    fields: [
      { key: 'input', label: 'Input CSV', type: 'csv', required: true, hint: "Needs a URL column and a 'source' column" },
      { key: 'output', label: 'Output CSV', type: 'save', required: true, def: 'links_checked.csv', hint: 'A folder → <input>_links.csv · appends' },
      { key: 'column', label: 'URL column', type: 'column', def: 'url', guess: /url/i },
      { key: 'batch_size', label: 'Batch size', type: 'int', def: '5', hint: 'Links checked in parallel' },
      { key: 'start_row', label: 'Start row', type: 'int', def: '0' },
      { key: 'end_row', label: 'End row', type: 'int', auto: 'rows', hint: 'Blank = end of file' },
    ],
    cols: [{ h: 'ROW', w: '7ch', r: 1 }, { h: 'STATUS', w: '7ch' }, { h: 'SOURCE', w: '8ch' }, { h: 'TIME', w: '7ch', r: 1 }, { h: 'ORG', w: 'minmax(12ch, 28ch)' }, { h: 'URL', w: '1fr' }],
    row: (r) => [[r.row, 't-dim'],
      r.status === 'dead' ? ['DEAD', 't-err t-bold'] : r.status === 'live' ? ['live', 't-ok'] : ['?', 't-warn t-bold'],
      [r.source, r.source === 'wayback' ? 't-blue' : 't-cyan'], secsCell(r.secs), [r.org, 't-br'], [r.url, '']],
  },

  summary: {
    tab: 'Summary',
    title: 'Summary – org × year matrix',
    blurb: 'One row per org, 1/0 per year it has a report, with years covered and longest consecutive streak.',
    unit: 'orgs',
    fields: [
      { key: 'input', label: 'Input CSV', type: 'csv', required: true, hint: "Dated reports, needs 'org', 'domain' and 'year' columns" },
      { key: 'output', label: 'Output CSV', type: 'save', def: '', ph: '<input>_matrix.csv', hint: 'Blank = <input>_matrix.csv · also writes <name>_stats.csv · overwrites' },
    ],
    cols: [{ h: 'NAME', w: '1fr' }, { h: 'DOMAIN', w: 'minmax(14ch, 28ch)' }, { h: 'YEARS', w: '7ch', r: 1 }, { h: 'STREAK', w: '8ch', r: 1 }, { h: 'SPAN', w: '11ch' }],
    row: (r) => r.total
      ? [['Yrly Sum · unique years', 't-warn t-bold'], ['', ''], [r.years, 't-warn t-bold'], [r.streak, 't-warn t-bold'], [r.span, 't-cyan']]
      : [[r.name, 't-br'], [r.domain, 't-dim'], [r.years, 't-ok t-bold'], [r.streak, 't-mag'], [r.span, 't-cyan']],
  },
};

// Columns a tool can't run without, checked as soon as the input CSV is picked.
const REQUIRED_COLS = { spider: ['domain'], wayback: ['domain'], links: ['source'], summary: ['org', 'domain'] };

const TABS = [['main', 'Main'], ...Object.entries(TOOLS).map(([k, t]) => [k, t.tab]), ['settings', 'Setup']];
const KEY_LABEL = { DeepSeek_key: 'DS', GPT_key: 'GPT', Gemini_key: 'GEM' };

// ── state ─────────────────────────────────────────────────────────────────────
let INFO = { keys: {}, states: [], version: '' };
let STATE = { forms: {}, tab: 'main', log: true };
const S = {
  meta: {},          // tool → inspected CSV / folder info
  data: {},          // tool → { rows, summary, error, active }
  sel: {},           // tool → selected row index, or null = follow newest
  job: null,         // { id, tool, rowCursor, logCursor, cancellable, last, t0 }
  keyTest: {},       // key → true/false after "test connections"
  quitArmed: 0,
  tick: 0,
};
const form = (tool) => (STATE.forms[tool] ||= {});
const data = (tool) => (S.data[tool] ||= { rows: [], summary: null, error: null, active: [] });
const saveState = debounce(() => api().save_state(STATE), 400);

function val(tool, f) {
  const v = form(tool)[f.key];
  return v == null ? (f.def ?? '') : v;
}

function msg(text, cls = 'info', ms = 6000) {
  const el = $('#msg');
  el.textContent = text || '';
  el.className = cls;
  clearTimeout(msg._t);
  if (text && ms) msg._t = setTimeout(() => { el.textContent = ''; }, ms);
}

// ── tabs ──────────────────────────────────────────────────────────────────────
function renderTabs() {
  $('#tabs').innerHTML = TABS.map(([k, label], i) => {
    const busy = S.job && S.job.tool === k && S.job.last?.state === 'running' ? '<span class="busy"> ●</span>' : '';
    return `<button class="tab${STATE.tab === k ? ' on' : ''}" data-tab="${k}" title="Ctrl+${i + 1}">${esc(label)}${busy}</button>`;
  }).join('');
}

function setTab(tab) {
  if (!TABS.some(([k]) => k === tab)) tab = 'main';
  STATE.tab = tab;
  document.body.classList.toggle('home', tab === 'main');
  saveState();
  renderTabs();
  renderView();
}

// ── main / overview page ──────────────────────────────────────────────────────
function renderMain() {
  const rows = Object.entries(TOOLS).map(([k, t], i) => `
    <div class="trow" data-goto="${k}" style="grid-template-columns: 4ch 26ch 1fr">
      <span class="t-cyan">^${i + 2}</span><span class="t-br">${esc(t.title)}</span><span class="t-dim">${esc(t.blurb)}</span>
    </div>`).join('');
  const rates = [['DeepSeek', '0.028 USD', '0.42 USD'], ['GPT 4.1', '3 USD', '0.75 USD'], ['Gemini', '1.25 USD', '12 USD']]
    .map(([a, b, c]) => `<div class="trow" style="grid-template-columns: 14ch 18ch 18ch"><span class="t-br">${a}</span><span>${b}</span><span>${c}</span></div>`).join('');
  $('#view').innerHTML = `
    <div class="page">
      <div class="banner"> ████   █████  ██████  █████
 ██  ██ ██  ██   ██   ██  ██
 ██  ██ █████    ██   █████
 ████   ██  ██   ██   ██  ██
<span class="b2"> ████  ████   ████   ████  ████  █████  █████  ████  ████
 ██  █ ██  █ ██  ██ ██    ██    ██     ██     ██  ██ ██  █
 ████  ████  ██  ██ ██    ████   ███    ███  ██  ██ ████
 ██    ██  █  ████   ████  ████ █████  █████  ████  ██  █</span></div>
      <p>version ${esc(INFO.version)} · click a tool or press its shortcut · F1 for help</p>
      <h2>Tools</h2>
      <div class="thead" style="grid-template-columns: 4ch 26ch 1fr"><span>KEY</span><span>TOOL</span><span>WHAT IT DOES</span></div>
      ${rows}
      <h2>LLM token rates</h2>
      <div class="thead" style="grid-template-columns: 14ch 18ch 18ch"><span>LLM</span><span>INPUT / 1M</span><span>OUTPUT / 1M</span></div>
      ${rates}
      <h2>API keys</h2>
      <div class="keymeters">${Object.entries(KEY_LABEL).map(([k, l]) => meterHTML(k, l)).join('')}</div>
      <p>Add or change keys on the Setup tab · F5 there tests each connection</p>
    </div>`;
  updateMeters();
}

// ── settings page ─────────────────────────────────────────────────────────────
async function renderSettings() {
  const st = await api().get_settings();
  if (STATE.tab !== 'settings') return;
  const grid = 'grid-template-columns: 14ch 16ch 24ch minmax(0,1fr) 8ch';
  const status = (k) => {
    if (S.keyTest[k] === true) return ['online', 't-ok t-bold'];
    if (S.keyTest[k] === false) return ['offline', 't-err t-bold'];
    if (S.keyTest[k] === 'testing') return ['testing…', 't-warn'];
    return INFO.keys[k] ? ['set · untested', 't-cyan'] : ['missing', 't-err'];
  };
  $('#view').innerHTML = `
    <div class="page setup">
      <h2>API keys</h2>
      <p>Stored in <span class="t-br">${esc(st.env_file)}</span> · shared with the terminal tool</p>
      <div class="thead" style="${grid}"><span>KEY</span><span>STATUS</span><span>STORED</span><span>NEW VALUE</span><span></span></div>
      ${st.keys.map((k) => { const [s, c] = status(k.name); return `
        <div class="trow" style="${grid}">
          <span class="pname">${esc(k.name)}</span>
          <span class="${c}" id="ks-${k.name}">${s}</span>
          <span class="t-dim">${esc(k.masked || '—')}</span>
          <span class="cell"><input class="fi" type="password" data-key="${k.name}" placeholder="paste a new key, Enter to save" spellcheck="false" autocomplete="off"></span>
          <button class="ibtn" data-savekey="${k.name}">[save]</button>
        </div>`; }).join('')}
      <div class="actions">
        <button class="kbtn" data-act="test"><span class="fk">F5</span><span class="fl">Test connections</span></button>
        <span class="status">Sends one tiny request to each provider</span>
      </div>
    </div>`;
}

async function saveKey(name) {
  const input = $(`input[data-key="${name}"]`);
  const res = await api().save_key(name, input.value);
  if (!res.ok) return msg(res.error, 'err');
  INFO.keys[name] = true;
  delete S.keyTest[name];
  msg(`${name} saved`, 'ok');
  renderSettings();
  updateMeters();
}

async function testConnections() {
  Object.keys(KEY_LABEL).forEach((k) => { S.keyTest[k] = 'testing'; });
  if (STATE.tab === 'settings') renderSettings();
  updateMeters();
  msg('Testing connections…', 'info', 0);
  const res = await api().test_connections();
  Object.assign(S.keyTest, res);
  const bad = Object.entries(res).filter(([, ok]) => !ok).map(([k]) => k);
  msg(bad.length ? `Offline: ${bad.join(', ')}` : 'All providers online', bad.length ? 'err' : 'ok');
  if (STATE.tab === 'settings') renderSettings();
  updateMeters();
}

// ── tool page: setup table ────────────────────────────────────────────────────
function fieldCell(tool, f) {
  const v = val(tool, f);
  const meta = S.meta[tool];
  const input = (type = 'text', extra = '') =>
    `<input class="fi" type="${type}" data-k="${f.key}" value="${esc(v)}" placeholder="${esc(f.ph || '')}" spellcheck="false" autocomplete="off" ${extra}>`;
  switch (f.type) {
    case 'csv': case 'save': case 'folder':
      return input() + `<button class="ibtn" data-browse="${f.key}" title="F3">[browse]</button>`;
    case 'int': {
      const auto = f.auto === 'rows' ? meta?.rows : null;
      return `<input class="fi" type="text" inputmode="numeric" data-k="${f.key}" value="${esc(v)}" placeholder="${auto != null ? esc(auto) : ''}" spellcheck="false">`;
    }
    case 'select': {
      const options = typeof f.options === 'function' ? f.options() : f.options;
      return `<select data-k="${f.key}">${options.map(([o, l]) => `<option value="${esc(o)}"${String(v) === o ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
    }
    case 'state':
      return `<select data-k="${f.key}">${INFO.states.map((s, i) => `<option value="${i}"${String(v) === String(i) ? ' selected' : ''}>${s}  (${i})</option>`).join('')}</select>`;
    case 'column': {
      if (!meta?.columns?.length) return input();
      const cols = meta.columns.includes(v) || !v ? meta.columns : [v, ...meta.columns];
      return `<select data-k="${f.key}">${cols.map((c) => `<option value="${esc(c)}"${c === v ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>`;
    }
    default:
      return input();
  }
}

function fieldHint(tool, f) {
  const meta = S.meta[tool];
  const v = val(tool, f);
  if (f === TOOLS[tool].fields[0] && f.type === 'csv') {
    if (meta?.error) return [meta.error, 't-err'];
    if (meta?.rows != null) {
      const need = REQUIRED_COLS[tool] || [];
      const missing = need.filter((c) => !meta.columns.includes(c));
      const base = `${num(meta.rows)} rows · ${meta.columns.length} columns`;
      if (missing.length) return [`${base} · missing '${missing.join("', '")}' column`, 't-err'];
      return [need.length ? `${base} · ${need.join(', ')} ✓` : base, 't-ok'];
    }
  }
  if (f.type === 'column' && meta?.columns && v && !meta.columns.includes(v)) return [`'${v}' is not in the file`, 't-err'];
  if (f.key === 'output' || f.key === 'out_dir') {
    const out = resolveOutput(tool);
    if (out && out !== unq(v)) return [`→ ${out}`, 't-dim'];
  }
  if (f.auto && meta?.rows != null) return [`of ${num(meta.rows)} · blank = all`, 't-dim'];
  return [f.hint || '', 't-dim'];
}

function renderSetup(tool) {
  const t = TOOLS[tool];
  return `
    <div class="setup">
      <div class="thead"><span>PARAMETER</span><span>VALUE</span><span>INFO</span></div>
      ${t.fields.map((f) => {
        const [h, c] = fieldHint(tool, f);
        return `<div class="trow" data-field="${f.key}">
          <span class="pname">${esc(f.label)}${f.required ? '<span class="req">*</span>' : ''}</span>
          <span class="cell" id="cell-${f.key}">${fieldCell(tool, f)}</span>
          <span class="hint ${c}" id="hint-${f.key}" title="${esc(h)}">${esc(h)}</span>
        </div>`;
      }).join('')}
    </div>`;
}

// Refresh hints / column dropdowns / placeholders without touching the input being typed in.
function refreshDynamic(tool) {
  if (STATE.tab !== tool) return;
  for (const f of TOOLS[tool].fields) {
    const hint = $(`#hint-${f.key}`);
    if (hint) { const [h, c] = fieldHint(tool, f); hint.textContent = h; hint.title = h; hint.className = `hint ${c}`; }
    if (f.type === 'column' || f.type === 'int') {
      const cell = $(`#cell-${f.key}`);
      if (cell && !cell.contains(document.activeElement)) cell.innerHTML = fieldCell(tool, f);
    }
  }
}

const inspect = debounce(async (tool) => {
  const f = TOOLS[tool].fields[0];
  if (f.type !== 'csv') return;
  const path = unq(val(tool, f));
  if (!path) { delete S.meta[tool]; return refreshDynamic(tool); }
  const res = await api().inspect_csv(tool, path);
  S.meta[tool] = res.ok ? res : { error: res.error };
  // Pick a sensible column when the saved one isn't in this file.
  const colField = TOOLS[tool].fields.find((x) => x.type === 'column');
  if (res.ok && colField && res.columns && !res.columns.includes(val(tool, colField))) {
    const guess = res.columns.find((c) => c === colField.def) || res.columns.find((c) => colField.guess?.test(c));
    if (guess) form(tool)[colField.key] = guess;
  }
  refreshDynamic(tool);
}, 250);

// ── tool page: results table ──────────────────────────────────────────────────
const gridOf = (cols) => `grid-template-columns: ${cols.map((c) => c.w).join(' ')}`;

function rowHTML(tool, r, i) {
  const t = TOOLS[tool];
  const cells = t.row(r).map(([text, cls], j) => `<span class="${cls}${t.cols[j].r ? ' right' : ''}">${esc(text)}</span>`).join('');
  return `<div class="trow" data-i="${i}" style="${gridOf(t.cols)}">${cells}</div>`;
}

function activeHTML(tool) {
  const t = TOOLS[tool];
  const act = data(tool).active || [];
  if (!t.activeCols || !act.length) return '';
  return `<div class="active-rows">${act.map((a) => `
    <div class="trow" style="${gridOf(t.activeCols)}"><span class="right">${esc(a.row ?? '')}</span><span class="t-bold">R</span><span class="t-cyan">${esc(a.lane)}</span><span>${esc(a.name)}</span></div>`).join('')}</div>`;
}

function summaryHTML(tool) {
  const d = data(tool);
  if (d.error) {
    return `<div class="summary error"><div class="stitle">Run failed</div><div class="kv"><span class="k">Error</span><span class="v">${esc(d.error)}</span></div>
      <div class="sbtns"><span class="t-dim">Nothing below this point was written. See the log for details.</span></div></div>`;
  }
  const s = d.summary;
  if (!s) return '';
  return `<div class="summary${s.stopped ? ' stopped' : ''}">
    <div class="stitle">${esc(s.title)}</div>
    <div class="kv">${s.items.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v" title="${esc(v)}">${esc(v)}</span>`).join('')}</div>
    ${s.output ? `<div class="sbtns">
      <button class="kbtn" data-act="reveal"><span class="fk">F7</span><span class="fl">Show in folder</span></button>
      ${/\.csv$/i.test(s.output) ? '<button class="kbtn" data-act="preview"><span class="fk">F8</span><span class="fl">Preview output</span></button>' : ''}
    </div>` : ''}
  </div>`;
}

function emptyText(tool) {
  const running = S.job?.tool === tool && S.job.last?.state === 'running';
  if (running) return 'Waiting for the first result…';
  return 'No results yet. Fill in the parameters above and press F5 (or click Run).\nEach processed row appears here as soon as it finishes.';
}

function renderTool(tool) {
  const t = TOOLS[tool];
  const d = data(tool);
  const running = S.job?.last?.state === 'running';
  const mine = running && S.job.tool === tool;
  $('#view').innerHTML = `
    <div class="tool">
      ${renderSetup(tool)}
      <div class="actions">
        <button class="kbtn" data-act="run" ${running ? 'disabled' : ''}><span class="fk">F5</span><span class="fl">Run</span></button>
        <button class="kbtn" data-act="stop" ${mine && S.job.cancellable ? '' : 'disabled'}><span class="fk">F6</span><span class="fl">Stop</span></button>
        <span class="status" id="tool-status">${esc(t.blurb)}</span>
      </div>
      <div id="summary">${summaryHTML(tool)}</div>
      <div class="results">
        <div class="thead" style="${gridOf(t.cols)}">${t.cols.map((c) => `<span class="${c.r ? 'right' : ''}">${c.h}</span>`).join('')}</div>
        <div id="active">${activeHTML(tool)}</div>
        <div class="tbody" id="tbody">${d.rows.length ? d.rows.slice(-3000).map((r, k) => rowHTML(tool, r, d.rows.length - Math.min(3000, d.rows.length) + k)).join('') : `<div class="empty">${emptyText(tool)}</div>`}</div>
      </div>
    </div>`;
  updateToolStatus();
  markSelection(tool, true);
}

function appendRows(tool, rows, startIndex) {
  const body = $('#tbody');
  if (!body || STATE.tab !== tool) return;
  $('.empty', body)?.remove();
  body.insertAdjacentHTML('beforeend', rows.map((r, k) => rowHTML(tool, r, startIndex + k)).join(''));
  while (body.children.length > 3000) body.firstElementChild.remove();
  markSelection(tool, true);
}

function markSelection(tool, scroll) {
  const body = $('#tbody');
  if (!body) return;
  const n = data(tool).rows.length;
  const sel = S.sel[tool] ?? n - 1;
  $('.trow.sel', body)?.classList.remove('sel');
  const el = $(`.trow[data-i="${sel}"]`, body);
  if (!el) return;
  el.classList.add('sel');
  if (scroll) el.scrollIntoView({ block: 'nearest' });
}

function moveSelection(delta) {
  const tool = STATE.tab;
  if (!TOOLS[tool]) return;
  const n = data(tool).rows.length;
  if (!n) return;
  let i = (S.sel[tool] ?? n - 1) + delta;
  i = Math.max(0, Math.min(n - 1, i));
  S.sel[tool] = i === n - 1 ? null : i;   // landing on the newest row resumes follow mode
  markSelection(tool, true);
}

function updateToolStatus() {
  const el = $('#tool-status');
  const tool = STATE.tab;
  if (!el || !TOOLS[tool]) return;
  const j = S.job?.last;
  if (j && S.job.tool === tool && j.state === 'running') {
    el.textContent = j.phase || 'Running…';
    el.className = 'status t-cyan';
  } else if (j && S.job.tool !== tool && j.state === 'running') {
    el.textContent = `${TOOLS[S.job.tool].title} is running — one job at a time`;
    el.className = 'status t-warn';
  } else {
    el.textContent = TOOLS[tool].blurb;
    el.className = 'status';
  }
}

// ── view dispatch ─────────────────────────────────────────────────────────────
function renderView() {
  if (STATE.tab === 'main') renderMain();
  else if (STATE.tab === 'settings') renderSettings();
  else { renderTool(STATE.tab); if (!S.meta[STATE.tab]) inspect(STATE.tab); }
}

// ── outputs ───────────────────────────────────────────────────────────────────
function resolveOutput(tool) {
  const get = (k) => unq(val(tool, TOOLS[tool].fields.find((f) => f.key === k) || { key: k }));
  if (tool === 'spider' || tool === 'wayback') {
    const out = get('out_dir');
    return out || (get('input') ? join(dirname(get('input')), 'reports') : '');
  }
  const out = get('output');
  if (!out) return '';
  if (tool === 'propublica' || isAbs(out)) return out;
  // Same rule as the CLI: a relative output lands next to the input file.
  const base = dirname(get('input'));
  return base ? join(base, out) : out;
}

function currentOutput() {
  const tool = STATE.tab;
  if (!TOOLS[tool]) return '';
  return data(tool).summary?.output || resolveOutput(tool);
}

async function reveal() {
  const out = currentOutput();
  if (!out) return msg('No output path yet', 'err');
  if (!(await api().reveal(out))) msg(`Not found yet: ${out}`, 'err');
}

async function preview(path) {
  path = path || currentOutput();
  if (!path || !/\.csv$/i.test(path)) return msg('Nothing to preview: no output CSV for this tool yet', 'err');
  msg('Loading preview…', 'info', 0);
  const res = await api().preview_csv(path, 300);
  msg('');
  if (!res.ok) return msg(res.error, 'err');
  const cols = ['#', ...res.columns];
  const kb = res.size > 1048576 ? `${(res.size / 1048576).toFixed(1)} MB` : `${Math.ceil(res.size / 1024)} KB`;
  openOverlay(`
    <div class="ohead"><span>PREVIEW  ${esc(res.path)}</span><span>${num(res.total)} rows · ${res.columns.length} cols · ${kb}${res.total > res.rows.length ? ` · first ${res.rows.length} shown` : ''}</span></div>
    <div class="obody preview">
      <div class="thead">${cols.map((c) => `<span title="${esc(c)}">${esc(c)}</span>`).join('')}</div>
      ${res.rows.map((r, i) => `<div class="trow"><span class="rn">${i}</span>${r.map((c) => `<span title="${esc(c)}">${esc(c)}</span>`).join('')}</div>`).join('')}
    </div>
    <div class="ofoot">${fk('Esc', 'Close', 'close')}${fk('F7', 'Show in folder', 'reveal')}</div>`);
}

// ── overlay ───────────────────────────────────────────────────────────────────
function openOverlay(html) {
  $('#overlay-box').innerHTML = html;
  $('#overlay').hidden = false;
}
const closeOverlay = () => { $('#overlay').hidden = true; };
const overlayOpen = () => !$('#overlay').hidden;

function help() {
  const tool = TOOLS[STATE.tab];
  openOverlay(`
    <div class="ohead"><span>HELP${tool ? ` · ${esc(tool.title)}` : ''}</span><span>DataProcessor ${esc(INFO.version)}</span></div>
    <div class="obody help">
      ${tool ? `<h3>${esc(tool.title)}</h3><pre>${esc(tool.blurb)}

Fields marked <span class="t-err">*</span> are required. A relative output name is saved next to the input,
and output CSVs are appended to, never overwritten, so a stopped run can resume from
the row shown in the summary. Results stream into the table as each row finishes.</pre>` : ''}
      <h3>Keys</h3>
<pre><span class="k">F1</span>  this help                <span class="k">F6</span>  stop after the current batch
<span class="k">F2</span>  jump to the first field  <span class="k">F7</span>  show the output in Finder / Explorer
<span class="k">F3</span>  browse for the focused path field   <span class="k">F8</span>  preview the output CSV
<span class="k">F4</span>  show / hide the log       <span class="k">F9</span>  clear this tool's results table
<span class="k">F5</span>  run (Settings: test API connections)   <span class="k">F10</span> quit
<span class="k">Ctrl+1…9</span>  switch tabs     <span class="k">↑ ↓</span>  move the highlighted row     <span class="k">End</span>  follow newest row
<span class="k">Esc</span>  close this window</pre>
      <h3>Meters</h3>
<pre><span class="k">Prog</span>            progress of the running job (bounces when the total isn't known yet)</pre>
      <h3>Notes</h3>
<pre>The Main tab shows API key status: missing, set, or online/offline after F5 on the Setup tab.
One job runs at a time. ProPublica Org Finder, Spider and Wayback can't be stopped mid-run.
Close the window to abort them; rows already written stay in the file.
API keys live in ${esc(INFO.env_file)} and are shared with the terminal tool.</pre>
    </div>
    <div class="ofoot">${fk('Esc', 'Close', 'close')}</div>`);
}

// ── run / stop / poll ─────────────────────────────────────────────────────────
function collectParams(tool) {
  const p = {};
  for (const f of TOOLS[tool].fields) {
    p[f.key] = unq(val(tool, f));
  }
  if (tool === 'propublica') p.end_state = String(Number(p.end_state) + 1);   // UI is inclusive, logic is exclusive
  return p;
}

function validate(tool, p) {
  for (const f of TOOLS[tool].fields) {
    if (f.required && !p[f.key]) return `${f.label} is required`;
    if (f.type === 'int' && p[f.key] !== '' && !/^\d+$/.test(p[f.key])) return `${f.label} must be a whole number`;
  }
  const meta = S.meta[tool];
  if (meta?.error) return meta.error;
  const missing = (TOOLS[tool].keys || []).filter((k) => !INFO.keys[k]);
  if (missing.length) return `Missing API key(s): ${missing.join(', ')} — add them on the Setup tab`;
  if (tool === 'filter' && p.mode === 'llm' && !INFO.keys.DeepSeek_key) return 'DeepSeek_key is missing — add it on the Setup tab, or use Pattern match';
  if (tool === 'propublica' && Number(p.end_state) <= Number(p.start_state)) return '"To state" must come after "From state"';
  return null;
}

async function run() {
  const tool = STATE.tab;
  if (tool === 'settings') return testConnections();
  if (!TOOLS[tool]) return msg('Pick a tool tab first', 'err');
  if (S.job?.last?.state === 'running') return msg(`${TOOLS[S.job.tool].title} is still running`, 'err');
  const p = collectParams(tool);
  const problem = validate(tool, p);
  if (problem) return msg(problem, 'err');

  const res = await api().start(tool, p);
  if (!res.ok) return msg(res.error, 'err');

  S.data[tool] = { rows: [], summary: null, error: null, active: [] };
  S.sel[tool] = null;
  S.job = { id: res.job_id, tool, rowCursor: 0, logCursor: 0, cancellable: res.cancellable, last: { state: 'running', total: 0, done: 0, elapsed: 0 }, t0: Date.now() };
  $('#logbody').innerHTML = '';
  if (STATE.log === false) toggleLog();
  msg(`Started ${TOOLS[tool].title}`, 'ok');
  renderTabs();
  renderTool(tool);
  poll();
}

async function stop() {
  if (!S.job || S.job.last?.state !== 'running') return msg('Nothing is running', 'err');
  if (!S.job.cancellable) return msg('This tool can’t be stopped mid-run. Close the window to abort it.', 'err');
  await api().stop(S.job.id);
  msg('Stopping after the current batch…', 'info');
}

async function poll() {
  const job = S.job;
  if (!job) return;
  let res;
  try {
    res = await api().poll(job.id, job.rowCursor, job.logCursor);
  } catch (e) {
    return setTimeout(poll, 1000);
  }
  if (!res || S.job !== job) return;
  const d = data(job.tool);

  if (res.rows.length) {
    const start = d.rows.length;
    d.rows.push(...res.rows);
    appendRows(job.tool, res.rows, start);
  }
  job.rowCursor = res.row_cursor;
  job.logCursor = res.log_cursor;
  appendLogs(res.logs);

  const activeChanged = JSON.stringify(d.active) !== JSON.stringify(res.active);
  d.active = res.active;
  job.last = res;
  job.t0 = Date.now() - res.elapsed * 1000;
  if (activeChanged && STATE.tab === job.tool) $('#active').innerHTML = activeHTML(job.tool);

  updateMeters();
  updateToolStatus();

  if (res.state === 'running') return setTimeout(poll, 400);

  // finished
  d.summary = res.summary;
  d.error = res.state === 'error' ? res.error : null;
  d.active = [];
  renderTabs();
  if (STATE.tab === job.tool) renderTool(job.tool);
  const word = { done: 'finished', cancelled: 'stopped', error: 'failed' }[res.state];
  msg(`${TOOLS[job.tool].title} ${word} · ${hms(res.elapsed)}`, res.state === 'error' ? 'err' : 'ok', 0);
}

// ── log pane ──────────────────────────────────────────────────────────────────
function appendLogs(logs) {
  if (!logs?.length) return;
  const body = $('#logbody');
  const stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 4;
  body.insertAdjacentHTML('beforeend', logs.map((l) => `<div class="lg ${l.level}"><span class="t">${l.t}</span><span class="m">${esc(l.msg)}</span></div>`).join(''));
  while (body.children.length > 2000) body.firstElementChild.remove();
  if (stick) body.scrollTop = body.scrollHeight;
  $('#logcount').textContent = `${num(S.job?.logCursor || 0)} lines`;
}

function toggleLog() {
  STATE.log = STATE.log === false;
  $('#log').classList.toggle('hidden', !STATE.log);
  saveState();
}

// ── meters (htop header) ──────────────────────────────────────────────────────
let CH = 8.4;
function measure() { CH = $('#measure').getBoundingClientRect().width / 10 || 8.4; }

function meterHTML(id, label) {
  return `<div class="meter" id="m-${id}"><span class="ml">${label}</span><span class="br">[</span><span class="bar"><span class="fill"></span><span class="val"></span></span><span class="br">]</span></div>`;
}

function setMeter(id, segments, value, valueCls = '') {
  const el = $(`#m-${id}`);
  if (!el) return;
  const bar = $('.bar', el);
  const width = Math.max(0, Math.floor(bar.clientWidth / CH));
  let used = 0;
  const html = segments.map(([frac, cls, ch = '|', offset = 0]) => {
    const pre = Math.min(width - used, Math.round(offset * width));
    const n = Math.max(0, Math.min(width - used - pre, Math.round(frac * width)));
    used += pre + n;
    return ' '.repeat(pre) + `<span class="${cls}">${ch.repeat(n)}</span>`;
  }).join('');
  $('.fill', el).innerHTML = html;
  const v = $('.val', el);
  v.textContent = value;
  v.className = `val ${valueCls}`;
}

function buildMeters() {
  $('#mleft').innerHTML = meterHTML('prog', 'Prog');
  $('#mright').innerHTML = '<div class="stat" id="s1"></div><div class="stat" id="s2"></div><div class="stat" id="s3"></div><div class="stat" id="s4"></div>';
}

function updateMeters() {
  for (const k of Object.keys(KEY_LABEL)) {
    const t = S.keyTest[k];
    if (t === true) setMeter(k, [[1, 'g']], 'online', 't-ok');
    else if (t === false) setMeter(k, [[1, 'r']], 'offline', 't-err');
    else if (t === 'testing') setMeter(k, [[0.15, 'y', '|', (S.tick % 7) / 8]], 'testing…', 't-warn');
    else if (INFO.keys[k]) setMeter(k, [[0.5, 'c']], 'key set', '');
    else setMeter(k, [], 'missing', 't-err');
  }

  const j = S.job?.last;
  const tool = S.job ? TOOLS[S.job.tool] : null;
  const running = j?.state === 'running';
  const elapsed = running ? (Date.now() - S.job.t0) / 1000 : (j?.elapsed || 0);
  const frac = j?.total ? Math.min(1, j.done / j.total) : 0;

  if (!j) setMeter('prog', [], 'idle', 't-dark');
  else if (running && !j.total) setMeter('prog', [[0.12, 'g', '|', ((S.tick % 16) / 16) * 0.88]], 'working…');
  else {
    const errs = S.job.tool === 'filter' ? data('filter').rows.filter((r) => r.result === 'error').length : 0;
    const ef = j.total ? errs / j.total : 0;
    setMeter('prog', [[Math.max(0, frac - ef), 'g'], [ef, 'r']], `${(frac * 100).toFixed(1)}%`, j.state === 'done' ? 't-ok' : '');
  }

  const stateCls = { running: 'ok', done: 'ok', cancelled: 'warn', error: 'err' };
  $('#s1').innerHTML = j
    ? `<span class="k">Job: </span><span class="v">${esc(tool.title)}</span><span class="d">, </span><span class="${stateCls[j.state]}">${esc(j.state === 'cancelled' ? 'stopped' : j.state)}</span>`
    : `<span class="k">Job: </span><span class="d">none · press F5 on a tool tab</span>`;

  if (j) {
    const rate = elapsed > 0 ? j.done / elapsed : 0;
    const eta = running && j.total && rate > 0 ? (j.total - j.done) / rate : null;
    $('#s2').innerHTML = `<span class="k">Done: </span><span class="v">${num(j.done)}</span><span class="d">/${j.total ? num(j.total) : '?'} ${esc(j.unit)}</span>`
      + `<span class="k">   Rate: </span><span class="v">${rate.toFixed(2)}</span><span class="d">/s</span>`
      + `<span class="k">   Time: </span><span class="v">${hms(elapsed)}</span>`
      + (eta != null ? `<span class="k">   ETA: </span><span class="v">${hms(eta)}</span>` : '');
    $('#s3').innerHTML = `<span class="k">Phase: </span><span class="d">${esc(j.phase || '')}</span>`;
    $('#s3').title = j.phase || '';
  } else {
    $('#s2').innerHTML = `<span class="k">Done: </span><span class="d">—</span>`;
    $('#s3').innerHTML = `<span class="k">Phase: </span><span class="d">waiting</span>`;
  }
  const up = INFO.started ? Date.now() / 1000 - INFO.started : 0;
  $('#s4').innerHTML = `<span class="k">Uptime: </span><span class="v">${hms(up)}</span><span class="k">   Version: </span><span class="v">${esc(INFO.version)}</span>`;
}

// ── function-key bar ──────────────────────────────────────────────────────────
const FKEYS = [['F1', 'Help', 'help'], ['F2', 'Setup', 'focus'], ['F3', 'Browse', 'browse'], ['F4', 'Log', 'log'], ['F5', 'Run', 'run'],
  ['F6', 'Stop', 'stop'], ['F7', 'Folder', 'reveal'], ['F8', 'Preview', 'preview'], ['F9', 'Clear', 'clear'], ['F10', 'Quit', 'quit']];
const fk = (k, l, act) => `<button class="kbtn" data-act="${act}"><span class="fk">${k}</span><span class="fl">${l}</span></button>`;

function renderFkeys() {
  $('#fkeys').innerHTML = FKEYS.map(([k, l, a]) => fk(k, a === 'run' && STATE.tab === 'settings' ? 'Test' : l, a)).join('');
}

async function browse(key) {
  const tool = STATE.tab;
  const t = TOOLS[tool];
  if (!t) return;
  const active = document.activeElement?.dataset?.k;
  const f = t.fields.find((x) => x.key === (key || active) && ['csv', 'save', 'folder'].includes(x.type))
    || t.fields.find((x) => x.type === 'csv');
  if (!f) return;
  const kind = f.type === 'csv' ? 'csv' : f.type === 'save' ? 'save_csv' : 'folder';
  const cur = unq(val(tool, f));
  const first = unq(val(tool, t.fields[0]));
  const startDir = cur ? (isAbs(cur) ? cur : dirname(first)) : (first ? dirname(first) : '');
  const name = f.type === 'save' ? (cur.split(/[\\/]/).pop() || f.def || 'output.csv') : '';
  const path = await api().pick(kind, startDir, name);
  if (!path) return;
  form(tool)[f.key] = path;
  saveState();
  const input = $(`input[data-k="${f.key}"]`);
  if (input) input.value = path;
  if (f === t.fields[0]) inspect(tool);
  refreshDynamic(tool);
}

function clearResults() {
  const tool = STATE.tab;
  if (!TOOLS[tool]) return;
  if (S.job?.tool === tool && S.job.last?.state === 'running') return msg('Can’t clear while this tool is running', 'err');
  S.data[tool] = { rows: [], summary: null, error: null, active: [] };
  renderTool(tool);
}

function quit() {
  if (S.job?.last?.state === 'running' && Date.now() - S.quitArmed > 3000) {
    S.quitArmed = Date.now();
    return msg('A job is running. Press F10 again within 3s to quit anyway (rows already written are kept).', 'err', 3000);
  }
  api().quit();
}

const ACTIONS = {
  help, run, stop, reveal, clearResults, quit,
  preview: () => preview(),
  browse: () => browse(),
  log: toggleLog,
  clear: clearResults,
  test: testConnections,
  close: closeOverlay,
  focus: () => $('#view input, #view select')?.focus(),
};

// ── events ────────────────────────────────────────────────────────────────────
function onInput(e) {
  const el = e.target;
  const tool = STATE.tab;
  const k = el.dataset.k;
  if (!k || !TOOLS[tool]) return;
  form(tool)[k] = el.value;
  saveState();
  if (k === TOOLS[tool].fields[0].key) inspect(tool);
  else refreshDynamic(tool);
}

function wire() {
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-tab],[data-act],[data-browse],[data-goto],[data-savekey],.tbody .trow');
    if (!t) { if (e.target.id === 'overlay') closeOverlay(); return; }
    if (t.dataset.tab) return setTab(t.dataset.tab);
    if (t.dataset.goto) return setTab(t.dataset.goto);
    if (t.dataset.browse) return browse(t.dataset.browse);
    if (t.dataset.savekey) return saveKey(t.dataset.savekey);
    if (t.dataset.act) { if (overlayOpen() && t.dataset.act !== 'close' && t.dataset.act !== 'reveal') closeOverlay(); return ACTIONS[t.dataset.act]?.(); }
    if (t.dataset.i != null) {
      const n = data(STATE.tab).rows.length;
      const i = Number(t.dataset.i);
      S.sel[STATE.tab] = i === n - 1 ? null : i;
      markSelection(STATE.tab, false);
    }
  });

  $('#view').addEventListener('input', onInput);
  $('#view').addEventListener('change', onInput);
  $('#view').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.dataset.key) saveKey(e.target.dataset.key);
  });

  document.addEventListener('keydown', (e) => {
    const map = { F1: 'help', F2: 'focus', F3: 'browse', F4: 'log', F5: 'run', F6: 'stop', F7: 'reveal', F8: 'preview', F9: 'clear', F10: 'quit' };
    if (map[e.key]) { e.preventDefault(); if (overlayOpen() && e.key !== 'F7') closeOverlay(); return ACTIONS[map[e.key]](); }
    if (e.key === 'Escape') { if (overlayOpen()) closeOverlay(); else document.activeElement?.blur(); return; }
    if ((e.ctrlKey || e.metaKey) && /^[1-9]$/.test(e.key)) {
      const tab = TABS[Number(e.key) - 1];
      if (tab) { e.preventDefault(); setTab(tab[0]); }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); return run(); }
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName);
    if (typing || overlayOpen()) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); moveSelection(1); }
    if (e.key === 'ArrowUp') { e.preventDefault(); moveSelection(-1); }
    if (e.key === 'PageDown') { e.preventDefault(); moveSelection(20); }
    if (e.key === 'PageUp') { e.preventDefault(); moveSelection(-20); }
    if (e.key === 'End') { e.preventDefault(); S.sel[STATE.tab] = null; markSelection(STATE.tab, true); }
    if (e.key === 'Home') { e.preventDefault(); S.sel[STATE.tab] = 0; markSelection(STATE.tab, true); }
  });

  window.addEventListener('resize', debounce(updateMeters, 50));
}

// ── boot ──────────────────────────────────────────────────────────────────────
async function boot() {
  measure();
  INFO = await api().get_info();
  const saved = await api().load_state();
  STATE = { forms: {}, tab: 'main', log: true, ...(saved || {}) };
  buildMeters();
  renderFkeys();
  wire();
  $('#log').classList.toggle('hidden', STATE.log === false);
  setTab(STATE.tab);
  updateMeters();
  setInterval(() => {
    S.tick++;
    if (!S.job || S.job.last?.state !== 'running') { if (S.tick % 3 === 0) updateMeters(); }
    else updateMeters();
  }, 330);
  // F5 label flips to "Test" on the Setup tab
  new MutationObserver(renderFkeys).observe($('#tabs'), { childList: true });
}

window.addEventListener('pywebviewready', boot);
