"""v0.5.256 — netgen-install shim shell+Python polyglot at tarball
build time so `bin/netgen-install` runs from any extract path.

Pre-fix the tarball's build step 5 rewrote the shebang to a
hardcoded absolute path `#!/opt/netgen-server/netgen-venv/bin/
python`. Operators who extracted to /tmp saw `No such file or
directory` at exec, with no clue about where to look.

Fix: build step now writes a shell+Python polyglot at the top of
the shim so it finds the bundled Python via relative path:

    #!/bin/sh
    "exec" "$(cd "$(dirname "$0")" && pwd)/../netgen-venv/bin/python3" "$0" "$@"

The Python source keeps `#!/usr/bin/env python3` for dev
convenience; only the tarball copy gets the polyglot.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = (REPO / ".github" / "workflows" / "build-server-tarball.yml").read_text()
SHIM_SRC = (REPO / "scripts" / "tarball" / "netgen-install").read_text()


# --- workflow polyglot pattern -------------------------------------


def test_build_step_writes_polyglot_not_absolute_shebang():
    """The build step must NOT sed-in the old absolute shebang."""
    # Old fragile pattern is gone.
    assert "sed -i '1s|.*|#!/opt/netgen-server/netgen-venv/bin/python|' \"$dst\"" not in WORKFLOW
    # New polyglot pattern is present.
    assert "#!/bin/sh" in WORKFLOW
    # The exec line is the key uniqueness marker.
    assert '"exec" "$(cd "$(dirname "$0")" && pwd)/../netgen-venv/bin/python3" "$0" "$@"' in WORKFLOW


def test_build_step_documents_polyglot_rationale():
    """Comment block explaining WHY the polyglot exists — so the
    next refactor doesn't 'simplify' back to the fragile absolute
    shebang."""
    assert "v0.5.256 fix" in WORKFLOW
    assert "polyglot" in WORKFLOW.lower()
    # References the concrete failure mode.
    assert "No such file or directory" in WORKFLOW


def test_build_step_polyglot_written_to_all_three_shims():
    """netgen-install / netgen-upgrade / netgen-uninstall must ALL
    get the polyglot, not just netgen-install. The loop in the
    workflow iterates all three."""
    idx = WORKFLOW.find("v0.5.256 fix")
    assert idx > 0
    # The `for script in netgen-install netgen-upgrade netgen-uninstall`
    # loop is what actually applies the polyglot to all three; the
    # rewrite lives inside that loop.
    loop_idx = WORKFLOW.rfind("for script in netgen-install netgen-upgrade netgen-uninstall", 0, idx)
    assert loop_idx > 0
    # And the polyglot lines fall between the loop start and the
    # loop's `done` marker.
    done_idx = WORKFLOW.find("done", idx)
    assert done_idx > idx


# --- source-side keeps dev shebang, syntax stays valid Python ------


def test_source_shim_keeps_env_python3_shebang():
    """The source file (bin/netgen-install pre-tarball-build) keeps
    the `#!/usr/bin/env python3` shebang so dev + tests can run it
    directly — the polyglot is added ONLY at tarball-build time."""
    assert SHIM_SRC.startswith("#!/usr/bin/env python3\n"), (
        "Source shim shebang changed — dev / test invocations break "
        "if this isn't `#!/usr/bin/env python3`."
    )


def test_source_shim_still_parses_as_python():
    """The polyglot lines aren't in the source; source must still
    parse cleanly as Python."""
    import ast
    ast.parse(SHIM_SRC)  # raises SyntaxError on failure


# --- preflight non-canonical-install-root warning ------------------


def test_preflight_refuses_non_canonical_install_root():
    """The v0.5.256 preflight step must warn (and refuse without
    --force) when the install root isn't /opt/netgen-server. The
    systemd unit hard-codes the path so a /tmp/* install breaks on
    reboot."""
    idx = SHIM_SRC.find("v0.5.256")
    assert idx > 0
    body = SHIM_SRC[idx:idx + 3000]
    # Warning text mentions the canonical anchor.
    assert "canonical" in body.lower()
    # The refusal is gated behind --force so the warning can be
    # overridden for legitimate non-default installs.
    assert "if not force:" in body
    assert "sys.exit(" in body


def test_install_root_default_still_opt_netgen_server():
    """INSTALL_ROOT_DEFAULT is the anchor the preflight compares
    against. If this drifts, the preflight warning fires on
    everyone — regressing v0.5.256 into a self-inflicted denial
    of service."""
    m = re.search(r'INSTALL_ROOT_DEFAULT\s*=\s*Path\("([^"]+)"\)', SHIM_SRC)
    assert m and m.group(1) == "/opt/netgen-server"


# --- Version bumped ------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 256)
