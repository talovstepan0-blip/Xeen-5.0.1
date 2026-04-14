"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Кун' (Профессор) — RAG по документам.
Этап 4 проекта 'Сиен 01'.

Стек: sentence-transformers + ChromaDB + Ollama.
Поддерживаемые форматы: PDF, DOCX, TXT.
"""

import os, logging, hashlib, json, tempfile, re
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

logger = logging.getLogger("kun")
logging.basicConfig(level=logging.INFO, format="[КУН] %(message)s")

router = APIRouter(prefix="/professor", tags=["kun"])

DATA_DIR   = Path("data/kun")
DB_DIR     = DATA_DIR / "chroma"
DATA_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_URL   = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
CHUNK_SIZE, CHUNK_OVERLAP = 500, 100

try:
    from sentence_transformers import SentenceTransformer
    _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
    EMBEDDINGS_OK = True
    logger.info("SentenceTransformer OK")
except ImportError:
    EMBEDDINGS_OK = False; _embed_model = None
    logger.warning("sentence-transformers не установлен. Установи: pip install sentence-transformers")

try:
    import chromadb
    _chroma = chromadb.PersistentClient(path=str(DB_DIR))
    _col    = _chroma.get_or_create_collection("sien_docs")
    CHROMA_OK = True
    logger.info("ChromaDB OK")
except ImportError:
    CHROMA_OK = False; _chroma = None; _col = None
    logger.warning("chromadb не установлен. Установи: pip install chromadb")

# ── Парсеры ───────────────────────────────────────────────────────────────────

def extract_text(path: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
        except ImportError:
            raise HTTPException(400, "Установи: pip install pypdf")
    elif ext in (".docx", ".doc"):
        try:
            from docx import Document
            return "\n".join(p.text for p in Document(path).paragraphs)
        except ImportError:
            raise HTTPException(400, "Установи: pip install python-docx")
    else:
        return open(path, encoding="utf-8", errors="ignore").read()

def split_chunks(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        c = text[start:end].strip()
        if len(c) > 50:
            chunks.append(c)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks

def embed(texts: list[str]) -> list[list[float]]:
    if EMBEDDINGS_OK:
        return _embed_model.encode(texts, show_progress_bar=False).tolist()
    import random; return [[random.random() for _ in range(384)] for _ in texts]

async def llm(prompt: str, system: str = "") -> str:
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_URL}/api/generate",
                             json={"model": OLLAMA_MODEL, "prompt": full, "stream": False})
            return r.json().get("response", "").strip()
    except Exception as e:
        return f"[Ollama недоступна: {e}]"

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    """Загрузить документ в базу знаний. Поддерживает: PDF, DOCX, TXT."""
    if not CHROMA_OK or not EMBEDDINGS_OK:
        raise HTTPException(503, "Требуется: pip install chromadb sentence-transformers")
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read()); tmp_path = tmp.name
    try:
        text = extract_text(tmp_path, file.filename)
    finally:
        os.unlink(tmp_path)
    if not text.strip():
        raise HTTPException(400, "Документ пустой")
    chunks  = split_chunks(text)
    doc_id  = hashlib.md5(file.filename.encode()).hexdigest()[:8]
    ids     = [f"{doc_id}_{i}" for i in range(len(chunks))]
    _col.upsert(ids=ids, embeddings=embed(chunks), documents=chunks,
                metadatas=[{"source": file.filename, "chunk": i} for i in range(len(chunks))])
    logger.info(f"Загружен '{file.filename}': {len(chunks)} чанков")
    return {"status": "indexed", "file": file.filename, "chunks": len(chunks)}

class AskReq(BaseModel):
    question: str
    top_k: int = 4

@router.post("/ask")
async def ask(req: AskReq):
    """RAG-вопрос по документам."""
    if not CHROMA_OK or not EMBEDDINGS_OK:
        raise HTTPException(503, "Требуется chromadb и sentence-transformers")
    results = _col.query(query_embeddings=embed([req.question]), n_results=req.top_k)
    docs    = results.get("documents", [[]])[0]
    metas   = results.get("metadatas", [[]])[0]
    if not docs:
        return {"answer": "База знаний пуста. Загрузи документы через /professor/upload", "sources": []}
    context = "\n\n".join(f"[{m['source']} §{m['chunk']}]\n{d}" for d, m in zip(docs, metas))
    answer  = await llm(
        f"Контекст:\n{context}\n\nВопрос: {req.question}\n\nОтвет:",
        "Отвечай строго по контексту. Цитируй источники. Если ответа нет — скажи об этом."
    )
    return {"answer": answer, "sources": [{"source": m["source"], "chunk": m["chunk"]} for m in metas]}

class ExamReq(BaseModel):
    topic: Optional[str] = None
    num_questions: int = 3

@router.post("/exam/generate")
async def exam_generate(req: ExamReq):
    """Сгенерировать экзаменационные вопросы."""
    if not CHROMA_OK:
        raise HTTPException(503, "Требуется chromadb")
    docs = _col.get(limit=20).get("documents", [])
    if not docs:
        raise HTTPException(400, "База знаний пуста")
    sample = "\n\n".join(docs[:5])
    topic  = f" по теме «{req.topic}»" if req.topic else ""
    raw    = await llm(
        f"По материалам{topic} составь {req.num_questions} вопроса.\n"
        f"Формат: JSON [{{'question':'...','answer':'...'}}]\n\nМатериалы:\n{sample}"
    )
    try:
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        questions = json.loads(m.group()) if m else []
    except Exception:
        questions = [{"question": raw, "answer": "—"}]
    return {"questions": questions}

class CheckReq(BaseModel):
    question: str; user_answer: str; correct_answer: str

@router.post("/exam/check")
async def exam_check(req: CheckReq):
    """Проверить ответ студента (0-10)."""
    raw = await llm(
        f"Вопрос: {req.question}\nПравильный ответ: {req.correct_answer}\n"
        f"Ответ студента: {req.user_answer}\n\n"
        "Оцени ответ 0-10. JSON: {\"score\": N, \"feedback\": \"...\"}"
    )
    try:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group()) if m else {"score": 0, "feedback": raw}
    except Exception:
        return {"score": 0, "feedback": raw}

@router.get("/documents")
def list_docs():
    if not CHROMA_OK:
        return {"documents": []}
    metas = _col.get().get("metadatas", [])
    sources = {}
    for m in metas:
        src = m.get("source", "?")
        sources[src] = sources.get(src, 0) + 1
    return {"documents": [{"file": k, "chunks": v} for k, v in sources.items()]}

@router.get("/health")
def health():
    return {"agent": "Кун", "alive": True, "embeddings": EMBEDDINGS_OK, "chroma": CHROMA_OK}

app = FastAPI(title="Кун — Профессор")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8007)

# ── Сиен 3.0.0: подключаем расширения ──
try:
    from agents.kun_ext import setup_kun_ext
    setup_kun_ext(app)
except Exception as _ext_err:
    import logging as _l
    _l.getLogger('kun').warning(f'Расширения недоступны: {_ext_err}')
