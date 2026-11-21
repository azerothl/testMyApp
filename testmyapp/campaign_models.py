"""Modèles éditables et compilation des tâches pour le moteur."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile

from testmyapp.config import Campaign, ConfigError, Expectation, Scenario, Step, load_toml

MODEL_ID = re.compile(r"modele-[0-9a-f]{16}")
TASK_ID = re.compile(r"task-[1-9][0-9]{0,5}")
TITLE = "{titre}"


def model_path(root: Path, model_id: str) -> Path:
    if not MODEL_ID.fullmatch(model_id):
        raise ConfigError("Identifiant de modèle invalide.")
    path = root / "campaign-models" / (model_id + ".json")
    if path.resolve() != path:
        raise ConfigError("Le modèle doit se trouver dans le dossier du projet.")
    return path


def load_model(root: Path, model_id: str) -> dict:
    return json.loads(model_path(root, model_id).read_text(encoding="utf-8"))


def save_model(root: Path, model: dict) -> None:
    path = model_path(root, model["id"])
    path.parent.mkdir(exist_ok=True)
    tmp = None
    try:
        with NamedTemporaryFile(dir=path.parent, encoding="utf-8", mode="w", delete=False) as out:
            tmp = Path(out.name)
            json.dump(model, out, ensure_ascii=False, indent=2)
        tmp.replace(path)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def text(value, label: str, limit: int = 500) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(char) < 32 for char in value) or "{{" in value):
        raise ConfigError(f"Indiquez {label} sur une seule ligne ({limit} caractères maximum).")
    return value.strip()


def proof_options(root: Path) -> dict:
    options = {}
    for path in sorted((root / "scenarios").glob("*.toml")):
        raw = load_toml(path)
        editor = raw.get("editor")
        if not editor:
            continue
        scenario = Scenario.from_toml(raw, str(path))
        discovery = next((step for step in scenario.steps if step.mode == "discovery"), None)
        visible = next((check for check in discovery.expects if check.kind == "text_contains"), None) if discovery else None
        if not visible:
            raise ConfigError(f"{scenario.id} doit définir un résultat visible pour l’éditeur.")
        pattern = editor["title_pattern"]
        options[scenario.id] = {
            "id": scenario.id, "target": editor["target"], "name": editor["label"],
            "goal": discovery.intent.replace(pattern, TITLE),
            "expected": visible.selector.replace(pattern, TITLE),
            "requires_parent": editor.get("requires_parent"),
            "description": editor["description"], "pattern": pattern,
            "failure_text": editor.get("failure_text", ""),
        }
    return options


def compile_model(model: dict, personas: dict, targets: dict, scenarios: dict, proofs: dict):
    name = text(model.get("name"), "un nom de campagne", 120)
    target_id = model.get("target")
    if not isinstance(target_id, str) or target_id not in targets:
        raise ConfigError("Choisissez une cible disponible dans ce projet.")
    rows = model.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 24 or not all(isinstance(row, dict) for row in rows):
        raise ConfigError("Ajoutez entre 1 et 24 tâches.")
    assigned = {}
    workflow = model.get("version") == 2
    for row in rows:
        pid = row.get("persona")
        key = row.get("id") if workflow else pid
        if not isinstance(pid, str) or pid not in personas:
            raise ConfigError("Choisissez un persona disponible pour chaque tâche.")
        if not isinstance(key, str) or key in assigned or (workflow and not TASK_ID.fullmatch(key)):
            raise ConfigError("Chaque tâche doit avoir un identifiant distinct.")
        assigned[key] = row

    prerequisites = {}
    for key, row in assigned.items():
        extra = row.get("wait_for", []) if workflow else []
        if not isinstance(extra, list) or any(not isinstance(item, str) for item in extra):
            raise ConfigError("Sélectionnez des tâches valides comme prérequis.")
        dependencies = [row["after"], *extra] if row.get("after") else extra
        if any(not isinstance(item, str) or item not in assigned or item == key for item in dependencies):
            raise ConfigError("Chaque prérequis doit désigner une autre tâche.")
        prerequisites[key] = list(dict.fromkeys(dependencies))

    ordered = []
    while len(ordered) < len(rows):
        ready = [pid for pid, row in assigned.items() if pid not in ordered
                 and all(parent in ordered for parent in prerequisites[pid])]
        if not ready:
            raise ConfigError("Chaque prérequis doit désigner une autre tâche, sans dépendance circulaire.")
        ordered.extend(ready)

    title = ""
    if model.get("title") or (not workflow and any(row.get("proof") for row in rows)):
        title = _title(model.get("title"))
    prepared = []
    tasks, depends_on, generated = {}, {}, {}
    titles = {}
    for key in ordered:
        row = assigned[key]
        pid = row["persona"]
        goal = text(row.get("goal"), f"l’objectif de {personas[pid].name}")
        expected = text(row.get("expected"), f"le texte attendu pour {personas[pid].name}")
        proof_id = row.get("proof") or ""
        after = row.get("after") or ""
        if not isinstance(proof_id, str) or not isinstance(after, str):
            raise ConfigError("Tâche ou prérequis invalide.")
        proof = proofs.get(proof_id)
        row_title = _title(row["title"]) if workflow and row.get("title") else ""
        if workflow and proof and not proof["requires_parent"]:
            data_title = row_title or title
            full_title = f"{data_title} {{{{run_suffix}}}}-{key[5:]}" if data_title else ""
        elif workflow:
            full_title = titles.get(after, "")
        else:
            full_title = title + " {{run_suffix}}" if title else ""
        titles[key] = full_title
        if not full_title and (proof_id or TITLE in goal or TITLE in expected):
            raise ConfigError(f"Indiquez un titre dans la tâche qui crée la donnée utilisée par {personas[pid].name}.")
        if after and not workflow:
            depends_on[pid] = after
        step = Step(goal.replace(TITLE, full_title), mode="discovery", explore_budget=25,
                    expects=[Expectation("text_contains", selector=expected.replace(TITLE, full_title), timeout_ms=3500)])
        steps = [step]
        if proof_id:
            if not proof or proof["target"] != target_id or proof_id not in scenarios or targets[target_id].type != "web":
                raise ConfigError("Cette preuve n’est pas configurée pour la cible choisie.")
            prerequisite = proof["requires_parent"]
            if prerequisite and (not after or assigned[after].get("proof") != prerequisite):
                raise ConfigError("Cette vérification doit attendre le persona qui crée la donnée.")
            if TITLE in proof["goal"] and TITLE not in goal:
                raise ConfigError(f"Insérez {TITLE} dans l’objectif de {personas[pid].name}.")
            if TITLE in proof["expected"] and TITLE not in expected:
                raise ConfigError(f"Insérez {TITLE} dans le texte attendu pour {personas[pid].name}.")
            steps = []
            for original in scenarios[proof_id].steps:
                checks = []
                for check in original.expects:
                    if check.kind == "text_contains":
                        checks.extend(replace(item, failure_text=proof["failure_text"]) for item in step.expects)
                    elif check.kind == "api_record":
                        api = check.api
                        match = {k: v.replace(proof["pattern"], full_title) if isinstance(v, str) else v
                                 for k, v in api["match"].items()}
                        checks.append(replace(check, api={**api, "match": match}))
                    else:
                        checks.append(check)
                intent = step.intent if original.mode == "discovery" else original.intent
                steps.append(replace(original, intent=intent, expects=checks))
        elif not targets[target_id].supports_free_tasks:
            raise ConfigError("Cette cible ne permet pas encore les objectifs libres.")
        sid = f"decouverte-{key}"
        generated[sid] = Scenario(sid, proof["name"] if proof_id else goal, steps=steps)
        tasks[key] = {"scenario": sid}
        entry = {"persona": pid, "goal": goal, "expected": expected, "proof": proof_id, "after": after}
        if workflow:
            wait_for = [parent for parent in prerequisites[key] if parent != after]
            tasks[key].update(persona=pid, after=after, wait_for=wait_for)
            entry.update(id=key, title=row_title, wait_for=wait_for)
            conditions = row.get("conditions", {})
            if not isinstance(conditions, dict) or any(
                parent not in prerequisites[key] or rule not in ("success", "failure", "always")
                for parent, rule in conditions.items()
            ):
                raise ConfigError("Chaque condition doit correspondre à un prérequis de cette tâche.")
            if conditions:
                tasks[key]["conditions"] = conditions
                entry["conditions"] = conditions
        prepared.append(entry)
    clean = {"id": model["id"], "name": name, "target": target_id, "title": title, "rows": prepared}
    if workflow:
        clean["version"] = 2
        by_id = {entry["id"]: entry for entry in prepared}
        clean["rows"] = [by_id[key] for key in assigned]
    participants = list(dict.fromkeys(assigned[key]["persona"] for key in ordered))
    campaign = Campaign(clean["id"], target_id, participants, list(generated), speed="fast",
                        score_pass=0, depends_on=depends_on)
    return clean, campaign, tasks, generated


def _title(value) -> str:
    title = text(value, "un titre de donnée de test", 240)
    if any(char in title for char in '«»"{}'):
        raise ConfigError("Le titre de test ne doit pas contenir de guillemets ou d’accolades.")
    return title
