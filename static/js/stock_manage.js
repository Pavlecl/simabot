// =====================================================================
// УПРАВЛЕНИЕ ОСТАТКАМИ (не Сима-Ленд)
// =====================================================================

let smPage = 1;
let smTotal = 0;
let smItems = [];          // все загруженные из БД
let smFiltered = [];       // после фильтрации
let googleStock = {};
let smOverrides = {};
let smChecked = new Set(); // выбранные offer_id
let smSearchTimer = null;
const SM_PER_PAGE = 100;
let smSortField = null;
let smSortDir = 'desc';

// =====================================================================
// CONFIRM MODAL
// =====================================================================
function smConfirm(text, title = 'Подтвердите действие') {
  return new Promise(resolve => {
    const modal = document.getElementById('sm-confirm-modal');
    document.getElementById('sm-confirm-title').textContent = title;
    document.getElementById('sm-confirm-text').textContent = text;
    modal.style.display = 'flex';
    const ok = document.getElementById('sm-confirm-ok');
    const cancel = document.getElementById('sm-confirm-cancel');
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
// ЗАГРУЗКА ДАННЫХ
// =====================================================================
function debounceStockManage() {
  clearTimeout(smSearchTimer);
  smSearchTimer = setTimeout(() => loadStockManage(1), 300);
}

function clearSmFilters() {
  document.getElementById('sm-search').value = '';
  document.getElementById('sm-filter-fbs').value = '';
  document.getElementById('sm-filter-google').value = '';
  loadStockManage(1);
}

function applySmFilter() {
  applyFiltersAndRender();
}

async function loadStockManage(page = 1) {
  smPage = page;
  const search = document.getElementById('sm-search')?.value || '';

  try {
    const params = new URLSearchParams({ page: 1, per_page: 10000, search });
    const r = await fetch(`/api/stock-manage/items?${params}`).then(r => r.json());
    smItems = r.items || [];
    smTotal = r.total || 0;
    applyFiltersAndRender();
  } catch(e) {
    document.getElementById('sm-tbody').innerHTML = '<tr><td colspan="8" class="state-msg" style="color:var(--red)">ОШИБКА ЗАГРУЗКИ</td></tr>';
  }
}

function applyFiltersAndRender() {
  const filterFbs = document.getElementById('sm-filter-fbs')?.value || '';
  const filterGoogle = document.getElementById('sm-filter-google')?.value || '';

  smFiltered = smItems.filter(item => {
    if (filterFbs === 'has_fbs' && item.fbs <= 0) return false;
    if (filterFbs === 'no_fbs' && item.fbs > 0) return false;
    const gs = googleStock[item.offer_id];
    const gsNum = gs !== undefined ? Number(gs) : -1;
    if (filterGoogle === 'has_stock' && gsNum <= 0) return false;
    if (filterGoogle === 'no_stock' && gsNum > 0) return false;
    return true;
  });

  document.getElementById('sm-count').textContent = smFiltered.length ? `${smFiltered.length} позиций` : '';
  updateSelectedCount();
  renderStockManage();
  renderSmPagination();
}

// =====================================================================
// GOOGLE SHEETS
// =====================================================================
async function loadGoogleStock() {
  const btn = document.getElementById('sm-google-btn');
  const status = document.getElementById('sm-google-status');
  btn.disabled = true;
  btn.textContent = '⏳ Загружаем...';
  status.textContent = '';

  try {
    const r = await fetch('/api/stock-manage/google').then(r => r.json());
    if (r.ok) {
      googleStock = r.data || {};
      const count = Object.keys(googleStock).length;
      status.textContent = `✓ Google: ${count} артикулов`;
      status.style.color = 'var(--green)';
      applyFiltersAndRender();
      showToast(`✓ Загружено ${count} остатков из Google Sheets`);
    } else {
      status.textContent = `Ошибка: ${r.error}`;
      status.style.color = 'var(--red)';
      showToast(`Ошибка Google Sheets: ${r.error}`, 'error');
    }
  } catch(e) {
    showToast('Ошибка загрузки Google Sheets', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ Обновить склад';
  }
}

// =====================================================================
// OZON FBO/FBS
// =====================================================================
async function loadOzonStocks() {
  try {
    const r = await fetch('/api/stock-manage/sync-stocks', {method: 'POST'}).then(r => r.json());
    if (r.ok && r.stocks) {
      smItems = smItems.map(item => ({
        ...item,
        fbo: r.stocks[item.offer_id]?.fbo ?? item.fbo,
        fbs: r.stocks[item.offer_id]?.fbs ?? item.fbs,
      }));
      applyFiltersAndRender();
    }
  } catch(e) {
    console.error('Stock sync error:', e);
  }
}

// =====================================================================
// ЧЕКБОКСЫ
// =====================================================================
function toggleCheck(offerId, checked) {
  if (checked) smChecked.add(offerId);
  else smChecked.delete(offerId);
  updateSelectedCount();
  updateCheckAllState();
}

function toggleCheckAll(checked) {
  const visibleItems = getVisibleItems();
  visibleItems.forEach(item => {
    if (checked) smChecked.add(item.offer_id);
    else smChecked.delete(item.offer_id);
  });
  updateSelectedCount();
  renderStockManage();
}

function updateSelectedCount() {
  const el = document.getElementById('sm-selected-count');
  if (smChecked.size > 0) {
    el.textContent = `✓ Выбрано: ${smChecked.size}`;
  } else {
    el.textContent = '';
  }
}

function updateCheckAllState() {
  const checkbox = document.getElementById('sm-check-all');
  if (!checkbox) return;
  const visible = getVisibleItems();
  const checkedVisible = visible.filter(i => smChecked.has(i.offer_id)).length;
  checkbox.indeterminate = checkedVisible > 0 && checkedVisible < visible.length;
  checkbox.checked = visible.length > 0 && checkedVisible === visible.length;
}

// =====================================================================
// СОРТИРОВКА И КОПИРОВАНИЕ
// =====================================================================
function sortSm(field) {
  if (smSortField === field) smSortDir = smSortDir === 'desc' ? 'asc' : 'desc';
  else { smSortField = field; smSortDir = 'desc'; }
  renderStockManage();
}

function copySmCell(text, el) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = el.style.color;
    el.style.color = 'var(--green)';
    setTimeout(() => el.style.color = orig, 800);
  });
}

// =====================================================================
// РЕНДЕР ТАБЛИЦЫ
// =====================================================================
function getVisibleItems() {
  let items = [...smFiltered];
  if (smSortField) {
    items.sort((a, b) => {
      let av, bv;
      if (smSortField === 'fbo') { av = a.fbo; bv = b.fbo; }
      else if (smSortField === 'fbs') { av = a.fbs; bv = b.fbs; }
      else if (smSortField === 'google') {
        av = googleStock[a.offer_id] ?? -1;
        bv = googleStock[b.offer_id] ?? -1;
      }
      return smSortDir === 'desc' ? bv - av : av - bv;
    });
  }
  // Пагинация
  const start = (smPage - 1) * SM_PER_PAGE;
  return items.slice(start, start + SM_PER_PAGE);
}

function renderStockManage() {
  const tbody = document.getElementById('sm-tbody');
  if (!smFiltered.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="state-msg">НЕТ ДАННЫХ — нажмите «Импорт из Google» для загрузки артикулов</td></tr>';
    return;
  }

  const visibleItems = getVisibleItems();

  tbody.innerHTML = visibleItems.map(item => {
    const warehouseStock = googleStock[item.offer_id] ?? '—';
    const currentOverride = smOverrides[item.offer_id];
    const displayValue = currentOverride !== undefined ? currentOverride
      : (item.fbs_override !== null && item.fbs_override !== undefined ? item.fbs_override : '');

    const fboColor = item.fbo > 10 ? 'var(--green)' : item.fbo > 0 ? 'var(--yellow,#f0a500)' : 'var(--text-dim)';
    const fbsColor = item.fbs > 0 ? 'var(--accent)' : 'var(--text-dim)';
    const warehouseColor = warehouseStock === 0 ? 'var(--red)' : warehouseStock === '—' ? 'var(--text-dim)' : 'var(--green)';
    const hasOverride = currentOverride !== undefined || (item.fbs_override !== null && item.fbs_override !== undefined);
    const isChecked = smChecked.has(item.offer_id);

    return `<tr style="${isChecked ? 'background:rgba(255,106,0,0.1)' : hasOverride ? 'background:rgba(255,106,0,0.04)' : ''}">
      <td style="text-align:center">
        <input type="checkbox" ${isChecked ? 'checked' : ''}
          onchange="toggleCheck('${item.offer_id}', this.checked)"
          style="width:15px;height:15px;cursor:pointer">
      </td>
      <td style="color:var(--accent);font-family:monospace;font-size:11px;cursor:pointer"
        onclick="copySmCell('${item.offer_id}', this)"
        title="Нажмите для копирования">${item.offer_id}</td>
      <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--text-dim)">${item.name || '—'}</td>
      <td style="text-align:right;font-weight:bold;color:${fboColor}">${item.fbo.toLocaleString('ru')}</td>
      <td style="text-align:right;color:${fbsColor}">${item.fbs.toLocaleString('ru')}</td>
      <td style="text-align:right;color:${warehouseColor};font-weight:bold">${warehouseStock !== '—' ? Number(warehouseStock).toLocaleString('ru') : '—'}</td>
      <td style="text-align:right;padding:4px 8px">
        <input type="number" min="0"
          value="${displayValue}"
          placeholder="${warehouseStock !== '—' ? warehouseStock : ''}"
          style="width:90px;padding:4px 8px;background:var(--surface2);border:1px solid var(--border);color:var(--text);font-size:12px;text-align:right"
          data-offer="${item.offer_id}"
          onchange="setOverride('${item.offer_id}', this.value)"
          oninput="setOverride('${item.offer_id}', this.value)">
      </td>
      <td style="text-align:center">
        <button class="btn" style="font-size:11px;padding:2px 8px;color:var(--text-dim)"
          onclick="clearOverride('${item.offer_id}')">✕</button>
      </td>
    </tr>`;
  }).join('');

  updateCheckAllState();
}

function setOverride(offerId, value) {
  const num = parseInt(value);
  if (!isNaN(num) && num >= 0) smOverrides[offerId] = num;
  else if (value === '' || value === null) delete smOverrides[offerId];
}

function clearOverride(offerId) {
  delete smOverrides[offerId];
  const input = document.querySelector(`input[data-offer="${offerId}"]`);
  if (input) input.value = '';
  renderStockManage();
}

// selectedOnly=true — только выбранные; false — все видимые
function applyAdjustment(selectedOnly = false) {
  const pct = parseFloat(document.getElementById('sm-adjust-pct').value);
  if (isNaN(pct)) { showToast('Введите корректный %', 'error'); return; }

  const targets = selectedOnly
    ? smFiltered.filter(item => smChecked.has(item.offer_id))
    : smFiltered;

  if (selectedOnly && targets.length === 0) {
    showToast('Нет выбранных артикулов', 'error');
    return;
  }

  targets.forEach(item => {
    const base = googleStock[item.offer_id];
    if (base !== undefined && base !== null && base !== '—') {
      const adjusted = Math.max(0, Math.round(Number(base) * (1 + pct / 100)));
      smOverrides[item.offer_id] = adjusted;
    }
  });

  renderStockManage();
  showToast(`✓ Корректировка ${pct > 0 ? '+' : ''}${pct}% применена к ${targets.length} позициям`);
}

// =====================================================================
// ПЕРЕДАЧА FBS В OZON
// =====================================================================
async function pushFbsToOzon() {
  const hasChecked = smChecked.size > 0;

  // Если есть выбранные — передаём только их, иначе все с данными
  const targets = hasChecked
    ? smFiltered.filter(item => smChecked.has(item.offer_id))
    : smFiltered;

  const stocks = [];
  targets.forEach(item => {
    let value = smOverrides[item.offer_id];
    if (value === undefined) {
      const gs = googleStock[item.offer_id];
      if (gs !== undefined && gs !== '—') value = Number(gs);
    }
    if (value !== undefined && value !== null && !isNaN(value)) {
      stocks.push({ offer_id: item.offer_id, stock: parseInt(value) });
    }
  });

  if (!stocks.length) {
    showToast('Нет данных для передачи. Обновите склад из Google или введите значения вручную.', 'error');
    return;
  }

  const scope = hasChecked ? `${smChecked.size} выбранных` : `всех ${stocks.length}`;
  if (!await smConfirm(
    `Передать FBS остатки для ${scope} позиций в Ozon?\nЭто обновит реальные остатки на маркетплейсе.`,
    'Передача остатков в Ozon'
  )) return;

  const btn = document.getElementById('sm-push-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Передаём...';

  try {
    const r = await fetch('/api/stock-manage/push-fbs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ stocks })
    }).then(r => r.json());

    if (r.errors && r.errors.length > 0) {
      showToast(`⚠ Обновлено ${r.total - r.errors.length} из ${r.total}, ошибок: ${r.errors.length}`, 'error');
    } else {
      showToast(`✓ FBS обновлён для ${r.total} позиций`);
      smOverrides = {};
      smChecked.clear();
      await loadStockManage(smPage);
    }
  } catch {
    showToast('Ошибка передачи в Ozon', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '→ Передать FBS в Ozon';
  }
}

// =====================================================================
// ЭКСПОРТ / ИМПОРТ
// =====================================================================
async function exportStockList() {
  const btn = document.getElementById('sm-export-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Готовим...';
  try {
    const resp = await fetch('/api/stock-manage/export-list');
    if (!resp.ok) throw new Error();
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `stock_items_${new Date().toISOString().slice(0,10)}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('✓ Файл скачан');
  } catch {
    showToast('Ошибка экспорта', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '↓ Список артикулов';
  }
}

async function importStockList(input) {
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  try {
    const r = await fetch('/api/stock-manage/import-list', { method: 'POST', body: formData }).then(r => r.json());
    showToast(`✓ Включено ${r.enabled} артикулов`);
    input.value = '';
    await loadStockManage(1);
  } catch {
    showToast('Ошибка импорта', 'error');
  }
}

async function importFromGoogle() {
  if (!await smConfirm('Импортировать все 717 артикулов из Google Sheets?', 'Импорт из Google')) return;
  const btn = document.getElementById('sm-google-import-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Импортируем...';
  try {
    const r = await fetch('/api/stock-manage/import-from-google', { method: 'POST' }).then(r => r.json());
    showToast(`✓ Импортировано ${r.added} артикулов`);
    await loadStockManage(1);
  } catch {
    showToast('Ошибка импорта', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '↓ Импорт из Google';
  }
}

// =====================================================================
// ПАГИНАЦИЯ
// =====================================================================
function renderSmPagination() {
  const el = document.getElementById('sm-pagination');
  const totalPages = Math.ceil(smFiltered.length / SM_PER_PAGE);
  if (totalPages <= 1) { el.innerHTML = ''; return; }

  const btnStyle = 'display:inline-flex;align-items:center;justify-content:center;min-width:32px;height:32px;padding:0 8px;margin:0 2px;border:1px solid var(--border);background:var(--surface);color:var(--text);cursor:pointer;font-size:12px;';
  const activeBtnStyle = btnStyle + 'border-color:var(--accent);color:var(--accent);background:rgba(255,106,0,0.1);';
  const dimBtnStyle = btnStyle + 'color:var(--text-dim);cursor:default;';

  let html = '<div style="display:flex;align-items:center;justify-content:center;gap:4px;padding:16px 0;flex-wrap:wrap">';
  html += smPage > 1 ? `<button style="${btnStyle}" onclick="goSmPage(${smPage-1})">‹</button>` : `<button style="${dimBtnStyle}" disabled>‹</button>`;

  const pages = buildSmPages(smPage, totalPages);
  for (const p of pages) {
    if (p === '...') html += `<span style="${dimBtnStyle}">…</span>`;
    else if (p === smPage) html += `<button style="${activeBtnStyle}">${p}</button>`;
    else html += `<button style="${btnStyle}" onclick="goSmPage(${p})">${p}</button>`;
  }

  html += smPage < totalPages ? `<button style="${btnStyle}" onclick="goSmPage(${smPage+1})">›</button>` : `<button style="${dimBtnStyle}" disabled>›</button>`;
  html += `<span style="margin-left:12px;font-size:11px;color:var(--text-dim)">стр. ${smPage} из ${totalPages}</span></div>`;
  el.innerHTML = html;
}

function goSmPage(page) {
  smPage = page;
  renderStockManage();
  renderSmPagination();
}

function buildSmPages(current, total) {
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

// =====================================================================
// СТИЛИ
// =====================================================================
const style = document.createElement('style');
style.textContent = `
.stock-manage-wrap { padding: 20px; }
.analytics-toolbar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; }
.toolbar-left { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.toolbar-right { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.analytics-table-wrap { background:var(--surface); border:1px solid var(--border); padding:16px; overflow-x:auto; }
.data-table { width:100%; border-collapse:collapse; }
.data-table th, .data-table td { padding:8px 12px; border-bottom:1px solid var(--border); text-align:left; white-space:nowrap; }
.data-table thead th { position:sticky; top:0; background:var(--surface); font-size:10px; text-transform:uppercase; letter-spacing:1px; color:var(--text-dim); }
.data-table thead th.sortable { cursor:pointer; user-select:none; }
.data-table thead th.sortable:hover { color:var(--accent); }
input[type=checkbox] { accent-color: var(--accent); }
input[type=number]::-webkit-inner-spin-button { opacity: 0.5; }
`;
document.head.appendChild(style);

// =====================================================================
// ИНИЦИАЛИЗАЦИЯ
// =====================================================================
loadStockManage(1);
loadGoogleStock();
loadOzonStocks();
document.getElementById('sm-export-btn').addEventListener('click', exportStockList);
document.getElementById('sm-google-btn').addEventListener('click', loadGoogleStock);
document.getElementById('sm-google-import-btn').addEventListener('click', importFromGoogle);
document.getElementById('sm-push-btn').addEventListener('click', pushFbsToOzon);