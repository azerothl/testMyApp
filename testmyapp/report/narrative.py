"""Lecture en français des actions et des résultats consignés pendant le test."""

STEP_LABELS = {
    "passed": "Réussie", "failed": "Échouée", "warned": "Avec avertissement",
    "skipped": "Ignorée", "running": "Sans résultat enregistré",
}
SESSION_LABELS = {
    "pass": "Parcours réussi", "fail": "Parcours en échec", "abandoned": "Parcours abandonné",
    "error": "Parcours interrompu par une erreur", "running": "Parcours sans résultat final",
    "blocked": "Parcours bloqué par un prérequis",
    "skipped": "Branche non déclenchée",
}
METRIC_LABELS = {
    "desktop_startup_ms": "Temps de démarrage de l’application",
    "action_p95_ms": "Durée des actions automatisées",
    "lcp_ms": "Temps d’affichage du contenu principal", "ttfb_ms": "Temps de première réponse du serveur",
    "fcp_ms": "Temps du premier affichage", "inp_ms": "Délai de réaction aux interactions",
    "cls": "Déplacement du contenu pendant le chargement", "step.duration_ms": "Durée de l’étape",
}


def describe_action(data: dict) -> str:
    action = data.get("action")
    target = data.get("intent_target") or data.get("image_match", {}).get("template")
    control = f"« {target} »" if target else "le contrôle prévu"
    descriptions = {
        "navigate": "Ouverture de la page prévue", "click": f"Clic sur {control}",
        "tap": f"Appui sur {control}", "invoke": f"Activation de {control}",
        "type": f"Saisie du texte dans {control}", "set_value": f"Remplissage de {control}",
        "press": "Utilisation du clavier", "hotkey": f"Raccourci clavier pour « {target} »" if target else "Utilisation d’un raccourci clavier",
        "wait": "Attente de la réponse de l’application", "wait_for": f"Attente de l’affichage de {control}",
        "close": "Fermeture de la fenêtre", "scroll": "Défilement de la page",
        "select": f"Sélection d’une option dans {control}", "check": f"Modification de {control}",
    }
    text = descriptions.get(action, f"Interaction avec {control}")
    if action in {"click", "tap", "invoke"}:
        if data.get("interaction") == "keyboard":
            text = f"Activation au clavier de {control}"
        elif data.get("interaction") == "touch":
            text = f"Appui tactile sur {control}"
    method = " Le contrôle a été repéré par son image." if data.get("resolution") == "image" else ""
    reason = {
        "label": " Le libellé affiché correspondait à l’objectif.",
        "tab": " Le persona a exploré cet onglet pour chercher la suite.",
        "history": " Le persona a repéré une entrée dans l’historique des exécutions.",
        "history_nav": " L’historique est l’espace qui correspond à la recherche d’une trace de création.",
        "fill": " Le champ de conversation était disponible pour formuler la demande.",
        "send": " Le champ était rempli et la commande d’envoi était visible.",
        "assistant": " L’assistant était un point d’entrée visible pour créer la tâche.",
        "confirm_task": " La proposition reprenait le titre et la date demandés.",
        "shared_task": " Le persona a retrouvé la tâche par son titre.",
        "next_period": " Le persona a consulté la période suivante pour chercher la tâche.",
        "choose_status": " Le persona a trouvé la commande dans le détail de la tâche.",
        "select_in_progress": " Le persona a choisi l’état « En cours ».",
        "submit_status": " Le persona a demandé l’enregistrement du nouvel état.",
        "confirm_status": " La proposition portait sur la tâche et le changement attendus.",
        "keyboard_fallback": " La tabulation n’a pas permis d’atteindre le contrôle. Le persona a essayé avec le pointeur.",
    }.get(data.get("decision"), "")
    if after := data.get("retry_after"):
        reason += f" Le persona a essayé cette action après l’échec de « {after} »."
    if data.get("ok") is False:
        return f"{text}. Cette tentative a échoué.{reason}"
    return f"{text}.{method}{reason}"


def format_measure(metric: str, value: float) -> str:
    if metric.endswith("_ms"):
        if value < 100:
            return f"{value:.0f} ms"
        return f"{value / 1000:.2f} s".replace(".", ",")
    return f"{value:g}".replace(".", ",")


def describe_check(check: dict, verdict: str) -> str:
    error = check.get("action_error")
    if error:
        target = check.get("target")
        context = f" sur « {target} »" if target else ""
        return f"Une action{context} a échoué. Motif consigné par le pilote : {error}."
    detail = check.get("detail") or {}
    kind = check.get("expect")
    ok = check.get("ok")
    if ok is None:
        ok = verdict != "failed"
    if kind == "api_record":
        count = detail.get("count")
        if count is None:
            return f"Le test n’a pas pu vérifier les données de l’application. {detail.get('error') or 'Une donnée attendue manque.'}"
        expected = detail.get("expected_count")
        if count == 0:
            text = "Le test n’a trouvé aucun enregistrement correspondant aux critères."
        elif count == 1:
            text = "Le test a trouvé un enregistrement correspondant aux critères."
        else:
            text = f"Le test a trouvé {count} enregistrements correspondant aux critères."
        if count != expected:
            text += f" Il en attendait {expected}."
        fields = detail.get("fields") or {}
        if "title" in fields:
            text += f" Titre recherché « {fields['title']} »."
        if "date" in fields:
            text += f" Date vérifiée {fields['date']}."
        if "status" in fields:
            status = {"todo": "À faire", "in_progress": "En cours", "done": "Terminée"}.get(fields["status"], fields["status"])
            text += f" État recherché « {status} »."
        if "id" in fields:
            text += f" Identifiant vérifié {fields['id']}."
        if "target" in fields:
            if ok and fields.get("operation") == "create" and fields.get("result") == "success":
                text += f" L’historique confirme la création de la tâche {fields['target']}."
            else:
                text += f" La trace recherchée devait concerner la tâche {fields['target']}."
        if task_id := detail.get("proof", {}).get("task_id"):
            text += f" Identifiant de la tâche créée {task_id}."
        if detail.get("recovered") == "retry":
            text += " À la reprise, le résultat était déjà enregistré. Aucune modification supplémentaire n’a été envoyée."
        elif detail.get("recovered") == "existing":
            text += " La tâche était déjà dans l’état attendu. Le parcours a pu se poursuivre."
        return text
    if kind == "visible":
        label = str(detail.get("selector", "")).rstrip(".")
        if ok:
            method = " par reconnaissance d’image" if detail.get("resolution") == "image" else ""
            return f"Le test a confirmé{method} que « {label} » était visible."
        return f"Le test n’a pas trouvé l’élément attendu « {label} »."
    if kind == "text_contains":
        label = detail.get("selector", "")
        if ok:
            return f"Le persona a pu observer « {label} » dans l’interface."
        if simulation := detail.get("simulated_failure"):
            return f"TestMyApp a volontairement mis ce contrôle en échec. {simulation}"
        if refusal := detail.get("observed_failure"):
            return f"L’application a affiché « {refusal} ». Le résultat attendu « {label} » n’a pas été atteint."
        return f"Le persona n’a pas observé « {label} » dans l’interface avant la fin de son exploration."
    if kind == "url_matches":
        url = detail.get("url")
        if ok:
            return f"La page attendue s’est ouverte ({url})." if url else "La page attendue s’est ouverte."
        return f"La page attendue ne s’est pas ouverte. Adresse observée : {url or 'inconnue'}."
    if kind == "metric_below":
        metric = detail.get("metric", "")
        label = METRIC_LABELS.get(metric, "Mesure de performance")
        value, limit = detail.get("value"), detail.get("max")
        if value is None or limit is None:
            return f"Le test n’a pas pu obtenir la mesure « {label} »."
        observed, threshold = format_measure(metric, value), format_measure(metric, limit)
        if not ok:
            return f"{label} : {observed}. Le seuil fixé à {threshold} a été dépassé."
        if detail.get("warn"):
            return f"{label} : {observed}. La mesure se rapproche du seuil de {threshold}."
        return f"{label} : {observed}. Le seuil de {threshold} a été respecté."
    if kind == "element_count":
        count = detail.get("count", 0)
        return f"Le test a repéré {count} élément(s). Le nombre attendu a été {'atteint' if ok else 'manqué'}."
    return "Le contrôle prévu a réussi." if ok else "Le contrôle prévu a échoué."


def step_result(verdict: str, checks: list[dict]) -> list[str]:
    if checks:
        return [describe_check(check, verdict) for check in checks]
    return [{
        "passed": "Les actions prévues se sont terminées sans erreur signalée.",
        "failed": "L’étape a échoué. Le journal ne précise pas le résultat attendu qui a manqué.",
        "warned": "L’étape s’est terminée avec un avertissement.",
        "skipped": "Cette étape a été ignorée.",
    }.get(verdict, "Le test a commencé cette étape. Aucun résultat final n’a été enregistré.")]


def previous_attempts(events: list[dict]) -> list[dict]:
    """Conserve les résultats remplacés lors d’une reprise du workflow."""
    records = {}
    history = []
    for event in events:
        kind = event["type"]
        if kind == "campaign.started":
            for sid in event.get("retry_sessions", []):
                prior = records.pop(sid, None)
                if prior and prior["outcome"]:
                    history.append(prior)
            continue
        sid = event.get("session_id")
        if kind in {"session.started", "session.blocked", "session.skipped"}:
            records[sid] = {"persona": event.get("persona_name") or event.get("persona") or sid,
                            "scenario": event.get("scenario_name") or event.get("scenario") or sid,
                            "attempt": event.get("attempt"), "outcome": "",
                            "details": [event["reason"]] if event.get("reason") else []}
        record = records.get(sid)
        if not record:
            continue
        if kind == "step.failed":
            record["details"].extend(step_result("failed", event.get("evidence", [])))
        if kind == "session.error" and event.get("error"):
            record["details"].append(event["error"])
        if kind == "session.completed":
            record["outcome"] = event["outcome"]
    return history


def campaign_summary(payload: dict) -> str:
    sessions = payload["sessions"]
    total = len(sessions)
    passed = sum(session["outcome"] == "pass" for session in sessions)
    verdict = {
        "pass": "Le test a réussi", "fail": "Le test a échoué",
        "error": "Une erreur a empêché le test d’aboutir normalement",
        "interrupted": "Le test a été interrompu",
    }[payload["status"]]
    if total == 1:
        session = sessions[0]
        ok, failed, warnings = (session[key] for key in ("steps_passed", "steps_failed", "steps_warned"))
        steps = ok + failed + warnings
        if steps:
            detail = (f" {ok} étape sur {steps} a réussi." if ok == 1 else
                      f" {ok} étapes sur {steps} ont réussi.")
        else:
            detail = " Aucune étape n’a été évaluée."
    elif total:
        detail = f" {passed} parcours sur {total} ont réussi."
    else:
        detail = " Aucun parcours n’a été exécuté."
    return f"{verdict} sur « {payload['campaign']['target']} »." + detail


def build_journeys(payload: dict) -> list[dict]:
    journeys = []
    for session in payload["sessions"]:
        steps: dict[int, dict] = {}
        errors = []
        persona = session["persona"]
        scenario = session["scenario"]
        prerequisites = ""
        attempt = 1
        for ref in session["evidence_refs"]:
            event = payload["evidence"][ref]
            kind, index, data = event["type"], event["step_id"], event["data"]
            if kind == "session.error" and data.get("error"):
                errors.append(data["error"])
            if kind in {"session.blocked", "session.skipped"} and data.get("reason"):
                errors.append(data["reason"])
                persona = data.get("persona_name") or persona
                scenario = data.get("scenario_name") or scenario
            if kind == "session.started":
                persona = data.get("persona_name") or persona
                scenario = data.get("scenario_name") or scenario
                attempt = data.get("attempt", 1)
            if kind in {"session.started", "session.blocked", "session.skipped"} and data.get("task"):
                scenario = f"Tâche {data['task']} · {scenario}"
                prerequisites = ", ".join(f"tâche {number}" for number in data.get("after_tasks", []))
            if index is None:
                continue
            step = steps.setdefault(index, {"number": index + 1, "intent": "", "actions": [],
                                           "captures": [], "verdict": "running", "checks": []})
            if kind == "step.started":
                step["intent"] = data.get("intent", "")
            elif kind == "action.performed":
                step["actions"].append(describe_action(data))
            elif kind == "step.screenshot":
                label = "Capture avant l’étape" if data.get("phase") == "before" else "Capture après l’étape"
                step["captures"].append({"id": ref, "label": label})
            elif kind.removeprefix("step.") in ("passed", "failed", "warned", "skipped"):
                step["verdict"] = kind.removeprefix("step.")
                step["intent"] = data.get("intent") or step["intent"]
                step["checks"] = data.get("evidence", [])
                step["reason"] = data.get("reason")
        for step in steps.values():
            step["label"] = STEP_LABELS[step["verdict"]]
            step["results"] = [step["reason"]] if step.get("reason") else step_result(step["verdict"], step["checks"])
        journeys.append({"persona": persona, "scenario": scenario,
                         "prerequisites": prerequisites, "attempt": attempt,
                         "label": SESSION_LABELS.get(session["outcome"], "Résultat indisponible"),
                         "steps": [steps[index] for index in sorted(steps)], "errors": errors})
    return journeys
