"""TUI Textual (spec §10) : dashboard campagne live.

Écrans : dashboard (sessions, scores, humeur, flux d'événements), vue
Test Floor en caractères (personas = blocs animés, émotions en emoji),
fiche persona, vue session. 100 % clavier, souris supportée. Parité
fonctionnelle avec la GUI : mêmes données du bus.
"""
from __future__ import annotations

import asyncio
import webbrowser
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from testmyapp.gui.floor import DOOR, REPORT_DESK, SOFA, STATIONS, TILES_H, TILES_W

MOOD_EMOJI = {"joyeux": "😄", "content": "🙂", "neutre": "😐", "agace": "😕", "furieux": "😠"}
PUNCT_EMOJI = {"celebrate": "🎉", "confus": "❓", "impatient": "⏳", "eureka": "💡",
               "dort": "💤", "abandonne": "🏳️", "agace": "😒", "furieux": "😡",
               "joyeux": "😄", "content": "🙂", "intrigue": "🤔", "fatigue": "🥱", "surpris": "😮"}
STATE_TXT = {"arriving": "arrive", "walking": "en chemin", "working": "travaille",
             "to_report": "va au rapport", "reporting": "dépose", "leaving": "s'en va", "gone": "parti"}


class PersonaTable(DataTable):
    def on_mount(self) -> None:
        self.add_columns("Persona", "Scénario", "État", "Étapes", "Humeur", "Émotion", "Durée")


class TestFloorTUI(App):
    """TUI attachée à un bus d'événements vivant (§7.2)."""

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #floor { width: 2fr; border: round #2a3042; padding: 0 1; }
    #right { width: 3fr; }
    DataTable { height: 1fr; }
    RichLog { height: 1fr; border: round #2a3042; }
    #stats { height: 3; dock: bottom; padding: 0 1; background: $surface; }
    """
    BINDINGS = [
        ("q", "quit", "Quitter"),
        ("r", "toggle_log", "Journal"),
        ("f", "toggle_floor", "Floor"),
        ("p", "toggle_pause", "Pause / reprendre"),
        ("s", "stop_campaign", "Arrêter"),
        ("o", "open_report", "Rapport"),
    ]

    def __init__(self, bus: Any, engine: Any = None) -> None:
        super().__init__()
        self.bus = bus
        self.engine = engine
        self.floor_personas: dict[str, dict] = {}
        self.stats: dict = {"sessions_done": 0, "sessions_total": 0, "score": None}
        self.session_rows: dict[str, dict] = {}
        self.campaign_task: asyncio.Task | None = None
        self.events_task: asyncio.Task | None = None
        self.queue: asyncio.Queue | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield Static(id="floor")
            with Vertical(id="right"):
                yield PersonaTable(id="sessions")
                yield Static("Entrée affiche le détail de la session.", id="session-detail", markup=False)
                yield RichLog(id="log", markup=True, wrap=True, max_lines=500)
        yield Static(id="stats")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "TestMyApp — TUI"
        table = self.query_one("#sessions", PersonaTable)
        table.cursor_type = "row"
        self.query_one("#log", RichLog).write("[dim]En attente d'événements…[/dim]")
        self.queue = self.bus.subscribe()
        self.events_task = asyncio.create_task(self._pump(self.queue))
        if self.engine:
            self.campaign_task = asyncio.create_task(self.engine.run())
        self.set_interval(0.25, self._refresh_views)

    async def _pump(self, q: asyncio.Queue) -> None:
        while True:
            ev = await q.get()
            self._handle_event(ev)

    def on_unmount(self) -> None:
        if self.queue is not None:
            self.bus.unsubscribe(self.queue)
        if self.events_task:
            self.events_task.cancel()

    async def finish_campaign(self) -> dict:
        if self.campaign_task is None:
            return {"status": "interrupted"}
        if not self.campaign_task.done():
            await self.engine.control("stop")
        return await self.campaign_task

    async def action_toggle_pause(self) -> None:
        if self.engine:
            await self.engine.control("resume" if self.stats.get("paused") else "pause")

    async def action_stop_campaign(self) -> None:
        if self.engine:
            await self.engine.control("stop")

    async def action_open_report(self) -> None:
        if self.engine and self.engine.report_path:
            await asyncio.to_thread(webbrowser.open, Path(self.engine.report_path).resolve().as_uri())

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        sid = event.row_key.value
        row = self.session_rows.get(sid)
        if not row:
            return
        lines = [f"{row['persona']} · {row['scenario']} · {row['state']}"]
        for ev in self.bus.events_of(sid):
            kind = ev["type"]
            if kind == "step.started":
                lines.append(f"{ev['step_id'] + 1}. {ev.get('intent', '')}")
            elif kind in {"step.passed", "step.failed", "step.warned"}:
                verdict = {"step.passed": "Réussite", "step.failed": "Échec", "step.warned": "Avertissement"}
                lines.append(f"    {verdict[kind]} · {ev.get('duration_ms', 0) / 1000:.2f} s")
            elif kind == "step.screenshot":
                lines.append(f"    Capture · {ev['path']}")
        self.query_one("#session-detail", Static).update("\n".join(lines[-12:]))

    # -------------------------------------------------------------- événements
    def _handle_event(self, ev: dict) -> None:
        etype = ev["type"]
        sid = ev.get("session_id")
        log = self.query_one("#log", RichLog)
        if etype == "campaign.started":
            self.stats.update({"sessions_total": ev.get("sessions_total", 0),
                               "sessions_done": 0, "seed": ev.get("seed"),
                               "started": True})
            log.write(f"[b]🧪 Campagne lancée[/b] — seed {ev.get('seed')}, "
                      f"{ev.get('sessions_total')} sessions, vitesse {ev.get('speed')}")
        elif etype == "campaign.completed":
            self.stats.update({"score": ev.get("score"), "completed": True})
            log.write(f"[b]🏁 Campagne terminée[/b] — score [b]{ev.get('score')}/100[/b], "
                      f"rapport : {ev.get('report_path')}")
        elif etype == "campaign.control":
            self.stats["paused"] = ev["command"] == "pause"
            self.stats["stopping"] = ev["command"] == "stop"
        elif etype == "session.restored":
            self.session_rows[sid] = {"persona": ev["persona"], "scenario": ev["scenario"],
                                      "state": ev["outcome"], "steps": "—", "mood": 0,
                                      "emoji": "—", "emotion": "", "duration": "—"}
            self.stats["sessions_done"] += 1
        elif etype == "session.started":
            pid = ev.get("persona", "")
            self.session_rows[sid] = {"persona": pid, "scenario": ev.get("scenario"),
                                      "state": "arrive", "steps": "0/0", "mood": 0.0,
                                      "emoji": "😐", "emotion": "", "duration": "—"}
            self._spawn_floor(pid, sid)
            log.write(f"🚶 [b]{pid}[/b] démarre « {ev.get('scenario')} »")
        elif sid and sid in self.session_rows:
            row = self.session_rows[sid]
            pid = row["persona"]
            if etype == "step.passed":
                row["state"] = "travaille"
                row["steps"] = f"{row['steps'].split('/')[0]}…"
                self._bump_steps(sid, passed=True)
                log.write(f"  ✅ {pid} — étape {(ev.get('step_id', 0)) + 1} "
                          f"({ev.get('duration_ms', 0) / 1000:.1f}s)")
                self._move_floor(pid, "work")
            elif etype == "step.failed":
                self._bump_steps(sid, passed=False)
                log.write(f"  ❌ {pid} — étape {(ev.get('step_id', 0)) + 1} échouée")
                self._move_floor(pid, "sad")
            elif etype == "step.warned":
                log.write(f"  ⚠️ {pid} — étape {(ev.get('step_id', 0)) + 1} avec avertissement")
            elif etype == "persona.mood":
                row["mood"] = ev.get("mood", 0.0)
                row["emoji"] = ev.get("badge_emoji", "😐")
                self._floor_set(pid, mood=ev.get("mood"), badge=ev.get("badge_emoji"))
            elif etype == "persona.emotion":
                emoji = PUNCT_EMOJI.get(ev.get("emotion"), "•")
                row["emotion"] = f"{emoji} {ev.get('label', '')}"
                log.write(f"  {emoji} {pid} : {ev.get('label')}")
            elif etype == "persona.speech":
                log.write(f"  💬 [i]{pid}[/i] « {ev.get('text')} »")
            elif etype == "a11y.violation":
                log.write(f"  ♿ [yellow]{ev.get('rule')}[/yellow] ({ev.get('severity')})")
            elif etype == "target.console_error":
                log.write(f"  🖥️ [red]console[/red] {str(ev.get('text'))[:120]}")
            elif etype == "session.completed":
                outcome = ev.get("outcome", "?")
                row["state"] = STATE_TXT.get("gone", outcome)
                row["duration"] = f"{ev.get('duration_ms', 0) / 1000:.1f}s"
                self.stats["sessions_done"] = self.stats.get("sessions_done", 0) + 1
                log.write(f"🏁 {pid} — {outcome} "
                          f"(humeur {ev.get('mood_final', 0):+.1f})")
                self._floor_exit(pid, outcome)

    def _bump_steps(self, sid: str, passed: bool) -> None:
        row = self.session_rows[sid]
        cur = row.get("_p", 0), row.get("_f", 0)
        p, f = cur
        if passed:
            p += 1
        else:
            f += 1
        row["_p"], row["_f"] = p, f
        row["steps"] = f"{p}/{f}"

    # ------------------------------------------------------------------ floor
    def _spawn_floor(self, pid: str, sid: str) -> None:
        self.floor_personas[pid] = {"x": float(DOOR[0]), "y": float(DOOR[1]),
                                    "tx": float(DOOR[0]), "ty": float(DOOR[1]),
                                    "anim": "idle", "emoji": "😐", "bubble": "",
                                    "session": sid}

    def _move_floor(self, pid: str, anim: str) -> None:
        p = self.floor_personas.get(pid)
        if not p:
            return
        idx = sum(1 for _ in self.floor_personas) % len(STATIONS)
        p["tx"], p["ty"] = STATIONS[idx % len(STATIONS)]
        p["anim"] = anim

    def _floor_set(self, pid: str, mood=None, badge=None) -> None:
        p = self.floor_personas.get(pid)
        if not p:
            return
        if mood is not None:
            p["mood"] = mood
        if badge:
            p["emoji"] = badge

    def _floor_exit(self, pid: str, outcome: str) -> None:
        p = self.floor_personas.get(pid)
        if not p:
            return
        p["tx"], p["ty"] = REPORT_DESK
        p["anim"] = "walk"
        p["bubble"] = "🎉" if outcome == "pass" else "🏳️"

    # ---------------------------------------------------------------- rendu
    def _refresh_views(self) -> None:
        self._render_floor()
        self._render_table()
        self._render_stats()

    def _render_floor(self) -> None:
        w, h = TILES_W, TILES_H
        grid = [["·" for _ in range(w)] for _ in range(h)]
        # décor
        dx, dy = DOOR
        grid[dy][dx] = "▣"
        sx, sy = SOFA
        for i in range(2):
            grid[sy][sx + i] = "▒"
        rx, ry = REPORT_DESK
        grid[ry][rx] = "▤"
        for i, (x, y) in enumerate(STATIONS):
            if y < h and x < w:
                grid[y][x] = "🖥" if (x + y) % 2 == 0 else "▥"
        # personas : position animée vers tx/ty (visualisation simple)
        for pid, p in self.floor_personas.items():
            px, py = int(p["x"]), int(p["y"])
            tx, ty = int(p["tx"]), int(p["ty"])
            if (px, py) != (tx, ty):
                if px != tx:
                    px += 1 if tx > px else -1
                elif py != ty:
                    py += 1 if ty > py else -1
                p["x"], p["y"] = px, py
            if 0 <= py < h and 0 <= px < w:
                grid[py][px] = "☻"
        lines = ["".join(row) for row in grid]
        # légende : personas + humeurs + émotions courantes
        legend = []
        for pid, p in self.floor_personas.items():
            bubble = p.get("bubble", "")
            legend.append(f"{pid} {p.get('emoji', '😐')} {bubble}".strip())
        floor_el = self.query_one("#floor", Static)
        floor_el.update("\n".join(lines) + "\n\n" + "  ·  ".join(legend))

    def _render_table(self) -> None:
        table = self.query_one("#sessions", PersonaTable)
        selected = table.cursor_row
        table.clear()
        for sid, row in self.session_rows.items():
            table.add_row(row["persona"], row["scenario"], row["state"], row["steps"],
                          f"{row['mood']:+.1f}", row["emotion"] or "—", row["duration"],
                          key=sid)
        if self.session_rows:
            table.move_cursor(row=min(selected, len(self.session_rows) - 1), animate=False)

    def _render_stats(self) -> None:
        st = self.stats
        done, total = st.get("sessions_done", 0), st.get("sessions_total", 0)
        score = f"{st['score']:.1f}/100" if st.get("score") is not None else "—"
        phase = ("Arrêt en cours" if st.get("stopping") and not st.get("completed") else
                 "Pause" if st.get("paused") and not st.get("completed") else
                 st.get("status") or "")
        self.query_one("#stats", Static).update(
            f"Sessions {done}/{total} · Score {score} {phase}")

    def action_toggle_log(self) -> None:
        log = self.query_one("#log", RichLog)
        log.display = not log.display

    def action_toggle_floor(self) -> None:
        floor = self.query_one("#floor", Static)
        floor.display = not floor.display


def run_tui(bus: Any, engine: Any = None) -> None:
    TestFloorTUI(bus, engine).run()
