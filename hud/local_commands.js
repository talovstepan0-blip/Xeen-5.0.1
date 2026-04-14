/**
 * ╔══════════════════════════════════════════════════════════════════╗
 * ║  hud/local_commands.js — Локальные команды для Electron HUD     ║
 * ║  Сиен 2.0 Beta                                                   ║
 * ║                                                                  ║
 * ║  Подключить в index.html ПЕРЕД main.js:                          ║
 * ║  <script src="hud/local_commands.js"></script>                    ║
 * ╚══════════════════════════════════════════════════════════════════╝
 *
 * Архитектура:
 *   1. LocalCommandsMatcher — загружает конфиг и распознаёт команды
 *   2. LocalCommandsExecutor — выполняет распознанные команды
 *   3. handleLocalCommand(text) — публичная функция для main.js
 */

'use strict';

// ══════════════════════════════════════════════════════════════════════════════
// КОНФИГУРАЦИЯ И КОНСТАНТЫ
// ══════════════════════════════════════════════════════════════════════════════

const LC_CONFIG = {
  apiBase:            'http://localhost:8100',   // веб-дашборд
  orchestratorBase:   'http://localhost:8000',   // оркестратор
  matchTimeout:       50,    // ms — лимит на распознавание
  confidenceThreshold: 0.5,  // минимальная уверенность
  logHistory:         true,
  historyMaxLocal:    100,
};

// Имена агентов и их порты
const AGENT_MAP = {
  apollo:  { name: 'АПОЛЛОН', port: 8002 },
  hermes:  { name: 'ГЕРМЕС',  port: 8003 },
  wen:     { name: 'ВЭНЬ',    port: 8006 },
  ahill:   { name: 'АХИЛЛ',   port: 8003 },
  plutos:  { name: 'ПЛУТОС',  port: 8009 },
  cronos:  { name: 'КРОНОС',  port: 8001 },
  argus:   { name: 'АРГУС',   port: 8002 },
  logos:   { name: 'ЛОГОС',   port: 8005 },
  kun:     { name: 'КУН',     port: 8007 },
  master:  { name: 'МАСТЕР',  port: 8008 },
  musa:    { name: 'МУСА',    port: 8010 },
  kallio:  { name: 'КАЛЛИО',  port: 8011 },
  hefest:  { name: 'ГЕФЕСТ',  port: 8012 },
  avto:    { name: 'АВТО',    port: 8013 },
  eho:     { name: 'ЭХО',     port: 8016 },
  irida:   { name: 'ИРИДА',   port: 8017 },
  fenix:   { name: 'ФЕНИКС',  port: 8004 },
};

// Локальная история (в памяти, синхронизируется с сервером)
const localHistory = [];

// ══════════════════════════════════════════════════════════════════════════════
// МАТЧЕР (клиентская часть)
// ══════════════════════════════════════════════════════════════════════════════

/**
 * LocalCommandsMatcher — быстрое распознавание команд на клиенте.
 * 
 * Содержит базовый набор паттернов; полный набор загружается с сервера.
 * Работает даже без соединения с сервером.
 */
const LocalCommandsMatcher = (() => {

  // ── Базовые паттерны (встроенные, работают без сервера) ───────────────────
  const BUILTIN_PATTERNS = [

    // Управление агентами — остановка
    {
      cmd: 'agent_stop',
      regex: /^(?:сиен[,\s]+)?(?:останови|выключи|заморозь|деактивируй|стопни|отключи|вырубай|погаси|убей|kill|stop)\s+(\w+)/i,
      extract: m => ({ agent_name: m[1].toLowerCase() }),
      confidence: 0.92,
    },
    // Управление агентами — запуск
    {
      cmd: 'agent_start',
      regex: /^(?:сиен[,\s]+)?(?:запусти|включи|активируй|стартуй|подними|разморозь|start|run|launch)\s+(\w+)/i,
      extract: m => ({ agent_name: m[1].toLowerCase() }),
      confidence: 0.92,
    },
    // Управление агентами — перезапуск
    {
      cmd: 'agent_restart',
      regex: /^(?:сиен[,\s]+)?(?:перезапусти|ребутни|рестарт|перегрузи|перезагрузи|restart|reboot)\s+(\w+)/i,
      extract: m => ({ agent_name: m[1].toLowerCase() }),
      confidence: 0.90,
    },
    // Статус агентов
    {
      cmd: 'agent_status_all',
      regex: /(?:статус|status|что работает|покажи.*агент|кто.*запущен|кто.*живой|agents?\s+status|все агенты|состояние агентов)/i,
      extract: () => ({}),
      confidence: 0.88,
    },
    // Пауза всего
    {
      cmd: 'pause_all',
      regex: /^(?:пауза|заморозь всё|приостанови всё|стоп всё|pause all|halt)$/i,
      extract: () => ({}),
      confidence: 0.90,
    },
    // Возобновление
    {
      cmd: 'resume_all',
      regex: /^(?:продолжить работу|возобновить|снять паузу|разморозь всё|resume|go|поехали)$/i,
      extract: () => ({}),
      confidence: 0.90,
    },

    // ── Система ──────────────────────────────────────────────────────────────
    {
      cmd: 'system_shutdown',
      regex: /(?:выключи\s+компьютер|выключить\s+пк|шутдаун|shutdown|poweroff|завершить\s+работу\s+(?:пк|компьютера))/i,
      extract: () => ({}),
      confidence: 0.95,
    },
    {
      cmd: 'system_reboot',
      regex: /(?:перезагрузи\s+компьютер|перезагрузить\s+пк|ребут|reboot\s+pc|restart\s+computer)/i,
      extract: () => ({}),
      confidence: 0.95,
    },
    {
      cmd: 'open_app',
      regex: /^(?:открой|запусти|стартни|launch|open)\s+(.+)$/i,
      extract: m => ({ app_name: m[1].trim() }),
      confidence: 0.85,
    },
    {
      cmd: 'window_minimize_all',
      regex: /(?:сверни все окна|покажи рабочий стол|show desktop|minimize all)/i,
      extract: () => ({}),
      confidence: 0.90,
    },
    {
      cmd: 'process_kill',
      regex: /(?:убей|убить|заверши|kill)\s+(?:процесс|задачу|task|process)?\s*(.+)/i,
      extract: m => ({ process_name: m[1].trim() }),
      confidence: 0.88,
    },

    // ── Мониторинг ───────────────────────────────────────────────────────────
    {
      cmd: 'monitor_ram',
      regex: /(?:озу|оперативк|оперативная память|ram\s*(?:usage|free|info)?|сколько памяти|загрузка памяти)/i,
      extract: () => ({}),
      confidence: 0.85,
    },
    {
      cmd: 'monitor_cpu',
      regex: /(?:процессор|цпу|cpu\s*(?:usage|load|temp)?|загрузка\s+(?:процессора|цпу))/i,
      extract: () => ({}),
      confidence: 0.85,
    },
    {
      cmd: 'monitor_gpu',
      regex: /(?:видеокарт|видюх|gpu\s*(?:usage|temp|load)?|температура\s+(?:видеокарты|gpu))/i,
      extract: () => ({}),
      confidence: 0.85,
    },
    {
      cmd: 'monitor_disk',
      regex: /(?:место на диске|диск\s+(?:свободно|занято)|disk\s+(?:space|usage)|ssd|hdd)/i,
      extract: () => ({}),
      confidence: 0.85,
    },
    {
      cmd: 'network_ip',
      regex: /(?:мой\s+ip|ip\s+адрес|my\s+ip|внешний\s+ip|what\s+is\s+my\s+ip)/i,
      extract: () => ({}),
      confidence: 0.90,
    },
    {
      cmd: 'network_check',
      regex: /(?:проверь\s+интернет|пинг|ping\s+google|интернет\s+работает|check\s+internet|есть\s+интернет)/i,
      extract: () => ({}),
      confidence: 0.88,
    },
    {
      cmd: 'battery_status',
      regex: /(?:уровень\s+заряда|заряд\s+батарей|аккумулятор|battery\s*(?:level|status)?|сколько\s+заряда)/i,
      extract: () => ({}),
      confidence: 0.88,
    },

    // ── Задачи ────────────────────────────────────────────────────────────────
    {
      cmd: 'tasks_show',
      regex: /(?:(?:что\s+у\s+меня\s+)?запланировано|мои\s+задачи|список\s+задач|покажи\s+задачи|todo|show\s+tasks|задачи\s+на\s+(?:сегодня|день))/i,
      extract: () => ({}),
      confidence: 0.88,
    },
    {
      cmd: 'task_complete',
      regex: /(?:задача|задание)\s*(?:номер\s*)?(\d+)\s*(?:готова|выполнена|сделана|done|complete)/i,
      extract: m => ({ task_id: parseInt(m[1]) }),
      confidence: 0.90,
    },
    {
      cmd: 'task_complete_keyword',
      regex: /(?:выполни|отметь|закрой)\s+(?:задачу|задание)\s*(?:номер\s*)?(\d+)/i,
      extract: m => ({ task_id: parseInt(m[1]) }),
      confidence: 0.88,
    },
    {
      cmd: 'task_delete',
      regex: /(?:удали|удалить|убери|убрать|забудь|delete|remove)\s+(?:задачу|задание|напоминание)\s*(?:номер\s*)?(\d+)?/i,
      extract: m => ({ task_id: m[1] ? parseInt(m[1]) : null }),
      confidence: 0.88,
    },

    // ── Информация ────────────────────────────────────────────────────────────
    {
      cmd: 'get_weather',
      regex: /(?:погода|прогноз погоды|какая погода|погода на |weather)/i,
      extract: () => ({}),
      confidence: 0.92,
    },
    {
      cmd: 'get_currency',
      regex: /(?:курс валют|курс доллара|курс евро|доллар|евро|usd|eur|rub|гривна|UAH|конвертация валют)/i,
      extract: () => ({}),
      confidence: 0.92,
    },

    // ── Системные ────────────────────────────────────────────────────────────
    { cmd: 'sien_help',    regex: /^(?:помощь|помоги|справка|help|что умеешь|команды|список команд)$/i, extract: () => ({}), confidence: 0.90 },
    { cmd: 'sien_version', regex: /^(?:версия|version|ver|какая версия|сиен версия)$/i,                extract: () => ({}), confidence: 0.92 },
    { cmd: 'sien_reload',  regex: /^(?:перезагрузить конфиг|reload config|обновить команды)$/i,         extract: () => ({}), confidence: 0.92 },
  ];

  // ── Поиск совпадения ──────────────────────────────────────────────────────

  function match(text) {
    const t = performance.now();
    const clean = text.trim().toLowerCase();

    for (const pattern of BUILTIN_PATTERNS) {
      const m = clean.match(pattern.regex);
      if (m) {
        const params = pattern.extract(m);
        // Разрешаем имя агента в ID
        if (params.agent_name) {
          params.agent = resolveAgentName(params.agent_name);
        }
        const elapsed = performance.now() - t;
        if (elapsed > LC_CONFIG.matchTimeout) {
          console.warn(`[LocalCmd] Превышен лимит: ${elapsed.toFixed(1)}ms`);
        }
        return {
          command_name: pattern.cmd,
          params,
          confidence: pattern.confidence,
          matched_pattern: 'builtin_regex',
          processing_ms: elapsed,
        };
      }
    }
    return null;
  }

  function resolveAgentName(name) {
    const lc = name.toLowerCase();
    const aliases = {
      'аполлон': 'apollo', 'аполлона': 'apollo',
      'гермес': 'hermes',  'гермеса': 'hermes',
      'вэнь': 'wen',       'вэня': 'wen',
      'ахилл': 'ahill',    'ахилла': 'ahill',
      'плутос': 'plutos',  'плутоса': 'plutos',
      'кронос': 'cronos',  'кроноса': 'cronos',
      'аргус': 'argus',    'аргуса': 'argus',
      'логос': 'logos',    'логоса': 'logos',
      'кун': 'kun',        'муса': 'musa',
      'мастер': 'master',  'гефест': 'hefest',
      'авто': 'avto',      'эхо': 'eho',
      'ирида': 'irida',    'феникс': 'fenix',
      'каллио': 'kallio',
    };
    return aliases[lc] || AGENT_MAP[lc] ? lc : lc;
  }

  return { match, BUILTIN_PATTERNS };
})();


// ══════════════════════════════════════════════════════════════════════════════
// ИСПОЛНИТЕЛЬ КОМАНД
// ══════════════════════════════════════════════════════════════════════════════

const LocalCommandsExecutor = (() => {

  async function execute(match, rawText) {
    const { command_name, params } = match;

    switch (command_name) {

      // ── Агенты ──────────────────────────────────────────────────────────────
      case 'agent_stop':
        return await agentAction('stop', params);

      case 'agent_start':
        return await agentAction('start', params);

      case 'agent_restart':
        return await agentAction('restart', params);

      case 'agent_status_all':
        return await fetchAgentsStatus();

      case 'pause_all':
        return await broadcastAgents('pause');

      case 'resume_all':
        return await broadcastAgents('resume');

      case 'agent_priority':
        return await agentAction('priority', params);

      // ── Система ─────────────────────────────────────────────────────────────
      case 'system_shutdown':
        return await systemCommand('shutdown');

      case 'system_reboot':
        return await systemCommand('reboot');

      case 'open_app':
        return await openApplication(params.app_name);

      case 'window_minimize_all':
        return minimizeAllWindows();

      case 'process_kill':
        return await killProcess(params.process_name);

      // ── Мониторинг ───────────────────────────────────────────────────────────
      case 'monitor_ram':
        return await getSystemInfo('ram');

      case 'monitor_cpu':
        return await getSystemInfo('cpu');

      case 'monitor_gpu':
        return await getSystemInfo('gpu');

      case 'monitor_disk':
        return await getSystemInfo('disk');

      case 'network_ip':
        return await getNetworkInfo('ip');

      case 'network_check':
        return await checkInternet();

      case 'battery_status':
        return await getSystemInfo('battery');

      // ── Задачи ───────────────────────────────────────────────────────────────
      case 'tasks_show':
        return await fetchTasks();

      case 'task_complete':
      case 'task_complete_keyword':
        return await completeTask(params.task_id);

      case 'task_delete':
        return await deleteTask(params.task_id);

      // ── Информация ───────────────────────────────────────────────────────────
      case 'get_weather':
        return await getWeather();

      case 'get_currency':
        return await getCurrency();

      // ── Системные Сиен ───────────────────────────────────────────────────────
      case 'sien_help':
        return showHelp();

      case 'sien_version':
        return `◈ Сиен 2.0 Beta | HUD v2.0 | Агентов: ${Object.keys(AGENT_MAP).length}`;

      case 'sien_reload':
        return reloadConfig();

      default:
        return null; // не обработано — передать в оркестратор
    }
  }

  // ── Агенты ──────────────────────────────────────────────────────────────────

  async function agentAction(action, params) {
    const agentId = params.agent || params.agent_name;
    if (!agentId) return '❌ Не указан агент. Пример: "останови аполлона"';

    const agentInfo = AGENT_MAP[agentId];
    const displayName = agentInfo?.name || agentId.toUpperCase();

    try {
      const r = await fetchWithTimeout(
        `${LC_CONFIG.orchestratorBase}/agent/${action}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent: agentId, ...params }),
        },
        3000
      );
      if (r.ok) {
        const icons = { stop: '🔴', start: '🟢', restart: '🔄', priority: '⚙️' };
        return `${icons[action] || '✓'} ${displayName}: ${action} выполнен`;
      }
      return `⚠️ ${displayName}: сервер вернул ${r.status}`;
    } catch (e) {
      return `❌ ${displayName}: нет связи с оркестратором`;
    }
  }

  async function fetchAgentsStatus() {
    try {
      const r = await fetchWithTimeout(`${LC_CONFIG.orchestratorBase}/agents`, {}, 3000);
      if (!r.ok) return '⚠️ Не удалось получить статус агентов';
      const data = await r.json();
      const agents = data.agents || [];
      const lines = agents.map(a => {
        const icon = a.status === 'ok' ? '🟢' : a.status === 'degraded' ? '🟡' : '🔴';
        return `${icon} ${a.name || a.id}: ${a.status}`;
      });
      return lines.length > 0 ? lines.join('\n') : 'Нет данных об агентах';
    } catch {
      return '❌ Оркестратор недоступен';
    }
  }

  async function broadcastAgents(action) {
    try {
      await fetchWithTimeout(
        `${LC_CONFIG.orchestratorBase}/agents/${action}`,
        { method: 'POST' },
        3000
      );
      return action === 'pause'
        ? '⏸ Все агенты поставлены на паузу'
        : '▶ Работа всех агентов возобновлена';
    } catch {
      return `❌ Не удалось выполнить ${action}`;
    }
  }

  // ── Система ──────────────────────────────────────────────────────────────────

  async function systemCommand(cmd) {
    const confirmText = cmd === 'shutdown'
      ? '⚠️ Выключить компьютер?'
      : '⚠️ Перезагрузить компьютер?';

    if (!confirm(confirmText)) return '↩ Отменено';

    try {
      const r = await fetchWithTimeout(
        `${LC_CONFIG.apiBase}/dashboard/api/system/${cmd}`,
        { method: 'POST' },
        3000
      );
      return r.ok
        ? (cmd === 'shutdown' ? '🔌 Завершение работы...' : '🔄 Перезагрузка...')
        : `❌ Ошибка выполнения команды ${cmd}`;
    } catch {
      // Если нет сервера — пробуем через Electron IPC
      if (window.electronAPI?.systemCommand) {
        window.electronAPI.systemCommand(cmd);
        return cmd === 'shutdown' ? '🔌 Завершение работы...' : '🔄 Перезагрузка...';
      }
      return `❌ Команда ${cmd} недоступна без сервера`;
    }
  }

  async function openApplication(appName) {
    if (!appName) return '❌ Не указано имя программы';
    try {
      const r = await fetchWithTimeout(
        `${LC_CONFIG.apiBase}/dashboard/api/system/open_app`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ app: appName }),
        },
        3000
      );
      return r.ok ? `🚀 Открываю: ${appName}` : `❌ Не удалось открыть: ${appName}`;
    } catch {
      if (window.electronAPI?.openApp) {
        window.electronAPI.openApp(appName);
        return `🚀 Открываю: ${appName}`;
      }
      return `❌ Открытие приложений недоступно`;
    }
  }

  function minimizeAllWindows() {
    if (window.electronAPI?.minimizeAll) {
      window.electronAPI.minimizeAll();
      return '🖥 Все окна свёрнуты';
    }
    // Fallback: в браузере нельзя напрямую
    return 'ℹ️ Для управления окнами нужен Electron HUD';
  }

  async function killProcess(processName) {
    if (!processName) return '❌ Не указан процесс';
    try {
      const r = await fetchWithTimeout(
        `${LC_CONFIG.apiBase}/dashboard/api/system/kill_process`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ process: processName }),
        },
        3000
      );
      return r.ok ? `💀 Процесс ${processName} завершён` : `❌ Не удалось завершить: ${processName}`;
    } catch {
      return `❌ Управление процессами недоступно`;
    }
  }

  // ── Мониторинг ───────────────────────────────────────────────────────────────

  async function getSystemInfo(type) {
    try {
      const r = await fetchWithTimeout(
        `${LC_CONFIG.apiBase}/dashboard/api/system/info?type=${type}`,
        {},
        3000
      );
      if (r.ok) {
        const d = await r.json();
        return formatSystemInfo(type, d);
      }
    } catch {}
    // Fallback: JavaScript API (частичный)
    return await jsSystemInfo(type);
  }

  function formatSystemInfo(type, d) {
    switch (type) {
      case 'ram':    return `🧠 ОЗУ: ${d.used_gb?.toFixed(1)}/${d.total_gb?.toFixed(1)} ГБ (${d.percent}% занято)`;
      case 'cpu':    return `⚙️ CPU: ${d.percent}% | Ядра: ${d.cores} | Частота: ${d.freq_mhz} МГц`;
      case 'gpu':    return `🎮 GPU: ${d.name} | Загрузка: ${d.load}% | Температура: ${d.temp}°C`;
      case 'disk':   return `💾 Диск: ${d.free_gb?.toFixed(1)} ГБ свободно из ${d.total_gb?.toFixed(1)} ГБ`;
      case 'battery':return `🔋 Батарея: ${d.percent}% | ${d.plugged ? '🔌 Заряжается' : `⏱ ~${d.hours_left}ч`}`;
      default:       return JSON.stringify(d);
    }
  }

  async function jsSystemInfo(type) {
    // Ограниченная информация через browser API
    if (type === 'battery') {
      try {
        const bat = await navigator.getBattery?.();
        if (bat) {
          const pct = Math.round(bat.level * 100);
          return `🔋 Батарея: ${pct}% | ${bat.charging ? '🔌 Заряжается' : ''}`;
        }
      } catch {}
    }
    if (type === 'network_ip') {
      return '📡 IP: получить через API сервера';
    }
    return `ℹ️ Мониторинг ${type}: запросите через сервер (localhost:8100)`;
  }

  async function getNetworkInfo(type) {
    if (type === 'ip') {
      try {
        const r = await fetchWithTimeout('https://api.ipify.org?format=json', {}, 3000);
        if (r.ok) {
          const d = await r.json();
          return `🌐 Внешний IP: ${d.ip}`;
        }
      } catch {}
      return '❌ Не удалось получить IP (нет интернета?)';
    }
    return `❌ Неизвестный тип: ${type}`;
  }

  async function checkInternet() {
    const t0 = performance.now();
    try {
      await fetchWithTimeout('https://www.google.com/generate_204', {}, 3000);
      const ms = Math.round(performance.now() - t0);
      return `✅ Интернет работает | Пинг до Google: ~${ms}ms`;
    } catch {
      return '❌ Интернет недоступен';
    }
  }

  // ── Информация ───────────────────────────────────────────────────────────────

  async function getWeather() {
    try {
      // Попытка получить погоду через API дашборда (если есть прокси)
      const r = await fetchWithTimeout(`${LC_CONFIG.apiBase}/dashboard/api/weather`, {}, 5000);
      if (r.ok) {
        const data = await r.json();
        return formatWeatherResponse(data);
      }
    } catch {}
    
    // Fallback — прямые запросы к публичным API
    try {
      // Open-Meteo (бесплатный, без ключа)
      const geoRes = await fetchWithTimeout('https://geocoding-api.open-meteo.com/v1/search?name=Moscow&count=1&language=ru&format=json', {}, 3000);
      let lat = 55.7558, lon = 37.6173; // Москва по умолчанию
      if (geoRes.ok) {
        const geo = await geoRes.json();
        if (geo.results && geo.results[0]) {
          lat = geo.results[0].latitude;
          lon = geo.results[0].longitude;
        }
      }
      
      const weatherRes = await fetchWithTimeout(
        `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current_weather=true&windspeed_unit=ms`,
        {}, 5000
      );
      if (weatherRes.ok) {
        const w = await weatherRes.json();
        const cw = w.current_weather;
        const icons = {
          0: '☀️', 1: '🌤️', 2: '⛅', 3: '☁️',
          45: '🌫️', 48: '🌫️',
          51: '🌦️', 53: '🌧️', 55: '🌧️', 56: '🌨️', 57: '🌨️',
          61: '🌧️', 63: '🌧️', 65: '🌧️', 66: '❄️', 67: '❄️',
          71: '❄️', 73: '❄️', 75: '❄️', 77: '❄️',
          80: '🌦️', 81: '🌧️', 82: '⛈️',
          95: '⛈️', 96: '⛈️', 99: '⛈️'
        };
        const icon = icons[cw.weathercode] || '🌡️';
        return `${icon} Температура: ${cw.temperature}°C\n💨 Ветер: ${cw.windspeed} м/с\n🧭 Направление: ${cw.winddirection}°`;
      }
    } catch (e) {
      console.warn('[Weather] Open-Meteo failed:', e);
    }
    
    return '🌐 Открываю Яндекс.Погоду... https://yandex.ru/pogoda';
  }

  function formatWeatherResponse(data) {
    if (!data) return '❌ Нет данных о погоде';
    const temp = data.temperature ?? data.temp ?? data.current?.temperature;
    const feelsLike = data.feels_like ?? data.current?.feels_like;
    const humidity = data.humidity ?? data.current?.humidity;
    const wind = data.wind_speed ?? data.current?.wind_speed;
    const desc = data.description ?? data.condition ?? '';
    
    let result = `🌡️ Температура: ${temp}°C`;
    if (feelsLike !== undefined) result += `\n🔹 Ощущается: ${feelsLike}°C`;
    if (humidity !== undefined) result += `\n💧 Влажность: ${humidity}%`;
    if (wind !== undefined) result += `\n💨 Ветер: ${wind} м/с`;
    if (desc) result += `\n${desc}`;
    return result;
  }

  async function getCurrency() {
    try {
      // Попытка получить курс через API дашборда
      const r = await fetchWithTimeout(`${LC_CONFIG.apiBase}/dashboard/api/currency`, {}, 5000);
      if (r.ok) {
        const data = await r.json();
        return formatCurrencyResponse(data);
      }
    } catch {}
    
    // Fallback — публичные API
    try {
      // ЦБ РФ (официальный курс)
      const cbRes = await fetchWithTimeout('https://www.cbr-xml-daily.ru/daily_json.js', {}, 5000);
      if (cbRes.ok) {
        const cb = await cbRes.json();
        const usd = cb.Valute?.USD;
        const eur = cb.Valute?.EUR;
        
        let result = '💱 Курс валют (ЦБ РФ):\\n';
        if (usd) {
          result += `🇺🇸 USD: ${usd.Value.toFixed(2)} ₽ (${usd.Name})\\n`;
        }
        if (eur) {
          result += `🇪🇺 EUR: ${eur.Value.toFixed(2)} ₽ (${eur.Name})`;
        }
        return result.replace(/\\\\n/g, '\\n');
      }
    } catch (e) {
      console.warn('[Currency] CB RF failed:', e);
    }
    
    try {
      // Альтернатива — Binance API (крипто + фиат)
      const binanceRes = await fetchWithTimeout('https://api.binance.com/api/v3/ticker/price?symbol=USDTUSDC', {}, 3000);
      if (binanceRes.ok) {
        return '💱 Курсы доступны на Binance: https://www.binance.com/ru/markets';
      }
    } catch {}
    
    return '💱 Текущие курсы: https://www.cbr.ru/currency_base/daily/';
  }

  function formatCurrencyResponse(data) {
    if (!data) return '❌ Нет данных о курсах';
    let result = '💱 Курсы валют:\\n';
    for (const [key, value] of Object.entries(data)) {
      if (typeof value === 'number') {
        result += `${key.toUpperCase()}: ${value.toFixed(2)} ₽\\n`;
      }
    }
    return result.replace(/\\\\n/g, '\\n').trim();
  }

  // ── Задачи ───────────────────────────────────────────────────────────────────

  async function fetchTasks() {
    try {
      const r = await fetchWithTimeout(`${LC_CONFIG.apiBase}/dashboard/api/tasks`, {}, 3000);
      if (r.ok) {
        const tasks = await r.json();
        if (!tasks.length) return '📋 Список задач пуст';
        return '📋 Задачи:\n' + tasks
          .slice(0, 10)
          .map((t, i) => `${i + 1}. [${t.done ? '✓' : '○'}] ${t.title}`)
          .join('\n');
      }
    } catch {}
    // Fallback — загрузить через loadTasks() из main.js
    if (typeof loadTasks === 'function') {
      loadTasks();
      return '📋 Задачи обновлены';
    }
    return '❌ Не удалось загрузить задачи';
  }

  async function completeTask(taskId) {
    if (!taskId) return '❌ Не указан номер задачи';
    try {
      const r = await fetchWithTimeout(
        `${LC_CONFIG.apiBase}/dashboard/api/tasks/${taskId}/complete`,
        { method: 'POST' },
        3000
      );
      return r.ok ? `✅ Задача #${taskId} выполнена` : `❌ Не удалось выполнить задачу #${taskId}`;
    } catch {
      return `❌ Ошибка выполнения задачи #${taskId}`;
    }
  }

  async function deleteTask(taskId) {
    if (!taskId) return '❌ Не указан номер задачи';
    try {
      const r = await fetchWithTimeout(
        `${LC_CONFIG.apiBase}/dashboard/api/tasks/${taskId}`,
        { method: 'DELETE' },
        3000
      );
      return r.ok ? `🗑 Задача #${taskId} удалена` : `❌ Не удалось удалить задачу #${taskId}`;
    } catch {
      return `❌ Ошибка удаления задачи #${taskId}`;
    }
  }

  // ── Системные Сиен ────────────────────────────────────────────────────────────

  function showHelp() {
    return [
      '⚡ ЛОКАЛЬНЫЕ КОМАНДЫ (без LLM):',
      '',
      '🤖 Агенты:',
      '  останови/запусти/перезапусти [имя]',
      '  статус агентов | пауза | продолжить',
      '',
      '💻 Система:',
      '  открой [программу] | сверни все окна',
      '  убей процесс [имя] | выключи компьютер',
      '',
      '📊 Мониторинг:',
      '  озу / процессор / видеокарта / диск',
      '  мой ip | проверь интернет | батарея',
      '',
      '✅ Задачи:',
      '  покажи задачи | задача 3 готова',
      '  удали задачу 5',
    ].join('\n');
  }

  async function reloadConfig() {
    try {
      const r = await fetchWithTimeout(
        `${LC_CONFIG.apiBase}/dashboard/api/local_commands/reload`,
        { method: 'POST' },
        3000
      );
      return r.ok ? '🔄 Конфигурация команд перезагружена' : '⚠️ Ошибка перезагрузки конфига';
    } catch {
      return '❌ Сервер недоступен';
    }
  }

  // ── Утилиты ──────────────────────────────────────────────────────────────────

  function fetchWithTimeout(url, options = {}, timeoutMs = 3000) {
    return fetch(url, { ...options, signal: AbortSignal.timeout(timeoutMs) });
  }

  return { execute };
})();


// ══════════════════════════════════════════════════════════════════════════════
// ИСТОРИЯ И ЛОГИРОВАНИЕ
// ══════════════════════════════════════════════════════════════════════════════

function logCommandHistory(rawText, matchResult, result, ok) {
  if (!LC_CONFIG.logHistory) return;

  const entry = {
    cmd:  rawText,
    name: matchResult?.command_name || 'unknown',
    conf: matchResult?.confidence || 0,
    ok,
    ts:   new Date().toLocaleString('ru-RU'),
  };

  localHistory.unshift(entry);
  if (localHistory.length > LC_CONFIG.historyMaxLocal) {
    localHistory.pop();
  }

  // Асинхронно отправляем на сервер
  if (matchResult) {
    fetch(`${LC_CONFIG.apiBase}/dashboard/api/profile/history/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        command_name: matchResult.command_name,
        raw_text:     rawText,
        params:       matchResult.params || {},
        confidence:   matchResult.confidence || 0,
        ok,
      }),
    }).catch(() => {});
  }
}


// ══════════════════════════════════════════════════════════════════════════════
// ПУБЛИЧНАЯ ФУНКЦИЯ — вызывается из main.js
// ══════════════════════════════════════════════════════════════════════════════

/**
 * handleLocalCommand(text)
 * 
 * Вызывается из sendCommand() в main.js ПЕРЕД отправкой в WebSocket.
 * 
 * @param {string} text — текст команды пользователя
 * @returns {Promise<boolean>} true если команда обработана локально
 */
async function handleLocalCommand(text) {
  if (!text || !text.trim()) return false;

  const t0 = performance.now();

  // 1. Распознаём команду
  const matchResult = LocalCommandsMatcher.match(text.trim());

  if (!matchResult) {
    return false; // не распознана — передаём дальше
  }

  const elapsed = performance.now() - t0;
  console.log(
    `[LocalCmd] ✓ "${text}" → ${matchResult.command_name} ` +
    `(conf: ${matchResult.confidence.toFixed(2)}, ${elapsed.toFixed(1)}ms)`
  );

  // 2. Выполняем
  let result = null;
  let ok = true;
  try {
    result = await LocalCommandsExecutor.execute(matchResult, text);
  } catch (e) {
    result = `❌ Ошибка выполнения команды: ${e.message}`;
    ok = false;
  }

  // 3. Отображаем результат
  if (result !== null && typeof addLog === 'function') {
    addLog(result, ok ? 'ok' : 'error', 'ЛОКАЛ', true);
  }

  // 4. Логируем историю
  logCommandHistory(text, matchResult, result, ok);

  return true; // команда обработана локально
}


// ══════════════════════════════════════════════════════════════════════════════
// ЭКСПОРТ (для Electron / CommonJS)
// ══════════════════════════════════════════════════════════════════════════════

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { handleLocalCommand, LocalCommandsMatcher, LocalCommandsExecutor };
}

console.log('[LocalCmd] Модуль локальных команд загружен (Сиен 2.0 Beta)');
