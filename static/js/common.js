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