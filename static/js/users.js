// ПОЛЬЗОВАТЕЛИ
// =====================================================================
async function loadUsers() {
  try {
    const data = await fetch('/api/users').then(r => r.json());
    const body = document.getElementById('users-list-body');

    if (!data.users || data.users.length === 0) {
      body.innerHTML = '<div class="state-msg">Нет пользователей</div>';
      return;
    }

    body.innerHTML = data.users.map(u => `
      <div class="user-row">
        <div class="user-avatar">${u.username[0].toUpperCase()}</div>
        <div style="flex:1">
          <div class="user-name">${u.username}</div>
          <div class="user-created">Создан: ${u.created_at ? u.created_at.slice(0,10) : '—'}</div>
        </div>
        <span class="role-badge ${u.role}">${u.role}</span>
      </div>
    `).join('');
  } catch(e) {
    document.getElementById('users-list-body').innerHTML =
      '<div class="state-msg" style="color:var(--red)">Ошибка загрузки</div>';
  }
}

async function createUser() {
  const username = document.getElementById('new-username').value.trim();
  const password = document.getElementById('new-password').value.trim();
  const role = document.getElementById('new-role').value;
  const msg = document.getElementById('create-user-msg');

  if (!username || !password) {
    msg.style.color = 'var(--red)';
    msg.textContent = 'Заполните логин и пароль';
    return;
  }
  if (password.length < 6) {
    msg.style.color = 'var(--red)';
    msg.textContent = 'Пароль минимум 6 символов';
    return;
  }

  try {
    const endpoint = role === 'admin'
      ? '/api/setup/create-admin'
      : '/api/setup/create-fulfillment';

    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const data = await resp.json();

    if (!resp.ok) {
      msg.style.color = 'var(--red)';
      msg.textContent = data.detail || 'Ошибка создания';
      return;
    }

    msg.style.color = 'var(--green)';
    msg.textContent = `✓ ${data.message}`;
    document.getElementById('new-username').value = '';
    document.getElementById('new-password').value = '';
    loadUsers();
    showToast('Пользователь создан');
  } catch(e) {
    msg.style.color = 'var(--red)';
    msg.textContent = 'Ошибка сети';
  }
}

// =====================================================================
// МОДАЛЬНОЕ ОКНО
// =====================================================================
function openEdit(order) {
  editingPosting = order.posting_number;
  document.getElementById('modal-posting').textContent = order.posting_number;
  document.getElementById('modal-sur').value = order.sur_number || '';
  document.getElementById('modal-ff-date').value = order.ff_delivery_date
    ? order.ff_delivery_date.slice(0, 16) : '';
  document.getElementById('modal-plan-date').value = order.plan_delivery_date || '';
  document.getElementById('modal-comment').value = order.comment || '';
  document.getElementById('edit-modal').classList.add('open');
}

function closeModal() {
  document.getElementById('edit-modal').classList.remove('open');
  editingPosting = null;
}

document.getElementById('edit-modal').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

async function saveOrder() {
  if (!editingPosting) return;
  const body = {
    sur_number: document.getElementById('modal-sur').value.trim(),
    ff_delivery_date: document.getElementById('modal-ff-date').value || null,
    plan_delivery_date: document.getElementById('modal-plan-date').value.trim(),
    comment: document.getElementById('modal-comment').value.trim(),
  };
  try {
    const resp = await fetch(`/api/orders/${encodeURIComponent(editingPosting)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || resp.statusText);
    }
    closeModal();
    showToast('✓ Сохранено');
    if (activeTab === 'orders') loadOrders(currentPage);
    if (activeTab === 'queue') loadQueue();
    if (activeTab === 'dashboard') loadDashboard();
  } catch(e) {
    showToast(`Ошибка: ${e.message}`, 'error');
  }
}

// =====================================================================
// TOAST
// =====================================================================
let toastTimer;
function showToast(msg, type = '') {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'toast show' + (type === 'error' ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3000);
}

// =====================================================================

document.addEventListener('DOMContentLoaded', () => loadUsers());