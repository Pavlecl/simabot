// =====================================================================
// АНАЛИТИКА ПРОДАЖ
// =====================================================================

let analyticsData = null;   // данные из sales_history (для карточек и графика штуки/выручка)
let ozonTopData = null;     // данные из Ozon Analytics API (для таблицы)
let chartInstance = null;
let chartMode = 'orders';
let tableMode = 'orders';
let sortField = 'ordered_units';
let sortDir = 'desc';
let analyticsSearchTimer = null;

(function setDefaultDates() {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 30);
  document.getElementById('date-to').value = to.toISOString().slice(0, 10);
  document.getElementById('date-from').value = from.toISOString().slice(0, 10);
})();

function debounceAnalytics() {
  clearTimeout(analyticsSearchTimer);
  analyticsSearchTimer = setTimeout(loadAnalytics, 400);
}

function clearAnalyticsFilters() {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 30);
  document.getElementById('date-to').value = to.toISOString().slice(0, 10);
  document.getElementById('date-from').value = from.toISOString().slice(0, 10);
  document.getElementById('status-filter').value = '';
  document.getElementById('direction-filter').value = '';
  document.getElementById('brand-filter').value = '';
  document.getElementById('category-filter').value = '';
  document.getElementById('search-input').value = '';
  loadAnalytics();
}

function getDateParams() {
  const dateFrom = document.getElementById('date-from').value;
  const dateTo = document.getElementById('date-to').value;
  return { dateFrom, dateTo };
}

function buildSalesUrl() {
  const { dateFrom, dateTo } = getDateParams();
  const status = document.getElementById('status-filter').value;
  const brand = document.getElementById('brand-filter').value;
  const categoryId = document.getElementById('category-filter').value;
  const search = document.getElementById('search-input').value.trim();

  let url = '/api/analytics/sales?';
  if (dateFrom) url += `date_from=${dateFrom}&`;
  if (dateTo) url += `date_to=${dateTo}&`;
  if (status) url += `status=${status}&`;
  if (brand) url += `brand=${encodeURIComponent(brand)}&`;
  if (categoryId) url += `category_id=${categoryId}&`;
  if (search) url += `search=${encodeURIComponent(search)}&`;
  return url;
}

function buildOzonTopUrl() {
  const { dateFrom, dateTo } = getDateParams();
  let url = '/api/analytics/top-by-orders?';
  if (dateFrom) url += `date_from=${dateFrom}&`;
  if (dateTo) url += `date_to=${dateTo}&`;
  return url;
}

async function loadAnalytics() {
  document.getElementById('analytics-tbody').innerHTML =
    '<tr><td colspan="8" class="state-msg">ЗАГРУЗКА...</td></tr>';

  // Загружаем параллельно: sales_history для карточек/графика и Ozon API для таблицы
  try {
    const [salesData, ozonData] = await Promise.all([
      fetch(buildSalesUrl()).then(r => r.json()),
      fetch(buildOzonTopUrl()).then(r => r.json()),
    ]);

    analyticsData = salesData;
    ozonTopData = ozonData.top || [];

    fillFilters(salesData.filters);

    // Карточки из Ozon API (реальные данные)
    const totalOrders = ozonTopData.reduce((s, r) => s + r.ordered_units, 0);
    const totalRevenue = ozonTopData.reduce((s, r) => s + r.revenue, 0);
    const totalCancels = ozonTopData.reduce((s, r) => s + r.cancellations, 0);
    const totalQty = totalOrders; // ordered_units = штуки заказанные

    document.getElementById('card-orders').textContent = totalOrders.toLocaleString('ru');
    document.getElementById('card-qty').textContent = totalQty.toLocaleString('ru');
    document.getElementById('card-revenue').textContent = formatMoney(totalRevenue);
    document.getElementById('card-cancels').textContent = totalCancels.toLocaleString('ru');

    // График
    if (chartMode === 'orders') {
      loadOrdersChart();
    } else {
      renderChart(salesData.chart);
    }

    // Таблица из Ozon API
    renderTable(ozonTopData);

  } catch(e) {
    document.getElementById('analytics-tbody').innerHTML =
      '<tr><td colspan="8" class="state-msg" style="color:var(--red)">ОШИБКА ЗАГРУЗКИ</td></tr>';
  }
}

function fillFilters(filters) {
  if (!filters) return;

  const brandSel = document.getElementById('brand-filter');
  const currentBrand = brandSel.value;
  if (brandSel.options.length <= 1) {
    filters.brands.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b; opt.textContent = b;
      brandSel.appendChild(opt);
    });
    brandSel.value = currentBrand;
  }

  const catSel = document.getElementById('category-filter');
  const currentCat = catSel.value;
  if (catSel.options.length <= 1) {
    filters.categories.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id; opt.textContent = c.name || c.id;
      catSel.appendChild(opt);
    });
    catSel.value = currentCat;
  }
}

function renderChart(chartData) {
  const days = [...new Set(chartData.map(r => r.day))].sort();
  const salesByDay = {}, cancelsByDay = {};
  days.forEach(d => { salesByDay[d] = 0; cancelsByDay[d] = 0; });

  chartData.forEach(r => {
    if (!r.day) return;
    const val = chartMode === 'qty' ? r.qty : r.revenue;
    if (r.status === 'sale') salesByDay[r.day] = (salesByDay[r.day] || 0) + val;
    if (r.status === 'cancel') cancelsByDay[r.day] = (cancelsByDay[r.day] || 0) + val;
  });

  const labels = days.map(d => { const p = d.split('-'); return p[2] + '.' + p[1]; });

  if (chartInstance) chartInstance.destroy();

  const ctx = document.getElementById('sales-chart').getContext('2d');
  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Продажи', data: days.map(d => salesByDay[d] || 0), backgroundColor: 'rgba(255,106,0,0.7)', borderColor: 'rgba(255,106,0,1)', borderWidth: 1, borderRadius: 3 },
        { label: 'Отмены', data: days.map(d => cancelsByDay[d] || 0), backgroundColor: 'rgba(220,50,50,0.5)', borderColor: 'rgba(220,50,50,0.8)', borderWidth: 1, borderRadius: 3 }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: '#aaa', font: { family: 'monospace' } } },
        tooltip: { callbacks: { label: ctx => chartMode === 'revenue' ? `${ctx.dataset.label}: ${formatMoney(ctx.raw)}` : `${ctx.dataset.label}: ${ctx.raw} шт.` } }
      },
      scales: {
        x: { ticks: { color: '#666', font: { family: 'monospace', size: 10 } }, grid: { color: '#222' } },
        y: { ticks: { color: '#666', font: { family: 'monospace', size: 10 }, callback: v => chartMode === 'revenue' ? formatMoney(v) : v }, grid: { color: '#222' } }
      }
    }
  });
}

async function loadOrdersChart() {
  const { dateFrom, dateTo } = getDateParams();
  let url = '/api/analytics/ozon-chart?';
  if (dateFrom) url += `date_from=${dateFrom}&`;
  if (dateTo) url += `date_to=${dateTo}&`;

  try {
    const data = await fetch(url).then(r => r.json());
    renderOrdersChart(data.chart);
  } catch(e) {
    showToast('Ошибка загрузки графика заказов', 'error');
  }
}

function renderOrdersChart(chartData) {
  const labels = chartData.map(r => { const p = r.day.split('-'); return p[2] + '.' + p[1]; });

  if (chartInstance) chartInstance.destroy();

  const ctx = document.getElementById('sales-chart').getContext('2d');
  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Заказано (шт)',
          data: chartData.map(r => r.ordered_units),
          backgroundColor: 'rgba(255,106,0,0.7)',
          borderColor: 'rgba(255,106,0,1)',
          borderWidth: 1, borderRadius: 3, yAxisID: 'y',
        },
        {
          label: 'Выручка (руб)',
          data: chartData.map(r => r.revenue),
          backgroundColor: 'rgba(100,200,100,0.3)',
          borderColor: 'rgba(100,200,100,0.9)',
          borderWidth: 2, type: 'line', yAxisID: 'y2',
          pointRadius: 6, pointHoverRadius: 9,
          pointBackgroundColor: 'rgba(100,200,100,0.9)',
          pointBorderColor: '#fff', pointBorderWidth: 2, tension: 0.3,
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: '#aaa', font: { family: 'monospace' } } },
        tooltip: { callbacks: { label: ctx => ctx.datasetIndex === 0 ? `${ctx.dataset.label}: ${ctx.raw} шт.` : `${ctx.dataset.label}: ${formatMoney(ctx.raw)}` } }
      },
      scales: {
        x: { ticks: { color: '#666', font: { family: 'monospace', size: 10 } }, grid: { color: '#222' } },
        y: { ticks: { color: '#666', font: { family: 'monospace', size: 10 } }, grid: { color: '#222' }, position: 'left' },
        y2: { ticks: { color: '#4a4', font: { family: 'monospace', size: 10 }, callback: v => formatMoney(v) }, grid: { display: false }, position: 'right' }
      }
    }
  });
}

function setChartMode(mode) {
  chartMode = mode;
  document.getElementById('btn-chart-qty').classList.toggle('active', mode === 'qty');
  document.getElementById('btn-chart-revenue').classList.toggle('active', mode === 'revenue');
  document.getElementById('btn-chart-orders').classList.toggle('active', mode === 'orders');
  if (mode === 'orders') loadOrdersChart();
  else if (analyticsData) renderChart(analyticsData.chart);
}

function setTableMode(mode) {
  tableMode = mode;
  document.getElementById('btn-table-orders').classList.toggle('active', mode === 'orders');
  document.getElementById('btn-table-revenue').classList.toggle('active', mode === 'revenue');

  const thead = document.getElementById('table-head');
  if (mode === 'orders') {
    thead.innerHTML = `<tr>
      <th style="width:36px">#</th>
      <th onclick="sortTable('offer_id')" class="sortable">Артикул ↕</th>
      <th>Название</th><th>Бренд</th><th>Категория</th>
      <th onclick="sortTable('ordered_units')" class="sortable">Заказано (шт) ↕</th>
      <th onclick="sortTable('revenue')" class="sortable">Выручка ↕</th>
      <th onclick="sortTable('cancellations')" class="sortable">Отмен (шт) ↕</th>
    </tr>`;
    sortField = 'ordered_units';
  } else {
    thead.innerHTML = `<tr>
      <th style="width:36px">#</th>
      <th onclick="sortTable('offer_id')" class="sortable">Артикул ↕</th>
      <th>Название</th><th>Бренд</th><th>Категория</th>
      <th onclick="sortTable('revenue')" class="sortable">Выручка ↕</th>
      <th onclick="sortTable('avg_price')" class="sortable">Средняя цена ↕</th>
      <th onclick="sortTable('cancellations')" class="sortable">Отмен (шт) ↕</th>
    </tr>`;
    sortField = 'revenue';
  }

  if (ozonTopData) renderTable(ozonTopData);
}

function sortTable(field) {
  if (sortField === field) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
  else { sortField = field; sortDir = 'desc'; }
  if (ozonTopData) renderTable(ozonTopData);
}

function copyCell(el, text) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = el.style.color;
    el.style.color = 'var(--green)';
    setTimeout(() => { el.style.color = orig; }, 600);
  });
}

function applyFrontFilters(items) {
  const direction = document.getElementById('direction-filter').value;
  const search = document.getElementById('search-input').value.trim().toLowerCase();
  const brand = document.getElementById('brand-filter').value;
  const categoryFilter = document.getElementById('category-filter').value;

  return items.filter(r => {
    if (direction === 'uzspace' && (r.brand || '').toUpperCase() !== 'UZSPACE') return false;
    if (direction === 'sima' && (r.brand || '').toUpperCase() === 'UZSPACE') return false;
    if (brand && (r.brand || '') !== brand) return false;
    if (search && !r.offer_id.toLowerCase().includes(search) && !(r.name || '').toLowerCase().includes(search)) return false;
    return true;
  });
}

function renderTable(items) {
  const tbody = document.getElementById('analytics-tbody');
  if (!items || items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="state-msg">НЕТ ДАННЫХ</td></tr>';
    document.getElementById('table-count').textContent = '';
    return;
  }

  const filtered = applyFrontFilters(items);

  const sorted = [...filtered].sort((a, b) => {
    let av = a[sortField] ?? 0;
    let bv = b[sortField] ?? 0;
    if (sortField === 'avg_price') {
      av = a.ordered_units > 0 ? a.revenue / a.ordered_units : 0;
      bv = b.ordered_units > 0 ? b.revenue / b.ordered_units : 0;
    }
    if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return sortDir === 'asc' ? av - bv : bv - av;
  });

  document.getElementById('table-count').textContent = `${sorted.length} позиций`;

  tbody.innerHTML = sorted.map((r, i) => {
    const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `${i + 1}`;
    const avgPrice = r.ordered_units > 0 ? Math.round(r.revenue / r.ordered_units) : 0;
    const safeId = r.offer_id.replace(/'/g, "\\'");

    if (tableMode === 'orders') {
      return `<tr>
        <td style="text-align:center;font-size:14px">${medal}</td>
        <td class="code copy-cell" style="font-size:11px;color:var(--accent);cursor:pointer"
            onclick="copyCell(this,'${safeId}')" title="Скопировать">${r.offer_id}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${r.name || '—'}</td>
        <td style="font-size:11px;color:var(--text-dim)">${r.brand || '—'}</td>
        <td style="font-size:11px;color:var(--text-dim)">${r.category_name || '—'}</td>
        <td style="text-align:right;font-weight:bold;color:var(--accent)">${r.ordered_units.toLocaleString('ru')}</td>
        <td style="text-align:right">${formatMoney(r.revenue)}</td>
        <td style="text-align:right;color:var(--red,#dc3232)">${r.cancellations.toLocaleString('ru')}</td>
      </tr>`;
    } else {
      return `<tr>
        <td style="text-align:center;font-size:14px">${medal}</td>
        <td class="code copy-cell" style="font-size:11px;color:var(--accent);cursor:pointer"
            onclick="copyCell(this,'${safeId}')" title="Скопировать">${r.offer_id}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${r.name || '—'}</td>
        <td style="font-size:11px;color:var(--text-dim)">${r.brand || '—'}</td>
        <td style="font-size:11px;color:var(--text-dim)">${r.category_name || '—'}</td>
        <td style="text-align:right;font-weight:bold;color:var(--green)">${formatMoney(r.revenue)}</td>
        <td style="text-align:right;color:var(--text-dim)">${avgPrice.toLocaleString('ru')} ₽</td>
        <td style="text-align:right;color:var(--red,#dc3232)">${r.cancellations.toLocaleString('ru')}</td>
      </tr>`;
    }
  }).join('');
}

function formatMoney(v) {
  if (v >= 1000000) return (v / 1000000).toFixed(1) + ' млн ₽';
  if (v >= 1000) return (v / 1000).toFixed(0) + ' тыс ₽';
  return v.toLocaleString('ru') + ' ₽';
}

function exportTable() {
  if (!ozonTopData) return;
  const filtered = applyFrontFilters(ozonTopData);
  const sorted = [...filtered].sort((a, b) => {
    let av = a[sortField] ?? 0, bv = b[sortField] ?? 0;
    if (sortField === 'avg_price') { av = a.ordered_units > 0 ? a.revenue / a.ordered_units : 0; bv = b.ordered_units > 0 ? b.revenue / b.ordered_units : 0; }
    return sortDir === 'asc' ? av - bv : bv - av;
  });

  const BOM = '\uFEFF';
  const headers = tableMode === 'orders'
    ? ['#', 'Артикул', 'Название', 'Бренд', 'Категория', 'Заказано (шт)', 'Выручка', 'Отмен (шт)']
    : ['#', 'Артикул', 'Название', 'Бренд', 'Категория', 'Выручка', 'Средняя цена', 'Отмен (шт)'];

  const rows = sorted.map((r, i) => {
    const avgPrice = r.ordered_units > 0 ? Math.round(r.revenue / r.ordered_units) : 0;
    if (tableMode === 'orders') return [i+1, r.offer_id, r.name||'', r.brand||'', r.category_name||'', r.ordered_units, r.revenue, r.cancellations];
    else return [i+1, r.offer_id, r.name||'', r.brand||'', r.category_name||'', r.revenue, avgPrice, r.cancellations];
  });

  const csv = BOM + [headers, ...rows].map(row => row.map(v => `"${v}"`).join(';')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `analytics_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('✓ Таблица скачана');
}

async function syncAnalytics() {
  const btn = document.getElementById('sync-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinning">⟳</span> Загружаем...';
  try {
    await fetch('/api/analytics/sync', { method: 'POST' });
    showToast('Синхронизация запущена, данные появятся через минуту');
    setTimeout(loadAnalytics, 10000);
  } catch(e) {
    showToast('Ошибка синхронизации', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⟳ Загрузить с Ozon';
  }
}

function toggleChart() {
  const container = document.getElementById('chart-container');
  const btn = document.getElementById('toggle-chart-btn');
  const isHidden = container.style.display === 'none';
  container.style.display = isHidden ? 'block' : 'none';
  btn.textContent = isHidden ? '▲ Свернуть' : '▼ Развернуть';
}

const style = document.createElement('style');
style.textContent = `
.analytics-wrap { padding: 20px; }
.analytics-toolbar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:20px; }
.toolbar-left { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.toolbar-right { display:flex; gap:8px; }
.field-group.inline { display:flex; align-items:center; gap:6px; }
.field-group.inline label { font-size:11px; color:var(--text-dim); white-space:nowrap; }
.analytics-cards { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:20px; }
.analytics-card { background:var(--surface); border:1px solid var(--border); padding:16px 20px; }
.analytics-card.red { border-color: rgba(220,50,50,0.4); }
.analytics-card .card-value { font-size:24px; font-weight:bold; font-family:monospace; color:var(--accent); }
.analytics-card.red .card-value { color: var(--red, #dc3232); }
.analytics-card .card-label { font-size:11px; color:var(--text-dim); margin-top:4px; text-transform:uppercase; letter-spacing:1px; }
.analytics-chart-wrap { background:var(--surface); border:1px solid var(--border); padding:16px; margin-bottom:20px; }
.analytics-table-wrap { background:var(--surface); border:1px solid var(--border); padding:16px; overflow-x:auto; }
.section-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; font-size:11px; text-transform:uppercase; letter-spacing:1px; color:var(--text-dim); }
.chart-toggle { display:flex; gap:4px; }
.chart-btn { background:var(--surface2); border:1px solid var(--border); padding:3px 10px; cursor:pointer; font-size:11px; color:var(--text-dim); font-family:monospace; }
.chart-btn.active { background:var(--accent); border-color:var(--accent); color:#000; }
.sortable { cursor:pointer; }
.sortable:hover { color:var(--accent); }
.data-table { width:100%; border-collapse:collapse; }
.data-table th, .data-table td { padding:8px 12px; border-bottom:1px solid var(--border); text-align:left; white-space:nowrap; }
.data-table thead th { position:sticky; top:0; background:var(--surface); font-size:10px; text-transform:uppercase; letter-spacing:1px; color:var(--text-dim); }
`;
document.head.appendChild(style);

loadAnalytics();
document.getElementById('sync-btn').addEventListener('click', syncAnalytics);
document.getElementById('export-btn').addEventListener('click', exportTable);