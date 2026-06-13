// unit_economics.js v2  — layout: statsponedelnik, style: simacontrol

let uePage = 1, ueTotalPages = 1;
let ueAll = [];       // all products (for client-side filter)
let ueSlice = [];     // current visible page
let ueEdits = {};     // {offer_id: {field: value}}
let ueChecked = new Set();
let ueMode = 'fbs';
let ueNoErr = true;
let ueFocus = false;
let ueTimer = null;
const UE_PER = 100;
const PVZ = 25;

const FBS_TARIFFS = [
  { max:2,        base:101.9, per:0  },
  { max:5,        base:76.9,  per:25 },
  { max:10,       base:151.9, per:20 },
  { max:20,       base:251.9, per:15 },
  { max:50,       base:401.9, per:10 },
  { max:100,      base:701.9, per:8  },
  { max:Infinity, base:1101.9,per:5  },
];

function baseLog(vol) {
  if (!vol || vol <= 0) return PVZ;
  let prev = 0;
  for (const t of FBS_TARIFFS) {
    if (vol <= t.max) return t.base + Math.max(0, vol - prev) * t.per;
    prev = t.max;
  }
  return PVZ;
}

function gAcq() { return parseFloat(document.getElementById('ue-acquiring')?.value ?? 1.9) || 1.9; }
function gNR()  { return parseFloat(document.getElementById('ue-non-red')?.value ?? 5) || 5; }

function ge(oid, f, fb) {
  const v = ueEdits[oid]?.[f];
  return v !== undefined ? v : (parseFloat(fb) || 0);
}

function calcRow(p) {
  const oid   = p.offer_id;
  const price = ge(oid, 'price', p.price);
  const commPct = ueMode === 'fbs' ? (p.commission_fbs_percent || 0) : (p.commission_fbs_percent || 0);
  const vol   = p.volume_liters || 0;

  const priemka   = ge(oid, 'priemka',  p.ff_cost || 0);
  const viezd     = ge(oid, 'viezd',    0);
  const dostKur   = ge(oid, 'dostKur',  0);
  const prodvizh  = ge(oid, 'prodvizh', 0);
  const oplataPct = ge(oid, 'oplataPct',0);
  const acqPct    = ge(oid, 'acqPct',   gAcq());
  const nrPct     = ge(oid, 'nrPct',    gNR());

  const oplataRub  = price * oplataPct / 100;
  const otherTotal = priemka + viezd + dostKur + prodvizh + oplataRub;

  const base  = baseLog(vol);
  const nev   = base * nrPct / 100;
  const logTot= base + nev + PVZ;

  const commRub = price * commPct / 100;
  const acqRub  = price * acqPct / 100;

  const payout = price - commRub - logTot - acqRub - otherTotal;
  const profit = payout - (p.cost_price || 0);
  const margin = payout ? profit / payout * 100 : 0;
  const roi    = p.cost_price ? profit / p.cost_price * 100 : 0;

  return { price, commPct, commRub,
    priemka, viezd, dostKur, prodvizh, oplataPct, oplataRub, otherTotal,
    base, nev, logTot,
    acqPct, acqRub,
    payout, profit, margin, roi };
}

function calcMarginPrice(p, tgt) {
  if (tgt >= 100 || tgt <= 0) return null;
  const cost = p.cost_price || 0;
  if (!cost) return null;
  const oid = p.offer_id;
  const commPct  = p.commission_fbs_percent || 0;
  const vol      = p.volume_liters || 0;
  const priemka  = ge(oid, 'priemka',  p.ff_cost || 0);
  const viezd    = ge(oid, 'viezd',    0);
  const dostKur  = ge(oid, 'dostKur',  0);
  const prodvizh = ge(oid, 'prodvizh', 0);
  const oplataPct= ge(oid, 'oplataPct',0);
  const acqPct   = ge(oid, 'acqPct',   gAcq());
  const nrPct    = ge(oid, 'nrPct',    gNR());
  const bl       = baseLog(vol);
  const logTot   = bl + bl * nrPct / 100 + PVZ;
  const fixed    = priemka + viezd + dostKur + prodvizh + logTot;
  const tPayout  = cost / (1 - tgt / 100);
  const rateSum  = (commPct + acqPct + oplataPct) / 100;
  if (1 - rateSum <= 0) return null;
  return Math.ceil((tPayout + fixed) / (1 - rateSum));
}

function calcROIPrice(p, tgt) {
  const cost = p.cost_price || 0;
  if (!cost || tgt <= 0) return null;
  const oid = p.offer_id;
  const commPct  = p.commission_fbs_percent || 0;
  const vol      = p.volume_liters || 0;
  const priemka  = ge(oid, 'priemka',  p.ff_cost || 0);
  const viezd    = ge(oid, 'viezd',    0);
  const dostKur  = ge(oid, 'dostKur',  0);
  const prodvizh = ge(oid, 'prodvizh', 0);
  const oplataPct= ge(oid, 'oplataPct',0);
  const acqPct   = ge(oid, 'acqPct',   gAcq());
  const nrPct    = ge(oid, 'nrPct',    gNR());
  const bl       = baseLog(vol);
  const logTot   = bl + bl * nrPct / 100 + PVZ;
  const fixed    = priemka + viezd + dostKur + prodvizh + logTot;
  const tPayout  = cost * (1 + tgt / 100);
  const rateSum  = (commPct + acqPct + oplataPct) / 100;
  if (1 - rateSum <= 0) return null;
  return Math.ceil((tPayout + fixed) / (1 - rateSum));
}

// ── Mode toggle ────────────────────────────────────────────────────
function setUEMode(mode) {
  ueMode = mode;
  document.getElementById('ue-tab-fbs').className = 'ue-tab' + (mode === 'fbs' ? ' fbs-on' : '');
  document.getElementById('ue-tab-fbo').className = 'ue-tab' + (mode === 'fbo' ? ' fbo-on' : '');
  renderUE();
}

function toggleNoErr() {
  ueNoErr = !ueNoErr;
  document.getElementById('ue-noerr-t').className = 'ue-tw' + (ueNoErr ? ' on' : '');
  applyUEFilters(1);
}

function toggleFocus() {
  ueFocus = !ueFocus;
  document.getElementById('ue-focus-t').className = 'ue-tw' + (ueFocus ? ' on' : '');
  applyUEFilters(1);
}

// ── Filters ────────────────────────────────────────────────────────
function debounceUE() {
  clearTimeout(ueTimer);
  ueTimer = setTimeout(() => applyUEFilters(1), 350);
}

function clearUEFilters() {
  ['ue-search','ue-brand','ue-category',
   'uf-prof-lo','uf-prof-hi','uf-mar-lo','uf-mar-hi','uf-roi-lo','uf-roi-hi']
    .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  applyUEFilters(1);
}

function applyUEFilters(pg) {
  uePage = pg || uePage;
  const srch  = document.getElementById('ue-search')?.value.trim().toLowerCase() || '';
  const brand = document.getElementById('ue-brand')?.value || '';
  const cat   = document.getElementById('ue-category')?.value || '';
  const plo   = parseFloat(document.getElementById('uf-prof-lo')?.value) || null;
  const phi   = parseFloat(document.getElementById('uf-prof-hi')?.value) || null;
  const mlo   = parseFloat(document.getElementById('uf-mar-lo')?.value)  || null;
  const mhi   = parseFloat(document.getElementById('uf-mar-hi')?.value)  || null;
  const rlo   = parseFloat(document.getElementById('uf-roi-lo')?.value)  || null;
  const rhi   = parseFloat(document.getElementById('uf-roi-hi')?.value)  || null;

  let list = ueAll;
  if (srch)  list = list.filter(p => p.offer_id.toLowerCase().includes(srch) || (p.name||'').toLowerCase().includes(srch));
  if (brand) list = list.filter(p => p.brand === brand);
  if (cat)   list = list.filter(p => p.category_name === cat);

  if (plo!==null||phi!==null||mlo!==null||mhi!==null||rlo!==null||rhi!==null) {
    list = list.filter(p => {
      const r = calcRow(p);
      if (plo!==null && r.profit < plo) return false;
      if (phi!==null && r.profit > phi) return false;
      if (mlo!==null && r.margin < mlo) return false;
      if (mhi!==null && r.margin > mhi) return false;
      if (rlo!==null && r.roi    < rlo) return false;
      if (rhi!==null && r.roi    > rhi) return false;
      return true;
    });
  }
  if (ueNoErr)  list = list.filter(p => calcRow(p).profit >= 0);
  if (ueFocus)  list = list.filter(p => ueChecked.has(p.offer_id));

  const total = list.length;
  ueTotalPages = Math.ceil(total / UE_PER) || 1;
  if (uePage > ueTotalPages) uePage = 1;
  const off = (uePage - 1) * UE_PER;
  ueSlice = list.slice(off, off + UE_PER);

  document.getElementById('ue-badge').textContent = total;
  document.getElementById('ue-count').textContent = total ? total.toLocaleString('ru') + ' товаров' : '';
  renderUE();
  renderUEPager(total);
}

// ── Load ───────────────────────────────────────────────────────────
async function loadUE() {
  document.getElementById('ue-tbody').innerHTML = '<tr><td colspan="30" class="state-msg">ЗАГРУЗКА...</td></tr>';
  try {
    const r = await fetch('/api/unit-economics/products?per_page=5000').then(r => r.json());
    ueAll = r.products || [];
    await loadUEFiltersDropdown();
    applyUEFilters(1);
  } catch(e) {
    document.getElementById('ue-tbody').innerHTML =
      `<tr><td colspan="30" class="state-msg" style="color:var(--red,#e84)">ОШИБКА: ${e.message}</td></tr>`;
  }
}

async function loadUEFiltersDropdown() {
  try {
    const r = await fetch('/api/unit-economics/filters').then(r => r.json());
    const bs = document.getElementById('ue-brand');
    const cs = document.getElementById('ue-category');
    const sb = bs.value, sc = cs.value;
    bs.innerHTML = '<option value="">Бренд</option>';
    (r.brands || []).forEach(b => {
      const o = document.createElement('option');
      o.value = b; o.textContent = b;
      if (b === sb) o.selected = true;
      bs.appendChild(o);
    });
    cs.innerHTML = '<option value="">Категория</option>';
    (r.categories || []).forEach(c => {
      const o = document.createElement('option');
      o.value = c; o.textContent = c;
      if (c === sc) o.selected = true;
      cs.appendChild(o);
    });
  } catch {}
}

// ── Render ─────────────────────────────────────────────────────────
function fmt(n, d) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  return (+n).toFixed(d ?? 2).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

function mkI(oid, f, val, cls, extra) {
  const m = ueEdits[oid]?.[f] !== undefined ? ' m' : '';
  return `<input class="ui ${cls}${m}" type="number" step="any" value="${val ?? ''}"
    onchange="onEC('${oid}','${f}',this)" onfocus="this.select()" ${extra||''}>`;
}

function renderUE() {
  const tb = document.getElementById('ue-tbody');
  if (!ueSlice.length) {
    tb.innerHTML = '<tr><td colspan="30" class="state-msg">Нет товаров</td></tr>';
    updateSendBtn();
    return;
  }

  tb.innerHTML = ueSlice.map(p => {
    const oid = p.offer_id;
    const r   = calcRow(p);
    const chk = ueChecked.has(oid);
    const img = p.image_url
      ? `<img src="${p.image_url}" alt="" loading="lazy">`
      : `<span class="ni"></span>`;

    const pCls = r.profit < 0 ? 'trd' : 'tgr';
    const mCls = r.margin < 0 ? 'trd' : 'tgr';
    const rCls = r.roi    < 0 ? 'trd' : 'tgr';

    const tmVal = ueEdits[oid]?.tm ?? '';
    const trVal = ueEdits[oid]?.tr ?? '';
    const pv = ge(oid, 'price', p.price);

    return `<tr>
<td style="text-align:center;padding:3px 4px"><input type="checkbox" ${chk?'checked':''}
  onchange="onChk('${oid}',this.checked)" style="accent-color:var(--accent);cursor:pointer"></td>
<td style="text-align:left;padding:4px 5px;color:var(--accent);font-family:monospace;font-size:11px"
  onclick="navigator.clipboard?.writeText('${oid}')" title="Копировать">${oid}</td>
<td class="tim">${img}</td>
<td class="tnm" title="${(p.name||'').replace(/"/g,'&quot;')}">${p.name||''}</td>
<td class="tdl">${fmt(p.cost_price)}</td>
<td class="tdl">${mkI(oid,'priemka', ge(oid,'priemka',p.ff_cost||0),'')}</td>
<td>${mkI(oid,'viezd',   ge(oid,'viezd',0),'')}</td>
<td>${mkI(oid,'dostKur', ge(oid,'dostKur',0),'')}</td>
<td>${mkI(oid,'prodvizh',ge(oid,'prodvizh',0),'')}</td>
<td>${mkI(oid,'oplataPct',ge(oid,'oplataPct',0),'')}</td>
<td data-c="${oid}-or">${fmt(r.oplataRub)}</td>
<td class="tcn tdl" data-c="${oid}-lt">${fmt(r.logTot)}</td>
<td data-c="${oid}-bl" class="tdi">${fmt(r.base)}</td>
<td>${mkI(oid,'nrPct',ge(oid,'nrPct',gNR()),'')}</td>
<td class="tdi">${PVZ}</td>
<td class="tdl tdi">${fmt(r.commPct,0)}%</td>
<td data-c="${oid}-cr">${fmt(r.commRub)}</td>
<td>${mkI(oid,'acqPct',ge(oid,'acqPct',gAcq()),'')}</td>
<td class="tdl"><input class="ui p${ueEdits[oid]?.price!==undefined?' m':''}" type="number" step="1"
  value="${pv}" onchange="onEC('${oid}','price',this)" onfocus="this.select()"></td>
<td><input class="ui t" type="number" step="0.1" placeholder="%"
  value="${tmVal}" onchange="onTM('${oid}',this)"></td>
<td><input class="ui t" type="number" step="0.1" placeholder="%"
  value="${trVal}" onchange="onTR('${oid}',this)"></td>
<td class="tdl" data-c="${oid}-py">${fmt(r.payout)}</td>
<td class="${pCls}" data-c="${oid}-pr">${fmt(r.profit)}</td>
<td class="${mCls}" data-c="${oid}-mg">${fmt(r.margin,2)}%</td>
<td class="${rCls}" data-c="${oid}-ri">${fmt(r.roi,2)}%</td>
</tr>`;
  }).join('');
  updateSendBtn();
}

// ── Cell handlers ──────────────────────────────────────────────────
function onEC(oid, f, inp) {
  const v = parseFloat(inp.value);
  if (!ueEdits[oid]) ueEdits[oid] = {};
  ueEdits[oid][f] = isNaN(v) ? 0 : v;
  inp.classList.add('m');
  rcalc(oid);
  updateSendBtn();
}

function onTM(oid, inp) {
  const pct = parseFloat(inp.value);
  if (!ueEdits[oid]) ueEdits[oid] = {};
  ueEdits[oid].tm = isNaN(pct) ? '' : pct;
  if (!isNaN(pct) && pct > 0 && pct < 100) {
    const p = ueAll.find(x => x.offer_id === oid);
    if (p) {
      const np = calcMarginPrice(p, pct);
      if (np) {
        ueEdits[oid].price = np;
        const pi = document.querySelector(`td input.p[onchange*="'${oid}','price'"]`);
        if (pi) { pi.value = np; pi.classList.add('m'); }
        rcalc(oid);
        updateSendBtn();
      }
    }
  }
}

function onTR(oid, inp) {
  const pct = parseFloat(inp.value);
  if (!ueEdits[oid]) ueEdits[oid] = {};
  ueEdits[oid].tr = isNaN(pct) ? '' : pct;
  if (!isNaN(pct) && pct > 0) {
    const p = ueAll.find(x => x.offer_id === oid);
    if (p) {
      const np = calcROIPrice(p, pct);
      if (np) {
        ueEdits[oid].price = np;
        const pi = document.querySelector(`td input.p[onchange*="'${oid}','price'"]`);
        if (pi) { pi.value = np; pi.classList.add('m'); }
        rcalc(oid);
        updateSendBtn();
      }
    }
  }
}

function rcalc(oid) {
  const p = ueAll.find(x => x.offer_id === oid);
  if (!p) return;
  const r = calcRow(p);
  const s = (a, v, d) => { const el = document.querySelector(`[data-c="${a}"]`); if (el) el.textContent = fmt(v, d); };
  s(`${oid}-or`, r.oplataRub);
  s(`${oid}-lt`, r.logTot);
  s(`${oid}-bl`, r.base);
  s(`${oid}-cr`, r.commRub);
  s(`${oid}-py`, r.payout);
  s(`${oid}-pr`, r.profit);
  s(`${oid}-mg`, r.margin, 2);
  s(`${oid}-ri`, r.roi, 2);
  const pf = document.querySelector(`[data-c="${oid}-pr"]`);
  if (pf) pf.className = r.profit < 0 ? 'trd' : 'tgr';
  const mf = document.querySelector(`[data-c="${oid}-mg"]`);
  if (mf) mf.className = r.margin < 0 ? 'trd' : 'tgr';
  const rf = document.querySelector(`[data-c="${oid}-ri"]`);
  if (rf) rf.className = r.roi < 0 ? 'trd' : 'tgr';
}

// ── Checkboxes ─────────────────────────────────────────────────────
function onChk(oid, checked) {
  if (checked) ueChecked.add(oid); else ueChecked.delete(oid);
  updateSendBtn();
}

function toggleUEAll(checked) {
  ueSlice.forEach(p => { if (checked) ueChecked.add(p.offer_id); else ueChecked.delete(p.offer_id); });
  document.querySelectorAll('#ue-tbody input[type=checkbox]').forEach(el => el.checked = checked);
  updateSendBtn();
}

function updateSendBtn() {
  const priceChg = [...ueChecked].filter(oid => ueEdits[oid]?.price !== undefined).length;
  const btn = document.getElementById('ue-send-btn');
  btn.disabled = priceChg === 0;
  document.getElementById('ue-send-count').textContent = priceChg;
}

// ── Pagination ─────────────────────────────────────────────────────
function renderUEPager(total) {
  const el = document.getElementById('ue-pagination');
  if (!el || ueTotalPages <= 1) { if (el) el.innerHTML = ''; return; }
  let h = `<button ${uePage===1?'disabled':''} onclick="applyUEFilters(${uePage-1})">‹</button>`;
  for (let i = 1; i <= ueTotalPages; i++) {
    if (ueTotalPages > 10 && Math.abs(i - uePage) > 2 && i !== 1 && i !== ueTotalPages) {
      if (i === uePage - 3 || i === uePage + 3) h += '<button disabled>…</button>';
      continue;
    }
    h += `<button class="${i===uePage?'cur':''}" onclick="applyUEFilters(${i})">${i}</button>`;
  }
  h += `<button ${uePage===ueTotalPages?'disabled':''} onclick="applyUEFilters(${uePage+1})">›</button>`;
  h += `<span class="pgi">стр. ${uePage} из ${ueTotalPages}</span>`;
  el.innerHTML = h;
}

// ── CSV ────────────────────────────────────────────────────────────
function downloadUECSV() {
  const h = ['Артикул','Название','Бренд','Категория','Себест.','ff_cost','Цена',
    'Ком.%','Ком.₽','База','Невыкупы','Лог.Итого','Эквайр.',
    'К выплате','Прибыль₽','Маржа%','ROI%'];
  const rows = ueAll.map(p => {
    const r = calcRow(p);
    return [p.offer_id, p.name, p.brand, p.category_name, p.cost_price, p.ff_cost,
      r.price, r.commPct, r.commRub, r.base, r.nev, r.logTot, r.acqRub,
      r.payout, r.profit, r.margin.toFixed(2), r.roi.toFixed(2)].join(';');
  });
  const csv = [h.join(';'), ...rows].join('\n');
  const b = new Blob(['﻿' + csv], {type:'text/csv;charset=utf-8;'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(b); a.download = 'unit_economics.csv'; a.click();
}

// ── Send prices ────────────────────────────────────────────────────
async function sendPrices() {
  const items = [...ueChecked]
    .filter(oid => ueEdits[oid]?.price !== undefined)
    .map(oid => {
      const p = ueAll.find(x => x.offer_id === oid);
      const np = ueEdits[oid].price;
      return {
        offer_id: oid,
        price: String(Math.round(np)),
        old_price: String(Math.round((p?.price || np) * 1.1)),
        min_price: String(Math.round(np * 0.9)),
        currency_code: 'RUB', vat: '0.1',
        auto_action_enabled: 'UNKNOWN', price_strategy_enabled: 'UNKNOWN',
      };
    });
  if (!items.length) return;

  const preview = items.slice(0,10).map(i => {
    const p = ueAll.find(x => x.offer_id === i.offer_id);
    const old = p?.price || 0;
    const diff = Math.round(i.price) - old;
    const dc = diff > 0 ? `<span style="color:var(--green)">+${diff}</span>` : diff < 0 ? `<span style="color:var(--red,#e84);">${diff}</span>` : '0';
    return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);font-size:12px">
      <span style="color:var(--accent);font-family:monospace">${i.offer_id}</span>
      <span>${old} → <b>${i.price}</b> ₽ ${dc}</span></div>`;
  }).join('');
  const more = items.length > 10 ? `<div style="color:var(--text-dim);font-size:11px;margin-top:6px">...и ещё ${items.length-10}</div>` : '';

  if (!await ueConfirm(`Передать цены — ${items.length} позиций`, preview + more)) return;
  const btn = document.getElementById('ue-send-btn');
  btn.disabled = true;
  try {
    const r = await fetch('/api/unit-economics/update-prices', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({prices: items}),
    }).then(r => r.json());
    if (r.errors > 0) showToast(`Обновлено ${(r.total||0)-(r.errors||0)} из ${r.total||0}, ошибок: ${r.errors}`, 'error');
    else showToast(`✓ Цены обновлены для ${r.total||items.length} позиций`);
    ueChecked.clear();
    ueEdits = {};
    await loadUE();
  } catch(e) {
    showToast('Ошибка: ' + e.message, 'error');
    btn.disabled = false;
    updateSendBtn();
  }
}

// ── Sync ───────────────────────────────────────────────────────────
async function syncUEProducts() {
  const btn = document.getElementById('ue-sync-btn');
  btn.disabled = true; btn.textContent = '⟳ СИНХРОНИЗАЦИЯ...';
  try {
    await fetch('/api/unit-economics/sync', {method:'POST'});
    let att = 0;
    while (++att < 120) {
      await new Promise(r => setTimeout(r, 2000));
      const s = await fetch('/api/unit-economics/sync/status').then(r => r.json());
      if (s.running) { btn.textContent = `⟳ ${s.progress || '...'}`;
      } else if (s.error) { showToast('Ошибка: ' + s.error, 'error'); break;
      } else { showToast(`✓ Синхронизировано ${(s.synced||0).toLocaleString('ru')} товаров`); await loadUE(); break; }
    }
  } catch(e) { showToast('Ошибка: ' + e.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = '⟳ ПОЛУЧИТЬ ДАННЫЕ С OZON'; }
}

// ── Confirm modal ──────────────────────────────────────────────────
function ueConfirm(title, bodyHtml) {
  return new Promise(res => {
    const m = document.getElementById('ue-confirm-modal');
    document.getElementById('ue-confirm-title').textContent = title;
    document.getElementById('ue-confirm-body').innerHTML = bodyHtml;
    m.classList.add('show');
    const ok = document.getElementById('ue-confirm-ok');
    const cn = document.getElementById('ue-confirm-cancel');
    function close(r) { m.classList.remove('show'); ok.removeEventListener('click',onOk); cn.removeEventListener('click',onCn); res(r); }
    const onOk = () => close(true), onCn = () => close(false);
    ok.addEventListener('click',onOk); cn.addEventListener('click',onCn);
  });
}

// ── Toast ──────────────────────────────────────────────────────────
function showToast(msg, type) {
  const el = document.createElement('div');
  el.textContent = msg;
  el.style.cssText = `position:fixed;bottom:24px;right:24px;z-index:9999;padding:10px 18px;
    background:${type==='error'?'var(--red,#e84444)':'#1a7a4a'};color:#fff;font-size:12px;
    border-radius:4px;max-width:380px;box-shadow:0 4px 16px rgba(0,0,0,.5)`;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ── Init ───────────────────────────────────────────────────────────
loadUE();
