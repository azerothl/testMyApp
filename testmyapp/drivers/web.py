"""Driver web Playwright (spec §6.2).

Playwright synchrone exécuté dans un thread DÉDIÉ (ThreadPoolExecutor à un
thread par driver) : l'API synchrone de Playwright est liée au thread qui
l'a créée — tous les appels passent donc par le même exécuteur. Par
persona : viewport/DPR/touch, locale, limitation réseau et CPU sous Chromium.
Observations : console, réseau, mesures de performance de laboratoire,
contrôles axe-core hors ligne et traces de parcours clavier.

Sélecteurs — cascade `role+name` → label → text → test-id → css (§6.2) ;
un clic par `name` essaie button puis link puis texte.
"""
from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from testmyapp.drivers.base import ActionResult, Driver, PageState, public_url

_ASSETS = files("testmyapp") / "assets"

# Réseaux (spec §4.1) : latence ms, débit kbit/s — profils §6.2
NETWORK_PROFILES: dict[str, dict[str, int]] = {
    "fiber": {"latency": 10, "download": 100_000, "upload": 50_000},
    "dsl": {"latency": 60, "download": 8_000, "upload": 1_000},
    "4g": {"latency": 100, "download": 9_000, "upload": 3_000},
    "3g": {"latency": 200, "download": 1_500, "upload": 750},
    "slow-3g": {"latency": 400, "download": 400, "upload": 400},
    "offline-flaky": {"latency": 100, "download": 9_000, "upload": 3_000},  # + pertes aléatoires
}

SNAPSHOT_JS = r"""elements => elements.slice(0, 100).map(el => {
  const rect = el.getBoundingClientRect();
  const role = el.getAttribute("role") || ({ A: "link", BUTTON: "button", INPUT: el.type === "search" ? "searchbox" : "textbox" })[el.tagName] || "";
  const labelledBy = (el.getAttribute("aria-labelledby") || "").split(/\s+/)
    .map(id => document.getElementById(id)?.textContent || "").join(" ").trim();
  const name = labelledBy || el.getAttribute("aria-label") ||
    [...(el.labels || [])].map(label => label.textContent).join(" ") ||
    el.innerText || el.getAttribute("title") || "";
  return { role, name: name.trim(), visible: rect.width > 0 && rect.height > 0 &&
    getComputedStyle(el).visibility === "visible" && !el.closest('[aria-hidden="true"], [inert]') };
})"""

DISCOVERY_SELECTOR = "a[href], button, summary, input, textarea, select, [role=tab], [role=button], [role=checkbox]"
DISCOVERY_JS = r"""elements => {
  const ids = window.__testmyappControlIds ??= { ids: new WeakMap(), next: 0 };
  const records = elements.slice(0, 180).flatMap((el, index) => {
    const rect = el.getBoundingClientRect();
    if (el.disabled || el.type === "password" || !rect.width || !rect.height ||
        getComputedStyle(el).visibility !== "visible" || el.closest('[aria-hidden="true"], [inert]')) return [];
    const role = el.getAttribute("role") || (
      el.type === "checkbox" ? "checkbox" :
      el.tagName === "SELECT" ? "select" :
      ["TEXTAREA", "INPUT"].includes(el.tagName) ? "entry" : "button");
    const label = el.cloneNode(true);
    for (const hidden of label.querySelectorAll('[aria-hidden="true"]')) hidden.remove();
    const labels = [...(el.labels || [])].map(label => {
      const copy = label.cloneNode(true);
      for (const control of copy.querySelectorAll("select, input, textarea, [aria-hidden=true]")) control.remove();
      return copy.textContent;
    }).join(" ");
    const name = (el.getAttribute("aria-label") || labels ||
      label.textContent || el.placeholder || "").replace(/\s+/g, " ").trim();
    if (!name) return [];
    if (!ids.ids.has(el)) ids.ids.set(el, String(++ids.next));
    const state = el.getAttribute("aria-selected") === "true" || el.getAttribute("aria-pressed") === "true" ?
      "selected" : role === "checkbox" ? (el.checked || el.getAttribute("aria-checked") === "true" ? "checked" : "unchecked") :
      role === "entry" && el.value ? "filled" : null;
    const context = role === "button" ? el.parentElement?.parentElement?.innerText.slice(0, 1200) : null;
    const options = role === "select" ? [...el.options].filter(option => !option.disabled).map(option => option.label) : [];
    const dialog = el.closest('[role="dialog"]')?.querySelector("h2")?.innerText || "";
    return [{ name, role, state, id: ids.ids.get(el), index, context, options, dialog,
              headline: el.querySelector("strong")?.innerText || "", focused: el === document.activeElement }];
  });
  records.push({ name: document.body?.innerText.slice(0, 20000) || "", role: "text", state: null });
  return records;
}"""


class WebDriver(Driver):
    """Un contexte navigateur par persona (spec §6.2)."""

    def __init__(self, target_cfg: dict[str, Any], persona_cfg: dict[str, Any],
                 run_dir: Path, session_id: str) -> None:
        super().__init__(target_cfg, persona_cfg, run_dir, session_id)
        # Playwright sync est lié au thread créateur : exécuteur dédié 1 thread
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"pw-{session_id}")
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp = None
        self.console_errors: list[dict] = []
        self.requests: list[dict] = []
        self.vitals: dict[str, float] = {}
        self._statuses: dict = {}
        self._audits: dict[str, dict] = {}
        self._audit_errors: dict[str, str] = {}
        self._focus_checks: dict[tuple[str, str], dict] = {}
        self._crashed = False
        self._trace_started = False
        self._keyboard_only = persona_cfg.get("motor") == "keyboard_only"
        self._axe_scan = (_ASSETS / "web" / "axe-scan.js").read_text(encoding="utf-8")
        self._base_url = str(target_cfg.get("base_url", "")).rstrip("/")

    # -- thread body --------------------------------------------------
    def _sync_launch(self) -> None:
        from playwright.sync_api import sync_playwright

        p = self.persona_cfg
        self._pw = sync_playwright().start()
        engine = str(self.target_cfg.get("browser") or p.get("browser") or "chromium")
        launcher = {
            "chromium": self._pw.chromium,
            "firefox": self._pw.firefox,
            "webkit": self._pw.webkit,
        }.get(engine)
        if launcher is None:
            raise ValueError(f"moteur Playwright inconnu : {engine}")
        self._browser = launcher.launch(headless=True)
        self.environment = {"browser": engine, "version": self._browser.version}
        viewport = {"width": int(p.get("screen_w", 1440)), "height": int(p.get("screen_h", 900))}
        context_options = {}
        if p.get("timezone"):
            context_options["timezone_id"] = p["timezone"]
        if self.target_cfg.get("storage_state"):
            context_options["storage_state"] = self.target_cfg["storage_state"]
        if self.target_cfg.get("video"):
            context_options["record_video_dir"] = str(self.run_dir / "videos")
        self._context = self._browser.new_context(
            viewport=viewport,
            device_scale_factor=float(p.get("dpr", 1.0)),
            has_touch=p.get("input_device") == "touch",
            is_mobile=p.get("device_type") == "phone" and engine != "firefox",
            locale=p.get("locale", "fr-FR"),
            **context_options,
        )
        self._context.add_init_script(script=(_ASSETS / "web" / "vitals.js").read_text(encoding="utf-8"))
        self._context.add_init_script(script=(_ASSETS / "vendor" / "axe-core" / "axe.min.js").read_text(encoding="utf-8"))
        if self.target_cfg.get("trace"):
            self._context.tracing.start(screenshots=True, snapshots=True, sources=False)
            self._trace_started = True
        self._page = self._context.new_page()
        self._page.set_default_timeout(8000)
        # collecte console
        self._page.on("console", lambda m: self._on_console(m))
        self._page.on("pageerror", lambda e: self.console_errors.append(
            {"level": "error", "text": str(e), "source": "pageerror"}))
        self._page.on("response", lambda response: self._statuses.update({response.request: response.status}))
        self._page.on("requestfinished", self._on_request)
        self._page.on("requestfailed", lambda request: self._on_request(request, failed=True))
        self._page.on("crash", lambda *_: setattr(self, "_crashed", True))
        # throttling chromium uniquement (CDP)
        profile = str(p.get("network_profile", "fiber"))
        if engine == "chromium" and profile in NETWORK_PROFILES:
            self._cdp = self._context.new_cdp_session(self._page)
            net = NETWORK_PROFILES[profile]
            self._cdp.send("Network.enable")
            self._cdp.send("Network.emulateNetworkConditions", {
                "offline": False, "latency": net["latency"],
                "downloadThroughput": net["download"] * 125, "uploadThroughput": net["upload"] * 125,
            })
            throttle = float(p.get("cpu_throttle", 1.0))
            if throttle != 1.0:
                self._cdp.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
        if self.target_cfg.get("free_discovery"):
            self._page.goto(self._base_url, wait_until="load")

    def _on_console(self, msg) -> None:
        if msg.type in {"error", "warning"}:
            self.console_errors.append({"level": msg.type, "text": msg.text, "source": "console"})

    def _on_request(self, request, *, failed: bool = False) -> None:
        timing = request.timing
        self.requests.append({"url": public_url(request.url), "method": request.method,
                              "resource_type": request.resource_type, "failed": failed,
                              "status": self._statuses.pop(request, None),
                              "duration_ms": timing["responseEnd"] if timing["responseEnd"] >= 0 else None,
                              "ttfb_ms": timing["responseStart"] if timing["responseStart"] >= 0 else None})

    def _resolve_selector(self, page, action: dict[str, Any]):
        """Cascade de résolution (§6.2) : role+name → label → text → test-id → css."""
        pref = action.get("selector_pref") or action.get("selector") or ""
        if pref.startswith("role:"):
            # format role:button[name='Payer']
            inside = pref[5:]
            name = ""
            if "[name=" in inside:
                role, rest = inside.split("[name=", 1)
                name = rest.rstrip("]").strip("'\"")
            else:
                role = inside
            return page.get_by_role(role, name=name or None)
        if pref.startswith("label:"):
            return page.get_by_label(pref[6:])
        if pref.startswith("text:"):
            return page.get_by_text(pref[5:])
        if pref.startswith("test-id:"):
            return page.get_by_test_id(pref[8:])
        if pref.startswith("css:"):
            return page.locator(pref[4:])
        if pref:
            # sélecteur nu : essayer css puis texte
            return page.locator(pref)
        # name seul : pas de résolution ici → la cascade button/link/text
        # de _sync_click_by_name prend le relais (les liens sont courants).
        return None

    def _name_locator(self, page, name: str):
        return (page.get_by_role("button", name=name, exact=True)
                .or_(page.get_by_role("link", name=name, exact=True))
                .or_(page.get_by_text(name, exact=True)))

    def _focus_with_tab(self, locator, timeout_ms: int) -> None:
        page = self._page
        end = time.monotonic() + timeout_ms / 1000
        locator.wait_for(state="visible", timeout=timeout_ms)
        try:
            for _ in range(48):
                if time.monotonic() >= end:
                    break
                if locator.evaluate("element => element === element.ownerDocument.activeElement",
                                    timeout=max(1, (end - time.monotonic()) * 1000)):
                    return
                page.keyboard.press("Tab")
                self._record_focus()
        except Exception as exc:
            raise RuntimeError("Parcours clavier interrompu pendant la recherche du contrôle") from exc
        raise RuntimeError("Contrôle introuvable dans l’ordre de tabulation")

    def _record_focus(self) -> None:
        item = self._page.evaluate("""() => {
          const el = document.activeElement;
          if (!el || el === document.body || el === document.documentElement) return null;
          const style = getComputedStyle(el);
          const selector = el.id ? "#" + CSS.escape(el.id) :
            el.tagName.toLowerCase() + (el.name ? "[name=" + CSS.escape(el.name) + "]" : "");
          return { selector, tag: el.tagName.toLowerCase(),
            outline: style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0,
            shadow: style.boxShadow !== "none" };
        }""")
        if item:
            self._focus_checks[(public_url(self._page.url), item["selector"])] = item

    def _sync_perform(self, action: dict[str, Any]) -> ActionResult:
        self._inspect_page()
        t0 = time.perf_counter()
        atype = action.get("type", "")
        page = self._page
        timeout = int(action.get("timeout_ms", 5000))
        try:
            observed = atype in {"observed_click", "observed_type", "observed_select"}
            if observed:
                atype = atype.removeprefix("observed_")
                controls = page.locator(DISCOVERY_SELECTOR)
                item = next((item for item in controls.evaluate_all(DISCOVERY_JS)
                             if item.get("id") == action["control_id"] and item["name"] == action.get("name")), None)
                expected = {"type": "entry", "select": "select"}.get(atype)
                if (item is None or (expected and item["role"] != expected)
                        or (not expected and item["role"] in {"entry", "select"})):
                    return self._result(False, atype, t0, error="Le contrôle observé n’est plus disponible")
                observed_locator = controls.nth(item["index"])
            if atype == "wait_for_url":
                pattern = action["pattern"]
                try:
                    page.wait_for_url(lambda url: pattern in url, wait_until="load", timeout=timeout)
                except Exception:
                    return self._result(False, atype, t0, error="adresse attendue introuvable",
                                        detail={"url": public_url(page.url)})
                return self._result(True, atype, t0, detail={"url": public_url(page.url)})
            if atype == "navigate":
                url = str(action.get("url", "/")).replace("{target.base_url}", self._base_url)
                full = urljoin(self._base_url + "/", url)
                response = page.goto(full, wait_until="load", timeout=timeout)
                ok = response is None or response.ok
                return self._result(ok, atype, t0,
                                    error="" if ok else f"HTTP {response.status}",
                                    detail={"url": public_url(full)})
            if atype in {"click", "tap", "type", "wait_for", "hover", "dblclick", "select", "check"}:
                loc = observed_locator if observed else self._resolve_selector(page, action)
                if loc is None and action.get("name"):
                    loc = self._name_locator(page, str(action["name"])).and_(page.locator(":visible"))
                if loc is None:
                    return self._result(False, atype, t0, error="aucune cible résolue")
                loc = loc.first
                keyboard = self._keyboard_only or (
                    self.persona_cfg.get("input_device") == "keyboard" and (atype in {"click", "type"}
                                                                           or observed and atype == "select"))
                detail = {"resolution": "observed"} if observed else {}
                if observed and keyboard:
                    try:
                        self._focus_with_tab(loc, timeout if self._keyboard_only else min(timeout, 1500))
                    except Exception:
                        if self._keyboard_only:
                            raise
                        keyboard = False
                        detail["decision"] = "keyboard_fallback"
                if atype == "wait_for":
                    loc.wait_for(state="visible", timeout=timeout)
                elif atype == "hover":
                    if self._keyboard_only:
                        self._focus_with_tab(loc, timeout)
                    else:
                        loc.hover(timeout=timeout)
                elif atype == "type":
                    value = str(action.get("text", ""))
                    if keyboard:
                        if not observed:
                            self._focus_with_tab(loc, timeout)
                        page.keyboard.press("ControlOrMeta+a")
                        page.keyboard.insert_text(value)
                        detail["interaction"] = "keyboard"
                    else:
                        loc.fill(value, timeout=timeout)
                elif atype == "select":
                    if observed and keyboard:
                        selected = loc.evaluate("el => el.selectedOptions[0]?.label")
                        options = item["options"]
                        offset = options.index(action["label"]) - options.index(selected)
                        for _ in range(abs(offset)):
                            page.keyboard.press("ArrowDown" if offset > 0 else "ArrowUp")
                        detail["interaction"] = "keyboard"
                    elif observed:
                        loc.select_option(label=action["label"], timeout=timeout)
                    else:
                        loc.select_option(str(action.get("value", "")), timeout=timeout)
                elif atype == "check":
                    checked = bool(action.get("checked", True))
                    if self._keyboard_only:
                        self._focus_with_tab(loc, timeout)
                        if loc.is_checked() != checked:
                            page.keyboard.press("Space")
                    else:
                        loc.set_checked(checked, timeout=timeout)
                elif atype == "dblclick":
                    if self._keyboard_only:
                        return self._result(False, atype, t0, error="double clic inaccessible au clavier")
                    loc.dblclick(timeout=timeout)
                elif keyboard:
                    if not observed:
                        self._focus_with_tab(loc, timeout)
                    page.keyboard.press("Enter")
                    detail["interaction"] = "keyboard"
                elif atype == "tap" or self.persona_cfg.get("input_device") == "touch":
                    loc.tap(timeout=timeout)
                    detail["interaction"] = "touch"
                else:
                    loc.click(timeout=timeout)
                    detail["interaction"] = "mouse"
                return self._result(True, atype, t0, detail=detail)
            if atype == "press":
                key = action.get("key", "Enter")
                page.keyboard.press(key)
                if key in {"Tab", "Shift+Tab"}:
                    self._record_focus()
                return self._result(True, atype, t0, detail={"interaction": "keyboard"})
            if atype == "scroll":
                if self._keyboard_only:
                    page.keyboard.press("PageDown" if int(action.get("dy", 600)) >= 0 else "PageUp")
                else:
                    page.mouse.wheel(0, int(action.get("dy", 600)))
                return self._result(True, atype, t0)
            return self._result(False, atype, t0, error=f"action non supportée '{atype}'")
        except Exception as exc:
            # Les erreurs Playwright brutes peuvent inclure des URL avec données de formulaire.
            message = str(exc).splitlines()[0] if isinstance(exc, RuntimeError) else type(exc).__name__
            return self._result(False, atype, t0, error=message,
                                detail={"target": action.get("name") or action.get("selector_pref", "")})

    def _result(self, ok: bool, atype: str, t0: float, error: str = "",
                detail: dict | None = None) -> ActionResult:
        return ActionResult(ok=ok, action=atype, duration_ms=(time.perf_counter() - t0) * 1000,
                            error=error, detail=detail or {})

    def _inspect_page(self) -> None:
        url = public_url(self._page.url)
        if not url.startswith(("http://", "https://")) or self._crashed:
            return
        try:
            latest = self._page.evaluate("() => window.__testmyappVitals?.() || {}")
            for key, value in latest.items():
                self.vitals[f"{key}::{url}"] = float(value)
        except Exception:
            pass
        if url not in self._audits and url not in self._audit_errors:
            try:
                self._audits[url] = self._page.evaluate(self._axe_scan)
            except Exception as exc:
                self._audit_errors[url] = f"{url} · axe-core · {type(exc).__name__}"

    def _sync_observe(self) -> dict[str, Any]:
        self._inspect_page()
        violations = [{**item, "url": url}
                      for url, audit in self._audits.items() for item in audit["violations"]]
        review = [{**item, "url": url}
                  for url, audit in self._audits.items() for item in audit["review"]]
        review.extend({"rule": "keyboard-focus-visible", "selector": selector, "severity": "moderate",
                       "url": url, "source": "keyboard", "wcag": ["2.4.7"], "impact": item}
                      for (url, selector), item in self._focus_checks.items()
                      if not item["outline"] and not item["shadow"])
        return {"url": public_url(self._page.url), "console_errors": self.console_errors,
                "web_requests": self.requests, "vitals": dict(self.vitals),
                "a11y_violations": violations, "a11y_review": review,
                "a11y_pages": list(self._audits), "observation_errors": list(self._audit_errors.values()),
                "focus_path": [{**item, "url": key[0]} for key, item in self._focus_checks.items()]}

    def _sync_snapshot(self) -> PageState:
        page = self._page
        for attempt in range(3):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                if self.target_cfg.get("free_discovery"):
                    elements = page.locator(DISCOVERY_SELECTOR).evaluate_all(DISCOVERY_JS)
                else:
                    elements = page.locator("a[href], button, input:not([type=hidden]), [role]").evaluate_all(SNAPSHOT_JS)
                return PageState(url=page.url, title=page.title(), elements=elements,
                                 console_errors=len(self.console_errors))
            except Exception as exc:
                if attempt == 2 or "navigation" not in str(exc):
                    raise

    def _sync_screenshot(self, name: str) -> Path | None:
        shots = self.run_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        path = shots / f"{name}.png"
        try:
            self._page.screenshot(path=str(path), full_page=False)
            return path
        except Exception:
            return None

    # -- interface asyncio (toujours le même thread, cf. Playwright sync)
    async def _call(self, fn, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, lambda: fn(*args))

    async def launch(self) -> None:
        await self._call(self._sync_launch)

    async def perform(self, action: dict[str, Any]) -> ActionResult:
        return await self._call(self._sync_perform, action)

    async def observe(self) -> dict[str, Any]:
        return await self._call(self._sync_observe)

    async def snapshot(self) -> PageState:
        return await self._call(self._sync_snapshot)

    async def read_json(self, endpoint: str, timeout_ms: int) -> dict:
        if (not endpoint.startswith("/") or endpoint.startswith("//") or "\\" in endpoint
                or any(ord(char) < 32 for char in endpoint)):
            raise ValueError("Le contrôle doit viser un chemin de la cible Web.")

        def read():
            response = self._context.request.get(urljoin(self._base_url, endpoint),
                                                 timeout=timeout_ms, max_redirects=0)
            try:
                if not response.ok:
                    raise RuntimeError(f"Réponse HTTP {response.status}")
                data = response.json()
                return data if isinstance(data, dict) else {}
            finally:
                response.dispose()

        return await self._call(read)

    async def health(self) -> dict[str, Any]:
        crashed = await self._call(lambda: self._crashed or bool(self._browser and not self._browser.is_connected()))
        return {"crashed": crashed}

    async def screenshot(self, name: str) -> Path | None:
        return await self._call(self._sync_screenshot, name)

    async def stop(self) -> None:
        def _stop() -> None:
            try:
                if self._trace_started:
                    trace_dir = self.run_dir / "traces"
                    trace_dir.mkdir(parents=True, exist_ok=True)
                    prefix = self.target_cfg.get("artifact_prefix", self.session_id)
                    self._context.tracing.stop(path=trace_dir / f"{prefix}.zip")
                    self._trace_started = False
            finally:
                for closer in (self._context, self._browser):
                    try:
                        if closer:
                            closer.close()
                    except Exception:
                        pass
                if self._pw:
                    self._pw.stop()

        try:
            await self._call(_stop)
        finally:
            self._pool.shutdown(wait=False)
