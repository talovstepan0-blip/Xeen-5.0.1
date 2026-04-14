"""
start.py — Сиен 3.0.0. Точка входа.

Структура проекта:
  agents/     — все 21 агент
  core/       — ядро (llm_cache, emotion, encryption, graceful_shutdown, local_commands)
  web/        — дашборд (web/dashboard.py → /dashboard/*)
  hud/        — HUD (index.html, main.js, style.css)
  templates/  — шаблоны (profile.html и др.)
  plugins/    — плагины core + productivity
  data/       — SQLite базы, кэш, соль
  logs/       — лог-файлы
  backups/    — резервные копии

Режимы:
  python start.py                # ядро: 5 агентов + dashboard (через оркестратор)
  python start.py --all          # все 21 агент
  python start.py --stage4       # ядро + расширенные
  python start.py --offline      # принудительный офлайн
  python start.py --no-gpu       # без GPU-агентов (Хуэй, Мэн)
  python start.py --no-cronos    # без Кроноса

Остановка: Ctrl+C — графически останавливает все процессы.
"""
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ["PYTHONPATH"] = ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")
for d in ("data", "logs", "backups"):
    Path(ROOT, d).mkdir(exist_ok=True)

PY = sys.executable


# ─── Сервисы (ВСЕ агенты теперь имеют app = FastAPI()) ───────────

CORE = [
    ("Orchestrator", [PY, "-m", "uvicorn", "orchestrator:app",
                      "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]),
    ("Wen",    [PY, "-m", "uvicorn", "agents.wen:app",    "--port", "8006", "--log-level", "warning"]),
    ("Ahill",  [PY, "-m", "uvicorn", "agents.ahill:app",  "--port", "8003", "--log-level", "warning"]),
    ("Fenix",  [PY, "-m", "uvicorn", "agents.fenix:app",  "--port", "8004", "--log-level", "warning"]),
    ("Logos",  [PY, "-m", "uvicorn", "agents.logos:app",  "--port", "8005", "--log-level", "warning"]),
]

STAGE4 = [
    ("Kun",    [PY, "-m", "uvicorn", "agents.kun:app",    "--port", "8007", "--log-level", "warning"]),
    ("Master", [PY, "-m", "uvicorn", "agents.master:app", "--port", "8008", "--log-level", "warning"]),
    ("Plutos", [PY, "-m", "uvicorn", "agents.plutos:app", "--port", "8009", "--log-level", "warning"]),
    ("Musa",   [PY, "-m", "uvicorn", "agents.musa:app",   "--port", "8010", "--log-level", "warning"]),
    ("Kallio", [PY, "-m", "uvicorn", "agents.kallio:app", "--port", "8011", "--log-level", "warning"]),
    ("Hefest", [PY, "-m", "uvicorn", "agents.hefest:app", "--port", "8012", "--log-level", "warning"]),
    ("Avto",   [PY, "-m", "uvicorn", "agents.avto:app",   "--port", "8013", "--log-level", "warning"]),
    ("Eho",    [PY, "-m", "uvicorn", "agents.eho:app",    "--port", "8016", "--log-level", "warning"]),
    ("Irida",  [PY, "-m", "uvicorn", "agents.irida:app",  "--port", "8017", "--log-level", "warning"]),
    ("Apollo", [PY, "-m", "uvicorn", "agents.apollo:app", "--port", "8018", "--log-level", "warning"]),
    ("Hermes", [PY, "-m", "uvicorn", "agents.hermes:app", "--port", "8019", "--log-level", "warning"]),
    ("Dike",   [PY, "-m", "uvicorn", "agents.dike:app",   "--port", "8020", "--log-level", "warning"]),
    ("Mnemon", [PY, "-m", "uvicorn", "agents.mnemon:app", "--port", "8021", "--log-level", "warning"]),
    ("Argus",  [PY, "-m", "uvicorn", "agents.argus:app",  "--port", "8022", "--log-level", "warning"]),
]

GPU_AGENTS = [
    ("Huei", [PY, "-m", "uvicorn", "agents.huei:app", "--port", "8014", "--log-level", "warning"]),
    ("Meng", [PY, "-m", "uvicorn", "agents.meng:app", "--port", "8015", "--log-level", "warning"]),
]

processes: list = []


def _stream(proc, name: str):
    try:
        for line in iter(proc.stdout.readline, ""):
            if line.strip():
                print(f"  [{name}] {line.rstrip()}", flush=True)
    except (ValueError, OSError):
        pass
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass


def start_svc(name: str, cmd: list) -> subprocess.Popen:
    port = next((c for c in reversed(cmd) if c.isdigit()), "")
    print(f"  [+] {name:<14} {'::' + port if port else ''}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
        cwd=ROOT,
        env=os.environ,
    )
    threading.Thread(target=_stream, args=(proc, name), daemon=True).start()
    time.sleep(0.8)
    return proc


def shutdown_all(signum=None, frame=None):
    print("\n[!] Graceful shutdown: SIGTERM всем процессам...")
    # Сначала мягко
    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass
    # Даём 5 секунд
    for _ in range(25):
        alive = [p for p in processes if p.poll() is None]
        if not alive:
            break
        time.sleep(0.2)
    # Жёстко тех, кто не вышел
    for p in processes:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
    print("[!] Остановлено.")
    sys.exit(0)


# ─── Предстартовые проверки ──────────────────────────────────────

def run_migrations():
    print("  [DB] Инициализация...", end=" ", flush=True)
    try:
        r = subprocess.run([PY, "init_db.py"], capture_output=True,
                           text=True, cwd=ROOT, timeout=15)
        print("✓" if r.returncode == 0 else f"⚠ {r.stderr[:100]}")
    except Exception as e:
        print(f"⚠ {e}")


def check_internet() -> bool:
    import socket
    for host in ["8.8.8.8", "1.1.1.1"]:
        try:
            socket.setdefaulttimeout(2)
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, 53))
            return True
        except Exception:
            continue
    return False


def check_ollama() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


# ─── Main ────────────────────────────────────────────────────────

def main():
    args = set(sys.argv[1:])

    print("=" * 60)
    print("   СИЕН 3.0.0  ·  Мультиагентная система")
    print("=" * 60)

    print("\n  [Предстарт]")
    run_migrations()

    force_offline = "--offline" in args
    if force_offline:
        print("  [NET] ⚠ Офлайн-режим (--offline)")
        online = False
    else:
        print("  [NET] Проверка интернета...", end=" ", flush=True)
        online = check_internet()
        print("✓ ОНЛАЙН" if online else "✗ ОФЛАЙН")

    Path(ROOT, "data", "offline_mode.flag").write_text("1" if not online else "0")

    print("  [LLM] Ollama...", end=" ", flush=True)
    print("✓ localhost:11434" if check_ollama() else "✗ не запущена")

    if "--no-cronos" not in args:
        secrets_file = Path(ROOT, "secrets.json.aes")
        if secrets_file.exists():
            print("  [🔐] Кронос: запусти →  python agents/cronos.py")
        else:
            print("  [🔐] Кронос: первый запуск → python agents/cronos.py")

    # Выбор набора
    if "--all" in args:
        services = CORE + STAGE4 + ([] if "--no-gpu" in args else GPU_AGENTS)
    elif "--stage4" in args:
        services = CORE + STAGE4
    else:
        services = CORE

    print(f"\n  Запуск {len(services)} сервисов...\n")
    for name, cmd in services:
        processes.append(start_svc(name, cmd))

    print()
    print("=" * 60)
    print(f"  ✓ Запущено       : {len(processes)}")
    print(f"  ✓ Режим          : {'ОФЛАЙН' if not online else 'ОНЛАЙН'}")
    print()
    print("  Интерфейсы:")
    print("    HUD            : http://localhost:8000/hud/  (или откройте hud/index.html)")
    print("    Дашборд        : http://localhost:8000/dashboard/profile")
    print("    Мониторинг     : http://localhost:8000/dashboard/system")
    print("    Оркестратор WS : ws://localhost:8000/ws")
    print("    API            : http://localhost:8000/agents/list")
    print()
    print("  Кронос (отдельный терминал):")
    print("    python agents/cronos.py")
    print()
    print("  Ctrl+C — остановить всё")
    print("=" * 60)

    # Ловим SIGTERM и SIGINT
    signal.signal(signal.SIGINT, shutdown_all)
    try:
        signal.signal(signal.SIGTERM, shutdown_all)
    except (AttributeError, ValueError):
        pass  # Windows

    try:
        while True:
            time.sleep(2)
            if not any(p.poll() is None for p in processes):
                print("[!] Все процессы завершились.")
                break
    except KeyboardInterrupt:
        shutdown_all()


if __name__ == "__main__":
    main()
