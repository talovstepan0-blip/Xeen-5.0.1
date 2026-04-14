"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Эхо' (Озвучка) — пакетная TTS (Text-to-Speech).
Этап 4 проекта 'Сиен 01'.

Бэкенды (в порядке приоритета):
  1. Piper TTS — высокое качество, оффлайн, русский голос
  2. pyttsx3 — базовый оффлайн TTS
  3. gTTS — Google TTS (требует интернет)
  4. Заглушка

Установи: pip install pyttsx3 gTTS
Piper: https://github.com/rhasspy/piper (скачай модель ru_RU-ruslan-medium)
"""

import os, logging, tempfile, subprocess, io, re
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("eho")
logging.basicConfig(level=logging.INFO, format="[ЭХО] %(message)s")

router  = APIRouter(prefix="/tts", tags=["eho"])
OUT_DIR = Path("data/eho/output")
OUT_DIR.mkdir(parents=True, exist_ok=True)

PIPER_BINARY = os.environ.get("PIPER_BIN", "piper")
PIPER_MODEL  = os.environ.get("PIPER_MODEL", "ru_RU-ruslan-medium.onnx")
PIPER_CONFIG = os.environ.get("PIPER_CONFIG", "ru_RU-ruslan-medium.onnx.json")
TTS_BACKEND  = os.environ.get("TTS_BACKEND", "auto")  # auto | piper | pyttsx3 | gtts | stub

# ── Проверка бэкендов ─────────────────────────────────────────────────────────

def _check_piper() -> bool:
    try:
        result = subprocess.run([PIPER_BINARY, "--version"],
                                capture_output=True, timeout=3)
        return result.returncode == 0 and Path(PIPER_MODEL).exists()
    except Exception:
        return False

def _check_pyttsx3() -> bool:
    try:
        import pyttsx3; pyttsx3.init(); return True
    except Exception:
        return False

def _check_gtts() -> bool:
    try:
        from gtts import gTTS; return True
    except ImportError:
        return False

# ── Генерация аудио ───────────────────────────────────────────────────────────

def tts_piper(text: str, out_path: str) -> bool:
    """
    Piper TTS — лучшее качество для русского.
    Установка:
        # Скачай piper: https://github.com/rhasspy/piper/releases
        # Скачай модель:
        wget https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx
        wget https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json
    Запуск:
        echo "Текст" | piper --model ru_RU-ruslan-medium.onnx --output_file output.wav
    """
    try:
        proc = subprocess.run(
            [PIPER_BINARY, "--model", PIPER_MODEL, "--config", PIPER_CONFIG,
             "--output_file", out_path],
            input=text.encode("utf-8"),
            capture_output=True, timeout=30
        )
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"Piper ошибка: {e}")
        return False

def tts_pyttsx3(text: str, out_path: str, rate: int = 150) -> bool:
    """pyttsx3 — базовый оффлайн TTS."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", rate)
        # Пытаемся установить русский голос
        voices = engine.getProperty("voices")
        for v in voices:
            if "russian" in v.name.lower() or "ru" in v.id.lower():
                engine.setProperty("voice", v.id)
                break
        engine.save_to_file(text, out_path)
        engine.runAndWait()
        return Path(out_path).exists()
    except Exception as e:
        logger.error(f"pyttsx3 ошибка: {e}")
        return False

def tts_gtts(text: str, out_path: str, lang: str = "ru") -> bool:
    """gTTS — Google TTS (требует интернет)."""
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang=lang, slow=False)
        tts.save(out_path)
        return True
    except Exception as e:
        logger.error(f"gTTS ошибка: {e}")
        return False

def split_sentences(text: str, max_len: int = 200) -> list[str]:
    """Разбить текст на предложения для пакетной обработки."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    result, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_len:
            current += (" " if current else "") + s
        else:
            if current:
                result.append(current)
            current = s
    if current:
        result.append(current)
    return result or [text]

def choose_backend() -> str:
    if TTS_BACKEND != "auto":
        return TTS_BACKEND
    if _check_piper():   return "piper"
    if _check_pyttsx3(): return "pyttsx3"
    if _check_gtts():    return "gtts"
    return "stub"

def synthesize_chunk(text: str, out_path: str, backend: str, lang: str) -> bool:
    if backend == "piper":
        return tts_piper(text, out_path)
    elif backend == "pyttsx3":
        return tts_pyttsx3(text, out_path)
    elif backend == "gtts":
        return tts_gtts(text, out_path, lang)
    else:
        # Заглушка: пустой WAV файл
        Path(out_path).write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
        logger.warning(f"TTS заглушка для: '{text[:40]}'")
        return True

def merge_wav_files(paths: list[str], out_path: str) -> bool:
    """Склеить WAV файлы через pydub или простой конкат."""
    try:
        from pydub import AudioSegment
        combined = sum(AudioSegment.from_wav(p) for p in paths)
        combined.export(out_path, format="wav")
        return True
    except ImportError:
        pass
    # Fallback: простая конкатенация (только если формат одинаковый)
    try:
        import wave, array
        data = []
        params = None
        for p in paths:
            with wave.open(p, "rb") as w:
                if params is None:
                    params = w.getparams()
                data.append(w.readframes(w.getnframes()))
        with wave.open(out_path, "wb") as out:
            out.setparams(params)
            for d in data:
                out.writeframes(d)
        return True
    except Exception as e:
        logger.error(f"Merge error: {e}")
        return False

# ── Модели ────────────────────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str
    lang: str = "ru"
    backend: Optional[str] = None    # None = auto
    split_sentences: bool = True
    voice: Optional[str] = None      # для piper: имя модели

class BatchTTSRequest(BaseModel):
    texts: list[str]
    lang: str = "ru"
    backend: Optional[str] = None

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/generate")
def generate_tts(req: TTSRequest):
    """Синтезировать речь из текста."""
    if len(req.text) > 10000:
        raise HTTPException(400, "Текст слишком длинный (макс. 10000 символов)")

    backend   = req.backend or choose_backend()
    timestamp = int(__import__("time").time())

    if req.split_sentences and len(req.text) > 200:
        # Пакетный режим: разбиваем на предложения и склеиваем
        sentences  = split_sentences(req.text)
        chunk_paths = []
        for i, sentence in enumerate(sentences):
            chunk_out = str(OUT_DIR / f"chunk_{timestamp}_{i}.wav")
            ok = synthesize_chunk(sentence, chunk_out, backend, req.lang)
            if ok:
                chunk_paths.append(chunk_out)

        out_path = str(OUT_DIR / f"tts_{timestamp}.wav")
        if len(chunk_paths) == 1:
            Path(chunk_paths[0]).rename(out_path)
        elif chunk_paths:
            merge_wav_files(chunk_paths, out_path)
            for p in chunk_paths:
                try: Path(p).unlink()
                except: pass
        else:
            raise HTTPException(500, "TTS генерация не удалась")
    else:
        out_path = str(OUT_DIR / f"tts_{timestamp}.wav")
        ok = synthesize_chunk(req.text, out_path, backend, req.lang)
        if not ok:
            raise HTTPException(500, f"TTS ({backend}) не смог сгенерировать аудио")

    filename = Path(out_path).name
    logger.info(f"TTS ({backend}): '{req.text[:50]}' → {filename}")

    return {
        "status": "generated",
        "file": filename,
        "backend": backend,
        "url": f"/tts/audio/{filename}",
        "chars": len(req.text),
    }

@router.post("/batch")
def batch_tts(req: BatchTTSRequest):
    """Пакетный синтез нескольких текстов."""
    if len(req.texts) > 20:
        raise HTTPException(400, "Максимум 20 текстов за раз")
    backend   = req.backend or choose_backend()
    timestamp = int(__import__("time").time())
    results   = []

    for i, text in enumerate(req.texts):
        out_path = str(OUT_DIR / f"batch_{timestamp}_{i}.wav")
        ok = synthesize_chunk(text[:500], out_path, backend, req.lang)
        results.append({
            "index": i, "text": text[:50],
            "file": Path(out_path).name if ok else None,
            "url": f"/tts/audio/{Path(out_path).name}" if ok else None,
            "success": ok,
        })

    return {"backend": backend, "results": results, "count": len(results)}

@router.get("/audio/{filename}")
def get_audio(filename: str):
    """Скачать сгенерированный аудиофайл."""
    path = OUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(path, media_type="audio/wav", filename=filename)

@router.get("/backends")
def backends():
    """Доступность TTS бэкендов."""
    return {
        "piper":    _check_piper(),
        "pyttsx3":  _check_pyttsx3(),
        "gtts":     _check_gtts(),
        "selected": choose_backend(),
        "install": {
            "piper":   "Скачай с https://github.com/rhasspy/piper/releases + русскую модель",
            "pyttsx3": "pip install pyttsx3",
            "gtts":    "pip install gTTS (требует интернет)",
            "pydub":   "pip install pydub (для склейки файлов)",
        }
    }

@router.get("/health")
def health():
    return {"agent": "Эхо", "alive": True, "backend": choose_backend()}

app = FastAPI(title="Эхо — Озвучка")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8016)
