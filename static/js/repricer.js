// РЕПРАЙСЕР
// =====================================================================
let repricerPage = 1;

let repricerSearchTimer = null;
function debounceRepricer() {
  clearTimeout(repricerSearchTimer);
  repricerSearchTimer = setTimeout(() => loadRepricer(1), 400);
}

async function loadRepricerFilters() {
  try {
    const r = await fetch('/api/repricer/filters').then(r=>r.json());

    const brandSel = document.getElementById('repricer-brand-filter');
    const catSel = document.getElementById('repricer-category-filter');
    const whSel = document.getElementById('repricer-warehouse-filter');

    (r.brands||[]).forEach(b => {
      const o = document.createElement('option');
      o.value = b; o.textContent = b;
      brandSel.appendChild(o);
    });
    (r.categories||[]).forEach(c => {
      const o = document.createElement('option');
      o.value = c.id; o.textContent = c.name;
      catSel.appendChild(o);
    });
    (r.warehouses||[]).forEach(w => {
      const o = document.createElement('option');
      o.value = w; o.textContent = w.toUpperCase();
      whSel.appendChild(o);
    });
  } catch(e) {}
}

let _syncPollTimer = null;

async function syncRepricer() {
  const btn = document.getElementById('repricer-sync-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinning">⟳</span> Запуск...';

  try {
    const r = await fetch('/api/repricer/sync', {method:'POST'}).then(r=>r.json());
    if (!r.started) {
      showToast(r.message || 'Синхронизация уже идёт');
    }
    // Начинаем polling статуса
    pollSyncStatus();
  } catch(e) {
    showToast('Ошибка запуска', 'error');
    btn.disabled = false;
    btn.innerHTML = '⟳ Загрузить с Ozon';
  }
}

async function pollSyncStatus() {
  const btn = document.getElementById('repricer-sync-btn');
  clearTimeout(_syncPollTimer);

  try {
    const s = await fetch('/api/repricer/sync/status').then(r=>r.json());
    if (s.running) {
      btn.innerHTML = `<span class="spinning">⟳</span> ${s.progress || 'Загрузка...'} (${s.synced||0})`;
      _syncPollTimer = setTimeout(pollSyncStatus, 2000);
    } else if (s.error) {
      showToast(`Ошибка: ${s.error}`, 'error');
      btn.disabled = false;
      btn.innerHTML = '⟳ Загрузить с Ozon';
    } else if (s.synced > 0) {
      showToast(`✓ Загружено ${s.synced} товаров`);
      btn.disabled = false;
      btn.innerHTML = '⟳ Загрузить с Ozon';
      loadRepricerFilters();
      loadRepricer(1);
    } else {
      btn.disabled = false;
      btn.innerHTML = '⟳ Загрузить с Ozon';
    }
  } catch(e) {
    _syncPollTimer = setTimeout(pollSyncStatus, 3000);
  }
}

async function loadRepricer(page=1) {
  repricerPage = page;
  const search = document.getElementById('repricer-search')?.value || '';
  const idx = document.getElementById('repricer-index-filter')?.value || '';
  const brand = document.getElementById('repricer-brand-filter')?.value || '';
  const category = document.getElementById('repricer-category-filter')?.value || '';
  const warehouse = document.getElementById('repricer-warehouse-filter')?.value || '';
  const tbody = document.getElementById('repricer-body');
  tbody.innerHTML = '<tr><td colspan="13" class="state-msg">ЗАГРУЗКА...</td></tr>';

  try {
    const params = new URLSearchParams({page, per_page: 100, search, index_filter: idx, brand, category_id: category, warehouse});
    const r = await fetch(`/api/repricer/products?${params}`).then(r=>r.json());
    document.getElementById('repricer-count').textContent = r.total ? `${r.total} товаров` : '';
    renderRepricer(r.products || []);
    renderRepricerPagination(r.total, page, 100);
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="13" class="state-msg error">ОШИБКА ЗАГРУЗКИ</td></tr>';
  }
}

function renderRepricer(products) {
  const tbody = document.getElementById('repricer-body');
  if (!products.length) {
    tbody.innerHTML = '<tr><td colspan="13" class="state-msg">НЕТ ДАННЫХ — нажмите "Загрузить товары с Ozon"</td></tr>';
    return;
  }

  const INDEX_ICON = { RED: '🔴', YELLOW: '🟡', GREEN: '🟢', '': '⚪' };

  tbody.innerHTML = products.map(p => {
    const marginColor = p.current_margin === null ? 'var(--text-dim)'
      : p.current_margin < 10 ? 'var(--red)'
      : p.current_margin < 25 ? 'var(--yellow)'
      : 'var(--green)';

    const suggestedHighlight = p.suggested_price && p.suggested_price > p.price
      ? 'color:var(--yellow)' : 'color:var(--green)';

    return `<tr>
      <td style="width:52px;padding:6px 8px">
        ${p.image_url
          ? `<img src="${p.image_url}" style="width:44px;height:44px;object-fit:cover;border:1px solid var(--border)" loading="lazy" onerror="this.style.display='none'">`
          : '<div style="width:44px;height:44px;background:var(--surface2);border:1px solid var(--border)"></div>'}
      </td>
      <td style="max-width:180px">
        <div class="copy-cell code" style="font-size:11px" onclick="copyCell('${p.offer_id}',this)">${p.offer_id}</div>
        <div style="color:var(--text-dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.name||'—'}</div>
      </td>
      <td style="font-weight:600">${p.price ? p.price.toLocaleString('ru') + ' ₽' : '—'}</td>
      <td class="editable-cell" data-type="repricer" data-field="min_price" data-offer="${p.offer_id}"
          onclick="editRepricerCell(this)" title="Нажмите для редактирования" style="color:var(--text-dim)">
        ${p.min_price ? p.min_price.toLocaleString('ru') + ' ₽' : '—'}
      </td>
      <td style="color:var(--text-dim)">
        ${p.commission_fbs_percent||0}%
        <div style="font-size:10px">+${p.commission_fbs_logistics||0} ₽ лог.</div>
      </td>
      <td>${p.net_price ? p.net_price.toLocaleString('ru') + ' ₽' : '—'}</td>
      <td class="editable-cell" data-type="repricer" data-field="cost_price" data-offer="${p.offer_id}"
          onclick="editRepricerCell(this)" title="Введите себестоимость">
        ${p.cost_price ? p.cost_price.toLocaleString('ru') + ' ₽' : '<span style="color:var(--border)">+</span>'}
      </td>
      <td style="font-weight:600;color:${marginColor}">
        ${p.current_margin !== null ? p.current_margin + '%' : '—'}
      </td>
      <td class="editable-cell" data-type="repricer" data-field="target_margin_pct" data-offer="${p.offer_id}"
          onclick="editRepricerCell(this)" title="Целевая маржа %">
        ${p.target_margin_pct ? p.target_margin_pct + '%' : '<span style="color:var(--border)">+</span>'}
      </td>
      <td style="font-weight:600;${p.suggested_price ? suggestedHighlight : 'color:var(--text-dim)'}">
        ${p.suggested_price ? p.suggested_price.toLocaleString('ru') + ' ₽' : '—'}
      </td>
      <td style="text-align:center;font-size:18px" title="${p.price_index_color||'нет данных'}">
        ${INDEX_ICON[p.price_index_color||''] || '⚪'}
        ${p.competitor_min_price ? `<div style="font-size:10px;color:var(--text-dim)">${p.competitor_min_price.toLocaleString('ru')} ₽</div>` : ''}
      </td>
      <td style="color:var(--text-dim)">
        ${p.competitor_min_price ? p.competitor_min_price.toLocaleString('ru') + ' ₽' : '—'}
      </td>
      <td>
        <div style="display:flex;gap:4px;flex-wrap:nowrap">
          ${p.suggested_price
            ? `<button class="btn primary" style="font-size:10px;padding:4px 8px"
                 onclick="applyPrice('${p.offer_id}',${p.suggested_price},${p.min_price||0})"
                 title="Применить рекомендованную цену">▲ ${p.suggested_price.toLocaleString('ru')}</button>`
            : ''}
          <button class="btn" style="font-size:10px;padding:4px 8px"
            onclick="openPriceHistory('${p.offer_id}')" title="История цен">📋</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function renderRepricerPagination(total, page, perPage) {
  const el = document.getElementById('repricer-pagination');
  const totalPages = Math.ceil(total / perPage);
  if (totalPages <= 1) { el.innerHTML=''; return; }
  let html = '';
  for (let i=1; i<=totalPages; i++) {
    html += `<button class="btn${i===page?' active':''}" onclick="loadRepricer(${i})">${i}</button> `;
  }
  el.innerHTML = html;
}

async function editRepricerCell(el) {
  if (el.querySelector('input')) return;
  const field = el.dataset.field;
  const offerId = el.dataset.offer;
  const currentText = el.innerText.trim().replace(/[₽%\s,]/g,'');
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
        method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
      });
      // Перерендериваем строку с новыми данными
      await loadRepricer(repricerPage);
      showToast('✓ Сохранено');
    } catch(e) {
      el.innerHTML = displayText || '<span style="color:var(--border)">+</span>';
    }
  }

  input.addEventListener('blur', save);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { el.innerHTML = displayText || '<span style="color:var(--border)">+</span>'; }
  });
}

async function applyPrice(offerId, newPrice, minPrice) {
  if (!confirm(`Установить цену ${newPrice.toLocaleString('ru')} ₽ для артикула ${offerId}?`)) return;
  try {
    const r = await fetch('/api/repricer/apply-price', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({offer_id: offerId, new_price: newPrice, min_price: minPrice, reason: 'auto_margin'})
    }).then(r=>r.json());

    if (r.ok && r.updated) {
      showToast(`✓ Цена ${newPrice.toLocaleString('ru')} ₽ применена`);
      loadRepricer(repricerPage);
    } else {
      const err = (r.errors||[]).map(e=>e.message).join(', ');
      showToast(`Ошибка: ${err || 'неизвестная'}`, 'error');
    }
  } catch(e) { showToast('Ошибка применения цены', 'error'); }
}

async function openPriceHistory(offerId) {
  document.getElementById('history-modal-title').textContent = `История цен: ${offerId}`;
  document.getElementById('history-modal-body').innerHTML = '<div class="state-msg">Загрузка...</div>';
  document.getElementById('history-modal').style.display = 'flex';

  try {
    const r = await fetch(`/api/repricer/history/${encodeURIComponent(offerId)}`).then(r=>r.json());
    const hist = r.history || [];
    if (!hist.length) {
      document.getElementById('history-modal-body').innerHTML = '<div class="state-msg">История пуста</div>';
      return;
    }
    document.getElementById('history-modal-body').innerHTML = `
      <table style="width:100%">
        <thead><tr><th>Дата</th><th>Было</th><th>Стало</th><th>Причина</th><th>Кто</th></tr></thead>
        <tbody>${hist.map(h => `<tr>
          <td style="font-size:11px;color:var(--text-dim)">${h.changed_at.slice(0,16).replace('T',' ')}</td>
          <td style="color:var(--red)">${h.old_price.toLocaleString('ru')} ₽</td>
          <td style="color:var(--green)">${h.new_price.toLocaleString('ru')} ₽</td>
          <td style="color:var(--text-dim);font-size:11px">${h.reason||'—'}</td>
          <td style="color:var(--text-dim);font-size:11px">${h.changed_by||'—'}</td>
        </tr>`).join('')}</tbody>
      </table>`;
  } catch(e) {
    document.getElementById('history-modal-body').innerHTML = '<div class="state-msg error">Ошибка загрузки</div>';
  }
}

function closeHistoryModal() {
  document.getElementById('history-modal').style.display = 'none';
}

// Загружаем репрайсер при переключении на вкладку
const origNavigate = window.navigate;
window.navigate = function(tab) {
  origNavigate(tab);
  if (tab === 'repricer') {
    loadRepricerFilters();
    loadRepricer(1);
  }
};