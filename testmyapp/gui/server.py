"""Interface locale du Test Floor et consultation des runs."""

from __future__ import annotations

import asyncio
import json
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from testmyapp.core.events import read_run_events
from testmyapp.gui.floor import FloorModel
from testmyapp.report.narrative import describe_action, step_result

STATIC_DIR = Path(__file__).parent / "static"
REPORT_CSP = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"}


class GuiHost:
    """Le serveur, le moteur et le modèle du floor partagent la même boucle."""

    def __init__(self, engine: Any = None, *, auto_start: bool = False) -> None:
        self.floor = FloorModel()
        self.events: list[dict] = []
        self.clients: dict[WebSocket, int] = {}
        self.sessions: dict[str, dict] = {}
        self.engine = engine
        self.auto_start = auto_start
        self.run_dir = engine.run_dir if engine else None
        self.workspace_report = None
        self.task: asyncio.Task | None = None
        self._replay_events: list[dict] = []
        self._replay_sessions: dict[str, dict] = {}
        self._replay_speed = 4.0
        self.result: dict | None = None
        if engine:
            self.floor.set_meta(engine.personas)
            engine.bus.on(self._record)

    def _record(self, event: dict, *, history: bool = False) -> None:
        if not history:
            self.floor._on_event(event)
            self.events.append(event)
        kind = event["type"]
        sid = event.get("session_id")
        if kind == "campaign.started":
            for reset in event.get("retry_sessions", []):
                self.sessions.pop(reset, None)
        if kind == "session.restored" and self.engine:
            for previous in self.engine.bus.events_of(sid):
                if previous["seq"] < event["seq"]:
                    self._record(previous, history=True)
        if kind in {"session.started", "session.blocked", "session.skipped"}:
            self.sessions[sid] = {"id": sid, "persona": event.get("persona"),
                                  "persona_name": event.get("persona_name"),
                                  "scenario": event.get("scenario_name") or event.get("scenario"),
                                  "outcome": None, "steps": {}}
        session = self.sessions.get(sid)
        if not session:
            return
        if kind == "session.completed":
            session["outcome"] = event.get("outcome")
        if kind in {"session.error", "session.blocked", "session.skipped"}:
            session["error"] = event.get("error") or event.get("reason")
        if "step_id" not in event or not (kind.startswith("step.") or kind == "action.performed"):
            return
        step = session["steps"].setdefault(event["step_id"], {"index": event["step_id"], "captures": [], "actions": []})
        if kind == "step.started":
            step["intent"] = event.get("intent")
        elif kind == "action.performed":
            step["actions"].append(describe_action(event))
        elif kind == "step.screenshot":
            path = Path(event.get("path", ""))
            if path.name:
                step["captures"].append({"name": path.name, "phase": event.get("phase", "after")})
        else:
            verdict = kind.split(".")[-1]
            evidence = event.get("evidence", [])
            if event.get("intent"):
                step["intent"] = event["intent"]
            step.update(verdict=verdict, duration_ms=event.get("duration_ms"),
                        evidence=evidence, results=step_result(verdict, evidence))

    def start_replay(self, run_dir: Path, speed: float = 4.0) -> bool:
        self.run_dir = Path(run_dir)
        self._replay_events = read_run_events(self.run_dir)
        # Les archives antérieures ne journalisaient que le maximum configuré.
        try:
            manifest = json.loads(self.file("campaign.json").read_text(encoding="utf-8"))
            desktop = manifest.get("target", {}).get("type") == "desktop"
        except (OSError, ValueError, AttributeError, HTTPException):
            desktop = False
        if desktop:
            for event in self._replay_events:
                if event.get("type") == "campaign.started":
                    event.setdefault("parallelism", 1)
        seen, retried = set(), set()
        for event in self._replay_events:
            retried.update(event.get("retry_sessions", []))
            if event["type"] == "session.started":
                sid = event["session_id"]
                if sid in seen:
                    retried.add(sid)
                seen.add(sid)
            self._record(event, history=True)
        self._replay_sessions = {sid: session for sid, session in self.sessions.items() if sid not in retried}
        self.sessions = {}
        self._replay_speed = max(0.5, speed)
        return bool(self._replay_events)

    def session_detail(self, session_id: str) -> dict | None:
        return self._replay_sessions.get(session_id) or self.sessions.get(session_id)

    async def _replay(self) -> None:
        previous = None
        for event in self._replay_events:
            current = event.get("ts", 0)
            if previous is not None:
                await asyncio.sleep(min(2.0, max(0, (current - previous) / self._replay_speed)))
            previous = current
            self._record(event)
        self.floor.stats["completed"] = True

    async def _run(self) -> None:
        try:
            self.result = await self.engine.run()
        except Exception as exc:
            self.result = {"status": "error", "score": None}
            self.floor.stats.update(completed=True, status="error")
            self.events.append({"type": "campaign.error", "error": str(exc)})

    async def command(self, command: str) -> bool:
        if command == "start" and self.task is None:
            if self.engine:
                self.task = asyncio.create_task(self._run())
            elif self._replay_events:
                self.task = asyncio.create_task(self._replay())
            return self.task is not None
        if self.engine and command in {"pause", "resume", "stop"}:
            return await self.engine.control(command)
        return False

    def file(self, relative: str) -> Path:
        if self.run_dir is None:
            raise HTTPException(404)
        root = self.run_dir.resolve()
        file = (root / relative).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise HTTPException(404)
        return file


def _local_origin(headers: Any) -> bool:
    host = headers.get("host", "")
    try:
        if urlsplit("//" + host).hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
    except ValueError:
        return False
    origin = headers.get("origin")
    return origin is None or origin == f"http://{host}"


def create_app(host: GuiHost, *, workspace: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        broadcaster = asyncio.create_task(broadcast_deltas(host))
        try:
            yield
        finally:
            broadcaster.cancel()
            if host.task and not host.task.done():
                host.task.cancel()
            await asyncio.gather(*(task for task in (broadcaster, host.task) if task), return_exceptions=True)

    app = FastAPI(title="TestMyApp GUI", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if not _local_origin(request.headers):
            return HTMLResponse("Origine locale requise", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        if workspace:
            page = page.replace('id="workspace-link" hidden', 'id="workspace-link"')
        return page

    @app.get("/api/state")
    async def api_state() -> dict:
        return host.floor.full_state()

    @app.get("/api/sessions/{session_id}")
    async def api_session(session_id: str) -> dict:
        session = host.session_detail(session_id)
        if session is None:
            raise HTTPException(404)
        return session

    @app.get("/captures/{filename}")
    async def capture(filename: str) -> FileResponse:
        if Path(filename).name != filename or Path(filename).suffix.lower() not in {".png", ".svg"}:
            raise HTTPException(404)
        file = host.file("screenshots/" + filename)
        if file.parent != host.run_dir.resolve() / "screenshots":
            raise HTTPException(404)
        return FileResponse(file, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @app.get("/report/report.html")
    async def report():
        if host.workspace_report:
            return RedirectResponse(host.workspace_report)
        return FileResponse(host.file("report/report.html"), media_type="text/html", headers=REPORT_CSP)

    @app.post("/api/control")
    async def api_control(payload: dict) -> dict:
        if not await host.command(str(payload.get("command", ""))):
            raise HTTPException(409, "Commande indisponible")
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        if not _local_origin(ws.headers):
            await ws.close(code=1008)
            return
        await ws.accept()
        snapshot = host.floor.full_state()
        snapshot.update(events=host.events[-120:], controls=bool(host.engine),
                        can_start=bool(host.engine or host._replay_events))
        host.clients[ws] = len(host.events)
        try:
            await ws.send_json(snapshot)
            if host.auto_start or host._replay_events:
                await host.command("start")
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            host.clients.pop(ws, None)

    return app


async def broadcast_deltas(host: GuiHost, rate_hz: float = 10.0) -> None:
    while True:
        await asyncio.sleep(1.0 / rate_hz)
        delta = host.floor.delta()
        if not host.clients:
            continue
        end = len(host.events)
        for ws, last in list(host.clients.items()):
            try:
                await asyncio.wait_for(ws.send_json({**delta, "events": host.events[last:end]}), timeout=0.25)
            except (RuntimeError, OSError, asyncio.TimeoutError):
                host.clients.pop(ws, None)
            else:
                if ws in host.clients:
                    host.clients[ws] = end


def run_gui_server(host: GuiHost, port: int = 7417, open_browser: bool = True) -> None:
    serve_local(create_app(host), port, open_browser)


def serve_local(app: FastAPI, port: int, open_browser: bool) -> None:
    import uvicorn

    class LocalServer(uvicorn.Server):
        async def startup(self, sockets=None) -> None:
            await super().startup(sockets=sockets)
            if self.started and open_browser:
                asyncio.create_task(asyncio.to_thread(webbrowser.open, f"http://127.0.0.1:{port}/"))

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    LocalServer(config).run()

