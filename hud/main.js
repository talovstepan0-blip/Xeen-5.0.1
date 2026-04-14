/**
 * СИЕН HUD 3.0 — main.js
 * Изменения против 2.0 Beta:
 *  • session_id хранится в localStorage и шлётся в каждом WS-сообщении
 *  • Кнопка «Сбросить контекст» создаёт новый session_id
 *  • Индикатор текущей эмоции в status-bar
 *  • Длинные ответы рендерятся в .log-response с прокруткой
 *  • Без локальных команд (вынесены в local_commands.js — если есть)
 *  • Безопасный escape всех пользовательских строк
 */
'use strict';

// ══════════════════════════════════════════════════════════════════
// КОНФИГУРАЦИЯ
// ══════════════════════════════════════════════════════════════════
const CONFIG = {
  ws_url:           (location.protocol === 'https:' ? 'wss' : 'ws') + '://' + (location.host || 'localhost:8000') + '/ws',
  api_base:         (location.protocol === 'https:' ? 'https' : 'http') + '://' + (location.host || 'localhost:8000'),
  reconnect_ms:     3000,
  refresh_tasks_ms: 30000,
};

// При открытии файла напрямую (file://), а не через сервер
if (location.protocol === 'file:') {
  CONFIG.ws_url = 'ws://localhost:8000/ws';
  CONFIG.api_base = 'http://localhost:8000';
}

// ══════════════════════════════════════════════════════════════════
// SESSION (диалоговая память)
// ══════════════════════════════════════════════════════════════════

function genSessionId() {
  return 'sess_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
}

function getSessionId() {
  let sid = localStorage.getItem('sien_session_id');
  if (!sid) {
    sid = genSessionId();
    localStorage.setItem('sien_session_id', sid);
  }
  return sid;
}

function resetSessionId() {
  const newSid = genSessionId();
  localStorage.setItem('sien_session_id', newSid);
  return newSid;
}

let SESSION_ID = getSessionId();

// ══════════════════════════════════════════════════════════════════
// УТИЛИТЫ
// ══════════════════════════════════════════════════════════════════

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function escapeHTML(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeMarkdown(str) {
  // Простой markdown → HTML, безопасно
  let s = escapeHTML(str);
  // Кодовые блоки
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code}</code></pre>`);
  // Inline код
  s = s.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  // **жирный**
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // *курсив*
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  // Переносы строк
  s = s.replace(/\n/g, '<br>');
  return s;
}

function nowTime() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}

function emotionEmoji(em) {
  return ({ joy: '😊', sadness: '😔', anger: '😠', fear: '😨', neutral: '😐' })[em] || '😐';
}

// ══════════════════════════════════════════════════════════════════
// ЛОГ
// ══════════════════════════════════════════════════════════════════

function logLine(msg, opts = {}) {
  const out = $('#log-output');
  if (!out) return;

  const cls = opts.cls || 'log-system';
  const tag = opts.tag || '[СИЕН]';
  const isResponse = opts.isResponse || false;
  const emotion = opts.emotion || '';

  const line = document.createElement('div');
  line.className = `log-line ${cls}` + (isResponse ? ' log-response' : '');

  const emojiSpan = emotion ? `<span class="log-emotion">${emotionEmoji(emotion)}</span>` : '';
  const content = isResponse ? escapeMarkdown(msg) : escapeHTML(msg);

  line.innerHTML = `
    <span class="log-time">${nowTime()}</span>
    <span class="log-tag">${escapeHTML(tag)}</span>
    <span class="log-msg">${emojiSpan}${content}</span>
    <button class="log-copy-btn" title="Копировать">📋</button>
  `;

  // Кнопка копирования
  line.querySelector('.log-copy-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(msg).then(() => {
      const btn = e.target;
      btn.classList.add('copied');
      btn.textContent = '✓';
      setTimeout(() => {
        btn.classList.remove('copied');
        btn.textContent = '📋';
      }, 1500);
    });
  });

  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}

function clearLog() {
  $('#log-output').innerHTML = '';
  logLine('Лог очищен', { cls: 'log-system' });
}

// ══════════════════════════════════════════════════════════════════
// АГЕНТЫ
// ══════════════════════════════════════════════════════════════════

const AGENT_INFO = {
  ahill:  { name: 'АХИЛЛ',  role: 'Защитник VPS' },
  fenix:  { name: 'ФЕНИКС', role: 'Парсер намерений' },
  logos:  { name: 'ЛОГОС',  role: 'Оформление' },
  wen:    { name: 'ВЭНЬ',   role: 'Секретарь' },
  cronos: { name: 'КРОНОС', role: 'Хранитель секретов' },
  argus:  { name: 'АРГУС',  role: 'Монитор / поиск' },
  apollo: { name: 'АПОЛЛОН',role: 'Видеомейкер' },
  hermes: { name: 'ГЕРМЕС', role: 'Affiliate' },
  plutos: { name: 'ПЛУТОС', role: 'Инвестор' },
  kun:    { name: 'КУН',    role: 'Профессор' },
  master: { name: 'МАСТЕР', role: 'Тренер' },
  musa:   { name: 'МУСА',   role: 'Контент' },
  kallio: { name: 'КАЛЛИО', role: 'Медиа' },
  hefest: { name: 'ГЕФЕСТ', role: 'Код' },
  avto:   { name: 'АВТО',   role: 'Макросы' },
  huei:   { name: 'ХУЭЙ',   role: 'Изображения' },
  meng:   { name: 'МЭН',    role: 'Длинные видео' },
  eho:    { name: 'ЭХО',    role: 'Озвучка' },
  irida:  { name: 'ИРИДА',  role: 'Telegram' },
  mnemon: { name: 'МНЕМОН', role: 'Переводчик' },
  dike:   { name: 'ДИКЕ',   role: 'Бухгалтер' },
};

const agentStatuses = {};

function updateAgent(name, status, task) {
  agentStatuses[name] = { status, task };
  renderAgents();
}

function renderAgents() {
  const grid = $('#agents-grid');
  if (!grid) return;
  grid.innerHTML = '';

  let online = 0;
  Object.keys(AGENT_INFO).forEach((key) => {
    const info = AGENT_INFO[key];
    const st = agentStatuses[key] || { status: 'offline', task: '—' };
    if (st.status === 'ok') online++;

    const card = document.createElement('div');
    card.className = `agent-card status-${escapeHTML(st.status)}`;
    card.innerHTML = `
      <div class="agent-name">${escapeHTML(info.name)}</div>
      <div class="agent-role">${escapeHTML(info.role)}</div>
      <div class="agent-task">${escapeHTML(st.task || '—')}</div>
      <div class="agent-status-badge">${escapeHTML(st.status.toUpperCase())}</div>
    `;
    grid.appendChild(card);
  });

  $('#agents-count').textContent = `${online} / ${Object.keys(AGENT_INFO).length}`;
}


// ══════════════════════════════════════════════════════════════════
// WEBSOCKET
// ══════════════════════════════════════════════════════════════════

let ws = null;
let wsReconnectTimer = null;

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  try {
    ws = new WebSocket(CONFIG.ws_url);
  } catch (e) {
    logLine(`Ошибка WebSocket: ${e.message}`, { cls: 'log-error' });
    scheduleReconnect();
    return;
  }

  ws.addEventListener('open', () => {
    setWsStatus(true);
    logLine('WebSocket подключён', { cls: 'log-ok', tag: '[WS]' });
    ws.send(JSON.stringify({ type: 'get_agents_status', session_id: SESSION_ID }));
  });

  ws.addEventListener('message', (e) => {
    let msg;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;
    }
    handleWSMessage(msg);
  });

  ws.addEventListener('close', () => {
    setWsStatus(false);
    scheduleReconnect();
  });

  ws.addEventListener('error', () => {
    setWsStatus(false);
  });
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    connectWS();
  }, CONFIG.reconnect_ms);
}

function setWsStatus(connected) {
  const dot = $('.ws-dot');
  const label = $('#ws-label');
  if (connected) {
    dot.classList.add('connected');
    label.textContent = 'CONNECTED';
  } else {
    dot.classList.remove('connected');
    label.textContent = 'DISCONNECTED';
  }
}

function handleWSMessage(msg) {
  switch (msg.type) {
    case 'agent_status':
      updateAgent(msg.agent, msg.status, msg.task);
      break;

    case 'command_result': {
      // Если Логос ответил "уточнение" — показываем предложения
      if (maybeShowSuggestions(msg) && window._lastQuery) {
        fetchSuggestions(window._lastQuery).then(sugs => {
          if (sugs.length) renderSuggestions(window._lastQuery, sugs);
          else logLine(msg.formatted || JSON.stringify(msg.result), {cls:'log-warn', tag:'[СИЕН]', isResponse:true});
        });
        break;
      }
      const tag = `[${(msg.agent || 'system').toUpperCase()}]`;
      logLine(msg.formatted || JSON.stringify(msg.result), {
        cls: 'log-ok',
        tag,
        isResponse: true,
        emotion: msg.emotion,
      });
      $('#active-agent').textContent = `🤖 Агент: ${msg.agent}`;
      if (msg.emotion) {
        $('#emotion-status').textContent = `${emotionEmoji(msg.emotion)} Эмоция: ${msg.emotion}`;
      }
      // Если задача — обновим список
      if (msg.action === 'create_task' || msg.action === 'delete_task') {
  }
      break;
    }

    case 'reminder':
      showNotification(msg.title || 'Напоминание', msg.message || '');
      logLine(`⏰ ${msg.message}`, { cls: 'log-warn', tag: '[ВЭНЬ]' });
      break;

    case 'error':
      logLine(msg.message || 'Неизвестная ошибка', { cls: 'log-error', tag: `[${msg.agent || 'system'}]` });
      break;

    case 'context_reset':
      logLine('Контекст диалога сброшен. Новая сессия.', { cls: 'log-system' });
      break;

    default:
      console.log('WS msg:', msg);
  }
}

function sendCommand(text) {
  if (!text.trim()) return;
  window._lastQuery = text;
  logLine(text, { cls: 'log-system', tag: '[YOU]' });
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: 'command',
      text,
      session_id: SESSION_ID,
    }));
  } else {
    logLine('WebSocket не подключён', { cls: 'log-error' });
  }
}

// ══════════════════════════════════════════════════════════════════
// УВЕДОМЛЕНИЯ
// ══════════════════════════════════════════════════════════════════

function showNotification(title, message) {
  const n = $('#notification');
  $('#notif-title').textContent = title;
  $('#notif-msg').textContent = message;
  n.hidden = false;
  setTimeout(() => { n.hidden = true; }, 8000);

  // Web Push (если разрешено)
  if ('Notification' in window && Notification.permission === 'granted') {
    const iconUrl = (location.protocol === 'file:' || !location.host) 
      ? 'http://localhost:8000/static/icon-192.png' 
      : '/static/icon-192.png';
    new Notification(title, { body: message, icon: iconUrl });
  }
}

// ══════════════════════════════════════════════════════════════════
// СБРОС КОНТЕКСТА
// ══════════════════════════════════════════════════════════════════

function resetContext() {
  SESSION_ID = resetSessionId();
  $('#session-status').textContent = `🔑 Сессия: ${SESSION_ID.slice(-6)}`;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'reset_context', session_id: SESSION_ID }));
  }
  logLine('Контекст сброшен. Новая сессия: ' + SESSION_ID.slice(-6),
    { cls: 'log-system' });
}

// ══════════════════════════════════════════════════════════════════
// ВВОД
// ══════════════════════════════════════════════════════════════════

const cmdHistory = [];
let cmdHistoryIdx = -1;

function setupInput() {
  const input = $('#cmd-input');
  const btnSend = $('#btn-send');

  function submit() {
    const text = input.value.trim();
    if (!text) return;
    cmdHistory.push(text);
    cmdHistoryIdx = cmdHistory.length;
    input.value = '';

    // Сначала локальные команды (если файл подключён)
    if (typeof window.handleLocalCommand === 'function') {
      const localResult = window.handleLocalCommand(text);
      if (localResult && localResult.handled) {
        logLine(text, { cls: 'log-system', tag: '[YOU]' });
        logLine(localResult.response, { cls: 'log-ok', tag: '[LOCAL]', isResponse: true });
        return;
      }
    }

    sendCommand(text);
  }

  btnSend.addEventListener('click', submit);

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      submit();
    } else if (e.key === 'ArrowUp') {
      if (cmdHistoryIdx > 0) {
        cmdHistoryIdx--;
        input.value = cmdHistory[cmdHistoryIdx];
      }
    } else if (e.key === 'ArrowDown') {
      if (cmdHistoryIdx < cmdHistory.length - 1) {
        cmdHistoryIdx++;
        input.value = cmdHistory[cmdHistoryIdx];
      } else {
        cmdHistoryIdx = cmdHistory.length;
        input.value = '';
      }
    } else if (e.key === '?' && input.value === '') {
      e.preventDefault();
      $('#help-modal').hidden = false;
    }
  });

  // Глобальные горячие клавиши
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      $('#help-modal').hidden = true;
      closeDrawer();
    }
    if (e.ctrlKey && e.key === 'l') {
      e.preventDefault();
      clearLog();
    }
    if (e.ctrlKey && e.key === 'r') {
      e.preventDefault();
      resetContext();
    }
  });
}

// ══════════════════════════════════════════════════════════════════
// ГОЛОСОВОЙ ВВОД (Web Speech API)
// ══════════════════════════════════════════════════════════════════

function setupVoice() {
  const btn = $('#btn-voice');
  if (!btn) return;

  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) {
    btn.style.display = 'none';
    return;
  }

  const rec = new SpeechRec();
  rec.lang = 'ru-RU';
  rec.continuous = false;
  rec.interimResults = false;

  let listening = false;
  btn.addEventListener('click', () => {
    if (listening) {
      rec.stop();
      return;
    }
    rec.start();
  });

  rec.addEventListener('start', () => {
    listening = true;
    btn.classList.add('listening');
    $('#voice-status').style.display = '';
  });

  rec.addEventListener('end', () => {
    listening = false;
    btn.classList.remove('listening');
    $('#voice-status').style.display = 'none';
  });

  rec.addEventListener('result', (e) => {
    const text = e.results[0][0].transcript;
    $('#cmd-input').value = text;
    sendCommand(text);
    $('#cmd-input').value = '';
  });
}

// ══════════════════════════════════════════════════════════════════
// КЛОК
// ══════════════════════════════════════════════════════════════════

function startClock() {
  const el = $('#clock');
  function tick() {
    el.textContent = nowTime();
  }
  tick();
  setInterval(tick, 1000);
}

// ══════════════════════════════════════════════════════════════════
// MATRIX RAIN
// ══════════════════════════════════════════════════════════════════

function startMatrixRain() {
  const canvas = $('#matrix-rain');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  const cols = Math.floor(canvas.width / 14);
  const drops = new Array(cols).fill(0);
  const chars = 'アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン01';
  setInterval(() => {
    ctx.fillStyle = 'rgba(5, 10, 15, 0.05)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#00f5ff';
    ctx.font = '14px monospace';
    drops.forEach((y, i) => {
      const ch = chars[Math.floor(Math.random() * chars.length)];
      ctx.fillText(ch, i * 14, y * 14);
      drops[i] = y > canvas.height / 14 || Math.random() > 0.97 ? 0 : y + 1;
    });
  }, 80);
  window.addEventListener('resize', () => {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  });
}


// ══════════════════════════════════════════════════════════════════
// ОБУЧЕНИЕ: кнопки-предложения
// ══════════════════════════════════════════════════════════════════

async function fetchSuggestions(query) {
  try {
    const r = await fetch(CONFIG.api_base + '/dashboard/api/learning/suggest', {
      method: 'POST',
      mode: 'cors', credentials: 'omit',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ query }),
    });
    if (!r.ok) return [];
    const data = await r.json();
    return data.suggestions || [];
  } catch (e) {
    return [];
  }
}

function renderSuggestions(query, suggestions) {
  const out = $('#log-output');
  if (!out) return;
  const line = document.createElement('div');
  line.className = 'log-line log-response';
  line.innerHTML = `
    <span class="log-time">${nowTime()}</span>
    <span class="log-tag">[СИЕН]</span>
    <span class="log-msg">
      🤔 Не знаю команду «${escapeHTML(query)}». Что ты имел в виду?
      <div class="suggestions-block">
        <div class="suggestions-title">◈ ВЫБЕРИ ДЕЙСТВИЕ</div>
        <div class="suggestion-btns" id="sugg-btns"></div>
      </div>
    </span>
  `;
  out.appendChild(line);

  const btnsEl = line.querySelector('#sugg-btns');

  suggestions.forEach(s => {
    const btn = document.createElement('button');
    btn.className = 'suggestion-btn';
    btn.textContent = s.label;
    btn.addEventListener('click', () => applySuggestion(query, s, line));
    btnsEl.appendChild(btn);
  });

  // "Ввести своё"
  const custom = document.createElement('button');
  custom.className = 'suggestion-btn custom';
  custom.textContent = '⌨ Ввести своё объяснение';
  custom.addEventListener('click', () => {
    const answer = prompt('Как объяснить команду «' + query + '»?\nНапример: «показать задачи» или «/dashboard/#tasks»');
    if (!answer) return;
    applySuggestion(query, {
      label: answer,
      action: { type: 'custom', text: answer },
    }, line);
  });
  btnsEl.appendChild(custom);

  // "Отмена"
  const cancel = document.createElement('button');
  cancel.className = 'suggestion-btn cancel';
  cancel.textContent = '❌ Отмена';
  cancel.addEventListener('click', () => {
    btnsEl.parentElement.innerHTML = '<div class="suggestions-title text-dim">Отменено</div>';
  });
  btnsEl.appendChild(cancel);

  out.scrollTop = out.scrollHeight;
}

async function applySuggestion(query, suggestion, lineEl) {
  const a = suggestion.action || {};
  // Выполняем действие
  if (a.type === 'nav' && a.url) {
    window.open(CONFIG.api_base + a.url, '_blank', 'noopener');
  } else if (a.type === 'plugin') {
    logLine(`🔌 Плагин: ${a.label || a.module}`, { cls: 'log-ok', tag: '[PLUGIN]' });
  } else if (a.type === 'agent' && a.agent) {
    sendCommand(a.text || query);
  } else if (a.type === 'custom' && a.text) {
    sendCommand(a.text);
  }

  // Обучаем сервер
  try {
    await fetch(CONFIG.api_base + '/dashboard/api/learning/learn', {
      method: 'POST',
      mode: 'cors', credentials: 'omit',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        phrase: query,
        action_type: a.type || 'custom',
        action_data: a,
      }),
    });
    // Заменяем кнопки на подтверждение
    if (lineEl) {
      const block = lineEl.querySelector('.suggestions-block');
      if (block) {
        block.innerHTML = '<div class="suggestions-title text-green">✓ ЗАПОМНИЛ: «' + escapeHTML(query) + '» → ' + escapeHTML(suggestion.label) + '</div>';
      }
    }
  } catch (e) {
    console.warn('Learning save failed:', e);
  }
}

// Проверка ответа Логоса и замена на интерактивные предложения
function maybeShowSuggestions(msg) {
  const text = (msg.formatted || msg.result || '').toString().toLowerCase();
  // Если это fallback-уточнение от Логоса — подменяем на предложения
  if (text.includes('уточнение') || text.includes('уточни запрос') || text.includes('не понял')) {
    return true;
  }
  return false;
}


// ══════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  $('#session-status').textContent = `🔑 Сессия: ${SESSION_ID.slice(-6)}`;
  $('#emotion-status').textContent = '😐 Эмоция: —';

  $('#btn-clear').addEventListener('click', clearLog);
  $('#btn-help').addEventListener('click', () => { $('#help-modal').hidden = false; });
  $('#help-close').addEventListener('click', () => { $('#help-modal').hidden = true; });
  $('#btn-reset-ctx').addEventListener('click', resetContext);

  // DRAWER с агентами
  const drawer = $('#agents-drawer');
  const backdrop = $('#drawer-backdrop');
  function openDrawer() {
    drawer.hidden = false;
    backdrop.hidden = false;
    renderAgents();
  }
  function closeDrawer() {
    drawer.hidden = true;
    backdrop.hidden = true;
  }
  $('#btn-agents-drawer').addEventListener('click', openDrawer);
  $('#drawer-close').addEventListener('click', closeDrawer);
  backdrop.addEventListener('click', closeDrawer);
  $('#notif-close').addEventListener('click', () => { $('#notification').hidden = true; });

  // Клик по фону модала закрывает его
  $('#help-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) e.target.hidden = true;
  });

  startClock();
  startMatrixRain();
  setupInput();
  setupVoice();
  connectWS();
  // Запросим разрешение на нативные уведомления (для Push)
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }

  logLine('HUD 3.0 готов. Сессия: ' + SESSION_ID.slice(-6),
    { cls: 'log-ok', tag: '[СИЕН]' });
});
