"""CLI TestMyApp (spec §14).

Commandes : init, persona (list/show/generate), target check, run
(tui|gui|headless), report, replay, compare, selftest, gui.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import webbrowser
from pathlib import Path

from testmyapp import __version__
from testmyapp.config import ConfigError

PROJECT_FILES = {
    "personas/README.md": "Déposez ici vos personas *.toml (ou clones des archétypes embarqués).\n",
    "targets/README.md": "Déposez ici vos cibles *.toml (type = web | sim | desktop).\n",
    "scenarios/README.md": "Déposez ici vos scénarios *.toml.\n",
    "campaigns/README.md": "Déposez ici vos campagnes *.toml.\n",
    "runs/README.md": "Résultats des campagnes (un dossier par run, spec §13).\n",
}


def _project_root(explicit: str | None) -> Path:
    return Path(explicit).resolve() if explicit else Path.cwd()


def _load_project(root: Path):
    from testmyapp.config import (Campaign, Scenario, Target, load_toml)
    from testmyapp.personas.library import load_personas

    personas = load_personas(root / "personas")
    targets: dict[str, Target] = {}
    tdir = root / "targets"
    if tdir.is_dir():
        for p in sorted(tdir.glob("*.toml")):
            t = Target.from_toml(load_toml(p), str(p))
            targets[t.id] = t
    scenarios: dict[str, Scenario] = {}
    sdir = root / "scenarios"
    if sdir.is_dir():
        for p in sorted(sdir.glob("*.toml")):
            s = Scenario.from_toml(load_toml(p), str(p))
            scenarios[s.id] = s
    campaigns: dict[str, Campaign] = {}
    cdir = root / "campaigns"
    if cdir.is_dir():
        for p in sorted(cdir.glob("*.toml")):
            c = Campaign.from_toml(load_toml(p), str(p))
            campaigns[c.id] = c
    return personas, targets, scenarios, campaigns


# --------------------------------------------------------------------- init
def cmd_init(args: argparse.Namespace) -> int:
    root = _project_root(args.dir)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    for rel, content in PROJECT_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    # assets par défaut : cible sim + campagne démo + scénario démo
    from importlib import resources

    assets = resources.files("testmyapp") / "assets"
    for src, dst in [
        ("targets/demo-sim.toml", "targets/demo-sim.toml"),
        ("scenarios/commander-produit.toml", "scenarios/commander-produit.toml"),
        ("campaigns/demo.toml", "campaigns/demo.toml"),
        ("campaigns/recette-web.toml", "campaigns/recette-web.toml"),
        ("campaigns/recette-web-corrigee.toml", "campaigns/recette-web-corrigee.toml"),
        ("targets/demo-web.toml", "targets/demo-web.toml"),
        ("targets/demo-web-fixed.toml", "targets/demo-web-fixed.toml"),
        ("campaigns/recette-windows.toml", "campaigns/recette-windows.toml"),
        ("targets/demo-windows.toml", "targets/demo-windows.toml"),
        ("scenarios/windows-action.toml", "scenarios/windows-action.toml"),
        ("scenarios/windows-gel.toml", "scenarios/windows-gel.toml"),
        ("scenarios/windows-crash.toml", "scenarios/windows-crash.toml"),
    ]:
        dest = root / dst
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text((assets / dst.split("/", 1)[0] / Path(dst).name)
                            .read_text(encoding="utf-8"), encoding="utf-8")
    # cloner un persona exemple
    from testmyapp.personas.library import builtin_personas, render_toml

    example = root / "personas" / "exemple-nadine.toml"
    if not example.exists():
        lib = builtin_personas()
        example.write_text(render_toml(lib["nadine"]), encoding="utf-8")
    print(f"✅ Projet initialisé dans {root}")
    print("   - cible démo : targets/demo-sim.toml (application simulée)")
    print("   - campagne   : campaigns/demo.toml (4 personas × 1 scénario)")
    print("   → lancer : testmyapp run -c campaigns/demo.toml --mode headless")
    return 0


# ------------------------------------------------------------------ persona
def cmd_persona(args: argparse.Namespace) -> int:
    from testmyapp.personas.library import builtin_personas, generate_variants, load_personas, render_toml

    if args.action == "list":
        root = _project_root(args.dir)
        lib = load_personas(root / "personas")
        for pid, p in sorted(lib.items()):
            print(f"{pid:12} {p.name:22} age={p.age:<3} savviness={p.tech_savviness:.2f} "
                  f"speed={p.speed_factor:.1f} vision={p.vision} input={p.input_device}")
        return 0
    if args.action == "show":
        pid = args.id or getattr(args, "id_opt", None)
        if not pid:
            print("usage : testmyapp persona show <id> [--builtin]", file=sys.stderr)
            return 1
        lib = builtin_personas() if args.builtin else load_personas(_project_root(args.dir) / "personas")
        p = lib.get(pid)
        if not p:
            print(f"❌ persona inconnu : {args.id}", file=sys.stderr)
            return 1
        print(render_toml(p))
        return 0
    if args.action == "generate":
        lib = builtin_personas()
        base = lib.get(args.archetype)
        if not base:
            print(f"❌ archétype inconnu : {args.archetype} "
                  f"(disponibles : {', '.join(sorted(lib))})", file=sys.stderr)
            return 1
        variants = generate_variants(base, args.count, args.seed)
        outdir = Path(args.out) if args.out else _project_root(args.dir) / "personas"
        outdir.mkdir(parents=True, exist_ok=True)
        for v in variants:
            path = outdir / f"{v.id}.toml"
            path.write_text(render_toml(v), encoding="utf-8")
            print(f"✅ {path}")
        return 0
    print("action inconnue", file=sys.stderr)
    return 1


# ------------------------------------------------------------------- target
def cmd_target_check(args: argparse.Namespace) -> int:
    root = _project_root(args.dir)
    _, targets, _, _ = _load_project(root)
    target = targets.get(args.id)
    if not target:
        print(f"❌ cible inconnue : {args.id} (disponibles : {', '.join(targets) or 'aucune'})",
              file=sys.stderr)
        return 1
    print(f"Vérification de la cible {target.id} (type={target.type})…")
    if target.type == "web":
        import urllib.request

        try:
            with urllib.request.urlopen(target.base_url, timeout=5) as resp:
                print(f"✅ HTTP {resp.status} — {target.base_url}")
        except Exception as exc:
            print(f"❌ injoignable : {exc}")
            return 1
    elif target.type == "sim":
        print("✅ cible simulée : toujours disponible, déterministe par graine")
    else:
        import shutil

        if sys.platform != "win32":
            print("❌ UI Automation nécessite Windows", file=sys.stderr)
            return 1
        if not shutil.which(target.executable) and not Path(target.executable).is_file():
            print(f"❌ exécutable introuvable : {target.executable}", file=sys.stderr)
            return 1
        try:
            import psutil  # noqa: F401
            import pywinauto  # noqa: F401
            from testmyapp.drivers.image_match import ImageTemplate

            for spec in target.templates.values():
                ImageTemplate(spec)
        except ImportError:
            print('❌ installez les prérequis avec pip install "testmyapp[desktop]"', file=sys.stderr)
            return 1
        except (OSError, ValueError) as exc:
            print(f"❌ modèle de repli inutilisable : {exc}", file=sys.stderr)
            return 1
        print(f"✅ prérequis UI Automation accessibles : {target.executable}")
        if target.templates:
            print(f"✅ modèles de repli validés : {len(target.templates)}")
    return 0


# ---------------------------------------------------------------------- run
def _build_engine(root: Path, *, campaign_id=None, resume=None, persona=None,
                  scenario=None, speed=None, seed=None, tasks=None, model=None, retry=None,
                  app_version=""):
    personas, targets, scenarios, campaigns = _load_project(root)
    run_dir = None
    resume_config = {}
    if resume is not None:
        if resume:
            run_dir = (root / resume).resolve()
        else:
            runs = [p.parent for p in (root / "runs").glob("*/campaign.json")]
            if not runs:
                raise ConfigError("Aucun dossier de campagne à reprendre")
            run_dir = max(runs, key=lambda p: (p / "campaign.json").stat().st_mtime_ns)
        config_path = run_dir / "campaign.json"
        if not config_path.is_file():
            raise ConfigError(f"Configuration de run introuvable : {config_path}")
        resume_config = json.loads(config_path.read_text(encoding="utf-8"))
        if (not isinstance(resume_config, dict)
                or not isinstance(resume_config.get("campaign_id"), str)
                or not all(isinstance(resume_config.get(key), list) for key in ("personas", "scenarios"))):
            raise ConfigError("Le dossier de cette campagne contient une configuration incomplète")

    from testmyapp.campaign_models import MODEL_ID, compile_model, load_model, proof_options
    from testmyapp.core.engine import CampaignEngine

    if resume_config:
        model = resume_config.get("model")
    if model is None and campaign_id and MODEL_ID.fullmatch(campaign_id):
        model = load_model(root, campaign_id)
        if model.get("id") != campaign_id:
            raise ConfigError("Le modèle enregistré ne correspond pas à la campagne.")
    if model is not None:
        model, campaign, assignments, generated = compile_model(
            model, personas, targets, scenarios, proof_options(root))
        return CampaignEngine(campaign, [personas[pid] for pid in campaign.personas], list(generated.values()),
                              targets[campaign.target_id], run_dir or (root / "runs" / _run_name()),
                              speed=speed or resume_config.get("speed"),
                              seed=seed if seed is not None else resume_config.get("seed"),
                              resume=resume is not None, tasks=assignments, model=model,
                              retry=retry, app_version=resume_config.get("app_version", app_version))

    campaign = None
    campaign_id = campaign_id or resume_config.get("campaign_id")
    if campaign_id:
        campaign = campaigns.get(campaign_id) or campaigns.get(Path(campaign_id).stem)
        if campaign is None:
            raise ConfigError(f"Campagne inconnue : {campaign_id}")
    if campaign is None and campaigns:
        campaign = campaigns.get("demo") or next(iter(campaigns.values()))
    if campaign is None:
        raise ConfigError("Aucune campagne configurée dans le dossier campaigns")
    if persona:
        campaign.personas = persona
    elif resume_config:
        campaign.personas = resume_config.get("persona_order", resume_config["personas"])
    if scenario:
        campaign.scenarios = scenario
    elif resume_config:
        campaign.scenarios = resume_config.get("scenario_order", resume_config["scenarios"])
    target = targets.get(campaign.target_id)
    if target is None:
        raise ConfigError(f"Cible inconnue : {campaign.target_id}")
    if resume_config:
        tasks = resume_config.get("tasks")
    elif tasks is None and campaign.depends_on:
        tasks = {pid: {"scenario": campaign.discovery.get(pid)} for pid in campaign.personas}
    if tasks is not None:
        if not isinstance(tasks, dict) or tasks.keys() != set(campaign.personas):
            raise ConfigError("Assignez une tâche à chaque persona sélectionné.")
        allowed = (set(campaign.scenarios) | set(campaign.discovery.values())
                   if campaign.discovery or target.supports_free_tasks else set())
        if resume_config:
            allowed = set(resume_config["scenarios"])
        for pid, task in tasks.items():
            if isinstance(task, dict) and ("goal" in task or "expected" in task):
                from testmyapp.config import Expectation, Scenario, Step

                if not target.supports_free_tasks or task.keys() != {"goal", "expected"}:
                    raise ConfigError(f"Tâche libre indisponible pour {pid} sur cette cible.")
                if any(not isinstance(value, str) or not value.strip() or len(value) > 500
                       or any(ord(char) < 32 for char in value) for value in task.values()):
                    raise ConfigError(f"Indiquez un objectif et un texte attendu valides pour {pid}.")
                sid = f"decouverte-{pid}"
                if sid in scenarios:
                    raise ConfigError(f"Identifiant de tâche libre déjà utilisé : {sid}.")
                task["goal"], task["expected"] = task["goal"].strip(), task["expected"].strip()
                scenarios[sid] = Scenario(sid, task["goal"], steps=[
                    Step(task["goal"], mode="discovery", explore_budget=10,
                         expects=[Expectation("text_contains", selector=task["expected"], timeout_ms=3500)])
                ])
                continue
            if not isinstance(task, dict) or task.keys() - {"scenario", "text"}:
                raise ConfigError(f"Tâche invalide pour {pid}.")
            sid = task.get("scenario")
            if not isinstance(sid, str) or sid not in allowed or sid not in scenarios:
                raise ConfigError(f"Tâche inconnue pour {pid}.")
            if "text" in task:
                value = task["text"]
                if (scenarios[sid].task_text is None or not isinstance(value, str) or not value.strip()
                        or len(value) > 1000 or any(ord(char) < 32 for char in value)):
                    raise ConfigError(f"Indiquez un message valide sur une ligne pour {pid} (1000 caractères au maximum).")
            elif scenarios[sid].task_text is not None:
                task["text"] = scenarios[sid].task_text
        campaign.scenarios = list(dict.fromkeys(tasks[pid].get("scenario", f"decouverte-{pid}")
                                                for pid in campaign.personas))
    if any(len(ids) != len(set(ids)) for ids in (campaign.personas, campaign.scenarios)):
        raise ConfigError("La sélection contient des identifiants répétés")
    if campaign.depends_on:
        if not tasks:
            raise ConfigError("Une campagne avec des prérequis doit attribuer une tâche à chaque persona.")
        order = {pid: index for index, pid in enumerate(campaign.personas)}
        for pid, predecessor in campaign.depends_on.items():
            if pid in order and (predecessor not in order or order[predecessor] >= order[pid]):
                raise ConfigError(f"{predecessor} doit être sélectionné avant {pid}.")
    missing_p = [p for p in campaign.personas if p not in personas]
    missing_s = [s for s in campaign.scenarios if s not in scenarios]
    if missing_p or missing_s:
        raise ConfigError(f"Personas inconnus : {missing_p} ; scénarios inconnus : {missing_s}")

    if run_dir is None:
        run_dir = root / "runs" / _run_name()
    persona_list = [personas[pid] for pid in campaign.personas]
    scenario_list = [scenarios[sid] for sid in campaign.scenarios]

    return CampaignEngine(campaign, persona_list, scenario_list, target,
                          run_dir=run_dir, speed=speed or resume_config.get("speed"),
                          seed=seed if seed is not None else resume_config.get("seed"),
                          resume=resume is not None, tasks=tasks,
                          app_version=resume_config.get("app_version", app_version))


def cmd_run(args: argparse.Namespace) -> int:
    engine = _build_engine(_project_root(args.dir), campaign_id=args.campaign,
                           resume=args.resume, persona=args.persona, scenario=args.scenario,
                           speed=args.speed, seed=args.seed)
    mode = args.mode
    if mode == "gui":
        return _run_gui(engine, args.port, not args.no_browser)
    if mode == "tui":
        return _run_tui(engine)
    return _run_headless(engine, engine.campaign)


def _run_name() -> str:
    from datetime import datetime
    from uuid import uuid4

    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"


def _run_headless(engine, campaign) -> int:
    """Mode CI : sortie minimale, code retour selon score (§14)."""
    engine.bus.on(lambda ev: _headless_line(ev))
    result = asyncio.run(engine.run())
    score = result["score"]
    print(f"\nScore : {f'{score}/100' if score is not None else 'non mesuré'}")
    print(f"Rapport : {result['report']}")
    status = result["status"]
    print({"pass": "✅ campagne réussie",
           "fail": f"❌ étape critique, abandon ou score inférieur au seuil ({campaign.score_pass})",
           "error": "❌ erreur d'exécution ou campagne sans mesure fonctionnelle",
           "interrupted": "⚠ campagne interrompue"}[status])
    return {"pass": 0, "fail": 2, "error": 1, "interrupted": 1}[status]


def _headless_line(ev: dict) -> None:
    t = ev.get("type", "")
    if t == "session.started":
        print(f"▶ session {ev.get('session_id')}")
    elif t == "step.failed":
        print(f"  ✗ étape {(ev.get('step_id', 0)) + 1} échouée")
    elif t == "session.completed":
        print(f"  ◼ {ev.get('outcome')} — humeur {ev.get('mood_final', 0):+.1f}")
    elif t == "session.error":
        print(f"  ⚠ erreur : {ev.get('error')}")


def _run_tui(engine) -> int:
    from testmyapp.tui.app import TestFloorTUI

    async def _go() -> dict:
        app = TestFloorTUI(engine.bus, engine)
        await app.run_async()
        return await app.finish_campaign()

    result = asyncio.run(_go())
    if result.get("report"):
        print(f"Rapport : {result['report']}")
    return {"pass": 0, "fail": 2, "interrupted": 1, "error": 1}[result["status"]]


def _run_gui(engine, port: int, open_browser: bool) -> int:
    from testmyapp.gui.server import GuiHost, run_gui_server

    host = GuiHost(engine, auto_start=True)
    print(f"GUI : http://127.0.0.1:{port}/ (Ctrl+C pour arrêter)")
    run_gui_server(host, port=port, open_browser=open_browser)
    result = host.result or {"status": "interrupted"}
    if result.get("report"):
        print(f"Rapport : {result['report']}")
    return {"pass": 0, "fail": 2, "interrupted": 1, "error": 1}[result["status"]]


# ------------------------------------------------------------------- report
def cmd_report(args: argparse.Namespace) -> int:
    path = Path(args.run) / "report" / f"report.{args.format}"
    if args.format == "pdf":
        from testmyapp.report.pdf import export_pdf

        source = path.with_suffix(".html")
        if source.is_file():
            try:
                path = export_pdf(source)
            except RuntimeError as exc:
                print(f"❌ {exc}", file=sys.stderr)
                return 1
    if not path.exists():
        print(f"❌ rapport introuvable : {path} (lancez d'abord une campagne)", file=sys.stderr)
        return 1
    if args.open:
        import os

        if sys.platform == "win32":
            os.startfile(str(path.resolve()))
        else:
            webbrowser.open(path.resolve().as_uri())
    else:
        print(path)
    return 0


# ------------------------------------------------------------------- replay
def cmd_replay(args: argparse.Namespace) -> int:
    from testmyapp.gui.server import GuiHost, run_gui_server

    host = GuiHost()
    ok = host.start_replay(Path(args.run), speed=args.speed)
    if not ok:
        print(f"❌ events.jsonl introuvable dans {args.run}", file=sys.stderr)
        return 1
    print(f"Replay de {args.run} — http://127.0.0.1:{args.port}/")
    run_gui_server(host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    if args.run:
        return cmd_replay(args)

    from testmyapp.gui.server import serve_local
    from testmyapp.gui.workspace import Projects, create_workspace, is_project

    projects = Projects(args.registry)
    root = _project_root(args.dir)
    if args.dir or is_project(root):
        projects.add(root, args.name)
    print(f"Accueil TestMyApp — http://127.0.0.1:{args.port}/")
    serve_local(create_workspace(projects), args.port, not args.no_browser)
    return 0


# ------------------------------------------------------------------ compare
def cmd_compare(args: argparse.Namespace) -> int:
    def load(run: Path) -> dict:
        p = Path(run) / "report" / "report.json"
        if not p.exists():
            print(f"❌ report.json introuvable : {p}", file=sys.stderr)
            sys.exit(1)
        return json.loads(p.read_text(encoding="utf-8"))

    a, b = load(Path(args.run_a)), load(Path(args.run_b))
    print(f"Comparaison : {args.run_a} (score {a['score']}) → {args.run_b} (score {b['score']})")
    if a["score"] is not None and b["score"] is not None:
        print(f"Δ score : {b['score'] - a['score']:+.1f}")
    aa = {x["name"]: x["score"] for x in a["axes"]}
    bb = {x["name"]: x["score"] for x in b["axes"]}
    print(f"{'axe':12} {'A':>6} {'B':>6} {'Δ':>7}")
    for name in sorted(aa.keys() | bb.keys()):
        left, right = aa.get(name), bb.get(name)
        delta = f"{right - left:+.1f}" if left is not None and right is not None else "—"
        print(f"{name:12} {str(left) if left is not None else '—':>6} "
              f"{str(right) if right is not None else '—':>6} {delta:>7}")
    neg_a = {f["title"] for f in a["negatives"]}
    neg_b = {f["title"] for f in b["negatives"]}
    fixed = neg_a - neg_b
    new = neg_b - neg_a
    if fixed:
        print("\n✅ corrigés :")
        for t in sorted(fixed):
            print(f"  - {t}")
    if new:
        print("\n❌ nouveaux :")
        for t in sorted(new):
            print(f"  - {t}")
    return 0


# ----------------------------------------------------------------- selftest
def cmd_selftest(args: argparse.Namespace) -> int:
    """Vérifie l'environnement (§14) : dépendances, navigateurs, drivers."""
    print("TestMyApp selftest")
    ok = True
    try:
        import tomllib  # noqa: F401

        print("✅ Python >= 3.11 (tomllib)")
    except ImportError:
        print("❌ Python 3.11+ requis")
        ok = False
    for mod, label in [("textual", "TUI (textual)"), ("fastapi", "GUI (fastapi)"),
                       ("uvicorn", "GUI (uvicorn)"), ("jinja2", "rapports (jinja2)")]:
        try:
            __import__(mod)
            print(f"✅ {label}")
        except ImportError:
            print(f"❌ {label} manquant — pip install -e .")
            ok = False
    try:
        import playwright  # type: ignore

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch(headless=True)
                b.close()
                print("✅ driver web (Playwright + Chromium)")
            except Exception as exc:
                print(f"⚠️ Playwright installé mais navigateur absent : "
                      f"`python -m playwright install chromium` ({exc})")
    except ImportError:
        print("⚠️ driver web indisponible — pip install \"testmyapp[web]\" (mode sim OK)")
    try:
        import pywinauto  # noqa: F401

        print("✅ driver desktop (pywinauto)")
    except ImportError:
        print("⚠️ driver desktop indisponible — pip install \"testmyapp[desktop]\" (optionnel)")
    from testmyapp.personas.library import builtin_personas

    lib = builtin_personas()
    print(f"✅ bibliothèque personas : {len(lib)} archétypes ({', '.join(sorted(lib))})")
    from testmyapp.drivers.sim import SIM_APP

    print(f"✅ app simulée : {len(SIM_APP)} pages")
    print("✅/❌ → " + ("environnement complet" if ok else "environnement partiel (le mode sim reste utilisable)"))
    return 0 if ok else 1


# ----------------------------------------------------------------- app démo
def cmd_demo(args: argparse.Namespace) -> int:
    """Lance la webapp d'exemple (cible démo réelle, driver Playwright)."""
    import uvicorn

    from testmyapp.demoapp.server import create_demo_app

    app = create_demo_app(fixed=getattr(args, "fixed", False))
    print(f"Webapp démo : http://127.0.0.1:{args.port}/ (Ctrl+C pour arrêter)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="testmyapp",
        description="Laboratoire de test par personas pixel art (spec TestMyApp).")
    parser.add_argument("--version", action="version", version=f"testmyapp {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold : dossiers + fichiers démo")
    p.add_argument("--dir", help="racine du projet (défaut : cwd)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("persona", help="gérer les personas")
    p.add_argument("action", choices=["list", "show", "generate"])
    p.add_argument("id", nargs="?", help="id du persona (show)")
    p.add_argument("--id", dest="id_opt", help="id du persona (variante : persona show --id X)")
    p.add_argument("--builtin", action="store_true", help="chercher parmi les archétypes embarqués")
    p.add_argument("--archetype", help="archétype de base (generate)")
    p.add_argument("--count", type=int, default=3, help="nombre de variants (generate)")
    p.add_argument("--seed", type=int, default=42, help="graine (generate)")
    p.add_argument("--out", help="dossier de sortie (generate)")
    p.add_argument("--dir", help="racine du projet")
    p.set_defaults(func=cmd_persona)

    p = sub.add_parser("target", help="vérifier une cible")
    p.add_argument("action", choices=["check"])
    p.add_argument("id", help="id de la cible")
    p.add_argument("--dir", help="racine du projet")
    p.set_defaults(func=cmd_target_check)

    p = sub.add_parser("run", help="lancer une campagne")
    p.add_argument("-c", "--campaign", help="chemin ou id de campagne (campaigns/*.toml)")
    p.add_argument("--mode", choices=["tui", "gui", "headless"], default="headless")
    p.add_argument("--port", type=int, default=7417, help="port local de la GUI")
    p.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur automatiquement")
    p.add_argument("--seed", type=int, help="graine (reproductibilité)")
    p.add_argument("--speed", choices=["realtime", "fast", "turbo"])
    p.add_argument("--persona", action="append", help="restreindre aux personas donnés (répétable)")
    p.add_argument("--scenario", action="append", help="restreindre aux scénarios donnés (répétable)")
    p.add_argument("--resume", nargs="?", const="", metavar="RUN",
                   help="reprendre un dossier de run (sans chemin : le plus récent)")
    p.add_argument("--dir", help="racine du projet")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="ouvrir/afficher un rapport")
    p.add_argument("run", help="dossier du run (runs/<id>)")
    p.add_argument("--format", choices=["md", "html", "json", "pdf"], default="html")
    p.add_argument("--open", action="store_true", help="ouvrir dans l'app par défaut")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("replay", help="rejouer un run dans la GUI")
    p.add_argument("run", help="dossier du run")
    p.add_argument("--port", type=int, default=7417)
    p.add_argument("--speed", type=float, default=4.0, help="accélération du replay")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("compare", help="comparer deux runs (régression)")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("selftest", help="vérifier l'environnement")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("demo", help="lancer la webapp d'exemple")
    p.add_argument("--port", type=int, default=7418)
    p.add_argument("--fixed", action="store_true", help="servir la variante avec les défauts de recette corrigés")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("gui", help="ouvrir les projets et leurs rapports")
    p.add_argument("run", nargs="?", help="dossier du run à rejouer")
    p.add_argument("--dir", help="ajouter ce dossier aux projets de l'accueil")
    p.add_argument("--name", help="nom du projet ajouté")
    p.add_argument("--registry", type=Path, default=Path.home() / ".testmyapp" / "projects.json",
                   help="fichier des projets enregistrés")
    p.add_argument("--speed", type=float, default=4.0, help="vitesse de lecture d'un replay")
    p.add_argument("--port", type=int, default=7417)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_gui)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, OSError, json.JSONDecodeError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
