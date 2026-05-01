// =====================================================================
// УПРАВЛЕНИЕ ОСТАТКАМИ (не Сима-Ленд)
// =====================================================================

let smPage = 1;
let smTotal = 0;
let smItems = [];         // данные из БД
let googleStock = {};     // остатки из Google Sheets
let smOverrides = {};     // локальные правки: offer_id -> новое значение FBS
let smSearchTimer = null;
const SM_PER_PAGE = 100;

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
  loadStockManage(1);
}

async function loadStockManage(page = 1) {
  smPage = page;
  const search = document.getElementById('sm-search')?.value || '';
  document.getElementById('sm-tbody').innerHTML = '<tr><td colspan="7" class="state-msg">ЗАГРУЗКА...</td></tr>';

  try {
    const params = new URLSearchParams({ page, per_page: SM_PER_PAGE, search });
    const r = await fetch(`/api/stock-manage/items?${params}`).then(r => r.json());
    smItems = r.items || [];
    smTotal = r.total || 0;
    document.getElementById('sm-count').textContent = smTotal ? `${smTotal} позиций` : '';
    renderStockManage();
    renderSmPagination();
  } catch(e) {
    document.getElementById('sm-tbody').innerHTML = '<tr><td colspan="7" class="state-msg" style="color:var(--red)">ОШИБКА ЗАГРУЗКИ</td></tr>';
  }
}

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
      renderStockManage();
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
// РЕНДЕР ТАБЛИЦЫ
// =====================================================================
function renderStockManage() {
  const tbody = document.getElementById('sm-tbody');
  if (!smItems.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="state-msg">НЕТ ДАННЫХ — загрузите список артикулов</td></tr>';
    return;
  }

  tbody.innerHTML = smItems.map(item => {
    const warehouseStock = googleStock[item.offer_id] ?? '—';
    const currentOverride = smOverrides[item.offer_id];
    const displayValue = currentOverride !== undefined ? currentOverride : (item.fbs_override !== null ? item.fbs_override : '');

    const fboColor = item.fbo > 10 ? 'var(--green)' : item.fbo > 0 ? 'var(--yellow,#f0a500)' : 'var(--text-dim)';
    const fbsColor = item.fbs > 0 ? 'var(--accent)' : 'var(--text-dim)';
    const warehouseColor = warehouseStock === 0 ? 'var(--red)' : warehouseStock === '—' ? 'var(--text-dim)' : 'var(--green)';
    const hasOverride = currentOverride !== undefined || item.fbs_override !== null;

    return `<tr style="${hasOverride ? 'background:rgba(255,106,0,0.06)' : ''}">
      <td style="color:var(--accent);font-family:monospace;font-size:11px">${item.offer_id}</td>
      <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--text-dim)">${item.name || '—'}</td>
      <td style="text-align:right;font-weight:bold;color:${fboColor}">${item.fbo.toLocaleString('ru')}</td>
      <td style="text-align:right;color:${fbsColor}">${item.fbs.toLocaleString('ru')}</td>
      <td style="text-align:right;color:${warehouseColor};font-weight:bold">${warehouseStock !== '—' ? Number(warehouseStock).toLocaleString('ru') : '—'}</td>
      <td style="text-align:right;padding:4px 8px">
        <input type="number"
          min="0"
          value="${displayValue}"
          placeholder="${warehouseStock !== '—' ? warehouseStock : '—'}"
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
}

function setOverride(offerId, value) {
  const num = parseInt(value);
  if (!isNaN(num) && num >= 0) {
    smOverrides[offerId] = num;
  } else if (value === '' || value === null) {
    delete smOverrides[offerId];
  }
}

function clearOverride(offerId) {
  delete smOverrides[offerId];
  // Обновляем input
  const input = document.querySelector(`input[data-offer="${offerId}"]`);
  if (input) input.value = '';
  renderStockManage();
}

// Применить % корректировку ко всем строкам
function applyAdjustment() {
  const pct = parseFloat(document.getElementById('sm-adjust-pct').value);
  if (isNaN(pct)) { showToast('Введите корректный %', 'error'); return; }

  smItems.forEach(item => {
    const base = googleStock[item.offer_id];
    if (base !== undefined && base !== null) {
      const adjusted = Math.max(0, Math.round(base * (1 + pct / 100)));
      smOverrides[item.offer_id] = adjusted;
    }
  });
  renderStockManage();
  showToast(`✓ Применена корректировка ${pct > 0 ? '+' : ''}${pct}%`);
}

// =====================================================================
// ПЕРЕДАЧА FBS В OZON
// =====================================================================
async function pushFbsToOzon() {
  // Собираем все строки: override или значение из Google
  const stocks = [];
  smItems.forEach(item => {
    let value = smOverrides[item.offer_id];
    if (value === undefined) {
      const gs = googleStock[item.offer_id];
      if (gs !== undefined) value = gs;
    }
    if (value !== undefined && value !== null && !isNaN(value)) {
      stocks.push({ offer_id: item.offer_id, stock: parseInt(value) });
    }
  });

  if (!stocks.length) {
    showToast('Нет данных для передачи. Обновите склад из Google или введите значения вручную.', 'error');
    return;
  }

  if (!await smConfirm(
    `Передать FBS остатки для ${stocks.length} позиций в Ozon?\nЭто обновит реальные остатки на маркетплейсе.`,
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
      await loadStockManage(smPage);
    }
  } catch(e) {
    showToast('Ошибка передачи в Ozon', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '→ Передать FBS в Ozon';
  }
}

// =====================================================================
// ЭКСПОРТ / ИМПОРТ СПИСКА АРТИКУЛОВ
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
    const r = await fetch('/api/stock-manage/import-list', {
      method: 'POST',
      body: formData
    }).then(r => r.json());

    showToast(`✓ Включено ${r.enabled} артикулов`);
    input.value = '';
    await loadStockManage(1);
  } catch {
    showToast('Ошибка импорта', 'error');
  }
}

// =====================================================================
// ПАГИНАЦИЯ
// =====================================================================
function renderSmPagination() {
  const el = document.getElementById('sm-pagination');
  const totalPages = Math.ceil(smTotal / SM_PER_PAGE);
  if (totalPages <= 1) { el.innerHTML = ''; return; }

  const btnStyle = 'display:inline-flex;align-items:center;justify-content:center;min-width:32px;height:32px;padding:0 8px;margin:0 2px;border:1px solid var(--border);background:var(--surface);color:var(--text);cursor:pointer;font-size:12px;';
  const activeBtnStyle = btnStyle + 'border-color:var(--accent);color:var(--accent);background:rgba(255,106,0,0.1);';
  const dimBtnStyle = btnStyle + 'color:var(--text-dim);cursor:default;';

  let html = '<div style="display:flex;align-items:center;justify-content:center;gap:4px;padding:16px 0;flex-wrap:wrap">';
  html += smPage > 1 ? `<button style="${btnStyle}" onclick="loadStockManage(${smPage-1})">‹</button>` : `<button style="${dimBtnStyle}" disabled>‹</button>`;

  const pages = buildSmPages(smPage, totalPages);
  for (const p of pages) {
    if (p === '...') html += `<span style="${dimBtnStyle}">…</span>`;
    else if (p === smPage) html += `<button style="${activeBtnStyle}">${p}</button>`;
    else html += `<button style="${btnStyle}" onclick="loadStockManage(${p})">${p}</button>`;
  }

  html += smPage < totalPages ? `<button style="${btnStyle}" onclick="loadStockManage(${smPage+1})">›</button>` : `<button style="${dimBtnStyle}" disabled>›</button>`;
  html += `<span style="margin-left:12px;font-size:11px;color:var(--text-dim)">стр. ${smPage} из ${totalPages}</span>`;
  html += '</div>';
  el.innerHTML = html;
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

async function importFromGoogle() {
  if (!await smConfirm('Импортировать все артикулы из Google Sheets как включённые?', 'Импорт из Google')) return;
  const btn = document.getElementById('sm-google-import-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Импортируем...';
  try {
    const r = await fetch('/api/stock-manage/import-from-google', {method: 'POST'}).then(r => r.json());
    showToast(`✓ Импортировано ${r.added} артикулов`);
    await loadStockManage(1);
  } catch {
    showToast('Ошибка импорта', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '↓ Импорт из Google';
  }
}

async function syncFboFbs() {
  const btn = document.getElementById('sm-sync-fbo-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Загружаем...';

  try {
    await fetch('/api/stock/fbo/sync', {method: 'POST'});

    let attempts = 0;
    while (attempts < 60) {
      await new Promise(r => setTimeout(r, 2000));
      const data = await fetch('/api/stock/fbo').then(r => r.json());
      if (!data.loading && data.total > 0) {
        await loadStockManage(smPage);
        showToast(`✓ FBO/FBS загружены: ${data.total} позиций`);
        break;
      }
      btn.textContent = `⏳ ${attempts * 2}с...`;
      attempts++;
    }
  } catch {
    showToast('Ошибка синхронизации FBO/FBS', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ Синхр. FBO/FBS';
  }
}

document.getElementById('sm-sync-fbo-btn').addEventListener('click', syncFboFbs);

document.getElementById('sm-google-import-btn').addEventListener('click', importFromGoogle);

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
input[type=number]::-webkit-inner-spin-button { opacity: 0.5; }
`;
document.head.appendChild(style);

// =====================================================================
// ИНИЦИАЛИЗАЦИЯ
// =====================================================================
async function initStockManage() {
  await loadStockManage(1);
  // Автозагрузка Google Sheets
  loadGoogleStock();
  // Если кэш FBO ещё грузится — поллим
  if (window._smLoadingCache) return;
  const r = await fetch('/api/stock-manage/items?page=1&per_page=1').then(r => r.json());
  if (r.loading) {
    window._smLoadingCache = true;
    showToast('⏳ Загружаем остатки FBO/FBS... (~30 сек)');
    const poll = setInterval(async () => {
      const r2 = await fetch('/api/stock-manage/items?page=1&per_page=1').then(r => r.json()).catch(() => ({}));
      if (!r2.loading) {
        clearInterval(poll);
        window._smLoadingCache = false;
        await loadStockManage(smPage);
        showToast('✓ Остатки FBO/FBS загружены');
      }
    }, 3000);
  }
}

initStockManage();
document.getElementById('sm-export-btn').addEventListener('click', exportStockList);
document.getElementById('sm-google-btn').addEventListener('click', loadGoogleStock);
document.getElementById('sm-push-btn').addEventListener('click', pushFbsToOzon);