"""Contrats moteur, reprise et rapport sur le driver simulé."""
import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from testmyapp.behavior.agent import AgentChoice
from testmyapp.behavior.runner import SessionRunner
from testmyapp.campaign_models import compile_model
from testmyapp.config import AgentSettings, Campaign, ConfigError, Expectation, Scenario, Step, Target, load_toml
from testmyapp.core.engine import CampaignEngine
from testmyapp.core.events import EventBus, read_run_events
from testmyapp.core.proofs import check_records
from testmyapp.core.scoring import compute_axes, global_score
from testmyapp.drivers.base import ActionResult, PageState, public_url
from testmyapp.drivers.desktop import DesktopDriver
from testmyapp.gui.floor import FloorModel
from testmyapp.personas.library import builtin_personas
from testmyapp.report.comparison import compare_reports


class CampaignEvidenceContract(unittest.TestCase):
    def test_model_orders_dependencies_and_rejects_a_cycle(self):
        personas = builtin_personas()
        targets = {"web": Target("web", "web", base_url="http://127.0.0.1")}
        goal = {"goal": "Consulter le journal", "expected": "Sans erreur"}
        model = {"id": "modele-0123456789abcdef", "name": "Journal", "target": "web",
                 "rows": [{**goal, "persona": "marc", "after": "eric"}, {**goal, "persona": "eric"}]}
        _, campaign, assignments, scenarios = compile_model(model, personas, targets, {}, {})
        self.assertEqual(campaign.personas, ["eric", "marc"])
        self.assertEqual(campaign.depends_on, {"marc": "eric"})
        self.assertEqual(scenarios[assignments["marc"]["scenario"]].steps[0].intent, goal["goal"])
        model["rows"][1]["after"] = "marc"
        with self.assertRaisesRegex(ConfigError, "dépendance circulaire"):
            compile_model(model, personas, targets, {}, {})

    def test_unique_creation_proof_is_passed_to_the_history_check(self):
        proof_rule = {"endpoint": "/items", "collection": "items",
                      "match": {"title": "Tâche {{run_suffix}}"}, "count": 1, "save_as": "task_id"}
        task = {"id": "same-task", "title": "Tâche abc"}
        ok, detail = check_records({"items": [task]}, proof_rule, {"run_suffix": "abc"})
        self.assertTrue(ok)
        audit_rule = {"endpoint": "/audit", "collection": "entries",
                      "match": {"target": "{{task_id}}", "operation": "create"}, "count": 1}
        matching = {"target": "same-task", "operation": "create"}
        unrelated = {"target": "other-task", "operation": "create"}
        self.assertTrue(check_records({"entries": [matching, unrelated]}, audit_rule, detail["proof"])[0])
        self.assertFalse(check_records({"entries": [unrelated]}, audit_rule, detail["proof"])[0])
        self.assertFalse(check_records({"entries": [matching], "next_cursor": "page2"}, audit_rule, detail["proof"])[0])

    def test_comparison_distinguishes_a_regression_from_an_absent_persona(self):
        eric = {"persona": "eric", "scenario": "create", "outcome": "pass"}
        marc = {"persona": "marc", "scenario": "audit", "outcome": "pass"}
        before = {"status": "pass", "score": 90, "sessions": [eric, marc]}
        after = {"status": "fail", "score": 60, "sessions": [{**eric, "outcome": "fail"}],
                 "negatives": [{"title": "Tâche introuvable"}]}
        comparison = compare_reports(before, after)
        self.assertEqual([row["after"] for row in comparison["sessions"]], ["fail", None])
        self.assertEqual(comparison["new_findings"], ["Tâche introuvable"])


class FloorContract(unittest.TestCase):
    def test_workstations_match_effective_parallelism_and_reuse_the_only_station(self):
        floor = FloorModel()
        floor._on_event({"type": "campaign.started", "sessions_total": 6,
                         "max_workers": 5, "parallelism": 1})
        state = floor.full_state()
        self.assertEqual(state["stations"], [(13, 6)])
        self.assertEqual(state["stats"]["parallelism"], 1)
        floor._on_event({"type": "session.started", "session_id": "s1", "persona": "eric"})
        floor._on_event({"type": "session.completed", "session_id": "s1", "outcome": "pass"})
        floor._on_event({"type": "session.started", "session_id": "s2", "persona": "marc"})
        self.assertEqual(floor.full_state()["station_sessions"], {0: "s2"})

    def test_older_parallel_campaign_draws_only_its_configured_workstations(self):
        floor = FloorModel()
        floor._on_event({"type": "campaign.started", "sessions_total": 8, "max_workers": 3})
        self.assertEqual(len(floor.full_state()["stations"]), 3)


class DesktopStartupContract(unittest.TestCase):
    def test_waits_for_a_late_main_window_and_its_discovery_controls(self):
        clock = [0.]
        driver = DesktopDriver({}, {}, Path("."), "desktop")
        self.addCleanup(driver._uia.shutdown)
        driver._child = MagicMock()
        driver._child.poll.return_value = None
        driver._discovery_file = Path("visible.json")
        driver._window_responsive = lambda: driver._handle == 123
        driver._sync_snapshot = lambda: PageState(elements=[{"name": "Résumé détaillé"}] if clock[0] >= 14 else [])

        def sleep(delay):
            clock[0] += delay

        with patch("testmyapp.drivers.desktop.time.monotonic", side_effect=lambda: clock[0]), \
             patch("testmyapp.drivers.desktop.time.sleep", side_effect=sleep):
            driver._wait_for_startup(lambda: [11, 123] if clock[0] >= 13 else [], 0.)
        self.assertEqual(driver._handle, 123)
        self.assertGreaterEqual(driver._startup_ms, 14000)


class RunnerContract(unittest.IsolatedAsyncioTestCase):
    def runner(self, root, steps):
        return SessionRunner("test", builtin_personas()["eric"], Scenario(id="s", steps=steps),
                             Target(id="sim", type="sim"), Campaign("t", "sim", ["eric"], ["s"]),
                             EventBus(), Path(root))

    async def test_recovery_verifies_an_already_written_state_without_repeating_actions(self):
        record = Expectation("api_record", api={
            "endpoint": "/items", "collection": "items", "match": {"id": "{{task_id}}", "status": "in_progress"},
        })
        steps = [Step("Vérifier la tâche", stop_on_failure=True),
                 Step("Passer la tâche « Révision » en cours", mode="discovery", screenshot="none",
                      expects=[record])]
        runner = self.runner(".", steps)
        runner.target = Target("web", "web", base_url="http://127.0.0.1")
        runner.parameters = {"task_id": "established-id"}
        runner.attempt = 2
        driver = AsyncMock()
        driver.read_json.return_value = {"items": [{"id": "established-id", "status": "in_progress"}]}
        driver.observe.return_value = {}
        driver.health.return_value = {}
        runner._driver = driver
        self.assertTrue(await runner._recover_committed_task())
        driver.perform.assert_not_awaited()
        event = next(event for event in runner.bus.events_of("test") if event["type"] == "step.passed")
        self.assertEqual(event["evidence"][0]["detail"]["recovered"], "retry")
        self.assertEqual(event["evidence"][0]["detail"]["fields"]["id"], "established-id")

    async def test_wait_checks_result_and_timeout(self):
        runner = self.runner(".", [])
        driver = AsyncMock()
        driver.snapshot.return_value = PageState(elements=[{"name": "Caché", "visible": False}])
        driver.perform.return_value = ActionResult(False, "wait_for", 1.0)
        runner._driver = driver
        ok, detail = await runner._check_expect(Expectation("visible", selector="Caché", timeout_ms=120))
        self.assertFalse(ok)
        self.assertEqual(detail["selector"], "Caché")
        driver.perform.assert_awaited_once_with({"type": "wait_for", "selector": "Caché", "timeout_ms": 120})

    async def test_exploration_executes_each_choice_once_and_stops_at_goal(self):
        step = Step("Ouvrir le panier", mode="explore", expects=[Expectation("url_matches", pattern="/cart")])
        runner = self.runner(".", [step])
        runner.speed_mult = 0
        driver = AsyncMock()
        driver.snapshot.side_effect = [
            PageState("/home", elements=[{"name": "Panier", "role": "link"}]),
            PageState("/cart", elements=[{"name": "Retour", "role": "link"}]),
            PageState("/cart"),
        ]
        driver.perform.return_value = ActionResult(True, "click", 1.0)
        driver.screenshot.return_value = None
        driver.observe.return_value = {}
        driver.health.return_value = {}
        runner._driver = driver
        await runner._run_step(0, step)
        driver.perform.assert_awaited_once_with({"type": "click", "name": "Panier"})
        self.assertEqual(runner.result.steps_passed, 1)

    async def test_free_task_replans_from_visible_controls_and_checks_the_actual_state(self):
        step = Step("Ouvrir les exécutions puis cocher Résumé détaillé", mode="discovery",
                    expects=[Expectation("text_contains", selector="Résumé détaillé coché")])
        runner = self.runner(".", [step])
        tab = {"name": "Exécutions", "role": "tab", "state": None, "id": "executions"}
        option = {"name": "Résumé détaillé", "role": "checkbox", "state": "unchecked", "id": "summary"}
        driver = AsyncMock()
        driver.snapshot.side_effect = [
            PageState(elements=[tab]),
            PageState(elements=[option]),
            PageState(elements=[{**option, "state": "checked"}]),
        ]
        runner._driver = driver
        actions = [action async for action in runner._step_actions(step, 0)]
        self.assertEqual([action["control_id"] for action in actions], ["executions", "summary"])
        self.assertFalse(runner._goal_matches(step.expects[0], PageState(elements=[option])))

    async def test_execution_discovery_opens_history_before_inspecting_the_node(self):
        step = Step("Retrouver la dernière exécution et vérifier les tests et SQLite", mode="discovery",
                    expects=[Expectation("text_contains", selector="Tests 4/4")])
        runner = self.runner(".", [step])
        tab = {"name": "Exécutions", "role": "tab", "state": "selected", "id": "tab"}
        row = {"name": "20/11 01:42 · Réussi", "role": "listitem", "state": None, "id": "row"}
        node = {"name": "Vérifier les tests et SQLite", "role": "node", "state": None, "id": "node"}
        driver = AsyncMock()
        driver.snapshot.side_effect = [
            PageState(elements=[tab, row, node]),
            PageState(elements=[tab, {**row, "state": "selected"}, node]),
            PageState(elements=[{"name": "Tests  4/4", "role": "text"}]),
        ]
        runner._driver = driver
        actions = [action async for action in runner._step_actions(step, 0)]
        self.assertEqual([(action["control_id"], action["decision"]) for action in actions],
                         [("row", "history"), ("node", "label")])

    async def test_free_task_waits_for_async_content_before_leaving_a_new_page(self):
        step = Step("Rejouer le test du projet", mode="discovery",
                    expects=[Expectation("text_contains", selector="Campagne lancée")])
        runner = self.runner(".", [step])
        replay = {"name": "Rejouer", "role": "button", "state": None, "id": "replay"}
        back = {"name": "Projets et rapports", "role": "button", "state": None, "id": "back"}
        driver = AsyncMock()
        driver.snapshot.side_effect = [
            PageState(url="http://localhost/", elements=[replay]),
            PageState(url="http://localhost/floor/", elements=[back]),
            PageState(url="http://localhost/floor/", elements=[{"name": "Campagne lancée", "role": "text"}]),
        ]
        runner._driver = driver
        actions = [action async for action in runner._step_actions(step, 0)]
        self.assertEqual([action["control_id"] for action in actions], ["replay"])

    def test_rejects_unknown_expectations(self):
        with self.assertRaisesRegex(ConfigError, "étape 1.*attente inconnu"):
            Scenario.from_toml({"id": "s", "steps": [{"expects": [{"kind": "visble"}]}]}, "test")

    def test_current_navigation_metric_and_redaction(self):
        runner = self.runner(".", [])
        obs = {"url": "https://demo.invalid/done",
               "vitals": {"lcp_ms::https://demo.invalid/checkout": 3000,
                          "lcp_ms::https://demo.invalid/done": 50}}
        self.assertEqual(runner._metric_value("lcp_ms", obs), 50)
        self.assertIsNone(runner._metric_value("inp_ms", obs))
        self.assertEqual(public_url("https://a:b@demo.invalid/done?card=fake#secret"), "https://demo.invalid/done")

    async def test_waits_for_the_current_page_metric_to_be_published(self):
        runner = self.runner(".", [])
        runner.target.type = "web"
        driver = AsyncMock()
        url = "http://127.0.0.1/checkout"
        driver.observe.side_effect = [
            {"url": url, "vitals": {"lcp_ms::http://127.0.0.1/cart": 3100}},
            {"url": url, "vitals": {f"lcp_ms::{url}": 50}},
        ]
        runner._driver = driver
        ok, detail = await runner._check_expect(Expectation("metric_below", metric="lcp_ms",
                                                             max=2500, timeout_ms=400))
        self.assertTrue(ok)
        self.assertEqual(detail["value"], 50)


class CampaignContract(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_starts_independent_tasks_and_blocks_only_descendants(self):
        personas = builtin_personas()
        campaign = Campaign("workflow", "sim", ["eric", "marc", "alice"], ["one", "other", "follow", "final"],
                            max_workers=3, speed="fast", score_pass=0)
        tasks = {
            "task-1": {"persona": "eric", "scenario": "one", "after": "", "wait_for": []},
            "task-2": {"persona": "alice", "scenario": "other", "after": "", "wait_for": []},
            "task-3": {"persona": "marc", "scenario": "follow", "after": "task-1", "wait_for": []},
            "task-4": {"persona": "eric", "scenario": "final", "after": "task-3", "wait_for": ["task-2"]},
        }
        participants = [personas[pid] for pid in ("eric", "marc")]
        from dataclasses import replace
        participants.append(replace(personas["marc"], id="alice", name="Alice"))
        first, other = asyncio.Event(), asyncio.Event()

        async def simulated(runner):
            scenario = runner.scenario.id
            if scenario == "one":
                first.set()
                await other.wait()
            elif scenario == "other":
                other.set()
                await first.wait()
            self.assertNotEqual(scenario, "final")
            passed = scenario != "follow"
            runner._emit("step.passed" if passed else "step.failed", step_id=0)
            runner._emit("session.completed", outcome="pass" if passed else "fail",
                         steps_passed=int(passed), steps_failed=int(not passed))

        with tempfile.TemporaryDirectory() as root:
            engine = CampaignEngine(campaign, participants, [Scenario(sid) for sid in campaign.scenarios],
                                    Target("sim", "sim"), Path(root), tasks=tasks,
                                    model={"version": 2, "name": "Relais"})
            with patch.object(SessionRunner, "run", simulated):
                result = await asyncio.wait_for(engine.run(), 6)
            self.assertEqual(result["status"], "fail")
            self.assertEqual({state.scenario_id: state.outcome for state in engine.state.values()},
                             {"one": "pass", "other": "pass", "follow": "fail", "final": "blocked"})
            self.assertEqual(read_run_events(Path(root))[0]["parallelism"], 2)

            selected = next(sid for sid, _, scenario in engine.session_plan() if scenario == "follow")
            retried = CampaignEngine(campaign, participants, [Scenario(sid) for sid in campaign.scenarios],
                                     Target("sim", "sim"), Path(root), tasks=tasks, resume=True,
                                     retry=selected, model={"version": 2, "name": "Relais"})

            async def recover(runner):
                self.assertIn(runner.scenario.id, {"follow", "final"})
                runner._emit("step.passed", step_id=0)
                runner._emit("session.completed", outcome="pass", steps_passed=1)

            with patch.object(SessionRunner, "run", recover):
                result = await asyncio.wait_for(retried.run(), 6)
            self.assertEqual(result["status"], "pass")
            starts = [event["scenario"] for event in read_run_events(Path(root))
                      if event["type"] == "session.started"]
            self.assertEqual(starts, ["one", "other", "follow", "follow", "final"])
            report = json.loads((Path(root) / "report" / "report.json").read_text(encoding="utf-8"))
            historical = {item["persona"]: item for item in report["previous_attempts"]}
            self.assertEqual(historical["Marc"]["outcome"], "fail")
            self.assertEqual(historical["Marc"]["attempt"], 1)
            self.assertEqual(historical["Éric"]["outcome"], "blocked")
            self.assertNotIn("Alice", historical)

    async def test_failed_prerequisite_blocks_the_dependent_without_launching_it(self):
        personas = builtin_personas()
        campaign = Campaign("chain", "sim", ["eric", "marc"], ["setup", "follow"], speed="fast",
                            depends_on={"marc": "eric"})
        setup = Scenario("setup", steps=[Step("Préparer", expects=[Expectation("element_count", min_count=500)],
                                             stop_on_failure=True),
                                         Step("Créer", actions=[{"type": "click", "name": "Créer"}])])
        follow = Scenario("follow", steps=[Step("Retrouver")])
        with tempfile.TemporaryDirectory() as root:
            engine = CampaignEngine(campaign, [personas["eric"], personas["marc"]], [setup, follow],
                                    Target("sim", "sim"), Path(root), tasks={
                                        "eric": {"scenario": "setup"}, "marc": {"scenario": "follow"},
                                    })
            result = await engine.run()
            events = read_run_events(Path(root))
            self.assertEqual(result["status"], "fail")
            self.assertEqual([event["persona"] for event in events if event["type"] == "session.started"], ["eric"])
            self.assertIn("step.skipped", [event["type"] for event in events])
            self.assertIn("session.blocked", [event["type"] for event in events])

    async def test_resume_keeps_each_personas_own_task_and_message(self):
        from testmyapp.cli import _build_engine, main
        from testmyapp.drivers.sim import SimDriver

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["init", "--dir", directory]), 0)
            (root / "scenarios" / "aide.toml").write_text(
                'id = "aide"\ntask_text = "Aide"\n[[steps]]\nintent = "Découvrir l’aide"\n'
                'actions = [{type = "type", text = "{task.text}"}]\n', encoding="utf-8")
            campaign = root / "campaigns" / "demo.toml"
            with campaign.open("a", encoding="utf-8") as file:
                file.write('\n[discovery]\neric = "aide"\nmarc = "commander-produit"\n')
            tasks = {"eric": {"scenario": "aide", "text": "Aide personnalisée"},
                     "marc": {"scenario": "commander-produit"}}
            engine = _build_engine(root, campaign_id="demo", persona=["eric", "marc"], speed="turbo", tasks=tasks)
            perform = SimDriver.perform
            messages = []

            async def record_message(driver, action):
                if driver.session_id.startswith("eric×") and action["type"] == "type":
                    messages.append(action.get("text"))
                return await perform(driver, action)

            with patch.object(SimDriver, "perform", record_message):
                await engine.run()
            self.assertIn("Aide personnalisée", messages)
            resumed = _build_engine(root, resume=engine.run_dir)
            resumed._prepare_run()
            self.assertEqual(resumed.tasks, tasks)
            self.assertEqual([(pid, sid) for _, pid, sid in resumed.session_plan()],
                             [("eric", "aide"), ("marc", "commander-produit")])

    async def test_profiles_submit_the_same_search_in_different_ways(self):
        library = builtin_personas()
        personas = [library[pid] for pid in ("eric", "marc", "mona")]
        for persona in personas:
            persona.error_proneness = 0
        path = Path(__file__).resolve().parents[1] / "testmyapp/assets/scenarios/commander-produit.toml"
        scenario = Scenario.from_toml(load_toml(path))
        scenario.steps = scenario.steps[:1]
        campaign = Campaign("routes", "sim", [p.id for p in personas], [scenario.id],
                            speed="turbo", score_pass=0)
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            engine = CampaignEngine(campaign, personas, [scenario], Target("sim", "sim"), run)
            result = await engine.run()
            self.assertEqual(result["status"], "pass")
            events = read_run_events(run)
        routes = {event["session_id"].split("×")[0]: event["action"] for event in events
                  if event["type"] == "action.performed" and event["action"] in {"press", "click", "tap"}}
        self.assertEqual(routes, {"eric": "press", "marc": "click", "mona": "tap"})

    def engine(self, path, *, resume=False, selector=None, seed=42):
        library = builtin_personas()
        personas = [library["eric"], library["nadine"]]
        target = Target("sim", "sim")
        expectation = (Expectation("visible", selector=selector) if selector else
                       Expectation("url_matches", pattern="results"))
        scenario = Scenario("test", steps=[
            Step("Ouvrir les résultats", actions=[{"type": "navigate", "url": "results"}],
                 expects=[expectation], screenshot="both"),
        ])
        return CampaignEngine(Campaign("t", "sim", ["eric", "nadine"], ["test"],
                                       speed="turbo", seed=seed, max_workers=1, score_pass=0),
                              personas, [scenario], target, path, resume=resume)

    async def test_failed_step_beats_score_and_has_offline_evidence(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            result = await self.engine(root, selector="Absent").run()
            self.assertEqual(result["status"], "fail")
            report = json.loads((root / "report" / "report.json").read_text(encoding="utf-8"))
            html = (root / "report" / "report.html").read_text(encoding="utf-8")
            markdown = (root / "report" / "report.md").read_text(encoding="utf-8")
            for document in (html, markdown):
                self.assertIn("Déroulement du test", document)
                self.assertIn("Ouvrir les résultats", document)
                self.assertIn("Ouverture de la page prévue.", document)
                self.assertIn("Le test n’a pas trouvé l’élément attendu « Absent ».", document)
            self.assertLess(html.index("Déroulement du test"), html.index("Détail des scores"))
            anchors = set(re.findall(r' id="([^"]+)"', html))
            self.assertIn("data:image/svg+xml;base64,", html)
            for finding in report["positives"] + report["negatives"]:
                self.assertTrue(finding["evidence_refs"], finding["title"])
                self.assertTrue(set(finding["evidence_refs"]).issubset(anchors))
                self.assertTrue(set(finding["evidence_refs"]).issubset(report["evidence"]))

    async def test_resumes_without_changing_completed_session_evidence(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            interrupted = self.engine(root / "resumed")
            started = asyncio.Event()
            interrupted.bus.on(lambda e: started.set() if e["type"] == "step.started" else None)
            run_task = asyncio.create_task(interrupted.run())
            await asyncio.wait_for(started.wait(), timeout=8)
            self.assertTrue(await interrupted.control("stop"))
            self.assertEqual((await run_task)["status"], "interrupted")
            events = interrupted.run_dir / "events.jsonl"
            old_bytes = events.read_bytes()
            with self.assertRaisesRegex(ConfigError, "configuration a changé"):
                await self.engine(interrupted.run_dir, seed=43, resume=True).run()
            self.assertEqual(events.read_bytes(), old_bytes)
            with events.open("ab") as handle:
                handle.write(b'{"seq":')
            resumed = self.engine(interrupted.run_dir, resume=True)
            full = self.engine(root / "full")
            await resumed.run()
            await full.run()
            self.assertTrue(events.read_bytes().startswith(old_bytes))
            self.assertEqual(events.with_suffix(".partial").read_bytes(), b'{"seq":\n')
            for line in events.read_text(encoding="utf-8").splitlines():
                json.loads(line)
            restored_events = read_run_events(resumed.run_dir)
            self.assertEqual(sum(e["type"] == "session.started" for e in restored_events), 2)
            self.assertEqual(resumed.score, full.score)
            self.assertEqual(set(resumed.state), set(full.state))
            for sid in resumed.state:
                left, right = resumed.state[sid], full.state[sid]
                self.assertEqual((left.outcome, left.vitals, left.screenshots),
                                 (right.outcome, right.vitals, right.screenshots))
                decisions = lambda engine: [(e["type"], e.get("intent_target"), e.get("simulated_delay_ms"))
                                            for e in engine.bus.events_of(sid) if e["type"] == "action.performed"]
                self.assertEqual(decisions(resumed), decisions(full))

    def test_no_measurement_does_not_produce_a_score(self):
        campaign = Campaign("t", "sim", ["eric"], ["s"])
        axes = compute_axes([{"outcome": "error"}], campaign.thresholds)
        self.assertTrue(all(a.score is None for a in axes))
        self.assertIsNone(global_score(axes, campaign.weights))

    async def test_one_hundred_simulated_sessions_finish(self):
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            base = builtin_personas()["eric"]
            personas = [replace(base, id=f"personne-{i}") for i in range(10)]
            step = Step("Afficher l’accueil", actions=[{"type": "navigate", "url": "/"}], screenshot="none")
            scenarios = [Scenario(f"scenario-{i}", steps=[step]) for i in range(10)]
            campaign = Campaign("volume", "sim", [p.id for p in personas], [s.id for s in scenarios],
                                max_workers=24, speed="turbo", score_pass=0)
            engine = CampaignEngine(campaign, personas, scenarios, Target("sim", "sim"), root)
            result = await asyncio.wait_for(engine.run(), timeout=30)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(len(engine.state), 100)
            self.assertTrue(all(state.outcome == "pass" for state in engine.state.values()))


class AgentContract(unittest.IsolatedAsyncioTestCase):
    async def test_masked_prompt_bounded_choice_and_budget(self):
        provider = AgentChoice(AgentSettings("http://127.0.0.1:11434/v1/chat/completions", "fake",
                                             max_requests=1), {"CodePrivé72"})
        candidate = {"name": "Ouvrir CodePrivé72", "role": "button", "value": "AUTRE_VALEUR"}
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"index":0}'}}],
            "usage": {"prompt_tokens": 24, "completion_tokens": 5},
        }).encode("utf-8")
        with patch("testmyapp.behavior.agent.build_opener", return_value=opener):
            index, telemetry = await provider.choose("Chercher CodePrivé72", [candidate])
            self.assertEqual(index, 0)
            self.assertEqual(telemetry["input_tokens"], 24)
            request = opener.open.call_args.args[0]
            self.assertNotIn("CodePrivé72".encode("utf-8"), request.data)
            self.assertNotIn(b"AUTRE_VALEUR", request.data)
            self.assertIsNone((await provider.choose("Chercher", [candidate]))[0])
            opener.open.assert_called_once()

    async def test_invalid_agent_index_falls_back(self):
        provider = AgentChoice(AgentSettings("http://127.0.0.1:11434/v1/chat/completions", "fake"), set())
        with patch.object(provider, "_request", return_value={
            "choices": [{"message": {"content": '{"index":99}'}}],
        }) as request:
            index, telemetry = await provider.choose("Chercher", [{"name": "Panier", "role": "link"}])
            self.assertIsNone(index)
            self.assertEqual(telemetry["source"], "weighted")
            await provider.choose("Chercher", [{"name": "Panier", "role": "link"}])
            request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
