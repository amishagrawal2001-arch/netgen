"""Canonical interface naming — the single source of truth for the
``"TG N - <portname>"`` key format (v0.3.11).

The GUI uses interface keys in two very different roles:

  * **Display + tree-selection format** — what the user sees in the
    server tree, in tab filters, in dialog defaults: ``"TG 1 - ens5"``
  * **Dictionary key** — used to bucket devices under
    ``self.all_devices[iface]``, to match selected ports against
    streams, to filter the device / BGP / OSPF / IS-IS / DHCP / VXLAN
    tables.

Pre-v0.3.11 these two roles drifted apart at every populate site:

  * ``prompt_add_device`` synthesized the canonical form correctly.
  * ``reload_devices_from_server`` only synthesized when the server
    returned an empty Interface — a server returning a malformed
    ``" - ens5np0"`` (leading separator with no TG prefix) flowed
    through verbatim, was bucketed under the malformed key, and
    became invisible to every table lookup that uses the canonical
    format.
  * ``save_session`` persisted whatever key the in-memory dict
    happened to hold; ``load_session`` trusted it.

This module centralizes the rules so a refactor or a new server
shape can't drift again:

  * ``canonical_iface_key(iface, tg_id=...)`` — given any reasonable
    input (``ens5np0``, ``" - ens5np0"``, ``"Port: ens5np0"``,
    ``"TG 1 - ens5np0"``), returns the canonical ``"TG 1 - ens5np0"``
    form. Returns the original input only when there's not enough
    context to normalize.
  * ``looks_canonical(iface)`` — fast check; True iff the string
    matches ``r"^TG \d+ - .+"``.
"""

from __future__ import annotations

import re
from typing import Optional


_TG_PREFIX_RE = re.compile(r"^TG\s+\d+\s+-\s+.+")


def looks_canonical(iface: str) -> bool:
    r"""True iff `iface` already has the canonical ``"TG N - "`` prefix.

    Matches the regex ``r"^TG\s+\d+\s+-\s+.+"`` — TG, whitespace,
    digits, whitespace, dash, whitespace, at least one non-empty
    suffix character. Anything else (bare port name, leading dash,
    Port:-prefixed) returns False.
    """
    if not iface or not isinstance(iface, str):
        return False
    return bool(_TG_PREFIX_RE.match(iface))


def _strip_known_prefixes(iface: str) -> str:
    """Strip the historical noise prefixes seen in the wild — leading
    ``" - "``, ``"- "``, ``"Port: "``, ``" - Port: "``, ``"• "``
    (server tree bullet). Idempotent; safe to call on already-clean
    input.

    Intentionally does NOT call ``.strip()`` up front — the leading
    space in ``" - ens5np0"`` is *part of* the malformed prefix we
    need to recognize. We trim whitespace only at the end, after
    prefix removal.
    """
    s = iface or ""
    # Apply each prefix only once but iterate so combinations strip
    # cleanly (e.g. " - Port: ens5" → "ens5"). Order matters here —
    # the longest prefix that still applies must be tried first so
    # `" - Port: x"` doesn't get short-circuited by the shorter
    # `" - "` rule (which would leave a dangling `Port: x`).
    changed = True
    while changed:
        changed = False
        for prefix in (" - Port: ", "- Port: ", " - ", "- ", "Port: ", "• "):
            if s.startswith(prefix):
                s = s[len(prefix):]
                changed = True
                break
    return s.strip()


def canonical_iface_key(
    iface: Optional[str],
    *,
    tg_id: Optional[object] = None,
) -> str:
    """Return the canonical ``"TG N - <portname>"`` form.

    Decision tree:
      * Empty / None → ``""`` (caller must handle).
      * Already canonical → returned unchanged.
      * Malformed (leading ``" - "``, ``"Port: "``, bare port name)
        and `tg_id` is supplied → strip noise prefixes and prepend
        ``"TG <tg_id> - "``.
      * Malformed but `tg_id` is None → strip prefixes and return
        the bare port name. The caller has lost the TG context, so
        the result still isn't canonical but it's at least clean
        (an upstream repair pass can match it against the server
        list).

    :param iface: the raw interface string (from a server response,
        session file, dialog input, server-tree text).
    :param tg_id: the TG id integer or string. Accepts ``0``,
        ``"0"``, etc. If a string already prefixed with ``"TG "``
        is passed, the ``"TG "`` is stripped first so we don't
        end up with ``"TG TG 0 - ens5"``.
    """
    if iface is None:
        return ""
    if not isinstance(iface, str):
        return ""
    if looks_canonical(iface):
        return iface
    bare = _strip_known_prefixes(iface)
    if not bare:
        return ""
    if tg_id is None:
        # Caller has no TG context — return the cleaned bare name.
        # An upstream repair (load_session's suffix-match pass) can
        # still match this against the server interface list.
        return bare
    tg_str = str(tg_id).strip()
    # Idempotent: if caller already passed "TG 0", normalize to "0"
    # so we don't double-prefix.
    if tg_str.upper().startswith("TG "):
        tg_str = tg_str[3:].strip()
    return f"TG {tg_str} - {bare}"
