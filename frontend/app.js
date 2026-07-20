// Anime GraphRAG — фронтенд логика

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let currentChat = null;
let isLoading = false;

// --- API ---

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

// --- Chats ---

async function loadChats() {
  const data = await api('GET', '/api/chats');
  renderChatList(data.chats);
}

function renderChatList(chats) {
  const list = $('#chatList');
  list.innerHTML = '';
  for (const chat of chats) {
    const el = document.createElement('div');
    el.className = 'chat-item' + (chat.id === currentChat ? ' active' : '');
    el.innerHTML = `
      <span class="title">${escapeHtml(chat.title)}</span>
      <button class="delete" title="Удалить">×</button>
    `;
    el.querySelector('.title').onclick = () => selectChat(chat.id, chat.title);
    el.querySelector('.delete').onclick = (e) => { e.stopPropagation(); deleteChat(chat.id); };
    list.appendChild(el);
  }
}

async function createChat() {
  const title = 'Новый чат';
  const data = await api('POST', '/api/chats', { title });
  currentChat = data.id;
  $('#chatTitle').textContent = title;
  clearMessages();
  await loadChats();
  $('#input').focus();
}

async function deleteChat(chatId) {
  await api('DELETE', `/api/chats/${chatId}`);
  if (currentChat === chatId) {
    currentChat = null;
    clearMessages();
    showWelcome();
  }
  await loadChats();
}

async function selectChat(chatId, title) {
  currentChat = chatId;
  $('#chatTitle').textContent = title;
  const data = await api('GET', `/api/chats/${chatId}/messages`);
  renderMessages(data.messages);
  await loadChats();
}

// --- Messages ---

function clearMessages() {
  $('#messages').innerHTML = '';
}

function showWelcome() {
  $('#messages').innerHTML = `
    <div class="welcome">
      <h1>Anime GraphRAG</h1>
      <p>Спросите меня о любом аниме, режиссёре, студии, сэйю или персонаже.</p>
      <div class="examples">
        <button class="example-btn" data-q="Кто режиссёр Fullmetal Alchemist: Brotherhood и что он ещё снимал?">Кто режиссёр FMA:B?</button>
        <button class="example-btn" data-q="Топ-10 аниме студии Kyoto Animation по оценкам">Топ аниме KyoAni</button>
        <button class="example-btn" data-q="Какие жанры у One Piece?">Жанры One Piece</button>
        <button class="example-btn" data-q="Сэйю с наибольшим числом ролей">Топ сэйю по ролям</button>
      </div>
    </div>
  `;
  bindExamples();
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') {
    return marked.parse(text, { breaks: true });
  }
  return escapeHtml(text);
}

function renderMessages(messages) {
  const container = $('#messages');
  container.innerHTML = '';
  for (const msg of messages) {
    if (msg.role === 'user') {
      container.innerHTML += `<div class="msg msg-user">${escapeHtml(msg.content)}</div>`;
    } else {
      container.innerHTML += `<div class="msg msg-bot"><div class="role-label">Assistant</div>${renderMarkdown(msg.content)}</div>`;
    }
  }
  scrollToBottom();
}

function addUserMessage(text) {
  const container = $('#messages');
  const welcome = container.querySelector('.welcome');
  if (welcome) welcome.remove();
  const el = document.createElement('div');
  el.className = 'msg msg-user';
  el.textContent = text;
  container.appendChild(el);
  scrollToBottom();
}

// --- Streaming send ---

async function send(text) {
  if (!text.trim() || isLoading) return;
  if (!currentChat) await createChat();

  isLoading = true;
  $('#sendBtn').disabled = true;

  addUserMessage(text);

  // Create streaming message container
  const botEl = document.createElement('div');
  botEl.className = 'msg msg-bot';
  botEl.innerHTML = '<div class="role-label">Assistant</div>';
  const cypherEl = document.createElement('div');
  cypherEl.className = 'cypher-streaming stream-cursor';
  botEl.appendChild(cypherEl);
  const answerEl = document.createElement('div');
  answerEl.className = 'answer-stream';
  botEl.appendChild(answerEl);
  $('#messages').appendChild(botEl);
  scrollToBottom();

  let cypherText = '';
  let answerText = '';
  let finalResult = null;

  try {
    const resp = await fetch(`/api/chats/${currentChat}/ask/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = JSON.parse(line.slice(6));

        if (data.type === 'cypher') {
          cypherText += data.text;
          cypherEl.textContent = cypherText;
          scrollToBottom();
        } else if (data.type === 'cypher_done') {
          cypherEl.classList.remove('stream-cursor');
          cypherText = data.cypher || cypherText;
        } else if (data.type === 'cypher_error') {
          cypherEl.classList.remove('stream-cursor');
          cypherEl.textContent = cypherText + `\n\n[Ошибка: ${data.error}]`;
        } else if (data.type === 'status') {
          cypherEl.style.display = 'none';
        } else if (data.type === 'answer') {
          answerText += data.text;
          answerEl.innerHTML = renderMarkdown(answerText) + '<span class="stream-cursor"></span>';
          scrollToBottom();
        } else if (data.type === 'result') {
          finalResult = data;
          answerText = data.answer || answerText;
          answerEl.innerHTML = renderMarkdown(answerText);
          // Add Cypher meta
          if (data.cypher) {
            const meta = document.createElement('div');
            meta.className = 'msg-meta';
            meta.onclick = function() {
              const c = this.querySelector('.cypher');
              c.style.display = c.style.display === 'none' ? 'block' : 'none';
            };
            const statusText = data.status === 'ok' ? `${data.rows} строк` :
                               data.status === 'empty' ? 'пусто' :
                               data.status === 'error' ? `ошибка (${data.attempts} попыток)` :
                               data.status === 'invalid' ? 'неподходящий вопрос' :
                               data.status === 'clarify' ? 'уточняющий вопрос' : data.status;
            meta.innerHTML = `Cypher: ${statusText} (попыток: ${data.attempts})<div class="cypher" style="display:none">${escapeHtml(data.cypher)}</div>`;
            botEl.appendChild(meta);
          }
          scrollToBottom();
        }
      }
    }
  } catch (e) {
    answerEl.innerHTML = `Ошибка: ${e.message}`;
  }

  // Auto-generate chat title from first message via LLM
  if (finalResult) {
    const chats = $$('.chat-item');
    if (chats.length === 1 && $('#chatTitle').textContent === 'Новый чат') {
      try {
        const titleData = await api('POST', `/api/chats/${currentChat}/title`, { message: text });
        if (titleData.title) {
          $('#chatTitle').textContent = titleData.title;
          await loadChats();
        }
      } catch (e) { /* fallback keeps original title */ }
    }
  }

  isLoading = false;
  updateSendBtn();
  $('#input').focus();
}

function scrollToBottom() {
  const container = $('#messages');
  container.scrollTop = container.scrollHeight;
}

// --- Input ---

function updateSendBtn() {
  $('#sendBtn').disabled = isLoading || !$('#input').value.trim();
}

$('#input').addEventListener('input', () => {
  updateSendBtn();
  const el = $('#input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
});

$('#input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send($('#input').value);
    $('#input').value = '';
    $('#input').style.height = 'auto';
    updateSendBtn();
  }
});

$('#sendBtn').addEventListener('click', () => {
  send($('#input').value);
  $('#input').value = '';
  $('#input').style.height = 'auto';
  updateSendBtn();
});

// --- Examples ---

function bindExamples() {
  $$('.example-btn').forEach(btn => {
    btn.onclick = () => send(btn.dataset.q);
  });
}

// --- Sidebar ---

$('#newChatBtn').addEventListener('click', createChat);

$('#toggleSidebar').addEventListener('click', () => {
  $('#sidebar').classList.toggle('hidden');
});

// --- Settings panel ---

$('#gearBtn').addEventListener('click', openSettings);
$('#backBtn').addEventListener('click', closeSettings);

// --- Think toggle (inline button in chat header) ---

let baseMaxTokens = null;  // запоминаем исходное значение

$('#thinkBtn').addEventListener('click', async () => {
  const btn = $('#thinkBtn');
  const isOn = btn.classList.toggle('active');
  const data = await api('GET', '/api/settings');
  if (baseMaxTokens === null) baseMaxTokens = data.max_tokens || 8192;
  const newMaxTokens = isOn ? baseMaxTokens * 2 : baseMaxTokens;
  await api('PUT', '/api/settings', { think: isOn, max_tokens: newMaxTokens });
});

async function syncThinkButton() {
  try {
    const data = await api('GET', '/api/settings');
    if (data.think) {
      $('#thinkBtn').classList.add('active');
      baseMaxTokens = (data.max_tokens || 8192) / 2;
    } else {
      $('#thinkBtn').classList.remove('active');
      baseMaxTokens = data.max_tokens || 8192;
    }
  } catch (e) { /* ignore */ }
}

async function openSettings() {
  $('#chatView').style.display = 'none';
  $('#settingsView').style.display = 'flex';
  try {
    const data = await api('GET', '/api/settings');
    $('#setModel').value = data.model || '';
    $('#setBaseUrl').value = data.base_url || '';
    $('#setApiKey').value = '';  // never show key
    $('#setApiKey').placeholder = data.api_key || 'оставьте пустым для .env';
    $('#setMaxTokens').value = data.max_tokens || '';
    $('#setCypherPrompt').value = data.cypher_prompt || '';
    $('#setAnswerPrompt').value = data.answer_prompt || '';
  } catch (e) {
    console.error('Failed to load settings:', e);
  }
}

function closeSettings() {
  $('#settingsView').style.display = 'none';
  $('#chatView').style.display = 'block';
}

$('#saveSettingsBtn').addEventListener('click', async () => {
  const body = {};
  const model = $('#setModel').value.trim();
  const baseUrl = $('#setBaseUrl').value.trim();
  const apiKey = $('#setApiKey').value.trim();
  const maxTokens = parseInt($('#setMaxTokens').value) || null;
  const cypherPrompt = $('#setCypherPrompt').value.trim();
  const answerPrompt = $('#setAnswerPrompt').value.trim();

  if (model) body.model = model;
  if (baseUrl) body.base_url = baseUrl;
  if (apiKey) body.api_key = apiKey;
  if (maxTokens) body.max_tokens = maxTokens;
  if (cypherPrompt) body.cypher_prompt = cypherPrompt;
  if (answerPrompt) body.answer_prompt = answerPrompt;

  try {
    await api('PUT', '/api/settings', body);
    closeSettings();
  } catch (e) {
    alert('Ошибка сохранения: ' + e.message);
  }
});

// --- Utils ---

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// --- Init ---

loadChats();
bindExamples();
syncThinkButton();