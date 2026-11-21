"""Recettes locales facultatives avec Chromium et un bureau Windows interactif."""
import asyncio
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from collections import Counter
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from itertools import product
from pathlib import Path
from unittest.mock import patch

from testmyapp.config import AgentSettings, Campaign, Expectation, Scenario, Step, Target, load_toml
from testmyapp.core.engine import CampaignEngine
from testmyapp.personas.library import builtin_personas

ASSETS = Path(__file__).resolve().parents[1] / "testmyapp" / "assets"


@contextmanager
def serve(app):
    import uvicorn

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while worker.is_alive() and not server.started and time.monotonic() < deadline:
                time.sleep(.05)
            if not server.started:
                raise RuntimeError("Le serveur de recette n’a pas démarré")
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            worker.join(timeout=10)


def run_fixture(name, root, *, url=None, browser=None):
    campaign = Campaign.from_toml(load_toml(ASSETS / "campaigns" / f"{name}.toml"))
    personas = [builtin_personas()[pid] for pid in campaign.personas]
    scenarios = [Scenario.from_toml(load_toml(ASSETS / "scenarios" / f"{sid}.toml"))
                 for sid in campaign.scenarios]
    target = Target.from_toml(load_toml(ASSETS / "targets" / f"{campaign.target_id}.toml"))
    if url:
        target.base_url = url
    if browser:
        target.browser = browser
    engine = CampaignEngine(campaign, personas, scenarios, target, root)
    result = asyncio.run(engine.run())
    return engine, result


@unittest.skipUnless(os.environ.get("TESTMYAPP_BROWSER") == "1", "recette Chromium optionnelle")
class WebIntegration(unittest.TestCase):
    def test_workspace_keeps_reports_by_project_and_resumes_an_interrupted_campaign(self):
        from playwright.sync_api import expect, sync_playwright

        from testmyapp.cli import _build_engine, main
        from testmyapp.gui.workspace import Projects, create_workspace

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first", root / "second"
            with redirect_stdout(StringIO()):
                for project in (first, second):
                    self.assertEqual(main(["init", "--dir", str(project)]), 0)
            finished = _build_engine(first, campaign_id="demo", persona=["eric"], speed="turbo")
            asyncio.run(finished.run())
            interrupted = _build_engine(second, campaign_id="demo", persona=["eric"], speed="turbo")
            interrupted.run_dir.mkdir(parents=True)
            interrupted._prepare_run()
            registry = root / "projects.json"
            projects = Projects(registry)
            projects.add(first, "Premier projet")

            with serve(create_workspace(Projects(registry))) as url, sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.set_default_timeout(8000)
                page.goto(url)
                expect(page.get_by_role("heading", name="Premier projet")).to_be_visible()
                page.get_by_role("link", name="Lire le rapport").click()
                self.assertIn(finished.run_dir.name, page.url)
                expect(page.locator("main")).to_contain_text("Score global")
                page.go_back()
                page.get_by_role("button", name="Rejouer").click()
                expect(page.locator("#floor")).to_be_visible()
                expect(page.locator("#timeline")).to_contain_text("campagne lancée")
                page.wait_for_function("""() => Object.values(walkers).some(walker => {
                    if (walker.stage !== "station" || walker.px < state.door[0] + 0.5) return false;
                    const x = Math.floor(walker.px * TILE + TILE / 2);
                    const y = Math.floor(walker.py * TILE - 16 * 3 + TILE - 6 + 3 * 9);
                    const [r, g, b] = ctx.getImageData(x, y, 1, 1).data;
                    return r === 0x4a && g === 0x6a && b === 0x8a;
                })""", timeout=8000)
                self.assertEqual(page.evaluate("""() => [0, 1].map(frame => {
                    const sprite = getSprite("test", DEFAULT_PAL, "walk", frame);
                    const pixels = sprite.getContext("2d");
                    return [4, 9, 15].every(row => pixels.getImageData(9, row * 3, 1, 1).data[3] === 255);
                })"""), [True, True])
                page.get_by_role("link", name="Projets et rapports").click()

                page.get_by_text("Ajouter un projet", exact=True).click()
                page.get_by_label("Chemin du dossier TestMyApp").fill(str(second))
                page.get_by_label("Nom affiché").fill("Second projet")
                page.get_by_role("button", name="Enregistrer le projet").click()
                expect(page.get_by_role("heading", name="Second projet")).to_be_visible()
                expect(page.get_by_role("link", name="Lire le rapport")).to_have_count(0)
                page.reload()
                expect(page.get_by_role("heading", name="Second projet")).to_be_visible()
                page.get_by_role("button", name="Reprendre la campagne").click()
                expect(page.locator("#btn-report")).to_be_visible(timeout=15000)
                page.get_by_role("link", name="Projets et rapports").click()
                page.get_by_role("link", name="Lire le rapport").click()
                self.assertIn(interrupted.run_dir.name, page.url)
                page.go_back()
                page.get_by_role("button", name="Premier projet").click()
                page.get_by_label("Campagne", exact=True).select_option("demo")
                checkboxes = page.get_by_role("group", name="Personas à faire participer").get_by_role("checkbox")
                expect(checkboxes).to_have_count(8)
                expect(page.get_by_role("checkbox", name="Éric")).to_be_checked()
                for checkbox in checkboxes.all():
                    checkbox.uncheck()
                launch = page.get_by_role("button", name="Lancer la campagne")
                expect(launch).to_be_disabled()
                page.get_by_role("checkbox", name="Marc").check()
                expect(launch).to_be_enabled()
                launch.click()
                expect(page.locator("#btn-report")).to_be_visible(timeout=20000)
                saved = next(path for path in (first / "runs").glob("*/campaign.json")
                             if path.parent != finished.run_dir)
                self.assertEqual(json.loads(saved.read_text(encoding="utf-8"))["personas"], ["marc"])
                self.assertTrue(any(json.loads(line).get("persona") == "marc"
                                    for line in (saved.parent / "events.jsonl").read_text(encoding="utf-8").splitlines()))
                page.get_by_role("link", name="Projets et rapports").click()
                expect(page.get_by_role("link", name="Lire le rapport")).to_have_count(2)
                browser.close()

    def test_pdf_export_includes_evidence_from_an_existing_run(self):
        from testmyapp.cli import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario = Scenario("pdf", steps=[Step("Afficher une capture",
                                actions=[{"type": "navigate", "url": "/"}], screenshot="both")])
            campaign = Campaign("pdf", "sim", ["eric"], [scenario.id], speed="turbo", score_pass=0)
            engine = CampaignEngine(campaign, [builtin_personas()["eric"]], [scenario],
                                    Target("sim", "sim"), root)
            asyncio.run(engine.run())
            before = (root / "events.jsonl").read_bytes()
            self.assertEqual(main(["report", directory, "--format", "pdf"]), 0)
            pdf = (root / "report" / "report.pdf").read_bytes()
            self.assertTrue(pdf.startswith(b"%PDF-"))
            self.assertIn(b"/StructTreeRoot", pdf)
            self.assertIn(b"/Subtype /Link", pdf)
            self.assertEqual((root / "events.jsonl").read_bytes(), before)

    def test_agent_explores_with_a_local_test_provider(self):
        from fastapi import FastAPI

        provider = FastAPI()
        requests = []

        @provider.post("/v1/chat/completions")
        async def choose(payload: dict):
            observation = json.loads(payload["messages"][1]["content"])
            requests.append(observation)
            index = next(row["index"] for row in observation["candidates"] if row["label"] == "Rechercher")
            return {"choices": [{"message": {"content": json.dumps({"index": index})}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 4}}

        step = Step("Rechercher un produit", mode="agent",
                    expects=[Expectation("url_matches", pattern="results")], screenshot="none")
        scenario = Scenario("agent", steps=[step])
        with tempfile.TemporaryDirectory() as directory, serve(provider) as url:
            campaign = Campaign("agent", "sim", ["eric"], [scenario.id], speed="turbo",
                                ai_mode=True, agent=AgentSettings(url + "/v1/chat/completions", "test"),
                                score_pass=0)
            engine = CampaignEngine(campaign, [builtin_personas()["eric"]], [scenario],
                                    Target("sim", "sim"), Path(directory))
            self.assertEqual(asyncio.run(engine.run())["status"], "pass")
            self.assertEqual(len(requests), 1)
            report = json.loads((Path(directory) / "report" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["agent"]["choices"], 1)
            self.assertEqual(report["agent"]["output_tokens"], 4)

    def test_checkout_defects_disappear_after_fix(self):
        from testmyapp.demoapp.server import create_demo_app

        browsers = [name.strip() for name in os.environ.get("TESTMYAPP_BROWSERS", "chromium").split(",")]
        variants = ((False, "recette-web"), (True, "recette-web-corrigee"))
        with tempfile.TemporaryDirectory() as directory:
            for browser, (fixed, name) in product(browsers, variants):
                with self.subTest(browser=browser, fixed=fixed), serve(create_demo_app(fixed=fixed)) as url:
                    root = Path(directory) / f"{name}-{browser}"
                    engine, result = run_fixture(name, root, url=url, browser=browser)
                    report = json.loads((root / "report" / "report.json").read_text(encoding="utf-8"))
                    rules = {v["rule"] for s in report["sessions"] for v in s["a11y_violations"]}
                    events = engine.bus.all_events
                    perf = [ev for ev in events if ev["type"] == "target.metric"
                            and ev["metric"] == f"lcp_ms::{url}/checkout"]
                    failures = [e for e in events if e["type"] in {"step.failed", "session.error"}]
                    self.assertTrue(perf, failures)
                    keyboard = [ev for ev in events if ev["type"] == "target.focus"
                                and ev["session_id"].startswith("karim×")]
                    self.assertTrue(keyboard, "Le scénario n’a pas parcouru les contrôles par tabulation")
                    delays = {name: [ev["simulated_delay_ms"] for ev in events
                                     if ev["type"] == "action.performed" and ev["session_id"].startswith(name + "×")]
                              for name in ("eric", "karim")}
                    self.assertNotEqual(delays["eric"], delays["karim"])
                    self.assertTrue(all("?" not in ev["url"] for ev in events if ev["type"] == "target.network"))
                    self.assertTrue(list((root / "traces").glob("*.zip")))
                    if fixed:
                        self.assertLess(max(ev["value"] for ev in perf), 2500)
                        self.assertFalse({"image-alt", "label", "color-contrast"} & rules, rules)
                        failures = [e for e in events if e["type"] in {"step.failed", "session.error"}]
                        self.assertEqual(result["status"], "pass", (result["score"], failures))
                    else:
                        self.assertGreater(max(ev["value"] for ev in perf), 2500)
                        self.assertTrue({"image-alt", "label", "color-contrast"}.issubset(rules), rules)
                        self.assertEqual(result["status"], "fail")

    def test_floor_controls_keyboard_proof_and_persistent_report(self):
        from playwright.sync_api import sync_playwright

        from testmyapp.gui.server import GuiHost, create_app

        personas = builtin_personas()
        scenario = Scenario.from_toml(load_toml(ASSETS / "scenarios" / "commander-produit.toml"))
        campaign = Campaign("gui", "sim", ["eric", "mona"], [scenario.id], max_workers=1, speed="fast")
        target = Target("sim", "sim")
        with tempfile.TemporaryDirectory() as directory:
            engine = CampaignEngine(campaign, list(personas.values()), [scenario], target, Path(directory))
            host = GuiHost(engine)
            with serve(create_app(host)) as url, sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(reduced_motion="reduce")
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(url, wait_until="networkidle")
                    page.get_by_role("button", name="Lancer").click()
                    pause = page.get_by_role("button", name="Pause", exact=True)
                    pause.click()
                    page.get_by_text("En pause", exact=True).wait_for()
                    page.get_by_role("button", name="Reprendre").click()
                    station = page.get_by_label("Station", exact=True)
                    page.wait_for_function("document.querySelector('#station-select').options.length > 1")
                    station.focus()
                    station.press("ArrowDown")
                    station.press("Tab")
                    page.keyboard.press("Enter")
                    page.get_by_role("heading", name=f"Éric · {scenario.name or scenario.id}").wait_for()
                    page.locator("#session-steps img").first.wait_for(timeout=20000)
                    self.assertTrue(page.locator("#session-steps img").first.evaluate("(image) => image.naturalWidth > 0"))
                    page.get_by_role("link", name="Voir le rapport").wait_for(timeout=30000)
                    canvas = page.locator("#floor")
                    point = canvas.evaluate("""c => ({
                      x: c.clientWidth * ((state.stations[0][0] + .5) / state.tiles[0]),
                      y: c.clientHeight * ((state.stations[0][1] - .25) / state.tiles[1])
                    })""")
                    canvas.click(position=point)
                    page.get_by_role("heading", name=f"Mona · {scenario.name or scenario.id}").wait_for()
                    report = page.request.get(url + "/report/report.html")
                    self.assertTrue(report.ok)
                    self.assertIn("Preuves et annexes", report.text())
                    self.assertFalse(errors, errors)
                finally:
                    browser.close()

    def test_floor_renders_24_actors_and_propagates_emotions(self):
        from playwright.sync_api import sync_playwright

        from testmyapp.gui.server import GuiHost, create_app

        host = GuiHost()
        host._record({"type": "campaign.started", "max_workers": 24, "sessions_total": 24})
        for i in range(24):
            host._record({"type": "session.started", "session_id": f"s{i}", "persona": f"p{i}", "scenario": "test"})
        self.assertEqual(len(host.floor.station_sessions), 24)
        with serve(create_app(host)) as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle")
                page.wait_for_function("Object.keys(state.personas).length === 24")
                frame_profile = page.evaluate("""() => new Promise(resolve => {
                  const start = performance.now();
                  let frames = 0;
                  function frame(time) {
                    frames++;
                    if (time - start >= 2000) resolve(1000 * frames / (time - start));
                    else requestAnimationFrame(frame);
                  }
                  requestAnimationFrame(frame);
                })""")
                stamp = time.time()
                host._record({"type": "persona.emotion", "session_id": "s0", "emotion": "confus",
                              "label": "Confus", "emoji": "❓", "ts": stamp})
                row = page.locator('[data-emotion="confus"][data-event-latency-ms]')
                row.wait_for()
                self.assertLess(float(row.get_attribute("data-event-latency-ms")), 500)
                print(f" (24 personas : {frame_profile:.1f} ips, émotion : "
                      f"{float(row.get_attribute('data-event-latency-ms')):.0f} ms)", flush=True)
                self.assertGreaterEqual(frame_profile, 55)
            finally:
                browser.close()

    def test_tui_opens_the_session_with_enter(self):
        from textual.widgets import Static

        from testmyapp.tui.app import PersonaTable, TestFloorTUI

        async def verify(directory):
            scenario = Scenario("tui", steps=[Step("Afficher accueil",
                                actions=[{"type": "navigate", "url": "/"}], screenshot="both")])
            campaign = Campaign("tui", "sim", ["eric"], [scenario.id], speed="fast", score_pass=0)
            engine = CampaignEngine(campaign, [builtin_personas()["eric"]], [scenario],
                                    Target("sim", "sim"), Path(directory))
            app = TestFloorTUI(engine.bus, engine)
            async with app.run_test(size=(120, 36)) as pilot:
                self.assertIsNotNone(app.campaign_task)
                self.assertEqual((await asyncio.wait_for(app.campaign_task, 10))["status"], "pass")
                await pilot.pause(.35)
                app.query_one(PersonaTable).focus()
                await pilot.press("enter")
                self.assertIn("Afficher accueil", app.query_one("#session-detail", Static).content)

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(verify(directory))


@unittest.skipUnless(os.name == "nt" and os.environ.get("TESTMYAPP_DESKTOP") == "1",
                     "recette native Windows optionnelle")
class DesktopIntegration(unittest.TestCase):
    def test_late_window_opens_once_after_targeted_workflow_retry(self):
        from testmyapp.behavior.runner import SessionRunner
        from testmyapp.core.events import read_run_events
        from testmyapp.drivers.desktop import DesktopDriver

        rows = [
            ("eric", "Vérifier le compteur initial", ""),
            ("marc", "Ajouter un élément et vérifier le compteur", "task-1"),
            ("alice", "Contrôler la fenêtre de la branche indépendante", ""),
            ("dany", "Vérifier le compteur de la fenêtre retardée", "task-2"),
        ]
        slow_ms = 13_000  # Beyond the previous twelve-second startup limit.
        scenarios = []
        tasks = {}
        for number, (persona, goal, parent) in enumerate(rows, 1):
            key = f"task-{number}"
            sid = f"decouverte-{key}"
            actions = [{"type": "invoke", "name": "Ajouter un item"}] if persona == "marc" else []
            expected = "Items : 1" if actions else "Items : 0"
            scenarios.append(Scenario(sid, name=goal, steps=[
                Step(goal, actions=actions, expects=[Expectation("text_contains", selector=expected)],
                     screenshot="none"),
            ]))
            tasks[key] = {"persona": persona, "scenario": sid, "after": parent, "wait_for": []}

        target = Target.from_toml(load_toml(ASSETS / "targets" / "demo-windows.toml"))
        personas = builtin_personas()
        people = [personas[persona] for persona, _, _ in rows]
        campaign = Campaign("windows-reprise", target.id, [persona.id for persona in people],
                            [scenario.id for scenario in scenarios], speed="fast", score_pass=0)
        model = {"version": 2, "name": "Recette Windows de reprise"}
        original_check = SessionRunner._check_expect
        original_launch = DesktopDriver.launch

        async def fail_after_observation(runner, expected, t_start=None):
            ok, detail = await original_check(runner, expected, t_start)
            if ok and runner.persona.id == "marc" and runner.attempt == 1:
                return False, {**detail, "simulated_failure": "Échec contrôlé après l’observation du compteur."}
            return ok, detail

        async def launch_slow_window(driver):
            if driver.session_id.startswith("dany×"):
                driver.target_cfg["args"] = [
                    *driver.target_cfg["args"], "-StartupDelayMs", str(slow_ms), "-StartupLog", str(startup_log),
                ]
            await original_launch(driver)

        async def run():
            initial = CampaignEngine(campaign, people, scenarios, target, run_dir, tasks=tasks, model=model)
            self.assertEqual((await initial.run())["status"], "fail")
            self.assertEqual({state.persona_id: state.outcome for state in initial.state.values()},
                             {"eric": "pass", "alice": "pass", "marc": "fail", "dany": "blocked"})
            self.assertFalse(startup_log.exists())
            marc = next(sid for sid, state in initial.state.items() if state.persona_id == "marc")
            self.assertTrue(any(check.get("detail", {}).get("simulated_failure")
                                for event in initial.bus.events_of(marc) if event["type"] == "step.failed"
                                for check in event["evidence"]))
            resumed = CampaignEngine(campaign, people, scenarios, target, run_dir, tasks=tasks, model=model,
                                     resume=True, retry=marc)
            result = await resumed.run()
            events = read_run_events(run_dir)
            self.assertEqual(result["status"], "pass",
                             [event for event in events if event["type"] in {"session.error", "step.failed"}])
            starts = [event for event in events if event["type"] == "session.started"]
            self.assertEqual(Counter(event["persona"] for event in starts),
                             {"eric": 1, "alice": 1, "marc": 2, "dany": 1})
            retry = next(event["seq"] for event in events if event.get("retry_sessions"))
            self.assertEqual([event["persona"] for event in starts if event["seq"] > retry], ["marc", "dany"])
            succeeded = next(event["seq"] for event in events if event["type"] == "session.completed"
                             and event["session_id"] == marc and event["outcome"] == "pass")
            dany = next(event for event in starts if event["persona"] == "dany")
            self.assertEqual(dany["attempt"], 1)
            self.assertGreater(dany["seq"], succeeded)
            self.assertEqual(len(startup_log.read_text(encoding="utf-8").splitlines()), 1)
            startup = next(event["value"] for event in events if event.get("metric") == "desktop_startup_ms"
                           and event.get("session_id") == dany["session_id"])
            self.assertGreaterEqual(startup, slow_ms)

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            startup_log = Path(directory) / "startup.log"
            with patch.object(SessionRunner, "_check_expect", fail_after_observation), \
                 patch.object(DesktopDriver, "launch", launch_slow_window):
                asyncio.run(run())

    def test_visual_recovery_clicks_a_drawn_control(self):
        from PIL import Image, ImageDraw

        from testmyapp.drivers.image_match import ImageTemplate

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "contrôle.png"
            template = Image.new("RGB", (46, 34), "#242843")
            drawing = ImageDraw.Draw(template)
            drawing.rectangle((3, 3, 41, 30), fill="#0f9688")
            drawing.polygon(((9, 15), (21, 8), (34, 22), (15, 25)), fill="#ffe54d")
            drawing.rectangle((4, 26, 9, 30), fill="#d65768")
            template.save(path)
            locator = ImageTemplate({"path": str(path), "confidence": .96})
            frame = Image.new("RGB", (140, 90), "#fff")
            frame.paste(template, (5, 7))
            self.assertEqual(locator.locate(frame)["bounds"], [5, 7, 46, 34])
            frame.paste(template, (65, 32))
            self.assertIsNone(locator.locate(frame))
            limited = ImageTemplate({"path": str(path), "confidence": .96, "region": [60, 30, 60, 45]})
            self.assertEqual(limited.locate(frame)["bounds"], [65, 32, 46, 34])

            target = Target.from_toml(load_toml(ASSETS / "targets" / "demo-windows.toml"))
            target.args.extend(["-VisualTemplate", str(path)])
            target.templates["Commande dessinée"] = {"path": str(path), "confidence": .96}
            scenario = Scenario("image", steps=[Step("Cliquer la commande dessinée", actions=[
                {"type": "click", "name": "Commande dessinée"},
            ], expects=[Expectation("visible", selector="Clic image recu")], screenshot="both")])
            campaign = Campaign("image", target.id, ["eric"], [scenario.id], speed="turbo", score_pass=0)
            engine = CampaignEngine(campaign, [builtin_personas()["eric"]], [scenario], target, root / "run")
            result = asyncio.run(engine.run())
            self.assertEqual(result["status"], "pass", [e for e in engine.bus.all_events
                             if e["type"] in {"session.error", "step.failed"}])
            actions = [e for e in engine.bus.all_events if e["type"] == "action.performed"]
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["resolution"], "image")
            self.assertGreaterEqual(actions[0]["image_match"]["confidence"], .96)

    def test_reference_app_reports_action_freeze_and_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, result = run_fixture("recette-windows", Path(directory))
            by_scenario = {s.scenario_id: s for s in engine.state.values()}
            self.assertEqual(by_scenario["windows-action"].outcome, "pass", by_scenario)
            self.assertEqual(by_scenario["windows-gel"].outcome, "pass")
            self.assertGreaterEqual(max(by_scenario["windows-gel"].freezes_ms), 1000)
            self.assertTrue(by_scenario["windows-crash"].crashed)
            self.assertEqual(result["status"], "error")
            self.assertTrue(any(e["type"] == "target.crash" and e.get("exit_code") == 23
                                for e in engine.bus.all_events))
