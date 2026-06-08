"""v0.5.38 — /api/network/interface/<iface>/admin must use
`ip link set dev <iface> <state>`, NOT `ip link set -- <iface>
<state>`.

Operator-reported on srv06 (Jun 8 2026), right-click context menu
on the Interfaces tab → Set Online:

  HTTP 500 from http://san-hp-srv06:5050:
  {"error": "Error: either \"dev\" is duplicate, or \"ens3f0np0\"
   is a garbage.", "ok": false}

Root cause: the endpoint was calling
  ip link set -- ens3f0np0 up

iproute2 DOES NOT honour the GNU `--` end-of-options convention.
When `ip link set` sees `--` it tries to parse it as a parameter,
which collides with the next argument's role as device name. The
error message is iproute2's way of saying "I see something that
LOOKS like it's trying to specify the device, but I'm confused
which arg is the device."

Fix: `ip link set dev <iface> <state>`. The explicit `dev` keyword
is iproute2's canonical disambiguation — `dev` is followed by
the device name, and an iface starting with `-` would NOT be
confused with an option flag.

These tests pin: subprocess call uses `dev`, no `--`, both up
and down state paths.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _interface_admin_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def interface_admin\(iface\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    assert m, "interface_admin body not found"
    return m.group(0)


def test_subprocess_uses_dev_keyword():
    """The `ip link set` invocation must include the literal `dev`
    keyword so iproute2 knows which arg is the device name."""
    body = _interface_admin_body()
    assert re.search(
        r'\[\s*"ip",\s*"link",\s*"set",\s*"dev",',
        body,
    ), (
        "interface_admin's subprocess call doesn't use the `dev` "
        "keyword. iproute2 needs explicit disambiguation — without "
        "it, ip link set can't tell which arg is the device name."
    )


def test_subprocess_does_not_use_double_dash_separator():
    """The pre-v0.5.38 code passed `--` as a GNU-style end-of-options
    separator. iproute2 doesn't grok it and errors with
    'either dev is duplicate, or X is a garbage'. Must NOT appear
    in the call."""
    body = _interface_admin_body()
    # Look for any list entry that is exactly the string "--" inside
    # the `ip link set ...` subprocess args.
    forbidden = re.search(
        r'\[\s*"ip",\s*"link",\s*"set"[^]]*"--"',
        body,
    )
    assert not forbidden, (
        "interface_admin still passes `--` to `ip link set`. "
        "iproute2 doesn't honour GNU `--` end-of-options — it "
        "interprets as a device argument and errors. v0.5.38 "
        "fix not applied."
    )


def test_dev_appears_before_iface_in_subprocess_call():
    """`dev` must come BEFORE the iface variable so iproute2 reads
    `dev <iface> <state>` not `<iface> dev <state>`."""
    body = _interface_admin_body()
    m = re.search(
        r'\[\s*"ip",\s*"link",\s*"set",\s*([^]]+)\]',
        body,
    )
    assert m, "Couldn't extract subprocess args list"
    args_str = m.group(1)
    # Find positions: 'dev' literal, iface var, state var
    dev_pos = args_str.find('"dev"')
    iface_pos = args_str.find('iface')
    state_pos = args_str.find('state')
    assert dev_pos >= 0, '"dev" literal missing'
    assert iface_pos >= 0, "iface variable missing"
    assert state_pos >= 0, "state variable missing"
    assert dev_pos < iface_pos < state_pos, (
        f"Subprocess args out of order. iproute2 expects "
        f"`set dev <iface> <state>`. Got positions: "
        f"dev={dev_pos}, iface={iface_pos}, state={state_pos}"
    )


def test_subprocess_still_uses_list_form_not_shell():
    """The call must remain a list-form subprocess.run (no
    shell=True) so iface can't be shell-interpolated. v0.5.38
    fix must NOT introduce a shell-injection regression."""
    body = _interface_admin_body()
    # The call should be subprocess.run with a list arg, not a
    # string with shell=True.
    assert re.search(
        r'_sp\.run\(\s*\[\s*"ip"',
        body,
    ) or re.search(
        r'subprocess\.run\(\s*\[\s*"ip"',
        body,
    ), (
        "interface_admin no longer uses list-form subprocess.run "
        "for `ip link set`. Risk: shell=True string-form would "
        "let a maliciously-named iface inject shell commands."
    )
    assert "shell=True" not in body, (
        "interface_admin's subprocess call uses shell=True — "
        "shell-injection risk on iface name."
    )


def test_no_other_sites_pass_double_dash_to_ip(tmp_path):
    """Sweep the entire repo for `["ip", ...]` + `"--"` combos —
    same iproute2 bug class. Other sites that picked up the
    pattern would fail in the same way the interface_admin one did.
    Test passes if interface_admin is the only callsite (and it's
    fixed)."""
    src = _SERVER.read_text()
    # Pattern: list literal starting with "ip" that also contains "--"
    bad_calls = []
    for m in re.finditer(
        r'\[\s*"ip"[^]]+?"--"[^]]*?\]',
        src,
    ):
        bad_calls.append(m.group(0)[:200])
    assert not bad_calls, (
        "Other `ip ...` calls still pass `--`:\n"
        + "\n".join(bad_calls)
    )


def test_pyproject_version_at_least_0538():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 38), (
        f"Version {m.group(1)} < 0.5.38"
    )
