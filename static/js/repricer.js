// =====================================================================
// РЕПРАЙСЕР — полная версия с demand-репрайсингом и мультиселектом
// =====================================================================

let repricerPage = 1;
let repricerSearchTimer = null;

// Состояние выбранных товаров
let selectedOfferIds = new Set();
// Кэш данных спроса {offer_id: {orders_7d, trend, recommended_action, recommended_price, ...}}
let demandCache = {};
// Последний загруженный список товаров
let repricerProducts = [];

// =====================================================================
// ДЕБАУНС И ИНИЦИАЛИЗАЦИЯ
// =====================================================================

function debounceRepricer() {
  clearTimeout(repricerSearchTimer);
  repricerSearchTimer = setTimeout(() => loadRepricer(1), 400);
}

async function loadRepricerFilters() {
  try {
    const r = await fetch('/api/repricer/filters').then(r => r.json());
    const brandSel = document.getElementById('repricer-brand-filter');
    const catSel = document.getElementById('repricer-category-filter');
    const whSel = document.getElementById('repricer-warehouse-filter');
    (r.brands || []).forEach(b => {
      const o = document.createElement('option');
      o.value = b; o.textContent = b;
      brandSel.appendChild(o);
    });
    (r.categories || []).forEach(c => {
      const o = document.createElement('option');
      o.value = c.id; o.textContent = c.name;
      catSel.appendChild(o);
    });
    (r.warehouses || []).forEach(w => {
      const o = document.createElement('option');
      o.value = w; o.textContent = w.toUpperCase();
      whSel.appendChild(o);
    });
  } catch (e) {}
}

// =====================================================================
// СИНХРОНИЗАЦИЯ С OZON
// =====================================================================

let _syncPollTimer = null;

async function syncRepricer() {
  const btn = document.getElementById('repricer-sync-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinning">⟳</span> Запуск...';
  try {
    const r = await fetch('/api/repricer/sync', { method: 'POST' }).then(r => r.json());
    if (!r.started) showToast(r.message || 'Синхронизация уже идёт');
    pollSyncStatus();
  } catch (e) {
    showToast('Ошибка запуска', 'error');
    btn.disabled = false;
    btn.innerHTML = '⟳ Загрузить с Ozon';
  }
}

async function pollSyncStatus() {
  const btn = document.getElementById('repricer-sync-btn');
  clearTimeout(_syncPollTimer);
  try {
    const s = await fetch('/api/repricer/sync/status').then(r => r.json());
    if (s.running) {
      btn.innerHTML = `<span class="spinning">⟳</span> ${s.progress || 'Загрузка...'} (${s.synced || 0})`;
      _syncPollTimer = setTimeout(pollSyncStatus, 2000);
    } else if (s.error) {
      showToast(`Ошибка: ${s.error}`, 'error');
      btn.disabled = false;
      btn.innerHTML = '⟳ Загрузить с Ozon';
    } else {
      if (s.synced > 0) showToast(`✓ Загружено ${s.synced} товаров`);
      btn.disabled = false;
      btn.innerHTML = '⟳ Загрузить с Ozon';
      if (s.synced > 0) { loadRepricerFilters(); loadRepricer(1); }
    }
  } catch (e) {
    _syncPollTimer = setTimeout(pollSyncStatus, 3000);
  }
}

// =====================================================================
// ЗАГРУЗКА ДАННЫХ СПРОСА
// =====================================================================

async function loadDemandData(offerIds) {
  if (!offerIds || offerIds.length === 0) return;
  try {
    const ids = offerIds.join(',');
    const r = await fetch(`/api/repricer/demand?offer_ids=${encodeURIComponent(ids)}`).then(r => r.json());
    Object.assign(demandCache, r.demand || {});
    // Обновляем только колонки спроса без полной перерисовки таблицы
    offerIds.forEach(id => updateDemandCell(id));
  } catch (e) {}
}

function updateDemandCell(offerId) {
  const cell = document.getElementById(`demand-${CSS.escape(offerId)}`);
  if (!cell) return;
  const d = demandCache[offerId];
  if (!d) return;
  cell.innerHTML = renderDemandContent(offerId, d);
}

function renderDemandContent(offerId, d) {
  if (!d) return '<span style="color:var(--border)">—</span>';

  const TREND = { up: '↑', down: '↓', flat: '→' };
  const TREND_COLOR = { up: 'var(--green)', down: 'var(--red)', flat: 'var(--text-dim)' };
  const ACTION_LABEL = { raise: '↑ Поднять', lower: '↓ Снизить' };
  const ACTION_COLOR = { raise: 'var(--green)', lower: 'var(--yellow)' };

  const trendIcon = TREND[d.trend] || '→';
  const trendColor = TREND_COLOR[d.trend] || 'var(--text-dim)';
  const demandColor = d.orders_7d >= d.min_orders ? 'var(--green)' : 'var(--red)';

  let html = `
    <div style="min-width:130px">
      <div style="display:flex;align-items:baseline;gap:6px;margin-bottom:3px">
        <span style="font-size:18px;font-weight:700;color:${demandColor};font-family:var(--display);line-height:1">
          ${d.orders_7d}
        </span>
        <span style="font-size:11px;color:var(--text-dim)">/ ${d.min_orders} зак.</span>
        <span style="font-size:14px;color:${trendColor};font-weight:700">${trendIcon}</span>
      </div>`;

  if (d.orders_prev_7d > 0 || d.orders_7d > 0) {
    html += `<div style="font-size:10px;color:var(--text-dim);margin-bottom:4px">
      пред. неделя: ${d.orders_prev_7d}
    </div>`;
  }

  if (d.recommended_action && d.recommended_price) {
    const aColor = ACTION_COLOR[d.recommended_action] || 'var(--text-dim)';
    const aLabel = ACTION_LABEL[d.recommended_action] || '';
    html += `
      <div style="display:flex;align-items:center;gap:6px;margin-top:4px">
        <span style="font-size:10px;color:${aColor};font-weight:600">${aLabel}</span>
        <button class="btn" style="font-size:10px;padding:2px 8px;border-color:${aColor};color:${aColor}"
          onclick="applyPrice('${offerId}',${d.recommended_price},0)"
          title="Применить рекомендованную цену">
          ${d.recommended_price.toLocaleString('ru')} ₽
        </button>
      </div>`;
  } else if (!d.recommended_action) {
    html += `<div style="font-size:10px;color:var(--text-dim)">Нет себест. / цены</div>`;
  }

  html += '</div>';
  return html;
}

// =====================================================================
// ЗАГРУЗКА И РЕНДЕР ТАБЛИЦЫ
// =====================================================================

async function loadRepricer(page = 1) {
  repricerPage = page;
  const search = document.getElementById('repricer-search')?.value || '';
  const idx = document.getElementById('repricer-index-filter')?.value || '';
  const brand = document.getElementById('repricer-brand-filter')?.value || '';
  const category = document.getElementById('repricer-category-filter')?.value || '';
  const warehouse = document.getElementById('repricer-warehouse-filter')?.value || '';
  const demandOnly = document.getElementById('repricer-demand-filter')?.checked ? '1' : '';
  const tbody = document.getElementById('repricer-body');
  tbody.innerHTML = '<tr><td colspan="15" class="state-msg">ЗАГРУЗКА...</td></tr>';

  try {
    const params = new URLSearchParams({
      page, per_page: 50, search, index_filter: idx,
      brand, category_id: category, warehouse, demand_only: demandOnly
    });
    const r = await fetch(`/api/repricer/products?${params}`).then(r => r.json());
    document.getElementById('repricer-count').textContent = r.total ? `${r.total} товаров` : '';
    repricerProducts = r.products || [];
    renderRepricer(repricerProducts);
    renderRepricerPagination(r.total, page, 50);

    // Подгружаем данные спроса асинхронно для видимых товаров
    const ids = repricerProducts.map(p => p.offer_id);
    if (ids.length) loadDemandData(ids);

  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="15" class="state-msg error">ОШИБКА ЗАГРУЗКИ</td></tr>';
  }
}

function renderRepricer(products) {
  const tbody = document.getElementById('repricer-body');
  if (!products.length) {
    tbody.innerHTML = '<tr><td colspan="15" class="state-msg">НЕТ ДАННЫХ — нажмите "Загрузить товары с Ozon"</td></tr>';
    return;
  }

  const INDEX_ICON = { RED: '🔴', YELLOW: '🟡', GREEN: '🟢', '': '⚪' };

  tbody.innerHTML = products.map(p => {
    const isSelected = selectedOfferIds.has(p.offer_id);
    const demandEnabled = p.demand_rule_enabled;

    const marginColor = p.current_margin === null ? 'var(--text-dim)'
      : p.current_margin < 10 ? 'var(--red)'
      : p.current_margin < 25 ? 'var(--yellow)'
      : 'var(--green)';

    const suggestedHighlight = p.suggested_price && p.suggested_price > p.price
      ? 'color:var(--yellow)' : 'color:var(--green)';

    // Данные спроса из кэша (могут загрузиться позже)
    const d = demandCache[p.offer_id];
    const demandCellContent = d ? renderDemandContent(p.offer_id, d) : `
      <div style="min-width:130px">
        <div style="font-size:10px;color:var(--text-dim)">
          ${demandEnabled ? '<span class="spinning">⟳</span> загрузка...' : '—'}
        </div>
        ${demandEnabled ? `<div style="font-size:10px;color:var(--text-dim);margin-top:2px">
          порог: ${p.demand_min_orders || 3} / шаг: ${p.demand_step_pct || 5}%
        </div>` : ''}
      </div>`;

    return `<tr style="${isSelected ? 'background:rgba(232,93,38,0.07)' : demandEnabled ? 'background:rgba(45,206,137,0.03)' : ''}">
      <td style="width:32px;padding:6px 8px;text-align:center">
        <input type="checkbox" class="row-cb" value="${p.offer_id}"
          ${isSelected ? 'checked' : ''}
          onchange="toggleRowSelect('${p.offer_id}', this.checked)"
          style="cursor:pointer;width:14px;height:14px;accent-color:var(--accent)">
      </td>
      <td style="width:52px;padding:6px 8px">
        ${p.image_url
          ? `<img src="${p.image_url}" style="width:44px;height:44px;object-fit:cover;border:1px solid var(--border)" loading="lazy" onerror="this.style.display='none'">`
          : '<div style="width:44px;height:44px;background:var(--surface2);border:1px solid var(--border)"></div>'}
      </td>
      <td style="max-width:180px">
        <div class="copy-cell code" style="font-size:11px" onclick="copyCell('${p.offer_id}',this)">${p.offer_id}</div>
        <div style="color:var(--text-dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.name || '—'}</div>
        ${demandEnabled ? `<div style="font-size:9px;color:var(--green);margin-top:2px;letter-spacing:1px">⬡ DEMAND ${p.demand_step_pct || 5}%</div>` : ''}
      </td>
      <td style="font-weight:600">${p.price ? p.price.toLocaleString('ru') + ' ₽' : '—'}</td>
      <td class="editable-cell" data-field="min_price" data-offer="${p.offer_id}"
          onclick="editRepricerCell(this)" title="Нажмите для редактирования" style="color:var(--text-dim)">
        ${p.min_price ? p.min_price.toLocaleString('ru') + ' ₽' : '—'}
      </td>
      <td style="color:var(--text-dim)">
        ${p.commission_fbs_percent || 0}%
        <div style="font-size:10px">+${p.commission_fbs_logistics || 0} ₽ лог.</div>
      </td>
      <td>${p.net_price ? p.net_price.toLocaleString('ru') + ' ₽' : '—'}</td>
      <td class="editable-cell" data-field="cost_price" data-offer="${p.offer_id}"
          onclick="editRepricerCell(this)" title="Введите себестоимость">
        ${p.cost_price ? p.cost_price.toLocaleString('ru') + ' ₽' : '<span style="color:var(--border)">+</span>'}
      </td>
      <td style="font-weight:600;color:${marginColor}">
        ${p.current_margin !== null ? p.current_margin + '%' : '—'}
      </td>
      <td class="editable-cell" data-field="target_margin_pct" data-offer="${p.offer_id}"
          onclick="editRepricerCell(this)" title="Целевая маржа %">
        ${p.target_margin_pct ? p.target_margin_pct + '%' : '<span style="color:var(--border)">+</span>'}
      </td>
      <td style="font-weight:600;${p.suggested_price ? suggestedHighlight : 'color:var(--text-dim)'}">
        ${p.suggested_price ? p.suggested_price.toLocaleString('ru') + ' ₽' : '—'}
      </td>
      <td style="text-align:center;font-size:18px" title="${p.price_index_color || 'нет данных'}">
        ${INDEX_ICON[p.price_index_color || ''] || '⚪'}
        ${p.competitor_min_price ? `<div style="font-size:10px;color:var(--text-dim)">${p.competitor_min_price.toLocaleString('ru')} ₽</div>` : ''}
      </td>
      <td style="color:var(--text-dim)">
        ${p.competitor_min_price ? p.competitor_min_price.toLocaleString('ru') + ' ₽' : '—'}
      </td>
      <td id="demand-${p.offer_id}">${demandCellContent}</td>
      <td>
        <div style="display:flex;gap:4px;flex-direction:column">
          ${p.suggested_price
            ? `<button class="btn primary" style="font-size:10px;padding:4px 8px;white-space:nowrap"
                 onclick="applyPrice('${p.offer_id}',${p.suggested_price},${p.min_price || 0})"
                 title="Применить рекомендованную цену по марже">▲ ${p.suggested_price.toLocaleString('ru')} ₽</button>`
            : ''}
          <button class="btn" style="font-size:10px;padding:4px 8px"
            onclick="openPriceHistory('${p.offer_id}')" title="История цен">📋 История</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

// =====================================================================
// МУЛЬТИСЕЛЕКТ
// =====================================================================

function toggleRowSelect(offerId, checked) {
  if (checked) {
    selectedOfferIds.add(offerId);
  } else {
    selectedOfferIds.delete(offerId);
  }
  updateBulkPanel();
}

function toggleSelectAll(checked) {
  repricerProducts.forEach(p => {
    if (checked) {
      selectedOfferIds.add(p.offer_id);
    } else {
      selectedOfferIds.delete(p.offer_id);
    }
  });
  // Обновляем чекбоксы строк
  document.querySelectorAll('.row-cb').forEach(cb => { cb.checked = checked; });
  updateBulkPanel();
}

function clearSelection() {
  selectedOfferIds.clear();
  document.querySelectorAll('.row-cb').forEach(cb => { cb.checked = false; });
  document.getElementById('select-all-cb').checked = false;
  updateBulkPanel();
}

function updateBulkPanel() {
  const panel = document.getElementById('bulk-panel');
  const label = document.getElementById('bulk-count-label');
  const count = selectedOfferIds.size;

  if (count === 0) {
    panel.style.display = 'none';
  } else {
    panel.style.display = 'flex';
    label.textContent = `Выбрано: ${count} товаров`;
  }
}

// =====================================================================
// МАССОВОЕ НАЗНАЧЕНИЕ DEMAND-ПРАВИЛА
// =====================================================================

async function bulkApplyDemandRule(enable) {
  const ids = Array.from(selectedOfferIds);
  if (!ids.length) return;

  const minOrders = parseInt(document.getElementById('bulk-min-orders').value) || 3;
  const stepPct = parseInt(document.getElementById('bulk-step-pct').value) || 5;

  const body = {
    offer_ids: ids,
    demand_rule_enabled: enable,
    demand_min_orders: minOrders,
    demand_step_pct: stepPct,
  };

  try {
    const r = await fetch('/api/repricer/bulk-demand-settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(r => r.json());

    if (r.ok) {
      const action = enable ? `включено (порог: ${minOrders}, шаг: ${stepPct}%)` : 'выключено';
      showToast(`✓ Demand-правило ${action} для ${r.updated} товаров`);
      clearSelection();
      loadRepricer(repricerPage);
    } else {
      showToast('Ошибка сохранения', 'error');
    }
  } catch (e) {
    showToast('Ошибка сети', 'error');
  }
}

// =====================================================================
// ПАГИНАЦИЯ
// =====================================================================

function renderRepricerPagination(total, page, perPage) {
  const el = document.getElementById('repricer-pagination');
  const totalPages = Math.ceil(total / perPage);
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  let html = '';
  for (let i = 1; i <= Math.min(totalPages, 30); i++) {
    html += `<button class="btn${i === page ? ' active' : ''}" onclick="loadRepricer(${i})">${i}</button> `;
  }
  el.innerHTML = html;
}

// =====================================================================
// ИНЛАЙН-РЕДАКТИРОВАНИЕ
// =====================================================================

async function editRepricerCell(el) {
  if (el.querySelector('input')) return;
  const field = el.dataset.field;
  const offerId = el.dataset.offer;
  const currentText = el.innerText.trim().replace(/[₽%\s,]/g, '');
  const displayText = (currentText === '+' || currentText === '—') ? '' : currentText;

  el.innerHTML = '';
  const input = document.createElement('input');
  input.type = 'number';
  input.className = 'inline-input';
  input.value = displayText;
  input.style.width = '70px';
  el.appendChild(input);
  input.focus();
  input.select();

  async function save() {
    const val = parseInt(input.value);
    if (isNaN(val) && input.value !== '') {
      el.innerHTML = displayText || '<span style="color:var(--border)">+</span>';
      return;
    }
    try {
      const body = {};
      body[field] = val || null;
      await fetch(`/api/repricer/products/${encodeURIComponent(offerId)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      await loadRepricer(repricerPage);
      showToast('✓ Сохранено');
    } catch (e) {
      el.innerHTML = displayText || '<span style="color:var(--border)">+</span>';
    }
  }

  input.addEventListener('blur', save);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { el.innerHTML = displayText || '<span style="color:var(--border)">+</span>'; }
  });
}

// =====================================================================
// ПРИМЕНЕНИЕ ЦЕНЫ
// =====================================================================

async function applyPrice(offerId, newPrice, minPrice) {
  if (!confirm(`Установить цену ${newPrice.toLocaleString('ru')} ₽ для артикула ${offerId}?`)) return;
  try {
    const r = await fetch('/api/repricer/apply-price', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer_id: offerId, new_price: newPrice, min_price: minPrice, reason: 'demand' })
    }).then(r => r.json());

    if (r.ok && r.updated) {
      showToast(`✓ Цена ${newPrice.toLocaleString('ru')} ₽ применена`);
      loadRepricer(repricerPage);
    } else {
      const err = (r.errors || []).map(e => e.message).join(', ');
      showToast(`Ошибка: ${err || 'неизвестная'}`, 'error');
    }
  } catch (e) {
    showToast('Ошибка применения цены', 'error');
  }
}

// =====================================================================
// ИСТОРИЯ ЦЕН
// =====================================================================

async function openPriceHistory(offerId) {
  document.getElementById('history-modal-title').textContent = `История цен: ${offerId}`;
  document.getElementById('history-modal-body').innerHTML = '<div class="state-msg">Загрузка...</div>';
  document.getElementById('history-modal').style.display = 'flex';

  try {
    const r = await fetch(`/api/repricer/history/${encodeURIComponent(offerId)}`).then(r => r.json());
    const hist = r.history || [];
    if (!hist.length) {
      document.getElementById('history-modal-body').innerHTML = '<div class="state-msg">История пуста</div>';
      return;
    }
    const REASON = {
      'manual': '✋ Вручную',
      'demand': '📊 Demand',
      'auto_margin': '📐 Авто маржа',
      'auto_competitor': '🏁 Конкурент',
    };
    document.getElementById('history-modal-body').innerHTML = `
      <table style="width:100%">
        <thead><tr><th>Дата</th><th>Было</th><th>Стало</th><th>Причина</th><th>Кто</th></tr></thead>
        <tbody>${hist.map(h => `<tr>
          <td style="font-size:11px;color:var(--text-dim)">${h.changed_at.slice(0, 16).replace('T', ' ')}</td>
          <td style="color:var(--red)">${h.old_price.toLocaleString('ru')} ₽</td>
          <td style="color:var(--green);font-weight:600">${h.new_price.toLocaleString('ru')} ₽</td>
          <td style="color:var(--text-dim);font-size:11px">${REASON[h.reason] || h.reason || '—'}</td>
          <td style="color:var(--text-dim);font-size:11px">${h.changed_by || '—'}</td>
        </tr>`).join('')}</tbody>
      </table>`;
  } catch (e) {
    document.getElementById('history-modal-body').innerHTML = '<div class="state-msg error">Ошибка загрузки</div>';
  }
}

function closeHistoryModal() {
  document.getElementById('history-modal').style.display = 'none';
}

// =====================================================================
// СТАРТ
// =====================================================================

loadRepricerFilters();
loadRepricer(1);