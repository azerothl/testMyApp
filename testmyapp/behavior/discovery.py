"""Choix locaux de navigation à partir des contrôles actuellement visibles."""

import re
import unicodedata

QUOTED_TEXT = re.compile(r'(?:«([^»]+)»|"([^"]+)")')

def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(re.findall(r"[a-z0-9]+", plain.encode("ascii", "ignore").decode()))


def message_from_goal(goal: str) -> str | None:
    quoted = QUOTED_TEXT.search(goal)
    words = normalize(goal)
    if not any(word.startswith(("demand", "envoi", "envoy", "saisi", "ecri")) for word in words.split()):
        return None
    if quoted:
        return next(value for value in quoted.groups() if value)
    if any(word in words for word in ("aide", "capaci", "possibil", "sait faire", "sais faire", "peut faire")):
        return "Aide"
    return None


def task_for_tomorrow(goal: str) -> str | None:
    words = normalize(goal).split()
    quoted = QUOTED_TEXT.search(goal)
    if words and words[0] in {"creer", "cree", "ajouter", "ajoute", "planifier"}:
        if "tache" in words and "demain" in words and quoted:
            return next(value for value in quoted.groups() if value).strip()
    return None


def task_transition(goal: str) -> tuple[str, str] | None:
    quoted = QUOTED_TEXT.search(goal)
    if not quoted:
        return None
    words = normalize(QUOTED_TEXT.sub(" ", goal, count=1))
    title = next(value for value in quoted.groups() if value).strip()
    if "tache" in words and "en cours" in words and words.startswith(("passer ", "mettre ")):
        return title, "en cours"
    if "tache" in words and words.startswith(("valider ", "terminer ")):
        return title, "terminée"
    return None


def choose_control(elements: list[dict], goal: str, persona, visited: set) -> dict | None:
    goal = normalize(goal)
    terms = {word[:5] for word in goal.split() if len(word) >= 4 and word not in {
        "dans", "avec", "sans", "pour", "voir", "faire", "puis", "quel", "sous",
        "ouvrir", "aller", "trouver", "depuis", "lire", "consulter", "afficher",
    }}
    surface = tuple(item["name"] for item in elements if item["role"] == "tab" and item["state"] == "selected")
    candidates = []
    for item in elements:
        label = normalize(item["name"])
        role = item["role"]
        key = (surface, role, label)
        if not label or key in visited or role not in {"button", "checkbox", "tab", "node", "listitem"} or item["state"] == "selected":
            continue
        if label.startswith(("lancer ", "supprimer ", "connecter ", "installer ")) and label not in goal:
            continue
        overlap = terms & {word[:5] for word in label.split() if len(word) >= 4}
        history = (role == "listitem" and any("execut" in normalize(tab) for tab in surface)
                   and bool(terms & {"execu", "histo"}))
        history_nav = label == "historique" and bool(terms & {"trace", "creat", "journ"})
        if not overlap and role != "tab" and not history and not history_nav:
            continue
        exact = len(label) >= 4 and f" {label} " in f" {goal} "
        score = 2 * len(overlap) + 3 * exact
        if role == "tab" and not score:
            score = .25 + persona.curiosity
        if "aide" in label and overlap:
            score += persona.help_seeking
        if overlap and role in {"checkbox", "node"}:
            score += persona.tech_savviness
        if history or history_nav:
            score += 10
        reason = (
            "history" if history else "history_nav" if history_nav else "label" if overlap or exact else "tab"
        )
        candidates.append((score, {**item, "decision": reason}, key))
    if not candidates:
        return None
    _, item, key = max(candidates, key=lambda candidate: candidate[0])
    visited.add(key)
    return item
