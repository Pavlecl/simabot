// =====================================================================
// ТЕМА
// =====================================================================
const THEME_KEY = 'sima_theme';

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.getElementById('theme-btn').textContent = theme === 'dark' ? '☀️' : '🌙';
  localStorage.setItem(THEME_KEY, theme);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  applyTheme(current === 'dark' ? 'light' : 'dark');
}

// Применяем сохранённую тему сразу
(function() {
  const saved = localStorage.getItem(THEME_KEY) || 'dark';
  applyTheme(saved);
})();

// =====================================================================
// СОСТОЯНИЕ
// =====================================================================
let currentPage = 1;
let totalPages = 1;
let editingPosting = null;
let searchTimer = null;
let allOrdersCache = []; // кэш для Excel экспорта


// Инициализация — в каждом JS файле страницы

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


function showToast(msg, type = '') {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'toast show' + (type === 'error' ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3000);
}