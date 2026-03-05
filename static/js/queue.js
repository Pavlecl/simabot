// ОЧЕРЕДЬ
// =====================================================================
async function loadQueue() {
  document.getElementById('queue-grid').innerHTML = '<div class="state-msg">ЗАГРУЗКА...</div>';
  try {
    const data = await fetch('/api/queue').then(r => r.json());
    document.getElementById('queue-count').textContent = `Итого: ${data.total}`;

    if (!data.queue || data.queue.length === 0) {
      document.getElementById('queue-grid').innerHTML = '<div class="state-msg">✅ ОЧЕРЕДЬ ПУСТА</div>';
      return;
    }

    document.getElementById('queue-grid').innerHTML = data.queue.map(item => `
      <div class="queue-card ${item.sur_number ? 'has-sur' : 'no-sur'}">
        <div class="queue-posting">${item.posting_number}</div>
        <div class="queue-meta">
          <div class="queue-meta-item">
            <span class="queue-meta-key">Сима: </span>
            <span class="queue-meta-value">${item.sima_order_number || '—'}</span>
          </div>
          <div class="queue-meta-item">
            <span class="queue-meta-key">Поставка: </span>
            <span class="queue-meta-value">${item.plan_delivery_date || '—'}</span>
          </div>
          <div class="queue-meta-item">
            <span class="queue-meta-key">СУР: </span>
            <span class="queue-meta-value" style="color:${item.sur_number ? 'var(--green)' : 'var(--yellow)'}">
              ${item.sur_number || 'не заполнен'}
            </span>
          </div>
          <div class="queue-meta-item">
            <span class="queue-meta-key">Добавлен: </span>
            <span class="queue-meta-value">${item.added_at ? item.added_at.slice(0,10) : '—'}</span>
          </div>
        </div>
        ${item.products.length ? `
        <div class="products-list">
          ${item.products.map(p => `
            <div class="product-item">
              <span>${typeof p === 'string' ? p : (p.name || p.offer_id || p.sku || '?')}</span>
              ${typeof p === 'object' && p.quantity ? `<span class="qty">×${p.quantity}</span>` : ''}
            </div>
          `).join('')}
        </div>` : ''}
        ${userRole === 'admin' ? `
        <div style="margin-top:14px">
          <button class="btn primary" style="width:100%;font-size:10px" onclick='openEdit(${JSON.stringify({
            posting_number: item.posting_number,
            sur_number: item.sur_number,
            ff_delivery_date: null,
            plan_delivery_date: item.plan_delivery_date,
            comment: item.comment
          })})'>✎ РЕДАКТИРОВАТЬ</button>
        </div>` : ''}
      </div>
    `).join('');
  } catch(e) {
    document.getElementById('queue-grid').innerHTML =
      '<div class="state-msg" style="color:var(--red)">ОШИБКА ЗАГРУЗКИ</div>';
  }
}

// =====================================================================