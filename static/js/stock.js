// =====================================================================
// ОСТАТКИ FBO (без UZSPACE)
// =====================================================================

let stockData = [];
let stockWarehouses = [];
let stockSortField = 'total_free';
let stockSortDir = 'desc';
let stockSearchTimer = null;

async function loadStock() {
  document.getElementById('stock-tbody').innerHTML =
    '<tr><td colspan="8" class="state-msg">ЗАГРУЗКА...</td></tr>';
  const btn = document.getElementById('stock-sync-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinning">⟳</span> Загружаем...';

  try {
    const data = await fetch('/api/stock/fbo').then(r => r.json());
    stockData = data.items || [];
    stockWarehouses = data.warehouses || [];

    const sel = document.getElementById('stock-warehouse');
    const cur = sel.value;
    sel.innerHTML = '<option value="">Все склады</option>';
    stockWarehouses.forEach(wh => {
      const opt = document.createElement('option');
      opt.value = wh; opt.textContent = wh;
      sel.appendChild(opt);
    });
    sel.value = cur;

    renderStock();
  } catch(e) {
    document.getElementById('stock-tbody').innerHTML =
      '<tr><td colspan="8" class="state-msg" style="color:var(--red)">ОШИБКА ЗАГРУЗКИ</td></tr>';
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⟳ Обновить';
  }
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

// Товар нужно отключить — есть и FBO и FBS остаток
function needsDisable(item) {
  return item.total_free > 0 && item.fbs_present > 0;
}

function sortStock(field) {
  if (stockSortField === field) stockSortDir = stockSortDir === 'desc' ? 'asc' : 'desc';
  else { stockSortField = field; stockSortDir = 'desc'; }
  renderStock();
}

function renderStock() {
  const wh = document.getElementById('stock-warehouse').value;
  let items = getFilteredStock();

  // Сортировка — "нужно отключить" всегда наверху
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

  document.getElementById('stock-count').textContent = `${items.length} позиций`;

  const tbody = document.getElementById('stock-tbody');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="state-msg">НЕТ ДАННЫХ</td></tr>';
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

    return `<tr style="${rowBg}cursor:pointer" onclick="openStockModal('${safeCode}')">
      <td class="code" style="color:var(--accent);font-size:11px">${item.item_code}${disable ? ' <span title="Сообщить Симе об отключении трансляции" style="color:var(--accent)">⚡</span>' : ''}</td>
      <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${item.item_name || '—'}</td>
      <td style="font-size:11px;color:var(--text-dim)">${item.brand || '—'}</td>
      <td style="text-align:right;font-weight:bold;color:${freeColor}">${free.toLocaleString('ru')}</td>
      <td style="text-align:right;color:var(--yellow,#f0a500)">${reserved.toLocaleString('ru')}</td>
      <td style="text-align:right;font-weight:bold;color:${fbsColor}">${item.fbs_present.toLocaleString('ru')}</td>
      <td style="text-align:right;color:var(--text-dim)">${promised.toLocaleString('ru')}</td>
      <td style="text-align:center;font-size:11px;color:var(--text-dim)">${whCount}</td>
    </tr>`;
  }).join('');
}

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
  // Только товары с FBO остатком > 0 (для отправки Симе об отключении трансляции)
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
`;
document.head.appendChild(style);

loadStock();
document.getElementById('stock-sync-btn').addEventListener('click', loadStock);
document.getElementById('stock-export-btn').addEventListener('click', exportStockCSV);
document.getElementById('stock-export-disable-btn').addEventListener('click', exportDisableCSV);