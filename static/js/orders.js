// ЗАКАЗЫ
// =====================================================================
function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadOrders(1), 400);
}

function clearFilters() {
  document.getElementById('status-filter').value = '';
  document.getElementById('search-input').value = '';
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  loadOrders(1);
}

function buildOrdersUrl(page, perPage = 50) {
  const status = document.getElementById('status-filter')?.value || '';
  const search = document.getElementById('search-input')?.value.trim() || '';
  const dateFrom = document.getElementById('date-from')?.value || '';
  const dateTo = document.getElementById('date-to')?.value || '';

  let url = `/api/orders?page=${page}&per_page=${perPage}`;
  if (status) url += `&status=${encodeURIComponent(status)}`;
  if (search) url += `&search=${encodeURIComponent(search)}`;
  if (dateFrom) url += `&date_from=${encodeURIComponent(dateFrom)}`;
  if (dateTo) url += `&date_to=${encodeURIComponent(dateTo)}`;
  url += `&sort=${sortAcceptedDir === 'asc' ? 'accepted_asc' : 'accepted_desc'}`;
  return url;
}

async function loadOrders(page) {
  currentPage = page;
  document.getElementById('orders-body').innerHTML =
    '<tr><td colspan="9" class="state-msg">ЗАГРУЗКА...</td></tr>';

  try {
    const data = await fetch(buildOrdersUrl(page)).then(r => r.json());
    totalPages = Math.ceil(data.total / data.per_page) || 1;
    allOrdersCache = data.orders;

    renderOrdersTable(data.orders, 'orders-body', true);

    document.getElementById('orders-count').textContent =
      `Показано ${data.orders.length} из ${data.total}`;
    document.getElementById('page-info').textContent =
      `Стр. ${page} / ${totalPages}`;
    document.getElementById('prev-btn').disabled = page <= 1;
    document.getElementById('next-btn').disabled = page >= totalPages;
  } catch(e) {
    document.getElementById('orders-body').innerHTML =
      `<tr><td colspan="9" class="state-msg" style="color:var(--red)">ОШИБКА ЗАГРУЗКИ</td></tr>`;
  }
}

function renderOrdersTable(orders, tbodyId, showActions) {
  const tbody = document.getElementById(tbodyId);
  if (!orders || orders.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="state-msg">НЕТ ДАННЫХ</td></tr>';
    return;
  }

  const STATUS_RU = {
    'awaiting_packaging': 'Ожидает сборки',
    'awaiting_deliver': 'Готов к отгрузке',
    'processing': 'В обработке',
    'delivered': 'Доставлен',
    'cancelled': 'Отменён'
  };

  function formatDate(s) {
    if (!s) return '—';
    // ISO yyyy-mm-dd
    if (s.length >= 10 && s[4] === '-') {
      const d = s.slice(0,10).split('-');
      return d[2] + '.' + d[1] + '.' + d[0];
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

  function copyCell(text, el) {
    if (!text || text === '—') return;
    navigator.clipboard.writeText(text).then(() => {
      const orig = el.style.color;
      el.style.color = 'var(--green)';
      setTimeout(() => { el.style.color = orig; }, 600);
    });
  }

  tbody.innerHTML = orders.map(o => {
    const firstProduct = (o.products && o.products.length > 0) ? o.products[0] : null;
    const imageUrl = (firstProduct && typeof firstProduct === 'object') ? (firstProduct.image_url || '') : '';
    const offerId = (firstProduct && typeof firstProduct === 'object') ? (firstProduct.offer_id || '') : '';
    const productName = (firstProduct && typeof firstProduct === 'object')
      ? (firstProduct.name || '')
      : (typeof firstProduct === 'string' ? firstProduct : '');
    const totalQty = o.products ? o.products.reduce((s, p) => s + (typeof p === 'object' ? (p.quantity || 0) : 0), 0) : 0;
    const extraCount = (o.products && o.products.length > 1) ? ` +${o.products.length - 1} поз.` : '';
    const acceptedDate = formatDate(o.ozon_accepted_at);
    const planDate = formatDate(o.plan_delivery_date);
    const statusRu = STATUS_RU[o.ozon_status] || o.ozon_status || '—';

    // Поставка - редактируемая ячейка для admin
    const deliveryCell = (userRole === 'admin')
      ? `<td class="editable-cell" data-field="plan_delivery_date" data-posting="${o.posting_number}"
           onclick="editCell(this)" title="Нажмите для редактирования">
           ${planDate !== '—' ? planDate : '<span style="color:var(--text-dim)">—</span>'}
         </td>`
      : `<td>${planDate}</td>`;

    // СУР - редактируемый для admin, копируемый для остальных
    const surCell = (userRole === 'admin')
      ? `<td class="editable-cell" data-field="sur_number" data-posting="${o.posting_number}"
             onclick="editCell(this)" title="Нажмите для редактирования">
           ${o.sur_number
             ? `<span style="color:var(--green)">${o.sur_number}</span>`
             : '<span style="color:var(--yellow)">нет</span>'}
         </td>`
      : (o.sur_number
          ? `<td class="copy-cell" onclick="copyCell('${o.sur_number}', this)" title="Скопировать">
               <span style="color:var(--green)">${o.sur_number}</span>
             </td>`
          : `<td><span style="color:var(--yellow)">нет</span></td>`);

    // Номер счёта - редактируемый для admin, копируемый для остальных
    const invoiceCell = (userRole === 'admin')
      ? `<td class="editable-cell" data-field="sima_order_number" data-posting="${o.posting_number}"
             onclick="editCell(this)" title="Нажмите для редактирования">
           ${o.sima_order_number
             ? `<span style="color:var(--text)">${o.sima_order_number}</span>`
             : '<span style="color:var(--border)">+</span>'}
         </td>`
      : (o.sima_order_number
          ? `<td class="copy-cell" onclick="copyCell('${o.sima_order_number}', this)" title="Скопировать">
               <span style="color:var(--text)">${o.sima_order_number}</span>
             </td>`
          : `<td><span style="color:var(--text-dim)">—</span></td>`);

    // Комментарий - редактируемый для всех
    const commentCell = `<td class="editable-cell comment-cell" data-field="comment" data-posting="${o.posting_number}"
         onclick="editCell(this)" title="Нажмите для редактирования"
         style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-dim)">
         ${o.comment || '<span style="color:var(--border)">+</span>'}
       </td>`;

    // Кнопка редактирования - только для admin
    const editBtn = (showActions && userRole === 'admin')
      ? `<td><button class="btn" onclick='openEdit(${JSON.stringify(o)})' title="Редактировать">✎</button></td>`
      : (showActions ? '<td></td>' : '');

    return `<tr>
      <td class="copy-cell code" style="white-space:nowrap"
          onclick="copyCell('${o.posting_number}', this)" title="Нажмите чтобы скопировать">
        ${o.posting_number}
      </td>
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
               onclick="copyCell('${offerId}', this)" title="Нажмите чтобы скопировать"
             >${offerId}${totalQty > 1 ? ` ×${totalQty}` : ''}${extraCount}</div>`
          : ''}
        <div style="color:var(--text-dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:190px">${productName || '—'}</div>
      </td>
      ${deliveryCell}
      ${surCell}
      ${invoiceCell}
      ${commentCell}
      ${editBtn}
    </tr>`;
  }).join('');
}


// =====================================================================
// ЭКСПОРТ В EXCEL
// =====================================================================
async function exportExcel() {
  showToast('Подготовка экспорта...');
  try {
    // Берём все записи с текущим фильтром (до 5000)
    const data = await fetch(buildOrdersUrl(1, 5000)).then(r => r.json());
    const orders = data.orders;

    if (!orders || orders.length === 0) {
      showToast('Нет данных для экспорта', 'error');
      return;
    }

    // Заголовки
    const headers = [
      'Отправление', 'Статус', 'Заказ Сима', 'Дата Сима',
      'Поставка (план)', 'Поставка (ФФ)', 'СУР', 'Комментарий', 'Добавлен'
    ];

    // Строки CSV
    const rows = orders.map(o => [
      o.posting_number,
      o.ozon_status || '',
      o.sima_order_number || '',
      o.sima_order_date || '',
      o.plan_delivery_date || '',
      o.ff_delivery_date ? o.ff_delivery_date.slice(0,10) : '',
      o.sur_number || '',
      (o.comment || '').replace(/"/g, '""'),
      o.added_at ? o.added_at.slice(0,10) : ''
    ]);

    // Собираем CSV с BOM для корректного открытия в Excel
    const BOM = '\uFEFF';
    const csv = BOM + [headers, ...rows]
      .map(row => row.map(v => `"${v}"`).join(';'))
      .join('\n');

    // Скачиваем файл
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const date = new Date().toISOString().slice(0,10);
    a.download = `sima_orders_${date}.csv`;
    a.click();
    URL.revokeObjectURL(url);

    showToast(`✓ Экспортировано ${orders.length} строк`);
  } catch(e) {
    showToast('Ошибка экспорта', 'error');
  }
}

// =====================================================================