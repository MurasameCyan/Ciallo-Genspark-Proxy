(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const els = {
    build: $('#build-id'), connection: $('#connection-pill'), service: $('#service-status'), servicePill: $('#service-pill'), uptime: $('#uptime-text'),
    accountCount: $('#account-count'), readyCount: $('#ready-count'), registrationState: $('#registration-state'), registrationDetail: $('#registration-detail'), registrationPill: $('#registration-pill'),
    accountsBadge: $('#accounts-badge'), accountsBody: $('#accounts-body'), registerForm: $('#registration-form'), registerStart: $('#register-start'), registerStop: $('#register-stop'),
    configForm: $('#config-form'), configPill: $('#config-pill'), configHint: $('#config-hint'), configSave: $('#config-save'), logList: $('#log-list'), logLevel: $('#log-level'), autoScroll: $('#log-autoscroll'),
    toastRegion: $('#toast-region'), themeToggle: $('#theme-toggle')
  };
  const fields = {
    registerSeq: $('#register-seq'), registerEmail: $('#register-email'), registerProxy: $('#register-proxy'),
    captcha: $('#captcha-value'), code: $('#code-value'), mailApiBase: $('#mail-api-base'), mailAdminAuth: $('#mail-admin-auth'), mailDomain: $('#mail-domain'),
    mailDomains: $('#mail-domains'), mailDomainMode: $('#mail-domain-mode'), mailAuthMode: $('#mail-auth-mode'), mailCreatePath: $('#mail-create-path'), mailPollInterval: $('#mail-poll-interval')
  };
  const state = { logs: [], accounts: [], configDirty: false, registration: null, source: null, refreshBusy: false, persistedForm: {} };
  const STORAGE_KEY = 'ciallo-genspark-dashboard';
  const FORM_KEYS = ['registerSeq', 'registerEmail', 'registerProxy', 'mailApiBase', 'mailDomain', 'mailDomains', 'mailDomainMode', 'mailAuthMode', 'mailCreatePath', 'mailPollInterval'];

  function setText(node, value, fallback = '—') { if (node) node.textContent = value === undefined || value === null || value === '' ? fallback : String(value); }
  function fmtUptime(seconds) {
    const n = Number(seconds);
    if (!Number.isFinite(n) || n < 0) return '运行时间 —';
    const day = Math.floor(n / 86400), hour = Math.floor(n % 86400 / 3600), min = Math.floor(n % 3600 / 60);
    return `运行时间 ${day ? `${day}天 ` : ''}${String(hour).padStart(2, '0')}:${String(min).padStart(2, '0')}`;
  }
  function escapeDate(ts) {
    const date = ts ? new Date(typeof ts === 'number' && ts < 2e10 ? ts * 1000 : ts) : new Date();
    return Number.isNaN(date.getTime()) ? '刚刚' : date.toLocaleTimeString('zh-CN', { hour12: false });
  }
  function showToast(message, kind = 'error') {
    const toast = document.createElement('div'); toast.className = `toast ${kind}`; toast.textContent = String(message || '请求失败'); els.toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), 4200);
  }
  function setPill(node, label, kind = 'neutral', dot = false) {
    if (!node) return; node.className = `status-pill status-${kind}`; node.textContent = '';
    if (dot) { const dotEl = document.createElement('i'); dotEl.setAttribute('aria-hidden', 'true'); node.append(dotEl); }
    node.append(document.createTextNode(label));
  }
  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) { button.dataset.label = button.textContent; button.disabled = true; button.textContent = label || '处理中…'; }
    else { button.disabled = false; button.textContent = button.dataset.label || label || '完成'; }
  }

  async function api(path, opts = {}) {
    const options = { ...opts, headers: { Accept: 'application/json', ...(opts.body ? { 'Content-Type': 'application/json' } : {}), ...(opts.headers || {}) } };
    let response;
    try { response = await fetch(path, options); } catch (error) { throw new Error(`网络连接失败：${error.message || '无法访问服务'}`); }
    const type = response.headers.get('content-type') || '';
    let payload = null;
    try { payload = type.includes('json') ? await response.json() : await response.text(); } catch (_) { payload = null; }
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' ? payload.detail || payload.message || payload.error : payload;
      throw new Error(detail ? String(detail) : `请求失败（${response.status}）`);
    }
    return payload;
  }

  function setConnected(connected) { setPill(els.connection, connected ? '已连接' : '连接断开', connected ? 'success' : 'error', true); }
  function normalizeArray(value) { return Array.isArray(value) ? value : []; }
  function accountStats(stats) {
    if (!stats || typeof stats !== 'object') return '— / —';
    const requests = stats.requests ?? stats.request_count ?? stats.total ?? stats.calls ?? '—';
    const success = stats.success ?? stats.success_count ?? stats.ok ?? '—';
    return `${requests} / ${success}`;
  }
  function renderAccounts(accounts) {
    state.accounts = normalizeArray(accounts); setText(els.accountsBadge, `${state.accounts.length} 个账号`, '0 个账号');
    els.accountsBody.textContent = '';
    if (!state.accounts.length) {
      const row = document.createElement('tr'); row.className = 'empty-row'; const cell = document.createElement('td'); cell.colSpan = 6;
      const icon = document.createElement('span'); icon.className = 'empty-icon'; icon.textContent = '◌'; const title = document.createElement('strong'); title.textContent = '还没有账号'; const hint = document.createElement('span'); hint.textContent = '完成一次注册后，账号会显示在这里。'; cell.append(icon, title, hint); row.append(cell); els.accountsBody.append(row); return;
    }
    state.accounts.forEach((account) => {
      const row = document.createElement('tr');
      const values = [account.seq ?? '—', account.email || '—', account.status || '未知'];
      values.forEach((value, index) => { const cell = document.createElement('td'); if (index === 1) cell.className = 'email-cell'; const strong = document.createElement(index === 1 ? 'strong' : 'span'); strong.textContent = String(value); cell.append(strong); row.append(cell); });
      const ready = Boolean(account.ready); const readyCell = document.createElement('td'); const readySpan = document.createElement('span'); readySpan.className = `cell-status ${ready ? 'ready' : 'no'}`; readySpan.textContent = ready ? '✓ 就绪' : '— 未就绪'; readyCell.append(readySpan); row.append(readyCell);
      const cookieCell = document.createElement('td'); const cookieSpan = document.createElement('span'); const cookie = account.cookie_file || account.cookie || ''; cookieSpan.className = `cell-status ${cookie ? 'ready' : 'no'}`; cookieSpan.textContent = cookie ? '✓ 已配置' : '— 缺失'; cookieCell.append(cookieSpan); row.append(cookieCell);
      const statsCell = document.createElement('td'); const statsSpan = document.createElement('span'); statsSpan.className = 'counter'; statsSpan.textContent = accountStats(account.stats); statsCell.append(statsSpan); row.append(statsCell); els.accountsBody.append(row);
    });
  }
  function renderRegistration(registration) {
    if (!registration || typeof registration !== 'object') return;
    state.registration = registration;
    const stateLabel = registration.state || '空闲'; const stateText = String(stateLabel).toLowerCase(); const running = registration.running === undefined ? Boolean(registration.job_id) : Boolean(registration.running); const email = registration.email || '';
    const kind = !running ? 'neutral' : /error|fail|stop/.test(stateText) ? 'error' : /done|success|complete/.test(stateText) ? 'success' : 'warning';
    setText(els.registrationState, stateLabel); setText(els.registrationDetail, email || (registration.job_id ? `任务 ${registration.job_id}` : '等待开始任务')); setPill(els.registrationPill, running ? stateLabel : '空闲', kind, true);
    els.registerStop.disabled = !running;
  }
  function renderStatus(data) {
    if (!data || typeof data !== 'object') return;
    if (data.build !== undefined) setText(els.build, data.build, 'dev');
    if (data.account_count !== undefined) setText(els.accountCount, data.account_count, '0');
    if (data.ready_count !== undefined) setText(els.readyCount, data.ready_count, '0');
    if (data.uptime_s !== undefined) setText(els.uptime, fmtUptime(data.uptime_s));
    const serviceReady = data.service === false || data.status === 'error' ? false : true; setText(els.service, serviceReady ? '运行中' : '异常'); setPill(els.servicePill, serviceReady ? '正常' : '异常', serviceReady ? 'success' : 'error', true);
    if (data.registration && typeof data.registration === 'object') renderRegistration(data.registration);
    else if (!state.registration) renderRegistration({ running: false });
  }

  function configPayload() {
    const numeric = Number(fields.mailPollInterval.value);
    return { mail_api_base: fields.mailApiBase.value.trim(), mail_admin_auth: fields.mailAdminAuth.value, mail_domain: fields.mailDomain.value.trim(), mail_domains: fields.mailDomains.value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean), mail_domain_mode: fields.mailDomainMode.value, mail_auth_mode: fields.mailAuthMode.value, mail_create_path: fields.mailCreatePath.value.trim(), mail_poll_interval: Number.isFinite(numeric) && numeric > 0 ? numeric : 3 };
  }
  function putConfig(config) {
    if (!config || typeof config !== 'object') return;
    const domains = Array.isArray(config.mail_domains) ? config.mail_domains : (typeof config.mail_domains === 'string' ? config.mail_domains.split(/[,\n]/).map((item) => item.trim()).filter(Boolean) : []);
    const hasServerConfig = Boolean(config.mail_api_base || config.mail_domain || domains.length || config.mail_create_path);
    const map = [['mailApiBase', 'mail_api_base'], ['mailDomain', 'mail_domain'], ['mailDomainMode', 'mail_domain_mode'], ['mailAuthMode', 'mail_auth_mode'], ['mailCreatePath', 'mail_create_path'], ['mailPollInterval', 'mail_poll_interval']];
    map.forEach(([field, key]) => { const keepLocal = !hasServerConfig && state.persistedForm[field] !== undefined; if (config[key] !== undefined && fields[field] && !state.configDirty && !keepLocal) fields[field].value = config[key]; });
    if (domains.length && !state.configDirty) fields.mailDomains.value = domains.join('\n');
    if (config.mail_admin_auth && !state.configDirty) fields.mailAdminAuth.value = config.mail_admin_auth;
    setPill(els.configPill, hasServerConfig ? '已配置' : '待配置', hasServerConfig ? 'success' : 'warning');
  }
  async function loadConfig(force = false) {
    const config = await api('/api/config');
    if (force) state.configDirty = false;
    putConfig(config); return config;
  }
  async function refreshAll() {
    if (state.refreshBusy) return; state.refreshBusy = true;
    try { const [status, accounts] = await Promise.all([api('/api/status'), api('/api/accounts')]); renderStatus(status); renderAccounts(accounts && accounts.accounts); setConnected(true); if (!state.configDirty) await loadConfig(); }
    catch (error) { setConnected(false); showToast(error.message); }
    finally { state.refreshBusy = false; }
  }

  function readStoredForm() {
    try { const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); state.persistedForm = stored; FORM_KEYS.forEach((key) => { if (stored[key] !== undefined && fields[key]) fields[key].value = stored[key]; }); if (stored.theme) applyTheme(stored.theme); } catch (_) { /* storage may be disabled */ }
  }
  function persistForm() {
    try { const stored = {}; FORM_KEYS.forEach((key) => { if (fields[key]) stored[key] = fields[key].value; }); stored.theme = document.documentElement.dataset.theme || 'dark'; state.persistedForm = stored; localStorage.setItem(STORAGE_KEY, JSON.stringify(stored)); } catch (_) { /* storage is optional */ }
  }
  function applyTheme(theme) { document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark'; els.themeToggle.textContent = theme === 'light' ? '☾' : '☼'; els.themeToggle.setAttribute('aria-label', theme === 'light' ? '切换到深色主题' : '切换到浅色主题'); persistForm(); }

  async function startRegistration(event) {
    event.preventDefault(); setBusy(els.registerStart, true, '启动中…');
    const seq = fields.registerSeq.value.trim(); const payload = { ...(seq ? { seq: Number(seq) } : {}), ...(fields.registerEmail.value.trim() ? { email: fields.registerEmail.value.trim() } : {}), ...(fields.registerProxy.value.trim() ? { proxy: fields.registerProxy.value.trim() } : {}) };
    try { const result = await api('/api/register/start', { method: 'POST', body: JSON.stringify(payload) }); renderRegistration(result); showToast(`注册任务已启动${result.job_id ? `：${result.job_id}` : ''}`, 'success'); }
    catch (error) { showToast(error.message); }
    finally { setBusy(els.registerStart, false, '开始注册'); }
  }
  async function stopRegistration() {
    setBusy(els.registerStop, true, '停止中…'); try { await api('/api/register/stop', { method: 'POST', body: JSON.stringify({}) }); showToast('已请求停止注册任务', 'success'); await refreshAll(); } catch (error) { showToast(error.message); } finally { setBusy(els.registerStop, false, '停止任务'); }
  }
  async function sendCommand(button) {
    const command = button.dataset.command; const valueId = button.dataset.valueId; const value = valueId && fields[valueId] ? fields[valueId].value.trim() : '';
    if (valueId && !value) { showToast('请先输入内容'); fields[valueId].focus(); return; }
    setBusy(button, true, '发送中…'); try { const result = await api('/api/register/command', { method: 'POST', body: JSON.stringify({ command, ...(value ? { value } : {}) }) }); if (result && result.ok === false) throw new Error(result.message || '命令未接受'); showToast(`已发送：${command}`, 'success'); if (valueId) fields[valueId].value = ''; } catch (error) { showToast(error.message); } finally { setBusy(button, false); }
  }
  async function saveConfig(event) {
    event.preventDefault(); setBusy(els.configSave, true, '保存中…'); try { await api('/api/config', { method: 'POST', body: JSON.stringify(configPayload()) }); state.configDirty = false; setPill(els.configPill, '已保存', 'success'); setText(els.configHint, '配置已保存到服务端'); showToast('邮箱配置已保存', 'success'); persistForm(); } catch (error) { showToast(error.message); setText(els.configHint, '保存失败，请检查配置'); } finally { setBusy(els.configSave, false, '保存配置'); }
  }

  function addLog(item) {
    const entry = Array.isArray(item) ? null : item;
    if (!entry || typeof entry !== 'object') return;
    state.logs.push({ ts: entry.ts || entry.time || Date.now(), level: String(entry.level || 'info').toLowerCase(), msg: entry.msg ?? entry.message ?? '' }); if (state.logs.length > 500) state.logs.splice(0, state.logs.length - 500); renderLogs();
  }
  function renderLogs() {
    const filter = els.logLevel.value; const visible = state.logs.filter((entry) => filter === 'all' || entry.level === filter); const shouldScroll = els.autoScroll.checked; els.logList.textContent = '';
    if (!visible.length) { const empty = document.createElement('div'); empty.className = 'log-empty'; empty.textContent = state.logs.length ? '当前筛选没有日志。' : '等待事件流…'; els.logList.append(empty); return; }
    visible.forEach((entry) => { const line = document.createElement('div'); line.className = 'log-line'; const time = document.createElement('span'); time.className = 'log-time'; time.textContent = escapeDate(entry.ts); const level = document.createElement('span'); level.className = `log-level ${['info', 'success', 'warning', 'error'].includes(entry.level) ? entry.level : 'info'}`; level.textContent = entry.level.toUpperCase(); const msg = document.createElement('span'); msg.className = 'log-message'; msg.textContent = String(entry.msg); line.append(time, level, msg); els.logList.append(line); });
    if (shouldScroll) els.logList.scrollTop = els.logList.scrollHeight;
  }
  function handleLogEvent(event) {
    try { const payload = JSON.parse(event.data); if (Array.isArray(payload)) { state.logs = []; payload.forEach(addLog); } else addLog(payload); } catch (_) { /* ignore malformed event */ }
  }
  function connectLogs() {
    if (!window.EventSource) { showToast('当前浏览器不支持实时日志'); return; }
    if (state.source) state.source.close();
    const source = new EventSource('/api/logs'); state.source = source;
    source.onopen = () => setConnected(true); source.onmessage = handleLogEvent; source.addEventListener('snapshot', handleLogEvent); source.addEventListener('log', handleLogEvent);
    source.onerror = () => { setConnected(false); source.close(); window.setTimeout(connectLogs, 5000); };
  }

  els.refreshButton = $('#refresh-button');
  els.registerForm.addEventListener('submit', startRegistration); els.registerStop.addEventListener('click', stopRegistration); els.configForm.addEventListener('submit', saveConfig);
  document.querySelectorAll('.command-button, .command-action').forEach((button) => button.addEventListener('click', () => sendCommand(button)));
  document.querySelectorAll('#registration-form input, #config-form input, #config-form textarea, #config-form select').forEach((input) => input.addEventListener('input', () => { if (input.form === els.configForm) state.configDirty = true; persistForm(); }));
  els.logLevel.addEventListener('change', renderLogs); $('#clear-logs').addEventListener('click', () => { state.logs = []; renderLogs(); }); els.refreshButton.addEventListener('click', refreshAll);
  els.themeToggle.addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'));

  readStoredForm(); applyTheme(document.documentElement.dataset.theme || 'dark'); refreshAll(); connectLogs(); window.setInterval(refreshAll, 15000);
})();
