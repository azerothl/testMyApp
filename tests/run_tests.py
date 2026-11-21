"""Tests TestMyApp (stdlib unittest, zéro dépendance).

Lancement : ``python tests/run_tests.py`` ou ``python -m unittest discover tests``.
Couvre : personas (roundtrip TOML, variantes, validation), émotions (EMA,
cooldown), drivers sim, comportement M1 (modulation persona), moteur
(reproductibilité par graine), scoring, rapport (constats + reco).
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testmyapp.config import (Campaign, Expectation, Scenario, Step, Target,  # noqa: E402
                              persona_from_toml)
from testmyapp.core.events import EventBus  # noqa: E402
from testmyapp.core.rng import derive_seed, make_rng  # noqa: E402
from testmyapp.core.scoring import compute_axes, global_score  # noqa: E402
from testmyapp.drivers.sim import SimDriver  # noqa: E402
from testmyapp.behavior.runner import SessionRunner  # noqa: E402
from testmyapp.personas.emotions import MoodTracker, mood_badge  # noqa: E402
from testmyapp.personas.library import (builtin_personas, generate_variants,  # noqa: E402
                                        render_toml)
from testmyapp.report.generator import build_findings, build_recommendations  # noqa: E402


class TestPersonas(unittest.TestCase):
    def test_bibliotheque_8_archetypes(self):
        lib = builtin_personas()
        self.assertEqual(len(lib), 8)
        for pid in ("nadine", "eric", "paula", "marc", "dany", "karim", "mona", "alice"):
            self.assertIn(pid, lib)
        self.assertEqual(lib["nadine"].tech_savviness, 0.1)
        self.assertEqual(lib["dany"].vision, "deuteranopia")
        self.assertEqual(lib["karim"].motor, "keyboard_only")
        self.assertEqual(lib["mona"].device_type, "phone")

    def test_roundtrip_toml(self):
        p = builtin_personas()["marc"]
        data = tomllib.loads(render_toml(p))
        p2 = persona_from_toml(data)
        self.assertEqual(p2.id, p.id)
        self.assertAlmostEqual(p2.tech_savviness, p.tech_savviness)
        self.assertEqual(p2.on_error, p.on_error)
        self.assertEqual(p2.goals_primary, p.goals_primary)

    def test_variants_reproductibles(self):
        base = builtin_personas()["eric"]
        v1 = generate_variants(base, 3, seed=7)
        v2 = generate_variants(base, 3, seed=7)
        self.assertEqual([x.name for x in v1], [x.name for x in v2])
        self.assertEqual([x.patience for x in v1], [x.patience for x in v2])

    def test_retries_derive(self):
        p = builtin_personas()["nadine"]  # persistence 0.9 → 5 réessais
        self.assertEqual(p.retries, 5)


class TestEmotions(unittest.TestCase):
    def test_ema_bornes(self):
        m = MoodTracker(bias=0.0, volatility=1.0)
        for _ in range(20):
            m.apply(-5.0)
        self.assertGreaterEqual(m.mood, -5.0)
        self.assertLessEqual(m.mood, 5.0)
        self.assertLess(m.mood, 0)

    def test_badges_humeur(self):
        self.assertEqual(mood_badge(4)["id"], "joyeux")
        self.assertEqual(mood_badge(0)["id"], "neutre")
        self.assertEqual(mood_badge(-4)["id"], "furieux")

    def test_cooldown_antispam(self):
        m = MoodTracker()
        e1 = m.punctual("impatient", now=100.0)
        self.assertIsNotNone(e1)
        self.assertIsNone(m.punctual("impatient", now=101.0))  # cooldown 2 s
        e2 = m.punctual("impatient", now=103.0)
        self.assertIsNotNone(e2)
        self.assertEqual(e2.get("count"), 2)  # fusion des répétitions


class TestRng(unittest.TestCase):
    def test_derive_stable(self):
        self.assertEqual(derive_seed(42, "a", "b"), derive_seed(42, "a", "b"))
        self.assertNotEqual(derive_seed(42, "a", "b"), derive_seed(43, "a", "b"))

    def test_rng_seme(self):
        self.assertEqual(make_rng(1, "x").random(), make_rng(1, "x").random())


class TestSimDriver(unittest.TestCase):
    def test_parcours_et_observations(self):
        async def go():
            with tempfile.TemporaryDirectory() as td:
                d = SimDriver({"type": "sim", "base_url": ""}, {"cpu_throttle": 1.0, "input_device": "mouse"},
                              Path(td), "sess-test")
                await d.launch()
                await d.perform({"type": "navigate", "url": "home"})
                await d.perform({"type": "type", "text": "lampe"})
                await d.perform({"type": "press", "key": "Enter"})
                snap = await d.snapshot()
                self.assertIn("results", snap.url)
                obs = await d.observe()
                self.assertTrue(any(k.startswith("lcp_ms") for k in obs["vitals"]))
                await d.screenshot("shot")
                self.assertTrue((Path(td) / "screenshots" / "shot.svg").exists())
                await d.stop()
        asyncio.run(go())

    def test_cible_tactile_gene_les_tactiles(self):
        async def go():
            with tempfile.TemporaryDirectory() as td:
                d = SimDriver({"type": "sim"}, {"cpu_throttle": 1.0, "input_device": "touch"},
                              Path(td), "sess-tactile")
                # forcer le hasard côté driver pour un échec garanti
                import random
                d._rng = random.Random(0)
                results = []
                for _ in range(12):
                    d._goto("cart", 0)
                    results.append((await d.perform({"type": "click", "name": "Passer la commande"})).ok)
                self.assertIn(False, results)  # le persona tactile rate parfois la cible 24px
                self.assertIn(True, results)
        asyncio.run(go())


class TestPipeline(unittest.TestCase):
    def _scenario(self) -> Scenario:
        return Scenario(id="s", steps=[
            Step(intent="étape 1", baseline_seconds=5, actions=[
                {"type": "navigate", "url": "home"},
                {"type": "press", "key": "Enter"},
            ], expects=[Expectation(kind="url_matches", pattern="results")]),
        ])

    def test_session_reproductible(self):
        async def go(seed):
            lib = builtin_personas()
            campaign = Campaign(id="t", target_id="sim", personas=["eric"], scenarios=["s"],
                                speed="fast", seed=seed)
            bus = EventBus()
            with tempfile.TemporaryDirectory() as td:
                runner = SessionRunner("x", lib["eric"], self._scenario(),
                                       Target(id="sim", type="sim"), campaign, bus, Path(td))
                return await runner.run()
        r1, r2 = asyncio.run(go(42)), asyncio.run(go(42))
        self.assertEqual(r1.outcome, r2.outcome)
        self.assertEqual(r1.steps_passed, r2.steps_passed)
        self.assertAlmostEqual(r1.mood_final, r2.mood_final, places=2)
        self.assertEqual(r1.friction_extra_actions, r2.friction_extra_actions)

    def test_bus_ordre_par_session(self):
        async def go():
            bus = EventBus()
            seen: list[str] = []
            bus.on(lambda ev: seen.append(ev["type"]) if ev.get("session_id") == "x" else None)
            with tempfile.TemporaryDirectory() as td:
                lib = builtin_personas()
                campaign = Campaign(id="t", target_id="sim", personas=["eric"], scenarios=["s"],
                                    speed="turbo", seed=1)
                runner = SessionRunner("x", lib["eric"], self._scenario(),
                                       Target(id="sim", type="sim"), campaign, bus, Path(td))
                await runner.run()
            self.assertIn("step.started", seen)  # session.started est émis par le moteur
            self.assertIn("session.completed", seen)
            self.assertLess(seen.index("step.started"), seen.index("session.completed"))
            self.assertIn("step.passed", seen)
        asyncio.run(go())


class TestScoring(unittest.TestCase):
    def test_score_global_pondere(self):
        sessions = [{
            "steps_passed": 3, "steps_failed": 0, "steps_warned": 0, "outcome": "pass",
            "mood_final": 2.0, "friction_extra_actions": 0, "console_errors": [],
            "vitals": {"lcp_ms::x": 1200}, "a11y_violations": [], "crashed": False,
        }]
        axes = compute_axes(sessions, {"lcp_ms": 2500, "cls": 0.1})
        by_name = {a.name: a.score for a in axes}
        self.assertEqual(set(by_name), {"fonctionnel", "perf", "a11y", "ux", "stabilite"})
        self.assertEqual(by_name["fonctionnel"], 100.0)
        score = global_score(axes, {"fonctionnel": 0.3, "perf": 0.2, "a11y": 0.15,
                                    "ux": 0.2, "stabilite": 0.15})
        self.assertTrue(0 <= score <= 100)

    def test_penalite_a11y_et_crash(self):
        sessions = [{
            "steps_passed": 1, "steps_failed": 0, "steps_warned": 0, "outcome": "pass",
            "mood_final": 0, "friction_extra_actions": 0, "console_errors": ["e"],
            "vitals": {}, "a11y_violations": [{"rule": "r", "severity": "critical"}],
            "a11y_scanned": True, "crashed": True,
        }]
        by_name = {a.name: a.score for a in compute_axes(sessions, {})}
        self.assertLess(by_name["a11y"], 100)
        self.assertLess(by_name["stabilite"], 100)


class TestReport(unittest.TestCase):
    def _states(self):
        from testmyapp.core.engine import SessionState
        s1 = SessionState("x", "nadine", "s", "sim")
        s1.outcome = "abandoned"
        s1.a11y_violations = [{"rule": "target-size", "severity": "moderate", "selector": "button"}]
        s1.vitals = {"lcp_ms::u": 4000.0}
        return {"x": s1}

    def test_constats_et_recommandations(self):
        persons = builtin_personas()
        campaign = Campaign(id="t", target_id="sim", personas=["nadine"], scenarios=["s"])
        positives, negatives = build_findings(self._states(), persons, Target(id="sim", type="sim"), campaign)
        titles = [f["title"] for f in negatives]
        self.assertTrue(any("abandon" in t.lower() for t in titles))
        self.assertTrue(any("target-size" in t for t in titles))
        self.assertTrue(any("lcp_ms" in t for t in titles))
        recos = build_recommendations(negatives, campaign)
        self.assertTrue(any(r["id"] == "REC-reduce_abandonment" for r in recos))
        self.assertTrue(all(r["priority"] in {"P0", "P1", "P2"} for r in recos))


class TestConfig(unittest.TestCase):
    def test_campagne_ponderations_invalides(self):
        from testmyapp.config import ConfigError
        with self.assertRaises(ConfigError):
            Campaign.from_toml({"id": "x", "weights": {"fonctionnel": 0.9}}, "t")

    def test_cible_web_sans_url(self):
        from testmyapp.config import ConfigError
        with self.assertRaises(ConfigError):
            Target.from_toml({"id": "x", "type": "web"}, "t")

    def test_scenario_parse(self):
        s = Scenario.from_toml({
            "id": "s",
            "steps": [
                {"intent": "a", "actions": [{"type": "click", "name": "x"}],
                 "expects": [{"kind": "url_matches", "pattern": "p"}],
                 "baseline_seconds": 12},
            ],
        }, "t")
        self.assertEqual(s.steps[0].expects[0].pattern, "p")


if __name__ == "__main__":
    unittest.main(verbosity=2)
