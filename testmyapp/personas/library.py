"""Bibliothèque de personas embarquée + génération contrainte (spec §4).

- ``builtin_personas()`` : les 8 archétypes de la spec §4.2 (TOML embarqués).
- ``load_personas(dir)`` : lit un dossier de fichiers TOML.
- ``generate(archetype, count, seed)`` : variants comportementaux
  reproductibles autour d'un archétype (§4.3).
"""
from __future__ import annotations

import random
from importlib import resources
from pathlib import Path

from testmyapp.config import Persona, persona_from_toml, persona_to_toml_dict, load_toml
from testmyapp.core.rng import make_rng


def _parse_persona_text(text: str, source: str) -> Persona:
    import tomllib

    data = tomllib.loads(text)
    return persona_from_toml(data, source)


def builtin_personas() -> dict[str, Persona]:
    out: dict[str, Persona] = {}
    root = resources.files("testmyapp") / "assets" / "personas"
    for entry in sorted(root.iterdir()):
        if entry.name.endswith(".toml"):
            out[entry.name.removesuffix(".toml")] = _parse_persona_text(
                entry.read_text(encoding="utf-8"), f"builtin:{entry.name}"
            )
    return out


def load_personas(directory: Path | None, extra_ids: list[str] | None = None) -> dict[str, Persona]:
    """Personas d'un dossier projet, complétés par les archétypes embarqués
    demandés. Les fichiers projet écrasent les embarqués à id égal."""
    lib = builtin_personas()
    if directory and Path(directory).is_dir():
        for path in sorted(Path(directory).glob("*.toml")):
            p = persona_from_toml(load_toml(path), str(path))
            lib[p.id] = p
    if extra_ids:
        missing = [pid for pid in extra_ids if pid not in lib]
        if missing:
            raise ValueError(f"Personas inconnus : {', '.join(missing)}")
    return lib


# ---------------------------------------------------------------- génération
_RANGES = {
    "patience": (0.1, 0.9),
    "curiosity": (0.1, 0.9),
    "tech_savviness": (0.05, 0.95),
    "error_proneness": (0.02, 0.35),
    "persistence": (0.15, 0.95),
    "help_seeking": (0.05, 0.9),
    "speed_factor": (0.7, 2.0),
}
_FIRST_NAMES = ["Jules", "Lila", "Omar", "Sofia", "Tom", "Inès", "Rayan", "Chloé", "Noé", "Maya"]
_OCCUPATIONS = ["Étudiant·e", "Infirmier·ère", "Vendeur·se", "Chauffeur·se", "Fonctionnaire", "Freelance"]


def generate_variants(archetype: Persona, count: int, seed: int) -> list[Persona]:
    """Variants aléatoires reproductibles : chaque trait varie d'au plus
    30 % de l'étendue autorisée autour de l'archétype (borné), identité
    légèrement modifiée (§4.3)."""
    rng = make_rng(seed, "persona-gen", archetype.id)
    out: list[Persona] = []
    for i in range(count):
        data = persona_to_toml_dict(archetype)
        data["id"] = f"{archetype.id}-v{i + 1}"
        data["name"] = f"{rng.choice(_FIRST_NAMES)} ({archetype.name}†{i + 1})"
        data["age"] = int(max(18, archetype.age + rng.randint(-8, 8)))
        data["occupation"] = rng.choice(_OCCUPATIONS)
        behavior = data["behavior"]
        for key, (lo, hi) in _RANGES.items():
            base = behavior.get(key, 0.5)
            if key == "speed_factor":
                v = base * rng.uniform(0.85, 1.15)
            else:
                spread = (hi - lo) * 0.30
                v = base + rng.uniform(-spread, spread)
            behavior[key] = round(min(hi, max(lo, v)), 3)
        emotion = data["emotion"]
        emotion["bias"] = round(max(-1.0, min(1.0, emotion["bias"] + rng.uniform(-0.3, 0.3))), 2)
        out.append(persona_from_toml(data, f"generated:{data['id']}"))
    return out


def write_persona_toml(p: Persona, path: Path) -> None:
    """Export TOML canonique (§4.3)."""
    Path(path).write_text(render_toml(p), encoding="utf-8")


def render_toml(p: Persona) -> str:
    """Sérialisation TOML lisible sans dépendance externe."""
    d = persona_to_toml_dict(p)
    lines: list[str] = []

    def fmt(v: object) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return '"' + v.replace('"', '\\"') + '"'
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return repr(v)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return "[" + ", ".join('"' + x + '"' for x in v) + "]"
        raise TypeError(f"non sérialisable : {v!r}")

    def emit_table(prefix: str, table: dict, header: str | None = None) -> None:
        # header : nom complet tel qu'écrit dans l'en-tête [table] ;
        # les clés scalaires sont toujours relatives à l'en-tête courant.
        scalars = {k: v for k, v in table.items() if not isinstance(v, dict) and not _is_table_list(v)}
        for k, v in scalars.items():
            lines.append(f"{k} = {fmt(v)}")
        for k, v in table.items():
            full = f"{header}.{k}" if header else k
            if isinstance(v, dict):
                lines.append("")
                lines.append(f"[{full}]")
                emit_table(k, v, full)
            elif _is_table_list(v):
                for item in v:
                    lines.append("")
                    lines.append(f"[[{full}]]")
                    emit_table(k, item, full)

    def _is_table_list(v: object) -> bool:
        return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)

    emit_table("", d)
    return "\n".join(lines) + "\n"
