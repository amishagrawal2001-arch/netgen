"""v0.5.68 — admin security fortification: auth on destructive
endpoints + MAX_CONTENT_LENGTH + strict bool + secure_filename +
wheel content validation.

Audit findings C1, C2, C3, C4 from the admin console audit.

C1: 9 destructive endpoints had no @require_role; only
/api/admin/upgrade_wheel was gated. Adds @require_role("admin")
to bind, unbind, hugepages, iommu, load_modules,
install_dpdk, install_rdma, interface_ip, bind_history.

C2: /api/dpdk/iommu accepted truthy-string `reboot`. JSON
`reboot: "false"` rebooted the host. Now requires literal True
AND a confirm: "REBOOT" sibling.

C3: /api/dpdk/bind `force` flag had the same coercion. Now
literal True only.

C4: No MAX_CONTENT_LENGTH. Wheel upload + bind_history POSTs
unbounded. Adds 200 MB cap. Plus secure_filename on the wheel
upload and zipfile + project-name validation on content.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# --------------------------- C1: auth gates ---------------------------

_DESTRUCTIVE_ROUTES = [
    "/api/dpdk/bind",
    "/api/dpdk/unbind",
    "/api/dpdk/hugepages",
    "/api/dpdk/iommu",
    "/api/dpdk/load_modules",
    "/api/admin/install_dpdk",
    "/api/admin/install_rdma",
    "/api/admin/interface_ip",
    "/api/admin/bind_history",
]


def test_destructive_endpoints_have_admin_decorator():
    """Every destructive endpoint must have @require_role("admin")
    immediately after its @app.route line.

    Exception: /api/admin/bind_history serves GET to viewers and
    POST to admins (v0.5.92 audit M1 — was stacked-decorator dead
    code before). For that route we verify the viewer decorator
    + an internal _role_for_request() admin gate on POST.
    """
    src = _src()
    for route in _DESTRUCTIVE_ROUTES:
        if route == "/api/admin/bind_history":
            # v0.5.92 (audit M1): GET=viewer, POST=admin via
            # internal branch.
            m = re.search(
                r'@app\.route\("' + re.escape(route)
                + r'"[\s\S]{0,500}?@require_role\([\s"\']*viewer',
                src,
            )
            assert m, (
                f"Route {route} lost its viewer decorator."
            )
            # Internal admin gate on POST.
            handler = re.search(
                r"def api_admin_bind_history\(\)[\s\S]+?"
                r"(?=\ndef [a-z_]|\n@app\.route)",
                src,
            )
            assert handler
            body = handler.group(0)
            assert "_role_for_request" in body
            assert "admin" in body
            continue
        # Pattern: @app.route("ROUTE"... followed by @require_role("admin")
        # within the next ~150 chars.
        m = re.search(
            r'@app\.route\("' + re.escape(route) + r'"[\s\S]{0,200}?@require_role\([\s"\']*admin',
            src,
        )
        assert m, (
            f"Route {route} not protected by @require_role(\"admin\")."
        )


# --------------------------- C4: MAX_CONTENT_LENGTH ---------------------------


def test_max_content_length_set():
    """Flask app.config must cap body size globally."""
    src = _src()
    assert re.search(
        r'app\.config\["MAX_CONTENT_LENGTH"\]\s*=\s*\d+\s*\*\s*1024\s*\*\s*1024',
        src,
    ), "No MAX_CONTENT_LENGTH cap on Flask app"


def test_max_content_length_is_at_least_50mb_at_most_500mb():
    """Real wheels are <30 MB; bind_history POSTs are <16 KB. Cap
    should be generous enough for future wheel growth but not
    unbounded. Pick a range that's clearly bounded."""
    src = _src()
    m = re.search(
        r'app\.config\["MAX_CONTENT_LENGTH"\]\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024',
        src,
    )
    assert m
    mb = int(m.group(1))
    assert 50 <= mb <= 500, (
        f"MAX_CONTENT_LENGTH = {mb} MB; expected 50-500 MB"
    )


# --------------------------- C2 + C3: strict bool ---------------------------


def test_strict_true_helper_defined():
    """A helper that accepts ONLY literal Python True. Used at
    the iommu reboot and bind force decision sites."""
    src = _src()
    assert "def _strict_true(" in src, "No _strict_true helper"
    m = re.search(
        r"def _strict_true\(value\):[\s\S]+?return\s+value\s+is\s+True",
        src,
    )
    assert m, "_strict_true must compare with `is True`"


def test_iommu_reboot_uses_strict_true_and_confirm():
    """The reboot path must call _strict_true AND require a
    sibling `confirm: "REBOOT"` field. JSON `reboot: "false"`
    must NOT trigger a reboot."""
    src = _src()
    iommu = re.search(
        r"def dpdk_configure_iommu\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert iommu
    body = iommu.group(0)
    assert "_strict_true(data.get(\"reboot\"" in body, (
        "iommu reboot doesn't use _strict_true — string-truthy "
        "values still trigger reboot"
    )
    assert re.search(
        r'confirm.{0,30}REBOOT',
        body,
    ), (
        "iommu reboot doesn't require `confirm: REBOOT` sibling — "
        "single bad bit triggers reboot"
    )


def test_bind_force_uses_strict_true():
    """The force flag in /api/dpdk/bind must use _strict_true."""
    src = _src()
    bind = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = bind.group(0)
    assert '_strict_true(data.get("force"' in body, (
        "bind force doesn't use _strict_true — string `force: "
        "\"no\"` still bypasses v0.2.76 safety guard"
    )


# --------------------------- C4: wheel upload hardening ---------------------------


def test_wheel_upload_uses_secure_filename():
    """The upload handler must run the operator-supplied filename
    through werkzeug.utils.secure_filename before joining with
    the wheel_dir."""
    src = _src()
    upgrade = re.search(
        r"def api_admin_upgrade_wheel\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = upgrade.group(0)
    assert "secure_filename" in body, (
        "Wheel upload doesn't use werkzeug.utils.secure_filename — "
        "defense-in-depth gap"
    )


def test_wheel_upload_validates_zipfile_content():
    """The handler must read the wheel as a zipfile and verify it
    contains a `*.dist-info/METADATA` AND the project name matches
    `ostg-trafficgen`. Prevents installing 0-byte files or
    completely unrelated wheels."""
    src = _src()
    upgrade = re.search(
        r"def api_admin_upgrade_wheel\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = upgrade.group(0)
    assert "zipfile" in body, (
        "No zipfile validation on uploaded wheel — corrupted or "
        "wrong-project uploads pass through to pip"
    )
    assert "dist-info/METADATA" in body, (
        "Doesn't probe the dist-info/METADATA file inside the wheel"
    )
    assert "ostg-trafficgen" in body, (
        "Doesn't check the project name in METADATA — any wheel "
        "would install"
    )


def test_wheel_upload_unlinks_invalid_uploads():
    """When content validation rejects the wheel, the saved file
    must be unlinked to avoid littering /tmp with junk."""
    src = _src()
    upgrade = re.search(
        r"def api_admin_upgrade_wheel\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = upgrade.group(0)
    # The cleanup pattern is os.unlink(wheel_path) inside the
    # except branch.
    assert re.search(
        r"except\s+Exception[\s\S]{0,200}?os\.unlink\(wheel_path\)",
        body,
    ), (
        "Failed validation doesn't unlink the saved file — /tmp "
        "fills with bogus wheels"
    )


def test_pyproject_version_at_least_0568():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 68), (
        f"Version {m.group(1)} < 0.5.68"
    )
