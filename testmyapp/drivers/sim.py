"""Driver simulé : une « webapp » fictive en mémoire, déterministe.

Objectifs (spec §1.3 O7, §20) : permettre ``init && run`` et les tests du
produit sans navigateur réel, avec un comportement reproductible par
graine. Il simule : navigation, formulaires, délais de chargement
paramétrables, erreurs console, violations a11y — assez fidèlement pour
que les métriques, l'humeur et le rapport soient significatifs.

La « page » est modélisée par un dict de pages nommées avec éléments
(role, name) ; les actions agissent dessus comme le ferait Playwright.
"""
from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Any

from testmyapp.drivers.base import ActionResult, Driver, PageState

# « Application » fictive : pages -> éléments. Les délais simulent un
# serveur lent (page checkout) ; un bouton sans nom simule une violation a11y.
SIM_APP: dict[str, dict[str, Any]] = {
    "home": {
        "url": "sim://home",
        "title": "Boutique Démo — Accueil",
        "load_ms": 300,
        "elements": [
            {"role": "searchbox", "name": "Rechercher un produit"},
            {"role": "button", "name": "Rechercher"},
            {"role": "link", "name": "Catalogue"},
            {"role": "link", "name": "Mon compte"},
            {"role": "banner", "name": "En-tête", "accessible": False},  # sans nom accessible
        ],
    },
    "results": {
        "url": "sim://results",
        "title": "Résultats de recherche",
        "load_ms": 600,
        "elements": [
            {"role": "link", "name": "Lampe de bureau « Lumen »"},
            {"role": "link", "name": "Carnet A5 quadrillé"},
            {"role": "button", "name": "Ajouter au panier"},
            {"role": "link", "name": "Panier (1)"},
        ],
    },
    "product": {
        "url": "sim://product/lumen",
        "title": "Lampe de bureau « Lumen »",
        "load_ms": 400,
        "elements": [
            {"role": "button", "name": "Ajouter au panier"},
            {"role": "link", "name": "Panier (1)"},
            {"role": "link", "name": "Continuer mes achats"},
        ],
    },
    "cart": {
        "url": "sim://cart",
        "title": "Votre panier",
        "load_ms": 350,
        "elements": [
            {"role": "textbox", "name": "Code promo"},
            {"role": "button", "name": "Appliquer"},
            {"role": "button", "name": "Passer la commande"},  # cible 24px (a11y: cible tactile)
            {"role": "link", "name": "Retour au catalogue"},
        ],
    },
    "checkout": {
        "url": "sim://checkout",
        "title": "Paiement",
        "load_ms": 2600,  # page volontairement lente (LCP KO en realtime)
        "elements": [
            {"role": "textbox", "name": "Numéro de carte"},
            {"role": "textbox", "name": "Date d'expiration"},
            {"role": "textbox", "name": "CVC"},
            {"role": "button", "name": "Payer maintenant"},
            {"role": "checkbox", "name": "", "accessible": False},  # case sans label (a11y)
        ],
    },
    "confirmation": {
        "url": "sim://confirmation",
        "title": "Commande confirmée",
        "load_ms": 300,
        "elements": [
            {"role": "heading", "name": "Merci pour votre commande !"},
            {"role": "link", "name": "Retour à l'accueil"},
        ],
    },
}

TOUCH_TARGET_VIOLATIONS = {"Passer la commande"}  # 24 px < 44 px (seuil §8.2)


class SimDriver(Driver):
    def __init__(self, target_cfg: dict[str, Any], persona_cfg: dict[str, Any],
                 run_dir: Path, session_id: str) -> None:
        super().__init__(target_cfg, persona_cfg, run_dir, session_id)
        self.page = "home"
        self.started_at = 0.0
        self.console_errors: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.vitals: dict[str, float] = {}
        self._rng = random.Random(f"{session_id}-sim")
        self._action_count = 0
        # le throttling CPU du persona ralentit les pages simulées (§4.1/§6.2)
        self._cpu = float(persona_cfg.get("cpu_throttle", 1.0))

    async def launch(self) -> None:
        self.started_at = time.time()
        await asyncio.sleep(0.05)

    def _goto(self, page: str, duration_ms: float) -> None:
        info = SIM_APP[page]
        self.page = page
        # lcp dépend du throttling CPU du persona : une machine modeste
        # dépasse le seuil sur la page lente, une machine rapide non.
        lcp = info["load_ms"] * self._cpu * 0.85
        self.vitals[f"lcp_ms::{info['url']}"] = round(lcp, 1)
        self.vitals[f"cls::{info['url']}"] = 0.02 if page != "checkout" else 0.18  # CLS KO sur checkout
        self.requests.append({"url": info["url"], "status": 200, "ms": info["load_ms"]})

    async def perform(self, action: dict[str, Any]) -> ActionResult:
        t0 = time.perf_counter()
        atype = action.get("type", "")
        info = SIM_APP[self.page]
        await asyncio.sleep(info.get("load_ms", 200) / 1000.0 * 0.2)
        if atype == "navigate":
            url = action.get("url", "")
            page = "home"
            if "results" in url:
                page = "results"
            elif "product" in url:
                page = "product"
            elif "cart" in url:
                page = "cart"
            elif "checkout" in url:
                page = "checkout"
            elif "confirmation" in url:
                page = "confirmation"
            self._goto(page, 0)
            return ActionResult(True, "navigate", (time.perf_counter() - t0) * 1000, detail={"url": url})
        if atype in {"click", "tap"}:
            name = action.get("name", action.get("selector", ""))
            device = self.persona_cfg.get("input_device") or "mouse"
            interaction = "touch" if atype == "tap" and device != "keyboard" else device
            if not any(el.get("visible", True) and el.get("name") == name for el in info["elements"]):
                return ActionResult(False, atype, (time.perf_counter() - t0) * 1000,
                                    error="élément indisponible dans la vue simulée", detail={"target": name})
            self._action_count += 1
            # cible tactile non conforme : échec partiel pour personas tactiles (§6.4)
            if name in TOUCH_TARGET_VIOLATIONS and interaction == "touch":
                if self._rng.random() < 0.4:
                    return ActionResult(False, atype, (time.perf_counter() - t0) * 1000,
                                        error="cible tactile trop petite (24 px), tap manqué",
                                        detail={"a11y_touch_target": True})
            # navigation associée aux clics connus
            routes = {
                "Rechercher": "results",
                "Ajouter au panier": "product" if self.page == "results" else "cart",
                "Panier (1)": "cart",
                "Passer la commande": "checkout",
                "Payer maintenant": "confirmation",
                "Appliquer": None,
            }
            target_page = routes.get(str(name), None)
            if target_page:
                self._goto(target_page, 0)
            return ActionResult(True, atype, (time.perf_counter() - t0) * 1000,
                                detail={"name": name, "interaction": interaction})
        if atype == "wait_for":
            selector = str(action.get("selector", "")).casefold()
            visible = any(el.get("visible", True) and (el.get("name") or "").casefold() == selector
                          for el in info["elements"])
            return ActionResult(visible, atype, (time.perf_counter() - t0) * 1000,
                                error="" if visible else "élément introuvable dans la simulation")
        if atype in {"type", "press", "select", "check", "scroll", "hover"}:
            # saisie : simule une validation serveur, une erreur console occasionnelle
            if atype == "type" and self._rng.random() < 0.05:
                self.console_errors.append({"level": "error", "text": "TypeError: undefined is not an object",
                                            "source": "console"})
            # Enter soumet le formulaire de recherche depuis l'accueil (comme la webapp)
            if atype == "press" and action.get("key") == "Enter" and self.page == "home":
                self._goto("results", 0)
            return ActionResult(True, atype, (time.perf_counter() - t0) * 1000)
        return ActionResult(False, atype, (time.perf_counter() - t0) * 1000, error=f"action inconnue '{atype}'")

    async def observe(self) -> dict[str, Any]:
        info = SIM_APP[self.page]
        violations = []
        for el in info["elements"]:
            if el.get("accessible") is False:
                violations.append({"rule": "button-name", "severity": "serious",
                                   "selector": el.get("name") or el.get("role"), "impact": "blocking"})
            if el.get("name") in TOUCH_TARGET_VIOLATIONS:
                violations.append({"rule": "target-size", "severity": "moderate",
                                   "selector": el["name"], "impact": {"w": 24, "h": 24}})
        return {
            "url": info["url"],
            "console_errors": self.console_errors,
            "requests": self.requests[-5:],
            "vitals": dict(self.vitals),
            "a11y_violations": violations,
            "a11y_scanned": True,
        }

    async def snapshot(self) -> PageState:
        info = SIM_APP[self.page]
        return PageState(url=info["url"], title=info["title"],
                         elements=[dict(e) for e in info["elements"]],
                         console_errors=len(self.console_errors))

    async def screenshot(self, name: str) -> Path | None:
        # driver simulé : pas de capture réelle, on dépose un marqueur SVG
        shots = self.run_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        info = SIM_APP[self.page]
        path = shots / f"{name}.svg"
        bars = "".join(
            f'<rect x="{20 + i * 90}" y="60" width="70" height="90" fill="#4a5a7a"/>'
            for i in range(min(len(info["elements"]), 8))
        )
        path.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="300">'
            f'<rect width="800" height="300" fill="#1c2230"/>'
            f'<text x="20" y="36" fill="#cfe0ff" font-family="monospace" font-size="20">'
            f'[sim] {info["title"]}</text>{bars}</svg>',
            encoding="utf-8",
        )
        return path

    async def stop(self) -> None:
        return None
