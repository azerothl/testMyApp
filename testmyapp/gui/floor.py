"""Modèle du Test Floor côté serveur (spec §11.2).

Traduit le flux d'événements du bus en états de scène : personas (position
cible, animation, bulle), stations, bureau rapport, porte. Le client
interpole les mouvements entre deltas (10 Hz).
"""
from __future__ import annotations

import time
from typing import Any

# Grille du floor (tuiles) — lay­out §11.2
TILES_W, TILES_H = 26, 15
DOOR = (2, 12)
SOFA = (20, 12)
REPORT_DESK = (13, 12)
STATIONS = [(5, 6), (9, 6), (13, 6), (17, 6), (21, 6)]
EXTENDED_STATIONS = [(x, y) for y in (4, 7, 10) for x in range(2, 24, 3)]
WALK_SPEED_TPS = 2.2  # tuiles/s (désynchronisé par persona côté client)


class FloorModel:
    """État de la scène dérivé du bus ; consommé par le serveur WS."""

    def __init__(self) -> None:
        self.personas: dict[str, dict[str, Any]] = {}
        self.stations: list[tuple[int, int]] = []
        self.station_sessions: dict[int, str] = {}
        self.meta: dict[str, dict] = {}  # persona_id -> nom/palette (rendu client)
        self.waiting: dict[str, dict] = {}
        self.stats: dict[str, Any] = {"score": None, "sessions_done": 0, "sessions_total": 0,
                                      "started": False, "completed": False}
        self.effects: list[dict] = []

    def set_meta(self, personas: dict[str, Any]) -> None:
        """Injecte nom/palette par persona (moteur vivant ou replay)."""
        for pid, p in personas.items():
            self.meta[pid] = {
                "name": getattr(p, "name", pid),
                "age": getattr(p, "age", None),
                "occupation": getattr(p, "occupation", ""),
                "palette": getattr(p, "palette", {}) or {},
                "accessories": getattr(p, "accessories", []) or [],
                "bio": p.bio, "goals": p.goals_primary, "locale": p.locale,
                "device": p.device_type, "network": p.network_profile, "input": p.input_device,
            }

    def attach(self, bus) -> None:
        bus.on(self._on_event)

    # ------------------------------------------------------------------ bus
    def _on_event(self, ev: dict) -> None:
        etype = ev.get("type", "")
        sid = ev.get("session_id")
        p = self.personas.get(sid)

        if etype == "campaign.started":
            self.waiting = {entry["session_id"]: entry for entry in ev.get("plan", [])}
            workflow = ev.get("workflow_plan") or [entry for entry in ev.get("plan", []) if "task" in entry]
            self.stats["workflow"] = {entry["session_id"]: {**entry, "status": "waiting"} for entry in workflow}
            self.stats.update(started=True, completed=False, sessions_total=ev.get("sessions_total", 0),
                              sessions_done=0, seed=ev.get("seed"), speed=ev.get("speed"),
                              paused=False, stopping=False)
            capacity = ev.get("parallelism", ev.get("max_workers", 5))
            count = max(0, min(capacity, self.stats["sessions_total"]))
            self.stats["parallelism"] = count
            if count > len(STATIONS):
                self.stations = EXTENDED_STATIONS[:count]
            else:
                center, y = STATIONS[len(STATIONS) // 2]
                self.stations = [(center + 4 * index - 2 * (count - 1), y) for index in range(count)]
        elif etype == "campaign.resumed":
            self.stats["sessions_done"] = ev["resumed_sessions"]
        elif etype == "campaign.control":
            command = ev["command"]
            self.stats["paused"] = command == "pause"
            self.stats["stopping"] = command == "stop"
        elif etype == "campaign.completed":
            self.waiting.clear()
            self.stats["score"] = ev.get("score")
            self.stats["completed"] = True
            self.stats["status"] = ev.get("status")
            self.stats["report_ready"] = bool(ev.get("report_path"))
        elif etype == "report.ready":
            self.stats["report_ready"] = True
        elif etype == "session.started":
            if sid in self.stats.get("workflow", {}):
                self.stats["workflow"][sid]["status"] = "running"
            self.waiting.pop(sid, None)
            pid = ev["persona"]
            self.meta.setdefault(pid, {"name": ev.get("persona_name") or pid})
            if ev.get("task"):
                self.personas = {key: actor for key, actor in self.personas.items()
                                 if actor["persona_id"] != pid or not actor.get("outcome")}
            busy = {actor["station"] for actor in self.personas.values() if not actor.get("outcome")}
            station = next((slot for slot in range(len(self.stations)) if slot not in busy), None)
            if station is None:
                return
            self.station_sessions[station] = sid
            self.personas[sid] = {"x": DOOR[0], "y": DOOR[1], "station": station,
                                  "anim": "walk", "state": "walking", "facing": 1,
                                  "persona_id": pid, "session_id": sid, "scenario": ev.get("scenario")}
        elif etype in {"session.completed", "session.restored"}:
            self.waiting.pop(sid, None)
            if sid in self.stats.get("workflow", {}):
                self.stats["workflow"][sid]["status"] = ev.get("outcome")
            if etype == "session.completed":
                self.stats["sessions_done"] = self.stats.get("sessions_done", 0) + 1
            if p is not None:
                p.update(outcome=ev.get("outcome", "pass"), state="to_report", completed_at=time.time())
        elif p is None:
            return
        elif etype == "persona.mood":
            p["mood"] = ev.get("mood", 0.0)
            p["badge"] = ev.get("emotion_badge", "neutre")
            p["badge_emoji"] = ev.get("badge_emoji", "😐")
        elif etype == "persona.emotion":
            p["bubble"] = {"emotion": ev.get("emotion"), "emoji": ev.get("emoji"),
                           "label": ev.get("label"), "count": ev.get("count", 1), "ts": time.time()}
            x, y = self.stations[p["station"]]
            if ev.get("emotion") in {"celebrate"}:
                self.effects.append({"kind": "confetti", "x": x, "y": y})
            if ev.get("emotion") == "furieux":
                self.effects.append({"kind": "smoke", "x": x, "y": y})
            if ev.get("emotion") == "eureka":
                self.effects.append({"kind": "stars", "x": x, "y": y})
        elif etype == "persona.speech":
            p["speech"] = ev.get("text", "")
            p["speech_ttl"] = ev.get("ttl_ms", 4000) / 1000.0
            p["speech_ts"] = time.time()
        elif etype == "step.started":
            p["anim"] = "work"
            p["state"] = "working"
        elif etype == "step.passed":
            p["last_step_ok"] = True
        elif etype == "step.failed":
            p["anim"] = "frustrated"
            x, y = self.stations[p["station"]]
            self.effects.append({"kind": "smoke", "x": x, "y": y})

    # ---------------------------------------------------------------- états
    def full_state(self) -> dict:
        """Snapshot complet à la connexion (protocole §12.3)."""
        return {
            "type": "floor.state",
            "tiles": [TILES_W, TILES_H],
            "door": DOOR, "sofa": SOFA, "report_desk": REPORT_DESK,
            "stations": self.stations,
            "station_sessions": self.station_sessions,
            "meta": dict(self.meta),
            "personas": {pid: self._persona_payload(p) for pid, p in self.personas.items()},
            "stats": {**self.stats, "waiting": list(self.waiting.values())},
        }

    def delta(self) -> dict:
        """Deltas légers 10 Hz : positions/états des personas + stats + effets."""
        self.personas = {sid: p for sid, p in self.personas.items()
                         if time.time() - p.get("completed_at", time.time()) < 24}
        effects = list(self.effects)
        self.effects.clear()
        return {
            "type": "floor.delta",
            "personas": {pid: self._persona_payload(p) for pid, p in self.personas.items()},
            "stats": {**self.stats, "waiting": list(self.waiting.values())},
            "effects": effects,
            "stations": self.stations,
            "station_sessions": self.station_sessions,
        }

    def _persona_payload(self, p: dict) -> dict:
        out = {k: p.get(k) for k in ("x", "y", "station", "persona_id", "anim", "state", "facing",
                                     "badge", "badge_emoji", "mood", "speech", "speech_ttl",
                                     "speech_ts", "scenario", "session_id", "outcome")}
        bubble = p.get("bubble")
        if bubble and time.time() - bubble["ts"] < 3.0:
            out["bubble"] = bubble
        speech = p.get("speech")
        if speech and time.time() - p.get("speech_ts", 0) < p.get("speech_ttl", 4.0):
            out["speech"] = speech
        return out
