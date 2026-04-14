// hud/nav_commands.js — локальный роутинг команд навигации
// Работает и через http://localhost:8000/hud/, и при file:// открытии.
(function () {
  'use strict';

  // Определяем базовый URL оркестратора
  const BASE = (location.protocol === 'file:' || !location.host)
    ? 'http://localhost:8000'
    : location.origin;

  const NAV_MAP = {
    'настройки':      [BASE + '/dashboard/profile', 'Открываю настройки'],
    'профиль':        [BASE + '/dashboard/profile', 'Открываю профиль'],
    'дашборд':        [BASE + '/dashboard/profile', 'Открываю дашборд'],
    'мониторинг':     [BASE + '/dashboard/system',  'Открываю мониторинг'],
    'система':        [BASE + '/dashboard/system',  'Открываю мониторинг'],
    'cpu':            [BASE + '/dashboard/system',  'Мониторинг CPU/RAM/VRAM'],
    'ram':            [BASE + '/dashboard/system',  'Мониторинг RAM'],
    'vram':           [BASE + '/dashboard/system',  'Мониторинг GPU'],
    'агенты':         [BASE + '/agents/list',       'Список агентов'],
    'список агентов': [BASE + '/agents/list',       'Список агентов'],
  };

  const PLUGIN_HINTS = {
    'канбан':   '📋 Канбан-доска: plugins/productivity/kanban.py (UI ещё не реализован)',
    'матрица':  '🎯 Матрица Эйзенхауэра: plugins/productivity/eisenhower_matrix.py',
    'привычки': '🎯 Чек-лист привычек: plugins/productivity/habit_checklist.py',
    'цели':     '🎯 Цели: plugins/productivity/goals.py',
    'фокус':    '🔒 Режим фокусировки: plugins/productivity/focus_mode.py',
    'план дня': '📅 План дня: plugins/productivity/daily_planner.py',
    'рутины':   '🔁 Рутины: plugins/productivity/routines.py',
    'макросы':  '⚙️ Макросы: plugins/productivity/macros.py',
    'заметки':  '📝 Заметки: plugins/core/notes.py',
  };

  function openUrl(url) {
    // Открываем в новой вкладке — HUD остаётся активным
    window.open(url, '_blank', 'noopener');
  }

  window.handleLocalCommand = function (text) {
    const t = (text || '').toLowerCase().trim();
    if (!t) return null;

    const cleaned = t.replace(/^(открой|покажи|запусти|открыть)\s+/, '').trim();

    for (const [key, val] of Object.entries(NAV_MAP)) {
      if (cleaned === key || cleaned.startsWith(key + ' ')) {
        setTimeout(() => openUrl(val[0]), 200);
        return { handled: true, response: '🔗 ' + val[1] + '\n' + val[0] };
      }
    }

    for (const [key, hint] of Object.entries(PLUGIN_HINTS)) {
      if (cleaned === key || cleaned.startsWith(key + ' ')) {
        return { handled: true, response: hint };
      }
    }

    return null;
  };

  console.log('[nav_commands] Base URL:', BASE);
})();
