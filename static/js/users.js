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
// TOAST
// =====================================================================
let toastTimer;

// =====================================================================

loadUsers();

document.getElementById('create-user-btn').addEventListener('click', createUser);