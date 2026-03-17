// =====================================================================
// АНАЛИТИКА ПРОДАЖ
// =====================================================================

let analyticsData = null;
let chartInstance = null;
let chartMode = 'qty';
let tableMode = 'orders'; // 'orders' или 'revenue'
let sortField = 'total_qty';
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
  document.getElementById('brand-filter').value = '';
  document.getElementById('category-filter').value = '';
  document.getElementById('search-input').value = '';
  loadAnalytics();
}

function buildAnalyticsUrl() {
  const dateFrom = document.getElementById('date-from').value;
  const dateTo = document.getElementById('date-to').value;
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

async function loadAnalytics() {
  document.getElementById('analytics-tbody').innerHTML =
    '<tr><td colspan="8" class="state-msg">ЗАГРУЗКА...</td></tr>';

  try {
    const data = await fetch(buildAnalyticsUrl()).then(r => r.json());
    analyticsData = data;

    fillFilters(data.filters);

    const chartCancels = data.chart.filter(r => r.status === 'cancel');
    const totalOrders = data.top.reduce((s, r) => s + r.orders_count, 0);
    const totalQty = data.top.reduce((s, r) => s + r.total_qty, 0);
    const totalRevenue = data.top.reduce((s, r) => s + r.total_revenue, 0);
    const totalCancels = chartCancels.reduce((s, r) => s + r.qty, 0);

    document.getElementById('card-orders').textContent = totalOrders.toLocaleString('ru');
    document.getElementById('card-qty').textContent = totalQty.toLocaleString('ru');
    document.getElementById('card-revenue').textContent = formatMoney(totalRevenue);
    document.getElementById('card-cancels').textContent = totalCancels.toLocaleString('ru');

    renderChart(data.chart);
    renderTable(data.top);

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

function setChartMode(mode) {
  chartMode = mode;
  document.getElementById('btn-qty').classList.toggle('active', mode === 'qty');
  document.getElementById('btn-revenue').classList.toggle('active', mode === 'revenue');
  if (analyticsData) renderChart(analyticsData.chart);
}

function setTableMode(mode) {
  tableMode = mode;
  document.getElementById('btn-table-orders').classList.toggle('active', mode === 'orders');
  document.getElementById('btn-table-revenue').classList.toggle('active', mode === 'revenue');

  // Обновляем заголовки таблицы
  const thead = document.getElementById('table-head');
  if (mode === 'orders') {
    thead.innerHTML = `<tr>
      <th style="width:36px">#</th>
      <th onclick="sortTable('offer_id')" class="sortable">Артикул ↕</th>
      <th>Название</th>
      <th>Бренд</th>
      <th>Категория</th>
      <th onclick="sortTable('orders_count')" class="sortable">Заказов ↕</th>
      <th onclick="sortTable('total_revenue')" class="sortable">Выручка ↕</th>
      <th onclick="sortTable('cancel_revenue')" class="sortable">Отмены (руб) ↕</th>
    </tr>`;
    sortField = 'orders_count';
  } else {
    thead.innerHTML = `<tr>
      <th style="width:36px">#</th>
      <th onclick="sortTable('offer_id')" class="sortable">Артикул ↕</th>
      <th>Название</th>
      <th>Бренд</th>
      <th>Категория</th>
      <th onclick="sortTable('total_revenue')" class="sortable">Выручка ↕</th>
      <th onclick="sortTable('avg_price')" class="sortable">Средняя цена ↕</th>
      <th onclick="sortTable('cancel_revenue')" class="sortable">Отмены (руб) ↕</th>
    </tr>`;
    sortField = 'total_revenue';
  }

  if (analyticsData) renderTable(analyticsData.top);
}

function sortTable(field) {
  if (sortField === field) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
  else { sortField = field; sortDir = 'desc'; }
  if (analyticsData) renderTable(analyticsData.top);
}

function copyCell(el, text) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = el.style.color;
    el.style.color = 'var(--green)';
    setTimeout(() => { el.style.color = orig; }, 600);
  });
}

function renderTable(items) {
  const tbody = document.getElementById('analytics-tbody');
  if (!items || items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="state-msg">НЕТ ДАННЫХ</td></tr>';
    document.getElementById('table-count').textContent = '';
    return;
  }

  // Считаем cancel данные из chart
  const cancelMap = {};
  if (analyticsData && analyticsData.chart) {
    // cancel данные агрегируем по offer_id — их нет в top, поэтому используем общие суммы
  }

  const sorted = [...items].sort((a, b) => {
    const av = a[sortField] ?? 0;
    const bv = b[sortField] ?? 0;
    if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return sortDir === 'asc' ? av - bv : bv - av;
  });

  document.getElementById('table-count').textContent = `${sorted.length} позиций`;

  tbody.innerHTML = sorted.map((r, i) => {
    const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `${i + 1}`;
    const avgPrice = r.total_qty > 0 ? Math.round(r.total_revenue / r.total_qty) : 0;

    if (tableMode === 'orders') {
      return `<tr>
        <td style="text-align:center;font-size:14px">${medal}</td>
        <td class="code copy-cell" style="font-size:11px;color:var(--accent);cursor:pointer"
            onclick="copyCell(this,'${r.offer_id}')" title="Скопировать">${r.offer_id}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${r.name || '—'}</td>
        <td style="font-size:11px;color:var(--text-dim)">${r.brand || '—'}</td>
        <td style="font-size:11px;color:var(--text-dim)">${r.category_name || '—'}</td>
        <td style="text-align:right">${r.orders_count.toLocaleString('ru')}</td>
        <td style="text-align:right;font-weight:bold;color:var(--accent)">${formatMoney(r.total_revenue)}</td>
        <td style="text-align:right;color:var(--red,#dc3232)">${formatMoney(r.cancel_revenue || 0)}</td>
      </tr>`;
    } else {
      return `<tr>
        <td style="text-align:center;font-size:14px">${medal}</td>
        <td class="code copy-cell" style="font-size:11px;color:var(--accent);cursor:pointer"
            onclick="copyCell(this,'${r.offer_id}')" title="Скопировать">${r.offer_id}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${r.name || '—'}</td>
        <td style="font-size:11px;color:var(--text-dim)">${r.brand || '—'}</td>
        <td style="font-size:11px;color:var(--text-dim)">${r.category_name || '—'}</td>
        <td style="text-align:right;font-weight:bold;color:var(--green)">${formatMoney(r.total_revenue)}</td>
        <td style="text-align:right;color:var(--text-dim)">${avgPrice.toLocaleString('ru')} ₽</td>
        <td style="text-align:right;color:var(--red,#dc3232)">${formatMoney(r.cancel_revenue || 0)}</td>
      </tr>`;
    }
  }).join('');
}

function formatMoney(v) {
  if (v >= 1000000) return (v / 1000000).toFixed(1) + ' млн ₽';
  if (v >= 1000) return (v / 1000).toFixed(0) + ' тыс ₽';
  return v.toLocaleString('ru') + ' ₽';
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