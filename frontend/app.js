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
        <button class="example-btn" data-q="Кто режиссёр Атаки Титанов и над какими проектами он ещё работал?">Кто режиссёр Атаки Титанов?</button>
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
  // Remove welcome if present
  const welcome = container.querySelector('.welcome');
  if (welcome) welcome.remove();

  const el = document.createElement('div');
  el.className = 'msg msg-user';
  el.textContent = text;
  container.appendChild(el);
  scrollToBottom();
}

function addTyping() {
  const container = $('#messages');
  const el = document.createElement('div');
  el.className = 'msg msg-bot typing';
  el.id = 'typing-indicator';
  el.textContent = 'Думаю';
  container.appendChild(el);
  scrollToBottom();
}

function removeTyping() {
  const el = $('#typing-indicator');
  if (el) el.remove();
}

function addBotMessage(text, meta) {
  const container = $('#messages');
  const el = document.createElement('div');
  el.className = 'msg msg-bot';
  
  let metaHtml = '';
  if (meta && meta.cypher) {
    const statusText = meta.status === 'ok' ? `${meta.rows} строк` :
                       meta.status === 'empty' ? 'пусто' :
                       meta.status === 'error' ? `ошибка (${meta.attempts} попыток)` :
                       meta.status === 'invalid' ? 'неподходящий вопрос' : meta.status;
    metaHtml = `
      <div class="msg-meta" onclick="this.querySelector('.cypher').style.display = this.querySelector('.cypher').style.display === 'none' ? 'block' : 'none'">
        Cypher: ${statusText} (попыток: ${meta.attempts})
        <div class="cypher" style="display:none">${escapeHtml(meta.cypher)}</div>
      </div>
    `;
  }
  
  el.innerHTML = `<div class="role-label">Assistant</div>${renderMarkdown(text)}${metaHtml}`;
  container.appendChild(el);
  scrollToBottom();
}

function scrollToBottom() {
  const container = $('#messages');
  container.scrollTop = container.scrollHeight;
}

// --- Send ---

async function send(text) {
  if (!text.trim() || isLoading) return;
  if (!currentChat) await createChat();

  isLoading = true;
  $('#sendBtn').disabled = true;

  addUserMessage(text);
  addTyping();

  try {
    const result = await api('POST', `/api/chats/${currentChat}/ask`, { message: text });
    removeTyping();
    addBotMessage(result.answer, {
      cypher: result.cypher,
      status: result.status,
      rows: result.rows,
      attempts: result.attempts,
    });

    // Update chat title from first message
    const chats = $$('.chat-item');
    if (chats.length === 1) {
      const title = text.slice(0, 40) + (text.length > 40 ? '...' : '');
      $('#chatTitle').textContent = title;
      await api('PUT', `/api/chats/${currentChat}`, { title });
      await loadChats();
    }
  } catch (e) {
    removeTyping();
    addBotMessage(`Ошибка: ${e.message}`, null);
  }

  isLoading = false;
  updateSendBtn();
  $('#input').focus();
}

// --- Input ---

function updateSendBtn() {
  $('#sendBtn').disabled = isLoading || !$('#input').value.trim();
}

$('#input').addEventListener('input', () => {
  updateSendBtn();
  // Auto-resize
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

// --- Utils ---

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// --- Init ---

loadChats();
bindExamples();