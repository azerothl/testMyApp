"""Bus d'événements typés (spec §7.2, Annexe C).

Chaque événement est un dictionnaire JSON-sérialisable avec au minimum :
``ts`` (horodatage monotone->epoch), ``type``, ``session_id`` (ou ``None``
pour les événements de campagne). Garanties : ordre total *par session*
les consommateurs d'une session voient ses événements dans l'ordre
d'émission ; un consommateur ``recorder`` persiste tout en JSONL.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable


def now() -> float:
    return time.time()


class EventBus:
    """Pub/sub in-process, consommateurs = files d'attente + callbacks."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []
        self._callbacks: list[Callable[[dict], None]] = []
        self._recorder: Callable[[dict], None] | None = None
        self._per_session: dict[str, list[dict]] = defaultdict(list)
        self._all: list[dict] = []
        self._seq = 0

    def emit(self, etype: str, session_id: str | None = None, **fields: Any) -> dict:
        event = {"seq": self._seq, "ts": now(), "type": etype, "session_id": session_id, **fields}
        if self._recorder:
            self._recorder(event)
        self._seq += 1
        self._store(event)
        for q in self._queues:
            if q.full():
                q.get_nowait()
                q.task_done()
            q.put_nowait(event)
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:  # un consommateur défaillant n'arrête pas le bus
                pass
        return event

    def restore(self, events: list[dict]) -> None:
        for event in events:
            self._store(event)
            self._seq = max(self._seq, event["seq"] + 1)

    def _store(self, event: dict) -> None:
        self._all.append(event)
        if event.get("session_id"):
            self._per_session[event["session_id"]].append(event)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def on(self, cb: Callable[[dict], None]) -> None:
        self._callbacks.append(cb)

    def events_of(self, session_id: str) -> list[dict]:
        return list(self._per_session.get(session_id, []))

    @property
    def all_events(self) -> list[dict]:
        return list(self._all)


class EventRecorder:
    """Écrit events.jsonl + metrics.jsonl dans le dossier du run (spec §13)."""

    def __init__(self, run_dir: Path, bus: EventBus, *, resume: bool = False) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.metrics_path = self.run_dir / "metrics.jsonl"
        if resume:
            for path in (self.events_path, self.metrics_path):
                _repair_tail(path)
        mode = "a" if resume else "x"
        self._fh_events = self.events_path.open(mode, encoding="utf-8")
        try:
            self._fh_metrics = self.metrics_path.open(mode, encoding="utf-8")
        except BaseException:
            self._fh_events.close()
            raise
        self._bus = bus
        bus._recorder = self._handle

    def _handle(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False) + "\n"
        self._fh_events.write(line)
        self._fh_events.flush()
        if event["type"].startswith("target.metric") or event["type"] == "metric.raw":
            self._fh_metrics.write(line)
            self._fh_metrics.flush()

    def close(self) -> None:
        self._bus._recorder = None
        self._fh_events.close()
        self._fh_metrics.close()


def _repair_tail(path: Path) -> None:
    """Sauvegarde à part la dernière ligne partielle avant de rouvrir le JSONL."""
    if not path.is_file() or not path.stat().st_size:
        return
    with path.open("r+b") as handle:
        handle.seek(-1, 2)
        if handle.read(1) == b"\n":
            return
        handle.seek(0)
        content = handle.read()
        offset = content.rfind(b"\n") + 1
        try:
            json.loads(content[offset:])
        except (json.JSONDecodeError, UnicodeDecodeError):
            with path.with_suffix(".partial").open("ab") as backup:
                backup.write(content[offset:] + b"\n")
            handle.truncate(offset)
        else:
            handle.write(b"\n")


def read_run_events(run_dir: Path) -> list[dict]:
    """Fusionne le journal et les métriques des anciens runs, par numéro d'événement."""
    by_seq: dict[int, dict] = {}
    for name in ("metrics.jsonl", "events.jsonl"):
        path = run_dir / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and type(event.get("seq")) is int and "type" in event:
                by_seq[event["seq"]] = event
    return [by_seq[seq] for seq in sorted(by_seq)]


@contextmanager
def lock_run(run_dir: Path):
    """Verrou libéré par l'OS si le processus est interrompu."""
    import os

    with (run_dir / ".lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BlockingIOError(f"Ce run est déjà ouvert : {run_dir}") from exc
        yield


async def record_stream(bus: EventBus) -> AsyncIterator[dict]:
    """Aide pour les consommateurs async (GUI)."""
    q = bus.subscribe()
    try:
        while True:
            yield await q.get()
    finally:
        bus.unsubscribe(q)
