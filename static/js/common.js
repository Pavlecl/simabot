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

const activeTab = '{{ active_tab }}';
const userRole = '{{ user.role }}';

document.addEventListener('DOMContentLoaded', () => {
  if (activeTab === 'dashboard') loadDashboard();
  if (activeTab === 'orders') loadOrders(1);
  if (activeTab === 'queue') loadQueue();
  if (activeTab === 'users') loadUsers();
});

// =====================================================================