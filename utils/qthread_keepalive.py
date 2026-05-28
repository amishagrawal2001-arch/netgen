"""Process-global QThread keepalive registry.

Why this exists
---------------
On PyQt5 5.15.11 + Python 3.14 (the client's runtime), a QThread whose
last Python reference is dropped *during the window between ``run()``
returning and Qt's internal QThreadPrivate teardown completing* gets its
C++ object deleted by PyQt's wrapper destructor while Qt still thinks the
thread is running. Qt then aborts the process with:

    QThread: Destroyed while thread is still running

This bit the client repeatedly on startup (interface fetch, stream
auto-start, stats polling) and the codebase had already worked around an
earlier instance by *disabling* the devices-tab auto-refresh timer
(see widgets/devices_tab.py reload_devices_from_servers).

``QObject.setParent()`` fixes it **only when the worker and the intended
parent live on the same thread** — a QObject child must share its
parent's thread affinity. Workers spawned from a background
``threading.Thread`` (e.g. a monitor callback) can't be parented to the
main-thread window, so setParent silently no-ops and the race remains.

The bulletproof, thread-agnostic fix: keep a *process-global* strong
reference to every worker so Python never GCs the wrapper during the
race window. To bound the resulting leak, ``keep()`` trims workers that
finished more than ``_TRIM_AGE_S`` ago — long past the teardown window,
so their deletion is safe — and hands the C++ destruction to Qt via
``deleteLater()``.

Usage
-----
    from utils.qthread_keepalive import keep
    worker = MyWorker(...)
    keep(worker)          # <-- in place of setParent / deleteLater
    worker.start()
"""

import time as _time
import logging as _logging

_logger = _logging.getLogger(__name__)

# Strong refs to every live (or recently-finished) worker.
_KEEP = []

# Trim only when the registry grows past this — amortises the scan cost.
_TRIM_THRESHOLD = 40

# A worker is safe to delete once it's not running AND has been parked
# at least this long. The post-run() teardown completes in microseconds;
# 30 s is paranoia margin so we never delete inside the race window.
_TRIM_AGE_S = 30.0


def keep(worker):
    """Register ``worker`` so Python never GCs it during the QThread
    destructor race window. Returns ``worker`` for call chaining.

    Idempotent: registering the same worker twice (e.g. an explicit
    keep() call plus the QThread.start monkeypatch from install()) only
    records it once. Safe to call from any thread — the registry is a
    plain list and the GIL serialises the append. The optional trim only
    ``deleteLater()``s workers that finished long ago, which Qt processes
    on the main thread's event loop regardless of which thread scheduled
    it.
    """
    # Dedup by identity — cheap because the list is bounded by the trim.
    for w in _KEEP:
        if w is worker:
            return worker
    try:
        worker._kept_at = _time.monotonic()
    except Exception:
        # Exotic worker that doesn't allow attribute set — still keep it,
        # it just won't be eligible for the age-based trim (we treat
        # missing _kept_at as "just kept").
        pass
    _KEEP.append(worker)

    if len(_KEEP) > _TRIM_THRESHOLD:
        _trim()
    return worker


_INSTALLED = False


def install():
    """Monkeypatch ``QThread.start`` so EVERY QThread in the process
    auto-registers in the keepalive on start — no per-call-site changes
    needed. Call once, as early as possible at client startup, before
    any QThread is created.

    This is the comprehensive form of the fix: individual ``keep()``
    calls cover the sites we know about, but a single app can spawn
    QThreads from dozens of widgets/dialogs/monitors, and any one of
    them hitting the destructor race aborts the whole process. Patching
    ``start`` catches them all, including workers that don't even keep a
    local reference (``MyWorker().start()``).

    Idempotent and best-effort: if the PyQt build doesn't allow patching
    the method (it does on PyQt5 5.15.x), we log and continue — the
    explicit keep() call sites still protect the known hot paths.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        from PyQt5.QtCore import QThread
    except Exception as e:
        _logger.warning("[QTHREAD KEEPALIVE] PyQt5 import failed, not installing: %s", e)
        return False

    try:
        _orig_start = QThread.start

        def _keeping_start(self, *args, **kwargs):
            try:
                keep(self)
            except Exception:
                pass  # never let bookkeeping break a thread start
            return _orig_start(self, *args, **kwargs)

        QThread.start = _keeping_start
        _INSTALLED = True
        _logger.info("[QTHREAD KEEPALIVE] Installed QThread.start hook")
        return True
    except (TypeError, AttributeError) as e:
        # Some SIP builds disallow assigning to the type's method.
        _logger.warning(
            "[QTHREAD KEEPALIVE] Could not patch QThread.start (%s); "
            "relying on explicit keep() call sites instead", e
        )
        return False


def _trim():
    """Drop + deleteLater workers that finished > _TRIM_AGE_S ago."""
    now = _time.monotonic()
    survivors = []
    for w in _KEEP:
        try:
            running = w.isRunning()
        except RuntimeError:
            # C++ side already gone (someone else deleted it) — drop the
            # dead wrapper from the registry.
            continue
        except Exception:
            # Unknown state — keep it to be safe.
            survivors.append(w)
            continue

        kept_at = getattr(w, "_kept_at", now)
        if (not running) and (now - kept_at > _TRIM_AGE_S):
            try:
                w.deleteLater()
            except RuntimeError:
                pass  # already deleted
        else:
            survivors.append(w)

    dropped = len(_KEEP) - len(survivors)
    if dropped:
        _logger.debug("[QTHREAD KEEPALIVE] trimmed %d finished worker(s); %d live",
                      dropped, len(survivors))
    _KEEP[:] = survivors


def live_count():
    """Number of workers currently held (for diagnostics/tests)."""
    return len(_KEEP)
