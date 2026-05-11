// =====================================================================
// ЮНИТ-ЭКОНОМИКА
// =====================================================================

let uePage = 1;
let ueTotalPages = 1;
let ueProducts = [];       // текущая страница
let ueEdits = {};          // offer_id -> {price, target_margin}
let ueChecked = new Set(); // выбранные offer_id
let ueSearchTimer = null;
const UE_PER_PAGE = 100;

// =====================================================================
// CONFIRM MODAL
// =====================================================================
function ueConfirm(title, bodyHtml) {
  return new Promise(resolve => {
    const modal = document.getElementById('ue-confirm-modal');
    document.getElementById('ue-confirm-title').textContent = title;
    document.getElementById('ue-confirm-body').innerHTML = bodyHtml;
    modal.style.display = 'flex';
    const ok = document.getElementById('ue-confirm-ok');
    const cancel = document.getElementById('ue-confirm-cancel');
    function close(r) {
      modal.style.display = 'none';
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
      resolve(r);
    }
    const onOk = () => close(true);
    const onCancel = () => close(false);
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
  });
}

// =====================================================================
// РАСЧЁТ ЮНИТ-ЭКОНОМИКИ
// =====================================================================
function calcUE(p, priceOverride) {
  const price = priceOverride !== undefined ? priceOverride : (p.price || 0);
  const cost = p.cost_price || 0;
  const commPct = (p.commission_fbs_percent || 0) / 100;
  const logistics = p.commission_fbs_logistics || 0;
  const acquiringPct = parseFloat(document.getElementById('ue-acquiring')?.value || 1.5) / 100;
  const adsPct = parseFloat(document.getElementById('ue-ads')?.value || 0) / 100;
  const returnsPct = parseFloat(document.getElementById('ue-returns')?.value || 5) / 100;

  const commission = price * commPct;
  const acquiring = price * acquiringPct;
  const ads = price * adsPct;
  // Стоимость возвратов: доля от логистики
  const returnsCost = logistics * returnsPct;
  const other = ads + returnsCost;

  const payout = price - commission - logistics - acquiring - other;
  const profit = payout - cost;
  const margin = payout > 0 ? (profit / payout * 100) : 0;
  const roi = cost > 0 ? (profit / cost * 100) : 0;

  return { price, cost, commission, logistics, acquiring, other, payout, profit, margin, roi };
}

// Обратный расчёт: целевая маржа → рекомендованная цена
// margin = (payout - cost) / payout
// payout = cost / (1 - margin)
// price = payout / (1 - commPct - acquiringPct - adsPct) + logistics / (...)
function calcPriceFromMargin(p, targetMarginPct) {
  const targetMargin = targetMarginPct / 100;
  if (targetMargin >= 1 || targetMargin < 0) return null;

  const cost = p.cost_price || 0;
  const commPct = (p.commission_fbs_percent || 0) / 100;
  const logistics = p.commission_fbs_logistics || 0;
  const acquiringPct = parseFloat(document.getElementById('ue-acquiring')?.value || 1.5) / 100;
  const adsPct = parseFloat(document.getElementById('ue-ads')?.value || 0) / 100;
  const returnsPct = parseFloat(document.getElementById('ue-returns')?.value || 5) / 100;

  const returnsCost = logistics * returnsPct;
  // payout = price * (1 - commPct - acquiringPct - adsPct) - logistics - returnsCost
  // profit = payout - cost
  // margin = profit / payout => profit = payout * targetMargin
  // payout - cost = payout * targetMargin
  // payout * (1 - targetMargin) = cost
  // payout = cost / (1 - targetMargin)
  const payout = cost / (1 - targetMargin);
  // payout = price * (1 - rateSum) - fixedCosts
  const rateSum = commPct + acquiringPct + adsPct;
  const fixedCosts = logistics + returnsCost;
  if (1 - rateSum <= 0) return null;
  const price = (payout + fixedCosts) / (1 - rateSum);
  return Math.ceil(price);
}

// =====================================================================
// ЗАГРУЗКА И РЕНДЕР
// =====================================================================
function debounceUE() {
  clearTimeout(ueSearchTimer);
  ueSearchTimer = setTimeout(() => loadUE(1), 400);
}

function clearUEFilters() {
  document.getElementById('ue-search').value = '';
  document.getElementById('ue-brand').value = '';
  loadUE(1);
}

async function loadUE(page = 1) {
  uePage = page;
  const search = document.getElementById('ue-search')?.value || '';
  const brand = document.getElementById('ue-brand')?.value || '';
  document.getElementById('ue-tbody').innerHTML = '<tr><td colspan="13" class="state-msg">ЗАГРУЗКА...</td></tr>';

  try {
    const params = new URLSearchParams({ page, per_page: UE_PER_PAGE, search, brand });
    const r = await fetch(`/api/unit-economics/products?${params}`).then(r => r.json());
    ueProducts = r.products || [];
    const total = r.total || 0;
    ueTotalPages = Math.ceil(total / UE_PER_PAGE);
    document.getElementById('ue-count').textContent = total ? `${total} товаров` : '';
    renderUE();
    renderUEPagination(total);
  } catch(e) {
    document.getElementById('ue-tbody').innerHTML = '<tr><td colspan="13" class="state-msg" style="color:var(--red)">ОШИБКА</td></tr>';
  }
}

async function loadUEFilters() {
  try {
    const r = await fetch('/api/repricer/filters').then(r => r.json());
    const sel = document.getElementById('ue-brand');
    (r.brands || []).forEach(b => {
      const o = document.createElement('option');
      o.value = b; o.textContent = b;
      sel.appendChild(o);
    });
  } catch {}
}

function recalcAll() {
  renderUE();
}

function renderUE() {
  const tbody = document.getElementById('ue-tbody');
  if (!ueProducts.length) {
    tbody.innerHTML = '<tr><td colspan="13" class="state-msg">НЕТ ДАННЫХ</td></tr>';
    return;
  }

  tbody.innerHTML = ueProducts.map(p => {
    const priceOverride = ueEdits[p.offer_id]?.price;
    const targetMargin = ueEdits[p.offer_id]?.target_margin;
    const displayPrice = priceOverride !== undefined ? priceOverride : p.price;
    const calc = calcUE(p, priceOverride);
    const isChecked = ueChecked.has(p.offer_id);
    const hasEdit = priceOverride !== undefined;

    const marginColor = calc.margin < 5 ? 'var(--red)' : calc.margin < 15 ? 'var(--yellow,#f0a500)' : 'var(--green)';
    const profitColor = calc.profit < 0 ? 'var(--red)' : calc.profit < 50 ? 'var(--yellow,#f0a500)' : 'var(--green)';

    return `<tr style="${isChecked ? 'background:rgba(255,106,0,0.08)' : hasEdit ? 'background:rgba(0,200,100,0.04)' : ''}">
      <td style="text-align:center;padding:4px 8px">
        <input type="checkbox" ${isChecked ? 'checked' : ''}
          onchange="toggleUECheck('${p.offer_id}', this.checked)"
          style="width:15px;height:15px;cursor:pointer;accent-color:var(--accent)">
      </td>
      <td style="color:var(--accent);font-family:monospace;font-size:11px;padding:6px 8px;cursor:pointer;white-space:nowrap"
        onclick="copyUECell('${p.offer_id}', this)">${p.offer_id}</td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:var(--text-dim);padding:6px 8px">${p.name || '—'}</td>
      <td style="text-align:right;padding:6px 8px;color:${p.cost_price ? 'var(--text)' : 'var(--text-dim)'}">${p.cost_price ? p.cost_price.toLocaleString('ru') + ' ₽' : '—'}</td>
      <td style="padding:4px 6px;text-align:right">
        <input type="number" min="0" value="${displayPrice}"
          style="width:80px;padding:3px 6px;background:var(--surface2);border:1px solid ${hasEdit ? 'var(--green)' : 'var(--border)'};color:var(--text);font-size:12px;text-align:right"
          onchange="setUEPrice('${p.offer_id}', this.value)"
          oninput="setUEPrice('${p.offer_id}', this.value)">
      </td>
      <td style="text-align:right;padding:6px 8px;color:var(--text-dim);font-size:11px">${fmtRub(calc.commission)} (${p.commission_fbs_percent || 0}%)</td>
      <td style="text-align:right;padding:6px 8px;color:var(--text-dim);font-size:11px">${fmtRub(calc.logistics)}</td>
      <td style="text-align:right;padding:6px 8px;color:var(--text-dim);font-size:11px">${fmtRub(calc.acquiring + (calc.other - calc.other + calc.acquiring === calc.acquiring ? 0 : 0))}${fmtRub(calc.other)}</td>
      <td style="text-align:right;padding:6px 8px;font-weight:600">${fmtRub(calc.payout)}</td>
      <td style="text-align:right;padding:6px 8px;font-weight:700;color:${profitColor}">${fmtRub(calc.profit)}</td>
      <td style="padding:4px 6px;text-align:right">
        <input type="number" min="0" max="100" step="0.1"
          value="${targetMargin !== undefined ? targetMargin : ''}"
          placeholder="—"
          style="width:65px;padding:3px 6px;background:var(--surface2);border:1px solid var(--border);color:var(--text);font-size:12px;text-align:right"
          onchange="setUETargetMargin('${p.offer_id}', this.value)"
          oninput="setUETargetMargin('${p.offer_id}', this.value)">
      </td>
      <td style="text-align:right;padding:6px 8px;font-weight:600;color:${marginColor}">${calc.margin.toFixed(1)}%</td>
      <td style="text-align:right;padding:6px 8px;color:var(--text-dim);font-size:11px">${calc.roi.toFixed(1)}%</td>
    </tr>`;
  }).join('');

  updateUECheckAll();
  updateUESendBtn();
}

function fmtRub(val) {
  if (val === null || val === undefined || isNaN(val)) return '—';
  return Math.round(val).toLocaleString('ru') + ' ₽';
}

function copyUECell(text, el) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = el.style.color;
    el.style.color = 'var(--green)';
    setTimeout(() => el.style.color = orig, 800);
  });
}

// =====================================================================
// РЕДАКТИРОВАНИЕ
// =====================================================================
function setUEPrice(offerId, value) {
  const num = parseFloat(value);
  if (!isNaN(num) && num > 0) {
    if (!ueEdits[offerId]) ueEdits[offerId] = {};
    ueEdits[offerId].price = num;
  } else {
    if (ueEdits[offerId]) delete ueEdits[offerId].price;
  }
  // Пересчитываем только строку без полного ре-рендера
  rerenderRow(offerId);
}

function setUETargetMargin(offerId, value) {
  const p = ueProducts.find(x => x.offer_id === offerId);
  if (!p) return;
  const num = parseFloat(value);
  if (!isNaN(num) && num > 0 && num < 100) {
    if (!ueEdits[offerId]) ueEdits[offerId] = {};
    ueEdits[offerId].target_margin = num;
    const newPrice = calcPriceFromMargin(p, num);
    if (newPrice) {
      ueEdits[offerId].price = newPrice;
      // Обновляем инпут цены
      const priceInput = document.querySelector(`input[data-offer-price="${offerId}"]`);
      if (priceInput) priceInput.value = newPrice;
    }
  } else {
    if (ueEdits[offerId]) {
      delete ueEdits[offerId].target_margin;
    }
  }
  rerenderRow(offerId);
}

function applyTargetMarginAll() {
  const globalMargin = parseFloat(document.getElementById('ue-target-margin')?.value);
  if (isNaN(globalMargin) || globalMargin <= 0) return;
  ueProducts.forEach(p => {
    if (!ueEdits[p.offer_id]) ueEdits[p.offer_id] = {};
    ueEdits[p.offer_id].target_margin = globalMargin;
    const newPrice = calcPriceFromMargin(p, globalMargin);
    if (newPrice) ueEdits[p.offer_id].price = newPrice;
  });
  renderUE();
  showToast(`✓ Целевая маржа ${globalMargin}% применена к ${ueProducts.length} товарам`);
}

function rerenderRow(offerId) {
  // Полный ре-рендер таблицы — просто и надёжно
  renderUE();
}

// =====================================================================
// ЧЕКБОКСЫ
// =====================================================================
function toggleUECheck(offerId, checked) {
  if (checked) ueChecked.add(offerId);
  else ueChecked.delete(offerId);
  updateUECheckAll();
  updateUESendBtn();
}

function toggleUEAll(checked) {
  ueProducts.forEach(p => {
    if (checked) ueChecked.add(p.offer_id);
    else ueChecked.delete(p.offer_id);
  });
  updateUESendBtn();
  renderUE();
}

function updateUECheckAll() {
  const cb = document.getElementById('ue-check-all');
  if (!cb) return;
  const checkedOnPage = ueProducts.filter(p => ueChecked.has(p.offer_id)).length;
  cb.indeterminate = checkedOnPage > 0 && checkedOnPage < ueProducts.length;
  cb.checked = ueProducts.length > 0 && checkedOnPage === ueProducts.length;
}

function updateUESendBtn() {
  const count = ueChecked.size;
  document.getElementById('ue-send-count').textContent = count;
  document.getElementById('ue-send-btn').disabled = count === 0;
  const el = document.getElementById('ue-selected-count');
  el.textContent = count > 0 ? `✓ Выбрано: ${count}` : '';
}

// =====================================================================
// ПЕРЕДАЧА ЦЕН
// =====================================================================
async function sendPrices() {
  const prices = [];
  ueChecked.forEach(offerId => {
    const p = ueProducts.find(x => x.offer_id === offerId);
    if (!p) return;
    const newPrice = ueEdits[offerId]?.price ?? p.price;
    if (!newPrice || newPrice <= 0) return;
    prices.push({
      offer_id: offerId,
      price: String(Math.round(newPrice)),
      old_price: String(Math.round(p.price * 1.1)),
      min_price: String(Math.round(newPrice * 0.9)),
      currency_code: "RUB",
      vat: "0.1",
      auto_action_enabled: "UNKNOWN",
      price_strategy_enabled: "UNKNOWN",
    });
  });

  if (!prices.length) {
    showToast('Нет данных для передачи', 'error');
    return;
  }

  const previewRows = prices.slice(0, 10).map(p => {
    const prod = ueProducts.find(x => x.offer_id === p.offer_id);
    const oldPrice = prod?.price || 0;
    const diff = Math.round(p.price) - oldPrice;
    const diffStr = diff > 0 ? `<span style="color:var(--green)">+${diff}</span>` : diff < 0 ? `<span style="color:var(--red)">${diff}</span>` : '0';
    return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);font-size:12px">
      <span style="color:var(--accent);font-family:monospace">${p.offer_id}</span>
      <span>${oldPrice} → <b>${p.price}</b> ₽ ${diffStr}</span>
    </div>`;
  }).join('');

  const more = prices.length > 10 ? `<div style="color:var(--text-dim);font-size:11px;margin-top:8px">... и ещё ${prices.length - 10} позиций</div>` : '';

  if (!await ueConfirm(`Передать цены — ${prices.length} позиций`, previewRows + more)) return;

  const btn = document.getElementById('ue-send-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Передаём...';

  try {
    const r = await fetch('/api/unit-economics/update-prices', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ prices })
    }).then(r => r.json());

    if (r.errors > 0) {
      showToast(`⚠ Обновлено ${r.total - r.errors} из ${r.total}, ошибок: ${r.errors}`, 'error');
    } else {
      showToast(`✓ Цены обновлены для ${r.total} позиций`);
      ueChecked.clear();
      ueEdits = {};
      loadUE(uePage);
    }
  } catch {
    showToast('Ошибка передачи цен', 'error');
  } finally {
    btn.disabled = ueChecked.size === 0;
    const cnt = document.getElementById('ue-send-count');
    if (cnt) cnt.textContent = ueChecked.size;
    btn.innerHTML = `→ Передать цены в Ozon (<span id="ue-send-count">${ueChecked.size}</span>)`;
  }
}

// =====================================================================
// ПАГИНАЦИЯ
// =====================================================================
function renderUEPagination(total) {
  const el = document.getElementById('ue-pagination');
  const totalPages = Math.ceil(total / UE_PER_PAGE);
  if (totalPages <= 1) { el.innerHTML = ''; return; }

  const btnStyle = 'display:inline-flex;align-items:center;justify-content:center;min-width:32px;height:32px;padding:0 8px;margin:0 2px;border:1px solid var(--border);background:var(--surface);color:var(--text);cursor:pointer;font-size:12px;';
  const activeBtnStyle = btnStyle + 'border-color:var(--accent);color:var(--accent);background:rgba(255,106,0,0.1);';
  const dimBtnStyle = btnStyle + 'color:var(--text-dim);cursor:default;';

  const pages = buildUEPages(uePage, totalPages);
  let html = '<div style="display:flex;align-items:center;justify-content:center;gap:4px;padding:12px 0;flex-wrap:wrap">';
  html += uePage > 1 ? `<button style="${btnStyle}" onclick="loadUE(${uePage-1})">‹</button>` : `<button style="${dimBtnStyle}" disabled>‹</button>`;
  for (const p of pages) {
    if (p === '...') html += `<span style="${dimBtnStyle}">…</span>`;
    else if (p === uePage) html += `<button style="${activeBtnStyle}">${p}</button>`;
    else html += `<button style="${btnStyle}" onclick="loadUE(${p})">${p}</button>`;
  }
  html += uePage < totalPages ? `<button style="${btnStyle}" onclick="loadUE(${uePage+1})">›</button>` : `<button style="${dimBtnStyle}" disabled>›</button>`;
  html += `<span style="margin-left:12px;font-size:11px;color:var(--text-dim)">стр. ${uePage} из ${totalPages}</span></div>`;
  el.innerHTML = html;
}

function buildUEPages(current, total) {
  if (total <= 7) return Array.from({length: total}, (_, i) => i + 1);
  const around = new Set([1, total, current, current-1, current+1, current-2, current+2]);
  const sorted = [...around].filter(p => p >= 1 && p <= total).sort((a, b) => a - b);
  const pages = [];
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && sorted[i] - sorted[i-1] > 1) pages.push('...');
    pages.push(sorted[i]);
  }
  return pages;
}

async function syncUEProducts() {
  const btn = document.getElementById('ue-sync-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Запускаем...';
  try {
    await fetch('/api/unit-economics/sync', {method: 'POST'});
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const s = await fetch('/api/unit-economics/sync/status').then(r => r.json());
      if (s.running) {
        btn.textContent = `⏳ ${s.progress || 'Синхронизация...'}`;
      } else if (s.error) {
        showToast(`Ошибка: ${s.error}`, 'error');
        break;
      } else {
        showToast(`✓ Синхронизировано ${s.synced.toLocaleString('ru')} товаров`);
        loadUE(1);
        break;
      }
    }
  } catch {
    showToast('Ошибка синхронизации', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ Синхронизировать';
  }
}


// =====================================================================
// ИНИЦИАЛИЗАЦИЯ
// =====================================================================
loadUEFilters();
loadUE(1);
document.getElementById('ue-send-btn').addEventListener('click', sendPrices);