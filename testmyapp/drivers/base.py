"""Protocole des drivers (spec §6.1).

Un driver exécute des actions déjà modulées par le persona et collecte des
observations. Aucune décision, aucun scoring. Les drivers asynchrones sont
lancés dans un thread dédié (Playwright et UIA sont bloquants) ; le moteur
reste en asyncio.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def public_url(url: str) -> str:
    """Conserve l'origine et le chemin, sans authentifiants ni paramètres de formulaire."""
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "[URL masquée]"
    if parsed.scheme not in {"http", "https", "sim"}:
        return parsed.scheme + ":" if parsed.scheme else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))


@dataclass
class ActionResult:
    ok: bool
    action: str
    duration_ms: float
    error: str = ""
    # contexte renvoyé pour les observations (sélecteur résolu, statut HTTP…)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageState:
    """Instantané compact de l'écran/la page — utilisé par M2 et le rapport."""

    url: str = ""
    title: str = ""
    elements: list[dict[str, Any]] = field(default_factory=list)  # role, name, visible, rect
    console_errors: int = 0
    screenshot: Path | None = None


class Driver(ABC):
    """Cycle de vie : launch → (perform | observe | snapshot | screenshot)* → stop."""

    def __init__(self, target_cfg: dict[str, Any], persona_cfg: dict[str, Any],
                 run_dir: Path, session_id: str) -> None:
        self.target_cfg = target_cfg
        self.persona_cfg = persona_cfg
        self.run_dir = Path(run_dir)
        self.session_id = session_id

    @abstractmethod
    async def launch(self) -> None:
        """Démarre navigateur/application pour ce persona."""

    @abstractmethod
    async def perform(self, action: dict[str, Any]) -> ActionResult:
        """Exécute une action élémentaire (dict: type + paramètres)."""

    @abstractmethod
    async def observe(self) -> dict[str, Any]:
        """Observations courantes : console, réseau, métriques perf, a11y."""

    @abstractmethod
    async def snapshot(self) -> PageState:
        """État de la page/écran (pour l'exploratoire et le rapport)."""

    @abstractmethod
    async def screenshot(self, name: str) -> Path | None:
        """Capture nommée dans run_dir/screenshots/."""

    @abstractmethod
    async def stop(self) -> None:
        """Arrêt propre."""

    async def health(self) -> dict[str, Any]:
        """Santé du processus cible : crashed / frozen / ressources (desktop)."""
        return {"crashed": False, "frozen": False}


def create_driver(target: dict[str, Any], persona: dict[str, Any],
                  run_dir: Path, session_id: str) -> Driver:
    """Fabrique selon target.type (web | sim | desktop)."""
    ttype = target.get("type", "web")
    if ttype == "web":
        from testmyapp.drivers.web import WebDriver

        return WebDriver(target, persona, run_dir, session_id)
    if ttype == "sim":
        from testmyapp.drivers.sim import SimDriver

        return SimDriver(target, persona, run_dir, session_id)
    if ttype == "desktop":
        from testmyapp.drivers.desktop import DesktopDriver

        return DesktopDriver(target, persona, run_dir, session_id)
    raise ValueError(f"type de cible inconnu : {ttype}")
