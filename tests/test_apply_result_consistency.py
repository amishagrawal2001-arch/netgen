"""Per-protocol apply-result UX consistency (v0.2.93).

Pinned behaviour:
  * MultiDeviceResultsDialog can be constructed with the same
    (title, summary, results, parent) signature every caller uses.
  * Results prefixed with ✅ get a green colour; ❌ red; ⚠️ orange;
    ℹ️ blue; ⏱️ purple. Pinning ensures future per-protocol callers
    use the same prefix convention.
  * Source-grep guard: ISIS apply + remove + VXLAN apply all import
    MultiDeviceResultsDialog (no protocol falls back to plain
    QMessageBox.information for the multi-device case).
"""

import re
from pathlib import Path

import pytest
from PyQt5.QtWidgets import QLabel, QWidget


REPO = Path(__file__).resolve().parent.parent


# ──────────────────────────────────────── MultiDeviceResultsDialog
def test_results_dialog_renders_each_result_as_label(qapp):
    """Each result string becomes its own labelled row. Colour
    coding follows the leading emoji."""
    parent = QWidget()  # noqa: F841
    parent.show()
    from widgets.devices_tab import MultiDeviceResultsDialog
    results = [
        "✅ R1: succeeded",
        "❌ R2: failed (HTTP 500)",
        "⚠️ R3: warning",
        "ℹ️ R4: info",
        "⏱️ R5: timeout",
    ]
    dlg = MultiDeviceResultsDialog(
        "Test results", "5 devices processed", results, parent,
    )
    # All result lines present in the dialog as QLabels.
    labels = [w.text() for w in dlg.findChildren(QLabel)]
    for r in results:
        assert r in labels, f"missing result label: {r!r}"


def test_results_dialog_colour_codes_by_emoji_prefix(qapp):
    """Prefix → stylesheet colour. Pin the convention so a future
    refactor can't quietly drop the colour mapping."""
    parent = QWidget()  # noqa: F841
    parent.show()
    from widgets.devices_tab import MultiDeviceResultsDialog
    results = [
        "✅ green",
        "❌ red",
        "⚠️ orange",
        "ℹ️ blue",
        "⏱️ purple",
    ]
    dlg = MultiDeviceResultsDialog(
        "Test", "5", results, parent,
    )
    # Find the matching QLabel for each result + check the stylesheet
    # has the expected colour token.
    expected = {
        "✅ green":  "green",
        "❌ red":    "red",
        "⚠️ orange": "orange",
        "ℹ️ blue":   "blue",
        "⏱️ purple": "purple",
    }
    labels = {w.text(): w for w in dlg.findChildren(QLabel)
              if w.text() in expected}
    for text, want_colour in expected.items():
        label = labels.get(text)
        assert label is not None, f"no label for {text!r}"
        ss = label.styleSheet()
        assert want_colour in ss, (
            f"{text!r} label missing {want_colour!r} colour token "
            f"(got: {ss!r})"
        )


def test_results_dialog_summary_label_rendered(qapp):
    parent = QWidget()  # noqa: F841
    parent.show()
    from widgets.devices_tab import MultiDeviceResultsDialog
    dlg = MultiDeviceResultsDialog(
        "Title", "3 succeeded, 1 failed", ["✅ a", "✅ b", "✅ c", "❌ d"], parent,
    )
    labels = [w.text() for w in dlg.findChildren(QLabel)]
    assert "3 succeeded, 1 failed" in labels


def test_results_dialog_accepts_empty_results(qapp):
    """An empty selection shouldn't crash the dialog — sometimes
    the caller filters out skipped devices."""
    parent = QWidget()  # noqa: F841
    parent.show()
    from widgets.devices_tab import MultiDeviceResultsDialog
    dlg = MultiDeviceResultsDialog(
        "Empty", "nothing to do", [], parent,
    )
    # No exception during construction.
    assert dlg is not None


# ────────────────────────────────────── source-grep guards
@pytest.mark.parametrize("source_file,context_marker", [
    ("utils/devices_tab_isis.py",
     "def _apply_isis_to_devices"),
    ("utils/devices_tab_isis.py",
     "def _remove_isis_from_devices"),
    ("widgets/devices_tab.py",
     "def apply_vxlan_configurations"),
])
def test_apply_path_imports_multi_device_results_dialog(
    source_file, context_marker,
):
    """Per-protocol apply path imports MultiDeviceResultsDialog — pin
    that the v0.2.93 consistency landed and didn't get accidentally
    reverted to plain QMessageBox.information.

    Checks that within the method body (from `def …` to the next
    `def …` or EOF) the string MultiDeviceResultsDialog appears.
    """
    src = (REPO / source_file).read_text()
    m = re.search(
        rf"{re.escape(context_marker)}.*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None, f"{context_marker} not found in {source_file}"
    body = m.group(0)
    assert "MultiDeviceResultsDialog" in body, (
        f"{context_marker} in {source_file} doesn't use "
        f"MultiDeviceResultsDialog — per-protocol apply UX has "
        f"regressed to a plain QMessageBox."
    )


def test_isis_apply_collects_per_device_results():
    """The shape: per-device result list with emoji prefixes. Pin
    that the source actually constructs `results = []` and appends
    to it (not just sends a summary string)."""
    src = (REPO / "utils" / "devices_tab_isis.py").read_text()
    m = re.search(
        r"def _apply_isis_to_devices.*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    body = m.group(0)
    # Constructs the list…
    assert "results = []" in body or "results: list" in body
    # …and prefixes lines with the standardised emoji.
    assert "✅" in body
    assert "❌" in body


def test_vxlan_apply_collects_per_device_results():
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    m = re.search(
        r"def apply_vxlan_configurations.*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    body = m.group(0)
    assert "results = []" in body
    assert "✅" in body
    assert "❌" in body


def test_vxlan_apply_has_dialog_failure_fallback():
    """If MultiDeviceResultsDialog construction fails the operator
    still gets a QMessageBox so they're not left wondering — pin
    that the fallback exists."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    m = re.search(
        r"def apply_vxlan_configurations.*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    body = m.group(0)
    assert "could not show results dialog" in body
    assert "QMessageBox" in body  # the fallback path
