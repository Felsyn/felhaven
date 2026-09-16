"""
kairos.py — Central Scheduler
==============================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own the clock. Fire workers. Dispatch results to panels.

No panel manages its own timing. Panels are dumb display surfaces.
Kairos owns the clock.

Thread-safety contract:
    result_queue is the only object shared between the worker threads and the
    main thread (§6). Workers write to it; _drain_queue (main thread) reads it
    and calls panel.update(). A worker's fetch() may run its own concurrency
    internally (e.g. Midas's ThreadPoolExecutor over Cerberus's lock-guarded
    ledger, §12) — but that never crosses into Kairos or a panel, so the
    one-queue contract holds.

Upstream:    felhaven.py — constructs Kairos, registers panels, calls
             start()/stop(), and wires refetch() to the Settings tab.
Downstream:  each WORKERS entry's fetch() (tools.*), resolved by dotted path at
             startup; each registered panel's update(data), called on the main
             thread.

Requires:    importlib, logging, queue, threading, time (stdlib only).

V0.02: Audited 2026-07-25 — verified honest (exhaustive coverage, no dead code);
       tightened the thread-safety contract to §6's precise worker↔main-thread
       wording (§12); added Upstream/Downstream/Requires.

2026-09-15: added the RETRY_DELAYS re-attempt ladder. A failed fetch used to
       wait out the worker's whole interval before trying again, so one blip
       pinned Aura's panel at "unavailable" for 30 minutes — long after the
       sky was fine. _note_outcome now backdates _last_run on failure to pull
       the next attempt forward. Main-thread only; the threading contract above
       is unchanged.
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
        ("artemis",     600, "tools.artemis.fetch"),
    ]

    # Re-attempt ladder after a failed fetch (seconds), in order. A worker that
    # fails gets its next attempt this soon instead of waiting out its whole
    # interval — a 30-minute worker like Aura otherwise shows "unavailable" for
    # a full half-hour after a blip that cleared in seconds.
    #
    # The ladder is SHORT on purpose, and deliberately runs out: recovery from a
    # transient failure is caught within a minute, but a sustained outage falls
    # back to the worker's normal cadence instead of hammering a dead endpoint
    # forever (see the decisions log — wttr.in's 2026-09-15 cert expiry failed
    # every attempt for nine hours; polling that twice as often helps nobody).
    RETRY_DELAYS = (60, 300, 900)

    def __init__(self, root):
        self.root = root
        self.result_queue = queue.Queue()
        self._panels: dict[str, list] = {}  # name -> [panel, ...]
        self._last_run = {}         # name -> monotonic time of last dispatch (main thread only)
        self._running_threads = {}  # name -> Thread (pile-up guard)
        self._fetch_fns = {}        # name -> callable, resolved once at startup
        self._intervals = {}        # name -> interval seconds, for the retry ladder
        self._fail_counts = {}      # name -> consecutive failures (main thread only)
        for name, interval, dotted in self.WORKERS:
            module_path, _, attr = dotted.rpartition(".")
            module = importlib.import_module(module_path)
            self._fetch_fns[name] = getattr(module, attr)
            self._intervals[name] = interval

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
        # Validated against the instance's own resolved registry, not the
        # WORKERS class attribute: _intervals and _fetch_fns are what every
        # other method schedules from, and a name this instance cannot fire is
        # not a name it should mark overdue.
        for name in names:
            if name in self._intervals:
                self._last_run[name] = float("-inf")
                # A manual refetch is a fresh start: clear any retry ladder
                # progress so a worker that had been failing gets the full
                # short-delay ladder again rather than resuming mid-climb.
                self._fail_counts.pop(name, None)

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
            self._note_outcome(name, data)

    def _note_outcome(self, name: str, data) -> None:
        """Advance or clear a worker's retry ladder from its latest result.

        `data is None` is Kairos's own failure signal (_run_worker puts None
        when fetch() raised), which is why this reads the drained result rather
        than anything the worker thread set: _fail_counts and _last_run stay
        main-thread-only, so §6's one-shared-object contract is untouched.
        """
        interval = self._intervals.get(name)
        if interval is None:
            return                      # unregistered name — nothing to schedule
        if data is not None:
            self._fail_counts.pop(name, None)
            return
        fails = self._fail_counts.get(name, 0) + 1
        self._fail_counts[name] = fails
        if fails > len(self.RETRY_DELAYS):
            return                      # ladder spent — resume the normal cadence
        delay = self.RETRY_DELAYS[fails - 1]
        if delay >= interval:
            return                      # already polls this often; nothing to gain
        # Backdate the last run so the next tick fires it `delay` from now.
        self._last_run[name] = time.monotonic() - interval + delay
        log.info(f"{name} failed ({fails}) — retrying in {delay}s "
                 f"instead of {interval}s")

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


if __name__ == "__main__":
    # Read-only registry probe: __init__ never touches root (only start/stop/
    # _tick do), so None is safe. Resolves all WORKERS dotted paths and prints
    # them — a CLI complement to test_kairos's Group A registry-integrity tests.
    k = Kairos(None)
    print(f"[Kairos] {len(k._fetch_fns)} workers resolve: "
          f"{', '.join(k._fetch_fns)}")
