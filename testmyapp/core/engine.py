"""Orchestration de campagne (spec §7).

Hiérarchie : campagne → sessions (persona × scénario × cible) → étapes →
actions. Parallélisme limité par ``max_workers`` ; reproductibilité par
graine dérivée par session ; reprise ``--resume`` ; le dossier de run
accumulate events.jsonl, metrics.jsonl et les captures (§13).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from testmyapp import __version__
from testmyapp.behavior.runner import SessionRunner
from testmyapp.config import Campaign, ConfigError, Persona, Scenario, Target
from testmyapp.core.events import EventBus, EventRecorder, lock_run, read_run_events
from testmyapp.core.scoring import AxisScore, compute_axes, global_score
from testmyapp.core.rng import derive_seed
from testmyapp.core.proofs import run_parameters
from testmyapp.drivers.base import public_url


@dataclass
class SessionState:
    """État consolidé d'une session pour scoring/rapport/GUI."""

    session_id: str
    persona_id: str
    scenario_id: str
    target_id: str
    outcome: str = "running"
    steps_passed: int = 0
    steps_failed: int = 0
    steps_warned: int = 0
    duration_ms: float = 0.0
    mood_final: float = 0.0
    mood: float = 0.0
    badge: str = "neutre"
    speech: str = ""
    emotion: str = ""
    friction_extra_actions: int = 0
    console_errors: list = field(default_factory=list)
    vitals: dict = field(default_factory=dict)
    a11y_violations: list = field(default_factory=list)
    a11y_review: list = field(default_factory=list)
    a11y_scanned: bool = False
    crashed: bool = False
    freezes_ms: list[int] = field(default_factory=list)
    screenshots: list = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    blocking_failure: bool = False
    a11y_pages: list[str] = field(default_factory=list)
    stability_observed: bool = False


class CampaignEngine:
    def __init__(self, campaign: Campaign, personas: list[Persona], scenarios: list[Scenario],
                 target: Target, run_dir: Path, speed: str | None = None,
                 seed: int | None = None, resume: bool = False, *, tasks: dict | None = None,
                 model: dict | None = None, retry: str | None = None, app_version: str = "") -> None:
        self.campaign = campaign
        self.personas = {p.id: p for p in personas}
        self.scenarios = {s.id: s for s in scenarios} if isinstance(scenarios, (list, tuple)) else dict(scenarios)
        self.target = target
        self.tasks = tasks or {}
        self.model = model
        self.workflow = bool(model and model.get("version") == 2)
        self.retry = retry
        self.app_version = app_version
        self.run_dir = Path(run_dir)
        self.bus = EventBus()
        if speed:
            campaign.speed = speed
        if seed is not None:
            campaign.seed = seed
        self.speed = campaign.speed
        self.seed = campaign.seed
        self.state: dict[str, SessionState] = {}
        self.done = asyncio.Event()
        self.started_at = 0.0
        self.score: float | None = None
        self.axes: list[AxisScore] = []
        self._sessions_total = len(self.session_plan())
        self._done_count = 0
        self.report_path: str | None = None
        self.resume = resume
        self._resumed_sessions: set[str] = set()
        self._running_gate = asyncio.Event()
        self._running_gate.set()
        self._stop_requested = False
        self._active = False

    def _prepare_run(self) -> str:
        cfg = {
            "schema_version": 2,
            "engine_version": __version__,
            "campaign_id": self.campaign.id,
            "target": asdict(self.target),
            "personas": sorted(self.campaign.personas),
            "scenarios": sorted(self.campaign.scenarios),
            "speed": self.campaign.speed,
            "seed": self.campaign.seed,
            "thresholds": self.campaign.thresholds,
            "weights": self.campaign.weights,
            "max_workers": self.campaign.max_workers,
        }
        if not any("goal" in task for task in self.tasks.values()):
            del cfg["target"]["discovery_bridge"]
        if not self.target.storage_state:
            del cfg["target"]["storage_state"]
        effective = {"campaign": asdict(self.campaign), "target": cfg["target"],
                     "personas": {pid: asdict(p) for pid, p in self.personas.items()},
                     "scenarios": {sid: asdict(s) for sid, s in self.scenarios.items()}}
        del effective["campaign"]["discovery"]
        if self.campaign.depends_on:
            cfg["depends_on"] = self.campaign.depends_on
        else:
            del effective["campaign"]["depends_on"]
        if self.tasks:
            cfg["tasks"] = effective["tasks"] = self.tasks
        if self.model is not None:
            cfg["model"] = effective["model"] = self.model
        for scenario in effective["scenarios"].values():
            if scenario["task_text"] is None:
                del scenario["task_text"]
            for step in scenario["steps"]:
                if not step["action_paths"]:
                    del step["action_paths"]
                if not step["stop_on_failure"]:
                    del step["stop_on_failure"]
                for check in step["expects"]:
                    if not check["api"]:
                        del check["api"]
                    if not check["failure_text"]:
                        del check["failure_text"]
        config_hash = hashlib.sha256(json.dumps(effective, sort_keys=True).encode("utf-8")).hexdigest()
        cfg_path = self.run_dir / "campaign.json"
        if self.resume:
            if not cfg_path.is_file():
                raise ConfigError(f"configuration de run introuvable : {cfg_path}")
            previous = json.loads(cfg_path.read_text(encoding="utf-8"))
            if previous.get("config_hash"):
                matches = previous["config_hash"] == config_hash and previous.get("engine_version") == __version__
            else:
                legacy_keys = cfg.keys() - {"schema_version", "engine_version"}
                matches = all(previous.get(k) == cfg[k] for k in legacy_keys)
            if not matches:
                raise ConfigError("La configuration a changé depuis le run d'origine")
            self._load_resumed()
            return previous.get("config_hash", config_hash)

        cfg["config_hash"] = config_hash
        if self.app_version:
            cfg["app_version"] = self.app_version
        cfg["target"]["base_url"] = public_url(self.target.base_url)
        cfg["persona_order"] = self.campaign.personas
        cfg["scenario_order"] = self.campaign.scenarios
        cfg["frozen_at"] = datetime.now(timezone.utc).isoformat()
        with cfg_path.open("x", encoding="utf-8") as handle:
            json.dump(cfg, handle, ensure_ascii=False, indent=2)
        return config_hash

    def _load_resumed(self) -> None:
        """Préserve le journal et reconstruit uniquement les tentatives terminées."""
        events = read_run_events(self.run_dir)
        self.bus.restore(events)
        retry_started = {sid: event["seq"] for event in events if event["type"] == "campaign.started"
                         for sid in event.get("retry_sessions", [])}
        for sid, pid, scid in self.session_plan():
            attempt: list[dict] = []
            for event in self.bus.events_of(sid):
                if event["seq"] < retry_started.get(sid, -1):
                    continue
                if event["type"] in {"session.started", "session.blocked", "session.skipped", "session.retrying"}:
                    attempt.clear()
                attempt.append(event)
            if any(event["type"] == "session.completed" for event in attempt):
                state = self._state_for(sid, pid, scid)
                for event in attempt:
                    self._apply_event(state, event)
                self._resumed_sessions.add(sid)
        if self.retry is not None:
            self._prepare_retry()

    def _workflow_dependencies(self) -> tuple[dict, dict]:
        by_scenario = {scid: sid for sid, _, scid in self.session_plan()}
        parents, dependencies = {}, {}
        for task in self.tasks.values():
            sid = by_scenario[task["scenario"]]
            if task["after"]:
                parents[sid] = by_scenario[self.tasks[task["after"]]["scenario"]]
            keys = [task["after"]] if task["after"] else []
            keys.extend(task.get("wait_for", []))
            dependencies[sid] = [by_scenario[self.tasks[key]["scenario"]] for key in keys]
        return parents, dependencies

    def _prepare_retry(self) -> None:
        state = self.state.get(self.retry)
        if not self.workflow or not state or state.outcome not in {"fail", "error", "abandoned"}:
            raise ConfigError("Choisissez une tâche de workflow en échec.")
        _, dependencies = self._workflow_dependencies()
        reconsider = {self.retry}
        for sid, waiting_for in dependencies.items():
            if reconsider.intersection(waiting_for) and (
                sid not in self.state or self.state[sid].outcome in {"blocked", "skipped"}
            ):
                reconsider.add(sid)
        for sid in reconsider:
            self.state.pop(sid, None)
            self._resumed_sessions.discard(sid)
        self._retry_sessions = reconsider

    def session_plan(self) -> list[tuple[str, str, str]]:
        """Liste (session_id, persona_id, scenario_id) — ids stables par graine."""
        if self.workflow:
            assignments = [(task["persona"], task["scenario"]) for task in self.tasks.values()]
        elif self.tasks:
            assignments = [(pid, self.tasks[pid].get("scenario", f"decouverte-{pid}"))
                           for pid in self.campaign.personas]
        else:
            assignments = [(pid, sid) for pid in self.campaign.personas for sid in self.campaign.scenarios]
        return [(f"{pid}×{scenario}-{derive_seed(self.seed, pid, scenario) % 10_000}", pid, scenario)
                for pid, scenario in assignments]

    async def run(self) -> dict[str, Any]:
        if self._active or self.done.is_set():
            raise RuntimeError("Ce moteur a déjà été lancé")
        self._active = True
        try:
            if not self.resume:
                self.run_dir.mkdir(parents=True, exist_ok=True)
            with lock_run(self.run_dir):
                config_hash = self._prepare_run()
                recorder = EventRecorder(self.run_dir, self.bus, resume=self.resume)
                try:
                    return await self._execute(config_hash)
                finally:
                    recorder.close()
        finally:
            self._active = False
            self.done.set()

    async def _execute(self, config_hash: str) -> dict[str, Any]:
        self.started_at = time.monotonic()
        entries = self.session_plan()
        pending = [entry for entry in entries if entry[0] not in self._resumed_sessions]
        remaining = len(pending)
        serial = self.target.type == "desktop" or bool(self.campaign.depends_on)
        concurrency = min(remaining, 1 if serial else max(1, self.campaign.max_workers))
        parents, dependencies = {}, {}
        if self.workflow:
            parents, dependencies = self._workflow_dependencies()
            concurrency = min(concurrency, self._workflow_width(pending, dependencies), len(self.personas))
        task_numbers = {sid: scid.removeprefix("decouverte-task-") for sid, _, scid in entries}
        task_conditions = {task["scenario"]: task.get("conditions", {}) for task in self.tasks.values()} if self.workflow else {}
        plan = [{"session_id": sid, "persona": pid, "name": self.personas[pid].name,
                 "after": self.personas[parent].name if (parent := self.campaign.depends_on.get(pid)) else None,
                 **({"task": task_numbers[sid],
                     "scenario": self.scenarios[scid].name or scid,
                     "after_tasks": [task_numbers[parent_sid] for parent_sid in dependencies[sid]],
                     "conditions": {key.removeprefix("task-"): value for key, value in task_conditions[scid].items()}}
                    if self.workflow else {})}
                for sid, pid, scid in entries]
        self.bus.emit("campaign.started", campaign_id=self.campaign.id, seed=self.seed,
                      sessions_total=self._sessions_total, speed=self.speed, config_hash=config_hash,
                      target=self.target.id, max_workers=self.campaign.max_workers,
                      parallelism=concurrency,
                      plan=[entry for entry in plan if entry["session_id"] not in self._resumed_sessions],
                      **({"workflow_plan": plan} if self.workflow else {}),
                      **({"retry_sessions": sorted(self._retry_sessions)} if self.retry is not None else {}))
        if self._resumed_sessions:
            self.bus.emit("campaign.resumed", resumed_sessions=len(self._resumed_sessions))
            for sid in sorted(self._resumed_sessions):
                state = self.state[sid]
                self.bus.emit("session.restored", session_id=sid, persona=state.persona_id,
                              scenario=state.scenario_id, outcome=state.outcome)
        self._done_count = len(self._resumed_sessions)
        self.bus.on(self._on_session_event)

        if self.workflow:
            await self._run_workflow(pending, parents, dependencies, concurrency)
        else:
            queue: asyncio.Queue = asyncio.Queue()
            for item in pending:
                queue.put_nowait(item)
            await self._join([asyncio.create_task(self._worker(queue)) for _ in range(concurrency)])

        self.axes = compute_axes([asdict(s) for s in self.state.values()], self.campaign.thresholds)
        self.score = global_score(self.axes, self.campaign.weights)
        status = self._verdict()
        duration_ms = (time.monotonic() - self.started_at) * 1000
        from testmyapp.report.generator import generate_report
        from testmyapp.report.narrative import previous_attempts

        self.report_path = generate_report(self.run_dir, self.campaign, self.target, self.personas,
                                           self.state, self.axes, self.score, status=status,
                                           campaign_name=self.model["name"] if self.model else "",
                                           workflow=self.workflow, app_version=self.app_version,
                                           history=previous_attempts(self.bus.all_events) if self.resume else None)
        self.bus.emit("report.ready", formats=["md", "html", "json"])
        self.bus.emit("campaign.completed", score=self.score, duration_ms=duration_ms,
                      report_path=self.report_path, status=status)
        return {"score": self.score, "duration_ms": duration_ms, "report": self.report_path,
                "status": status}

    def _verdict(self) -> str:
        states = list(self.state.values())
        if self._stop_requested or len(states) != self._sessions_total:
            return "interrupted"
        if (not states or any(s.outcome in {"error", "running"} for s in states)
                or not any(s.steps_passed + s.steps_failed + s.steps_warned for s in states)):
            return "error"
        if (any(s.blocking_failure or s.outcome in {"abandoned", "blocked"} for s in states)
                or self.score is None or self.score < self.campaign.score_pass):
            return "fail"
        return "pass"

    async def _worker(self, queue: asyncio.Queue) -> None:
        while not queue.empty() and not self._stop_requested:
            await self._running_gate.wait()
            if queue.empty() or self._stop_requested:
                return
            sid, pid, scid = queue.get_nowait()
            await self._run_session(sid, pid, scid)
            self._done_count += 1

    @staticmethod
    async def _join(workers: list[asyncio.Task]) -> None:
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    @staticmethod
    def _workflow_width(pending: list[tuple[str, str, str]], dependencies: dict[str, list[str]]) -> int:
        """Nombre maximum de tâches sans relation de dépendance entre elles."""
        ancestors = {}
        for sid, direct in dependencies.items():
            ancestors[sid] = set(direct).union(*(ancestors[parent] for parent in direct))
        remaining = {sid for sid, _, _ in pending}
        matched = {}

        def pair(sid: str, seen: set[str]) -> bool:
            for parent in sorted(ancestors[sid] & remaining - seen):
                seen.add(parent)
                if parent not in matched or pair(matched[parent], seen):
                    matched[parent] = sid
                    return True
            return False

        return len(remaining) - sum(pair(sid, set()) for sid, _, _ in pending)

    async def _run_workflow(self, pending: list[tuple[str, str, str]], parents: dict[str, str],
                            dependencies: dict[str, list[str]], concurrency: int) -> None:
        slots = asyncio.Semaphore(concurrency)
        people = {pid: asyncio.Lock() for pid in self.personas}
        jobs = {}

        async def run(sid: str, pid: str, scid: str) -> None:
            prerequisites = []
            parent = parents.get(sid)
            await asyncio.gather(*(jobs[dependency] for dependency in dependencies[sid] if dependency in jobs))
            while parent:
                prerequisites.append(parent)
                parent = parents.get(parent)
            async with people[pid], slots:
                await self._running_gate.wait()
                if not self._stop_requested:
                    await self._run_session(sid, pid, scid, prerequisites, dependencies[sid])
                    self._done_count += 1

        for sid, pid, scid in pending:
            jobs[sid] = asyncio.create_task(run(sid, pid, scid))
        await self._join(list(jobs.values()))

    async def _run_session(self, sid: str, pid: str, scid: str, prerequisites: list[str] | None = None,
                           waits_for: list[str] | None = None) -> None:
        self._state_for(sid, pid, scid)
        parameters = run_parameters(self.run_dir.name)
        parent = self.campaign.depends_on.get(pid)
        scenario_name = self.scenarios[scid].name or scid
        task_info = {}
        if self.workflow:
            task_info = {"task": scid.removeprefix("decouverte-task-"),
                         "after_tasks": [self.state[key].scenario_id.removeprefix("decouverte-task-")
                                         for key in waits_for or []]}
        previous = None
        reason = ""
        abstain = "blocked"
        conditions = self.tasks.get(scid.removeprefix("decouverte-"), {}).get("conditions", {}) if self.workflow else {}
        mismatches = []
        descriptions = {"success": "devait réussir", "failure": "devait échouer", "required": "n’a pas réussi"}
        for key in waits_for or []:
            state = self.state[key]
            rule = conditions.get(state.scenario_id.removeprefix("decouverte-"), "required")
            if rule == "always" or (rule in {"success", "required"} and state.outcome == "pass"):
                continue
            if rule == "failure" and state.outcome in {"fail", "error", "abandoned"}:
                continue
            kind = "blocked" if rule == "required" and state.outcome != "skipped" else "skipped"
            task = state.scenario_id.removeprefix("decouverte-task-")
            mismatches.append((kind, f"La tâche {task} {descriptions[rule]}."))
        if mismatches:
            abstain, reason = next((item for item in mismatches if item[0] == "blocked"), mismatches[0])
        if parent:
            previous = next((state for state in self.state.values() if state.persona_id == parent), None)
            if not previous or previous.outcome != "pass":
                reason = f"La tâche préalable de {self.personas[parent].name} n’a pas réussi."
        if reason:
            self.bus.emit(f"session.{abstain}", session_id=sid, persona=pid, persona_name=self.personas[pid].name,
                          scenario_name=scenario_name, reason=reason, **task_info)
            self.bus.emit("session.completed", session_id=sid, persona=pid, outcome=abstain)
            return
        sources = [self.state[key] for key in reversed(prerequisites)] if prerequisites else [previous]
        for source in filter(None, sources):
            for event in source.evidence:
                if event["type"] == "step.passed":
                    for check in event.get("evidence", []):
                        parameters.update(check.get("detail", {}).get("proof", {}))
        attempt = 1 + sum(e["type"] == "session.started" for e in self.bus.events_of(sid))
        self.bus.emit("session.started", session_id=sid, persona=pid, scenario=scid,
                      persona_name=self.personas[pid].name,
                      scenario_name=scenario_name, target=self.target.id, speed=self.speed, attempt=attempt, **task_info)
        try:
            runner = SessionRunner(sid, self.personas[pid], self.scenarios[scid], self.target,
                                   self.campaign, self.bus, self.run_dir, attempt=attempt,
                                   task_text=self.tasks.get(pid, {}).get("text"), parameters=parameters)
            await runner.run()
        except Exception as exc:
            self.bus.emit("session.error", session_id=sid, error=str(exc))
            self.bus.emit("session.completed", session_id=sid, outcome="error")

    def _on_session_event(self, event: dict) -> None:
        sid = event.get("session_id")
        if sid in self.state:
            self._apply_event(self.state[sid], event)

    @staticmethod
    def _apply_event(state: SessionState, event: dict) -> None:
        kind = event["type"]
        if kind == "session.completed":
            for name in ("outcome", "steps_passed", "steps_failed", "steps_warned",
                         "duration_ms", "mood_final", "friction_extra_actions"):
                if name in event:
                    setattr(state, name, event[name])
            state.stability_observed = state.outcome in {"pass", "fail", "abandoned"} or state.crashed
        elif kind == "session.error":
            state.outcome = "error"
        elif kind == "persona.mood":
            state.mood = event.get("mood", state.mood)
            state.badge = event.get("emotion_badge", state.badge)
        elif kind == "persona.speech":
            state.speech = event.get("text", "")
        elif kind == "persona.emotion":
            state.emotion = event.get("emotion", "")
        elif kind == "step.failed":
            state.blocking_failure |= event.get("critical", True)
        elif kind == "target.console_error":
            state.console_errors.append(event.get("text", ""))
        elif kind == "target.metric":
            state.vitals[event["metric"]] = event["value"]
        elif kind in {"a11y.violation", "a11y.review"}:
            rule = {key: event.get(key) for key in ("rule", "severity", "selector", "url", "wcag", "source")}
            findings = state.a11y_violations if kind == "a11y.violation" else state.a11y_review
            if rule not in findings:
                findings.append(rule)
        elif kind == "a11y.page_scanned":
            state.a11y_scanned = True
            url = event.get("url", "")
            if url not in state.a11y_pages:
                state.a11y_pages.append(url)
        elif kind == "target.crash":
            state.crashed = True
        elif kind == "target.freeze":
            state.freezes_ms.append(event["duration_ms"])
        elif kind == "step.screenshot":
            state.screenshots.append(event.get("path", ""))
        if (kind.startswith(("step.", "target.", "a11y.", "action."))
                or kind in {"session.started", "session.completed", "session.error", "session.blocked",
                            "session.skipped", "persona.emotion"}):
            state.evidence.append(event)

    def _state_for(self, sid: str, pid: str, scid: str) -> SessionState:
        if sid not in self.state:
            self.state[sid] = SessionState(session_id=sid, persona_id=pid, scenario_id=scid,
                                           target_id=self.target.id)
        return self.state[sid]

    # ------------------------------------------------------- pilotage live
    async def control(self, command: str) -> bool:
        """La pause bloque les nouvelles sessions. L'arrêt laisse finir les sessions actives."""
        if not self._active or self._stop_requested:
            return False
        if command == "pause":
            self._running_gate.clear()
        elif command == "resume":
            self._running_gate.set()
        elif command == "stop":
            self._stop_requested = True
            self._running_gate.set()
        else:
            return False
        self.bus.emit("campaign.control", command=command)
        return True
