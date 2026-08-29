import os
import time
import asyncio
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
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