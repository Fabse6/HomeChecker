import os
import time
import asyncio
import re
import sqlite3
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from dartlogic import dartgame
from sensors import get_window_states

app = FastAPI()
templates = Jinja2Templates(directory="templates")

game = dartgame()

# Ordner für temporäre Dateien erstellen
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
CLOUD_DB = Path("cloud.db")

# Speicher für Datei-Metadaten
uploaded_files = {}

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

@app.get("/files", response_class=HTMLResponse)
async def get_files_page(request: Request):
    return templates.TemplateResponse("files.html", {"request": request})

@app.get("/cloud", response_class=HTMLResponse)
async def get_cloud_page(request: Request):
    return templates.TemplateResponse("cloud.html", {"request": request})

# --- FILE TRANSFER API ---

@app.get("/api/files")
async def list_files():
    return {"files": get_active_files()}

@app.post("/api/files/upload")
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    
    # Datei speichern
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
        
    expires_at = time.time() + 300  # 5 Minuten (300 Sekunden)
    uploaded_files[file.filename] = {
        "size": len(content),
        "expires_at": expires_at
    }
    
    # Löschauftrag im Hintergrund starten
    background_tasks.add_task(delete_file_after_delay, file.filename, 300)
    
    return {"status": "ok", "filename": file.filename}

@app.get("/api/files/download/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(file_path) and filename in uploaded_files:
        return FileResponse(path=file_path, filename=filename)
    return HTMLResponse(content="Datei nicht gefunden oder bereits abgelaufen.", status_code=404)

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
        return {"folder": dict(folder), "breadcrumbs": list(reversed(breadcrumbs)), "folders": [dict(item) for item in folders], "files": [dict(item) for item in files]}

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