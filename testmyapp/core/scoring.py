"""Score composite 0–100 (spec §8.3) et agrégation des métriques.

Axes : fonctionnel 30 %, perf 20 %, a11y 15 %, UX 20 %, stabilité 15 %
(pondérations configurables par campagne). Chaque axe est normalisé
0–100. Le score est un résumé : le rapport reste la lecture principale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AxisScore:
    name: str
    score: float | None
    detail: dict[str, Any] = field(default_factory=dict)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def compute_axes(sessions: list[dict], thresholds: dict[str, float]) -> list[AxisScore]:
    """sessions = liste des résultats sérialisés (session.completed + observations)."""
    completed = [s for s in sessions if s.get("outcome") in {"pass", "fail", "abandoned"}]

    # A. fonctionnel : réussite des étapes, abandon pénalise fort
    total_steps = sum(s.get("steps_passed", 0) + s.get("steps_failed", 0) + s.get("steps_warned", 0) for s in sessions)
    passed = sum(s.get("steps_passed", 0) for s in sessions)
    abandoned = sum(1 for s in sessions if s.get("outcome") == "abandoned")
    errors = sum(s.get("outcome") == "error" for s in sessions)
    fonc = _clamp(passed / total_steps * 100 - abandoned * 10) if total_steps else None

    # B. perf : ratio aux seuils, moyenne sur les vitals observés (LCP, CLS, INP)
    ratios = []
    for s in sessions:
        for metric in ("lcp_ms", "cls", "inp_ms", "desktop_startup_ms", "action_p95_ms"):
            if metric not in thresholds:
                continue
            values = [float(v) for k, v in (s.get("vitals") or {}).items()
                      if k.split("::", 1)[0] == metric]
            if values:
                value = max(values)
                threshold = thresholds[metric]
                ratio = threshold / value if value > 0 else 1.0
                ratios.append(min(ratio, 1.0))
    perf = _clamp(sum(ratios) / len(ratios) * 100) if ratios else None

    # C. a11y : pénalité par violation pondérée sévérité
    sev_weight = {"critical": 10.0, "serious": 6.0, "moderate": 3.0, "minor": 1.0}
    findings: dict[tuple, str] = {}
    pages: set[tuple] = set()
    for s in sessions:
        violations = s.get("a11y_violations") or []
        target = s.get("target_id", "")
        pages.update((target, url) for url in s.get("a11y_pages", []))
        if violations or s.get("a11y_scanned"):
            if not s.get("a11y_pages"):
                pages.add((target, ""))
        for v in violations:
            findings[(target, v.get("url"), v.get("rule"), v.get("selector"))] = v.get("severity", "minor")
    penalty = sum(sev_weight.get(sev, 1.0) for sev in findings.values())
    a11y = _clamp(100 - penalty / len(pages)) if pages else None

    # D. UX : humeur finale normalisée + friction (actions superflues) + abandons
    n = max(len(completed), 1)
    mood_avg = sum(s.get("mood_final", 0.0) for s in completed) / n
    mood_score = (mood_avg + 5.0) / 10.0 * 100
    friction = sum(s.get("friction_extra_actions", 0) for s in completed) / n
    ux = _clamp(mood_score * 0.7 + 30 * 0.3 - friction * 2.0 - abandoned * 8) if completed else None

    # E. stabilité : erreurs console, crashes
    console_errors = sum(len(s.get("console_errors") or []) for s in sessions)
    crashes = sum(1 for s in sessions if s.get("crashed"))
    freezes = sum(len(s.get("freezes_ms", [])) for s in sessions)
    stability_observed = completed or console_errors or crashes or any(s.get("stability_observed") for s in sessions)
    stab = _clamp(100 - console_errors * 5 - crashes * 40 - freezes * 15) if stability_observed else None

    axes = [
        AxisScore("fonctionnel", fonc, {"steps_total": total_steps, "steps_passed": passed,
                                       "abandoned": abandoned, "errors": errors}),
        AxisScore("perf", perf, {"samples": len(ratios)}),
        AxisScore("a11y", a11y, {"violations": len(findings), "pages_scanned": len(pages)}),
        AxisScore("ux", ux, {"mood_avg": round(mood_avg, 2), "friction_per_session": round(friction, 2)}),
        AxisScore("stabilite", stab, {"console_errors": console_errors, "crashes": crashes, "freezes": freezes}),
    ]
    for axis in axes:
        if axis.score is not None:
            axis.score = round(axis.score, 1)
    return axes


def global_score(axes: list[AxisScore], weights: dict[str, float]) -> float | None:
    if not any(a.name == "fonctionnel" and a.score is not None for a in axes):
        return None
    observed = [a for a in axes if a.score is not None]
    weight = sum(weights.get(a.name, 0.0) for a in observed)
    if not weight:
        return None
    return round(sum(weights.get(a.name, 0.0) * a.score for a in observed) / weight, 1)
