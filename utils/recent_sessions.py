"""Recent-sessions list — File → Recent Sessions submenu backing
store (v0.3.11).

Why a separate file: ``session.json`` is the WORKING session
(devices / streams / TG state). The recent-sessions list is META —
"which session files has the operator opened lately." Mixing the two
would mean the list grows inside every saved snapshot, which is
confusing (a snapshot from yesterday would have a stale list) and
breaks Save-As semantics (the snapshot under name X knows about
unrelated names Y/Z).

So this module owns ``recent_sessions.json`` next to
``session.json`` in the OSTG data dir:

  {
    "paths": [
      "/Users/me/sessions/baseline-evpn.json",
      "/Users/me/sessions/stress-1500.json",
      ...
    ],
    "max": 5
  }

Most-recent-first ordering. Dedup on insert. ``MAX_RECENT`` caps the
list so the submenu doesn't grow unbounded. Read/write are
defensive — corruption returns an empty list rather than raising,
because the recent-list is convenience-only and must never block the
File menu from opening.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List

logger = logging.getLogger(__name__)


MAX_RECENT = 5


def _recent_file_path() -> str:
    """Resolve the recent-sessions JSON path. Lives next to
    session.json in the OSTG data dir so a clean uninstall takes both
    out together."""
    from utils.path_utils import get_ostg_data_directory
    return os.path.join(get_ostg_data_directory(), "recent_sessions.json")


def load_recent() -> List[str]:
    """Read the recent-sessions list.

    Returns ``[]`` on any error (missing file / parse failure /
    permission denied) — the menu must always open even if the list
    is unrecoverable. Filters out paths whose files no longer exist
    so the submenu doesn't offer dead links.
    """
    path = _recent_file_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.debug(f"[RECENT SESSIONS] load failed, returning empty: {exc}")
        return []
    raw = data.get("paths", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        return []
    # Drop non-strings (defensive against hand-edits) and dead paths
    # (operator moved / deleted the file since last open).
    cleaned: List[str] = []
    for p in raw:
        if not isinstance(p, str):
            continue
        if not p.strip():
            continue
        if not os.path.exists(p):
            continue
        cleaned.append(p)
    return cleaned[:MAX_RECENT]


def add_recent(session_path: str) -> List[str]:
    """Insert ``session_path`` at the front of the recent list,
    deduplicating any prior occurrence. Returns the new list.

    Silently no-ops on falsy / non-string input — call sites pass
    whatever the file picker returned, which can be ``""`` if the
    user cancels.
    """
    if not session_path or not isinstance(session_path, str):
        return load_recent()
    session_path = os.path.abspath(session_path)
    current = load_recent()
    # Dedup — preserve insertion order otherwise.
    deduped = [p for p in current if os.path.abspath(p) != session_path]
    new_list = [session_path] + deduped
    new_list = new_list[:MAX_RECENT]

    # Best-effort write — failure leaves the in-memory list usable
    # for this session, just doesn't persist. Logged so operators
    # debugging "why isn't my recent list sticky" find the cause.
    path = _recent_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"paths": new_list, "max": MAX_RECENT}, fh,
                      indent=2)
            fh.write("\n")
    except Exception as exc:
        logger.warning(f"[RECENT SESSIONS] write failed: {exc}")
    return new_list


def clear_recent() -> None:
    """Reset the list — used by the optional 'Clear Recent' menu
    action. Best-effort; failure is logged, not raised."""
    path = _recent_file_path()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"paths": [], "max": MAX_RECENT}, fh, indent=2)
            fh.write("\n")
    except Exception as exc:
        logger.warning(f"[RECENT SESSIONS] clear failed: {exc}")
