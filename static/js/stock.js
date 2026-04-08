// =====================================================================
// ОСТАТКИ FBO
// =====================================================================

let stockData = [];
let stockWarehouses = [];
let stockWatchlist = [];
let stockSortField = 'total_free';
let stockSortDir = 'desc';
let stockSearchTimer = null;

// Состояние сворачивания — сохраняем в sessionStorage
const collapseState = {
  main: sessionStorage.getItem('stock_main_collapsed') === '1',
  watchlist: sessionStorage.getItem('stock_watchlist_collapsed') === '1',
};

function showConfirm(text, title = 'Подтвердите действие') {
  return new Promise(resolve => {
    const modal = document.getElementById('confirm-modal');
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-text').textContent = text;
    modal.style.display = 'flex';

    const ok = document.getElementById('confirm-ok');
    const cancel = document.getElementById('confirm-cancel');

    function close(result) {
      modal.style.display = 'none';
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
      modal.removeEventListener('click', onBg);
      resolve(result);
    }
    const onOk = () => close(true);
    const onCancel = () => close(false);
    const onBg = e => { if (e.target === modal) close(false); };

    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
    modal.addEventListener('click', onBg);
  });
}

function toggleSection(section) {
  collapseState[section] = !collapseState[section];
  sessionStorage.setItem(`stock_${section}_collapsed`, collapseState[section] ? '1' : '0');
  applyCollapseState(section);
}

function applyCollapseState(section) {
  const body = document.getElementById(section === 'main' ? 'main-table-body' : 'watchlist-table-body');
  const icon = document.getElementById(section === 'main' ? 'main-collapse-icon' : 'watchlist-collapse-icon');
  if (!body || !icon) return;

  const collapsed = collapseState[section];
  body.style.display = collapsed ? 'none' : '';
  icon.textContent = collapsed ? '▼' : '▲';
  icon.style.opacity = collapsed ? '0.4' : '1';
}

function initCollapseState() {
  applyCollapseState('main');
  applyCollapseState('watchlist');
}

// =====================================================================
// ЗАГРУЗКА ДАННЫХ
// =====================================================================

async function loadStock() {
  const btn = document.getElementById('stock-sync-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinning">⟳</span> Загружаем...';

  try {
    await fetch('/api/stock/fbo/sync', {method: 'POST'});

    let attempts = 0;
    while (attempts < 60) {
      await new Promise(r => setTimeout(r, 2000));
      const data = await fetch('/api/stock/fbo').then(r => r.json());

      if (!data.loading && data.total > 0) {
        stockData = data.items || [];
        stockWarehouses = data.warehouses || [];
        stockWatchlist = data.watchlist || [];
        fillWarehouseFilter();
        renderStock();
        renderWatchlist();
        if (data.updated_at) {
          document.getElementById('stock-count').textContent = `Обновлено: ${data.updated_at}`;
        }
        break;
      }
      attempts++;
    }
  } catch(e) {
    document.getElementById('stock-tbody').innerHTML =
      '<tr><td colspan="9" class="state-msg" style="color:var(--red)">ОШИБКА ЗАГРУЗКИ</td></tr>';
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⟳ Обновить';
  }
}

function fillWarehouseFilter() {
  const sel = document.getElementById('stock-warehouse');
  const cur = sel.value;
  sel.innerHTML = '<option value="">Все склады</option>';
  stockWarehouses.forEach(wh => {
    const opt = document.createElement('option');
    opt.value = wh; opt.textContent = wh;
    sel.appendChild(opt);
  });
  sel.value = cur;
}

function debounceStock() {
  clearTimeout(stockSearchTimer);
  stockSearchTimer = setTimeout(renderStock, 300);
}

function clearStockFilters() {
  document.getElementById('stock-search').value = '';
  document.getElementById('stock-warehouse').value = '';
  document.getElementById('stock-only-available').checked = false;
  document.getElementById('stock-show-promised').checked = false;
  renderStock();
}

function getFilteredStock() {
  const search = document.getElementById('stock-search').value.trim().toLowerCase();
  const wh = document.getElementById('stock-warehouse').value;
  const onlyAvail = document.getElementById('stock-only-available').checked;
  const onlyPromised = document.getElementById('stock-show-promised').checked;

  return stockData.filter(item => {
    if (search && !item.item_code.toLowerCase().includes(search) &&
        !(item.item_name || '').toLowerCase().includes(search)) return false;
    if (wh) {
      const whData = item.warehouses[wh];
      if (!whData) return false;
      if (onlyAvail && whData.free <= 0 && whData.reserved <= 0) return false;
      if (onlyPromised && whData.promised <= 0) return false;
    } else {
      if (onlyAvail && item.total_free <= 0 && item.total_reserved <= 0) return false;
      if (onlyPromised && item.total_promised <= 0) return false;
    }
    return true;
  });
}

function needsDisable(item) {
  return item.total_free > 0 && item.fbs_present > 0;
}

function sortStock(field) {
  if (stockSortField === field) stockSortDir = stockSortDir === 'desc' ? 'asc' : 'desc';
  else { stockSortField = field; stockSortDir = 'desc'; }
  renderStock();
}

// =====================================================================
// РЕНДЕР ОСНОВНОЙ ТАБЛИЦЫ
// =====================================================================

function renderStock() {
  const wh = document.getElementById('stock-warehouse').value;
  let items = getFilteredStock();

  items = [...items].sort((a, b) => {
    const aDis = needsDisable(a) ? 1 : 0;
    const bDis = needsDisable(b) ? 1 : 0;
    if (aDis !== bDis) return bDis - aDis;

    let av, bv;
    if (stockSortField === 'item_code') { av = a.item_code; bv = b.item_code; }
    else if (stockSortField === 'fbs_present') { av = a.fbs_present; bv = b.fbs_present; }
    else {
      av = wh && a.warehouses[wh] ? (a.warehouses[wh][stockSortField.replace('total_', '')] || 0) : (a[stockSortField] || 0);
      bv = wh && b.warehouses[wh] ? (b.warehouses[wh][stockSortField.replace('total_', '')] || 0) : (b[stockSortField] || 0);
    }
    if (typeof av === 'string') return stockSortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return stockSortDir === 'asc' ? av - bv : bv - av;
  });

  // Бейдж с количеством в заголовке секции
  const disableCount = items.filter(needsDisable).length;
  const badge = document.getElementById('main-table-badge');
  badge.textContent = `${items.length} позиций${disableCount ? ` · ⚡ ${disableCount} к отключению` : ''}`;

  document.getElementById('stock-count').textContent = `${items.length} позиций`;

  const tbody = document.getElementById('stock-tbody');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="state-msg">НЕТ ДАННЫХ</td></tr>';
    return;
  }

  tbody.innerHTML = items.map(item => {
    const free = wh && item.warehouses[wh] ? item.warehouses[wh].free : item.total_free;
    const reserved = wh && item.warehouses[wh] ? item.warehouses[wh].reserved : item.total_reserved;
    const promised = wh && item.warehouses[wh] ? item.warehouses[wh].promised : item.total_promised;
    const whCount = Object.keys(item.warehouses).length;
    const disable = needsDisable(item);

    let rowBg = '';
    if (disable) rowBg = 'background:rgba(255,106,0,0.12);';
    else if (item.total_free > 0) rowBg = 'background:rgba(100,200,100,0.06);';

    const freeColor = free > 10 ? 'var(--green)' : free > 0 ? 'var(--yellow,#f0a500)' : 'var(--text-dim)';
    const fbsColor = item.fbs_present > 0 ? 'var(--accent)' : 'var(--text-dim)';
    const safeCode = item.item_code.replace(/'/g, "\\'");

    return `<tr style="${rowBg}">
      <td style="text-align:center" onclick="event.stopPropagation()">
        <input type="checkbox" class="main-check" value="${item.item_code}" onchange="onMainCheckChange()">
      </td>
      <td class="code" style="color:var(--accent);font-size:11px;cursor:pointer" onclick="openStockModal('${safeCode}')">${item.item_code}${disable ? ' <span title="Сообщить Симе об отключении трансляции" style="color:var(--accent)">⚡</span>' : ''}</td>
      <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;cursor:pointer" onclick="openStockModal('${safeCode}')">${item.item_name || '—'}</td>
      <td style="font-size:11px;color:var(--text-dim);cursor:pointer" onclick="openStockModal('${safeCode}')">${item.brand || '—'}</td>
      <td style="text-align:right;font-weight:bold;color:${freeColor};cursor:pointer" onclick="openStockModal('${safeCode}')">${free.toLocaleString('ru')}</td>
      <td style="text-align:right;color:var(--yellow,#f0a500);cursor:pointer" onclick="openStockModal('${safeCode}')">${reserved.toLocaleString('ru')}</td>
      <td style="text-align:right;font-weight:bold;color:${fbsColor};cursor:pointer" onclick="openStockModal('${safeCode}')">${item.fbs_present.toLocaleString('ru')}</td>
      <td style="text-align:right;color:var(--text-dim);cursor:pointer" onclick="openStockModal('${safeCode}')">${promised.toLocaleString('ru')}</td>
      <td style="text-align:center;font-size:11px;color:var(--text-dim);cursor:pointer" onclick="openStockModal('${safeCode}')">${whCount}</td>
    </tr>`;
  }).join('');

  // Сбрасываем чекбокс "выбрать все"
  const allCheck = document.getElementById('main-check-all');
  if (allCheck) { allCheck.checked = false; allCheck.indeterminate = false; }
  updateZeroFbsBtn();
}

// =====================================================================
// ЧЕКБОКСЫ ОСНОВНОЙ ТАБЛИЦЫ + КНОПКА ОБНУЛИТЬ FBS
// =====================================================================

function onMainCheckChange() {
  const all = document.querySelectorAll('.main-check');
  const checked = document.querySelectorAll('.main-check:checked');
  const allCheck = document.getElementById('main-check-all');
  if (allCheck) {
    allCheck.indeterminate = checked.length > 0 && checked.length < all.length;
    allCheck.checked = checked.length === all.length && all.length > 0;
  }
  updateZeroFbsBtn();
}

function toggleAllMain(checked) {
  document.querySelectorAll('.main-check').forEach(cb => cb.checked = checked);
  updateZeroFbsBtn();
}

function updateZeroFbsBtn() {
  const checked = document.querySelectorAll('.main-check:checked');
  const btn = document.getElementById('stock-zero-fbs-btn');
  if (checked.length > 0) {
    btn.style.display = '';
    btn.textContent = `⊘ Обнулить FBS (${checked.length})`;
  } else {
    btn.style.display = 'none';
  }
}

async function zeroFbsSelected() {
  const checked = [...document.querySelectorAll('.main-check:checked')].map(cb => cb.value);
  if (!checked.length) return;

  if (!await showConfirm(
    `Обнулить FBS остаток для ${checked.length} позиций на складе Ozon?\nПосле обновления они перейдут в список «К подключению FBS».`,
    'Обнуление FBS остатка'
  )) return;

  const btn = document.getElementById('stock-zero-fbs-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Отправляем...';

  try {
    const resp = await fetch('/api/stock/fbs/zero', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({offer_ids: checked})
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Ошибка');

    if (data.errors && data.errors.length > 0) {
      showToast(`⚠ Обновлено ${data.total - data.errors.length} из ${data.total}, ошибок: ${data.errors.length}`, 'error');
    } else {
      showToast(`✓ FBS обнулён для ${data.total} позиций. Нажмите «Обновить» чтобы увидеть изменения.`);
    }

    // Снимаем чекбоксы
    document.querySelectorAll('.main-check:checked').forEach(cb => cb.checked = false);
    const allCheck = document.getElementById('main-check-all');
    if (allCheck) { allCheck.checked = false; allCheck.indeterminate = false; }
    updateZeroFbsBtn();

  } catch(e) {
    showToast(`Ошибка: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    updateZeroFbsBtn();
  }
}

// =====================================================================
// WATCHLIST
// =====================================================================

function renderWatchlist() {
  const section = document.getElementById('watchlist-section');
  const tbody = document.getElementById('watchlist-tbody');
  const countEl = document.getElementById('watchlist-count');

  if (!stockWatchlist.length) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  countEl.textContent = `${stockWatchlist.length} позиций`;

  if (sessionStorage.getItem('stock_watchlist_collapsed') === null) {
    collapseState.watchlist = false;
  }
  applyCollapseState('watchlist');

  tbody.innerHTML = stockWatchlist.map(w => {
    const notedDate = w.noted_at
      ? new Date(w.noted_at).toLocaleString('ru', {day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'})
      : '—';
    const safeId = w.offer_id.replace(/'/g, "\\'");

    return `<tr style="background:rgba(100,220,100,0.06)">
      <td style="text-align:center">
        <input type="checkbox" class="wl-check" value="${w.offer_id}" onchange="onWatchlistCheckChange()">
      </td>
      <td style="color:var(--green,#64c864);font-family:monospace;font-size:11px">${w.offer_id}</td>
      <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${w.item_name || '—'}</td>
      <td style="font-size:11px;color:var(--text-dim)">${w.brand || '—'}</td>
      <td style="text-align:right;font-size:11px;color:var(--text-dim)">${notedDate}</td>
      <td style="text-align:center">
        <button class="btn" style="padding:2px 8px;font-size:11px;border-color:rgba(255,60,60,0.4);color:#ff6b6b"
          onclick="deleteWatchlistItem('${safeId}')">✕</button>
      </td>
    </tr>`;
  }).join('');

  updateWatchlistDeleteBtn();
}

function onWatchlistCheckChange() {
  const all = document.querySelectorAll('.wl-check');
  const checked = document.querySelectorAll('.wl-check:checked');
  const allCheck = document.getElementById('watchlist-check-all');
  allCheck.indeterminate = checked.length > 0 && checked.length < all.length;
  allCheck.checked = checked.length === all.length && all.length > 0;
  updateWatchlistDeleteBtn();
}

function toggleAllWatchlist(checked) {
  document.querySelectorAll('.wl-check').forEach(cb => cb.checked = checked);
  updateWatchlistDeleteBtn();
}

function updateWatchlistDeleteBtn() {
  const checked = document.querySelectorAll('.wl-check:checked');
  const btn = document.getElementById('watchlist-delete-btn');
  if (checked.length > 0) {
    btn.style.display = '';
    btn.textContent = `🗑 Удалить выбранные (${checked.length})`;
  } else {
    btn.style.display = 'none';
  }
}

async function deleteWatchlistItem(offerId) {
  if (!await showConfirm(`Удалить артикул ${offerId} из списка наблюдения?`)) return;
  try {
    const resp = await fetch(`/api/stock/watchlist/${encodeURIComponent(offerId)}`, {method: 'DELETE'});
    if (!resp.ok) throw new Error();
    stockWatchlist = stockWatchlist.filter(w => w.offer_id !== offerId);
    renderWatchlist();
    showToast('✓ Удалено из списка');
  } catch {
    showToast('Ошибка удаления', 'error');
  }
}

async function deleteWatchlistBulk() {
  const checked = [...document.querySelectorAll('.wl-check:checked')].map(cb => cb.value);
  if (!checked.length) return;
  if (!await showConfirm(`Удалить ${checked.length} позиций из списка наблюдения?`)) return;
  try {
    const resp = await fetch('/api/stock/watchlist', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({offer_ids: checked})
    });
    if (!resp.ok) throw new Error();
    stockWatchlist = stockWatchlist.filter(w => !checked.includes(w.offer_id));
    renderWatchlist();
    showToast(`✓ Удалено ${checked.length} позиций`);
  } catch {
    showToast('Ошибка удаления', 'error');
  }
}

function exportWatchlistCSV() {
  if (!stockWatchlist.length) { showToast('Список пуст', 'error'); return; }
  const BOM = '\uFEFF';
  const headers = ['Артикул', 'Название', 'Бренд', 'Дата обнуления FBO'];
  const rows = stockWatchlist.map(w => [
    w.offer_id, w.item_name || '', w.brand || '',
    w.noted_at ? new Date(w.noted_at).toLocaleString('ru') : ''
  ]);
  const csv = BOM + [headers, ...rows].map(r => r.map(v => `"${v}"`).join(';')).join('\n');
  downloadCSV(csv, `stock_connect_fbs_${new Date().toISOString().slice(0,10)}.csv`);
  showToast(`✓ Экспортировано ${stockWatchlist.length} позиций для подключения FBS`);
}

// =====================================================================
// MODAL
// =====================================================================

function openStockModal(itemCode) {
  const item = stockData.find(i => i.item_code === itemCode);
  if (!item) return;

  document.getElementById('modal-item-code').textContent = item.item_code;
  document.getElementById('modal-item-name').textContent = item.item_name || '';

  const whs = Object.entries(item.warehouses)
    .filter(([, d]) => d.free + d.reserved + d.promised > 0)
    .sort((a, b) => b[1].free - a[1].free);

  document.getElementById('modal-tbody').innerHTML = whs.map(([wh, d]) => {
    const freeColor = d.free > 5 ? 'var(--green)' : d.free > 0 ? 'var(--yellow,#f0a500)' : 'var(--text-dim)';
    return `<tr>
      <td style="font-size:12px">${wh}</td>
      <td style="text-align:right;font-weight:bold;color:${freeColor}">${d.free}</td>
      <td style="text-align:right;color:var(--yellow,#f0a500)">${d.reserved}</td>
      <td style="text-align:right;color:var(--text-dim)">${d.promised}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="4" class="state-msg">Нет остатков</td></tr>';

  const modal = document.getElementById('stock-modal');
  modal.style.display = 'flex';
  modal.onclick = e => { if (e.target === modal) closeStockModal(); };
}

function closeStockModal() {
  document.getElementById('stock-modal').style.display = 'none';
}

// =====================================================================
// CSV EXPORT
// =====================================================================

function buildCSV(items) {
  const BOM = '\uFEFF';
  const headers = ['Артикул', 'Название', 'Бренд', 'FBO Доступно', 'FBO Резерв', 'FBS Остаток', 'Ожидается'];
  const rows = items.map(item => [
    item.item_code, item.item_name || '', item.brand || '',
    item.total_free, item.total_reserved, item.fbs_present, item.total_promised
  ]);
  return BOM + [headers, ...rows].map(r => r.map(v => `"${v}"`).join(';')).join('\n');
}

function exportStockCSV() {
  const items = getFilteredStock();
  if (!items.length) { showToast('Нет данных', 'error'); return; }
  downloadCSV(buildCSV(items), `stock_fbo_${new Date().toISOString().slice(0,10)}.csv`);
  showToast(`✓ Экспортировано ${items.length} позиций`);
}

function exportDisableCSV() {
  const items = getFilteredStock().filter(item => item.total_free > 0);
  if (!items.length) { showToast('Нет позиций с остатком', 'error'); return; }
  const BOM = '\uFEFF';
  const headers = ['Артикул', 'Название', 'FBO Доступно', 'FBS Остаток'];
  const rows = items.map(item => [item.item_code, item.item_name || '', item.total_free, item.fbs_present]);
  const csv = BOM + [headers, ...rows].map(r => r.map(v => `"${v}"`).join(';')).join('\n');
  downloadCSV(csv, `stock_disable_${new Date().toISOString().slice(0,10)}.csv`);
  showToast(`✓ Экспортировано ${items.length} позиций для отключения`);
}

function downloadCSV(csv, filename) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// =====================================================================
// СТИЛИ
// =====================================================================

const style = document.createElement('style');
style.textContent = `
.stock-wrap { padding: 20px; }
.analytics-toolbar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; }
.toolbar-left { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.toolbar-right { display:flex; gap:8px; align-items:center; }
.analytics-table-wrap { background:var(--surface); border:1px solid var(--border); padding:16px; overflow-x:auto; margin-top:8px; }
.data-table { width:100%; border-collapse:collapse; }
.data-table th, .data-table td { padding:8px 12px; border-bottom:1px solid var(--border); text-align:left; white-space:nowrap; }
.data-table thead th { position:sticky; top:0; background:var(--surface); font-size:10px; text-transform:uppercase; letter-spacing:1px; color:var(--text-dim); }
.sortable { cursor:pointer; }
.sortable:hover { color:var(--accent); }
.section-header:hover { filter: brightness(1.1); }
.collapsible-body { transition: none; }
#watchlist-section .analytics-table-wrap { border-color:rgba(100,220,100,0.25); }
#watchlist-section .data-table thead th { color:rgba(100,220,100,0.6); }
`;
document.head.appendChild(style);

// =====================================================================
// ИНИЦИАЛИЗАЦИЯ
// =====================================================================

initCollapseState();
loadStock();
document.getElementById('stock-sync-btn').addEventListener('click', loadStock);
document.getElementById('stock-export-btn').addEventListener('click', exportStockCSV);
document.getElementById('stock-export-disable-btn').addEventListener('click', exportDisableCSV);
document.getElementById('watchlist-export-btn').addEventListener('click', exportWatchlistCSV);
document.getElementById('watchlist-delete-btn').addEventListener('click', deleteWatchlistBulk);
document.getElementById('stock-zero-fbs-btn').addEventListener('click', zeroFbsSelected);