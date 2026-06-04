"""Regression tests for the apt-keyring gpg-dearmor fix.

Operator hit this during Fresh Install on a clean Ubuntu 24.04
(svl-d-ai-srv04):

    gpg: cannot open '/dev/tty': No such device or address
    Traceback (most recent call last):
      File "/tmp/netgen_install/install_ostg_complete.py", line 842, in install_docker
        self.run_command("curl -fsSL https://download.docker.com/linux/ubuntu/gpg
                         | gpg --dearmor -o /etc/apt/keyrings/docker.gpg")
    subprocess.CalledProcessError: ... returned non-zero exit status 2.

Root cause: modern GnuPG 2.x defaults to interactive mode and
opens ``/dev/tty`` for pinentry even on non-interactive ops like
``--dearmor``. The Fresh Install runs detached via nohup with no
controlling terminal → /dev/tty open fails → gpg exits 2 → entire
install path aborts at the docker step.

Secondary problem: the inline ``curl | gpg`` pipe returned gpg's
exit code, not curl's. A failed curl (network blip, 404) would
silently produce an empty .gpg file + the next apt-get update
would fail with NO_PUBKEY instead of pointing at the real cause.

Fix: new ``_install_apt_keyring(name, key_url)`` helper that:
  1. Downloads the key to a tmp file (curl failure surfaces)
  2. Dearmors with ``--batch --no-tty --yes`` (never touches /dev/tty)
  3. Sets 0644 perms (apt's expected)
  4. Cleans the tmp file

Both raw call sites converted (docker + influxdb keyrings)."""
from __future__ import annotations

import re


_INSTALLER_PATH = "/Users/surajsharma/dev/netgen/install_ostg_complete.py"


def _installer_src():
    return open(_INSTALLER_PATH).read()


def test_helper_method_exists():
    """``_install_apt_keyring`` must exist as a method on the
    installer. If it disappears, all keyring installs regress to the
    broken inline-pipe pattern."""
    src = _installer_src()
    assert re.search(
        r"def _install_apt_keyring\(self,\s+name",
        src,
    ), "_install_apt_keyring() helper missing"


def _helper_body():
    src = _installer_src()
    m = re.search(
        r"def _install_apt_keyring\(self,.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "_install_apt_keyring body not found"
    return m.group(0)


def test_helper_uses_batch_and_no_tty():
    """The gpg invocation in the helper MUST include both --batch
    and --no-tty so gpg never opens /dev/tty. Either flag alone
    is not enough on every GnuPG 2.x build."""
    body = _helper_body()
    assert "--batch" in body, (
        "_install_apt_keyring must use `gpg --batch` — without it "
        "gpg can still try to touch /dev/tty for pinentry"
    )
    assert "--no-tty" in body, (
        "_install_apt_keyring must use `gpg --no-tty` — without it "
        "gpg may still attempt /dev/tty access"
    )


def test_helper_downloads_to_tmp_then_dearmors():
    """The fix replaces the inline ``curl | gpg`` pipe with a
    download-then-dearmor sequence so curl failures surface cleanly.
    A pipe loses curl's exit code (shell pipelines return the LAST
    command's status by default), masking 404 / network errors."""
    body = _helper_body()
    # Strip the docstring out before checking — the docstring quotes
    # the pre-fix pattern to explain the bug, which would false-
    # positive the |gpg check below.
    body_no_doc = re.sub(r'"""[^"]*"""', "", body, flags=re.DOTALL)
    # Should download via curl -o tmp file, NOT pipe
    has_download_step = re.search(
        r"curl\s+-fsSL\s+\S+\s+-o\s+\S+",
        body_no_doc,
    ) is not None
    assert has_download_step, (
        "_install_apt_keyring must download the key to a tmp file "
        "(curl -fsSL <url> -o <tmp>), not pipe to gpg. The pipe "
        "swallows curl's exit code on network failures."
    )
    # Must NOT pipe curl | gpg in executable code
    assert "| gpg" not in body_no_doc, (
        "_install_apt_keyring must NOT pipe `curl | gpg` — that's "
        "the pre-fix pattern that swallowed curl failures."
    )


def test_helper_removes_stale_keyring_first():
    """Defensive: remove any pre-existing keyring file before
    dearmoring. Some older gpg builds still prompt on file-already-
    exists even with --yes if --batch is missing somewhere."""
    body = _helper_body()
    assert "rm -f" in body and ".gpg" in body, (
        "_install_apt_keyring should remove a stale keyring before "
        "writing the new one"
    )


def test_helper_chmods_to_0644():
    """apt expects keyring files to be world-readable (0644). Without
    this, _apt may fail to read the key when running as non-root in
    some sandboxed configurations."""
    body = _helper_body()
    assert "chmod 0644" in body, (
        "_install_apt_keyring should chmod the keyring to 0644 (apt's "
        "expected permission)"
    )


def test_no_bare_curl_gpg_dearmor_pipes_remain():
    """Every keyring install must go through the helper. A leftover
    raw ``curl ... | gpg --dearmor ...`` pipe in executable code is
    exactly the failure we're guarding against — operators on
    detached installs would still hit /dev/tty."""
    src = _installer_src()
    # Strip docstrings + comments
    code_lines = []
    in_docstring = False
    docstring_marker = None
    for line in src.split("\n"):
        stripped = line.strip()
        for marker in ('"""', "'''"):
            if stripped.count(marker) == 1:
                if not in_docstring:
                    in_docstring = True
                    docstring_marker = marker
                elif marker == docstring_marker:
                    in_docstring = False
                    docstring_marker = None
                continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        if "#" in line:
            code_lines.append(line.split("#", 1)[0])
        else:
            code_lines.append(line)
    code_only = "\n".join(code_lines)
    # Now check: no bare `gpg --dearmor` in executable code
    bare_pipe = re.search(
        r"\|\s*gpg\s+--dearmor",
        code_only,
    )
    assert bare_pipe is None, (
        f"Bare `| gpg --dearmor` pipe found in executable code — "
        f"this re-introduces the /dev/tty failure. Use "
        f"_install_apt_keyring() instead. Match: {bare_pipe.group(0)!r}"
    )


def test_docker_install_uses_helper():
    """The original failure site (install_docker, line 842 pre-fix)
    must go through _install_apt_keyring. This pins the conversion
    so a partial revert can't bring back the broken pattern."""
    src = _installer_src()
    m = re.search(
        r"def install_docker\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "install_docker() not found"
    body = m.group(0)
    # The apt branch must call the helper with docker's key URL
    assert "_install_apt_keyring(" in body, (
        "install_docker must use _install_apt_keyring() — this was "
        "the v0.3.16 failure site on srv04."
    )
    assert "download.docker.com/linux/ubuntu/gpg" in body, (
        "install_docker must reference Docker's GPG key URL"
    )


def test_influxdb_keyring_uses_helper():
    """The other inline-pipe site (_fix_apt_gpg_keys, the InfluxData
    key) must also use the helper."""
    src = _installer_src()
    m = re.search(
        r"def _fix_apt_gpg_keys\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "_fix_apt_gpg_keys() not found"
    body = m.group(0)
    assert "_install_apt_keyring(" in body, (
        "_fix_apt_gpg_keys must use _install_apt_keyring for the "
        "InfluxData key — pre-fix had a raw curl|gpg pipe with the "
        "same /dev/tty vulnerability."
    )
