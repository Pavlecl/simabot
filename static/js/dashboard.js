// ДАШБОРД
// =====================================================================
async function loadDashboard() {
  try {
    const stats = await fetch('/api/stats').then(r => r.json());

    document.getElementById('s-total').textContent = stats.total ?? '—';
    document.getElementById('s-today').textContent = stats.today ?? '—';
    document.getElementById('s-overdue').textContent = stats.overdue ?? '—';
    document.getElementById('s-virtual').textContent = stats.virtual ?? '—';

    if (stats.last_sync) {
      document.getElementById('last-sync-time').textContent = stats.last_sync;
    }

    // Таблица просроченных
    const tbody = document.getElementById('overdue-body');
    document.getElementById('overdue-count').textContent =
      stats.overdue_orders?.length ? `${stats.overdue_orders.length} записей` : '';

    if (!stats.overdue_orders || stats.overdue_orders.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="state-msg" style="color:var(--green)">✅ ПРОСРОЧЕННЫХ НЕТ</td></tr>';
      return;
    }

    const STATUS_RU = {
      'awaiting_packaging': 'Ожидает сборки',
      'awaiting_deliver': 'Готов к отгрузке',
      'processing': 'В обработке',
      'delivered': 'Доставлен',
      'cancelled': 'Отменён'
    };

    function fmtDate(s) {
      if (!s) return '—';
      // ISO yyyy-mm-dd → дд.мм.гггг
      if (s.length >= 10 && s[4] === '-') {
        const p = s.slice(0,10).split('-');
        return p[2] + '.' + p[1] + '.' + p[0];
      }
      // Короткий д.мм.гг → дд.мм.20гг
      if (s.includes('.')) {
        const parts = s.split('.');
        if (parts.length === 3) {
          const d = parts[0].padStart(2, '0');
          const m = parts[1].padStart(2, '0');
          const y = parts[2].length === 2 ? '20' + parts[2] : parts[2];
          return d + '.' + m + '.' + y;
        }
      }
      return s;
    }

    tbody.innerHTML = stats.overdue_orders.map(o => {
      const firstProduct = (o.products && o.products.length > 0) ? o.products[0] : null;
      const imageUrl = (firstProduct && typeof firstProduct === 'object') ? (firstProduct.image_url || '') : '';
      const offerId = (firstProduct && typeof firstProduct === 'object') ? (firstProduct.offer_id || '') : '';
      const productName = (firstProduct && typeof firstProduct === 'object')
        ? (firstProduct.name || '')
        : (typeof firstProduct === 'string' ? firstProduct : '');
      const acceptedDate = fmtDate(o.ozon_accepted_at);
      const statusRu = STATUS_RU[o.ozon_status] || o.ozon_status || '—';

      return `<tr class="overdue-row">
        <td class="copy-cell code" style="white-space:nowrap"
            onclick="copyCell('${o.posting_number}', this)" title="Скопировать">${o.posting_number}</td>
        <td><span class="status ${o.ozon_status}">${statusRu}</span></td>
        <td style="white-space:nowrap;color:var(--text-dim)">${acceptedDate}</td>
        <td style="width:52px;padding:6px 8px">
          ${imageUrl
            ? `<img src="${imageUrl}" style="width:44px;height:44px;object-fit:cover;border:1px solid var(--border);display:block" loading="lazy" onerror="this.style.display='none'">`
            : '<div style="width:44px;height:44px;background:var(--surface2);border:1px solid var(--border)"></div>'}
        </td>
        <td style="max-width:200px">
          ${offerId
            ? `<div class="copy-cell code" style="font-size:11px;display:inline-block"
                   onclick="copyCell('${offerId}', this)" title="Скопировать">${offerId}</div>`
            : ''}
          <div style="color:var(--text-dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${productName || '—'}</div>
        </td>
        <td>${o.sima_order_number
          ? `<span class="copy-cell" onclick="copyCell('${o.sima_order_number}', this)" title="Скопировать">${o.sima_order_number}</span>`
          : '<span style="color:var(--text-dim)">—</span>'}</td>
        <td style="color:var(--red);font-weight:600">${fmtDate(o.plan_delivery_date)}</td>
        <td>${o.sur_number
          ? `<span class="copy-cell" style="color:var(--green)" onclick="copyCell('${o.sur_number}', this)" title="Скопировать">${o.sur_number}</span>`
          : '<span style="color:var(--yellow)">нет</span>'}</td>
      </tr>`;
    }).join('');

  } catch(e) {
    showToast('Ошибка загрузки дашборда', 'error');
  }
}

async function syncOzon() {
  const btn = document.getElementById('sync-btn');
  const timeEl = document.getElementById('last-sync-time');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinning">⟳</span> Синхронизация...';
  timeEl.textContent = 'получаем данные...';

  try {
    const result = await fetch('/api/sync', { method: 'POST' }).then(r => r.json());
    if (result.error) {
      showToast(`Ошибка: ${result.error}`, 'error');
    } else {
      const removed = result.removed ? `, убрано ${result.removed}` : '';
      showToast(`✓ Обновлено ${result.synced} заказов${removed}`);
      timeEl.textContent = result.last_sync || '—';
      // Перезагружаем текущую вкладку
      if (activeTab === 'dashboard') loadDashboard();
      if (activeTab === 'orders') loadOrders(currentPage);
    }
  } catch(e) {
    showToast('Ошибка синхронизации', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⟳ Обновить данные с Ozon';
  }
}

// =====================================================================
// КОПИРОВАНИЕ И INLINE-РЕДАКТИРОВАНИЕ
// =====================================================================
function copyCell(text, el) {
  if (!text || text === '—') return;
  navigator.clipboard.writeText(text).then(() => {
    const orig = el.style.color;
    el.style.color = 'var(--green)';
    setTimeout(() => { el.style.color = orig; }, 700);
    showToast('✓ Скопировано');
  }).catch(() => {
    // fallback для старых браузеров
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast('✓ Скопировано');
  });
}

function editCell(el) {
  if (el.querySelector('input')) return; // уже редактируется
  const field = el.dataset.field;
  const posting = el.dataset.posting;

  // comment редактируют все; plan_delivery_date, sur_number, sima_order_number — только admin
  const adminOnlyFields = ['plan_delivery_date', 'sur_number', 'sima_order_number'];
  if (adminOnlyFields.includes(field) && userRole !== 'admin') return;

  const currentText = el.innerText.trim();
  const displayText = (currentText === '+' || currentText === '—') ? '' : currentText;

  el.innerHTML = '';
  const input = document.createElement('input');
  input.className = 'inline-input';

  if (field === 'plan_delivery_date') {
    input.type = 'date';
    // Конвертируем формат дд.мм.гггг → гггг-мм-дд для input[type=date]
    if (displayText && displayText.includes('.')) {
      const parts = displayText.split('.');
      if (parts.length === 3) input.value = parts[2] + '-' + parts[1] + '-' + parts[0];
    } else {
      input.value = displayText;
    }
  } else {
    input.type = 'text';
    input.value = displayText;
  }
  el.appendChild(input);
  input.focus();
  input.select();

  async function save() {
    let val = input.value.trim();
    // Для даты конвертируем гггг-мм-дд → дд.мм.гггг (формат бота)
    if (field === 'plan_delivery_date' && val && val.includes('-') && val.length === 10) {
      const parts = val.split('-');
      val = parts[2] + '.' + parts[1] + '.' + parts[0];
    }
    try {
      const body = {};
      body[field] = val;
      const resp = await fetch(`/api/orders/${encodeURIComponent(posting)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (resp.ok) {
        // Для СУР и счёта красим по-своему
        let displayVal;
        if (!val) {
          displayVal = field === 'comment' ? '<span style="color:var(--border)">+</span>' : '<span style="color:var(--text-dim)">—</span>';
        } else if (field === 'sur_number') {
          displayVal = `<span style="color:var(--green)">${val}</span>`;
        } else if (field === 'sima_order_number') {
          displayVal = `<span style="color:var(--text)">${val}</span>`;
        } else {
          displayVal = val;
        }
        el.innerHTML = displayVal;
        showToast('✓ Сохранено');
        // Обновляем данные в кэше
        const order = allOrdersCache.find(o => o.posting_number === posting);
        if (order) order[field] = val;
      } else {
        showToast('Ошибка сохранения', 'error');
        el.innerHTML = displayText || '<span style="color:var(--text-dim)">—</span>';
      }
    } catch(e) {
      showToast('Ошибка', 'error');
      el.innerHTML = displayText || '<span style="color:var(--text-dim)">—</span>';
    }
  }

  input.addEventListener('blur', save);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') {
      el.innerHTML = displayText || (field === 'comment' ? '<span style="color:var(--border)">+</span>' : '<span style="color:var(--text-dim)">—</span>');
    }
  });
}

// Сортировка по дате принятия
let sortAcceptedDir = 'asc';
function sortByAccepted() {
  sortAcceptedDir = sortAcceptedDir === 'asc' ? 'desc' : 'asc';
  document.getElementById('sort-icon').textContent = sortAcceptedDir === 'asc' ? '↑' : '↓';
  // Сортировка серверная — перезагружаем текущую страницу
  loadOrders(currentPage);
}

async function syncOzonOrders() {
  const btn = document.getElementById('orders-sync-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinning">⟳</span> Синхронизация...';
  try {
    const result = await fetch('/api/sync', { method: 'POST' }).then(r => r.json());
    if (result.error) {
      showToast(`Ошибка: ${result.error}`, 'error');
    } else {
      const removedO = result.removed ? `, убрано ${result.removed}` : '';
      showToast(`✓ Обновлено ${result.synced} заказов${removedO}`);
      loadOrders(currentPage);
    }
  } catch(e) {
    showToast('Ошибка синхронизации', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⟳ Обновить с Ozon';
  }
}

// =====================================================================