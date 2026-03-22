// =====================================================================
// ОСТАТКИ FBO
// =====================================================================

let stockData = [];
let stockWarehouses = [];
let stockSortField = 'total_free';
let stockSortDir = 'desc';
let stockSearchTimer = null;

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

    // Заполняем фильтр складов
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

function debounceStock() {
  clearTimeout(stockSearchTimer);
  stockSearchTimer = setTimeout(renderStock, 300);
}

function clearStockFilters() {
  document.getElementById('stock-search').value = '';
  document.getElementById('stock-warehouse').value = '';
  document.getElementById('stock-only-available').checked = false;
  renderStock();
}

function getFilteredStock() {
  const search = document.getElementById('stock-search').value.trim().toLowerCase();
  const wh = document.getElementById('stock-warehouse').value;
  const onlyAvail = document.getElementById('stock-only-available').checked;

  return stockData.filter(item => {
    if (search && !item.item_code.toLowerCase().includes(search) &&
        !(item.item_name || '').toLowerCase().includes(search)) return false;

    if (wh) {
      // Фильтр по складу — показываем только если есть остаток на этом складе
      const whData = item.warehouses[wh];
      if (!whData) return false;
      if (onlyAvail && whData.free <= 0) return false;
    } else {
      if (onlyAvail && item.total_free <= 0) return false;
    }
    return true;
  });
}

function sortStock(field) {
  if (stockSortField === field) stockSortDir = stockSortDir === 'desc' ? 'asc' : 'desc';
  else { stockSortField = field; stockSortDir = 'desc'; }
  renderStock();
}

function renderStock() {
  const wh = document.getElementById('stock-warehouse').value;
  let items = getFilteredStock();

  // Сортировка
  items = [...items].sort((a, b) => {
    let av, bv;
    if (stockSortField === 'total_all') {
      av = a.total_free + a.total_reserved + a.total_promised;
      bv = b.total_free + b.total_reserved + b.total_promised;
    } else if (stockSortField === 'item_code') {
      av = a.item_code; bv = b.item_code;
    } else {
      av = wh && a.warehouses[wh] ? a.warehouses[wh][stockSortField.replace('total_', '')] : a[stockSortField] || 0;
      bv = wh && b.warehouses[wh] ? b.warehouses[wh][stockSortField.replace('total_', '')] : b[stockSortField] || 0;
    }
    if (typeof av === 'string') return stockSortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return stockSortDir === 'asc' ? av - bv : bv - av;
  });

  document.getElementById('stock-count').textContent = `${items.length} позиций`;

  const tbody = document.getElementById('stock-tbody');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="state-msg">НЕТ ДАННЫХ</td></tr>';
    return;
  }

  tbody.innerHTML = items.map(item => {
    const free = wh && item.warehouses[wh] ? item.warehouses[wh].free : item.total_free;
    const reserved = wh && item.warehouses[wh] ? item.warehouses[wh].reserved : item.total_reserved;
    const promised = wh && item.warehouses[wh] ? item.warehouses[wh].promised : item.total_promised;
    const total = free + reserved + promised;
    const whCount = Object.keys(item.warehouses).length;

    const freeColor = free > 10 ? 'var(--green)' : free > 0 ? 'var(--yellow,#f0a500)' : 'var(--red,#dc3232)';

    return `<tr style="cursor:pointer" onclick="openStockModal('${item.item_code.replace(/'/g, "\\'")}')">
      <td class="code" style="color:var(--accent);font-size:11px">${item.item_code}</td>
      <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${item.item_name || '—'}</td>
      <td style="text-align:right;font-weight:bold;color:${freeColor}">${free.toLocaleString('ru')}</td>
      <td style="text-align:right;color:var(--yellow,#f0a500)">${reserved.toLocaleString('ru')}</td>
      <td style="text-align:right;color:var(--text-dim)">${promised.toLocaleString('ru')}</td>
      <td style="text-align:right">${total.toLocaleString('ru')}</td>
      <td style="text-align:center;font-size:11px;color:var(--text-dim)">${whCount} скл.</td>
    </tr>`;
  }).join('');
}

function openStockModal(itemCode) {
  const item = stockData.find(i => i.item_code === itemCode);
  if (!item) return;

  document.getElementById('modal-item-code').textContent = item.item_code;
  document.getElementById('modal-item-name').textContent = item.item_name || '';

  const whs = Object.entries(item.warehouses)
    .sort((a, b) => b[1].free - a[1].free);

  document.getElementById('modal-tbody').innerHTML = whs.map(([wh, d]) => {
    const total = d.free + d.reserved + d.promised;
    if (total === 0) return '';
    const freeColor = d.free > 10 ? 'var(--green)' : d.free > 0 ? 'var(--yellow,#f0a500)' : 'var(--text-dim)';
    return `<tr>
      <td style="font-size:12px">${wh}</td>
      <td style="text-align:right;font-weight:bold;color:${freeColor}">${d.free}</td>
      <td style="text-align:right;color:var(--yellow,#f0a500)">${d.reserved}</td>
      <td style="text-align:right;color:var(--text-dim)">${d.promised}</td>
    </tr>`;
  }).filter(Boolean).join('') || '<tr><td colspan="4" class="state-msg">Нет остатков</td></tr>';

  const modal = document.getElementById('stock-modal');
  modal.style.display = 'flex';
  modal.onclick = e => { if (e.target === modal) closeStockModal(); };
}

function closeStockModal() {
  document.getElementById('stock-modal').style.display = 'none';
}

function exportStockCSV() {
  const wh = document.getElementById('stock-warehouse').value;
  const items = getFilteredStock();
  if (!items.length) { showToast('Нет данных для экспорта', 'error'); return; }

  const BOM = '\uFEFF';
  const headers = ['Артикул', 'Название', 'Доступно', 'Резерв', 'Ожидается', 'Итого'];
  const rows = items.map(item => {
    const free = wh && item.warehouses[wh] ? item.warehouses[wh].free : item.total_free;
    const reserved = wh && item.warehouses[wh] ? item.warehouses[wh].reserved : item.total_reserved;
    const promised = wh && item.warehouses[wh] ? item.warehouses[wh].promised : item.total_promised;
    return [item.item_code, item.item_name || '', free, reserved, promised, free + reserved + promised];
  });

  const csv = BOM + [headers, ...rows].map(r => r.map(v => `"${v}"`).join(';')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `stock_fbo_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('✓ Файл скачан');
}

// Стили
const style = document.createElement('style');
style.textContent = `
.stock-wrap { padding: 20px; }
.analytics-toolbar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:0; }
.toolbar-left { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.toolbar-right { display:flex; gap:8px; align-items:center; }
`;
document.head.appendChild(style);

loadStock();
document.getElementById('stock-sync-btn').addEventListener('click', loadStock);
document.getElementById('stock-export-btn').addEventListener('click', exportStockCSV);