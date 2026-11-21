"""Génération du rapport de campagne (spec §9).

Trois formats : report.md (versionnable), report.html (autonome, CSS et
graphiques inline, images base64), report.json (schéma versionné intégral).
Constats : chaque point positif/négatif porte ses preuves. Recommandations
générées par règles motifs→conseil, priorisées impact×effort (§9.2).
"""
from __future__ import annotations

import base64
import json
import math
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from testmyapp import __version__
from testmyapp.config import Campaign, Persona, Target
from testmyapp.core.scoring import AxisScore
from testmyapp.report.narrative import METRIC_LABELS, SESSION_LABELS, build_journeys, campaign_summary, format_measure

SEV_ORDER = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3, "low": 4}
EFFORT_BY_RULE = {"optimize_lcp": "M", "fix_a11y_names": "S", "fix_touch_targets": "S",
                  "simplify_step": "M", "reduce_console_errors": "S", "speed_up_checkout": "L",
                  "reduce_abandonment": "L", "fix_contrast": "S",
                  "fix_execution": "M", "fix_crash": "L", "fix_freeze": "L"}


# ------------------------------------------------------------------ constats
def _fmt_duration(ms: float) -> str:
    if ms >= 1000:
        return f"{ms / 1000:.1f} s"
    return f"{ms:.0f} ms"


def build_findings(states: dict[str, Any], personas: dict[str, Persona],
                   target: Target, campaign: Campaign) -> tuple[list[dict], list[dict]]:
    """Retourne (positifs, négatifs). Chaque constat : titre, preuves, sévérité, portée."""
    positives: list[dict] = []
    negatives: list[dict] = []
    sessions = list(states.values())

    total = len(sessions)
    passed = sum(1 for s in sessions if s.outcome == "pass")
    failed = [s for s in sessions if s.outcome == "fail"]
    abandoned = [s for s in sessions if s.outcome == "abandoned"]

    if total and passed / total >= 0.5:
        who = ", ".join(sorted(s.persona_id for s in sessions if s.outcome == "pass"))
        positives.append({
            "title": f"{passed}/{total} sessions terminées avec succès",
            "evidence": [f"scénarios réussis par : {who}"],
            "severity": "info",
            "personas": sorted({s.persona_id for s in sessions if s.outcome == "pass"}),
            "ids": ["FONCT-PASS"],
        })

    # les novices ont réussi → signal de simplicité (§9.1 exemple)
    savvy_ok = [s for s in sessions if s.outcome == "pass"
                and personas[s.persona_id].tech_savviness < 0.4]
    if savvy_ok:
        positives.append({
            "title": "Parcours réussi par des personas novices (tech_savviness < 0,4)",
            "evidence": [f"{s.scenario_id} réussi par {s.persona_id} en {_fmt_duration(s.duration_ms)}"
                         for s in savvy_ok],
            "severity": "info",
            "personas": sorted({s.persona_id for s in savvy_ok}),
            "ids": ["FONCT-NOVICE"],
        })

    if failed:
        for s in failed[:6]:
            negatives.append({
                "title": f"Échec du scénario « {s.scenario_id} » pour {s.persona_id}",
                "evidence": [f"{s.steps_failed} étape(s) échouée(s), humeur finale {s.mood_final:+.1f}"]
                + ([p for p in s.screenshots or []]),
                "severity": "serious",
                "personas": [s.persona_id],
                "ids": [f"STEP-{s.session_id}"],
            })
    if abandoned:
        names = ", ".join(s.persona_id for s in abandoned)
        negatives.append({
            "title": f"{len(abandoned)} abandon(s) en cours de scénario ({names})",
            "evidence": ["abandon = signal UX fort (spec §4.5) : patience ou persistence épuisée"]
            + [f"{s.persona_id} a abandonné à « {s.scenario_id} »" for s in abandoned],
            "severity": "critical" if len(abandoned) > 1 else "serious",
            "personas": sorted({s.persona_id for s in abandoned}),
            "ids": [f"ABANDON-{s.session_id}" for s in abandoned],
        })

    # a11y : groupées par règle (§9.7)
    rule_map: dict[str, dict] = {}
    manual_checks: dict[str, set[str]] = {}
    for s in sessions:
        for v in s.a11y_violations:
            entry = rule_map.setdefault(v.get("rule", "?"),
                                        {"count": 0, "severity": v.get("severity", "moderate"),
                                         "selectors": [], "personas": set()})
            entry["count"] += 1
            sel = str(v.get("selector", ""))
            if sel and len(entry["selectors"]) < 5:
                entry["selectors"].append(sel)
            entry["personas"].add(s.persona_id)
        for item in s.a11y_review:
            manual_checks.setdefault(item["rule"], set()).add(s.persona_id)
    for rule, entry in rule_map.items():
        negatives.append({
            "title": f"Accessibilité : règle « {rule} » violée ({entry['count']} occurrence(s))",
            "evidence": [f"sélecteurs concernés : {', '.join(entry['selectors'])}"] if entry["selectors"] else [],
            "severity": entry["severity"],
            "personas": sorted(entry["personas"]),
            "ids": [f"A11Y-{rule}"],
        })
    for rule, affected in sorted(manual_checks.items()):
        negatives.append({"title": f"Accessibilité : contrôle manuel « {rule} »",
                          "evidence": [], "severity": "info", "personas": sorted(affected),
                          "ids": [f"REVIEW-{rule}"]})

    # vitals aux seuils (§9.6)
    metric_agg: dict[str, list[float]] = {}
    for s in sessions:
        for key, value in s.vitals.items():
            metric = key.split("::")[0]
            metric_agg.setdefault(metric, []).append(float(value))
    def _fmt_val(v: float) -> str:
        if v >= 100 or v == int(v):
            return f"{v:.0f}"
        return f"{v:.2f}"

    for metric, values in metric_agg.items():
        if metric not in campaign.thresholds:
            continue
        p95 = sorted(values)[math.ceil(0.95 * len(values)) - 1] if values else 0.0
        threshold = float(campaign.thresholds.get(metric, math.inf))
        if p95 > threshold:
            negatives.append({
                "title": f"{metric} p95 = {_fmt_val(p95)} dépasse le seuil ({_fmt_val(threshold)})",
                "evidence": [f"observé sur {len(values)} page(s) — cible {target.id}"],
                "severity": "serious" if metric in {"lcp_ms", "inp_ms"} else "moderate",
                "personas": sorted({s.persona_id for s in sessions if any(
                    k.split("::")[0] == metric for k in s.vitals)}),
                "ids": [f"PERF-{metric}"],
            })
        elif p95 and metric in {"lcp_ms", "fcp_ms"}:
            positives.append({
                "title": f"{metric} conforme : p95 = {_fmt_val(p95)} ≤ seuil {_fmt_val(threshold)}",
                "evidence": [f"{len(values)} mesure(s)"],
                "severity": "info",
                "personas": [],
                "ids": [f"PERF-OK-{metric}"],
            })

    # console (§9.8)
    errors_total = sum(len(s.console_errors) for s in sessions)
    if errors_total:
        negatives.append({
            "title": f"{errors_total} erreur(s) console/JS pendant les tests",
            "evidence": [str(e)[:160] for s in sessions for e in s.console_errors[:2]],
            "severity": "moderate",
            "personas": sorted({s.persona_id for s in sessions if s.console_errors}),
            "ids": ["STAB-CONSOLE"],
        })
    for s in sessions:
        if s.outcome == "error":
            negatives.append({
                "title": f"Erreur d’exécution du scénario « {s.scenario_id} » pour {s.persona_id}",
                "evidence": [f"session {s.session_id}"],
                "severity": "critical",
                "personas": [s.persona_id],
                "ids": [f"RUN-{s.session_id}"],
            })
        if s.crashed:
            negatives.append({
                "title": f"Crash / instabilité détecté pour {s.persona_id}",
                "evidence": [f"session {s.session_id}"],
                "severity": "critical",
                "personas": [s.persona_id],
                "ids": [f"CRASH-{s.session_id}"],
            })
        if s.freezes_ms:
            negatives.append({
                "title": f"{len(s.freezes_ms)} gel(s) de la fenêtre pour {s.persona_id}",
                "evidence": [f"Durée cumulée {_fmt_duration(sum(s.freezes_ms))}"],
                "severity": "serious", "personas": [s.persona_id],
                "ids": [f"FREEZE-{s.session_id}"],
            })

    # humeur globale positive
    moods = [s.mood_final for s in sessions if s.outcome == "pass"]
    if moods and sum(moods) / len(moods) > 1.0:
        positives.append({
            "title": f"Satisfaction globale positive (humeur moyenne {sum(moods) / len(moods):+.1f}/5)",
            "evidence": [f"{len(moods)} session(s) réussie(s)"],
            "severity": "info",
            "personas": [],
            "ids": ["UX-MOOD"],
        })
    positives.sort(key=lambda f: 0)
    negatives.sort(key=lambda f: SEV_ORDER.get(f["severity"], 9))
    return positives, negatives


# ------------------------------------------------------------ recommandations
def build_recommendations(negatives: list[dict], campaign: Campaign) -> list[dict]:
    """Règles motifs → recommandation, priorisation impact×effort (§9.2)."""
    out: list[dict] = []
    touch_size = campaign.thresholds.get("touch_target_px", 44)

    rules = [
        ("PERF", {"lcp_ms"}, "Optimiser le chargement des pages : précharger l'image principale, "
                            "différer les scripts non critiques, compresser les ressources.", "optimize_lcp"),
        ("A11Y", {"image-alt", "label", "button-name", "link-name", "form-label", "uia-control-name"},
                       "Corriger les noms accessibles (alt, labels de formulaires, aria-label) sur "
                       "les composants signalés ; vérifier au lecteur d'écran.", "fix_a11y_names"),
        ("A11Y", {"target-size"}, f"Viser une zone tactile de {touch_size:g}×{touch_size:g} px "
                                  "sur les contrôles signalés.", "fix_touch_targets"),
        ("A11Y", {"color-contrast"}, "Ajuster les couleurs et vérifier les états de focus, "
                                     "de survol et de validation.", "fix_contrast"),
        ("STEP", None, "Simplifier les étapes échouées : vocabulaire, hiérarchie visuelle et "
                       "position des actions principales ; tester à nouveau avec les novices.", "simplify_step"),
        ("STAB", None, "Corriger les erreurs console révélées par les parcours ; ajouter un "
                       "suivi d'erreurs côté application.", "reduce_console_errors"),
        ("ABANDON", None, "Réduire l'abandon : raccourcir le parcours critique, ajouter des repères "
                          "de progression et sauvegarder l'état entre les étapes.", "reduce_abandonment"),
        ("RUN", None, "Vérifier les prérequis de la cible et le message d’exécution avant de relancer.",
         "fix_execution"),
        ("CRASH", None, "Reproduire la sortie du processus à partir de la dernière étape et examiner "
                        "son code de sortie.", "fix_crash"),
        ("FREEZE", None, "Examiner les tâches déclenchées à la dernière étape et déplacer le travail "
                         "long hors du thread de l’interface.", "fix_freeze"),
    ]
    for prefix, rule_filter, advice, rule_id in rules:
        found = [f for f in negatives if any(fid.startswith(prefix + "-") and
                 (rule_filter is None or fid.partition("-")[2] in rule_filter) for fid in f["ids"])]
        if not found:
            continue
        personas_touched = sorted({p for f in found for p in f.get("personas", [])})
        impact = len(found) * (2 if any(f["severity"] in {"critical", "serious"} for f in found) else 1)
        effort = EFFORT_BY_RULE.get(rule_id, "M")
        priority = "P0" if impact >= 4 else ("P1" if impact >= 2 else "P2")
        out.append({
            "id": f"REC-{rule_id}",
            "advice": advice,
            "priority": priority,
            "effort": effort,
            "impact": impact,
            "based_on": sorted({fid for f in found for fid in f.get("ids", [])})[:10],
            "personas": personas_touched,
        })
    out.sort(key=lambda r: (r["priority"], -r["impact"]))
    return out


def _attach_evidence(states: dict, findings: list[dict], target: Target) -> dict[str, dict]:
    """Indexe les preuves par session et par événement, indépendamment de leur ordre global."""
    index: dict[str, dict] = {}
    by_session: dict[str, list[dict]] = {}
    for state in states.values():
        events = state.evidence or [{"type": "session.completed", "outcome": state.outcome,
                                    "session_id": state.session_id}]
        by_session[state.session_id] = []
        for i, event in enumerate(events, 1):
            item = {
                "id": f"{state.session_id}-ev-{i}",
                "session_id": state.session_id,
                "step_id": event.get("step_id"),
                "type": event["type"],
                "journal_seq": event.get("seq"),
                "data": {key: value for key, value in event.items()
                         if key not in {"session_id", "step_id", "type", "seq", "ts"}},
            }
            by_session[state.session_id].append(item)
            index[item["id"]] = item

    for finding in findings:
        matched: dict[str, dict] = {}
        for fid in finding["ids"]:
            prefix, _, detail = fid.partition("-")
            if prefix in {"STEP", "ABANDON", "RUN", "CRASH", "FREEZE"}:
                sessions = (states[detail],) if detail in states else ()
            else:
                sessions = states.values()
            for state in sessions:
                records = by_session[state.session_id]
                if prefix in {"STEP", "ABANDON"}:
                    steps = {e["step_id"] for e in records if e["type"] == "step.failed"}
                    selected = [e for e in records if e["step_id"] in steps
                                and e["type"] in {"step.failed", "step.started", "step.screenshot"}]
                elif prefix in {"RUN", "CRASH", "FREEZE", "STAB"}:
                    kind = {"RUN": "session.error", "CRASH": "target.crash", "FREEZE": "target.freeze",
                            "STAB": "target.console_error"}[prefix]
                    selected = [e for e in records if e["type"] == kind]
                elif prefix in {"A11Y", "REVIEW"}:
                    kind = "a11y.violation" if prefix == "A11Y" else "a11y.review"
                    selected = [e for e in records if e["type"] == kind
                                and e["data"].get("rule") == detail]
                elif prefix == "PERF":
                    metric = detail.removeprefix("OK-")
                    matching = [e for e in records if e["type"] == "target.metric"
                                and e["data"]["metric"].partition("::")[0] == metric]
                    latest = {e["data"]["metric"]: e for e in matching}
                    selected = list(latest.values())
                    if not selected and not any(k.partition("::")[0] == metric for k in state.vitals):
                        continue
                else:
                    if state.outcome != "pass" or (finding["personas"] and state.persona_id not in finding["personas"]):
                        continue
                    selected = [e for e in records if e["type"] == "session.completed"]
                if not selected and prefix in {"STEP", "ABANDON", "RUN", "CRASH", "FREEZE"}:
                    selected = [e for e in records if e["type"] == "session.completed"]
                for item in selected:
                    matched[item["id"]] = item
        finding["evidence_refs"] = list(matched)
        finding["confidence"] = ("human_required" if not matched or finding["ids"][0].startswith("REVIEW-") else
                                 "reported" if target.type == "sim" or any(fid.startswith(("A11Y-", "UX-"))
                                                                            for fid in finding["ids"])
                                 else "verified")
    return index


def _embedded_capture(run_dir: Path, path: str) -> str | None:
    candidate = (run_dir / path).resolve()
    if not candidate.is_relative_to(run_dir.resolve() / "screenshots") or not candidate.is_file():
        return None
    mime = {".png": "image/png", ".svg": "image/svg+xml"}.get(candidate.suffix.lower())
    if mime is None:
        return None
    encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# ----------------------------------------------------------------- rapports
def generate_report(run_dir: Path, campaign: Campaign, target: Target,
                    personas: dict[str, Persona], states: dict[str, Any],
                    axes: list[AxisScore], score: float | None, *, status: str = "pass",
                    campaign_name: str = "", workflow: bool = False, app_version: str = "",
                    history: list[dict] | None = None) -> str:
    run_dir = Path(run_dir)
    report_dir = run_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if workflow:
        states = dict(sorted(states.items(), key=lambda item: min(event["seq"] for event in item[1].evidence)))
    else:
        states = dict(sorted(states.items()))
    positives, negatives = build_findings(states, personas, target, campaign)
    recommendations = build_recommendations(negatives, campaign)
    evidence = _attach_evidence(states, positives + negatives, target)
    decisions = [item["data"] for item in evidence.values() if item["type"] == "action.ai_decision"]
    payload = {
        "schema_version": 2,
        "generated_at": now,
        "engine_version": __version__,
        "campaign": {"id": campaign.id, **({"name": campaign_name} if campaign_name else {}),
                     **({"app_version": app_version} if app_version else {}),
                     "seed": campaign.seed, "speed": campaign.speed,
                     "target": target.id, "target_type": target.type, "thresholds": campaign.thresholds,
                     "weights": campaign.weights},
        **({"previous_attempts": history} if history else {}),
        "score": score,
        "status": status,
        "axes": [{"name": a.name, "score": a.score, "detail": a.detail} for a in axes],
        "positives": positives,
        "negatives": negatives,
        "recommendations": recommendations,
        "evidence": evidence,
        "agent": {
            "enabled": campaign.ai_mode,
            "choices": sum(item["source"] == "agent" for item in decisions),
            "fallbacks": sum(item["source"] == "weighted" for item in decisions),
            "latency_ms": sum(item.get("latency_ms", 0) for item in decisions),
            "input_tokens": sum(item.get("input_tokens", 0) for item in decisions),
            "output_tokens": sum(item.get("output_tokens", 0) for item in decisions),
            "estimated_cost": sum(item.get("estimated_cost", 0) for item in decisions),
        },
        "sessions": [
            {"session_id": s.session_id, "persona": s.persona_id, "scenario": s.scenario_id,
             "outcome": s.outcome, "steps_passed": s.steps_passed, "steps_failed": s.steps_failed,
             "steps_warned": s.steps_warned, "duration_ms": round(s.duration_ms),
             "mood_final": s.mood_final, "friction_extra_actions": s.friction_extra_actions,
             "console_errors": len(s.console_errors), "crashed": s.crashed, "freezes_ms": s.freezes_ms,
             "a11y_violations": s.a11y_violations, "a11y_review": s.a11y_review, "a11y_pages": s.a11y_pages,
             "vitals": s.vitals, "blocking_failure": s.blocking_failure,
             "evidence_refs": [key for key, value in evidence.items() if value["session_id"] == s.session_id]}
            for s in states.values()
        ],
    }
    from jsonschema import Draft202012Validator

    schema = json.loads((files("testmyapp") / "assets" / "schemas" / "report-v2.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)
    outputs = {
        "report.json": json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        "report.md": _render_markdown(payload),
        "report.html": _render_html(payload, report_dir),
    }
    for name, contents in outputs.items():
        path = report_dir / name
        temporary = path.with_suffix(".tmp" + path.suffix)
        temporary.write_text(contents, encoding="utf-8")
        temporary.replace(path)
    return str(report_dir / "report.html")


STATUS_LABELS = {"pass": "Réussie", "fail": "Échec", "error": "Erreur d’exécution",
                 "interrupted": "Interrompue"}
CONFIDENCE_LABELS = {"verified": "Vérifié", "reported": "Signalé", "human_required": "Humain requis"}


def _finding_titles(payload: dict) -> dict[str, str]:
    """Présente les seuils de performance dans les unités du lecteur."""
    values_by_metric: dict[str, list[float]] = {}
    for session in payload["sessions"]:
        for key, value in session["vitals"].items():
            values_by_metric.setdefault(key.partition("::")[0], []).append(value)
    titles = {}
    for finding in payload["positives"] + payload["negatives"]:
        identifier = finding["ids"][0]
        title = finding["title"]
        if identifier.startswith("PERF-"):
            metric = identifier.removeprefix("PERF-").removeprefix("OK-")
            values = values_by_metric.get(metric, [])
            threshold = payload["campaign"]["thresholds"].get(metric)
            if values and threshold is not None:
                value = sorted(values)[math.ceil(.95 * len(values)) - 1]
                verb = "respecte" if value <= threshold else "dépasse"
                title = (f"{METRIC_LABELS.get(metric, metric)} : {format_measure(metric, value)}. "
                         f"Cette mesure {verb} le seuil de {format_measure(metric, threshold)}.")
        titles[identifier] = title
    return titles


def _score_badge(score: float | None) -> str:
    if score is None:
        return "Non mesuré"
    if score >= 70:
        return f"✅ **{score:.0f}/100**"
    if score >= 50:
        return f"⚠️ **{score:.0f}/100**"
    return f"❌ **{score:.0f}/100**"


def _render_markdown(payload: dict) -> str:
    lines: list[str] = []
    ap = lines.append
    titles = _finding_titles(payload)
    ap(f"# Rapport de campagne — {payload['campaign'].get('name') or payload['campaign']['id']}")
    ap("")
    ap(f"*Généré le {payload['generated_at']} par TestMyApp v{payload['engine_version']} · "
       f"graine {payload['campaign']['seed']} · vitesse {payload['campaign']['speed']} · "
       f"cible {payload['campaign']['target']}*")
    ap("")
    ap("## Résultat en bref")
    ap("")
    ap(campaign_summary(payload))
    ap("")
    ap("## Déroulement du test")
    ap("")
    if payload.get("previous_attempts"):
        ap("### Résultats avant la reprise")
        ap("")
        for prior in payload["previous_attempts"]:
            attempt = f" · tentative {prior['attempt']}" if prior["attempt"] else ""
            ap(f"- {prior['persona']} · {prior['scenario']}{attempt} · {SESSION_LABELS.get(prior['outcome'], prior['outcome'])}.")
            for detail in prior["details"]:
                ap(f"  - {detail}")
        ap("")
    for journey in build_journeys(payload):
        ap(f"### {journey['persona']} · {journey['scenario']}")
        ap("")
        ap(f"{journey['label']}.")
        if journey["attempt"] > 1:
            ap(f"Parcours repris à la tentative {journey['attempt']}.")
        if journey["prerequisites"]:
            ap(f"Prérequis : {journey['prerequisites']}.")
        ap("")
        for error in journey["errors"]:
            ap(error)
        for step in journey["steps"]:
            ap(f"#### Étape {step['number']} · {step['intent'] or 'Action du scénario'}")
            ap("")
            for action in step["actions"]:
                ap(f"- {action}")
            if step["actions"]:
                ap("")
            ap(f"**{step['label']}.** " + " ".join(step["results"]))
            for capture in step["captures"]:
                path = payload["evidence"][capture["id"]]["data"].get("path")
                if path and not Path(path).is_absolute():
                    ap(f"[{capture['label']}](../{path})")
            ap("")
    ap("## Indicateurs")
    ap("")
    ap(f"- Score global : {_score_badge(payload['score'])}")
    ap(f"- Verdict : {STATUS_LABELS[payload['status']]}")
    if payload["campaign"]["speed"] == "turbo":
        ap("- Mode turbo. Les délais UX sont indicatifs.")
    for a in payload["axes"]:
        value = f"{a['score']:.0f}/100" if a["score"] is not None else "non mesuré"
        ap(f"- Axe **{a['name']}** : {value}")
    agent = payload["agent"]
    if agent["choices"] or agent["fallbacks"]:
        ap(f"- Exploration assistée : {agent['choices']} choix IA, {agent['fallbacks']} replis pondérés, "
           f"{agent['latency_ms']} ms cumulées.")
    top_neg = [titles[n["ids"][0]] for n in payload["negatives"][:3]]
    top_rec = [f"{r['priority']} : {r['advice'][:80]}…" for r in payload["recommendations"][:5]]
    if top_neg:
        ap(f"- Constats majeurs : {' ; '.join(top_neg)}")
    if top_rec:
        ap("- Top recommandations :")
    for rec in top_rec:
        ap(f"  - {rec}")
    ap("")
    ap("## Points positifs")
    ap("")
    if not payload["positives"]:
        ap("*(aucun)*")
    for f in payload["positives"]:
        ap(f"- ✅ **{titles[f['ids'][0]]}**")
        for ev in f.get("evidence", []):
            ap(f"  - preuve : {ev}")
    ap("")
    ap("## Points à améliorer")
    ap("")
    if not payload["negatives"]:
        ap("*(aucun)*")
    for f in payload["negatives"]:
        sev = f["severity"]
        ap(f"- {'🔴' if sev in {'critical', 'serious'} else '🟠'} **[{sev}] {titles[f['ids'][0]]}**")
        for ev in f.get("evidence", []):
            ap(f"  - preuve : {ev}")
    ap("")
    ap("## Bilan par profil de test")
    ap("")
    ap("| Persona | Scénario | Résultat | Étapes ✓/✗ | Durée | Humeur | Friction |")
    ap("|---|---|---|---|---|---|---|")
    for s in payload["sessions"]:
        ap(f"| {s['persona']} | {s['scenario']} | {SESSION_LABELS.get(s['outcome'], s['outcome'])} | "
           f"{s['steps_passed']}/{s['steps_failed']} | {_fmt_duration(s['duration_ms'])} | "
           f"{s['mood_final']:+.1f} | {s['friction_extra_actions']} |")
    ap("")
    ap("## Bilan par scénario")
    ap("")
    by_scenario: dict[str, list] = {}
    for s in payload["sessions"]:
        by_scenario.setdefault(s["scenario"], []).append(s)
    for scid, sessions in sorted(by_scenario.items()):
        rate = sum(1 for s in sessions if s["outcome"] == "pass") / len(sessions) * 100
        durations = sorted(s["duration_ms"] for s in sessions)
        ap(f"### {scid} — réussite {rate:.0f} %, durée médiane {_fmt_duration(durations[len(durations) // 2])}")
    ap("")
    ap("## Mesures de performance")
    ap("")
    vitals_rows = []
    for s in payload["sessions"]:
        for key, value in s["vitals"].items():
            metric, _, url = key.partition("::")
            vitals_rows.append((metric, url, value, s["persona"]))
    if vitals_rows:
        ap("| Métrique | Page | Valeur | Persona |")
        ap("|---|---|---|---|")
        for metric, url, value, pid in sorted(vitals_rows)[:30]:
            ap(f"| {METRIC_LABELS.get(metric, metric)} | {url} | {format_measure(metric, value)} | {pid} |")
    else:
        ap("*(aucune métrique vitale collectée)*")
    ap("")
    ap("## Accessibilité")
    ap("")
    a11y = [f for f in payload["negatives"] if any(fid.startswith("A11Y") for fid in f.get("ids", []))]
    if not a11y:
        if any(s["a11y_pages"] for s in payload["sessions"]):
            ap("Aucun problème signalé par les contrôles automatiques exécutés.")
        else:
            ap("Aucun contrôle automatique d’accessibilité n’a été enregistré pour ce test.")
    for f in a11y:
        ap(f"- [{f['severity']}] {f['title']}")
    ap("")
    ap("## Stabilité")
    ap("")
    ap(f"- Erreurs console : {sum(s['console_errors'] for s in payload['sessions'])}")
    ap(f"- Fermetures inattendues : {sum(s['crashed'] for s in payload['sessions'])}")
    ap("")
    ap("## Recommandations")
    ap("")
    ap("| Priorité | Recommandation | Effort | Basée sur |")
    ap("|---|---|---|---|")
    for r in payload["recommendations"]:
        ap(f"| {r['priority']} | {r['advice']} | {r['effort']} | {', '.join(r['based_on'])} |")
    ap("")
    ap("## Annexes")
    ap("")
    ap(f"- Journal complet : `events.jsonl`, `metrics.jsonl` dans le dossier du run")
    ap(f"- Captures : `screenshots/` ; config figée : `campaign.json`")
    ap("- Distinction de preuve : automatique `● vérifié` ; un audit humain "
       "(assistive technology) reste requis pour le reste (§16).")
    ap("")
    return "\n".join(lines)


_CSS = """
:root { --bg:#12141c; --panel:#1a1e2a; --ink:#dde3f0; --dim:#8a93a8;
        --good:#5ad19a; --bad:#e86a6a; --warn:#e8b64a; --accent:#7a9ae8; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--ink); font:15px/1.6 system-ui, sans-serif;
       margin:0; padding:2rem; }
h1,h2,h3 { color:#fff; } h1{font-size:1.7rem;} h2{margin-top:2.2rem;border-bottom:1px solid #2a3040;padding-bottom:.3rem;}
main { max-width: 1080px; margin:0 auto; }
.badge { display:inline-block; padding:.2rem .6rem; border-radius:6px; font-weight:700; }
.badge.good { background:#1d3a2c; color:var(--good);} .badge.warn{background:#3a2f1d;color:var(--warn);}
.badge.bad{background:#3a1d1d;color:var(--bad);}
table{border-collapse:collapse;width:100%;margin:.8rem 0;}
th,td{text-align:left;padding:.45rem .6rem;border-bottom:1px solid #242a38;}
th{color:var(--dim);font-size:.85rem;text-transform:uppercase;letter-spacing:.04em;}
.finding{background:var(--panel);border-radius:8px;padding:.8rem 1rem;margin:.5rem 0;}
.finding.evidence{color:var(--dim);font-size:.88rem;}
.sev-critical,.sev-serious{border-left:4px solid var(--bad);}
.sev-moderate,.sev-minor{border-left:4px solid var(--warn);}
.sev-info{border-left:4px solid var(--good);}
.axis{margin:.6rem 0;} .axis .bar{height:10px;background:#242a38;border-radius:5px;overflow:hidden;}
.axis .bar>div{height:100%;background:var(--accent);}
.meta{color:var(--dim);font-size:.9rem;}
li{margin:.25rem 0;}
a { color:#a9c3ff; }
.journey { padding-left:1.6rem; }
.journey>li { margin-block:1rem; padding:.7rem 1rem; background:var(--panel); }
.journey h4 { margin:0; font-size:1rem; overflow-wrap:anywhere; }
.journey p { margin:.5rem 0; }
.journey ul { padding-left:1.2rem; }
.score-detail { margin:1rem 0; }
"""


def _render_html(payload: dict, report_dir: Path) -> str:
    from jinja2 import Template

    def sev_class(sev: str) -> str:
        return f"sev-{sev}"

    badge = {"pass": "good", "fail": "bad", "error": "bad", "interrupted": "warn"}[payload["status"]]
    tpl = Template("""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rapport TestMyApp — {{ p.campaign.name or p.campaign.id }}</title><style>{{ css|safe }}</style></head>
<body><main>
<h1>Rapport de campagne — {{ p.campaign.name or p.campaign.id }}</h1>
{% if p.campaign.app_version %}<p>Version déclarée · {{ p.campaign.app_version }}</p>{% endif %}
<p class="meta">Généré le {{ p.generated_at }} · TestMyApp v{{ p.engine_version }} ·
graine {{ p.campaign.seed }} · vitesse {{ p.campaign.speed }} · cible {{ p.campaign.target }}</p>

<h2>Résultat en bref</h2>
<p><strong>{{ summary }}</strong></p>
<p><span class="badge {{ badge }}">{{ status_label }}</span> ·
{% if p.score is not none %}Score global de {{ ('%.1f'|format(p.score))|replace('.', ',') }}/100
{% else %}Score global non mesuré{% endif %}</p>
{% if p.campaign.speed == 'turbo' %}<p class="meta">Mode turbo. Les délais UX sont indicatifs.</p>{% endif %}
<h2>Déroulement du test</h2>
{% if p.previous_attempts %}
<h3>Résultats avant la reprise</h3>
<ul>{% for prior in p.previous_attempts %}
<li><strong>{{ prior.persona }} · {{ prior.scenario }}</strong>{% if prior.attempt %} · tentative {{ prior.attempt }}{% endif %}
· {{ session_labels.get(prior.outcome, prior.outcome) }}.
{% if prior.details %}<ul>{% for detail in prior.details %}<li>{{ detail }}</li>{% endfor %}</ul>{% endif %}</li>
{% endfor %}</ul>
{% endif %}
{% for journey in journeys %}
<section>
<h3>{{ journey.persona }} · {{ journey.scenario }}</h3>
<p>{{ journey.label }}.</p>
{% if journey.attempt > 1 %}<p>Parcours repris à la tentative {{ journey.attempt }}.</p>{% endif %}
{% if journey.prerequisites %}<p>Prérequis · {{ journey.prerequisites }}</p>{% endif %}
{% for error in journey.errors %}<p>{{ error }}</p>{% endfor %}
{% if journey.steps %}
<ol class="journey">
{% for step in journey.steps %}
<li value="{{ step.number }}">
<h4>{{ step.intent or 'Action du scénario' }}</h4>
{% if step.actions %}<ul>{% for action in step.actions %}<li>{{ action }}</li>{% endfor %}</ul>{% endif %}
<p><strong>{{ step.label }}.</strong> {{ step.results|join(' ') }}</p>
{% for capture in step.captures if capture.id in captures %}
<p><a href="#{{ capture.id }}">{{ capture.label }}</a></p>
{% endfor %}
</li>
{% endfor %}
</ol>
{% elif not journey.errors %}<p>Aucune étape enregistrée pour ce parcours.</p>{% endif %}
</section>
{% else %}<p>Aucun parcours enregistré.</p>{% endfor %}
<details class="score-detail">
<summary>Détail des scores</summary>
{% for a in p.axes %}<div class="axis"><strong>{{ a.name }}</strong> —
{% if a.score is not none %}{{ '%.0f'|format(a.score) }}/100
<div class="bar"><div style="width:{{ a.score }}%"></div></div>{% else %}non mesuré{% endif %}</div>{% endfor %}
</details>

{% macro finding(f) -%}
{% for id in f.ids %}<span id="{{ id }}"></span>{% endfor %}
<strong>{{ finding_titles[f.ids[0]] }}</strong> · {{ confidence_labels[f.confidence] }}
{% if f.severity != 'info' %} · {{ f.severity }}{% endif %}
{% for description in f.evidence %}<div class="evidence">{{ description }}</div>{% endfor %}
{% if f.evidence_refs %}<ul>{% for id in f.evidence_refs %}
<li><a href="#{{ id }}">{{ evidence_label(p.evidence[id]) }}</a></li>{% endfor %}</ul>{% endif %}
{%- endmacro %}

<h2>Points positifs</h2>
{% for f in p.positives %}<div class="finding sev-info">{{ finding(f) }}</div>
{% else %}<p class="meta">(aucun)</p>{% endfor %}

<h2>Points à améliorer</h2>
{% for f in p.negatives %}<div class="finding {{ sev_class(f.severity) }}">
{{ finding(f) }}</div>
{% else %}<p class="meta">(aucun)</p>{% endfor %}

<h2>Bilan par profil de test</h2>
<table><tr><th>Persona</th><th>Scénario</th><th>Résultat</th><th>Étapes ✓/✗</th><th>Durée</th>
<th>Humeur</th><th>Friction</th></tr>
{% for s in p.sessions %}<tr><td>{{ s.persona }}</td><td>{{ s.scenario }}</td><td>{{ session_labels.get(s.outcome, s.outcome) }}</td>
<td>{{ s.steps_passed }}/{{ s.steps_failed }}</td><td>{{ '%.1f'|format(s.duration_ms/1000) }} s</td>
<td>{{ '%+.1f'|format(s.mood_final) }}</td><td>{{ s.friction_extra_actions }}</td></tr>{% endfor %}</table>

<h2>Mesures de performance</h2>
{% if vitals %}
<table><tr><th>Métrique</th><th>Page</th><th>Valeur</th><th>Persona</th></tr>
{% for m in vitals %}<tr><td>{{ metric_labels.get(m.metric, m.metric) }}</td><td>{{ m.url or '—' }}</td>
<td>{{ format_measure(m.metric, m.value) }}</td><td>{{ m.persona }}</td></tr>{% endfor %}</table>
{% else %}<p>Aucune mesure de performance n’a été relevée.</p>{% endif %}

<h2>Accessibilité</h2>
{% for f in p.negatives if f.ids[0].startswith('A11Y-') or f.ids[0].startswith('REVIEW-') %}<div class="finding {{ sev_class(f.severity) }}">
<a href="#{{ f.ids[0] }}">{{ f.title }}</a></div>
{% else %}
{% if p.sessions|selectattr('a11y_pages')|list %}
<p>Aucun problème signalé par les contrôles automatiques exécutés.</p>
{% else %}<p>Aucun contrôle automatique d’accessibilité n’a été enregistré pour ce test.</p>{% endif %}
{% endfor %}

<h2>Recommandations</h2>
{% if p.recommendations %}
<table><tr><th>Priorité</th><th>Recommandation</th><th>Effort</th><th>Basée sur</th></tr>
{% for r in p.recommendations %}<tr><td>{{ r.priority }}</td><td>{{ r.advice }}</td>
<td>{{ r.effort }}</td><td>{% for id in r.based_on %}<a href="#{{ id }}">{{ id }}</a><br>{% endfor %}</td></tr>
{% endfor %}</table>
{% else %}<p>Aucune recommandation automatique pour ce test.</p>{% endif %}

<h2>Preuves et annexes</h2>
<p class="meta">Le numéro du journal correspond au champ <code>seq</code> de <code>events.jsonl</code>.
Les contrôles manuels d’accessibilité restent requis.</p>
{% if p.agent.choices or p.agent.fallbacks %}
<p>Exploration assistée · {{ p.agent.choices }} choix IA, {{ p.agent.fallbacks }} replis pondérés.
{{ p.agent.latency_ms }} ms cumulées, {{ p.agent.input_tokens }} jetons entrants,
{{ p.agent.output_tokens }} sortants, coût estimé {{ '%.6f'|format(p.agent.estimated_cost) }} au tarif configuré.</p>
{% endif %}
{% for ev in p.evidence.values() %}
<article class="finding" id="{{ ev.id }}">
<h3>{{ evidence_label(ev) }}</h3>
{% if ev.journal_seq is not none %}<p class="meta">Événement {{ ev.journal_seq }}</p>{% endif %}
{% if ev.id in captures %}<img src="{{ captures[ev.id] }}" alt="{{ evidence_label(ev) }}"
loading="lazy" style="max-width:100%;height:auto">{% endif %}
<details><summary>Données de la preuve</summary><pre style="white-space:pre-wrap">{{ ev.data|tojson(indent=2) }}</pre></details>
</article>
{% endfor %}
</main></body></html>""", autoescape=True)

    vitals = []
    for s in payload["sessions"]:
        for key, value in s.get("vitals", {}).items():
            metric, _, url = key.partition("::")
            vitals.append({"metric": metric, "url": url, "value": float(value), "persona": s["persona"]})
    vitals.sort(key=lambda m: (m["metric"], m["url"]))
    captures = {}
    for item in payload["evidence"].values():
        if item["type"] == "step.screenshot" and item["data"].get("path"):
            image = _embedded_capture(report_dir.parent, item["data"]["path"])
            if image:
                captures[item["id"]] = image
    return tpl.render(p=payload, css=_CSS, badge=badge, vitals=vitals, sev_class=sev_class,
                      status_label=STATUS_LABELS[payload["status"]], captures=captures,
                      confidence_labels=CONFIDENCE_LABELS, evidence_label=_evidence_label,
                      summary=campaign_summary(payload), journeys=build_journeys(payload),
                      session_labels=SESSION_LABELS, metric_labels=METRIC_LABELS,
                      format_measure=format_measure, finding_titles=_finding_titles(payload))


def _evidence_label(item: dict) -> str:
    kind = item["type"]
    name = {"step.started": "Étape", "step.failed": "Échec", "step.passed": "Réussite",
            "action.performed": "Action",
            "step.warned": "Avertissement", "step.screenshot": "Capture", "target.metric": "Mesure",
            "target.console_error": "Journal console", "target.crash": "Crash", "target.freeze": "Gel de la fenêtre",
            "session.error": "Erreur d’exécution", "session.completed": "Résultat",
            "a11y.violation": "Signalement d’accessibilité", "a11y.review": "Contrôle manuel",
            "a11y.page_scanned": "Page analysée", "persona.emotion": "Émotion"}.get(kind, kind)
    step = f", étape {item['step_id'] + 1}" if item["step_id"] is not None else ""
    match = item["data"].get("image_match", {})
    detail = (item["data"].get("phase") if kind == "step.screenshot" else
              match.get("template") or item["data"].get("metric") or item["data"].get("rule"))
    return f"{name} · {item['session_id']}{step}" + (f" · {detail}" if detail else "")
