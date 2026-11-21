"""Pilote Windows UI Automation avec surveillance indépendante de la fenêtre."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from testmyapp.drivers.base import ActionResult, Driver, PageState
from testmyapp.drivers.image_match import ImageTemplate

VISUAL_ACTIONS = {"click", "invoke", "type", "set_value", "wait_for"}
CONTROL_ACTIONS = VISUAL_ACTIONS | {"focus", "select"}
WINDOW_STARTUP_TIMEOUT = 30.0


def desktop_command(executable: str, args: list[str]) -> tuple[str, list[str]]:
    legacy_demo = "{demo.windows_script}"
    if legacy_demo in args:
        # Les projets déjà initialisés passent encore le marqueur après les options PowerShell.
        executable = "{demo.windows_executable}"
        args = args[args.index(legacy_demo) + 1:]
    if executable == "{demo.windows_executable}":
        executable = str(files("testmyapp") / "assets" / "desktop" / "RecetteWindows.exe")
    return executable, args


class DesktopDriver(Driver):
    def __init__(self, target_cfg: dict[str, Any], persona_cfg: dict[str, Any],
                 run_dir: Path, session_id: str) -> None:
        super().__init__(target_cfg, persona_cfg, run_dir, session_id)
        self._uia = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"uia-{session_id}")
        self._app = self._window = self._proc = self._child = None
        self._handle: int | None = None
        self._monitor: asyncio.Task | None = None
        self._expect_exit = False
        self._crashed = False
        self._exit_code = None
        self._startup_ms = None
        self._freeze_since: float | None = None
        self._freezes: list[int] = []
        self._samples: list[dict] = []
        self._action_times: list[float] = []
        self._process_samples: dict[int, Any] = {}
        self._templates: dict[str, ImageTemplate] = {}
        self._discovery_dir = None
        self._discovery_file = None

    async def _call(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._uia, fn, *args)

    def _sync_launch(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Le pilote UI Automation nécessite Windows")
        try:
            import psutil
            import win32gui
            import win32process
            from pywintypes import error as Win32Error
            from pywinauto.application import Application
        except ImportError as exc:
            raise RuntimeError('Installez les prérequis avec pip install "testmyapp[desktop]"') from exc

        self._templates = {name: ImageTemplate(spec) for name, spec in self.target_cfg.get("templates", {}).items()}
        if self._templates:
            import ctypes

            # Capture et clic utilisent les mêmes pixels physiques sur les écrans à DPI élevé.
            dpi_context = getattr(ctypes.windll.user32, "SetThreadDpiAwarenessContext", None)
            if dpi_context is not None:
                dpi_context.argtypes = [ctypes.c_void_p]
                dpi_context.restype = ctypes.c_void_p
                dpi_context(ctypes.c_void_p(-4))
        executable, args = desktop_command(self.target_cfg["executable"], self.target_cfg.get("args", []))
        started = time.monotonic()
        env = None
        if self.target_cfg.get("discovery_bridge"):
            self._discovery_dir = TemporaryDirectory(prefix=".interface-", dir=self.run_dir)
            self._discovery_file = Path(self._discovery_dir.name).resolve() / "visible.json"
            env = {**os.environ, "TESTMYAPP_DISCOVERY_FILE": str(self._discovery_file)}
        self._child = subprocess.Popen([executable, *args], creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        self._proc = psutil.Process(self._child.pid)
        title = self.target_cfg.get("window_title", "")

        def inspect_window(hwnd, matches):
            try:
                if (win32process.GetWindowThreadProcessId(hwnd)[1] == self._child.pid
                        and win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd)
                        and (not title or re.search(title, win32gui.GetWindowText(hwnd)))):
                    matches.append(hwnd)
            except Win32Error:
                pass  # A transient window can close during enumeration.
            return True

        def find_windows():
            handles = []
            win32gui.EnumWindows(inspect_window, handles)
            return handles

        self._wait_for_startup(find_windows, started)

        try:
            self._app = Application(backend="uia").connect(handle=self._handle, timeout=2)
            self._window = self._app.window(handle=self._handle)
        except Exception:
            if not self._templates:
                raise

    def _wait_for_startup(self, find_windows, started: float) -> None:
        reason = "aucune fenêtre principale visible"
        deadline = started + WINDOW_STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if (exit_code := self._child.poll()) is not None:
                raise RuntimeError(f"la cible a quitté au démarrage (code {exit_code})")
            handles = find_windows()
            if handles:
                reason = "la fenêtre principale ne répondait pas"
            for handle in handles:
                self._handle = handle
                if not self._window_responsive():
                    continue
                if self._discovery_file and not self._sync_snapshot().elements:
                    reason = "la fenêtre répondait, ses commandes n’étaient pas encore disponibles"
                    continue
                self._startup_ms = (time.monotonic() - started) * 1000
                return
            time.sleep(max(0, min(.1, deadline - time.monotonic())))
        self._handle = None
        raise TimeoutError(f"{reason} après {WINDOW_STARTUP_TIMEOUT:g} s")

    def _client_capture(self):
        import win32gui
        from PIL import ImageGrab

        if self._child.poll() is not None or win32gui.GetForegroundWindow() != self._handle:
            raise RuntimeError("La fenêtre cible doit rester visible et au premier plan")
        _, _, width, height = win32gui.GetClientRect(self._handle)
        x, y = win32gui.ClientToScreen(self._handle, (0, 0))
        return ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True), (x, y)

    def _ensure_foreground(self, deadline: float) -> None:
        import win32con
        import win32gui
        from pywintypes import error as Win32Error
        from pywinauto import mouse

        hwnd = self._handle
        caption_clicked = False
        while (self._child.poll() is None and win32gui.IsWindow(hwnd)
               and win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd)):
            if win32gui.GetForegroundWindow() == hwnd:
                return
            if time.monotonic() >= deadline:
                break
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Win32Error:
                pass  # Windows may refuse a background process permission to activate a window.
            if win32gui.GetForegroundWindow() == hwnd:
                return

            # A normal click can activate the window. Only use the visible, draggable
            # caption of this window, never an application control or another window.
            try:
                left, top, right, _ = win32gui.GetWindowRect(hwnd)
                _, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
                if not caption_clicked and client_top > top:
                    point = ((left + right) // 2, (top + client_top) // 2)
                    x, y = point
                    if win32gui.GetAncestor(win32gui.WindowFromPoint(point), win32con.GA_ROOT) == hwnd:
                        packed = (x & 0xffff) | ((y & 0xffff) << 16)
                        responded, hit = win32gui.SendMessageTimeout(hwnd, win32con.WM_NCHITTEST, 0, packed,
                                                                   win32con.SMTO_ABORTIFHUNG, 100)
                        if (responded and hit == win32con.HTCAPTION
                                and win32gui.GetAncestor(win32gui.WindowFromPoint(point), win32con.GA_ROOT) == hwnd):
                            caption_clicked = True
                            mouse.click(coords=point)
            except Win32Error:
                pass
            time.sleep(max(0, min(.1, deadline - time.monotonic())))
        raise RuntimeError("Impossible de mettre la fenêtre cible au premier plan. Vérifiez qu’elle est visible et dégagée.")

    def _visual_action(self, kind: str, template_name: str, action: dict, deadline: float) -> dict:
        import win32gui
        from pywinauto import keyboard, mouse

        template = self._templates[template_name]
        while True:
            self._ensure_foreground(deadline)
            image, (left, top) = self._client_capture()
            match = template.locate(image)
            if match:
                x, y, width, height = match["bounds"]
                point = (left + x + width // 2, top + y + height // 2)
                if (win32gui.GetForegroundWindow() == self._handle
                        and win32gui.ClientToScreen(self._handle, (0, 0)) == (left, top)
                        and win32gui.GetClientRect(self._handle)[2:] == image.size
                        and win32gui.GetAncestor(win32gui.WindowFromPoint(point), 2) == self._handle):
                    if kind != "wait_for":
                        mouse.click(coords=point)
                    if kind in {"type", "set_value"}:
                        if win32gui.GetForegroundWindow() != self._handle:
                            raise RuntimeError("La cible a perdu le focus avant la saisie")
                        text = str(action.get("text", ""))
                        escaped = "".join("{" + c + "}" if c in "{}+^%~()" else c for c in text)
                        keyboard.send_keys("^a")
                        if escaped:
                            keyboard.send_keys(escaped, with_spaces=True, with_tabs=True, with_newlines=True)
                    detail = {"resolution": "image", "image_match": {"template": template_name, **match}}
                    if kind in {"click", "invoke"}:
                        detail["interaction"] = "mouse"
                    return detail
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Modèle « {template_name} » absent, ambigu ou masqué")
            time.sleep(min(.1, remaining))

    def _sync_perform(self, action: dict[str, Any]) -> ActionResult:
        started = time.perf_counter()
        kind = action.get("type", "")
        name = action.get("name") or action.get("selector")
        timeout = int(action.get("timeout_ms", 8000)) / 1000
        fallback = action.get("template", name)
        visual = kind in VISUAL_ACTIONS and fallback in self._templates
        deadline = time.monotonic() + timeout
        detail = {"resolution": "uia"}
        try:
            if kind in CONTROL_ACTIONS and (name or visual):
                control = None
                if name and self._window is not None:
                    try:
                        specification = self._window.child_window(title=name)
                        probe = min(.4, timeout) if visual else timeout
                        specification.wait("visible", timeout=probe)
                        control = specification.wrapper_object()
                    except Exception:
                        if not visual:
                            raise
                if control is None:
                    if not visual:
                        raise RuntimeError("Aucun contrôle UI Automation disponible")
                    if kind != "wait_for" and self.persona_cfg.get("motor") == "keyboard_only":
                        raise RuntimeError("Le contrôle repéré par image n’offre aucun accès clavier vérifiable")
                    detail = self._visual_action(kind, fallback, action, deadline)
                else:
                    # Un échec d'injection ne déclenche pas un deuxième clic.
                    if kind == "click" and self.persona_cfg.get("input_device") == "keyboard":
                        control.set_focus()
                        self._send_keys("{SPACE}", deadline)
                        detail["interaction"] = "keyboard"
                    elif kind in {"click", "invoke"}:
                        control.invoke() if hasattr(control, "invoke") else control.click()
                    elif kind in {"type", "set_value"}:
                        control.set_edit_text(str(action.get("text", "")))
                    elif kind == "focus":
                        control.set_focus()
                    elif kind == "select":
                        control.select(action.get("value"))
            elif kind in {"observed_click", "observed_type"}:
                detail = self._interact_with_observed_control(action, deadline)
                kind = "type" if kind == "observed_type" else "click"
            elif kind == "hotkey":
                self._send_keys(str(action.get("keys", "")), deadline)
                detail = {"interaction": "keyboard"}
            elif kind == "wait_for":
                self._window.wait("ready", timeout=timeout)
            elif kind == "close":
                self._expect_exit = True
                self._window.close()
            else:
                return ActionResult(False, kind, 0.0, error=f"action UIA incomplète ou inconnue : {kind}")
        except Exception as exc:
            kind = {"observed_click": "click", "observed_type": "type"}.get(kind, kind)
            return ActionResult(False, kind, (time.perf_counter() - started) * 1000,
                                error=str(exc) if isinstance(exc, RuntimeError) else f"Windows : {type(exc).__name__}")
        duration = (time.perf_counter() - started) * 1000
        if kind != "wait_for":
            self._action_times.append(duration)
        return ActionResult(True, kind, duration, detail=detail)

    def _send_keys(self, keys: str, deadline: float) -> None:
        import win32gui
        from pywinauto import keyboard

        self._ensure_foreground(deadline)
        if win32gui.GetForegroundWindow() != self._handle:
            raise RuntimeError("La fenêtre cible n’a pas le focus clavier")
        keyboard.send_keys(keys)

    def _window_responsive(self) -> bool:
        import ctypes
        from ctypes import wintypes

        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_ssize_t
        result = ctypes.c_size_t()
        return bool(send(self._handle, 0, 0, 0, 3, 150, ctypes.byref(result)))

    def _sample_process(self) -> dict:
        import psutil

        try:
            live = [self._proc, *self._proc.children(recursive=True)]
            self._process_samples = {p.pid: self._process_samples.get(p.pid, p) for p in live}
            processes = self._process_samples.values()
            return {"cpu_percent": round(sum(p.cpu_percent(interval=None) for p in processes), 1),
                    "rss_bytes": sum(p.memory_info().rss for p in processes)}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return {}

    async def _watch(self) -> None:
        last_sample = 0.0
        while True:
            self._exit_code = self._child.poll()
            if self._exit_code is not None:
                self._crashed = not self._expect_exit
                self._close_freeze()
                return
            now = time.monotonic()
            if await asyncio.to_thread(self._window_responsive):
                self._close_freeze()
            elif self._freeze_since is None:
                self._freeze_since = now
            if now - last_sample >= 1:
                sample = await asyncio.to_thread(self._sample_process)
                if sample:
                    self._samples.append(sample)
                last_sample = now
            await asyncio.sleep(.2)

    def _close_freeze(self) -> None:
        if self._freeze_since is None:
            return
        duration = round((time.monotonic() - self._freeze_since) * 1000)
        if duration >= 500:
            self._freezes.append(duration)
        self._freeze_since = None

    def _sync_snapshot(self) -> PageState:
        if self._discovery_file:
            try:
                elements = json.loads(self._discovery_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                elements = []
            return PageState(elements=elements)
        try:
            controls = self._window.descendants()[:100]
            elements = [{"name": ctrl.element_info.name or "", "role": ctrl.element_info.control_type,
                         "visible": ctrl.is_visible()} for ctrl in controls]
        except Exception:
            elements = []
        return PageState(title=self.target_cfg.get("window_title", ""), elements=elements)

    def _interact_with_observed_control(self, action: dict, deadline: float) -> dict:
        import win32con
        import win32gui
        from pywinauto import keyboard, mouse

        self._ensure_foreground(deadline)
        control = next((item for item in self._sync_snapshot().elements if item["id"] == action["control_id"]), None)
        if not control or control["role"] not in {"tab", "button", "checkbox", "node", "entry", "listitem"}:
            raise RuntimeError("Le contrôle observé n’est plus disponible")
        if control["role"] == "entry" and action["type"] != "observed_type":
            raise RuntimeError("Ce contrôle attend du texte")
        interaction = "mouse"
        if self.persona_cfg.get("motor") == "keyboard_only":
            if control["role"] not in {"entry", "button", "checkbox"}:
                raise RuntimeError("Accès à ce contrôle non vérifié au clavier")
            for _ in range(25):
                focused = next((item for item in self._sync_snapshot().elements if item["focused"]), None)
                if focused and focused["id"] == control["id"]:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Contrôle introuvable dans l’ordre de tabulation")
                self._send_keys("{TAB}", deadline)
                time.sleep(.2)
            else:
                raise RuntimeError("Contrôle introuvable dans l’ordre de tabulation")
            interaction = "keyboard"
            if action["type"] == "observed_click":
                self._send_keys("{SPACE}", deadline)
        else:
            point = tuple(control["point"])
            if (win32gui.GetForegroundWindow() != self._handle
                    or win32gui.GetAncestor(win32gui.WindowFromPoint(point), win32con.GA_ROOT) != self._handle):
                raise RuntimeError("Le contrôle observé est masqué")
            mouse.click(coords=point)
        if action["type"] == "observed_type":
            text = action["text"]
            escaped = "".join("{" + c + "}" if c in "{}+^%~()" else c for c in text)
            if win32gui.GetForegroundWindow() != self._handle:
                raise RuntimeError("La cible a perdu le focus avant la saisie")
            keyboard.send_keys("^a" + escaped, with_spaces=True)
            interaction = "keyboard"
        return {"resolution": "observed", "interaction": interaction}

    def _sync_screenshot(self, name: str) -> Path | None:
        if self._crashed or self._freeze_since is not None:
            return None
        path = self.run_dir / "screenshots" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._window.capture_as_image().save(path)
            return path
        except Exception:
            if self._templates:
                try:
                    self._client_capture()[0].save(path)
                    return path
                except Exception:
                    pass
        return None

    async def launch(self) -> None:
        await self._call(self._sync_launch)
        self._monitor = asyncio.create_task(self._watch())

    async def perform(self, action: dict[str, Any]) -> ActionResult:
        if action.get("type") == "wait":
            delay_ms = min(15000, max(0, int(action.get("duration_ms", 1000))))
            await asyncio.sleep(delay_ms / 1000)
            return ActionResult(True, "wait", delay_ms)
        if action.get("type") == "wait_for_exit":
            started = time.monotonic()
            timeout = min(15, max(.1, int(action.get("timeout_ms", 8000)) / 1000))
            try:
                self._exit_code = await asyncio.to_thread(self._child.wait, timeout)
            except subprocess.TimeoutExpired:
                return ActionResult(False, "wait_for_exit", (time.monotonic() - started) * 1000,
                                    error="le processus est toujours actif")
            self._crashed = not self._expect_exit
            return ActionResult(True, "wait_for_exit", (time.monotonic() - started) * 1000)
        return await self._call(self._sync_perform, action)

    async def observe(self) -> dict[str, Any]:
        import math

        vitals = {}
        if self._startup_ms is not None:
            vitals["desktop_startup_ms"] = self._startup_ms
        if self._action_times:
            times = sorted(self._action_times)
            vitals["action_p95_ms"] = times[math.ceil(.95 * len(times)) - 1]
        return {"vitals": vitals, "desktop_freezes": list(self._freezes),
                "desktop_samples": list(self._samples)}

    async def snapshot(self) -> PageState:
        return await self._call(self._sync_snapshot)

    async def screenshot(self, name: str) -> Path | None:
        return await self._call(self._sync_screenshot, name)

    async def health(self) -> dict[str, Any]:
        self._exit_code = self._child.poll()
        if self._exit_code is not None:
            self._crashed = not self._expect_exit
        return {"crashed": self._crashed, "exit_code": self._exit_code, "frozen": self._freeze_since is not None}

    async def stop(self) -> None:
        if self._monitor:
            self._monitor.cancel()
            await asyncio.gather(self._monitor, return_exceptions=True)
        try:
            if self._child and self._child.poll() is None:
                self._expect_exit = True
                self._child.terminate()
                await asyncio.to_thread(self._child.wait, 5)
        finally:
            self._uia.shutdown(wait=False)
            if self._discovery_dir:
                self._discovery_dir.cleanup()

