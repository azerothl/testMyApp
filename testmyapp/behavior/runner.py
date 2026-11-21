"""Exécution d'une session : étapes scriptées, exploratoires et assistées
(spec §4.4, §7.3).

Le runner module chaque action par le persona (délais, fautes de frappe,
misclicks, comportement on_error), évalue les attentes, met à jour
l'humeur/les émotions et émet tous les événements du bus. Aucune décision
driver-side : le runner pilote, le driver exécute.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from testmyapp.behavior.agent import AgentChoice
from testmyapp.config import Campaign, Expectation, Persona, Scenario, Step, Target
from testmyapp.core.events import EventBus
from testmyapp.core.rng import make_rng
from testmyapp.drivers.base import ActionResult, Driver, create_driver, public_url
from testmyapp.personas.emotions import VALENCE, MoodTracker, mood_badge
from testmyapp.behavior.discovery import choose_control, message_from_goal, normalize, task_for_tomorrow, task_transition
from testmyapp.core.proofs import check_records, expand

SPEED_MULTIPLIER = {"realtime": 1.0, "fast": 0.3, "turbo": 0.06}
ACTION_BASE_S = {"navigate": 0.8, "click": 0.9, "type": 1.6, "press": 0.5,
                 "select": 1.0, "check": 0.6, "scroll": 0.5, "hover": 0.4,
                 "wait_for": 0.3, "tap": 0.9}


@dataclass
class SessionResult:
    session_id: str
    persona_id: str
    scenario_id: str
    outcome: str = "pass"  # pass | fail | abandoned | error
    steps_passed: int = 0
    steps_failed: int = 0
    steps_warned: int = 0
    duration_ms: float = 0.0
    mood_final: float = 0.0
    friction_extra_actions: int = 0  # actions superflues (hesitations/aide/détours)
    evidence: list[dict] = field(default_factory=list)


class SessionRunner:
    """Une session = persona × scénario × cible (spec §3)."""

    def __init__(self, session_id: str, persona: Persona, scenario: Scenario,
                 target: Target, campaign: Campaign, bus: EventBus, run_dir: Path, *, attempt: int = 1,
                 task_text: str | None = None, parameters: dict | None = None) -> None:
        self.session_id = session_id
        self.persona = persona
        self.scenario = scenario
        self.task_text = task_text if task_text is not None else scenario.task_text
        self.parameters = parameters or {}
        self.target = target
        self.campaign = campaign
        self.bus = bus
        self.run_dir = Path(run_dir)
        self.artifact_prefix = session_id if attempt == 1 else f"{session_id}-attempt{attempt}"
        self.attempt = attempt
        self.rng = make_rng(campaign.seed, persona.id, scenario.id, session_id)
        self.mood = MoodTracker(persona.emotion_bias, persona.emotion_volatility)
        self.speed_mult = SPEED_MULTIPLIER.get(campaign.speed, 1.0)
        self.result = SessionResult(session_id, persona.id, scenario.id)
        self._driver: Driver | None = None
        self._reported_console = 0
        self._reported_violations: set[tuple[str, str, str]] = set()
        self._reported_scans: set[str] = set()
        self._reported_metrics: dict[str, float] = {}
        self._reported_requests = 0
        self._reported_review: set[tuple[str, str, str]] = set()
        self._reported_diagnostics: set[str] = set()
        self._reported_focus: set[tuple[str, str]] = set()
        self._reported_freezes = 0
        self._reported_samples = 0
        private_values = {action["text"] for step in scenario.steps for action in step.actions
                          if isinstance(action.get("text"), str) and action["text"]}
        if self.task_text:
            private_values.add(self.task_text)
        self.agent = (AgentChoice(campaign.agent, private_values)
                      if campaign.ai_mode and campaign.agent and target.type in {"web", "sim"} else None)

    # ------------------------------------------------------------------ util
    def _emit(self, etype: str, **fields: Any) -> None:
        self.bus.emit(etype, session_id=self.session_id, **fields)

    def _persona_delay(self, action_type: str) -> float:
        """Délai humain : base × speed.factor × bruit lognormal × vitesse campagne."""
        base = ACTION_BASE_S.get(action_type, 0.8) * self.persona.speed_factor
        noise = math.exp(self.rng.gauss(0, 0.25))
        return max(0.02, base * noise * self.speed_mult)

    def _emotion(self, punctual_id: str) -> None:
        emo = self.mood.punctual(punctual_id)
        if emo:
            self._emit("persona.emotion", emotion=emo["id"], label=emo["label"],
                       emoji=emo["emoji"], count=emo.get("count", 1), cause=emo["id"])

    def _apply_valence(self, event_key: str) -> None:
        v = VALENCE.get(event_key, 0.0)
        self.mood.apply(v)
        badge = mood_badge(self.mood.mood)
        self._emit("persona.mood", mood=round(self.mood.mood, 2),
                   emotion_badge=badge["id"], badge_label=badge["label"], badge_emoji=badge["emoji"])

    def _speak(self, text: str) -> None:
        self._emit("persona.speech", text=text, ttl_ms=4000)

    # ------------------------------------------------------------------ run
    async def run(self) -> SessionResult:
        t0 = time.perf_counter()
        try:
            self._driver = create_driver(
                {"type": self.target.type, "base_url": self.target.base_url,
                 "browser": self.target.browser or self.persona.browser,
                 "executable": self.target.executable, "window_title": self.target.window_title,
                 "args": self.target.args, "templates": self.target.templates,
                 "storage_state": self.target.storage_state,
                 "free_discovery": any(step.mode == "discovery" for step in self.scenario.steps),
                 "discovery_bridge": self.target.discovery_bridge and any(
                     step.mode == "discovery" for step in self.scenario.steps),
                 "trace": self.campaign.trace, "video": self.campaign.video,
                 "artifact_prefix": self.artifact_prefix},
                {"screen_w": self.persona.screen_w, "screen_h": self.persona.screen_h,
                 "dpr": self.persona.dpr, "input_device": self.persona.interaction_input,
                 "motor": self.persona.motor, "timezone": self.persona.timezone,
                 "device_type": self.persona.device_type, "network_profile": self.persona.network_profile,
                 "cpu_throttle": self.persona.cpu_throttle, "locale": self.persona.locale,
                 "browser": self.persona.browser},
                self.run_dir, self.session_id)
            await self._driver.launch()
            environment = getattr(self._driver, "environment", None)
            if isinstance(environment, dict):
                self._emit("target.environment", **environment)
        except asyncio.CancelledError:
            await self._close_driver()
            raise
        except Exception as exc:
            self.result.outcome = "error"
            self._emit("session.error", error=f"lancement impossible : {exc}")
            await self._close_driver()
            self._finish(t0)
            return self.result

        first_intent = self.scenario.steps[0].intent if self.scenario.steps else "commencer"
        self._speak(f"Objectif : {expand(first_intent, self.parameters)}")
        try:
            if not await self._recover_committed_task():
                for index, step in enumerate(self.scenario.steps):
                    previous_failures = self.result.steps_failed
                    await self._run_step(index, step)
                    if self.result.outcome in {"abandoned", "error"}:
                        break
                    if step.stop_on_failure and self.result.steps_failed > previous_failures:
                        for skipped, pending in enumerate(self.scenario.steps[index + 1:], index + 1):
                            self._emit("step.skipped", step_id=skipped, intent=expand(pending.intent, self.parameters))
                        break
            if self.result.outcome == "pass" and self.result.steps_failed == 0:
                self._apply_valence("goal_reached")
                self._emotion("celebrate")
                self._speak("C'est terminé, j'ai réussi !")
            elif self.result.outcome != "abandoned":
                self._speak("Je n'ai pas réussi à tout faire…")
        except Exception as exc:
            self.result.outcome = "error"
            self._emit("session.error", error=str(exc))
        finally:
            await self._close_driver()
        self._finish(t0)
        return self.result

    async def _recover_committed_task(self) -> bool:
        steps = self.scenario.steps
        if self.target.type != "web" or len(steps) != 2 or not steps[0].stop_on_failure:
            return False
        final = steps[1]
        intent = expand(final.intent, self.parameters)
        transition = task_transition(intent)
        if self.attempt < 2 and not (transition and transition[1] == "en cours" and self.parameters.get("task_id")):
            return False
        checks = [check for check in final.expects if check.kind == "api_record"]
        if not checks or not (task_for_tomorrow(intent) or transition) or final.mode != "discovery":
            return False
        for check in checks:
            fields = check.api["match"]
            if not ({"id", "status"} <= fields.keys() or {"title", "date", "type"} <= fields.keys()):
                return False
        evidence = []
        for check in checks:
            success, detail = await self._check_expect(check)
            if not success:
                return False
            evidence.append({"expect": check.kind, "ok": True,
                             "detail": {**detail, "recovered": "retry" if self.attempt > 1 else "existing"}})
        self._emit("step.skipped", step_id=0, intent=expand(steps[0].intent, self.parameters),
                   reason="Le résultat est déjà enregistré dans l’application.")
        self._emit("step.started", step_id=1, intent=intent, mode=final.mode, critical=final.critical)
        self._emit("step.passed", step_id=1, duration_ms=0, evidence=evidence)
        self.result.steps_passed += 1
        if final.screenshot in {"after", "both"}:
            await self._capture(1, "after")
        await self._collect_observations(1)
        return True

    async def _close_driver(self) -> None:
        if self._driver is None:
            return
        try:
            await self._driver.stop()
        except Exception as exc:
            self.result.outcome = "error"
            self._emit("session.error", error=f"fermeture impossible : {exc}")

    def _finish(self, t0: float) -> None:
        self.result.duration_ms = (time.perf_counter() - t0) * 1000
        self.result.mood_final = round(self.mood.mood, 2)
        outcome = self.result.outcome
        if outcome == "pass" and (self.result.steps_failed > 0 or self.result.steps_warned > 0):
            outcome = self.result.outcome = "fail" if self.result.steps_failed else "pass"
        self._emit("session.completed", outcome=outcome, persona=self.persona.id,
                   scenario=self.scenario.id, target=self.target.id,
                   steps_passed=self.result.steps_passed, steps_failed=self.result.steps_failed,
                   steps_warned=self.result.steps_warned, duration_ms=self.result.duration_ms,
                   mood_final=self.result.mood_final,
                   friction_extra_actions=self.result.friction_extra_actions)

    # ----------------------------------------------------------------- étape
    async def _run_step(self, index: int, step: Step) -> None:
        step = replace(step, intent=expand(step.intent, self.parameters),
                       expects=[replace(expect, selector=expand(expect.selector, self.parameters),
                                        failure_text=expand(expect.failure_text, self.parameters))
                                for expect in step.expects])
        self._emit("step.started", step_id=index, intent=step.intent, mode=step.mode, critical=step.critical,
                   **({"precondition": True} if step.stop_on_failure else {}))
        self._speak(step.intent)
        t_start = time.perf_counter()
        if step.screenshot in {"before", "both"}:
            await self._capture(index, "before")

        failed_action = None
        retry_after = None
        async for action in self._step_actions(step, index):
            delay = self._persona_delay(str(action.get("type", "")))
            await asyncio.sleep(delay)
            outcome = await self._perform_with_persona(action)
            self.result.friction_extra_actions += outcome.retry_used
            decision = outcome.action_result.detail.get("decision") or action.get("decision")
            self._emit("action.performed", step_id=index, action=outcome.action_result.action,
                       ok=outcome.action_result.ok, duration_ms=outcome.action_result.duration_ms,
                       simulated_delay_ms=round(delay * 1000), simulated_errors=outcome.retry_used,
                       intent_target=action.get("name") or action.get("selector_pref") or action.get("selector") or action.get("template"),
                       **{field: outcome.action_result.detail[field]
                          for field in ("resolution", "image_match", "interaction") if field in outcome.action_result.detail},
                       **({"decision": decision} if decision else {}),
                       **({"retry_after": retry_after} if retry_after else {}),
                       explored=step.mode != "scripted")
            if outcome.action_result.ok:
                retry_after = None
                continue
            if step.mode == "discovery":
                retry_after = action.get("name") or "la tentative précédente"
                continue
            handled = await self._on_error(action, outcome.action_result)
            if handled == "abandon":
                self._abandon(step, index, failed_action=outcome.action_result)
                if step.screenshot in {"after", "both"}:
                    await self._capture(index, "after")
                await self._collect_observations(index)
                return
            if handled != "recovered":
                failed_action = outcome.action_result
                break

        # attentes
        verdict = "passed"
        evidence: list[dict] = []
        if failed_action is None:
            for expect in step.expects:
                exp_ok, exp_detail = await self._check_expect(expect, t_start)
                if not exp_ok:
                    verdict = "failed"
                elif exp_detail.get("warn"):
                    if verdict == "passed":
                        verdict = "warned"
                evidence.append({"expect": expect.kind, "ok": exp_ok, "detail": exp_detail})
        else:
            verdict = "failed"
            evidence.append({"action_error": failed_action.error or "échec d'action",
                             "action": failed_action.action,
                             "target": failed_action.detail.get("target", ""),
                             "url": failed_action.detail.get("url", "")})

        duration = (time.perf_counter() - t_start) * 1000
        expected_ms = step.baseline_seconds * self.persona.speed_factor * 1000
        if verdict == "failed":
            self.result.steps_failed += 1
            self._emit("step.failed", step_id=index, critical=step.critical,
                       duration_ms=duration, evidence=evidence)
            self._apply_valence("step_fail")
            self._emotion("confus" if self.persona.help_seeking > 0.5 else "agace")
            self._speak(f"Ça ne fonctionne pas : {evidence[0].get('action_error', 'attente non remplie')}")
        elif verdict == "warned":
            self.result.steps_warned += 1
            self._emit("step.warned", step_id=index, duration_ms=duration, evidence=evidence)
            self._apply_valence("step_warn")
        else:
            self.result.steps_passed += 1
            fast = duration < expected_ms * 0.6
            slow = duration > expected_ms * 2.0
            key = "step_fast_pass" if fast else ("step_slow_pass" if slow else "step_pass")
            self._emit("step.passed", step_id=index, duration_ms=duration,
                       evidence=evidence, fast=fast, slow=slow)
            self._apply_valence(key)
            if slow:
                self._emotion("impatient")
                self._speak("C'était long, mais c'est passé.")
        # screenshot de fin d'étape (§7.3)
        if step.screenshot in {"after", "both"}:
            await self._capture(index, "after")
        # observations collectées à chaque étape (§7.3.5) : console, vitals, a11y
        await self._collect_observations(index)

    async def _capture(self, index: int, phase: str) -> None:
        shot = await self._driver.screenshot(f"{self.artifact_prefix}-step{index + 1}-{phase}")
        if shot:
            self._emit("step.screenshot", step_id=index, phase=phase,
                       path=shot.relative_to(self.run_dir).as_posix())

    async def _collect_observations(self, step_id: int) -> None:
        """Relève console/réseau/CWV/a11y et les émet sur le bus (§8)."""
        try:
            obs = await self._driver.observe()
        except Exception:
            return
        console = obs.get("console_errors", [])
        for err in console[self._reported_console:]:
            self._emit("target.console_error", step_id=step_id, level=err.get("level", "error"),
                       text=str(err.get("text", ""))[:400], source=err.get("source", ""))
            self._apply_valence("console_error")
        self._reported_console = len(console)
        url = obs.get("url", "")
        persona_affected = self.persona.vision != "normal" or self.persona.motor != "normal" \
            or self.persona.input_device in {"touch", "keyboard"} or self.persona.screen_reader
        for key_name, event_type, seen in (("a11y_violations", "a11y.violation", self._reported_violations),
                                            ("a11y_review", "a11y.review", self._reported_review)):
            for violation in obs.get(key_name, []):
                rule, selector = violation["rule"], str(violation.get("selector", ""))
                page = violation.get("url", url)
                key = (page, rule, selector)
                if key in seen:
                    continue
                seen.add(key)
                self._emit(event_type, step_id=step_id, url=page, rule=rule,
                           severity=violation.get("severity", "moderate"), selector=selector,
                           impact=violation.get("impact"), wcag=violation.get("wcag", []),
                           source=violation.get("source", self.target.type),
                           persona_affected=persona_affected)
                if persona_affected and event_type == "a11y.violation":
                    self._apply_valence("a11y_violation")
        for page in obs.get("a11y_pages", [url] if obs.get("a11y_scanned") else []):
            if page not in self._reported_scans:
                self._reported_scans.add(page)
                self._emit("a11y.page_scanned", step_id=step_id, url=page)
        for key, value in obs.get("vitals", {}).items():
            if self._reported_metrics.get(key) == value:
                continue
            self._reported_metrics[key] = value
            self._emit("target.metric", step_id=step_id, metric=key, value=float(value),
                       thresholds=self.campaign.thresholds)
        requests = obs.get("web_requests", [])
        for request in requests[self._reported_requests:]:
            self._emit("target.network", step_id=step_id, **request)
        self._reported_requests = len(requests)
        for diagnostic in obs.get("observation_errors", []):
            if diagnostic not in self._reported_diagnostics:
                self._reported_diagnostics.add(diagnostic)
                self._emit("target.observation_error", step_id=step_id, detail=diagnostic)
        for focus in obs.get("focus_path", []):
            key = (focus.get("url", url), focus["selector"])
            if key not in self._reported_focus:
                self._reported_focus.add(key)
                self._emit("target.focus", step_id=step_id, **focus)
        freezes = obs.get("desktop_freezes", [])
        for duration in freezes[self._reported_freezes:]:
            self._emit("target.freeze", step_id=step_id, duration_ms=duration)
            self._apply_valence("crash")
            self._emotion("impatient")
        self._reported_freezes = len(freezes)
        samples = obs.get("desktop_samples", [])
        for sample in samples[self._reported_samples:]:
            self._emit("target.desktop_sample", step_id=step_id, **sample)
        self._reported_samples = len(samples)
        health = await self._driver.health()
        if health.get("crashed"):
            self._emit("target.crash", exit_code=health.get("exit_code"))
            self._apply_valence("crash")
            self._emotion("furieux")
            self.result.outcome = "error"

    async def _perform_with_persona(self, action: dict[str, Any]) -> "ActionOutcome":
        """Une action, modulée : fautes de frappe, misclicks (§4.4, §4.5)."""
        driver = self._driver
        atype = str(action.get("type", ""))
        retry_used = 0

        # faute de frappe simulée puis corrigée
        if atype == "type" and self.rng.random() < self.persona.error_proneness:
            text = str(action.get("text", ""))
            if len(text) > 3:
                typo = text[:-1] + (text[-1].upper() if text[-1].islower() else text[-1])
                await driver.perform({**action, "text": typo})
                await asyncio.sleep(0.4 * self.speed_mult)
                self._speak("Oups, une faute de frappe…")
                retry_used += 1

        # misclick : clic à côté puis re-ciblage
        if atype in {"click", "tap"} and self.rng.random() < self.persona.error_proneness * 0.5:
            self._speak("Je n'ai pas cliqué au bon endroit.")
            await asyncio.sleep(0.5 * self.speed_mult)
            retry_used += 1

        result = await driver.perform(action)
        return ActionOutcome(action_result=result, retry_used=retry_used)

    async def _on_error(self, action: dict[str, Any], failed: ActionResult) -> str:
        """Comportement on_error pondéré du persona (§4.1, §4.5).

        Retourne 'recovered' (réussi après réessai), 'failed' ou 'abandon'.
        """
        weights = self.persona.on_error or {}
        choice = self.rng.choices(
            list(weights.keys()) or ["retry"], list(weights.values()) or [1.0], k=1
        )[0]
        if choice == "retry":
            for attempt in range(self.persona.retries):
                self.result.friction_extra_actions += 1
                await asyncio.sleep(self._persona_delay("click"))
                retry = await self._driver.perform(action)
                self._emit("action.retry", attempt=attempt + 1, action=failed.action, ok=retry.ok)
                if retry.ok:
                    return "recovered"
            return "failed"
        if choice == "explore":
            self.result.friction_extra_actions += 1
            self._speak("Je cherche une autre façon de faire…")
            await self._driver.screenshot(f"{self.artifact_prefix}-explore-{int(time.time())}")
            await asyncio.sleep(1.2 * self.speed_mult)
            retry = await self._driver.perform(action)
            return "recovered" if retry.ok else "failed"
        if choice == "ask_help":
            self.result.friction_extra_actions += 1
            self._speak("Je cherche de l'aide…")
            await self._driver.perform({"type": "scroll", "dy": 500})
            await asyncio.sleep(1.5 * self.persona.speed_factor * self.speed_mult)
            return "failed"
        return "abandon"  # spec §4.5 : abandon = signal UX fort

    async def _check_expect(self, expect: Expectation, t_start: float | None = None) -> tuple[bool, dict]:
        driver = self._driver
        if expect.kind == "api_record":
            endpoint = expand(expect.api["endpoint"], self.parameters)
            detail = {"endpoint": endpoint.partition("?")[0], "expected_count": expect.api.get("count", 1)}
            if self.target.type != "web":
                return False, {**detail, "error": "Cette vérification exige une cible Web."}
            try:
                return check_records(await driver.read_json(endpoint, expect.timeout_ms),
                                     {**expect.api, "endpoint": endpoint}, self.parameters)
            except Exception:
                return False, {**detail, "error": "Les données de l’application sont indisponibles."}
        if expect.kind == "text_contains":
            deadline = time.monotonic() + expect.timeout_ms / 1000
            while True:
                snapshot = await driver.snapshot()
                if self._goal_matches(expect, snapshot):
                    return True, {"selector": expect.selector, "resolution": "observed"}
                if self._visible_failure([expect], snapshot):
                    return False, {"selector": expect.selector, "observed_failure": expect.failure_text}
                if time.monotonic() >= deadline:
                    return False, {"selector": expect.selector}
                await asyncio.sleep(.18)
        if expect.kind == "url_matches":
            if self.target.type == "web":
                result = await driver.perform({"type": "wait_for_url", "pattern": expect.pattern,
                                               "timeout_ms": expect.timeout_ms})
                return result.ok, {"url": result.detail.get("url", ""), "error": result.error}
            snap = await driver.snapshot()
            ok = expect.pattern in (snap.url or "")
            return ok, {"url": public_url(snap.url)}
        if expect.kind == "visible":
            snap = await driver.snapshot()
            name = expect.selector
            matched = any(name.casefold() == (el.get("name") or "").casefold()
                          and el.get("visible", True) for el in snap.elements)
            if matched:
                return True, {"selector": name}
            result = await driver.perform({"type": "wait_for", "selector": name,
                                           "timeout_ms": expect.timeout_ms})
            return result.ok, {"selector": name, "error": result.error, **result.detail}
        if expect.kind == "metric_below":
            if expect.metric == "step.duration_ms" and t_start is not None:
                value = (time.perf_counter() - t_start) * 1000
            else:
                value = self._metric_value(expect.metric, await driver.observe())
                if value is None and self.target.type == "web":
                    # L'observateur de peinture publie après le chargement du document.
                    deadline = time.monotonic() + expect.timeout_ms / 1000
                    while value is None and time.monotonic() < deadline:
                        await asyncio.sleep(min(.1, max(0, deadline - time.monotonic())))
                        value = self._metric_value(expect.metric, await driver.observe())
            ok = value is not None and value <= expect.max
            return ok, {"metric": expect.metric, "value": value, "max": expect.max,
                        "measured": value is not None,
                        "warn": value is not None and value > expect.max * 0.8}
        if expect.kind == "element_count":
            snap = await driver.snapshot()
            return len(snap.elements) >= expect.min_count, {"count": len(snap.elements)}
        raise ValueError(f"type d'attente inconnu : {expect.kind}")

    def _metric_value(self, metric: str, obs: dict) -> float | None:
        short = metric.split(".")[-1]  # step.duration_ms -> duration_ms
        if short in obs:
            return float(obs[short])
        vitals = obs.get("vitals", {})
        value = vitals.get(f"{short}::{obs.get('url', '')}", vitals.get(short))
        return float(value) if value is not None else None

    # ------------------------------------------------------------- M2 explore
    async def _step_actions(self, step: Step, step_id: int) -> AsyncIterator[dict]:
        """Choisit l'action suivante après l'exécution de la précédente."""
        if step.mode == "discovery":
            async for action in self._discover(step):
                yield action
            return
        if step.mode == "scripted":
            actions = step.action_paths.get(f"persona:{self.persona.id}")
            if actions is None:
                actions = step.action_paths.get(f"input:{self.persona.interaction_input}", step.actions)
            for action in actions:
                if action.get("type") == "type" and action.get("text") == "{task.text}" and self.task_text is not None:
                    yield {**action, "text": self.task_text}
                else:
                    yield action
            return
        goal_text = step.intent + " " + " ".join(self.persona.goals_primary)
        keywords = [w for w in goal_text.lower().replace(",", " ").split() if len(w) > 3]
        visited: set[tuple[str, str]] = set()
        for i in range(step.explore_budget):
            snap = await self._driver.snapshot()
            goals = [exp for exp in step.expects if exp.kind in {"url_matches", "visible", "element_count"}]
            if goals and all(self._goal_matches(exp, snap) for exp in goals):
                if i:
                    self._emotion("eureka")
                return
            candidates = [el for el in snap.elements if el.get("visible", True) and el.get("name")
                          and el.get("role") in {"button", "link"}
                          and (snap.url, el["name"]) not in visited]
            if not candidates:
                break
            scored = []
            for el in candidates:
                name_l = (el.get("name") or "").lower()
                score = sum(1 for kw in keywords if kw in name_l) * 2.0
                if any(known.casefold() in name_l for known in self.persona.knowledge if known.strip()):
                    score += self.persona.tech_savviness
                if any(word in name_l.split() for word in ("aide", "assistance", "help", "faq")):
                    score += self.persona.help_seeking
                if any(m.lower() in name_l for m in self.persona.misconceptions):
                    score += 0.8 * self.persona.curiosity  # méconnaissance : détour possible
                score += self.rng.uniform(0, 0.2 + 0.6 * self.persona.curiosity)
                scored.append((score, el))
            scored.sort(key=lambda pair: -pair[0])
            best = scored[0][1]
            if step.mode == "agent":
                candidates = [el for _, el in scored[:24]]
                choice, telemetry = (await self.agent.choose(goal_text, candidates) if self.agent else
                                     (None, {"source": "weighted", "reason": "disabled"}))
                if choice is not None:
                    best = candidates[choice]
                self._emit("action.ai_decision", step_id=step_id, **telemetry)
            visited.add((snap.url, best["name"]))
            self.result.friction_extra_actions += 1
            yield {"type": "click", "name": best["name"]}

    async def _discover(self, step: Step) -> AsyncIterator[dict]:
        visited = set()
        goals = [expect for expect in step.expects if expect.kind != "api_record"]
        transition = task_transition(step.intent) if self.target.type == "web" and self.parameters.get("task_id") else None
        if transition:
            async for action in self._discover_transition(step, goals, *transition):
                yield action
            return
        message = message_from_goal(step.intent)
        creation = task_for_tomorrow(step.intent) if self.target.type == "web" else None
        if creation:
            day = self.parameters.get("tomorrow")
            tomorrow = day or (datetime.now(ZoneInfo(self.persona.timezone or "Europe/Paris")).date()
                               + timedelta(days=1)).isoformat()
            message = f"Ajoute une tâche {creation} {day or 'demain'}"
        sent = False
        typed = False
        opened_assistant = False
        needs_conversation = "nouvelle session" in normalize(step.intent)
        previous_url = None
        for _ in range(step.explore_budget):
            await asyncio.sleep(.22)
            snapshot = await self._driver.snapshot()
            if self._visible_failure(goals, snapshot):
                return
            if previous_url is not None and snapshot.url != previous_url:
                # A new page can render navigation before any asynchronous result.
                for _ in range(6):
                    if goals and all(self._goal_matches(exp, snapshot) for exp in goals):
                        return
                    await asyncio.sleep(.18)
                    snapshot = await self._driver.snapshot()
            previous_url = snapshot.url
            if goals and all(self._goal_matches(exp, snapshot) for exp in goals):
                return
            elements = snapshot.elements
            entry = next((item for item in elements if item["role"] == "entry"), None)
            send = next((item for item in elements if item["role"] == "button"
                         and normalize(item["name"]) in {"envoyer", "send"}), None)
            new_session = next((item for item in elements if item["role"] == "button"
                                and normalize(item["name"]) == "nouvelle session"), None)
            if needs_conversation and new_session:
                needs_conversation = False
                yield {"type": "observed_click", "control_id": new_session["id"],
                       "name": new_session["name"], "decision": "label"}
                continue
            if creation and sent:
                confirm = next((item for item in elements if item["role"] == "button"
                                and normalize(item["name"]) in {"confirmer", "valider"}
                                and normalize(f"Créer tâche {creation} {tomorrow}")
                                in normalize(item.get("context") or "")), None)
                if confirm:
                    yield {"type": "observed_click", "control_id": confirm["id"],
                           "name": confirm["name"], "decision": "confirm_task"}
                    break
                continue
            if creation and not (entry and send) and not opened_assistant:
                assistant = next((item for item in elements if item["role"] == "button"
                                  and normalize(item["name"]) == "assistant"), None)
                if assistant:
                    opened_assistant = True
                    yield {"type": "observed_click", "control_id": assistant["id"],
                           "name": assistant["name"], "decision": "assistant"}
                    continue
            if message and entry and send and not sent:
                if not typed:
                    typed = True
                    yield {"type": "observed_type", "control_id": entry["id"], "name": entry["name"],
                           "text": message, "decision": "fill"}
                    continue
                if entry["state"] == "filled":
                    sent = True
                    if self.target.type == "desktop" and self.persona.interaction_input == "keyboard" and entry["focused"]:
                        yield {"type": "hotkey", "keys": "{ENTER}", "name": send["name"], "decision": "send"}
                    else:
                        yield {"type": "observed_click", "control_id": send["id"],
                               "name": send["name"], "decision": "send"}
                    if creation:
                        continue
                    break
            control = choose_control(elements, step.intent, self.persona, visited)
            if control:
                yield {"type": "observed_click", "control_id": control["id"],
                       "name": control["name"], "decision": control["decision"]}
            elif elements:
                return

    async def _discover_transition(self, step: Step, goals: list[Expectation],
                                   title: str, status: str) -> AsyncIterator[dict]:
        """Suit une tâche déjà identifiée par la preuve de sa création."""
        phase = "find"
        periods = 0
        for _ in range(step.explore_budget):
            await asyncio.sleep(.25)
            screen = await self._driver.snapshot()
            if self._visible_failure(goals, screen) or (goals and all(self._goal_matches(goal, screen) for goal in goals)):
                return
            controls = [item for item in screen.elements if item["role"] in {"button", "select"}]
            buttons = {normalize(item["name"]): item for item in controls if item["role"] == "button"}
            action, decision = None, ""
            if phase == "find":
                action = next((item for item in controls if item.get("headline", "").strip() == title), None)
                if action:
                    phase, decision = "detail", "shared_task"
                elif periods == 0:
                    action = buttons.get("periode suivante")
                    if action:
                        periods, decision = 1, "next_period"
            elif phase == "detail":
                label = "modifier" if status == "en cours" else "terminer"
                action = next((item for item in controls if normalize(item["name"]) == label
                               and item.get("dialog", "").strip() == title), None)
                if action:
                    phase, decision = ("edit" if status == "en cours" else "confirm"), "choose_status"
            elif phase == "edit":
                field = next((item for item in controls if item["role"] == "select"
                              and normalize(item["name"]) == "etat"
                              and "En cours" in item.get("options", [])), None)
                if field:
                    phase = "submit"
                    yield {"type": "observed_select", "control_id": field["id"], "name": field["name"],
                           "label": "En cours", "decision": "select_in_progress"}
                    continue
            elif phase == "submit":
                action = buttons.get(normalize("Demander à l’assistant"))
                if action:
                    phase, decision = "confirm", "submit_status"
            elif phase == "confirm":
                proposal = buttons.get("confirmer")
                context = normalize(proposal.get("context") or "") if proposal else ""
                signature = f"Modifier {title}" if status == "en cours" else f"Marquer {title} comme terminée"
                state_change = "etat a faire en cours" if status == "en cours" else ""
                if proposal and normalize(signature) in context and state_change in context:
                    action, phase, decision = proposal, "wait", "confirm_status"
            if action:
                yield {"type": "observed_click", "control_id": action["id"], "name": action["name"], "decision": decision}

    @staticmethod
    def _visible_failure(goals: list[Expectation], snapshot) -> bool:
        return any(expect.failure_text and SessionRunner._goal_matches(
            replace(expect, selector=expect.failure_text), snapshot) for expect in goals)

    @staticmethod
    def _goal_matches(expect: Expectation, snap) -> bool:
        if expect.kind == "text_contains":
            expected = normalize(expect.selector)
            prefix, _, suffix = expected.rpartition(" ")
            states = {"coche": "checked", "cochee": "checked", "decoche": "unchecked", "decochee": "unchecked",
                      "selectionne": "selected", "selectionnee": "selected"}
            if prefix and suffix in states:
                return any(normalize(item["name"]) == prefix and item.get("state") == states[suffix]
                           for item in snap.elements)
            return bool(expected) and any(expected in normalize(item["name"])
                                          for item in snap.elements if item["role"] != "entry")
        if expect.kind == "url_matches":
            return expect.pattern in snap.url
        if expect.kind == "element_count":
            return len(snap.elements) >= expect.min_count
        return any(el.get("visible", True) and (el.get("name") or "").casefold() == expect.selector.casefold()
                   for el in snap.elements)

    def _abandon(self, step: Step, index: int, failed_action: ActionResult) -> None:
        self.result.outcome = "abandoned"
        self.result.steps_failed += 1
        self._emit("step.failed", step_id=index, critical=step.critical, duration_ms=0,
                   evidence=[{"action_error": failed_action.error, "abandon": True}])
        self._apply_valence("abandon")
        self._emotion("abandonne")
        self._speak("J'abandonne, c'est trop compliqué.")


@dataclass
class ActionOutcome:
    action_result: ActionResult
    retry_used: int = 0
