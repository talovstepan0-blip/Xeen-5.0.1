"""
Агент 'Мэн' (Видео длинные) — генерация длинного видео (HunyuanVideo/CogVideoX).
Этап 4 проекта 'Сиен 01'.

Требует: NVIDIA A100/H100 с ≥40GB VRAM или облачный GPU.
В текущей версии — полноценная заглушка с инструкцией по развёртыванию.
"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

import os, logging, sqlite3, time
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("meng")
logging.basicConfig(level=logging.INFO, format="[МЭН] %(message)s")

router  = APIRouter(prefix="/longvideo", tags=["meng"])
DB_PATH = str(_DATA_DIR / "meng.db")

Path("data").mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt      TEXT NOT NULL,
            model       TEXT,
            duration_s  INTEGER,
            status      TEXT DEFAULT 'queued',
            result_path TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit(); conn.close()

init_db()

def check_gpu_vram() -> dict:
    try:
        import torch
        if torch.cuda.is_available():
            vram = torch.cuda.get_device_properties(0).total_memory / 1e9
            return {"cuda": True, "vram_gb": round(vram, 1), "sufficient_for_hunyuan": vram >= 40}
        return {"cuda": False, "vram_gb": 0, "sufficient_for_hunyuan": False}
    except ImportError:
        return {"cuda": False, "vram_gb": 0, "sufficient_for_hunyuan": False}

class VideoReq(BaseModel):
    prompt: str
    duration_seconds: int = 5       # HunyuanVideo поддерживает до ~13 секунд
    model: str = "hunyuan"          # hunyuan | cogvideox | wan
    resolution: str = "720p"
    fps: int = 24

@router.post("/generate")
async def generate(req: VideoReq):
    """Сгенерировать длинное видео. Требует мощный GPU."""
    gpu = check_gpu_vram()

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute(
        "INSERT INTO jobs (prompt, model, duration_s, status) VALUES (?,?,?,?)",
        (req.prompt, req.model, req.duration_seconds, "gpu_required")
    )
    job_id = cur.lastrowid
    conn.commit(); conn.close()

    if not gpu["sufficient_for_hunyuan"]:
        return {
            "status":    "gpu_required",
            "job_id":    job_id,
            "gpu_info":  gpu,
            "models": {
                "hunyuan": {
                    "repo": "https://github.com/Tencent/HunyuanVideo",
                    "vram": "≥40GB (A100/H100)",
                    "install": [
                        "git clone https://github.com/Tencent/HunyuanVideo",
                        "pip install -r requirements.txt",
                        "python sample_video.py --prompt 'your prompt'"
                    ]
                },
                "cogvideox": {
                    "repo": "https://github.com/THUDM/CogVideo",
                    "vram": "≥24GB",
                    "install": [
                        "pip install diffusers transformers accelerate",
                        "from diffusers import CogVideoXPipeline"
                    ]
                },
                "wan": {
                    "repo": "https://github.com/Wan-Video/Wan2.1",
                    "vram": "≥16GB",
                    "note": "Более доступный вариант"
                }
            },
            "cloud_options": [
                "RunPod: https://runpod.io (A100 ~$1.6/час)",
                "Vast.ai: https://vast.ai (дешевле)",
                "Google Colab Pro+: https://colab.research.google.com",
                "Modal: https://modal.com",
            ],
            "prompt":    req.prompt,
            "message": (
                f"Генерация видео через {req.model} требует GPU с ≥40GB VRAM. "
                f"Текущий GPU: {gpu.get('vram_gb', 0):.1f}GB. "
                "Используй облачный GPU или запусти на соответствующем железе."
            )
        }

    # Если GPU достаточно — можно интегрировать реальный пайплайн
    # (раскомментируй нужный блок)

    # == HunyuanVideo ==
    # from hyvideo.utils.file_utils import save_videos_grid
    # from hyvideo.config import parse_args
    # from hyvideo.inference import HunyuanVideoSampler
    # sampler = HunyuanVideoSampler.from_pretrained("ckpts", args=args)
    # outputs = sampler.predict(prompt=req.prompt, height=720, width=1280, ...)
    # save_videos_grid(outputs['samples'], f"output/video_{job_id}.mp4")

    # == CogVideoX ==
    # from diffusers import CogVideoXPipeline
    # import torch
    # pipe = CogVideoXPipeline.from_pretrained("THUDM/CogVideoX-5b", torch_dtype=torch.bfloat16).to("cuda")
    # video = pipe(prompt=req.prompt, num_frames=req.fps * req.duration_seconds).frames[0]

    return {"status": "not_implemented", "job_id": job_id, "message": "Интегрируй реальный пайплайн выше"}

@router.get("/jobs")
def list_jobs():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()
    return {"jobs": [dict(r) for r in rows]}

@router.get("/gpu")
def gpu_info():
    return check_gpu_vram()

@router.get("/health")
def health():
    gpu = check_gpu_vram()
    return {"agent": "Мэн", "alive": True, "gpu_sufficient": gpu["sufficient_for_hunyuan"]}

app = FastAPI(title="Мэн — Длинное видео")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8015)
