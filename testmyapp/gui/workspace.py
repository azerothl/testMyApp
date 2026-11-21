"""Accueil local, projets enregistrés et historique de campagnes."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from testmyapp.campaign_models import MODEL_ID, load_model, proof_options, save_model

from testmyapp.config import Campaign, ConfigError, Persona, Scenario, Target, load_toml
from testmyapp.gui.server import REPORT_CSP, STATIC_DIR, GuiHost, _local_origin, broadcast_deltas, create_app
from testmyapp.personas.library import load_personas
from testmyapp.report.comparison import compare_reports, execution_context


def is_project(path: Path) -> bool:
    return any((path / folder).is_dir() for folder in ("campaigns", "runs"))


def persona_profile(persona: Persona) -> dict:
    input_label = {"keyboard": "Clavier", "mouse": "Souris", "touch": "Écran tactile"}
    if persona.motor == "keyboard_only":
        interaction = "Clavier uniquement"
    else:
        interaction = input_label.get(persona.interaction_input, persona.interaction_input)
    if persona.screen_reader:
        interaction += " avec lecteur d’écran"
    skill = "Très à l’aise" if persona.tech_savviness >= .75 else "Débutant" if persona.tech_savviness <= .35 else "À l’aise avec les outils courants"
    pace = "Rapide" if persona.speed_factor < .9 else "Prend son temps" if persona.speed_factor > 1.2 else "Rythme modéré"
    recovery = max(persona.on_error, key=persona.on_error.get, default=None)
    return {"id": persona.id, "name": persona.name, "interaction": interaction,
            "skill": skill, "pace": pace, "bio": persona.bio, "goals": persona.goals_primary,
            "recovery": {"retry": "Réessayer", "explore": "Chercher une autre façon de faire",
                         "ask_help": "Chercher de l’aide", "abandon": "Abandonner"}.get(recovery)}


class Projects:
    def __init__(self, file: Path) -> None:
        self.file = file
        try:
            self.entries = json.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.entries = {}
        if not isinstance(self.entries, dict):
            raise ConfigError(f"Liste des projets invalide : {file}")

    def add(self, path: Path, name: str | None = None) -> dict:
        root = path.expanduser().resolve()
        if not is_project(root):
            raise ConfigError("Choisissez un dossier TestMyApp qui contient un dossier « campaigns » ou « runs ».")
        key = sha256(str(root).encode("utf-8")).hexdigest()[:20]
        old = self.entries.get(key, {})
        project = {"id": key, "name": (name or "").strip() or old.get("name") or root.name or str(root),
                   "path": str(root)}
        if old == project:
            return project
        updated = {**self.entries, key: project}
        self.file.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.file, updated)
        self.entries = updated
        return project


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, value: dict) -> None:
    tmp = None
    try:
        with NamedTemporaryFile(dir=path.parent, encoding="utf-8", mode="w", delete=False) as out:
            tmp = Path(out.name)
            json.dump(value, out, ensure_ascii=False, indent=2)
        tmp.replace(path)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def _version(value) -> str:
    if not isinstance(value, str) or len(value) > 100 or any(ord(c) < 32 for c in value):
        raise HTTPException(422, "Indiquez la version de l’application sur une seule ligne.")
    return value.strip()


def _stored_file(run: Path, relative: str) -> Path | None:
    file = run / relative
    return file if file.is_file() and file.resolve() == file else None


def _unfinished(journal: Path) -> bool:
    with journal.open("rb") as handle:
        handle.seek(max(0, journal.stat().st_size - 16384))
        lines = handle.read().splitlines()
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except (UnicodeError, ValueError):
            continue
        if isinstance(event, dict) and "type" in event:
            return event["type"] != "campaign.completed"
    return True


def _run_entry(run: Path) -> dict | None:
    manifest = _stored_file(run, "campaign.json")
    html = _stored_file(run, "report/report.html")
    pdf = _stored_file(run, "report/report.pdf")
    journal = _stored_file(run, "events.jsonl")
    timestamp = manifest or html or pdf or journal
    if timestamp is None:
        return None
    config = _json(manifest) if manifest else {}
    summary = _stored_file(run, "report/report.json")
    report = _json(summary) if summary else {}
    has_report = bool(html or pdf)
    status = report.get("status") or ("archived" if has_report else "interrupted")
    workflow = (config.get("model") or {}).get("version") == 2
    if workflow and summary and journal and journal.stat().st_mtime_ns > summary.stat().st_mtime_ns and _unfinished(journal):
        status = "interrupted"
    if pdf and summary and pdf.stat().st_mtime_ns < summary.stat().st_mtime_ns:
        pdf = None
    labels = {session["session_id"]: session.get("scenario") for session in report.get("sessions", [])}
    for event in report.get("evidence", {}).values():
        if event.get("type") == "session.started":
            data = event.get("data", {})
            if data.get("scenario_name"):
                labels[event["session_id"]] = f"{data.get('persona_name')} · {data['scenario_name']}"
    retry = [{"id": session["session_id"], "label": labels[session["session_id"]]}
             for session in report.get("sessions", []) if session["outcome"] in {"fail", "error", "abandoned"}]
    campaign = report.get("campaign")
    campaign_id = config.get("campaign_id") or (campaign.get("id") if isinstance(campaign, dict) else None)
    try:
        date = datetime.fromisoformat(config["frozen_at"]).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        date = datetime.fromtimestamp(timestamp.stat().st_mtime, timezone.utc)
    return {"id": run.name, "campaign": campaign_id, "status": status, "score": report.get("score"),
            "date": date.isoformat(),
            "report": bool(html), "pdf": bool(pdf), "replay": bool(journal and journal.stat().st_size),
            "comparable": bool(report and status != "interrupted"),
            "app_version": config.get("app_version"),
            "retry": retry if workflow and status in {"fail", "error"} else [],
            "rerunnable": bool(has_report and config.get("campaign_id") and config.get("personas")),
            "resumable": bool(config.get("campaign_id") and status == "interrupted")}


def _history(root: Path) -> list[dict]:
    runs = root / "runs"
    if not runs.is_dir() or runs.resolve() != runs:
        return []
    entries = []
    for run in runs.iterdir():
        try:
            if run.is_dir() and run.resolve() == run and (entry := _run_entry(run)):
                entries.append(entry)
        except (OSError, RuntimeError):
            continue
    return sorted(entries, key=lambda entry: entry["date"], reverse=True)


def create_workspace(projects: Projects) -> FastAPI:
    floors = {}
    active: tuple[str, GuiHost] | None = None

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            tasks = [task for _, host, broadcaster in floors.values()
                     for task in (host.task, broadcaster) if task and not task.done()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="TestMyApp", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    async def floor_app(scope, receive, send):
        selected = floors.get(scope["path_params"]["floor_id"])
        if selected:
            await selected[0](scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            response = RedirectResponse("/", status_code=303)
            await response(scope, receive, send)

    app.mount("/floor/{floor_id}", floor_app)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if not _local_origin(request.headers):
            return PlainTextResponse("Origine locale requise", status_code=403)
        return await call_next(request)

    def project_root(project_id: str) -> Path:
        project = projects.entries.get(project_id)
        if project is None:
            raise HTTPException(404, "Projet introuvable.")
        return Path(project["path"])

    def run_path(project_id: str, run_id: str) -> Path:
        runs = project_root(project_id) / "runs"
        run = runs / run_id
        if run.parent != runs or run_id in {".", ".."} or run.resolve() != run or not run.is_dir():
            raise HTTPException(404, "Campagne introuvable.")
        return run

    def live_url(run: Path | None = None) -> str | None:
        if active:
            url, host = active
            if host.task and not host.task.done() and (run is None or run == host.run_dir):
                return url
        return None

    def reference_path(project_id: str) -> Path:
        path = project_root(project_id) / "campaign-reference.json"
        if path.resolve() != path:
            raise HTTPException(422, "La référence doit se trouver dans le projet.")
        return path

    def comparison_source(project_id: str, run_id: str) -> dict:
        run = run_path(project_id, run_id)
        file = _stored_file(run, "report/report.json")
        manifest = _stored_file(run, "campaign.json")
        report = _json(file) if file else {}
        if not report or live_url(run) or (_run_entry(run) or {}).get("status") == "interrupted":
            raise HTTPException(409, "Sélectionnez une exécution terminée avec un rapport.")
        return {"id": run_id, "report": report,
                "context": execution_context(report, _json(manifest) if manifest else {})}

    @app.get("/")
    async def home():
        return FileResponse(STATIC_DIR / "home.html")

    @app.get("/api/projects")
    async def list_projects():
        return sorted(projects.entries.values(), key=lambda project: project["name"].casefold())

    @app.post("/api/projects", status_code=201)
    async def add_project(data: dict):
        raw_path = data.get("path")
        name = data.get("name")
        if not isinstance(raw_path, str) or not raw_path.strip() or (name is not None and not isinstance(name, str)):
            raise HTTPException(422, "Indiquez le chemin du dossier TestMyApp.")
        try:
            path = Path(raw_path.strip().strip('"')).expanduser()
            if not path.is_absolute():
                raise HTTPException(422, "Indiquez le chemin complet du dossier TestMyApp.")
            return projects.add(path, name)
        except (OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/projects/{project_id}")
    async def project_info(project_id: str):
        root = project_root(project_id)
        campaigns = []
        warnings = []
        catalog = {}
        custom_task_targets = set()
        targets = []
        for path in sorted((root / "targets").glob("*.toml")):
            try:
                target = Target.from_toml(load_toml(path), str(path))
                targets.append({"id": target.id, "free": target.supports_free_tasks})
                if target.supports_free_tasks:
                    custom_task_targets.add(target.id)
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                warnings.append(str(exc))
        for path in sorted((root / "scenarios").glob("*.toml")):
            try:
                scenario = Scenario.from_toml(load_toml(path), str(path))
                catalog[scenario.id] = {"id": scenario.id, "name": scenario.name or scenario.id,
                                        "description": scenario.description, "text": scenario.task_text}
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                warnings.append(str(exc))
        for path in sorted((root / "campaigns").glob("*.toml")):
            try:
                raw = load_toml(path)
                campaign = Campaign.from_toml(raw, str(path))
                custom = campaign.target_id in custom_task_targets
                task_ids = (list(dict.fromkeys([*campaign.scenarios, *campaign.discovery.values()]))
                            if campaign.discovery or custom else [])
                if missing := [sid for sid in task_ids if sid not in catalog]:
                    raise ConfigError(f"{campaign.id} : tâches introuvables ({', '.join(missing)}).")
                name = raw.get("name")
                campaigns.append({"id": campaign.id, "name": name if isinstance(name, str) else "",
                                  "target": campaign.target_id, "personas": campaign.personas,
                                  "tasks": [catalog[sid] for sid in task_ids], "discovery": campaign.discovery,
                                  "custom_task": custom, "depends_on": campaign.depends_on})
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                warnings.append(str(exc))
        proofs = {}
        try:
            proofs = proof_options(root)
        except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
            warnings.append(str(exc))
        models = []
        for path in sorted((root / "campaign-models").glob("modele-*.json")):
            try:
                model = load_model(root, path.stem)
                if model.get("id") != path.stem:
                    raise ConfigError("Identifiant de modèle incohérent.")
                models.append(model)
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                warnings.append(str(exc))
        personas = []
        if campaigns or targets:
            try:
                library = load_personas(root / "personas")
                personas = [persona_profile(p) for p in sorted(library.values(), key=lambda p: p.name.casefold())]
                for campaign in campaigns:
                    missing = [pid for pid in campaign["personas"] if pid not in library]
                    if missing:
                        warnings.append(f"{campaign['id']} : personas introuvables ({', '.join(missing)}).")
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                warnings.append(f"Impossible de charger les personas : {exc}")
        results = _history(root)
        if active and live_url() and active[1].run_dir.parent == root / "runs":
            current = active[1].run_dir
            row = next((entry for entry in results if entry["id"] == current.name), None)
            if row:
                row.update(status="running", resumable=False, live=active[0])
            else:
                results.insert(0, {"id": current.name, "campaign": active[1].engine.campaign.id,
                                   "status": "running", "live": active[0], "date": datetime.now(timezone.utc).isoformat()})
        reference = _json(reference_path(project_id))
        public_reference = {key: reference[key] for key in ("id", "context", "saved_at")} if reference else None
        return {"available": is_project(root), "campaigns": campaigns, "personas": personas,
                "runs": results, "warnings": warnings, "models": models, "targets": targets,
                "proofs": list(proofs.values()), "reference": public_reference}

    async def show_floor(host: GuiHost, project_id: str, *, live: bool):
        nonlocal active
        key = uuid4().hex
        url = f"/floor/{key}/"
        host.workspace_report = app.url_path_for("read_report", project_id=project_id,
                                                 run_id=host.run_dir.name, filename="report.html")
        floors[key] = (create_app(host, workspace=True), host, asyncio.create_task(broadcast_deltas(host)))
        if live:
            await host.command("start")
            active = (url, host)
        return {"url": url}

    def engine_for(project_id: str, **options):
        from testmyapp.cli import _build_engine

        if live_url():
            raise HTTPException(409, "Une campagne est déjà en cours. Attendez la fin ou arrêtez-la depuis son suivi.")
        root = project_root(project_id)
        runs = root / "runs"
        if runs.resolve() != runs:
            raise HTTPException(422, "Le dossier des résultats doit se trouver dans le projet.")
        try:
            return _build_engine(root, **options)
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/projects/{project_id}/models")
    async def store_model(project_id: str, data: dict):
        from testmyapp.cli import _build_engine

        root = project_root(project_id)
        model_id = data.get("id")
        try:
            if model_id is None:
                model_id = "modele-" + uuid4().hex[:16]
            elif not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id):
                raise ConfigError("Identifiant de modèle invalide.")
            else:
                load_model(root, model_id)
            engine = _build_engine(root, model={**data, "id": model_id})
            save_model(root, engine.model)
            return engine.model
        except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/projects/{project_id}/start")
    async def start(project_id: str, data: dict):
        campaign = data.get("campaign")
        if not isinstance(campaign, str) or not campaign:
            raise HTTPException(422, "Sélectionnez la campagne à lancer.")
        personas = data.get("personas")
        if "personas" in data and (not isinstance(personas, list) or not personas
                                   or any(not isinstance(pid, str) or not pid for pid in personas)):
            raise HTTPException(422, "Sélectionnez au moins un persona valide.")
        version = _version(data.get("app_version", ""))
        return await show_floor(GuiHost(engine_for(project_id, campaign_id=campaign, persona=personas,
                                                   tasks=data.get("tasks"), app_version=version)),
                                project_id, live=True)

    @app.post("/api/projects/{project_id}/runs/{run_id}/{action}")
    async def open_run(project_id: str, run_id: str, action: str, data: dict | None = None):
        run = run_path(project_id, run_id)
        if action == "reference":
            reference = comparison_source(project_id, run_id)
            reference["saved_at"] = datetime.now(timezone.utc).isoformat()
            _write_json(reference_path(project_id), reference)
            return {key: reference[key] for key in ("id", "context", "saved_at")}
        if action == "replay":
            host = GuiHost()
            if not _stored_file(run, "events.jsonl") or not host.start_replay(run):
                raise HTTPException(404, "Aucun événement à rejouer pour cette campagne.")
            return await show_floor(host, project_id, live=False)
        if action == "resume":
            entry = _run_entry(run)
            if not entry or not entry["resumable"]:
                raise HTTPException(409, "Cette campagne ne peut pas être reprise.")
            return await show_floor(GuiHost(engine_for(project_id, resume=run)), project_id, live=True)
        if action == "rerun":
            manifest = _stored_file(run, "campaign.json")
            config = _json(manifest) if manifest else {}
            if not config.get("campaign_id") or not config.get("personas") or not config.get("scenarios"):
                raise HTTPException(409, "Les réglages de la campagne archivée sont indisponibles.")
            version = _version((data or {}).get("app_version", config.get("app_version", "")))
            engine = engine_for(project_id, campaign_id=config["campaign_id"],
                                persona=config.get("persona_order", config["personas"]),
                                scenario=config.get("scenario_order", config["scenarios"]),
                                speed=config.get("speed"), seed=config.get("seed"), tasks=config.get("tasks"),
                                model=config.get("model"), app_version=version)
            if config.get("depends_on", {}) != engine.campaign.depends_on:
                raise HTTPException(409, "Les prérequis de cette campagne ont changé.")
            return await show_floor(GuiHost(engine), project_id, live=True)
        raise HTTPException(404)

    @app.post("/api/projects/{project_id}/runs/{run_id}/tasks/retry")
    async def retry_task(project_id: str, run_id: str, data: dict):
        run = run_path(project_id, run_id)
        session = data.get("session")
        entry = _run_entry(run)
        if not entry or not any(item["id"] == session for item in entry["retry"]):
            raise HTTPException(409, "Cette tâche ne peut pas être reprise.")
        return await show_floor(GuiHost(engine_for(project_id, resume=run, retry=session)), project_id, live=True)

    @app.get("/api/projects/{project_id}/compare")
    async def compare(project_id: str, before: str, after: str):
        if before != "@reference" and before == after:
            raise HTTPException(422, "Sélectionnez deux exécutions différentes.")
        left = _json(reference_path(project_id)) if before == "@reference" else comparison_source(project_id, before)
        if not left:
            raise HTTPException(404, "Aucune exécution de référence n’est enregistrée.")
        right = comparison_source(project_id, after)
        return {**compare_reports(left["report"], right["report"]),
                "contexts": {key: {**source["context"], "run": source["id"]}
                             for key, source in (("before", left), ("after", right))}}

    @app.get("/projects/{project_id}/runs/{run_id}/captures/{filename}")
    async def read_capture(project_id: str, run_id: str, filename: str):
        if Path(filename).name != filename or Path(filename).suffix.lower() not in {".png", ".svg"}:
            raise HTTPException(404)
        file = _stored_file(run_path(project_id, run_id), "screenshots/" + filename)
        if file is None:
            raise HTTPException(404)
        return FileResponse(file, headers=REPORT_CSP)

    @app.get("/projects/{project_id}/runs/{run_id}/report/{filename}")
    async def read_report(project_id: str, run_id: str, filename: str):
        if filename not in {"report.html", "report.pdf"}:
            raise HTTPException(404)
        run = run_path(project_id, run_id)
        entry = _run_entry(run)
        file = _stored_file(run, "report/" + filename)
        if not file or (filename == "report.pdf" and not (entry and entry["pdf"])):
            raise HTTPException(404, "Rapport introuvable.")
        if filename == "report.html" and entry and entry["retry"]:
            url = escape("/?" + urlencode({"project": project_id, "retry": run_id}) + "#recovery", quote=True)
            source = file.read_text(encoding="utf-8")
            action = f'<p><a href="{url}">Reprendre une tâche en échec dans TestMyApp</a></p>'
            return HTMLResponse(source.replace("</h1>", "</h1>" + action, 1), headers=REPORT_CSP)
        return FileResponse(file, headers=REPORT_CSP if filename == "report.html" else None)

    @app.get("/report/report.html")
    async def latest_report():
        reports = [(run["date"], project["id"], run["id"])
                   for project in projects.entries.values() for run in _history(Path(project["path"])) if run["report"]]
        if not reports:
            return RedirectResponse("/")
        _, project, run = max(reports)
        return RedirectResponse(app.url_path_for("read_report", project_id=project, run_id=run, filename="report.html"))

    return app
