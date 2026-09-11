import os
import time
import asyncio
import re
import shutil
import sqlite3
import mimetypes
import subprocess
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from PIL import Image, UnidentifiedImageError

try:
    import pillow_heif
except ImportError:  # pragma: no cover
    pillow_heif = None

from dartlogic import dartgame
from sensors import get_window_states

app = FastAPI()
templates = Jinja2Templates(directory="templates")

game = dartgame()

# Ordner für temporäre Dateien erstellen
UPLOAD_DIR = "uploads"
FILES_DIR = Path("files")
THUMBNAILS_DIR = Path("thumbnails")
UPLOAD_DIR = Path(UPLOAD_DIR)
FILES_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CLOUD_DB = Path("cloud.db")

# Speicher für Datei-Metadaten
uploaded_files = {}

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "heif"}
PDF_EXTENSIONS = {"pdf"}
TEXT_EXTENSIONS = {"txt", "md", "json", "csv", "log", "yaml", "yml", "xml", "ini", "cfg", "toml", "sql"}
CODE_EXTENSIONS = {"py", "js", "ts", "tsx", "jsx", "java", "c", "cc", "cpp", "h", "hpp", "cs", "go", "rs", "php", "swift", "kt", "rb", "sh", "bash", "html", "css", "scss", "vue", "tsx", "jsx"}
AUDIO_EXTENSIONS = {"mp3", "wav", "ogg", "m4a", "aac", "flac", "opus"}
VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}
OFFICE_EXTENSIONS = {"docx", "odt", "doc", "xls", "xlsx", "ppt", "pptx"}

BADGE_MAP = {
    "pdf": "PDF",
    "txt": "TXT",
    "md": "MD",
    "json": "JSON",
    "csv": "CSV",
    "py": "PY",
    "js": "JS",
    "ts": "TS",
    "tsx": "TSX",
    "jsx": "JSX",
    "html": "HTML",
    "css": "CSS",
    "mp3": "MP3",
    "wav": "WAV",
    "ogg": "OGG",
    "m4a": "M4A",
    "aac": "AAC",
    "flac": "FLAC",
    "mp4": "MP4",
    "mov": "MOV",
    "avi": "AVI",
    "mkv": "MKV",
    "webm": "WEBM",
    "jpg": "JPG",
    "jpeg": "JPG",
    "png": "PNG",
    "gif": "GIF",
    "webp": "WEBP",
    "heic": "HEIC",
    "heif": "HEIF",
    "docx": "DOCX",
    "odt": "ODT",
    "doc": "DOC",
    "xls": "XLS",
    "xlsx": "XLSX",
    "ppt": "PPT",
    "pptx": "PPTX",
}


def sanitize_filename(name: str) -> str:
    """Protects a user-supplied file name against unsafe path segments."""
    cleaned = Path((name or "unnamed").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß._ ()-]", "_", cleaned).strip(" .")
    return cleaned or "unnamed"


def extension_for(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def categorize_file(filename: str) -> str:
    ext = extension_for(filename)
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in OFFICE_EXTENSIONS:
        return "office"
    return "other"


def preview_kind_for_file(filename: str) -> str:
    category = categorize_file(filename)
    if category == "image":
        return "image"
    if category in {"pdf", "office"}:
        return "pdf"
    if category in {"text", "code"}:
        return "text"
    if category == "audio":
        return "audio"
    if category == "video":
        return "video"
    return "download"


def badge_for_extension(filename: str) -> str:
    ext = extension_for(filename)
    return BADGE_MAP.get(ext, ext.upper()[:4] if ext else "FILE")


def file_mime_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    category = categorize_file(filename)
    if category == "image":
        return "image/jpeg"
    if category == "pdf":
        return "application/pdf"
    if category == "audio":
        return "audio/mpeg"
    if category == "video":
        return "video/mp4"
    if category in {"text", "code"}:
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def thumbnail_path_for(filename: str) -> Path:
    raw_name = sanitize_filename(filename)
    stem = Path(raw_name).stem
    return THUMBNAILS_DIR / f"{stem}.jpg"


def generate_thumbnail_for_image(source_path: Path, target_path: Path) -> bool:
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if pillow_heif is not None and source_path.suffix.lower() in {".heic", ".heif"}:
            pillow_heif.register_heif_opener()
        with Image.open(source_path) as image:
            image = image.convert("RGB")
            image.thumbnail((300, 300))
            image.save(target_path, format="JPEG", quality=82, optimize=True)
        return True
    except (UnidentifiedImageError, OSError, ValueError):
        return False


def convert_office_to_pdf(source_path: Path, output_dir: Path) -> bool:
    if source_path.suffix.lower() not in {".docx", ".odt", ".doc"}:
        return False
    if not shutil.which("soffice"):
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        "soffice",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(source_path),
    ], capture_output=True, text=True)
    if result.returncode != 0:
        return False
    pdf_name = source_path.with_suffix(".pdf").name
    converted_path = output_dir / pdf_name
    if converted_path.exists():
        return True
    return False


def get_file_entry(path: Path) -> dict:
    stat = path.stat()
    filename = path.name
    category = categorize_file(filename)
    badge = badge_for_extension(filename)
    has_thumbnail = False
    thumbnail_url = None
    if category == "image":
        thumb_path = thumbnail_path_for(filename)
        if thumb_path.exists():
            has_thumbnail = True
            thumbnail_url = f"/thumbnail/{quote(filename)}"
    elif category == "office":
        thumb_path = thumbnail_path_for(filename)
        if thumb_path.exists():
            has_thumbnail = True
            thumbnail_url = f"/thumbnail/{quote(filename)}"
    return {
        "name": filename,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "category": category,
        "badge": badge,
        "mime_type": file_mime_type(filename),
        "has_thumbnail": has_thumbnail,
        "thumbnail_url": thumbnail_url,
        "can_preview_as_pdf": category in {"pdf", "office"},
        "can_preview_as_text": category in {"text", "code"},
        "can_play_audio": category == "audio",
        "can_play_video": category == "video",
        "can_view_image": category == "image",
        "is_supported": category in {"image", "pdf", "text", "code", "audio", "video", "office"},
    }


def list_files_response() -> dict:
    items = []
    for file_path in sorted(FILES_DIR.iterdir(), key=lambda item: item.name.lower()):
        if file_path.is_file():
            items.append(get_file_entry(file_path))
    return {"files": items, "count": len(items), "directory": str(FILES_DIR)}


async def generate_thumbnail_for_uploaded_file(source_path: Path) -> None:
    category = categorize_file(source_path.name)
    if category != "image":
        return
    thumbnail_target = thumbnail_path_for(source_path.name)
    if thumbnail_target.exists():
        thumbnail_target.unlink()
    generate_thumbnail_for_image(source_path, thumbnail_target)


def resolve_storage_path(filename: str) -> Path:
    safe_name = sanitize_filename(filename)
    return FILES_DIR / safe_name


def read_text_file(file_path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="replace")


async def stream_file_range(request: Request, file_path: Path, media_type: str):
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
    if not range_header or not range_header.startswith("bytes="):
        return StreamingResponse(file_path.open("rb"), media_type=media_type, headers={"Accept-Ranges": "bytes"})

    try:
        range_value = range_header.split("=", 1)[1]
        start_str, end_str = range_value.split("-", 1)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        if start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        end = min(end, file_size - 1)
        length = end - start + 1
        def content_iterator():
            with file_path.open("rb") as target:
                target.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = target.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        return StreamingResponse(
            content_iterator(),
            status_code=206,
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(length),
            },
        )
    except ValueError:
        return StreamingResponse(file_path.open("rb"), media_type=media_type, headers={"Accept-Ranges": "bytes"})

class StartGameData(BaseModel):
    players: list[str]
    legs_to_win: int = 1
    mode: str = "501"

# --- HELPER / BACKGROUND TASKS ---

async def delete_file_after_delay(filename: str, delay: int = 300):
    """Löscht die Datei nach Ablauf von delay (Standard: 300 Sek / 5 Min)"""
    await asyncio.sleep(delay)
    file_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    if filename in uploaded_files:
        del uploaded_files[filename]

def get_active_files():
    """Gibt Dateien mit verbleibender Sekundenzahl zurück"""
    now = time.time()
    active = []
    for filename, data in list(uploaded_files.items()):
        remaining = int(data["expires_at"] - now)
        if remaining > 0:
            active.append({
                "filename": filename,
                "size_mb": round(data["size"] / (1024 * 1024), 2),
                "remaining_seconds": remaining
            })
    return active

def safe_cloud_name(name: str, fallback: str = "Unbenannt") -> str:
    """Bereinigt Namen, bevor sie in der Cloud-Datenbank gespeichert werden."""
    clean_name = Path((name or fallback).replace("\\", "/")).name
    clean_name = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß._ ()-]", "_", clean_name).strip(" .")
    return clean_name or fallback

def cloud_db():
    database = sqlite3.connect(CLOUD_DB)
    database.execute("PRAGMA foreign_keys = ON")
    database.row_factory = sqlite3.Row
    return database

def init_cloud_db():
    with cloud_db() as database:
        database.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS cloud_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER REFERENCES cloud_folders(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                UNIQUE(parent_id, name)
            );
            CREATE TABLE IF NOT EXISTS cloud_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER NOT NULL REFERENCES cloud_folders(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                size INTEGER NOT NULL,
                modified_at REAL NOT NULL,
                UNIQUE(folder_id, name)
            );
            CREATE TABLE IF NOT EXISTS cloud_file_chunks (
                file_id INTEGER NOT NULL REFERENCES cloud_files(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content BLOB NOT NULL,
                PRIMARY KEY(file_id, chunk_index)
            );
        """)
        database.execute(
            "INSERT OR IGNORE INTO cloud_folders(id, parent_id, name) VALUES (1, NULL, 'Meine Dateien')"
        )

init_cloud_db()

# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/dart", response_class=HTMLResponse)
async def get_dart_lobby(request: Request):
    players = game.refresh_players()
    return templates.TemplateResponse("index.html", {"request": request, "players": players})

@app.get("/dart/game", response_class=HTMLResponse)
async def get_game_page(request: Request):
    return templates.TemplateResponse("game.html", {"request": request})

@app.get("/dart/cricket", response_class=HTMLResponse)
async def get_cricket_page(request: Request):
    return templates.TemplateResponse("cricket.html", {"request": request})

@app.get("/windows", response_class=HTMLResponse)
async def get_windows_page(request: Request):
    return templates.TemplateResponse("windows.html", {"request": request})

@app.get("/cloud", response_class=HTMLResponse)
async def get_cloud_page(request: Request):
    return templates.TemplateResponse("cloud.html", {"request": request})

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    safe_name = sanitize_filename(file.filename or "unnamed")
    target_path = resolve_storage_path(safe_name)
    file_bytes = await file.read()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("wb") as destination:
        destination.write(file_bytes)

    if categorize_file(safe_name) == "image":
        await generate_thumbnail_for_uploaded_file(target_path)

    if categorize_file(safe_name) == "office":
        preview_pdf = FILES_DIR / f"{target_path.stem}.preview.pdf"
        if preview_pdf.exists():
            preview_pdf.unlink()
        convert_office_to_pdf(target_path, FILES_DIR)

    return {
        "status": "ok",
        "filename": safe_name,
        "category": categorize_file(safe_name),
        "badge": badge_for_extension(safe_name),
        "thumbnail": bool(thumbnail_path_for(safe_name).exists()),
        "size": len(file_bytes),
        "metadata": get_file_entry(target_path),
    }

@app.get("/view/{filename}")
async def view_file(filename: str, request: Request):
    file_path = resolve_storage_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")

    category = categorize_file(file_path.name)
    if category == "office":
        pdf_preview = FILES_DIR / f"{file_path.stem}.preview.pdf"
        if pdf_preview.exists():
            return await stream_file_range(request, pdf_preview, "application/pdf")
        if convert_office_to_pdf(file_path, FILES_DIR):
            return await stream_file_range(request, FILES_DIR / f"{file_path.stem}.pdf", "application/pdf")

    media_type = file_mime_type(file_path.name)
    if category in {"audio", "video"}:
        return await stream_file_range(request, file_path, media_type)
    return FileResponse(path=file_path, media_type=media_type, filename=file_path.name)

@app.get("/view-text/{filename}")
async def view_text_file(filename: str):
    file_path = resolve_storage_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")

    category = categorize_file(file_path.name)
    if category not in {"text", "code"}:
        raise HTTPException(status_code=400, detail="Diese Datei kann nicht als Text angezeigt werden.")

    text = read_text_file(file_path)
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")

@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = resolve_storage_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
    return FileResponse(path=file_path, filename=file_path.name, media_type=file_mime_type(file_path.name))

@app.get("/thumbnail/{filename}")
async def get_thumbnail(filename: str):
    file_path = resolve_storage_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
    thumbnail = thumbnail_path_for(file_path.name)
    if not thumbnail.exists():
        if categorize_file(file_path.name) == "image":
            await generate_thumbnail_for_uploaded_file(file_path)
        if not thumbnail.exists():
            raise HTTPException(status_code=404, detail="Kein Thumbnail verfügbar.")
    return FileResponse(path=thumbnail, media_type="image/jpeg")


# --- PERSISTENT CLOUD STORAGE API ---

@app.get("/api/cloud/folders")
async def list_cloud_folders(parent_id: int = 1):
    with cloud_db() as database:
        folders = database.execute(
            "SELECT id, name FROM cloud_folders WHERE parent_id = ? ORDER BY name COLLATE NOCASE",
            (parent_id,),
        ).fetchall()
        return {"folders": [dict(folder) for folder in folders]}

@app.post("/api/cloud/folders")
async def create_cloud_folder(data: dict):
    payload = data or {}
    folder_name = safe_cloud_name(payload.get("name"), "")
    parent_id = int(payload.get("parent_id") or 1)
    if not folder_name:
        raise HTTPException(status_code=400, detail="Bitte einen Ordnernamen angeben.")
    with cloud_db() as database:
        if not database.execute("SELECT id FROM cloud_folders WHERE id = ?", (parent_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Übergeordneter Ordner nicht gefunden.")
        try:
            cursor = database.execute(
                "INSERT INTO cloud_folders(parent_id, name) VALUES (?, ?)",
                (parent_id, folder_name),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Dieser Ordner existiert bereits.")
        return {"status": "ok", "id": cursor.lastrowid, "name": folder_name}

@app.delete("/api/cloud/folders/{folder_id}")
async def delete_cloud_folder(folder_id: int):
    if folder_id == 1:
        raise HTTPException(status_code=404, detail="Ordner nicht gefunden.")
    with cloud_db() as database:
        folder = database.execute("SELECT id FROM cloud_folders WHERE id = ?", (folder_id,)).fetchone()
        if not folder:
            raise HTTPException(status_code=404, detail="Ordner nicht gefunden.")
        database.execute("DELETE FROM cloud_folders WHERE id = ?", (folder_id,))
    return {"status": "ok"}

@app.get("/api/cloud/files")
async def list_cloud_files(folder_id: int = 1):
    with cloud_db() as database:
        folder = database.execute("SELECT id, name, parent_id FROM cloud_folders WHERE id = ?", (folder_id,)).fetchone()
        if not folder:
            raise HTTPException(status_code=404, detail="Ordner nicht gefunden.")
        folders = database.execute(
            "SELECT id, name FROM cloud_folders WHERE parent_id = ? ORDER BY name COLLATE NOCASE", (folder_id,)
        ).fetchall()
        files = database.execute(
            "SELECT id, name AS filename, size, modified_at FROM cloud_files WHERE folder_id = ? ORDER BY name COLLATE NOCASE",
            (folder_id,),
        ).fetchall()
        breadcrumbs = []
        current = folder
        while current:
            breadcrumbs.append({"id": current["id"], "name": current["name"]})
            current = database.execute("SELECT id, name, parent_id FROM cloud_folders WHERE id = ?", (current["parent_id"],)).fetchone() if current["parent_id"] else None
        prepared_files = []
        for item in files:
            record = dict(item)
            record["preview_kind"] = preview_kind_for_file(record["filename"])
            record["mime_type"] = file_mime_type(record["filename"])
            record["has_preview"] = record["preview_kind"] in {"image", "pdf", "text", "audio", "video"}
            prepared_files.append(record)
        return {"folder": dict(folder), "breadcrumbs": list(reversed(breadcrumbs)), "folders": [dict(item) for item in folders], "files": prepared_files}

@app.get("/api/cloud/files/{file_id}/preview")
async def preview_cloud_file(file_id: int):
    with cloud_db() as database:
        file = database.execute(
            "SELECT id, name, size FROM cloud_files WHERE id = ?",
            (file_id,),
        ).fetchone()
        if not file:
            raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
        chunks = database.execute(
            "SELECT content FROM cloud_file_chunks WHERE file_id = ? ORDER BY chunk_index",
            (file_id,),
        ).fetchall()
        content = b"".join(chunk["content"] for chunk in chunks)

    media_type = file_mime_type(file["name"])
    preview_kind = preview_kind_for_file(file["name"])
    if preview_kind == "text":
        try:
            payload = content.decode("utf-8")
        except UnicodeDecodeError:
            payload = content.decode("utf-8", errors="replace")
        return PlainTextResponse(payload, media_type=media_type)
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'inline; filename="{file["name"]}"'})

@app.post("/api/cloud/upload")
async def upload_cloud_file(file: UploadFile = File(...), folder_id: int = 1):
    filename = safe_cloud_name(file.filename)
    chunk_size = 1024 * 1024
    modified_at = time.time()
    with cloud_db() as database:
        if not database.execute("SELECT id FROM cloud_folders WHERE id = ?", (folder_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Ordner nicht gefunden.")
        existing = database.execute("SELECT id FROM cloud_files WHERE folder_id = ? AND name = ?", (folder_id, filename)).fetchone()
        if existing:
            database.execute("DELETE FROM cloud_files WHERE id = ?", (existing["id"],))
        cursor = database.execute(
            "INSERT INTO cloud_files(folder_id, name, size, modified_at) VALUES (?, ?, 0, ?)",
            (folder_id, filename, modified_at),
        )
        file_id = cursor.lastrowid
        size = 0
        chunk_index = 0
        while chunk := await file.read(chunk_size):
            database.execute(
                "INSERT INTO cloud_file_chunks(file_id, chunk_index, content) VALUES (?, ?, ?)",
                (file_id, chunk_index, chunk),
            )
            size += len(chunk)
            chunk_index += 1
        database.execute("UPDATE cloud_files SET size = ? WHERE id = ?", (size, file_id))
    return {"status": "ok", "id": file_id, "filename": filename}

@app.get("/api/cloud/download/{file_id}")
async def download_cloud_file(file_id: int):
    def read_chunks():
        with cloud_db() as database:
            file = database.execute("SELECT name FROM cloud_files WHERE id = ?", (file_id,)).fetchone()
            if not file:
                return
            chunks = database.execute("SELECT content FROM cloud_file_chunks WHERE file_id = ? ORDER BY chunk_index", (file_id,))
            for chunk in chunks:
                yield chunk["content"]
    with cloud_db() as database:
        file = database.execute("SELECT name FROM cloud_files WHERE id = ?", (file_id,)).fetchone()
    if not file:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
    return StreamingResponse(read_chunks(), media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{file["name"]}"'})

@app.delete("/api/cloud/files/{file_id}")
async def delete_cloud_file(file_id: int):
    with cloud_db() as database:
        if not database.execute("SELECT id FROM cloud_files WHERE id = ?", (file_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
        database.execute("DELETE FROM cloud_files WHERE id = ?", (file_id,))
    return {"status": "ok"}

# --- OTHER APIS & WEBSOCKETS ---

@app.get("/api/windows")
async def api_windows():
    return {"windows": get_window_states()}

@app.websocket("/ws")
async def websocket_lobby(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "add_player":
                updated_players = game.add_player(data["name"])
                await websocket.send_json({"type": "player_added", "players": updated_players})
    except WebSocketDisconnect:
        print("Lobby WebSocket sauber getrennt.")

@app.websocket("/ws/game")
async def websocket_game(websocket: WebSocket):
    await websocket.accept()
    initial_state = game.get_game_state()
    await websocket.send_json(initial_state)
    if game.is_bot_turn():
        await asyncio.sleep(0.2)
        await websocket.send_json(game.resolve_bot_turn())
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "throw":
                if data.get("shots") is not None:
                    new_state = game.update_score({"shots": data["shots"]})
                else:
                    new_state = game.update_score(data.get("score"))
                await websocket.send_json(new_state)
                if game.is_bot_turn() and not new_state.get("winner"):
                    await asyncio.sleep(0.2)
                    await websocket.send_json(game.resolve_bot_turn())
            if data.get("action") == "back":
                new_state = game.go_back()
                await websocket.send_json(new_state)
    except WebSocketDisconnect:
        print("Game WebSocket sauber getrennt.")

@app.post("/api/players/delete")
async def delete_player(data: dict):
    name = (data or {}).get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Kein Spielername angegeben.")

    updated_players = game.delete_player(name)
    return {"status": "ok", "players": updated_players}


@app.post("/api/start-game")
async def start_game(data: StartGameData):
    game.start_game(data.players, data.legs_to_win, data.mode)
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)