// =====================================================================
// СЕБЕСТОИМОСТЬ
// =====================================================================
let costsPage = 1;
let costsSearchTimer = null;
let pendingCostData = [];

function debounceCosts() {
  clearTimeout(costsSearchTimer);
  costsSearchTimer = setTimeout(() => loadCosts(1), 400);
}

async function loadCosts(page = 1) {
  costsPage = page;
  const search = document.getElementById('cost-search')?.value || '';
  const brand = document.getElementById('cost-brand-filter')?.value || '';
  const tbody = document.getElementById('costs-body');
  tbody.innerHTML = '<tr><td colspan="9" class="state-msg">ЗАГРУЗКА...</td></tr>';

  try {
    const params = new URLSearchParams({ page, per_page: 100, search, brand });
    const r = await fetch(`/api/costs/products?${params}`).then(r => r.json());
    document.getElementById('cost-count').textContent = r.total ? `${r.total} товаров` : '';
    renderCosts(r.products || []);
    renderCostsPagination(r.total, page, 100);
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="9" class="state-msg">ОШИБКА ЗАГРУЗКИ</td></tr>';
  }
}

function renderCosts(products) {
  const tbody = document.getElementById('costs-body');
  if (!products.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="state-msg">НЕТ ДАННЫХ</td></tr>';
    return;
  }

  tbody.innerHTML = products.map(p => {
    const marginColor = p.margin === null ? 'var(--text-dim)'
      : p.margin < 10 ? 'var(--red)'
      : p.margin < 25 ? 'var(--yellow)'
      : 'var(--green)';

    const updatedAt = p.cost_updated_at
      ? new Date(p.cost_updated_at).toLocaleDateString('ru-RU')
      : '—';

    return `<tr>
      <td style="width:52px;padding:6px 8px">
        ${p.image_url
          ? `<img src="${p.image_url}" style="width:44px;height:44px;object-fit:cover;border:1px solid var(--border)" loading="lazy" onerror="this.style.display='none'">`
          : '<div style="width:44px;height:44px;background:var(--surface2);border:1px solid var(--border)"></div>'}
      </td>
      <td class="copy-cell code" style="font-size:11px" onclick="copyCell('${p.offer_id}', this)">${p.offer_id}</td>
      <td style="max-width:200px;font-size:12px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.name || '—'}</td>
      <td style="font-size:11px;color:var(--text-dim)">${p.brand || '—'}</td>
      <td>
        <div style="display:flex;align-items:center;gap:6px">
          <span style="${p.cost_price ? 'color:var(--text)' : 'color:var(--border)'};font-weight:600">
            ${p.cost_price ? p.cost_price.toLocaleString('ru') + ' ₽' : '—'}
          </span>
          <button class="btn" style="font-size:10px;padding:2px 6px" onclick="openCostHistoryModal('${p.offer_id}')">📋</button>
        </div>
      </td>
      <td style="font-size:11px;color:var(--text-dim)">${updatedAt}</td>
      <td>${p.price ? p.price.toLocaleString('ru') + ' ₽' : '—'}</td>
      <td>${p.net_price ? p.net_price.toLocaleString('ru') + ' ₽' : '—'}</td>
      <td style="font-weight:600;color:${marginColor}">
        ${p.margin !== null ? p.margin + '%' : '—'}
      </td>
    </tr>`;
  }).join('');
}

function renderCostsPagination(total, page, perPage) {
  const el = document.getElementById('costs-pagination');
  const totalPages = Math.ceil(total / perPage);
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  let html = '';
  for (let i = 1; i <= Math.min(totalPages, 20); i++) {
    html += `<button class="btn${i === page ? ' active' : ''}" onclick="loadCosts(${i})">${i}</button> `;
  }
  el.innerHTML = html;
}

// ---- Загрузка брендов в фильтр ----
async function loadCostFilters() {
  try {
    const r = await fetch('/api/repricer/filters').then(r => r.json());
    const sel = document.getElementById('cost-brand-filter');
    (r.brands || []).forEach(b => {
      const o = document.createElement('option');
      o.value = b; o.textContent = b;
      sel.appendChild(o);
    });
  } catch (e) {}
}

// ---- Upload Modal ----
function openCostUploadModal() {
  document.getElementById('cost-upload-modal').style.display = 'flex';
  document.getElementById('cost-upload-preview').style.display = 'none';
  document.getElementById('cost-file-input').value = '';
  pendingCostData = [];
}

function closeCostModal() {
  document.getElementById('cost-upload-modal').style.display = 'none';
}

function handleCostDrop(e) {
  e.preventDefault();
  document.getElementById('cost-drop-zone').style.borderColor = 'var(--border)';
  const file = e.dataTransfer.files[0];
  if (file) handleCostFile(file);
}

function handleCostFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function (e) {
    try {
      const wb = XLSX.read(e.target.result, { type: 'array' });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json(ws, { defval: '' });

      // Определяем колонки — поддерживаем разные форматы
      // Формат 1: Артикул + Себестоимость
      // Формат 2 (Сима): Код + Цена
      // Формат 3: offer_id + cost_price
      pendingCostData = [];
      let skipped = 0;

      for (const row of rows) {
        const keys = Object.keys(row);
        let offerId = row['Артикул'] || row['артикул'] || row['offer_id'] || row['Код'] || row['код'] || row['SKU'] || '';
        let cost = row['Себестоимость'] || row['себестоимость'] || row['cost_price'] ||
          row['Цена'] || row['цена'] || row['Закупочная цена'] || row['закупочная цена'] || '';

        offerId = String(offerId).trim();
        const costNum = parseFloat(String(cost).replace(',', '.').replace(/[^\d.]/g, ''));

        if (offerId && !isNaN(costNum) && costNum > 0) {
          pendingCostData.push({ offer_id: offerId, cost_price: Math.round(costNum) });
        } else {
          skipped++;
        }
      }

      // Превью
      document.getElementById('cost-upload-info').textContent =
        `Найдено: ${pendingCostData.length} позиций${skipped ? `, пропущено: ${skipped}` : ''}`;

      const previewBody = document.getElementById('cost-preview-body');
      previewBody.innerHTML = pendingCostData.slice(0, 20).map(d =>
        `<tr><td class="code" style="font-size:11px">${d.offer_id}</td><td>${d.cost_price.toLocaleString('ru')} ₽</td></tr>`
      ).join('') + (pendingCostData.length > 20
        ? `<tr><td colspan="2" style="color:var(--text-dim);font-size:11px">... и ещё ${pendingCostData.length - 20}</td></tr>`
        : '');

      document.getElementById('cost-upload-preview').style.display = 'block';
    } catch (err) {
      alert('Ошибка чтения файла: ' + err.message);
    }
  };
  reader.readAsArrayBuffer(file);
}

async function submitCostUpload() {
  if (!pendingCostData.length) return;
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = 'Загрузка...';

  try {
    const r = await fetch('/api/costs/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: pendingCostData })
    }).then(r => r.json());

    showToast(`✓ Обновлено ${r.updated} позиций`);
    closeCostModal();
    loadCosts(costsPage);
  } catch (e) {
    showToast('Ошибка загрузки', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Загрузить и обновить';
  }
}

// ---- Экспорт шаблона ----
function exportCostTemplate() {
  const ws = XLSX.utils.aoa_to_sheet([
    ['Артикул', 'Себестоимость'],
    ['12345/red', '150'],
    ['67890', '320'],
  ]);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Себестоимость');
  XLSX.writeFile(wb, 'cost_template.xlsx');
}

// ---- История себестоимости ----
async function openCostHistoryModal(offerId) {
  document.getElementById('cost-history-title').textContent = `История: ${offerId}`;
  document.getElementById('cost-history-body').innerHTML = '<div class="state-msg">Загрузка...</div>';
  document.getElementById('cost-history-modal').style.display = 'flex';

  try {
    const r = await fetch(`/api/costs/history/${encodeURIComponent(offerId)}`).then(r => r.json());
    const hist = r.history || [];
    if (!hist.length) {
      document.getElementById('cost-history-body').innerHTML = '<div class="state-msg">История пуста</div>';
      return;
    }
    document.getElementById('cost-history-body').innerHTML = `
      <table style="width:100%">
        <thead><tr><th>Дата</th><th>Было</th><th>Стало</th><th>Источник</th></tr></thead>
        <tbody>${hist.map(h => `<tr>
          <td style="font-size:11px;color:var(--text-dim)">${new Date(h.changed_at).toLocaleString('ru-RU')}</td>
          <td style="color:var(--red)">${h.old_cost ? h.old_cost.toLocaleString('ru') + ' ₽' : '—'}</td>
          <td style="color:var(--green);font-weight:600">${h.new_cost.toLocaleString('ru')} ₽</td>
          <td style="font-size:11px;color:var(--text-dim)">${h.source || '—'}</td>
        </tr>`).join('')}</tbody>
      </table>`;
  } catch (e) {
    document.getElementById('cost-history-body').innerHTML = '<div class="state-msg">Ошибка</div>';
  }
}

function closeCostHistoryModal() {
  document.getElementById('cost-history-modal').style.display = 'none';
}

// ---- Init ----
loadCostFilters();
loadCosts(1);