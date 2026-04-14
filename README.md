# Сиен 3.0.0 — Инструкция по установке и запуску

## Что нового против 2.0 Beta

- 🔥 **Все агенты теперь стартуют** (исправлен баг "Attribute app not found")
- 🧠 **Диалоговая память** + эмоциональный анализ
- ⚡ **LLM-кэш** через `core/llm_cache.py`
- 📱 **PWA**: установка на телефон, офлайн-страница, push-уведомления
- 🔒 **2FA** (TOTP) и **AES-256-GCM** шифрование
- 📊 **Мониторинг** CPU/RAM/VRAM на `/dashboard/system`
- 🎨 **Переписан HUD CSS**: 14px+, никаких наложений, word-wrap всюду
- 🛑 **Graceful shutdown** (SIGTERM/SIGINT)
- 🔌 **20 плагинов** (core + productivity)
- 🛠 **Расширения агентов:** Гермес (Telethon/VK/A/B), Вэнь (IMAP/Calendar), Мастер (сон/питание), Плутос (Тинькофф/Binance), Кун (генерация курсов), Аполлон (Whisper/Piper)

---

## Структура проекта

```
sien3/
├── orchestrator.py          # Центральный хаб (порт 8000)
├── start.py                  # Точка входа
├── requirements.txt
├── core/                     # Ядро системы
│   ├── llm_cache.py
│   ├── emotion.py
│   ├── encryption.py
│   ├── graceful_shutdown.py
│   └── local_commands.py
├── agents/                   # 20 агентов + расширения
│   ├── ahill.py, fenix.py, logos.py, wen.py, ...
│   ├── wen_email.py          # IMAP/SMTP + Google Calendar
│   ├── master_ext.py         # Сон, питание, тренировки
│   ├── plutos_ext.py         # Тинькофф / Binance
│   └── kun_ext.py            # Генерация курсов
├── web/
│   └── dashboard.py          # /dashboard/* (профиль, мониторинг, 2FA)
├── hud/                      # HUD интерфейс
│   ├── index.html
│   ├── style.css
│   ├── main.js
│   └── local_commands.js
├── templates/
│   ├── profile.html
│   ├── system.html
│   └── dashboard_email.html
├── static/                   # PWA + иконки
│   ├── manifest.json
│   ├── service-worker.js
│   ├── offline.html
│   ├── icon-192.png
│   └── icon-512.png
├── plugins/
│   ├── core/                 # 10 плагинов
│   │   ├── weather.py, news.py, tasks.py, notes.py,
│   │   ├── calendar.py, scheduler.py, settings.py,
│   │   └── logger.py, cache_helper.py, sentiment.py
│   └── productivity/         # 10 плагинов
│       ├── kanban.py, eisenhower_matrix.py, project_kanban.py,
│       ├── macros.py, goals.py, routines.py,
│       ├── productivity_stats.py, focus_mode.py,
│       └── daily_planner.py, habit_checklist.py
├── data/                     # SQLite базы (создаётся автоматически)
├── logs/
└── backups/
```

---

## 1. Установка

### Минимальная (только ядро + 5 базовых агентов)

```bash
cd sien3
python3 -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

### Установка зависимостей для расширений

В `requirements.txt` все опциональные блоки **закомментированы**. Раскомментируй нужные перед `pip install`:

| Что нужно | Раскомментировать |
|---|---|
| Гермес: Telegram-парсинг | `telethon` |
| Гермес: VK | `vk_api` |
| Гермес: ML-подбор времени | `scikit-learn`, `numpy` |
| Аполлон: субтитры | `openai-whisper` |
| Аполлон: видеомонтаж | `moviepy` |
| Вэнь: Google Calendar | `google-api-python-client`, `google-auth-*` |
| Вэнь: Яндекс.Календарь | `caldav` |
| Плутос: Тинькофф | `tinkoff-investments` |
| Плутос: Binance | `binance-connector` |
| Кун: RAG | `sentence-transformers`, `chromadb` |
| Мнемон: офлайн-перевод | `transformers`, `sentencepiece`, `torch` |
| Хуэй/Мэн: GPU | `diffusers`, `torch` (CUDA) |
| Авто: макросы | `pynput` |
| GPU мониторинг | `pynvml` |

### Установка Ollama (для Феникса)

```bash
# https://ollama.com/download
ollama pull llama3.2:3b
ollama serve
```

Без Ollama система работает в rule-based режиме.

---

## 2. Первый запуск

```bash
python init_db.py     # Создаёт data/sien.db и таблицы
python start.py       # Запускает оркестратор + 5 базовых агентов
```

Расширенный режим:

```bash
python start.py --stage4   # ядро + 14 расширенных агентов
python start.py --all       # абсолютно всё (включая GPU-агентов)
python start.py --offline   # принудительный офлайн (без интернета)
python start.py --no-gpu    # все агенты, кроме Хуэй/Мэн
```

После запуска доступны:

| Что | Адрес |
|---|---|
| HUD | http://localhost:8000/hud/ |
| Профиль | http://localhost:8000/dashboard/profile |
| Мониторинг | http://localhost:8000/dashboard/system |
| Список агентов (JSON) | http://localhost:8000/agents/list |
| Health оркестратора | http://localhost:8000/health |
| WebSocket | ws://localhost:8000/ws |

### Проверка что всё работает

```bash
curl http://localhost:8000/health
curl http://localhost:8000/agents/list
```

В ответе должен быть список из 21 агента.

---

## 3. Установка как PWA (мобильное приложение)

### На Android (Chrome/Edge)

1. Открой `http://<твой-ip>:8000/hud/` в Chrome.
2. Меню → «Установить приложение» (или «Добавить на главный экран»).
3. Иконка появится на рабочем столе. Запускается как нативное приложение.
4. Работает офлайн (статика кэшируется через service worker).

### На iOS (Safari)

1. Открой в Safari.
2. Кнопка «Поделиться» → «На экран Домой».
3. Готово.

### Push-уведомления

При первом заходе HUD спрашивает разрешение на уведомления. Соглашайся — тогда напоминания от Вэнь будут приходить даже если HUD закрыт.

---

## 4. Удалённый доступ через Cloudflare Tunnel

Чтобы подключаться к домашнему серверу с телефона, не открывая порт наружу:

### Установка cloudflared

```bash
# Linux
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# macOS
brew install cloudflare/cloudflare/cloudflared

# Windows: скачай .msi с github.com/cloudflare/cloudflared/releases
```

### Запуск туннеля

**Быстрый временный туннель** (адрес меняется при каждом запуске):

```bash
cloudflared tunnel --url http://localhost:8000
```

В консоли увидишь что-то вроде:
```
Your quick Tunnel has been created! Visit it at:
https://random-words-1234.trycloudflare.com
```

Открывай этот URL с телефона — попадёшь в HUD.

**Постоянный туннель** (нужен бесплатный аккаунт Cloudflare):

```bash
cloudflared login                              # откроется браузер
cloudflared tunnel create sien                 # создание туннеля
cloudflared tunnel route dns sien sien.your-domain.com
cloudflared tunnel run sien
```

### QR-код для телефона

Сгенерируй QR с твоим URL через [qrcode-monkey.com](https://www.qrcode-monkey.com) или прямо в Python:

```python
import qrcode
img = qrcode.make("https://sien.your-domain.com/hud/")
img.save("sien-qr.png")
```

### Альтернатива: ngrok

```bash
ngrok http 8000
```

---

## 5. Двухфакторная аутентификация (2FA)

### Установка

1. Открой `http://localhost:8000/dashboard/profile`
2. Перейди в раздел «Безопасность»
3. Нажми «Настроить 2FA»

API-вызов:
```bash
curl -X POST http://localhost:8000/dashboard/api/2fa/setup
```

Ответ содержит `qr_png_base64` — base64-картинка с QR-кодом.

4. Отсканируй QR через **Google Authenticator** / **Authy** / **Microsoft Authenticator**
5. Введи 6-значный код из приложения для подтверждения:

```bash
curl -X POST http://localhost:8000/dashboard/api/2fa/verify \
     -H 'Content-Type: application/json' \
     -d '{"code": "123456"}'
```

### Отключение

```bash
curl -X POST http://localhost:8000/dashboard/api/2fa/disable
```

---

## 6. Шифрование данных (AES-256-GCM)

Кронос и `core/encryption.py` шифруют чувствительные данные мастер-паролем.

### Запуск Кроноса с мастер-паролем

```bash
python agents/cronos.py
```

При первом запуске задаст мастер-пароль. Дальше будет запрашивать его при каждом старте.

### Что шифруется

- Секреты в `secrets.json.aes` (Telegram токены, API-ключи, пароли почты)
- Опционально — история диалогов в `sien.db` (через `encrypt_if_possible`)
- LLM-кэш (если нужно)

### Сброс мастер-пароля

**ВНИМАНИЕ:** при сбросе пароля все зашифрованные данные становятся нечитаемыми.

```bash
rm data/.salt secrets.json.aes
python agents/cronos.py
```

---

## 7. Конфигурация

### Глобальные настройки

Через плагин `plugins.core.settings.SettingsPlugin`:

```python
from plugins.core.settings import SettingsPlugin
s = SettingsPlugin()
s.set("user.city", "Moscow")
s.set("llm.model", "mistral:7b")
s.set("assistant.context_messages", 15)
```

Хранится в `data/plugins/settings/settings.json`.

### Settings.yaml для агентов

`settings.yaml` в корне — глобальный конфиг (порты, пути, режимы). Редактируй вручную.

---

## 8. Типичные ошибки и решения

### "Attribute app not found"

Это был главный баг 2.0 Beta — исправлено. Если всё-таки получаешь:
- Проверь, что запускаешь из корня проекта (`cd sien3`)
- Проверь PYTHONPATH: `export PYTHONPATH=$(pwd):$PYTHONPATH`

### "ModuleNotFoundError: No module named 'agents'"

Запускай через `start.py` или из корня:
```bash
cd sien3 && python -m uvicorn orchestrator:app --port 8000
```

### "Address already in use" (порт занят)

```bash
# Linux
sudo lsof -i :8000
kill -9 <PID>

# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

### Ollama недоступна

```bash
ollama serve
```

Без Ollama Феникс работает в rule-based режиме (медленнее, но работает).

### `cryptography` не устанавливается

```bash
pip install --upgrade pip setuptools wheel
pip install cryptography
```

На старых системах может потребоваться `apt install libssl-dev`.

### HUD не подключается к WebSocket

- Открой DevTools (F12) → Console → проверь ошибки
- Убедись, что `ws://localhost:8000/ws` доступен:
  ```bash
  curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
       -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: x" \
       http://localhost:8000/ws
  ```
- Проверь, что в `hud/main.js` `CONFIG.ws_url` указан правильно.

### "psutil not installed" в /dashboard/system

```bash
pip install psutil pynvml
```

`pynvml` опционален — нужен только для GPU stats.

### Telethon: "Could not find session"

При первом запуске Гермеса с `telegram_api_id`/`api_hash` в Кроносе Telethon попросит код из SMS — надо запустить интерактивно:

```bash
python -c "from agents.hermes import *; import asyncio; asyncio.run(fetch_trends_telegram(['тест'], ['@durov']))"
```

---

## 9. Команды HUD

### Локальные (без сервера, моментально)

В HUD набери `?` для полного списка. Основные:

- `время`, `дата`, `который час`
- `погода`, `погода Москва`, `прогноз на 5 дней`
- `курс доллара`, `курс биткоина`
- `мой ip`, `статус`, `агенты`
- `1+1`, `sqrt(144)`, `5 * 8`
- `км в мили`, `25 celsius в фаренгейт`
- `uuid`, `пароль`, `base64 текст`
- `таймер 30`, `монетка`, `кубик`

### Команды агентам (через WebSocket)

- `найди X`, `поищи X` → Аргус
- `создай задачу X`, `мои задачи` → Вэнь
- `напомни о X в 18:00` → Вэнь scheduler
- `смени сервер` → Ахилл
- `переведи hello world` → Мнемон
- `озвучь X` → Эхо

### Хоткеи

- **Enter** — отправить
- **↑ / ↓** — история команд
- **Esc** — закрыть модал
- **Ctrl+L** — очистить лог
- **Ctrl+R** — сбросить контекст диалога
- **?** в пустой строке — открыть справку

---

## 10. API-эндпоинты

### Оркестратор (порт 8000)

| Метод | URL | Описание |
|---|---|---|
| GET | `/health` | Здоровье |
| GET | `/agents/list` | Все агенты |
| WS | `/ws` | WebSocket для HUD |
| POST | `/internal/push` | Push от агентов в HUD |
| GET | `/tasks/list` | Прокси к Вэнь |
| POST | `/tasks/create` | Прокси к Вэнь |

### Дашборд (`/dashboard/...`)

| Метод | URL |
|---|---|
| GET | `/profile` |
| GET | `/system` |
| GET | `/api/profile/load` |
| POST | `/api/profile/save` |
| GET | `/api/conversations?session_id=...` |
| GET | `/api/system/stats` |
| POST | `/api/2fa/setup` |
| POST | `/api/2fa/verify` |

### Агенты (примеры)

- Wen: `/tasks/create`, `/tasks/list`, `/mail/send`, `/mail/calendar/sync`
- Ахилл: `/proxy/status`, `/proxy/switch`
- Гермес: `/affiliate/trends/fetch`, `/affiliate/ab/generate`, `/affiliate/metrics`
- Аполлон: `/video/generate`, `/video/templates`
- Мастер: `/trainer/sleep/import_csv`, `/trainer/nutrition/add`, `/trainer/workout/start`
- Плутос: `/invest/order`, `/invest/confirm/{id}`, `/invest/rebalance/preview`
- Кун: `/professor/courses/generate`, `/professor/courses/{id}/lessons/{id}`

---

## 11. Резервное копирование

Все данные хранятся в `data/`. Простой бэкап:

```bash
tar czf sien-backup-$(date +%Y%m%d).tar.gz data/
```

Восстановление:

```bash
tar xzf sien-backup-20260410.tar.gz
```

Автоматические бэкапы: см. настройки `backup` в `settings.yaml`.

---

## 12. Что дальше

- Добавить свои плагины: создай файл в `plugins/core/myplugin.py`, наследуй от `CorePlugin`.
- Кастомизировать HUD: правь `hud/style.css` (CSS-переменные в `:root`).
- Свой агент: посмотри как устроены маленькие — `dike.py`, `mnemon.py`. Скопируй структуру.
- Свои локальные команды: добавь в `local_commands.yaml`.

---

## Лицензия и контакт

Личный проект. Все агенты и плагины можешь свободно дорабатывать.
