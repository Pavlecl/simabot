// Трансляция остатков Сима-Ленд -> Ozon FBS. Раздел управления.
const SB = {
  itemsPage: 1,
  itemsSearch: '',
  itemsFilter: '',
  cfg: null,
};

const $ = (id) => document.getElementById(id);
const nf = (n) => (n == null ? '—' : Number(n).toLocaleString('ru-RU'));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.status;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

// ---------------------------------------------------------------- вкладки
document.querySelectorAll('.sb-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.sb-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.sb-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    $('p-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'items') loadItems();
    if (btn.dataset.tab === 'disable') loadDisable();
    if (btn.dataset.tab === 'fasovka') loadFasovka();
    if (btn.dataset.tab === 'settings') loadSettings();
    if (btn.dataset.tab === 'journal') loadJournal();
  });
});

// ---------------------------------------------------------------- обзор
async function loadOverview() {
  let d;
  try { d = await api('/api/stock-broadcast/overview'); }
  catch (e) { $('sb-status-line').textContent = 'ошибка: ' + e.message; return; }
  SB.cfg = d.config;
  SB._status = d.status || {};

  $('sb-enabled').checked = !!d.config.enabled;
  $('sb-dryrun').checked = !!d.config.dry_run;

  const wh = d.config.warehouse_id ? `склад ${d.config.warehouse_id}` : 'склад не выбран';
  const acc = d.config.account_name || 'кабинет не выбран';
  $('sb-target-badge').textContent = `${acc} · ${wh}`;

  const st = d.status || {};
  let line = st.running ? `⏳ выполняется: ${st.phase || '…'}` : 'ожидание';
  if (st.last_run) line += ` · последний запуск ${fmtDt(st.last_run)}`;
  if (st.next_run && d.config.enabled) line += ` · следующий ~${fmtDt(st.next_run)}`;
  $('sb-status-line').textContent = line;

  const c = d.counts;
  $('sb-cards').innerHTML = [
    ['items_enabled', 'включено артикулов', ''],
    ['items_total', 'всего в списке', ''],
    ['disabled', 'отключён остаток', c.disabled ? 'red' : ''],
    ['fasovka', 'ручная фасовка', ''],
    ['rejected', 'Ozon отклонил', c.rejected ? 'red' : ''],
  ].map(([k, l, cls]) => `<div class="stat-card ${cls}"><div class="stat-value">${nf(c[k])}</div><div class="stat-label">${l}</div></div>`).join('');

  $('sb-conflict-warn').innerHTML = c.stock_items_conflict
    ? `<div class="sb-warn">⚠️ ${c.stock_items_conflict} артикул(ов) одновременно включены и здесь, и в разделе «Остатки» (stock-manage). Два писателя FBS-остатков будут спорить — оставьте товар только в одном месте.</div>`
    : '';

  const lc = d.last_cycle;
  if (lc) renderLastCycle(lc);
}

function renderLastCycle(lc) {
  const delta = lc.total_after - lc.total_before;
  const errs = (lc.errors || []).length
    ? `<div class="sb-alert" style="margin-top:8px">${lc.errors.map(esc).join('<br>')}</div>` : '';
  const notes = (lc.notes || []).map(n => `<div class="muted" style="font-size:11px">ℹ️ ${esc(n)}</div>`).join('');
  $('sb-last-cycle').innerHTML = `
    <div class="sb-row" style="gap:20px">
      <span>#${lc.id} · ${fmtDt(lc.started_at)} · ${lc.seconds ?? '?'} с ${lc.dry_run ? '· <b style="color:var(--yellow)">DRY-RUN</b>' : ''}</span>
    </div>
    <div style="margin-top:8px">остаток: <b>${nf(lc.total_before)}</b> → <b>${nf(lc.total_after)}</b>
      <span class="${delta < 0 ? '' : 'muted'}">(${delta > 0 ? '+' : ''}${nf(delta)})</span></div>
    <div style="margin-top:4px">записано изменений: <b>${lc.written}</b>
      (вкл ${lc.turned_on}, обнул ${lc.turned_off}, изм ${lc.changed}) · план ${lc.planned}</div>
    <div style="margin-top:4px">вычтено по заказам: ${lc.orders_subtracted} из ${lc.orders_total}</div>
    ${notes}${errs}`;
}

function fmtDt(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
}

$('sb-enabled').addEventListener('change', e => saveConfig({enabled: e.target.checked}));
$('sb-dryrun').addEventListener('change', e => saveConfig({dry_run: e.target.checked}));
$('sb-run-btn').addEventListener('click', async () => {
  $('sb-run-btn').disabled = true;
  try {
    await api('/api/stock-broadcast/run', {method: 'POST'});
    showToast('Цикл запущен');
    pollStatus();
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
  finally { setTimeout(() => { $('sb-run-btn').disabled = false; }, 3000); }
});

// Пока цикл идёт — чаще обновляем обзор, потом останавливаемся.
let statusTimer = null;
function pollStatus() {
  clearInterval(statusTimer);
  let n = 0;
  statusTimer = setInterval(async () => {
    n += 1;
    await loadOverview();
    const running = SB._status && SB._status.running;
    if (n > 60 || (n > 2 && !running)) clearInterval(statusTimer);
  }, 3000);
}

async function saveConfig(patch) {
  try {
    const d = await api('/api/stock-broadcast/config', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(patch),
    });
    SB.cfg = d.config;
    showToast('Сохранено');
    loadOverview();
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
}

// ---------------------------------------------------------------- артикулы
let searchDebounce;
$('sb-search').addEventListener('input', e => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => { SB.itemsSearch = e.target.value.trim(); SB.itemsPage = 1; loadItems(); }, 300);
});
$('sb-filter').addEventListener('change', e => { SB.itemsFilter = e.target.value; SB.itemsPage = 1; loadItems(); });

async function loadItems() {
  const p = new URLSearchParams({search: SB.itemsSearch, page: SB.itemsPage, per_page: 200, only: SB.itemsFilter});
  let d;
  try { d = await api('/api/stock-broadcast/items?' + p); }
  catch (e) { $('sb-items-body').innerHTML = `<tr><td colspan="10" class="muted">${e.message}</td></tr>`; return; }

  $('sb-items-count').textContent = `${d.total} артикулов`;
  if (!d.items.length) {
    $('sb-items-body').innerHTML = `<tr><td colspan="10" class="muted" style="padding:20px">пусто</td></tr>`;
    $('sb-items-pager').innerHTML = '';
    return;
  }
  $('sb-items-body').innerHTML = d.items.map(it => {
    const s = it.state || {};
    const balance = s.branch === 'Достаточно' ? '∞' : (s.sima_balance ?? '—');
    let tag = '';
    if (it.in_disable_list) tag = '<span class="tag zero">ОтклОстаток</span>';
    else if (s.branch) tag = `<span class="tag ${s.last_amount > 0 ? 'on' : 'zero'}">${esc(s.branch)}</span>`;
    return `<tr>
      <td><span class="copy-cell" onclick="copyCell('${esc(it.offer_id)}', this)">${esc(it.offer_id)}</span></td>
      <td class="muted">${esc(it.name || '')}</td>
      <td><input type="checkbox" ${it.enabled ? 'checked' : ''} onchange="toggleItem('${esc(it.offer_id)}', this.checked)"></td>
      <td class="num">${balance}</td>
      <td class="num">${s.real_min ?? '—'}${s.real_min_src ? `<span class="muted" style="font-size:9px"> ${esc(s.real_min_src)}</span>` : ''}</td>
      <td class="num">${s.calc_qty ?? '—'}</td>
      <td class="num">${s.orders || 0}</td>
      <td class="num"><b>${s.last_amount ?? '—'}</b></td>
      <td>${tag} <span class="muted" style="font-size:11px">${esc(s.reason || '')}</span></td>
      <td><button class="link-btn" onclick="delItem('${esc(it.offer_id)}')">✕</button></td>
    </tr>`;
  }).join('');

  const pages = Math.ceil(d.total / 200);
  $('sb-items-pager').innerHTML = pages > 1
    ? `<button class="btn" ${SB.itemsPage <= 1 ? 'disabled' : ''} onclick="SB.itemsPage--;loadItems()">←</button>
       <span class="muted">${SB.itemsPage} / ${pages}</span>
       <button class="btn" ${SB.itemsPage >= pages ? 'disabled' : ''} onclick="SB.itemsPage++;loadItems()">→</button>` : '';
}

async function toggleItem(offerId, enabled) {
  try {
    await api('/api/stock-broadcast/items/' + encodeURIComponent(offerId), {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled}),
    });
    showToast(enabled ? 'Включён' : 'Выключен');
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); loadItems(); }
}

async function delItem(offerId) {
  if (!confirm(`Убрать ${offerId} из трансляции?`)) return;
  try {
    await api('/api/stock-broadcast/items/' + encodeURIComponent(offerId), {method: 'DELETE'});
    loadItems();
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
}

$('sb-add-btn').addEventListener('click', async () => {
  const txt = $('sb-add-text').value.trim();
  if (!txt) return;
  try {
    const d = await api('/api/stock-broadcast/items', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({offer_ids: txt, source: 'import_text'}),
    });
    showToast(`Добавлено: ${d.added}`);
    $('sb-add-text').value = '';
    loadItems();
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
});

$('sb-import-input').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  try {
    const d = await api('/api/stock-broadcast/items/import', {method: 'POST', body: fd});
    showToast(`Включено: ${d.enabled}`);
    loadItems();
  } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  e.target.value = '';
});

// ---------------------------------------------------------------- отключение остатка
async function loadDisable() {
  let d;
  try { d = await api('/api/stock-broadcast/disable'); }
  catch (e) { $('sb-dis-body').innerHTML = `<tr><td colspan="5" class="muted">${e.message}</td></tr>`; return; }
  $('sb-dis-body').innerHTML = d.items.length ? d.items.map(r => `<tr>
    <td><span class="copy-cell" onclick="copyCell('${esc(r.offer_id)}', this)">${esc(r.offer_id)}</span></td>
    <td class="muted">${esc(r.reason || '')}</td>
    <td class="muted">${esc(r.added_by || '')}</td>
    <td class="muted">${fmtDt(r.added_at)}</td>
    <td><button class="link-btn" onclick="delDisable('${esc(r.offer_id)}')">✕</button></td>
  </tr>`).join('') : `<tr><td colspan="5" class="muted" style="padding:20px">список пуст</td></tr>`;
}

$('sb-dis-add').addEventListener('click', async () => {
  const ids = $('sb-dis-ids').value.trim();
  if (!ids) return;
  try {
    await api('/api/stock-broadcast/disable', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({offer_ids: ids, reason: $('sb-dis-reason').value.trim()}),
    });
    $('sb-dis-ids').value = ''; $('sb-dis-reason').value = '';
    loadDisable();
    showToast('Добавлено');
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
});

async function delDisable(offerId) {
  try { await api('/api/stock-broadcast/disable/' + encodeURIComponent(offerId), {method: 'DELETE'}); loadDisable(); }
  catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
}

// ---------------------------------------------------------------- фасовка
async function loadFasovka() {
  let d;
  try { d = await api('/api/stock-broadcast/fasovka'); }
  catch (e) { $('sb-fas-body').innerHTML = `<tr><td colspan="6" class="muted">${e.message}</td></tr>`; return; }
  $('sb-fas-body').innerHTML = d.items.length ? d.items.map(r => `<tr>
    <td><span class="copy-cell" onclick="copyCell('${esc(r.offer_id)}', this)">${esc(r.offer_id)}</span></td>
    <td class="num">${r.real_min ?? '—'}</td>
    <td>${r.disabled ? '<span class="tag off">да</span>' : '<span class="muted">—</span>'}</td>
    <td class="num">${r.current_real_min ?? '—'}</td>
    <td class="muted">${esc(r.current_src || '')}</td>
    <td><button class="link-btn" onclick="delFasovka('${esc(r.offer_id)}')">✕</button></td>
  </tr>`).join('') : `<tr><td colspan="6" class="muted" style="padding:20px">переопределений нет</td></tr>`;
}

$('sb-fas-add').addEventListener('click', async () => {
  const oid = $('sb-fas-id').value.trim();
  if (!oid) return;
  try {
    await api('/api/stock-broadcast/fasovka', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        offer_id: oid,
        real_min: $('sb-fas-rm').value || null,
        disabled: $('sb-fas-off').checked,
      }),
    });
    $('sb-fas-id').value = ''; $('sb-fas-rm').value = ''; $('sb-fas-off').checked = false;
    loadFasovka();
    showToast('Сохранено');
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
});

async function delFasovka(offerId) {
  try { await api('/api/stock-broadcast/fasovka/' + encodeURIComponent(offerId), {method: 'DELETE'}); loadFasovka(); }
  catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
}

// ---------------------------------------------------------------- настройки
async function loadSettings() {
  if (!SB.cfg) await loadOverview();
  const cfg = SB.cfg || {};
  let accounts = [];
  try { accounts = (await api('/api/accounts')).accounts || []; } catch (e) {}
  $('sb-set-account').innerHTML = '<option value="">— выбрать —</option>' +
    accounts.map(a => `<option value="${a.id}" ${a.id === cfg.ozon_account_id ? 'selected' : ''}>${esc(a.name)}</option>`).join('');

  $('sb-set-budget').value = cfg.budget_limit ?? 50000;
  $('sb-set-cutoff').value = cfg.cutoff_balance ?? 20;
  $('sb-set-divisor').value = cfg.safety_divisor ?? 1.5;
  $('sb-set-stockid').value = cfg.sima_stock_id ?? 115;
  $('sb-set-interval').value = cfg.cycle_minutes ?? 14;
  $('sb-set-tgmode').value = cfg.tg_report_mode || 'onchange';

  await loadWarehouses(cfg.ozon_account_id, cfg.warehouse_id);
}

$('sb-set-account').addEventListener('change', e => loadWarehouses(e.target.value, null));

async function loadWarehouses(accountId, selected) {
  const sel = $('sb-set-warehouse');
  if (!accountId) { sel.innerHTML = '<option value="">— сначала кабинет —</option>'; return; }
  sel.innerHTML = '<option>загрузка…</option>';
  try {
    const d = await api('/api/stock-broadcast/warehouses?account_id=' + accountId);
    sel.innerHTML = '<option value="">— выбрать —</option>' +
      (d.warehouses || []).map(w => `<option value="${w.id}" ${w.id === selected ? 'selected' : ''}>${esc(w.name)} (${w.id})</option>`).join('');
  } catch (e) { sel.innerHTML = `<option value="">ошибка: ${e.message}</option>`; }
}

$('sb-set-save').addEventListener('click', async () => {
  await saveConfig({
    ozon_account_id: $('sb-set-account').value || null,
    warehouse_id: $('sb-set-warehouse').value || null,
    budget_limit: $('sb-set-budget').value,
    cutoff_balance: $('sb-set-cutoff').value,
    safety_divisor: $('sb-set-divisor').value,
    sima_stock_id: $('sb-set-stockid').value,
    cycle_minutes: $('sb-set-interval').value,
    tg_report_mode: $('sb-set-tgmode').value,
  });
});

// ---------------------------------------------------------------- журнал
async function loadJournal() {
  let d;
  try { d = await api('/api/stock-broadcast/cycles'); }
  catch (e) { $('sb-journal-body').innerHTML = `<tr><td colspan="8" class="muted">${e.message}</td></tr>`; return; }

  $('sb-journal-body').innerHTML = d.cycles.length ? d.cycles.map(c => {
    const notes = [...(c.notes || []), ...(c.errors || [])].map(esc).join('; ');
    return `<tr style="cursor:pointer" onclick="openCycle(${c.id})">
      <td>#${c.id}</td>
      <td class="muted">${fmtDt(c.started_at)}</td>
      <td class="muted">${esc(c.trigger || '')}${c.dry_run ? ' · dry' : ''}</td>
      <td class="num">${c.planned}</td>
      <td class="num">${c.written}</td>
      <td class="muted">${nf(c.total_before)} → ${nf(c.total_after)}</td>
      <td class="muted" style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${notes}</td>
      <td>›</td>
    </tr>`;
  }).join('') : `<tr><td colspan="8" class="muted" style="padding:20px">циклов ещё не было</td></tr>`;

  try {
    const rj = await api('/api/stock-broadcast/rejected');
    $('sb-rejected-line').textContent = rj.items.length
      ? `Ozon отклоняет ${rj.items.length}: ${rj.items.slice(0, 5).map(x => x.offer_id).join(', ')}` : '';
  } catch (e) {}
}

$('sb-journal-refresh').addEventListener('click', loadJournal);

async function openCycle(id) {
  let d;
  try { d = await api('/api/stock-broadcast/cycles/' + id); }
  catch (e) { showToast('Ошибка: ' + e.message, 'error'); return; }
  $('sb-cycle-detail').style.display = 'block';
  $('sb-cd-id').textContent = '#' + id;
  $('sb-cd-body').innerHTML = (d.plan || []).map(p => `<tr>
    <td>${esc(p.offer_id)}</td>
    <td class="num">${p.enough ? '∞' : (p.balance ?? '—')}</td>
    <td class="num">${p.real_min ?? '—'}</td>
    <td class="num">${p.calc_qty ?? '—'}</td>
    <td class="num">${p.orders || 0}</td>
    <td class="num"><b>${p.want}</b></td>
    <td class="num muted">${p.was}</td>
    <td class="muted">${esc(p.branch || '')} ${esc(p.reason || '')}</td>
  </tr>`).join('') || '<tr><td colspan="8" class="muted">план пуст</td></tr>';
  $('sb-cycle-detail').scrollIntoView({behavior: 'smooth'});
}

// ---------------------------------------------------------------- init
loadOverview();
setInterval(() => {
  if ($('p-overview').classList.contains('active')) loadOverview();
}, 15000);
