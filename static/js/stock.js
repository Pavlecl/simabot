// =====================================================================
// ОСТАТКИ FBO
// =====================================================================

let stockData = [];
let stockWarehouses = [];
let stockSortField = 'total_free';
let stockSortDir = 'desc';
let stockSearchTimer = null;
let stockDirection = ''; // '' | 'uzspace' | 'sima'

async function loadStock() {
  document.getElementById('stock-tbody').innerHTML =
    '<tr><td colspan="7" class="state-msg">ЗАГРУЗКА...</td></tr>';
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
      '<tr><td colspan="7" class="state-msg" style="color:var(--red)">ОШИБКА ЗАГРУЗКИ</td></tr>';
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⟳ Обновить';
  }
}

function setDirection(dir) {
  stockDirection = dir;
  document.getElementById('btn-dir-all').classList.toggle('active', dir === '');
  document.getElementById('btn-dir-uzspace').classList.toggle('active', dir === 'uzspace');
  document.getElementById('btn-dir-sima').classList.toggle('active', dir === 'sima');
  renderStock();
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
  setDirection('');
}

function getFilteredStock() {
  const search = document.getElementById('stock-search').value.trim().toLowerCase();
  const wh = document.getElementById('stock-warehouse').value;
  const onlyAvail = document.getElementById('stock-only-available').checked;

  return stockData.filter(item => {
    // Фильтр направления по бренду
    if (stockDirection === 'uzspace' && (item.brand || '').toUpperCase() !== 'UZSPACE') return false;
    if (stockDirection === 'sima' && (item.brand || '').toUpperCase() === 'UZSPACE') return false;

    if (search && !item.item_code.toLowerCase().includes(search) &&
        !(item.item_name || '').toLowerCase().includes(search)) return false;

    if (wh) {
      const whData = item.warehouses[wh];
      if (!whData) return false;
      if (onlyAvail && whData.free <= 0 && whData.reserved <= 0) return false;
    } else {
      if (onlyAvail && item.total_free <= 0 && item.total_reserved <= 0) return false;
    }
    return true;
  });
}

function isZeroingOut(item, wh) {
  if (wh) {
    const d = item.warehouses[wh];
    return d && d.free === 0 && d.reserved > 0;
  }
  return item.total_free === 0 && item.total_reserved > 0;
}

function isLow(item, wh) {
  if (wh) {
    const d = item.warehouses[wh];
    return d && d.free > 0 && d.free <= 5;
  }
  return item.total_free > 0 && item.total_free <= 5;
}

function sortStock(field) {
  if (stockSortField === field) stockSortDir = stockSortDir === 'desc' ? 'asc' : 'desc';
  else { stockSortField = field; stockSortDir = 'desc'; }
  renderStock();
}

function renderStock() {
  const wh = document.getElementById('stock-warehouse').value;
  const showPromised = document.getElementById('stock-show-promised').checked;
  let items = getFilteredStock();

  // Обновляем заголовок таблицы
  const colCount = showPromised ? 8 : 7;
  document.getElementById('stock-thead').innerHTML = `<tr>
    <th onclick="sortStock('item_code')" class="sortable">Артикул ↕</th>
    <th>Название</th>
    <th>Бренд</th>
    <th onclick="sortStock('total_free')" class="sortable" style="text-align:right">Доступно ↕</th>
    <th onclick="sortStock('total_reserved')" class="sortable" style="text-align:right">Резерв ↕</th>
    ${showPromised ? '<th onclick="sortStock(\'total_promised\')" class="sortable" style="text-align:right">Ожидается ↕</th>' : ''}
    <th onclick="sortStock('total_all')" class="sortable" style="text-align:right">Итого ↕</th>
    <th style="text-align:center">Склады</th>
  </tr>`;

  // Сортировка — обнуляющиеся сначала
  items = [...items].sort((a, b) => {
    // Обнуляющиеся всегда наверху
    const aZero = isZeroingOut(a, wh) ? 1 : 0;
    const bZero = isZeroingOut(b, wh) ? 1 : 0;
    if (aZero !== bZero) return bZero - aZero;

    let av, bv;
    if (stockSortField === 'total_all') {
      av = a.total_free + a.total_reserved + a.total_promised;
      bv = b.total_free + b.total_reserved + b.total_promised;
    } else if (stockSortField === 'item_code') {
      av = a.item_code; bv = b.item_code;
    } else {
      av = wh && a.warehouses[wh] ? (a.warehouses[wh][stockSortField.replace('total_', '')] || 0) : (a[stockSortField] || 0);
      bv = wh && b.warehouses[wh] ? (b.warehouses[wh][stockSortField.replace('total_', '')] || 0) : (b[stockSortField] || 0);
    }
    if (typeof av === 'string') return stockSortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return stockSortDir === 'asc' ? av - bv : bv - av;
  });

  document.getElementById('stock-count').textContent = `${items.length} позиций`;

  const tbody = document.getElementById('stock-tbody');
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="${colCount}" class="state-msg">НЕТ ДАННЫХ</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(item => {
    const free = wh && item.warehouses[wh] ? item.warehouses[wh].free : item.total_free;
    const reserved = wh && item.warehouses[wh] ? item.warehouses[wh].reserved : item.total_reserved;
    const promised = wh && item.warehouses[wh] ? item.warehouses[wh].promised : item.total_promised;
    const total = free + reserved + promised;
    const whCount = Object.keys(item.warehouses).length;

    const zeroing = isZeroingOut(item, wh);
    const low = isLow(item, wh);

    let rowBg = '';
    if (zeroing) rowBg = 'background:rgba(220,50,50,0.15);';
    else if (low) rowBg = 'background:rgba(255,106,0,0.08);';

    const freeColor = zeroing ? 'var(--red,#dc3232)' : free > 5 ? 'var(--green)' : free > 0 ? 'var(--yellow,#f0a500)' : 'var(--text-dim)';
    const safeCode = item.item_code.replace(/'/g, "\\'");

    return `<tr style="${rowBg}cursor:pointer" onclick="openStockModal('${safeCode}')">
      <td class="code" style="color:var(--accent);font-size:11px">${item.item_code}${zeroing ? ' <span style="color:var(--red,#dc3232);font-size:10px">⚠</span>' : ''}</td>
      <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${item.item_name || '—'}</td>
      <td style="font-size:11px;color:var(--text-dim)">${item.brand || '—'}</td>
      <td style="text-align:right;font-weight:bold;color:${freeColor}">${free.toLocaleString('ru')}</td>
      <td style="text-align:right;color:var(--yellow,#f0a500)">${reserved.toLocaleString('ru')}</td>
      ${showPromised ? `<td style="text-align:right;color:var(--text-dim)">${promised.toLocaleString('ru')}</td>` : ''}
      <td style="text-align:right">${total.toLocaleString('ru')}</td>
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
    const freeColor = d.free === 0 && d.reserved > 0 ? 'var(--red,#dc3232)' :
                      d.free > 5 ? 'var(--green)' : d.free > 0 ? 'var(--yellow,#f0a500)' : 'var(--text-dim)';
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

function buildCSV(items, wh) {
  const showPromised = document.getElementById('stock-show-promised').checked;
  const BOM = '\uFEFF';
  const headers = ['Артикул', 'Название', 'Бренд', 'Доступно', 'Резерв'];
  if (showPromised) headers.push('Ожидается');
  headers.push('Итого');

  const rows = items.map(item => {
    const free = wh && item.warehouses[wh] ? item.warehouses[wh].free : item.total_free;
    const reserved = wh && item.warehouses[wh] ? item.warehouses[wh].reserved : item.total_reserved;
    const promised = wh && item.warehouses[wh] ? item.warehouses[wh].promised : item.total_promised;
    const row = [item.item_code, item.item_name || '', item.brand || '', free, reserved];
    if (showPromised) row.push(promised);
    row.push(free + reserved + promised);
    return row;
  });

  return BOM + [headers, ...rows].map(r => r.map(v => `"${v}"`).join(';')).join('\n');
}

function exportStockCSV() {
  const wh = document.getElementById('stock-warehouse').value;
  const items = getFilteredStock();
  if (!items.length) { showToast('Нет данных для экспорта', 'error'); return; }
  const csv = buildCSV(items, wh);
  downloadCSV(csv, `stock_fbo_${new Date().toISOString().slice(0,10)}.csv`);
  showToast(`✓ Экспортировано ${items.length} позиций`);
}

function exportZeroingCSV() {
  const wh = document.getElementById('stock-warehouse').value;
  const items = getFilteredStock().filter(item => isZeroingOut(item, wh));
  if (!items.length) { showToast('Нет обнуляющихся позиций', 'error'); return; }
  const csv = buildCSV(items, wh);
  downloadCSV(csv, `stock_zeroing_${new Date().toISOString().slice(0,10)}.csv`);
  showToast(`✓ Экспортировано ${items.length} обнуляющихся позиций`);
}

function downloadCSV(csv, filename) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// Стили
const style = document.createElement('style');
style.textContent = `
.stock-wrap { padding: 20px; }
.analytics-toolbar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; }
.toolbar-left { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.toolbar-right { display:flex; gap:8px; align-items:center; }
.chart-toggle { display:flex; gap:4px; }
.chart-btn { background:var(--surface2); border:1px solid var(--border); padding:3px 10px; cursor:pointer; font-size:11px; color:var(--text-dim); font-family:monospace; }
.chart-btn.active { background:var(--accent); border-color:var(--accent); color:#000; }
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
document.getElementById('stock-export-zero-btn').addEventListener('click', exportZeroingCSV);