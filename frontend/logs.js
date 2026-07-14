// Anime GraphRAG — logs page

const $ = (s) => document.querySelector(s);

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function renderMarkdown(text) {
  if (!text) return '';
  if (typeof marked !== 'undefined') {
    return marked.parse(text, { breaks: true });
  }
  return escapeHtml(text);
}

function statusBadge(status) {
  const labels = {
    ok: 'ok',
    empty: 'empty',
    error: 'error',
    invalid: 'invalid',
    clarify: 'clarify',
    llm_error: 'llm_error',
  };
  const cls = `badge badge-${status || 'error'}`;
  return `<span class="${cls}">${labels[status] || status}</span>`;
}

function truncate(text, n) {
  if (!text) return '';
  return text.length > n ? text.slice(0, n) + '…' : text;
}

function expandableCell(content, escaped = true) {
  if (!content) return '<span class="muted">—</span>';
  const html = escaped ? escapeHtml(content) : content;
  return `<div class="expandable" onclick="this.classList.toggle('expanded')">
    <div class="truncated">${html}</div>
    <div class="expand-hint">нажми для раскрытия</div>
  </div>`;
}

function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'Z');
  return d.toLocaleString('ru-RU', { hour12: false });
}

function updateStats(logs) {
  const stats = {};
  let totalDuration = 0;
  let totalRows = 0;
  for (const log of logs) {
    stats[log.status] = (stats[log.status] || 0) + 1;
    totalDuration += log.duration_sec || 0;
    totalRows += log.rows_returned || 0;
  }
  const cards = [
    { label: 'Всего', num: logs.length },
    { label: 'OK', num: stats.ok || 0 },
    { label: 'Empty', num: stats.empty || 0 },
    { label: 'Error', num: stats.error || 0 },
    { label: 'Invalid', num: stats.invalid || 0 },
    { label: 'Clarify', num: stats.clarify || 0 },
    { label: 'LLM error', num: stats.llm_error || 0 },
    { label: ' avg duration (с)', num: logs.length ? (totalDuration / logs.length).toFixed(1) : '0' },
    { label: 'Total rows', num: totalRows },
  ];
  $('#logsStats').innerHTML = cards.map(c =>
    `<div class="stat-card">${c.label}: <span class="num">${c.num}</span></div>`
  ).join('');
}

async function loadHealth() {
  try {
    const resp = await fetch('/api/health');
    const data = await resp.json();
    const dotClass = data.ok ? 'dot-ok' : 'dot-err';
    $('#healthBar').innerHTML = `
      <div class="item"><span class="dot ${dotClass}"></span> Status: <span class="val">${data.ok ? 'OK' : 'DOWN'}</span></div>
      <div class="item">Model: <span class="val">${escapeHtml(data.model || '—')}</span></div>
      <div class="item">URL: <span class="val mono">${escapeHtml(data.llm_base_url || '—')}</span></div>
    `;
  } catch (e) {
    $('#healthBar').innerHTML = `<div class="item"><span class="dot dot-err"></span> Health check failed</div>`;
  }
}

let allLogs = [];

async function loadLogs() {
  const limit = $('#limitSelect').value;
  const filter = $('#statusFilter').value;
  const url = `/api/logs?limit=${limit}`;

  try {
    const resp = await fetch(url);
    const data = await resp.json();
    allLogs = data.logs || [];
    renderLogs(filter);
  } catch (e) {
    $('#logsBody').innerHTML = `<tr><td colspan="11" class="loading">Ошибка: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function renderLogs(filter) {
  const logs = filter ? allLogs.filter(l => l.status === filter) : allLogs;
  updateStats(logs);

  if (!logs.length) {
    $('#logsBody').innerHTML = '<tr><td colspan="11" class="loading">Нет записей</td></tr>';
    return;
  }

  $('#logsBody').innerHTML = logs.map(log => {
    return `<tr>
      <td class="muted mono">${formatTime(log.created_at)}</td>
      <td>${statusBadge(log.status)}</td>
      <td class="mono">${escapeHtml(log.model || '—')}</td>
      <td>${expandableCell(log.question)}</td>
      <td>${log.cypher ? expandableCell(log.cypher) : '<span class="muted">—</span>'}</td>
      <td>${log.answer ? `<div class="answer-content">${renderMarkdown(log.answer)}</div>` : '<span class="muted">—</span>'}</td>
      <td>${log.cypher_raw ? `<div class="raw-llm">${escapeHtml(log.cypher_raw)}</div>` : '<span class="muted">—</span>'}</td>
      <td class="mono">${log.attempts || 0}</td>
      <td class="mono">${log.rows_returned || 0}</td>
      <td class="mono">${log.duration_sec != null ? log.duration_sec.toFixed(1) : '—'}</td>
      <td class="mono muted">${escapeHtml(truncate(log.chat_id, 12))}</td>
    </tr>`;
  }).join('');
}

// Init
loadHealth();
loadLogs();

$('#refreshBtn').addEventListener('click', () => { loadHealth(); loadLogs(); });
$('#limitSelect').addEventListener('change', loadLogs);
$('#statusFilter').addEventListener('change', () => renderLogs($('#statusFilter').value));