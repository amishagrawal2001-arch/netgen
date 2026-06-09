"""v0.5.48 — /api/dpdk/load_modules success path no longer falls
through to the bottom-of-loop failed_modules.append.

This is the ACTUAL root cause of the operator's srv06 complaint
that v0.5.46 only papered over.

Pre-v0.5.48 loop structure (paraphrased):

  for module in modules_to_load:
      try:
          result = subprocess.run([modprobe, module], ...)
          if result.returncode == 0:
              verify_result = subprocess.run([lsmod], ...)
              if verify_result.returncode == 0 and loaded_ok:
                  loaded_modules.append(module)
                  # ⚠ NO continue
              else:
                  failed_modules.append(...)  # ⚠ NO continue
          else:
              failed_modules.append(...)      # ⚠ NO continue
      except (FileNotFoundError, TimeoutExpired, Exception) as e:
          error_msg = str(e)
          # (no append in handlers — intentional)

      # sudo fallback
      if error_msg and module not in loaded_modules and not is_root:
          ...

      # ⚠ UNCONDITIONAL append at the end of the loop body
      failed_modules.append({
          "module": module,
          "error": error_msg or "Unknown error - check server logs"
      })

For a SUCCESSFUL load (most common path on srv06):
  loaded_modules: [vfio_pci]
  failed_modules: [{"module": "vfio_pci", "error": "Unknown error - check server logs"}]
                  ^^^ from the unconditional append at the bottom

failed_modules is non-empty → endpoint returns HTTP 500 →
operator sees "Failed to load modules: vfio-pci: Unknown error".
v0.5.46's journalctl scrape fix never fires because there's no
actual subprocess failure to scrape from — the error_msg
literally came from the bottom-append's hard-coded fallback
string.

v0.5.48 adds `continue` after every explicit append (success
AND failure paths) so the bottom-of-loop append only fires for
the exception handlers. Plus a defense-in-depth `module not in
loaded_modules and not already-in-failed_modules` guard at the
bottom append in case a future branch forgets the continue.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _load_modules_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def dpdk_load_modules\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    assert m, "dpdk_load_modules body not located"
    return m.group(0)


def test_success_path_has_continue_after_loaded_modules_append():
    """Success path: `loaded_modules.append(module)` must be
    followed by a `continue` to skip past the bottom-of-loop
    unconditional `failed_modules.append`."""
    body = _load_modules_body()
    # Locate the success branch: the `if loaded_ok:` block.
    m = re.search(
        r"if\s+loaded_ok:\s*\n\s+loaded_modules\.append\(module\)"
        r"[\s\S]+?continue",
        body,
    )
    assert m, (
        "Success path doesn't `continue` after appending to "
        "loaded_modules — control falls through to the bottom-of-"
        "loop unconditional failed_modules.append, double-recording "
        "every successful load as a failure with 'Unknown error'."
    )


def test_verify_failed_path_has_continue():
    """Verify-failed path: when modprobe rc=0 but lsmod doesn't
    show the module (kernel 6.8+ split, vfio_pci_core vs vfio_pci),
    we append to failed_modules with a specific diagnostic. Must
    also continue to avoid the bottom-append double-record."""
    body = _load_modules_body()
    # Locate the `else:` of `if loaded_ok` — first append + continue.
    m = re.search(
        r"if\s+loaded_ok:[\s\S]+?else:\s*\n[\s\S]+?"
        r"failed_modules\.append\(\{[\s\S]+?\}\)\s*\n\s+continue",
        body,
    )
    assert m, (
        "Verify-failed path doesn't `continue` after failed_modules."
        "append — control falls through to bottom-of-loop append, "
        "double-recording the same module."
    )


def test_modprobe_failed_path_has_continue():
    """modprobe rc != 0 path: explicit append (with journalctl
    diagnostic from v0.5.46) must continue to avoid double-record
    at the bottom-of-loop append."""
    body = _load_modules_body()
    # The modprobe-rc-nonzero else branch ends with the
    # subprocess-error append followed by continue, then the
    # next-iteration `except FileNotFoundError`. The continue may
    # have an inline comment after it, so allow arbitrary chars
    # between it and the except.
    m = re.search(
        r"failed_modules\.append\(\{[\s\S]+?\}\)[\s\S]{0,200}?"
        r"\bcontinue\b[\s\S]{0,200}?except FileNotFoundError",
        body,
    )
    assert m, (
        "modprobe-failed path doesn't `continue` before the "
        "except handlers — bottom-of-loop append would double-record."
    )


def test_bottom_of_loop_append_has_dedupe_guard():
    """Defense in depth: the bottom-of-loop unconditional append
    must check that the module isn't already in loaded_modules
    AND isn't already in failed_modules. This protects against
    future branches that forget to `continue`."""
    body = _load_modules_body()
    # Find the "# If we get here, loading failed" block and its
    # subsequent append.
    m = re.search(
        r"#\s*If we get here[\s\S]+?"
        r"module\s+not\s+in\s+loaded_modules[\s\S]+?"
        r"failed_modules\.append",
        body,
    )
    assert m, (
        "Bottom-of-loop append doesn't guard against duplicates. "
        "If a future branch forgets to `continue`, every success "
        "becomes a 'Unknown error' failure all over again."
    )


def test_unknown_error_literal_still_present_as_last_resort():
    """Keep the literal `Unknown error - check server logs` as a
    last-resort fallback in the bottom-of-loop append — when the
    exception handlers set no error_msg at all (shouldn't happen
    but defense in depth), at least the operator gets a hint."""
    body = _load_modules_body()
    assert "Unknown error - check server logs" in body, (
        "Last-resort 'Unknown error' literal removed from bottom "
        "append — could leave error_msg=None in the response on "
        "an unexpected exception path."
    )


def test_resolve_dpdk_bind_script_helper_exists():
    """v0.5.48: helper that probes /opt/netgen/ then /opt/OSTG/
    for dpdk_bind.sh. Pre-fix, four endpoints hardcoded the
    legacy /opt/OSTG path — on a clean netgen-only install
    (no compat symlink), all four returned 404 or silently
    dropped interfaces."""
    src = _SERVER.read_text()
    assert "def _resolve_dpdk_bind_script(" in src, (
        "Helper _resolve_dpdk_bind_script() missing — endpoints "
        "would still hardcode /opt/OSTG/."
    )
    # Helper must probe /opt/netgen/ FIRST. Inspect ONLY the
    # `candidates = [...]` list literal — the docstring above it
    # references /opt/OSTG/ in the historical narration ("the
    # legacy OSTG path") and would otherwise come first.
    cand_m = re.search(
        r"def _resolve_dpdk_bind_script\(\)[\s\S]+?candidates\s*=\s*\[([\s\S]+?)\]",
        src,
    )
    assert cand_m, "candidates list literal not located in helper"
    cand_block = cand_m.group(1)
    netgen_idx = cand_block.find("/opt/netgen/resources/dpdk/dpdk_bind.sh")
    ostg_idx = cand_block.find("/opt/OSTG/resources/dpdk/dpdk_bind.sh")
    assert netgen_idx >= 0 and ostg_idx >= 0, (
        "Helper candidates list missing one of the two paths"
    )
    assert netgen_idx < ostg_idx, (
        "candidates list orders /opt/OSTG/ BEFORE /opt/netgen/ — "
        "wheel install would still hit the legacy path first."
    )


def test_endpoints_use_resolver_not_hardcoded_path():
    """The four endpoints (bind/unbind/status/interfaces) must
    call _resolve_dpdk_bind_script() instead of hardcoding the
    /opt/OSTG path."""
    src = _SERVER.read_text()
    # No remaining literal `dpdk_bind_script = "/opt/OSTG/..."` in
    # the file (apart from inside the helper's `candidates` list,
    # which uses a different assignment form).
    hardcoded = re.findall(
        r'dpdk_bind_script\s*=\s*[\"\']/opt/OSTG/',
        src,
    )
    assert not hardcoded, (
        f"{len(hardcoded)} endpoint(s) still hardcode "
        f"dpdk_bind_script = '/opt/OSTG/...' — would 404 on "
        f"clean netgen-only installs."
    )
    # And at least 4 callers of the resolver (one per endpoint).
    callers = re.findall(r"_resolve_dpdk_bind_script\(\)", src)
    # 4 endpoints (interfaces, bind, unbind, status) + 1 inside
    # the helper definition's docstring reference doesn't count.
    # Allow 4+ callers.
    assert len(callers) >= 4, (
        f"Only {len(callers)} call(s) to _resolve_dpdk_bind_script "
        f"— expected at least 4 (one per dpdk endpoint)."
    )


def test_pyproject_version_at_least_0548():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 48), (
        f"Version {m.group(1)} < 0.5.48"
    )
