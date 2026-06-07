"""v0.5.15: Make DPDK Ready wizard offers inline reboot after IOMMU
is enabled (instead of just instructing the operator to do it
manually).

Operator request:
  > when installing dpdk using make dpdk ready, it enables iommu
  > and prompt user to if reboot is required, also let user reboot
  > from the prompt itself.

Before v0.5.15, the wizard after IOMMU enable showed an
informational QMessageBox saying "reboot the server, then run Make
DPDK Ready again". The operator had to alt-tab to a terminal, ssh
in, run `sudo reboot`. Friction the wizard could remove.

After v0.5.15:
  - QMessageBox.Question with three buttons
  - "Reboot Now" → POST /api/system/reboot (the v0.5.2 endpoint)
  - "I'll Reboot Later" → close dialog
  - 404 fallback → operator sees the manual ssh recipe
"""
from __future__ import annotations

import re
from pathlib import Path


_WIZARD = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_make_ready_dialog.py"
)


def test_wizard_has_prompt_reboot_helper():
    """A dedicated _prompt_reboot() method should encapsulate the
    QMessageBox + 3 buttons. Mixing it inline in _on_step_done()
    makes the IOMMU success path hard to test and hard to reuse."""
    src = _WIZARD.read_text()
    assert "_prompt_reboot" in src, (
        "Wizard has no _prompt_reboot helper. The reboot UX should "
        "be its own method, not inlined in _on_step_done."
    )


def test_prompt_reboot_offers_reboot_now_button():
    """The prompt must include a 'Reboot Now' button that takes
    AcceptRole — the visual default action so an Enter-press fires
    reboot, matching what an operator actually wants after seeing
    'IOMMU enabled'."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _prompt_reboot[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_prompt_reboot body not found"
    body = m.group(0)
    assert re.search(r'addButton\(\s*"Reboot Now"', body), (
        "_prompt_reboot doesn't add a 'Reboot Now' button. The "
        "whole point of v0.5.15 is removing the alt-tab-to-terminal "
        "friction."
    )
    # Reboot button must be AcceptRole + default (Enter = reboot).
    assert "QMessageBox.AcceptRole" in body, (
        "'Reboot Now' button isn't AcceptRole. The operator's hand "
        "is already on the keyboard from the wizard — Enter should "
        "trigger reboot, not dismiss."
    )
    assert "setDefaultButton" in body, (
        "_prompt_reboot doesn't call setDefaultButton. Pressing "
        "Enter should hit Reboot Now."
    )


def test_prompt_reboot_offers_dismiss_option():
    """Three-button shape: operator might want to reboot from a
    different terminal (longer-running session, screen scrollback,
    etc.). 'I'll Reboot Later' lets them dismiss cleanly without
    triggering the API call."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _prompt_reboot[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert re.search(r'"I\'ll Reboot Later"', body) or \
           "I'll Reboot Later" in body, (
        "_prompt_reboot missing 'I'll Reboot Later' option. "
        "Operators with a separate terminal session should be able "
        "to dismiss without triggering the API call."
    )
    assert "QMessageBox.RejectRole" in body, (
        "'I'll Reboot Later' button needs RejectRole so Escape "
        "dismisses cleanly."
    )


def test_trigger_reboot_posts_to_api_system_reboot():
    """The reboot path must POST to /api/system/reboot — the
    endpoint added in v0.5.2 specifically for this kind of remote
    reboot. NOT a fire-and-pray `ssh` since the dialog doesn't have
    SSH creds at this point."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _trigger_reboot[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_trigger_reboot not found"
    body = m.group(0)
    assert "/api/system/reboot" in body, (
        "_trigger_reboot doesn't POST to /api/system/reboot. The "
        "v0.5.2 endpoint is the only way to reboot without SSH "
        "creds being open in this dialog."
    )
    # Must use POST.
    assert re.search(r'method\s*=\s*["\']POST["\']', body), (
        "_trigger_reboot doesn't use POST method. /api/system/reboot "
        "is documented as POST-only."
    )
    # Must run async via the existing _api_worker (don't block UI).
    assert "_api_worker" in body or "Worker(" in body, (
        "_trigger_reboot doesn't use the async _api_worker. Blocking "
        "on a 10s POST in the UI thread freezes the dialog."
    )


def test_reboot_response_handles_404_with_helpful_fallback():
    """If the server is too old to have /api/system/reboot
    (pre-v0.5.2), the 404 response should surface a helpful message
    with the manual ssh command — not a generic 'oh well'."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_reboot_response[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_on_reboot_response not found"
    body = m.group(0)
    assert "404" in body, (
        "_on_reboot_response doesn't special-case 404. Pre-v0.5.2 "
        "servers don't have the endpoint; operator should see a "
        "useful 'use ssh' message, not 'request failed'."
    )
    # Must surface the manual recipe (mention v0.5.2 + ssh).
    assert "v0.5.2" in body, (
        "_on_reboot_response 404 path doesn't mention v0.5.2. "
        "Operator needs to know WHY the endpoint is missing."
    )
    assert "ssh" in body, (
        "_on_reboot_response 404 path doesn't give the ssh "
        "remediation. Operator should see the exact command."
    )


def test_reboot_response_handles_success_message_clearly():
    """On 2xx, the dialog should explain what happens next — the
    server replies first, then disappears ~3 s later. Without
    explicit messaging, operator might think the wizard hung."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_reboot_response[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    # Success path must mention coming back online + re-running wizard.
    assert "come back" in body.lower() or "online" in body.lower(), (
        "_on_reboot_response success path doesn't tell operator "
        "what to do next (wait for server to come back online, "
        "re-run wizard)."
    )


def test_iommu_step_invokes_prompt_helper():
    """The IOMMU-success branch of _on_step_done() must invoke
    _prompt_reboot() — not duplicate the old inline informational
    message."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_step_done[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert "needs_reboot" in body, (
        "_on_step_done doesn't check action.needs_reboot — IOMMU "
        "step wouldn't trigger the prompt at all."
    )
    assert "_prompt_reboot" in body, (
        "_on_step_done doesn't call _prompt_reboot. IOMMU success "
        "still falls through to the old inline-message-only flow."
    )
