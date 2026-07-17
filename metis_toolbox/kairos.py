"""
kairos.py — Central Scheduler
==============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own the clock. Fire workers. Dispatch results to panels.

No panel manages its own timing. Panels are dumb display surfaces.
Kairos owns the clock.

Thread-safety contract:
    result_queue is the only shared object between worker threads and panels.
    Workers write to it. _drain_queue (main thread) reads from it.
    No other cross-thread access.
"""

import importlib
import logging
import queue
import threading
import time

log = logging.getLogger("METIS.kairos")


class Kairos:
    TICK_MS = 500  # main-thread heartbeat

    # (worker_name, interval_seconds, fetch_fn_dotted_path)
    WORKERS = [
        ("aether",     3600, "tools.aether.fetch"),
        ("aura",       1800, "tools.aura.fetch"),
        ("pheme",       900, "tools.pheme.fetch"),
        ("midas",        60, "tools.midas.fetch"),
        ("hephaestus",    5, "tools.hephaestus.fetch"),
        ("argus",         5, "tools.argus.fetch"),
        ("horai",         1, "tools.horai.fetch"),
        ("emanon",        2, "tools.emanon.fetch"),
        ("morpheus",      2, "tools.morpheus.fetch"),
        ("orpheus",       2, "tools.orpheus.fetch"),
        ("hypatia",      60, "tools.hypatia.fetch"),
    ]

    def __init__(self, root):
        self.root = root
        self.result_queue = queue.Queue()
        self._panels: dict[str, list] = {}  # name -> [panel, ...]
        self._last_run = {}         # name -> monotonic time of last dispatch (main thread only)
        self._running_threads = {}  # name -> Thread (pile-up guard)
        self._fetch_fns = {}        # name -> callable, resolved once at startup
        for name, _interval, dotted in self.WORKERS:
            module_path, _, attr = dotted.rpartition(".")
            module = importlib.import_module(module_path)
            self._fetch_fns[name] = getattr(module, attr)

    def register_panel(self, name: str, panel) -> None:
        self._panels.setdefault(name, []).append(panel)

    def refetch(self, *names: str) -> None:
        """Force the named workers to fire on the next tick (<= TICK_MS) instead
        of waiting out their interval — used when a Settings edit changes what a
        worker fetches (e.g. Aura's location at a 1800 s interval, so weather,
        sky, planets and season all follow a location change promptly). Marks the
        worker infinitely overdue; the pile-up guard still applies. Main-thread
        only (called from a panel's Save handler), same as _schedule_workers, so
        no lock is needed. Unknown names are ignored."""
        known = {n for n, _interval, _dotted in self.WORKERS}
        for name in names:
            if name in known:
                self._last_run[name] = float("-inf")

    def start(self) -> None:
        now = time.monotonic()
        for name, interval, _ in self.WORKERS:
            self._last_run[name] = now - interval  # fires every worker on first tick
        self._tick()

    def stop(self) -> None:
        """Cancel the pending tick — prevents callbacks firing into a dead root."""
        if hasattr(self, "_after_id"):
            self.root.after_cancel(self._after_id)

    def _tick(self) -> None:
        self._drain_queue()
        self._schedule_workers()
        self._after_id = self.root.after(self.TICK_MS, self._tick)

    def _drain_queue(self) -> None:
        while True:
            try:
                name, data = self.result_queue.get_nowait()
            except queue.Empty:
                break
            for panel in self._panels.get(name, []):
                panel.update(data)

    def _schedule_workers(self) -> None:
        now = time.monotonic()
        for name, interval, _ in self.WORKERS:
            thread = self._running_threads.get(name)
            if thread is not None and thread.is_alive():
                continue
            if now - self._last_run.get(name, 0) >= interval:
                self._last_run[name] = now
                t = threading.Thread(
                    target=self._run_worker,
                    args=(name, self._fetch_fns[name]),
                    daemon=True,
                )
                self._running_threads[name] = t
                t.start()

    def _run_worker(self, name: str, fetch_fn) -> None:
        # Routine fire/success is DEBUG — heartbeat noise the file (at INFO)
        # drops, so Emanon only ever surfaces problems. Raise the root level
        # to DEBUG (setup_logging(..., logging.DEBUG)) to see the full trace.
        start = time.monotonic()
        log.debug(f"fired {name}")
        try:
            result = fetch_fn()
            elapsed = time.monotonic() - start
            log.debug(f"{name} ok ({elapsed:.2f}s)")
        except Exception as e:
            result = None
            elapsed = time.monotonic() - start
            log.error(f"{name} fetch failed after {elapsed:.2f}s: {type(e).__name__}: {e}")
        self.result_queue.put((name, result))
