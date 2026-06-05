# stream_dialog.py
import logging
import os
import uuid

logger = logging.getLogger(__name__)

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QStackedWidget, QSpinBox,
    QTableWidgetItem, QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox, QWidget, QMessageBox,
    QHeaderView, QRadioButton, QGroupBox, QGridLayout, QTabWidget, QScrollArea, QCheckBox, QInputDialog, QSplitter,
    QAction, QMenu, QAbstractItemView, QSizePolicy, QTreeWidget, QTreeWidgetItem, QTextEdit, QTextBrowser,
    QSpacerItem, QFileDialog
)
from PyQt5.QtCore import QTimer, Qt, QRegExp, QSize, QItemSelectionModel, QDateTime
from PyQt5.QtGui import QIntValidator, QBrush, QRegExpValidator, QIcon, QValidator, QPixmap, QColor, QKeySequence
from PyQt5.QtWidgets import QShortcut

# v0.2.96: pure-function validators + live red-border stylesheet
# constants. The dialog wires both live (textChanged → border colour)
# and at submit-time (accept() override → blocking QMessageBox with
# the full error list). See utils/stream_input.py for the parsing.
from utils.stream_input import (
    validate_mac, is_zero_mac,
    validate_ipv4, validate_ipv6,
    validate_frame_sizes, collect_errors,
)

_LIVE_OK_QSS  = ""  # Qt default — respects platform theme
_LIVE_BAD_QSS = "QLineEdit { border: 1px solid #dc2626; background: #fef2f2; }"


class Unsigned32BitValidator(QValidator):
    """Custom validator for 32-bit unsigned integers."""
    def validate(self, input, pos):
        if not input:  # Allow empty field for user input
            return QValidator.Intermediate, input, pos
        try:
            value = int(input)
            if 0 <= value <= 4294967295:
                return QValidator.Acceptable, input, pos
            return QValidator.Invalid, input, pos
        except ValueError:
            return QValidator.Invalid, input, pos


# =============================================================================
# DPDK Workflow Guide — content + dialog factory
# =============================================================================
# Reachable from two places: the "Read More" button on the stream editor's
# Variable Fields tab, and the Help → "DPDK Workflow Guide" menu in main.py.
# The HTML below is rendered with QTextBrowser so the calibrated tables, code
# blocks, and inline styling stay readable. Keep this in sync with the
# "DPDK Multi-Queue Scaling" README section.

_DPDK_GUIDE_HTML = r"""
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         color: #1f2937; line-height: 1.55; font-size: 12px; }
  h1 { color: #1e40af; font-size: 20px; margin: 0 0 8px 0; }
  h2 { color: #374151; font-size: 15px; margin-top: 18px;
       border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
  h3 { color: #4b5563; font-size: 13px; margin-top: 14px; }
  p, li { color: #374151; }
  code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
         background: #f3f4f6; padding: 1px 5px; border-radius: 3px;
         font-size: 11px; color: #1e3a8a; }
  pre { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
        background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 4px;
        padding: 10px; font-size: 11px; color: #111827; }
  table { border-collapse: collapse; margin-top: 6px; font-size: 11px; }
  th, td { border: 1px solid #d1d5db; padding: 5px 9px; text-align: left; }
  th { background: #f3f4f6; color: #374151; font-weight: 600; }
  td.num { text-align: right; font-family: ui-monospace, SFMono-Regular, monospace; }
  .hot { color: #1d4ed8; font-weight: 600; }
  .muted { color: #6b7280; font-size: 11px; }
</style>

<h1>DPDK Traffic Blast — Workflow Guide</h1>
<p class="muted">End-to-end walkthrough for using the multi-queue DPDK <code>tx_worker</code>
backend to saturate 100G / 400G line rate.</p>

<h2>1. One-time server setup</h2>
<p>Done once per server — visit <code>http://&lt;server&gt;:5050/admin</code> in a browser:</p>
<ol>
  <li><b>DPDK runtime installed</b> — Card "DPDK Runtime". Click <i>Install DPDK</i>
      if missing; tail the log inline.</li>
  <li><b>Hugepages allocated</b> — Card "Hugepages". Allocate 4–8 GB on the NIC's
      NUMA node (1G pages preferred).</li>
  <li><b>IOMMU enabled</b> — required for <code>vfio-pci</code> binding. The portal
      detects and offers a one-click GRUB toggle (reboot needed).</li>
  <li><b>NIC ready:</b>
    <ul>
      <li><b>Mellanox (mlx5):</b> no bind needed — DPDK uses <code>mlx5_core</code>
          alongside the kernel driver.</li>
      <li><b>Broadcom / Intel / AMD:</b> click <i>Bind to DPDK</i> on the interface row
          to bind to <code>vfio-pci</code>.</li>
    </ul>
  </li>
</ol>

<h2>2. Per-stream workflow</h2>

<h3>Step 1 — Create the stream</h3>
<p>Right-click an interface in the server tree → <b>Add Stream</b>.</p>

<h3>Step 2 — Configure protocol fields</h3>
<ul>
  <li><b>L2:</b> source / destination MAC (must be valid; the worker doesn't ARP)</li>
  <li><b>L3:</b> source / destination IPv4</li>
  <li><b>L4:</b> <span class="hot">UDP</span> — the worker is UDP-only;
      don't pick TCP/ICMP</li>
  <li><b>Frame size:</b> 64 (max pps stress) or 1500 (typical line-rate)</li>
  <li><b>Stream Rate Type:</b> <i>Line Rate</i> for max blast, or <i>PPS</i> for a target rate</li>
</ul>

<h3>Step 3 — Enable DPDK + pick TX cores</h3>
<p>On the <b>Variable Fields</b> tab:</p>
<pre>☑  Use DPDK (tx_worker)
☐  Force Multi-Instance DPDK         ← leave unchecked for single-NIC blast
TX Cores (queues): [ 1 ▾ ]   [ Recommend ]</pre>

<p>Three ways to land on a good TX-cores value, in increasing user effort:</p>

<ol>
  <li><b>Do nothing — Line Rate auto-picks.</b>
      If your <i>Stream Rate Type</i> is <b>Line Rate</b> and you leave
      <b>TX Cores</b> at the default <code>1</code>, the launcher derives a
      sufficient core count from the interface's link speed at start-up. See
      "Line Rate auto-pick" below.</li>
  <li><b>Click <code>Recommend</code></b> — sends the iface, frame size, and
      target pps to <code>GET /api/dpdk/recommend</code> and auto-selects
      from <code>1 / 2 / 4 / 8 / 12 / 16</code>. The hint label below the
      combo box shows the server's reasoning. Use this when you want the
      number visible in the UI before saving.</li>
  <li><b>Set it manually</b> using the calibrated table (Mellanox CX-7,
      AMD EPYC):
    <table>
      <tr><th>Goal</th><th>Frame</th><th>TX Cores</th></tr>
      <tr><td>Saturate 100G</td><td>1500B</td><td class="num"><b>2</b></td></tr>
      <tr><td>Saturate 200G</td><td>1500B</td><td class="num"><b>4</b></td></tr>
      <tr><td>Saturate ~400G</td><td>1500B</td><td class="num"><b>8</b></td></tr>
      <tr><td>Max 64B pps on this NIC</td><td>64B</td>
          <td class="num"><b>16</b>
          <span class="muted">(~58 Mpps, 39% of 100G line rate)</span></td></tr>
    </table>
  </li>
</ol>

<h3>Line Rate auto-pick — how it works</h3>
<p><b>Why it exists.</b> "Line Rate" used to mean <code>--pps 0</code> (each
worker floods uncapped) but the achievable flood is bounded by
<code>tx_cores</code>. With the default <code>1</code> you only get
~4 Mpps × frame_size — about <b>49 Gbps on a 400G link with 1500B frames</b>,
nowhere near line rate. Surprising and wrong.</p>

<p><b>What the launcher does at start-up</b>, only when
<i>Stream Rate Type</i> is <b>Line Rate</b> AND the user did not explicitly
set <code>dpdk_tx_cores</code>:</p>

<ol>
  <li>Read <code>/sys/class/net/&lt;iface&gt;/speed</code>
      <span class="muted">(skip auto-pick if unreadable; keep tx_cores=1)</span></li>
  <li>Compute the link's line-rate pps for the chosen frame size
      <pre>line_pps = link_mbps × 1e6  /  ((frame_size + 20) × 8)</pre>
      <span class="muted">+20 bytes covers the L1 preamble + IFG.</span></li>
  <li>Divide by the calibrated per-core ceiling
      <table>
        <tr><th>Frame size</th><th>Per-core pps ceiling</th></tr>
        <tr><td>≤ 128B</td><td class="num">4,500,000</td></tr>
        <tr><td>≤ 512B</td><td class="num">4,300,000</td></tr>
        <tr><td>&gt; 512B</td><td class="num">4,100,000</td></tr>
      </table>
  </li>
  <li>Round the result up to the next supported step
      (<code>1 / 2 / 4 / 8 / 12 / 16</code>; cap at 16).</li>
  <li>Use the result <i>only if it's higher</i> than what's already configured
      — auto-pick never decreases your value.</li>
</ol>

<p><b>Auto-pick by frame size on a 400G link</b>:</p>
<table>
  <tr><th>Frame</th><th>Line-rate pps</th><th>Need</th><th>Auto tx_cores</th></tr>
  <tr><td>64B</td>   <td class="num">595,238,095</td><td class="num">~133</td><td class="num"><b>16</b></td></tr>
  <tr><td>128B</td>  <td class="num">337,837,837</td><td class="num">~76</td> <td class="num"><b>16</b></td></tr>
  <tr><td>256B</td>  <td class="num">181,159,420</td><td class="num">~41</td> <td class="num"><b>16</b></td></tr>
  <tr><td>512B</td>  <td class="num">93,984,962</td> <td class="num">~22</td> <td class="num"><b>16</b></td></tr>
  <tr><td>1500B</td> <td class="num">32,894,736</td> <td class="num">~9</td>  <td class="num"><b>12</b></td></tr>
  <tr><td>9000B</td> <td class="num">5,541,016</td>  <td class="num">~2</td>  <td class="num"><b>2</b></td></tr>
</table>
<p class="muted">At ≤ 512B the theoretical "need" is well above the
per-NIC ceiling, so auto-pick caps at 16. Beyond that point the limit is
PCIe / per-queue PMD throughput, not core count — see "Calibrated
performance numbers" below.</p>

<p><b>How to override.</b> Pick any non-default value in the <i>TX Cores</i>
combo box, set <code>dpdk_tx_cores</code> in the stream JSON, or export
<code>DPDK_TX_CORES=N</code> on the server. Any of those counts as
"explicit" and disables auto-pick — the launcher will use exactly your
value (clamped at the NIC's <code>max_tx_queues</code>).</p>

<p><b>How to confirm it fired.</b> When auto-pick kicks in, the launcher
logs at INFO:</p>
<pre>[dpdk] Line Rate auto-picked tx_cores=12 for enp181s0f0np0
       (frame=1500B, link-derived); set 'TX Cores (queues)'
       explicitly to override.</pre>
<pre>ssh root@&lt;server&gt; 'journalctl -u netgen-server -f' | grep auto-picked</pre>

<h3>Step 4 — Save and start</h3>
<p>Click <b>Save</b>, then the <b>▶ Start</b> icon on the stream row. The icon
flashes yellow (pending) → green (running). Your <code>dpdk_tx_cores</code> rides
through the API to <code>tx_worker --tx-cores N</code>.</p>

<h2>3. Verification — three places to look</h2>

<h3>A. Stream Statistics dock (this client)</h3>
<p>The bottom <b>Statistics</b> dock → <b>Stream Statistics</b> tab shows an
<b>Engine</b> column rendering <span class="hot">DPDK ×N</span> (multi-queue),
<span class="hot">DPDK</span> (single), or <code>Scapy</code> (kernel). Rates
update live.</p>

<h3>B. /admin Network Interfaces table</h3>
<p>Browse <code>http://&lt;server&gt;:5050/admin</code> → "Network Interfaces" — a
<b>TX queues</b> column shows the same badge server-side, useful when triaging
without the client open.</p>

<h3>C. Server logs</h3>
<pre>ssh root@&lt;server&gt; 'journalctl -u netgen-server -f' | grep STAT</pre>
<p>Each <code>STAT</code> line shows aggregated tx / drop counts and
<code>tx_cores=N</code>:</p>
<pre>STAT stream=&lt;id&gt; tx=256487808 drop=0 frame=1500
     pps_target=0 burst=64 tx_cores=8 offload=0x2</pre>

<h2>4. Calibrated performance numbers</h2>
<p>Mellanox CX-7 (BDF <code>0000:b5:00.0</code>, NUMA 1) on AMD EPYC, NUMA-pinned
worker cores. Linear scaling holds to 8 cores; 80–90% efficiency at 12–16. Past
that the bottleneck is per-queue PMD throughput / PCIe overhead, not software.</p>

<table>
  <tr><th>Cores</th><th>64B Mpps</th><th>64B % of 100G</th><th>1500B Gbps</th></tr>
  <tr><td class="num">1</td>  <td class="num">4.54</td>  <td class="num">3%</td>   <td class="num">49</td></tr>
  <tr><td class="num">2</td>  <td class="num">9.14</td>  <td class="num">6%</td>   <td class="num"><b>99.6 (100G)</b></td></tr>
  <tr><td class="num">4</td>  <td class="num">18.21</td> <td class="num">12%</td>  <td class="num">199</td></tr>
  <tr><td class="num">8</td>  <td class="num">35.66</td> <td class="num">24%</td>  <td class="num"><b>385 (~400G)</b></td></tr>
  <tr><td class="num">12</td> <td class="num">48.12</td> <td class="num">32%</td>  <td>—</td></tr>
  <tr><td class="num">16</td> <td class="num">58.24</td> <td class="num">39%</td>  <td>—</td></tr>
</table>

<h2>5. When to change <code>tx_cores</code></h2>
<table>
  <tr><th>Symptom</th><th>Action</th></tr>
  <tr><td>Want more pps, CPU available</td><td>Bump up one step (1→2→4→8)</td></tr>
  <tr><td>Drops &gt; 0 in STAT lines</td><td>Hit NIC HW ceiling — diminishing returns</td></tr>
  <tr><td>1500B at line rate, target met</td><td>Stay where you are</td></tr>
  <tr><td>64B small-packet stress</td><td>Use 16 (NIC ceiling)</td></tr>
  <tr><td>Dual-port wire-rate from one host</td>
      <td>Run two streams on different ports, each with its own <code>tx_cores</code> budget</td></tr>
</table>

<h2>6. Troubleshooting</h2>
<table>
  <tr><th>Symptom</th><th>Likely cause / fix</th></tr>
  <tr><td>"Stream starts and stops immediately"</td>
      <td>Check <code>journalctl -u netgen-server</code> for <code>tx_worker</code>
          errors. Most common: hugepages not on the NIC's NUMA node, or
          <code>vfio-pci</code> not bound (Broadcom / Intel).</td></tr>
  <tr><td><code>tx-cores=N clamped to M</code> warning</td>
      <td><code>M</code> is <code>min(available_lcores, NIC max_tx_queues)</code>.
          Usually means another DPDK process owns the cores —
          <code>pgrep tx_worker</code>.</td></tr>
  <tr><td>Engine column shows "Scapy" with DPDK checked</td>
      <td>Stream was created before DPDK options existed.
          Re-edit, re-tick <i>Use DPDK</i>, save.</td></tr>
  <tr><td>L4=ICMP doesn't work</td>
      <td>tx_worker is UDP-only. Switch L4 to UDP.</td></tr>
  <tr><td>Drops jump 8 → 16 cores</td>
      <td>NIC PCIe / per-queue PMD ceiling. Drop back to 8 or 12.</td></tr>
  <tr><td>100G link reports only ~50G</td>
      <td>Receiving side is the bottleneck (NIC, cables, intermediate switch
          PFC, or autoneg to 50G/25G). Verify with
          <code>ethtool &lt;iface&gt; | grep Speed</code> on both ends.</td></tr>
</table>

<h2>7. API-only workflow (no GUI)</h2>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "BlastUDP",
        "enabled": true,
        "frame_size": 1500,
        "stream_rate_type": "Line Rate",
        "L4": "UDP",
        "mac_source_address": "8c:91:3a:d6:1b:7a",
        "mac_destination_address": "8c:91:3a:d6:1b:7b",
        "ipv4_source": "10.0.0.1",
        "ipv4_destination": "10.0.0.2",
        "dpdk_enable": true,
        "dpdk_tx_cores": 4
      }]
    }
  }'</pre>

<p class="muted">Six clicks (or one curl) from a fresh stream to 100G saturated.</p>
"""


def show_dpdk_usage_guide(parent=None):
    """Open the DPDK Workflow Guide dialog. Used from the stream editor's
    "Read More" button and from the main window's Help menu."""
    _open_help_dialog(parent, "DPDK Traffic Blast — Workflow Guide", _DPDK_GUIDE_HTML)


# =============================================================================
# Installation Guide — content + dialog factory
# =============================================================================
# Reachable from Help → Install Guide. Documents the single-command install
# flow (install_ostg_complete.py) and what gets put on the target server, so
# operators don't have to read the install script's source to know what it
# does. Same QTextBrowser shell as the DPDK guide.

_INSTALL_GUIDE_HTML = r"""
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         color: #1f2937; line-height: 1.55; font-size: 12px; }
  h1 { color: #1e40af; font-size: 20px; margin: 0 0 8px 0; }
  h2 { color: #374151; font-size: 15px; margin-top: 18px;
       border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
  h3 { color: #4b5563; font-size: 13px; margin-top: 14px; }
  p, li { color: #374151; }
  code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
         background: #f3f4f6; padding: 1px 5px; border-radius: 3px;
         font-size: 11px; color: #1e3a8a; }
  pre { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
        background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 4px;
        padding: 10px; font-size: 11px; color: #111827; }
  table { border-collapse: collapse; margin-top: 6px; font-size: 11px; }
  th, td { border: 1px solid #d1d5db; padding: 5px 9px; text-align: left;
           vertical-align: top; }
  th { background: #f3f4f6; color: #374151; font-weight: 600; }
  .ok { color: #15803d; font-weight: 600; }
  .muted { color: #6b7280; font-size: 11px; }
</style>

<h1>Netgen Server — Installation Guide</h1>
<p class="muted">Three paths: in-GUI installer (no terminal), prebuilt
release artifacts (download &amp; double-click), or the
<code>install_ostg_complete.py</code> CLI for scripted provisioning.
All three land the same wheel + DPDK runtime + systemd unit. Pick
based on whether you have SSH creds, a working server, or just want
a click-through experience.</p>

<h2>1. In-GUI installer (NEW in 0.2.6) <span class="ok">★ recommended</span></h2>

<p>You're in the client right now. Drive both install and upgrade
without leaving it:</p>

<p><b>Help → Install / Upgrade Server...</b></p>

<table>
  <tr><th>Tab</th><th>When to use</th><th>What it does</th></tr>
  <tr><td><b>Upgrade running server</b><br><span class="muted">HTTP or SSH, ~30–60 s</span></td>
      <td>Server is already running an older wheel; you want to roll a
          new release onto it.</td>
      <td>The Upgrade tab has <b>two</b> buttons (see §1a below):
          <b>Upload &amp;&amp; Upgrade</b> (HTTP) and
          <b>Upgrade via SSH (manual)</b>. Both land the new wheel via
          <code>pip install --upgrade --force-reinstall --no-deps</code>
          then restart the service; the client polls
          <code>/api/health</code> for the new instance.</td></tr>
  <tr><td><b>Fresh install via SSH</b><br><span class="muted">paramiko, 15–45 min, <b>detached</b></span></td>
      <td>Bare Linux host. No netgen, no Docker, no DPDK yet.</td>
      <td>Connects (password OR SSH key, configurable port) → sftp-copies
          the wheel + <code>install_ostg_complete.py</code> to
          <code>/tmp/netgen_install/</code> → spawns the installer
          <i>detached</i> via <code>nohup</code> on the target →
          polls <code>/var/log/netgen-install.log</code> for live
          output. Optional flags: <code>--no-dpdk</code>,
          <code>--skip-dpdk-build</code>.</td></tr>
</table>

<h3>1a. Two ways to upgrade a running server</h3>

<p>On the <b>Upgrade running server</b> tab, fill in the server URL +
wheel (+ optional auth token), then pick a button:</p>

<table>
  <tr><th>Button</th><th>Path</th><th>Use when</th></tr>
  <tr><td><b>Upload &amp;&amp; Upgrade</b></td>
      <td>HTTP <code>POST /api/admin/upgrade_wheel</code> → server
          pip-installs &amp; restarts itself. <b>No SSH needed.</b></td>
      <td>Normal case — the server is on 0.2.6+ (has the upgrade
          endpoint). If the endpoint is missing/erroring and you've
          filled in the SSH box, it <i>auto-falls back</i> to SSH
          (see below).</td></tr>
  <tr><td><b>Upgrade via SSH (manual)</b><br><span class="muted">NEW in 0.2.35</span></td>
      <td>Skips HTTP entirely: sftp the wheel →
          <code>pip install --upgrade --force-reinstall --no-deps</code>
          → restart the service. Uses the SSH credentials on the tab.</td>
      <td><b>OLD servers</b> that predate the
          <code>/api/admin/upgrade_wheel</code> endpoint (pre-0.2.6), or
          whenever you just prefer the direct path. No 404 round-trip.</td></tr>
</table>

<div class="warn"><b>Old server without the HTTP upgrade endpoint?</b>
Use <b>Upgrade via SSH (manual)</b>. (As of 0.2.34, <b>Upload &amp;&amp;
Upgrade</b> also auto-detects the missing endpoint — HTTP 404 — and
offers the SSH fallback, provided you've entered SSH credentials.) The
SSH restart tries <code>systemctl restart netgen-server</code> and falls
back to the legacy <code>ostg-server</code> unit, so it works on both
new and old installs.</div>

<p class="muted">Equivalent manual one-liner (if you'd rather use a
terminal): <code>scp ostg_trafficgen-&lt;ver&gt;.whl root@&lt;host&gt;:/tmp/
&amp;&amp; ssh root@&lt;host&gt; 'pip3 install --upgrade --force-reinstall
--no-deps /tmp/ostg_trafficgen-&lt;ver&gt;.whl &amp;&amp; (systemctl
restart netgen-server || systemctl restart ostg-server)'</code>. Once on
0.2.28+, the server's startup self-heal redeploys the FRR/DHCP assets and
rebuilds the container image automatically.</p>

<h3>Test Connection + pre-flight checks (NEW in 0.2.9)</h3>

<p>The Fresh Install tab grew a <b>Test Connection</b> button next
to <b>Install</b>. Run it first to verify SSH credentials work and
the target meets the install's prerequisites — saves a 15-min
install round-trip when the password's wrong or Python is too old.
2–3 seconds total budget. Four probes after the SSH connect:</p>

<table>
  <tr><th>Probe</th><th>What it checks</th><th>Why</th></tr>
  <tr><td><b>Python ≥ 3.9</b></td>
      <td><code>python3 --version</code> → parses major/minor</td>
      <td><code>install_python_dependencies</code> needs ≥3.9 for
          f-strings + type hints in the wheel</td></tr>
  <tr><td><b>Sudo capability</b></td>
      <td><code>sudo -n true</code> (skipped when user is root)</td>
      <td>Non-root installs that hit a password prompt block
          forever — paramiko has no terminal to type into</td></tr>
  <tr><td><b>Free disk ≥ 4 GB on /var</b></td>
      <td><code>df -BG --output=avail /var</code></td>
      <td>DPDK build + FRR Docker image + apt cache all land under
          <code>/var</code>; smaller boxes crash mid-meson</td></tr>
  <tr><td><b>netgen-server already active?</b></td>
      <td><code>systemctl is-active netgen-server.service</code></td>
      <td>If yes, hints to switch to the <b>Upgrade running server</b>
          tab — a 30s round-trip instead of a 15-min full install</td></tr>
</table>

<p>Results stream into the log pane as <code>[test] ✓ / ✗</code>
lines. Tail QMessageBox shows either <b>"All checks passed — safe
to click Install"</b> or <b>"Some checks failed — install will
likely fail too"</b> with the failing bullet list. The Install
button still works without first clicking Test, but Test catches
the obvious bugs in 2 s instead of 30 s into spawn.</p>

<h3>SSH port + host configuration</h3>

<p>The Host row exposes <b>address / user / port</b> — port
defaults to 22 but lab boxes behind jump hosts or alternate
sshd configs (2222 / 22000 / etc.) were unreachable before. Both
the connect step and the pid-probe / pre-flight probes use the
configured port.</p>

<h3>Error surfacing (NEW in 0.2.9)</h3>

<p>The log pane is now <b>color-coded</b> as output streams in:</p>

<ul>
  <li><span style="color:#dc2626;">red</span> — <code>[ERROR]</code>,
      <code>error</code>, <code>exception</code>, <code>failed</code>,
      <code>fatal</code>, <code>traceback</code>, <code>-E-</code>,
      <code>✗</code></li>
  <li><span style="color:#d97706;">amber</span> — <code>[WARNING]</code>,
      <code>warn</code>, <code>⚠</code></li>
  <li><span style="color:#15803d;">green</span> — <code>✓</code>,
      <code>success</code>, <code>OK</code></li>
  <li>neutral — everything else</li>
</ul>

<p>Cleanup-style messages from <code>install_ostg_complete.py</code>
that aren't real errors (<code>Failed to stop ostg-server: Unit
not loaded</code>, <code>No such image: ostg-frr:latest</code>,
etc.) get filtered out of the error color so the operator's eye
isn't drawn to "this is fine" cleanup spam.</p>

<p>On failure, the dialog now pops a <b>QMessageBox.critical</b>
with the last 6 captured error lines bullet-listed — no more
scrolling a 1000-line log to find the actual cause. Example:</p>

<pre style="background:#fef2f2; border-left:3px solid #dc2626; padding:8px;">
Install failed

The fresh install did not complete. Most recent errors from the log:

 • install_ostg_complete.py: error: unrecognized arguments: -w
 • [ERROR] Wheel file not found: dist/ostg_trafficgen-0.0.0-py3-none-any.whl
</pre>

<p>Errors are also reset between runs — a failed install doesn't
leak its captured errors into the next attempt's failure dialog.</p>

<h3>Detached install — what survives client exit</h3>

<p>The fresh-install path runs the installer under
<code>nohup</code> on the target, with stdout/stderr redirected to
a log file. <b>Close the dialog, lose WiFi, crash the client —
the install keeps running.</b> The state lives in three target-side
files:</p>

<table>
  <tr><th>Path</th><th>Purpose</th></tr>
  <tr><td><code>/var/log/netgen-install.log</code></td>
      <td>Full installer stdout+stderr. Permanent record; survives
          reboot. <code>tail -F</code>-able alongside the GUI.</td></tr>
  <tr><td><code>/var/run/netgen-install.pid</code></td>
      <td>Wrapper script's PID while the install is alive. Removed
          when the installer exits.</td></tr>
  <tr><td><code>/var/run/netgen-install.exit</code></td>
      <td>Installer's exit code, written by the wrapper when the
          install finishes (0 = success, non-zero = failure).</td></tr>
</table>

<p>Re-open the dialog and click Install against the same host:
the client probes for the pid file, finds the live install, and
prompts <b>"Resume monitoring its log?"</b> The worker switches
into resume mode — skips SFTP upload + spawn entirely, jumps
straight to polling the log from byte 0. One click to reattach
to an install you walked away from an hour ago.</p>

<p>Closing the dialog mid-install prompts: <i>"The fresh install
is running detached on the target — closing this dialog will stop
monitoring the log, but the install itself will continue to
completion."</i> The upgrade tab (HTTP-based) still uses the old
"abort-on-close" prompt because there's no way to detach an HTTP
upload mid-flight.</p>

<p><b>Safety properties:</b></p>
<ul>
  <li>Upgrade tab: overlapping wheel uploads return HTTP 409;
      filenames other than <code>*.whl</code> return HTTP 400
      (no path traversal); server pip-installs into its own
      Python via <code>sys.executable -m pip</code>.</li>
  <li>Fresh-install tab: <code>nohup</code> + redirected stdin
      so no PTY → no SIGHUP on disconnect; pre-flight pid check
      refuses to start a second install on the same target while
      one's already running.</li>
  <li>SSH password lives only in the dialog field for the
      operation's duration (never written to disk). SSH keys
      are read from the chosen file at connection time.</li>
  <li>Cancelling reads the right copy depending on the tab —
      detached SSH says "monitoring stops, install continues",
      foreground HTTP says "abort and may leave server
      inconsistent".</li>
</ul>

<p class="muted">The dialog uses HTTP (Tab 1) and paramiko (Tab 2);
no Python toolchain required on the target. Pre-fills Server URL
from the current client connection and auth token from
<code>$NETGEN_AUTH_TOKEN</code>. Adaptive log-poll backoff: 1 s
while output is flowing, 5 s when idle — keeps SSH churn low
during the 15+ min DPDK build.</p>

<h2>2. Prebuilt release artifacts</h2>

<p>Every tagged release on GitHub ships four CI-built artifacts under
<a href="https://github.com/amishagrawal2001-arch/netgen/releases/latest">releases/latest</a>.
They're not interchangeable — pick by <i>what you're trying to install
where</i>, not by your laptop's OS:</p>

<table>
  <tr><th>File</th><th>Contains</th><th>Runs on</th></tr>
  <tr><td><code>ostg_trafficgen-&lt;v&gt;-py3-none-any.whl</code><br>
          <span class="muted">~1.4 MB</span></td>
      <td><b>Both</b> server + client + CLI (Python source). Four entry
          points: <code>ostg-server</code>, <code>ostg-client</code>,
          <code>netgen-cli</code>, <code>ostg-docker-install</code>.</td>
      <td>Any platform with Python ≥3.9. <b>Server only runs on Linux
          (DPDK, VRFs, systemd are Linux-only).</b></td></tr>
  <tr><td><code>Netgen-TrafficGenerator-&lt;v&gt;.dmg</code><br>
          <span class="muted">~59 MB</span></td>
      <td><b>Client GUI only.</b> Single bundle:
          <code>Netgen Client.app</code> (PyQt5 + Python frozen).
          No Server.app on purpose — see below.</td>
      <td>macOS only</td></tr>
  <tr><td><code>Netgen-Client-&lt;v&gt;-windows.exe</code><br>
          <span class="muted">~73 MB</span></td>
      <td><b>Client GUI only</b> — PyInstaller one-file installer.</td>
      <td>Windows only</td></tr>
  <tr><td><code>Netgen-Client-&lt;v&gt;-linux-x86_64.AppImage</code><br>
          <span class="muted">~92 MB</span></td>
      <td><b>Client GUI only</b> — single-file portable.</td>
      <td>Any modern Linux distro</td></tr>
</table>

<h3>Pick the right one</h3>

<table>
  <tr><th>If you want to...</th><th>Download</th></tr>
  <tr><td>GUI on macOS, point at an existing server</td><td>the <b>DMG</b></td></tr>
  <tr><td>GUI on Windows</td><td>the <b>EXE</b></td></tr>
  <tr><td>GUI on Linux</td><td>the <b>AppImage</b></td></tr>
  <tr><td>Install or upgrade the server itself</td>
      <td>the <b>wheel</b> — use <code>install_ostg_complete.py</code>
          or the in-GUI <b>Help → Install / Upgrade Server</b> dialog</td></tr>
  <tr><td>Headless CLI / Docker / CI / scripted client install</td>
      <td>the <b>wheel</b> — <code>pip install</code> it directly</td></tr>
</table>

<h3>Why no Server bundle in the DMG / EXE / AppImage?</h3>

<p>The DMG was deliberately stripped of <code>Netgen Server.app</code>
in 0.2.5 (commit <code>e03bccf</code>). Netgen-server depends on
Linux-only kernel features that don't exist on macOS or Windows:</p>

<ul>
  <li>DPDK kernel modules (<code>vfio-pci</code>, <code>uio_pci_generic</code>)</li>
  <li>Per-device Linux VRFs (<code>ip link add type vrf</code>)</li>
  <li><code>iproute2</code> for VLAN / VXLAN subinterfaces</li>
  <li>systemd for service management</li>
  <li>FRR Docker containers (work on Docker Desktop but cross-platform is slow + restricted)</li>
</ul>

<p>Shipping a <code>Server.app</code> would mislead operators into
thinking they could run a full Netgen server on a Mac. The wheel
<i>does</i> contain the server code, but on macOS / Windows
<code>ostg-server</code> only works with <code>--no-dpdk</code>
(Scapy-only fallback) for protocol-correctness testing — no line
rate, no DPDK acceleration.</p>

<table>
  <tr><th>Command</th><th>macOS</th><th>Windows</th><th>Linux</th></tr>
  <tr><td><code>ostg-client</code></td><td>✅</td><td>✅</td><td>✅</td></tr>
  <tr><td><code>netgen-cli</code></td><td>✅</td><td>✅</td><td>✅</td></tr>
  <tr><td><code>ostg-server --no-dpdk</code> (Scapy)</td>
      <td>⚠️ protocol-correctness only</td>
      <td>⚠️ same caveat</td>
      <td>✅ full</td></tr>
  <tr><td><code>ostg-server</code> (DPDK)</td>
      <td>❌</td><td>❌</td><td>✅</td></tr>
</table>

<h2>3. CLI install (deep / scripted)</h2>

<h3>3a. What files do I actually need?</h3>

<p><b>Two files</b>, both must end up on the operator's machine before
the install can start:</p>

<table>
  <tr><th>File</th><th>Why</th><th>Where it ships</th></tr>
  <tr><td><code>ostg_trafficgen-&lt;v&gt;-py3-none-any.whl</code></td>
      <td>The Python runtime (server, client, CLI, bundled
          <code>resources/dpdk/</code> + <code>ostg_docker/</code> +
          <code>Dockerfile.frr</code> as package data).</td>
      <td>GitHub Releases asset; also bundled inside the
          <code>.dmg</code> / <code>.exe</code> / AppImage.</td></tr>
  <tr><td><code>install_ostg_complete.py</code></td>
      <td>The OS-level orchestrator — installs Docker, Python 3.10,
          perftest, rdma-core, DPDK build deps, systemd unit. <b>NOT
          in the wheel today</b>; needs to be shipped alongside it.</td>
      <td>Repo root (clone the repo or copy this single file out).
          Also auto-uploaded by the in-GUI Fresh Install dialog from
          its own bundled copy.</td></tr>
</table>

<p>The 4 optional files (<code>Dockerfile.frr</code>,
<code>requirements.txt</code>, <code>resources/dpdk/</code>,
<code>ostg_docker/</code>) are <i>extracted from inside the wheel</i>
during the install if not present alongside the installer. Operator
only needs to coordinate them when shipping patched versions for
testing.</p>

<h3>3b. One-shot run</h3>

<pre># From a fresh checkout — build the wheel locally (gitignored)
python3 -m build --wheel

# Install on a target host (root credentials required)
python3 install_ostg_complete.py -H &lt;host&gt; -p &lt;password&gt;</pre>

<p>That's it. ~15-45 minutes on a fresh box (most of which is the DPDK build).
At the end you have netgen-server running as a systemd unit, listening on
port 5050, and a <code>tx_worker</code> binary ready to drive line-rate
DPDK streams.</p>

<h3>3c. Wheel-only path (today: not yet supported)</h3>

<p>A user with ONLY <code>ostg_trafficgen-&lt;v&gt;-py3-none-any.whl</code>
in hand can <code>pip install</code> it successfully, but
<code>ostg-server</code> won't actually start — Docker, perftest,
DPDK, the systemd unit, and Python 3.10 itself aren't there yet, and
the wheel has no way to install them. Use one of the in-GUI Fresh
Install flow (§1), the GitHub-release download flow (§2), or the
git-clone CLI flow (§3b) for now.</p>

<h2>4. What gets installed, in order</h2>

<p>The orchestrator runs these steps in sequence on the target. Each
shells out via the hardened <code>_apt_install</code> /
<code>_install_apt_keyring</code> helpers (see notes at the end of this
table) so noninteractive installs survive conffile prompts and modern
GnuPG 2.x.</p>

<table>
  <tr><th>Step</th><th>What it does</th></tr>
  <tr><td><b>_heal_dpkg_state</b><br><span class="ok">NEW in 0.3.16</span></td>
      <td>Pre-flight that recovers from a prior failed install:
          <code>dpkg --audit</code> → force-remove half-installed
          Docker stack if needed →
          <code>dpkg --configure -a --force-confdef --force-confold</code>
          → <code>apt-get clean / update</code>. No-op on a clean box.
          Eliminates the "user must SSH in and run apt --fix-broken
          install" recovery step after a failed run.</td></tr>
  <tr><td><b>cleanup_old_install</b></td>
      <td>Wipes legacy <code>/opt/OSTG/</code> artifacts and the old
          <code>ostg-server.service</code> systemd unit. No-op on a
          fresh box.</td></tr>
  <tr><td><b>install_system_dependencies</b></td>
      <td>apt baseline: <code>python3-pip</code>, build-essential, git,
          curl, wget, gnupg, lsb-release, sshpass, etc. All apt installs
          go through <code>_apt_install</code> which appends
          <code>-o Dpkg::Options::=--force-confdef -o
          Dpkg::Options::=--force-confold</code> to handle conffile
          diffs without prompting.</td></tr>
  <tr><td><b>_install_python_3_10</b></td>
      <td>Adds deadsnakes PPA on Ubuntu →
          <code>_apt_install("python3.10 python3.10-venv")</code>.
          RHEL/Fedora use the distro's Python package directly.</td></tr>
  <tr><td><b>_bootstrap_pip_for_python310</b></td>
      <td>Three-step fallback chain to put pip on the new Python:
          <code>python3.10 -m ensurepip --upgrade --default-pip</code>
          → distro <code>python3-pip</code> via apt/dnf → final
          fallback <code>get-pip.py --ignore-installed</code> (the
          <code>--ignore-installed</code> avoids the "Cannot uninstall
          packaging 24.0" failure on Debian-managed packages).</td></tr>
  <tr><td><b>install_python_dependencies</b></td>
      <td>Python deps from <code>requirements.txt</code> (matches
          wheel metadata).</td></tr>
  <tr><td><b>install_docker</b><br><span class="ok">hardened in 0.3.16</span></td>
      <td>Adds Docker's apt repo with the new
          <code>_install_apt_keyring</code> helper —
          <code>curl</code> the gpg key to a tmp file then
          <code>gpg --batch --no-tty --yes --dearmor</code> to
          <code>/etc/apt/keyrings/docker.gpg</code>. The
          <code>--batch --no-tty</code> flags prevent the
          "<code>gpg: cannot open '/dev/tty'</code>" failure on
          nohup-detached installs. Then
          <code>_apt_install("docker-ce docker-ce-cli containerd.io
          docker-compose-plugin")</code> — the
          <code>--force-confdef</code> flags handle
          <code>containerd.io</code>'s <code>config.toml</code>
          diff (the v0.3.16 fresh-install failure site).</td></tr>
  <tr><td><b>_install_rdma_userspace</b><br><span class="ok">split in 0.3.16</span></td>
      <td><b>Two-pass install</b> to handle Mellanox-MOFED-only
          packages on hosts without the MOFED apt repo:<br/>
          <b>Pass 1 (CORE, must succeed):</b>
          <code>_apt_install("perftest rdma-core libibverbs-dev")</code>
          — these are in Ubuntu main, so they always install on a
          reachable box. <code>perftest</code> ships ib_send_bw,
          ib_write_bw, ib_read_bw + their _lat variants.<br/>
          <b>Pass 2 (MOFED-optional, warns on failure):</b>
          <code>_apt_install("libmlx5-dev", check=False)</code> —
          this header only lives in Mellanox's MOFED apt repo. Pre-fix,
          all 4 packages were lumped together; apt's all-or-nothing
          batching meant a MOFED-less host got NONE of them, breaking
          RDMA entirely even though 3 of 4 were available.</td></tr>
  <tr><td><b>install_dpdk_runtime</b><br><span class="ok">★ default ON</span></td>
      <td>Runs <code>resources/dpdk/install_dpdk.sh --auto</code> on
          the target. Installs apt prereqs (build-essential, meson,
          ninja-build, pkg-config, libnuma-dev, libelf-dev, libpcap-dev,
          libibverbs-dev), clones DPDK source, configures with meson
          (<code>-Ddisable_drivers=net/mana</code>), builds with ninja,
          installs to <code>/usr/local/lib/x86_64-linux-gnu</code>,
          <code>ldconfig</code>, then compiles <code>tx_worker</code>
          against the freshly-installed DPDK. The DPDK script ALSO has
          the same <code>--force-confdef</code> / <code>--force-confold</code>
          + a separate <code>mlx5_install_cmd</code> with
          <code>log_warning</code> fallback, mirroring the Python-side
          two-pass split.</td></tr>
  <tr><td><b>install_ai_dependencies</b></td>
      <td>Optional ML/AI helpers for the AI Assistant menu.</td></tr>
  <tr><td><b>install_ollama</b></td>
      <td>Local LLM runtime for the AI Assistant.</td></tr>
  <tr><td><b>install_ostg (wheel install)</b><br><span class="ok">hardened in 0.3.17</span></td>
      <td><code>pip3 install --force-reinstall --no-deps
          &lt;wheel&gt;</code> — primary pass. The
          <code>--force-reinstall</code> is critical for the case where
          a developer rebuilds the same-version wheel (e.g. shipping a
          fix without bumping the version): without it pip says
          "Requirement already satisfied" and silently keeps the old
          code on disk. <code>--no-deps</code> keeps the reinstall fast.
          A separate deps-only pass (no <code>--no-deps</code>) handles
          first-install dependency resolution. Falls back to
          <code>--ignore-installed</code> if a Debian-managed
          distutils package blocks uninstall.</td></tr>
  <tr><td><b>setup_docker_frr</b></td>
      <td>Builds the FRR sidecar Docker image used by emulated devices,
          with <code>--network=host</code> so the build can reach
          Alpine's apk mirrors on networks without a default Docker
          bridge.</td></tr>
  <tr><td><b>create_systemd_services</b></td>
      <td>Drops <code>netgen-server.service</code> into
          <code>/etc/systemd/system/</code> and enables it.</td></tr>
  <tr><td><b>start_ostg_services</b></td>
      <td><code>systemctl start netgen-server</code> + waits for the
          <code>/api/health</code> endpoint to come up.</td></tr>
  <tr><td><b>verify_installation</b></td>
      <td>Sanity checks: pip list, systemctl status, /api/health 200,
          <code>which perftest</code>, <code>ibv_devices</code>.</td></tr>
  <tr><td><b>test_frr_functionality</b></td>
      <td>Smoke-test FRR Docker container can spawn (skipped if Docker
          is missing).</td></tr>
</table>

<h3>4a. Helper notes</h3>

<p>Three helpers carry the install's hardening — they're chokepoints
for every install step that talks to apt or gpg, so a refactor can't
silently regress to the old behavior. Each is pinned by tests under
<code>tests/test_apt_noninteractive.py</code>,
<code>tests/test_apt_keyring_helper.py</code>,
<code>tests/test_dpkg_heal_preflight.py</code>,
<code>tests/test_rdma_install_split.py</code>,
<code>tests/test_pip_force_reinstall.py</code>:</p>

<ul>
  <li><b><code>_apt_install(packages, check=True, extra_opts="")</code></b>
      — wraps every <code>apt-get install -y</code> with
      <code>-o Dpkg::Options::=--force-confdef
            -o Dpkg::Options::=--force-confold</code>.
      Without these flags, conffile prompts from packages like
      <code>containerd.io</code> EOF the nohup-detached install. Use
      <code>check=False</code> for MOFED-optional packages.</li>
  <li><b><code>_install_apt_keyring(name, key_url)</code></b> —
      <code>curl</code> the gpg key to a tmp file, then
      <code>gpg --batch --no-tty --yes --dearmor</code> to
      <code>/etc/apt/keyrings/&lt;name&gt;.gpg</code>. The
      <code>--batch --no-tty</code> avoids the
      <code>cannot open '/dev/tty'</code> failure modern GnuPG 2.x
      triggers in detached sessions.</li>
  <li><b><code>_heal_dpkg_state()</code></b> — pre-flight that recovers
      from prior failed installs (half-installed Docker stack,
      pending conffile resolution). Removes the operator-must-SSH-in
      manual recovery step entirely.</li>
</ul>

<h2>5. CLI flags</h2>

<table>
  <tr><th>Flag</th><th>Effect</th></tr>
  <tr><td><code>-H, --host</code></td>
      <td>Remote host to install on. Omit for local install (must run as
          root).</td></tr>
  <tr><td><code>-u, --user</code></td><td>Remote user (default: <code>root</code>).</td></tr>
  <tr><td><code>-p, --password</code></td><td>Remote password (required when
          <code>-H</code> is given).</td></tr>
  <tr><td><code>--no-dpdk</code></td>
      <td>Skip the DPDK runtime install step entirely. Pass this on hosts
          that won't generate traffic — devbox-only installs, hosts with no
          DPDK-capable NIC.</td></tr>
  <tr><td><code>--skip-dpdk-build</code></td>
      <td>Install DPDK apt prerequisites only — don't compile DPDK or
          tx_worker. Useful when DPDK is already installed system-wide and
          you just want netgen-server's apt deps in place. The tx_worker
          binary won't be built; you can build it later from the
          <code>/admin</code> portal.</td></tr>
</table>

<p class="muted">Defaults preserve "install DPDK" behavior — no flag needed
for normal use.</p>

<h2>6. Tolerant of failures</h2>

<p>If the DPDK build fails (kernel-header / libibverbs version mismatch,
flaky apt mirror, NIC vendor lib missing), the rest of the install
<i>continues</i>. You'll see in the log:</p>

<pre>WARNING: DPDK install exited rc=N. Continuing — netgen-server will
         start fine without DPDK; streams that need it will fall back
         to the Scapy/kernel path.
WARNING: Diagnose with: ssh root@&lt;host&gt; 'tail -200 /var/log/netgen-install-dpdk.log'
WARNING: Or retry from the /admin portal once netgen-server is up.</pre>

<p>Even worst-case, you get a working netgen-server with two clear DPDK
recovery paths:</p>
<ol>
  <li>Read the log on the target: <code>tail -f /var/log/netgen-install-dpdk.log</code></li>
  <li>Open <code>http://&lt;server&gt;:5050/admin</code> → Card "DPDK Runtime"
      → click <b>Install DPDK</b>. Same script, retried interactively.</li>
</ol>

<h2>7. Sanity-check after install</h2>

<pre>ssh root@&lt;server&gt; 'systemctl is-active netgen-server'      # active
ssh root@&lt;server&gt; 'curl -s http://localhost:5050/api/health' # 200 OK
ssh root@&lt;server&gt; 'ls -la /opt/netgen/resources/dpdk/tx_worker/build/tx_worker'
                                                            # tx_worker binary present
ssh root@&lt;server&gt; 'pkg-config --modversion libdpdk'         # e.g. 23.11.0</pre>

<p>Then open <code>http://&lt;server&gt;:5050/admin</code> in a browser to
confirm the runtime cards (DPDK Runtime, Hugepages, Network Interfaces)
all show green.</p>

<h2>8. What lives where on the target</h2>

<table>
  <tr><th>Path</th><th>Contents</th></tr>
  <tr><td><code>/opt/netgen/</code></td>
      <td>Install root. Contains the wheel, systemd unit, FRR Docker artifacts.</td></tr>
  <tr><td><code>/opt/netgen/resources/dpdk/</code></td>
      <td>DPDK helper scripts (<code>dpdk_bind.sh</code>,
          <code>install_dpdk.sh</code>, etc.) and the <code>tx_worker/</code>
          source + build directory.</td></tr>
  <tr><td><code>/opt/netgen/resources/dpdk/tx_worker/build/tx_worker</code></td>
      <td>The compiled multi-queue tx_worker binary. The runtime path is
          fixed; the launcher resolves it here first.</td></tr>
  <tr><td><code>/usr/local/lib/python3.13/dist-packages/run_tgen_server.py</code></td>
      <td>The Flask server, installed by <code>pip install</code>ing the
          wheel.</td></tr>
  <tr><td><code>/usr/local/lib/python3.13/dist-packages/utils/</code></td>
      <td>Server-side Python modules (DPDK launcher, stream tracker,
          SQLite database).</td></tr>
  <tr><td><code>/usr/local/lib/x86_64-linux-gnu/librte_*.so*</code></td>
      <td>DPDK runtime libraries (installed by <code>install_dpdk.sh</code>
          via <code>meson install</code>).</td></tr>
  <tr><td><code>/etc/systemd/system/netgen-server.service</code></td>
      <td>Systemd unit. Runs <code>ostg-server</code> (entry point from the
          wheel) on port 5050.</td></tr>
  <tr><td><code>/var/log/netgen-install-dpdk.log</code></td>
      <td>Stdout/stderr from <code>install_dpdk.sh</code>. Tail this if the
          DPDK build acted up.</td></tr>
</table>

<h2>9. Reinstall / upgrade</h2>

<p>Four paths, fastest to heaviest:</p>

<table>
  <tr><th>Method</th><th>Time</th><th>When to use</th></tr>
  <tr><td><b>Help → Install / Upgrade Server → Upgrade tab</b><br>
          <span class="muted">(NEW in 0.2.6)</span></td>
      <td>30–60 s</td>
      <td>Just rolling a new wheel onto a running server. No SSH, no
          re-running the full provisioning flow. See section&nbsp;1
          above.</td></tr>
  <tr><td><b>Manual SSH one-liner</b> ★</td>
      <td>~30 s</td>
      <td>The Upgrade tab is broken or unreachable
          (firewall, proxy, server version with the latent
          <code>NameError</code> bug); scripted CI/CD pipelines that
          want explicit control over each step.</td></tr>
  <tr><td><b>Step-by-step on the box</b></td>
      <td>~1 min</td>
      <td>Production lab box where you want to see each step's output
          before proceeding — confirm version before, verify after,
          have a clean rollback path ready.</td></tr>
  <tr><td><b><code>install_ostg_complete.py</code> rerun</b></td>
      <td>5–10 min</td>
      <td>Full re-provision (idempotent — <code>cleanup_old_install</code>
          wipes legacy artifacts). Use when you've also changed DPDK
          config, systemd unit, or the FRR Docker image.</td></tr>
</table>

<h3>9a. Manual SSH one-liner</h3>

<p>One terminal, two commands:</p>

<pre>scp ~/Downloads/ostg_trafficgen-&lt;v&gt;-py3-none-any.whl root@&lt;host&gt;:/tmp/
ssh root@&lt;host&gt; 'pip3 install --upgrade --force-reinstall --no-deps \
  /tmp/ostg_trafficgen-&lt;v&gt;-py3-none-any.whl \
  &amp;&amp; systemctl restart netgen-server \
  &amp;&amp; sleep 2 \
  &amp;&amp; curl -fsS http://127.0.0.1:5050/api/health &amp;&amp; echo'</pre>

<p>What each piece does:</p>
<ul>
  <li><code>scp</code> — copies the wheel to <code>/tmp/</code> on the
      target.</li>
  <li><code>pip3 install --upgrade --force-reinstall --no-deps</code>
      — installs the new wheel using the system Python (matches what
      the server runs under). <code>--no-deps</code> skips
      re-resolving / re-installing dependencies (faster + safer:
      your existing deps are already correct).</li>
  <li><code>systemctl restart netgen-server</code> — bounces the
      running process so it loads the new code in memory.
      <b>Critical:</b> without this, pip-installing the new wheel
      only updates files on disk; the live process keeps running
      the OLD code until restart.</li>
  <li><code>curl /api/health</code> — confirms the new instance came
      back up.</li>
</ul>

<h3>9b. Step-by-step on the box</h3>

<p>SSH in first, then run each step interactively so you can see
what's happening:</p>

<pre>ssh root@&lt;host&gt;</pre>

<pre><span style="color:#6b7280;"># 1. Confirm current version</span>
pip3 show ostg-trafficgen | head -2
<span style="color:#6b7280"># Name: ostg-trafficgen
# Version: 0.2.9        ← what's installed now</span>

<span style="color:#6b7280"># 2. Fetch the new wheel from GitHub releases (or scp from laptop)</span>
wget -O /tmp/ostg_trafficgen-0.2.11-py3-none-any.whl \
  https://github.com/amishagrawal2001-arch/netgen/releases/download/v0.2.11/ostg_trafficgen-0.2.11-py3-none-any.whl

<span style="color:#6b7280"># 3. Confirm the wheel is valid + peek at its metadata</span>
ls -la /tmp/ostg_trafficgen-*.whl
unzip -p /tmp/ostg_trafficgen-*.whl '*/METADATA' | head -10

<span style="color:#6b7280"># 4. Upgrade</span>
pip3 install --upgrade --force-reinstall --no-deps \
  /tmp/ostg_trafficgen-0.2.11-py3-none-any.whl

<span style="color:#6b7280"># 5. Verify pip sees the new version</span>
pip3 show ostg-trafficgen | head -2
<span style="color:#6b7280"># Version: 0.2.11       ← upgraded ✓</span>

<span style="color:#6b7280"># 6. Restart so the running process loads the new code</span>
systemctl restart netgen-server

<span style="color:#6b7280"># 7. Confirm the new instance is healthy</span>
systemctl status netgen-server --no-pager | head -7
curl -fsS http://127.0.0.1:5050/api/health</pre>

<p>The critical step is <b>#6</b> — <code>pip3 install</code> alone
leaves the running server unchanged. Skipping the restart was a real
footgun until 0.2.8 added auto-restart to
<code>install_ostg_complete.py</code>.</p>

<h3>9c. Rollback</h3>

<p>Wheels stick around forever (in <code>~/Downloads/</code> on your
laptop and on GitHub Releases). Rollback is always one
<code>pip install</code> away:</p>

<pre>ssh root@&lt;host&gt; 'pip3 install --upgrade --force-reinstall --no-deps \
  /tmp/ostg_trafficgen-&lt;previous-version&gt;-py3-none-any.whl \
  &amp;&amp; systemctl restart netgen-server'</pre>

<p>Tip: keep the last two or three wheels in
<code>/tmp/</code> on the target box — they're tiny (~1.4 MB each)
and instant to install when you need to bisect a regression.</p>

<h3>9d. Full re-provision (rare)</h3>

<pre><span style="color:#6b7280"># Bump pyproject.toml version locally, then:</span>
python3 -m build --wheel
python3 install_ostg_complete.py -H &lt;host&gt; -p &lt;password&gt;</pre>

<p class="muted">For a quick redeploy without re-running the entire
flow, the file-by-file <code>scp</code> +
<code>systemctl restart netgen-server</code> pattern still works
during development.</p>

<h3>9e. When the Upgrade tab is broken</h3>

<p>The Upgrade tab calls <code>/api/admin/upgrade_wheel</code> on the
server. If that endpoint is broken (e.g. the
<code>NameError: name 'sys' is not defined</code> bug latent in
0.2.6 → 0.2.10), the dialog can't fix itself — chicken and egg.
Recovery paths:</p>

<ul>
  <li><b>Manual SSH one-liner (9a)</b> — bypasses the broken endpoint
      entirely; uses pip directly on the target.</li>
  <li><b>Fresh Install via SSH tab</b> — also bypasses the broken
      endpoint. Uses <code>install_ostg_complete.py</code> end-to-end
      via paramiko/sftp. The auto-restart in
      <code>start_ostg_services</code> (0.2.8+) handles the
      "running process needs to bounce" step.</li>
  <li><b>Once on 0.2.11+, the Upgrade tab works again</b> — the
      <code>sys</code> import is fixed. Subsequent upgrades can use
      Tab 1 cleanly.</li>
</ul>

<h2>10. RDMA / perftest setup <span class="ok">NEW in 0.3.12</span> <span class="ok">parser fixes 0.3.13</span></h2>

<p class="muted"><b>Status check after install:</b> open
<code>Tools → RDMA → RDMA Devices…</code>. v0.3.13+ shows the real
<b>perftest version</b> (e.g. <code>perftest 24.04.0-0.41 present</code>
on Ubuntu 24.04 + MOFED) and the real <b>per-port MTU</b> in bytes
(e.g. 1024, 4096). Pre-v0.3.13 showed <code>perftest installed (?)</code>
and <code>MTU: 0 B</code> due to parser bugs in
<code>utils/rdma_perf.py</code> — upgrade the wheel to see correct
values without changing any install state.</p>

<p>RDMA traffic generation (<code>Tools → RDMA → Blast a RDMA Flow…</code>
+ per-stream <code>Engine: RDMA (perftest)</code>) shells out to the
<b>perftest</b> suite (<code>ib_send_bw</code>, <code>ib_write_bw</code>,
<code>ib_read_bw</code>, and the <code>_lat</code> variants). On fresh
installs from 0.3.12+, perftest is <b>auto-installed</b> on both code
paths:</p>

<table>
  <tr><th>Install path</th><th>Where perftest comes from</th></tr>
  <tr><td><code>install_ostg_complete.py</code> (CLI, in-GUI SSH installer)</td>
      <td><code>_install_rdma_userspace()</code> runs as part of
          <code>install_system_dependencies</code> — installs
          <code>perftest rdma-core libibverbs-dev libmlx5-dev</code>
          (apt; analogous packages on dnf/yum/apk/zypper). Runs on
          <i>every</i> install, including <code>--no-dpdk</code>.</td></tr>
  <tr><td><code>install_dpdk.sh --auto</code> (called by the
          <code>install_dpdk_runtime</code> step or the
          <b>Tools → DPDK → Install DPDK</b> admin action)</td>
      <td>Apt prereqs list includes <code>perftest</code> alongside
          <code>libibverbs-dev libmlx5-dev rdma-core</code> — so any
          DPDK install lands RDMA capability too.</td></tr>
</table>

<p>Either path leaves <code>ib_send_bw</code> on PATH. The installer
runs <code>which ib_send_bw</code> after the package install and
warns explicitly if it didn't land — surfaces the gap before the
operator opens Tools → RDMA and gets a "perftest not installed"
error.</p>

<h3>10·m. Manual install (if the auto-install was skipped)</h3>

<p>Pre-0.3.12 installs, or hosts where the auto-install warning fired
because the distro's package mirror was missing perftest at the time:</p>

<pre># Debian / Ubuntu
apt install perftest rdma-core

# RHEL 9+ / Fedora / Rocky 9+ — perftest in main repo
dnf install perftest rdma-core

# RHEL 7 / 8 / CentOS — perftest from EPEL
yum install -y epel-release &amp;&amp; yum install -y perftest rdma-core

# Alpine
apk add perftest rdma-core

# openSUSE
zypper install -y perftest rdma-core</pre>

<p class="muted">On hosts where DPDK is already installed, the
<code>rdma-core</code> + <code>libibverbs-dev</code> +
<code>libmlx5-dev</code> packages are typically already present (pulled
in by DPDK's mlx5 PMD prereqs), so a manual install only needs
<code>perftest</code> on top.</p>

<h3>10a. Sanity-check after install</h3>

<pre># perftest binaries on PATH?
ssh root@&lt;server&gt; 'which ib_send_bw ib_write_bw ib_read_bw'
# → /usr/bin/ib_send_bw, ...

# What does the kernel see in /sys/class/infiniband?
ssh root@&lt;server&gt; 'ls -1 /sys/class/infiniband/'
# → mlx5_0   (or similar — empty list means no RDMA support loaded)

# Per-port state + link layer + GIDs
ssh root@&lt;server&gt; 'for d in /sys/class/infiniband/*/ports/*; do
  echo "$d  state=$(cat $d/state)  link=$(cat $d/link_layer)
        rate=$(cat $d/rate)  mtu=$(cat $d/active_mtu)"; done'</pre>

<p>In the GUI, the same probes are surfaced under
<code>Tools → RDMA → RDMA Devices…</code> — picks up the device list +
checks perftest install state in one click per selected TG.</p>

<h3>10b. RoCEv2 vs InfiniBand link layer</h3>

<table>
  <tr><th><code>link_layer</code></th><th>Means</th><th>Operator action</th></tr>
  <tr><td><code>Ethernet</code></td>
      <td><b>RoCEv2</b> — RDMA over IPv4/IPv6 UDP. Most common in
          modern data centres.</td>
      <td>Configure an IPv4 (or IPv6) address on the RDMA NIC. Confirm
          the peer TG is reachable via plain <code>ping</code>. perftest
          uses GID index 3 by default for RoCEv2-IPv4 on Mellanox.</td></tr>
  <tr><td><code>InfiniBand</code></td>
      <td><b>Native IB</b> — uses LID-based routing on an IB fabric.
          Subnet manager (OpenSM) must be running somewhere on the
          fabric.</td>
      <td>No IP needed; pass the peer's LID instead of an IP. perftest
          uses GID index 0 by default for native IB.</td></tr>
</table>

<h3>10c. Common install gotchas</h3>

<ul>
  <li><b><code>perftest</code> not found</b> after apt install →
      the package landed but the binaries are under
      <code>/usr/local/bin</code> or similar; rerun the install
      with the distro's official package source, not a custom
      build.</li>
  <li><b><code>/sys/class/infiniband/</code> empty</b> on a host that
      has an RDMA NIC → kernel modules not loaded. Run
      <code>modprobe mlx5_ib ib_uverbs rdma_cm rdma_ucm</code> (Mellanox)
      or <code>modprobe irdma ib_uverbs rdma_cm</code> (Intel).
      Persist via <code>/etc/modules-load.d/rdma.conf</code>.</li>
  <li><b>Containerised TG (Docker)</b> → mount
      <code>/sys/class/infiniband</code> and
      <code>/dev/infiniband</code> into the container with
      <code>--device</code> and <code>-v</code>; without them the GUI
      reports "no RDMA devices" even on hosts where the HCA works
      fine outside the container.</li>
  <li><b>Latency tests need root</b> on some HCAs that gate
      <code>IBV_QPT_UD</code> / hardware timestamps. If <code>ib_send_lat</code>
      runs cleanly from the shell but the per-stream engine returns
      "perftest exited rc=1", make sure netgen-server is running as
      root (which it is by default via the systemd unit).</li>
</ul>
"""


def show_install_guide(parent=None):
    """Open the Installation Guide dialog. Reachable from Help → Install Guide."""
    _open_help_dialog(parent, "Netgen Server — Installation Guide", _INSTALL_GUIDE_HTML)


# =============================================================================
# API Guide — REST cheatsheet for the netgen-server traffic API
# =============================================================================
# Reachable from Help → API Guide. Worked examples for every packet type
# supported by the engine, both Scapy/kernel path and DPDK path, plus the
# stream-control + stats endpoints. Single source of truth for "how do I
# script traffic against this thing."

_API_GUIDE_HTML = r"""
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         color: #1f2937; line-height: 1.55; font-size: 12px; }
  h1 { color: #1e40af; font-size: 20px; margin: 0 0 8px 0; }
  h2 { color: #374151; font-size: 15px; margin-top: 22px;
       border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
  h3 { color: #4b5563; font-size: 13px; margin-top: 14px; }
  h4 { color: #6b7280; font-size: 12px; margin-top: 10px;
       text-transform: uppercase; letter-spacing: 0.4px; }
  p, li { color: #374151; }
  code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
         background: #f3f4f6; padding: 1px 5px; border-radius: 3px;
         font-size: 11px; color: #1e3a8a; }
  pre { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
        background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 4px;
        padding: 10px; font-size: 11px; color: #111827;
        white-space: pre; }
  table { border-collapse: collapse; margin-top: 6px; font-size: 11px; }
  th, td { border: 1px solid #d1d5db; padding: 5px 9px; text-align: left;
           vertical-align: top; }
  th { background: #f3f4f6; color: #374151; font-weight: 600; }
  td.method { font-family: ui-monospace, monospace; font-weight: 600;
              color: #1d4ed8; white-space: nowrap; }
  .dpdk { color: #1d4ed8; font-weight: 600; }
  .scapy { color: #6b7280; font-weight: 600; }
  .muted { color: #6b7280; font-size: 11px; }
  .warn { background: #fef3c7; border-left: 3px solid #d97706;
          padding: 8px 12px; margin: 10px 0; }
</style>

<h1>Netgen Server — REST API Guide</h1>
<p class="muted">The Netgen API is a Flask app on port 5050. Auth is
opt-in (see §15) — set <code>NETGEN_AUTH_TOKEN</code> server-side and
every endpoint except <code>/api/health</code> requires
<code>Authorization: Bearer &lt;token&gt;</code>. All requests/responses
are JSON. Examples below are copy-pasteable curl commands you can run
from any host that can reach <code>http://&lt;server&gt;:5050</code>.</p>
<p class="muted">For CI / scripting without a GUI, every workflow in
this guide is also available via the <code>netgen-cli</code> headless
companion — see §20.</p>

<h2>1. Endpoint summary</h2>

<table>
  <tr><th>Method</th><th>Endpoint</th><th>Purpose</th></tr>
  <tr><td class="method">POST</td><td><code>/api/traffic/start</code></td>
      <td>Launch one or more streams.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/traffic/stop</code></td>
      <td>Stop running streams by id.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/traffic/restart</code></td>
      <td>Stop + start a stream in one call.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/streams/stats</code></td>
      <td>Live per-stream stats (tx/rx counts, rates, status).</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/streams/load</code></td>
      <td>Persisted streams from the SQLite stream database.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/streams/save</code></td>
      <td>Force a sync of the in-memory stream tracker to disk.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/interfaces</code></td>
      <td>Per-interface kernel netdev counters + link state.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/dpdk/recommend</code></td>
      <td>Suggested <code>dpdk_tx_cores</code> for an iface + frame_size + pps target.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/dpdk/status</code></td>
      <td>DPDK runtime status (libs installed, hugepages, IOMMU, tx_worker / DPDK ABI version).</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/dpdk/interfaces</code></td>
      <td>List of interfaces currently bound to a DPDK driver (<code>vfio-pci</code> or bifurcated).</td></tr>
  <tr><td class="method">POST</td><td><code>/api/dpdk/bind</code> /
                                       <code>unbind</code></td>
      <td>Bind/unbind a NIC to <code>vfio-pci</code> (Broadcom/Intel only).</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/dpdk/verify</code></td>
      <td>Confirm a NIC is functionally usable by DPDK (EAL probe).</td></tr>
  <tr><td class="method">POST</td><td><code>/api/dpdk/hugepages</code></td>
      <td>Allocate hugepages. Schema:
          <code>{"num_pages": N, "page_size": "2MB"}</code>. <b>v0.3.11:</b>
          server accepts <code>"2MB"</code> only; <code>"1GB"</code>
          requires kernel boot-time reservation.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/dpdk/iommu</code></td>
      <td>Enable IOMMU on the host (reboot required to take effect).</td></tr>
  <tr><td class="method">POST</td><td><code>/api/dpdk/load_modules</code></td>
      <td>Load <code>vfio</code> + <code>vfio-pci</code> kernel modules.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/admin/bind_history</code></td>
      <td>Map of pci_bdf → original interface name + kernel_driver before
          vfio-pci binding. Used by the GUI to label vfio-bound NICs by
          their pre-bind name (e.g. <code>"eth0 (DPDK)"</code>).</td></tr>
  <tr><td class="method">POST</td><td><code>/api/admin/install_dpdk</code></td>
      <td>Server-side DPDK + tx_worker install. Async; tail via
          <code>/api/admin/install_dpdk/log</code>.</td></tr>

  <tr><th colspan="3" style="background:#fef9c3;">RDMA / perftest (v0.3.12, hardened in v0.3.13, HCA caps in v0.3.15)</th></tr>
  <tr><td class="method">GET</td> <td><code>/api/rdma/devices</code></td>
      <td>Enumerate RDMA HCAs via <code>/sys/class/infiniband</code> +
          <code>ibv_devinfo -v -d &lt;dev&gt;</code>. Returns per-port
          state, link_layer (Ethernet vs InfiniBand), rate, active
          MTU, GIDs. Empty list when no RDMA support — not an error.
          <br/><b>MTU contract (v0.3.13):</b> the <code>mtu</code> field
          is always BYTES (e.g. 1024, 4096) regardless of how the
          kernel writes <code>active_mtu</code>. Modern Mellanox /
          mlx5 5.x+ kernels write a bare IB MTU enum digit
          (<code>1</code> → 256 B … <code>5</code> → 4096 B per IBA
          §3.5.3); v0.3.12 returned 0 for these.
          <br/><b>HCA caps (v0.3.15):</b> each device entry now
          carries <code>max_qp</code>, <code>max_qp_wr</code>,
          <code>max_cq</code>, <code>max_cqe</code>,
          <code>max_mr</code>, <code>max_pd</code>,
          <code>max_sge</code> — raw libibverbs caps from
          <code>ibv_devinfo</code>. <code>None</code> when ibv_devinfo
          isn't installed or perms blocked the probe (graceful
          degrade; rest of the device payload is unaffected).
          Surfaced in <code>Tools → RDMA → RDMA Devices…</code> as a
          "HCA caps" line per device.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/rdma/perftest/installed</code></td>
      <td>Probe PATH for <code>ib_send_bw</code>, <code>ib_write_bw</code>,
          <code>ib_read_bw</code>, and their <code>_lat</code> siblings.
          Returns <code>{installed: bool, tools: {test_id: path|null},
          version: str|null}</code>.
          <br/><b>Version probe (v0.3.13):</b> five-stage fallback chain
          — <code>&lt;tool&gt; --version</code> → <code>-V</code> →
          <code>dpkg -s perftest</code> → <code>rpm -q perftest</code>
          → <code>apk info -v perftest</code>. First non-empty result
          wins. <code>version: null</code> only when <i>every</i> probe
          fails (rare; GUI then renders "perftest installed" without
          a version suffix). On the newer Ubuntu 24.04 + MOFED perftest
          24.04.0-0.41 build, <code>--version</code> returns nothing
          useful so the dpkg probe is what works in practice.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/rdma/perftest/start</code></td>
      <td>Spawn a perftest job. Body: <code>{role: "server"|"client",
          test: "send_bw"|…|"read_lat", device, ib_port, peer_addr (client
          only), msg_size, qp_count, duration, mtu, gid_index, tx_depth,
          bidirectional, cpu_util, handshake_id?, note?}</code>.
          Response on success: <code>{status: "started", job_id,
          handshake_id, listen_port, listen_addr (server only), cmd,
          tool, record}</code>.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/rdma/perftest/stop</code></td>
      <td>SIGTERM the perftest child. Body: <code>{job_id, forget_pair: bool}</code>.
          When <code>forget_pair</code> is true, also drops the pairing
          tag from the handshake registry.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/rdma/perftest/jobs</code></td>
      <td>List every perftest job (running + recently-finished) on this
          TG. Each entry annotated with <code>handshake_id</code> so the
          GUI can group the two halves of one Blast RDMA Flow.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/rdma/perftest/job/&lt;id&gt;</code></td>
      <td>Single job's full state — including parsed BW / latency, local
          + remote QPN/PSN/LID, and a capped stdout tail for debugging.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/rdma/handshakes</code></td>
      <td>Every active pairing tag on this TG.</td></tr>
  <tr><td class="method">GET/DELETE</td>
      <td><code>/api/rdma/handshakes/&lt;id&gt;</code></td>
      <td>GET → full pairing record; DELETE → forget the pairing
          (does <b>not</b> stop the underlying jobs — caller stops each
          side via <code>/api/rdma/perftest/stop</code>).</td></tr>

  <tr><td class="method">POST</td><td><code>/api/rfc2544/start</code></td>
      <td>Kick off an RFC 2544 §26.1 throughput test (binary search per frame size).</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/rfc2544/progress</code></td>
      <td>Poll for RFC 2544 progress + converged per-frame-size results.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/rfc2544/stop</code></td>
      <td>Cooperatively cancel an in-flight RFC 2544 test.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/latency/stats</code></td>
      <td>One-way latency rolling stats (min/avg/p50/p99/max) for an iface.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/latency/stop</code></td>
      <td>Stop the latency sampler for an iface (or all if no iface).</td></tr>
  <tr><td class="method">GET</td> <td><code>/admin</code></td>
      <td>Single-page web UI for runtime configuration.</td></tr>
  <tr><th colspan="3" style="background:#eff6ff; color:#1d4ed8;">Device lifecycle &amp; control plane</th></tr>
  <tr><td class="method">POST</td><td><code>/api/device/apply</code></td>
      <td>Create or reconfigure a device: VLAN subif, IP, protocols, VRF.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/device/start</code></td>
      <td>Resume a stopped device (restarts its FRR container, preserves VRF).</td></tr>
  <tr><td class="method">POST</td><td><code>/api/device/stop</code></td>
      <td>Stop a device's FRR container (Exited state — container + VRF preserved).</td></tr>
  <tr><td class="method">POST</td><td><code>/api/device/remove</code></td>
      <td>Tear down: container removed, VRF deleted, DB row dropped.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/device/arp/&lt;id&gt;</code></td>
      <td>Live ARP/ND probe (in-VRF) — IPv4 / IPv6 / gateway reachability.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/arp/monitor/force-check</code></td>
      <td>Re-probe every running device now and persist to DB.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/device/bgp/{start,stop,configure}</code></td>
      <td>Per-device BGP lifecycle (issues VRF-scoped vtysh into the device's FRR container).</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/bgp/status/&lt;id&gt;</code></td>
      <td>Established / Active / Idle, per-AF, scoped to the device's VRF.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/bgp/monitor/force-check</code></td>
      <td>Re-probe every BGP device now; writes Established state to DB.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/device/ospf/{start,stop,configure}</code></td>
      <td>Per-device OSPFv2 + OSPFv3 lifecycle.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/ospf/status/&lt;id&gt;</code></td>
      <td>Adjacency state, neighbor list (VRF-scoped).</td></tr>
  <tr><td class="method">POST</td><td><code>/api/ospf/monitor/force-check</code></td>
      <td>Refresh OSPF state for every running device.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/device/isis/{start,stop,configure}</code></td>
      <td>Per-device IS-IS lifecycle.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/isis/status/&lt;id&gt;</code></td>
      <td>Adjacency state + system-id / NET (VRF-scoped).</td></tr>
  <tr><td class="method">POST</td><td><code>/api/isis/monitor/force-check</code></td>
      <td>Refresh IS-IS state for every running device.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/device/ping</code></td>
      <td>Server-side ping (in-VRF if <code>device_id</code> is supplied).</td></tr>
  <tr><th colspan="3" style="background:#eff6ff; color:#1d4ed8;">Health, monitors &amp; bulk ops (§16–§17)</th></tr>
  <tr><td class="method">GET</td> <td><code>/api/health</code></td>
      <td><strong>Auth-exempt.</strong> Structured health probe — for k8s / HAProxy / Consul.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/monitors/health</code></td>
      <td>Aggregated background-monitor status (ARP / BGP / OSPF / ISIS / DHCP) + staleness.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/dhcp/monitor/force-check</code></td>
      <td>Force an immediate DHCP poll for every running client device.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/devices/export</code></td>
      <td>Snapshot the entire device topology as JSON.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/devices/import</code></td>
      <td>Restore a previously-exported topology (same shape).</td></tr>
  <tr><th colspan="3" style="background:#eff6ff; color:#1d4ed8;">State history (§18)</th></tr>
  <tr><td class="method">GET</td> <td><code>/api/device/database/devices/&lt;id&gt;/history</code></td>
      <td>Interleaved state-transition timeline across all protocols.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/device/database/devices/&lt;id&gt;/history/&lt;proto&gt;</code></td>
      <td>Filter to one of <code>bgp / ospf / isis / arp / dhcp</code>.</td></tr>
  <tr><th colspan="3" style="background:#eff6ff; color:#1d4ed8;">Stateful TCP (§19)</th></tr>
  <tr><td class="method">POST</td><td><code>/api/stateful_tcp/start</code></td>
      <td>Spawn a real-socket client OR server session — raw / HTTP / <strong>DNS</strong> / <strong>SIP</strong>, TLS, VRF.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/stateful_tcp/stop</code></td>
      <td>Stop one session by ID, or all if no <code>session_id</code> given.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/stateful_tcp/sessions</code></td>
      <td>List every known session (running + finished).</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/stateful_tcp/stats/&lt;id&gt;</code></td>
      <td>Live counters: conns / bytes / handshake-ms / RTT / retransmits / per-protocol bins.</td></tr>
  <tr><th colspan="3" style="background:#eff6ff; color:#1d4ed8;">Live events &amp; templates (§21–§22)</th></tr>
  <tr><td class="method">GET</td> <td><code>/api/events/stream</code></td>
      <td>Server-Sent Events feed — state transitions + device/stream lifecycle pushed live.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/events/status</code></td>
      <td>Current SSE subscriber count.</td></tr>
  <tr><th colspan="3" style="background:#eff6ff; color:#1d4ed8;">L2 frame generators &amp; multicast (§23)</th></tr>
  <tr><td class="method">POST</td><td><code>/api/l2/{lacp,lldp,vrrp,igmp,pim,bfd}/start</code></td>
      <td>Spawn a periodic L2 frame emitter. <strong>BFD</strong> (RFC 5880) added in 0.2.61; all protocols accept inline 802.1Q + <strong>QinQ</strong> (802.1ad) tags since 0.2.60.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/l2/stop</code></td>
      <td>Stop one L2 session by ID, or all if no ID given.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/l2/sessions</code></td>
      <td>List every L2 session (running + finished).</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/l2/stats/&lt;id&gt;</code></td>
      <td>Live counters for one L2 session — frames sent / failed / bytes.</td></tr>
  <tr><th colspan="3" style="background:#eff6ff; color:#1d4ed8;">EVPN bulk inject (§24) — new in 0.2.62 / 0.2.66</th></tr>
  <tr><td class="method">POST</td><td><code>/api/evpn/type2/inject</code></td>
      <td>Manufacture N synthetic MAC (+ optional IP) entries on a VXLAN iface so FRR advertises them as Type-2 routes.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/evpn/type2/clear</code></td>
      <td>Undo a previous Type-2 inject by <code>inject_id</code>.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/evpn/type5/inject</code></td>
      <td>Inject N consecutive IPv4 prefixes as kernel routes; FRR advertises them as Type-5 (IP Prefix) routes.</td></tr>
  <tr><td class="method">POST</td><td><code>/api/evpn/type5/clear</code></td>
      <td>Undo a previous Type-5 inject by <code>inject_id</code>.</td></tr>
  <tr><td class="method">GET</td> <td><code>/api/evpn/type2/list</code></td>
      <td>Unified snapshot of every active inject on this server (Type-2 + Type-5 with <code>kind</code> field).</td></tr>
  <tr><th colspan="3" style="background:#eff6ff; color:#1d4ed8;">Preflight (§25) — new in 0.2.68</th></tr>
  <tr><td class="method">GET</td> <td><code>/api/preflight/check</code></td>
      <td>Pre-Apply sanity report: BGP/VXLAN/OSPF/ISIS misconfigs + cross-device duplicate IPv4. Counts + per-device grouping.</td></tr>
</table>

<h2>2. Common stream JSON shape</h2>

<p>Every <code>/api/traffic/start</code> body is keyed by interface label,
with a list of stream objects per interface:</p>

<pre>{
  "streams": {
    "Port:&lt;iface&gt;": [
      { ...stream object 1... },
      { ...stream object 2... }
    ]
  }
}</pre>

<p>The stream object accepts these top-level fields (all optional except
where noted; sensible defaults are applied):</p>

<table>
  <tr><th>Field</th><th>Type</th><th>Notes</th></tr>
  <tr><td><code>name</code></td><td>string</td>
      <td>Display name. Defaults to "Unnamed Stream".</td></tr>
  <tr><td><code>enabled</code></td><td>bool</td>
      <td>Must be <code>true</code> for the stream to actually start.</td></tr>
  <tr><td><code>stream_id</code></td><td>string (UUID)</td>
      <td>Optional. Server generates one if missing. Required for stop.</td></tr>
  <tr><td><code>L2</code> / <code>L3</code> / <code>L4</code></td>
      <td>string</td>
      <td>"Ethernet" / ("IPv4" or "IPv6") / ("UDP" or "TCP" or "ICMP")</td></tr>
  <tr><td><code>VLAN</code></td><td>string</td>
      <td>"Untagged" or "Tagged".</td></tr>
  <tr><td><code>frame_size</code></td><td>int</td>
      <td>Bytes. Server-side default 64. Used for rate math too.</td></tr>
  <tr><td><code>flow_tracking_enabled</code></td><td>bool</td>
      <td>Enable RX sniff for loss-percentage / rx_count tracking.</td></tr>
  <tr><td><code>rx_port</code></td><td>string</td>
      <td>"Same as TX Port" or a "TG N - Port: ifaceX" label. Optional.</td></tr>
  <tr><td><code>protocol_data</code></td><td>nested object</td>
      <td>Per-layer field bag. See per-protocol sections below.</td></tr>
  <tr><td><code>stream_rate_type</code></td><td>string</td>
      <td>"Line Rate" | "Packets Per Second (PPS)" | "Bit Rate (Mbps)" | "Load (%)"</td></tr>
  <tr><td><code>stream_pps_rate</code></td><td>int</td>
      <td>When <code>stream_rate_type == "Packets Per Second (PPS)"</code>.</td></tr>
  <tr><td><code>stream_bit_rate</code></td><td>int</td>
      <td>Mbps. When rate_type is bit-rate.</td></tr>
  <tr><td><code>stream_duration_mode</code> /
          <code>stream_duration_seconds</code></td><td>string / int</td>
      <td>"Continuous" or "Seconds" + a count. Defaults to continuous.</td></tr>
  <tr><td class="dpdk"><code>dpdk_enable</code></td><td>bool</td>
      <td><span class="dpdk">DPDK only.</span> Use <code>tx_worker</code> backend.
          Requires <code>L4 == "UDP"</code>.</td></tr>
  <tr><td class="dpdk"><code>dpdk_tx_cores</code></td><td>int</td>
      <td><span class="dpdk">DPDK only.</span> 1–16. Default 1. With Line Rate
          + default 1, the launcher auto-bumps based on link speed.</td></tr>
  <tr><td class="dpdk"><code>enable_timestamps</code></td><td>bool</td>
      <td><span class="dpdk">DPDK + UDP only.</span> Embeds a 16-byte NLAT
          header at the start of each UDP payload for one-way latency
          measurement. Pair with <code>/api/latency/stats</code> on the
          RX side. See section 10.</td></tr>
  <tr><td class="dpdk"><code>src_ip_count</code> /
          <code>dst_ip_count</code> /
          <code>src_port_count</code> /
          <code>dst_port_count</code></td><td>int</td>
      <td><span class="dpdk">DPDK only.</span> Per-packet field
          randomization. The launcher cycles src/dst IP and L4 port
          through a range of size N for 5-tuple-hash distribution
          (Spirent/Ixia "modifiers"). Default 1 (no variation).</td></tr>
</table>

<h2>3. Engine: Scapy/kernel vs DPDK</h2>

<table>
  <tr><th></th>
      <th><span class="scapy">Scapy / kernel</span></th>
      <th><span class="dpdk">DPDK / tx_worker</span></th></tr>
  <tr><td><b>How to opt in</b></td>
      <td>Default. Don't set <code>dpdk_enable</code>.</td>
      <td>Set <code>"dpdk_enable": true</code> in the stream JSON.</td></tr>
  <tr><td><b>L4 supported</b></td>
      <td>UDP, TCP, ICMP, raw payloads</td>
      <td><b>UDP only.</b> TCP/ICMP fall back to Scapy with a warning.</td></tr>
  <tr><td><b>L3</b></td><td>IPv4, IPv6</td><td>IPv4 (IPv6 not yet supported in tx_worker)</td></tr>
  <tr><td><b>VLAN</b></td><td>✓</td><td>✓ (single tag)</td></tr>
  <tr><td><b>Typical rate</b></td>
      <td>~50K – 1M pps</td>
      <td>Up to ~395 Gbps / 32 Mpps at 1500B (12 cores, line-rate)</td></tr>
  <tr><td><b>Prereqs</b></td>
      <td>Just kernel + python-scapy</td>
      <td>DPDK libs + tx_worker binary + hugepages + (Broadcom/Intel) vfio-pci</td></tr>
</table>

<div class="warn">
<b>Heads up:</b> if you set <code>dpdk_enable: true</code> with
<code>L4: "ICMP"</code> (or any non-UDP), the launcher logs a warning and
silently uses UDP for the L4 header. The Scapy path honors L4 faithfully.
For ICMP / TCP, leave DPDK off.
</div>

<h2>4. Worked examples — packet types</h2>

<h3>4a. Bare Ethernet (no L3)</h3>
<p>Two MACs flooding raw frames. Useful for L2 forwarding tests.</p>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "L2-bare",
        "enabled": true,
        "frame_size": 64,
        "L2": "Ethernet",
        "L3": "None",
        "L4": "None",
        "VLAN": "Untagged",
        "stream_rate_type": "Packets Per Second (PPS)",
        "stream_pps_rate": 100000,
        "protocol_data": {
          "mac": {
            "mac_source_address": "00:00:00:00:00:01",
            "mac_destination_address": "00:00:00:00:00:02"
          }
        }
      }]
    }
  }'</pre>

<h3>4b. IPv4 + UDP <span class="muted">(works on both engines)</span></h3>

<h4>Scapy / kernel path</h4>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "ipv4-udp",
        "enabled": true,
        "frame_size": 512,
        "L2": "Ethernet", "L3": "IPv4", "L4": "UDP",
        "VLAN": "Untagged",
        "stream_rate_type": "Packets Per Second (PPS)",
        "stream_pps_rate": 100000,
        "protocol_data": {
          "mac":  { "mac_source_address": "aa:bb:cc:dd:ee:01",
                    "mac_destination_address": "aa:bb:cc:dd:ee:02" },
          "ipv4": { "ipv4_source": "10.0.0.1",
                    "ipv4_destination": "10.0.0.2",
                    "ttl": 64 },
          "udp":  { "udp_source_port": "12345",
                    "udp_destination_port": "54321" }
        }
      }]
    }
  }'</pre>

<h4>DPDK / tx_worker path — line rate</h4>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "ipv4-udp-blast",
        "enabled": true,
        "frame_size": 1500,
        "L2": "Ethernet", "L3": "IPv4", "L4": "UDP",
        "VLAN": "Untagged",
        "stream_rate_type": "Line Rate",
        "dpdk_enable": true,
        "protocol_data": {
          "mac":  { "mac_source_address": "aa:bb:cc:dd:ee:01",
                    "mac_destination_address": "aa:bb:cc:dd:ee:02" },
          "ipv4": { "ipv4_source": "10.0.0.1",
                    "ipv4_destination": "10.0.0.2" },
          "udp":  { "udp_source_port": "1234",
                    "udp_destination_port": "4791" }
        }
      }]
    }
  }'</pre>
<p class="muted">With Line Rate + no <code>dpdk_tx_cores</code> override, the
launcher reads <code>/sys/class/net/&lt;iface&gt;/speed</code> and auto-picks
the right number of TX queues (12 for 400G@1500B). To pin manually, add
<code>"dpdk_tx_cores": 8</code>.</p>

<h3>4c. IPv4 + TCP <span class="muted">(Scapy only)</span></h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "ipv4-tcp",
        "enabled": true,
        "frame_size": 256,
        "L2": "Ethernet", "L3": "IPv4", "L4": "TCP",
        "VLAN": "Untagged",
        "stream_rate_type": "Packets Per Second (PPS)",
        "stream_pps_rate": 50000,
        "protocol_data": {
          "mac":  { "mac_source_address": "aa:bb:cc:dd:ee:01",
                    "mac_destination_address": "aa:bb:cc:dd:ee:02" },
          "ipv4": { "ipv4_source": "10.0.0.1",
                    "ipv4_destination": "10.0.0.2" },
          "tcp":  { "tcp_source_port": "10000",
                    "tcp_destination_port": "80",
                    "tcp_flags": "SYN" }
        }
      }]
    }
  }'</pre>

<h3>4d. IPv4 + ICMP <span class="muted">(Scapy only)</span></h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "ipv4-icmp",
        "enabled": true,
        "frame_size": 64,
        "L2": "Ethernet", "L3": "IPv4", "L4": "ICMP",
        "VLAN": "Untagged",
        "stream_rate_type": "Packets Per Second (PPS)",
        "stream_pps_rate": 1000,
        "protocol_data": {
          "mac":  { "mac_source_address": "aa:bb:cc:dd:ee:01",
                    "mac_destination_address": "aa:bb:cc:dd:ee:02" },
          "ipv4": { "ipv4_source": "10.0.0.1",
                    "ipv4_destination": "10.0.0.2" },
          "icmp": { "icmp_type": "8", "icmp_code": "0" }
        }
      }]
    }
  }'</pre>

<h3>4e. IPv6 + UDP <span class="muted">(Scapy only — DPDK doesn't support IPv6 yet)</span></h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "ipv6-udp",
        "enabled": true,
        "frame_size": 512,
        "L2": "Ethernet", "L3": "IPv6", "L4": "UDP",
        "VLAN": "Untagged",
        "stream_rate_type": "Packets Per Second (PPS)",
        "stream_pps_rate": 100000,
        "protocol_data": {
          "mac":  { "mac_source_address": "aa:bb:cc:dd:ee:01",
                    "mac_destination_address": "aa:bb:cc:dd:ee:02" },
          "ipv6": { "ipv6_source": "2001:db8::1",
                    "ipv6_destination": "2001:db8::2",
                    "hop_limit": 64 },
          "udp":  { "udp_source_port": "12345",
                    "udp_destination_port": "54321" }
        }
      }]
    }
  }'</pre>

<h3>4f. VLAN-tagged IPv4 + UDP <span class="muted">(both engines)</span></h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "vlan-udp",
        "enabled": true,
        "frame_size": 1500,
        "L2": "Ethernet", "L3": "IPv4", "L4": "UDP",
        "VLAN": "Tagged",
        "stream_rate_type": "Line Rate",
        "dpdk_enable": true,
        "protocol_data": {
          "mac":  { "mac_source_address": "aa:bb:cc:dd:ee:01",
                    "mac_destination_address": "aa:bb:cc:dd:ee:02" },
          "vlan": { "vlan_id": "100", "vlan_priority": "0" },
          "ipv4": { "ipv4_source": "10.0.0.1",
                    "ipv4_destination": "10.0.0.2" },
          "udp":  { "udp_source_port": "1234",
                    "udp_destination_port": "4791" }
        }
      }]
    }
  }'</pre>

<h3>4g. Multiple streams in one call</h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [
        { "name": "udp-1k",  "enabled": true, "frame_size": 1500,
          "L4": "UDP", "stream_rate_type": "Packets Per Second (PPS)",
          "stream_pps_rate": 1000, "dpdk_enable": false,
          "protocol_data": { ...IPv4+UDP... } },
        { "name": "tcp-syn", "enabled": true, "frame_size": 256,
          "L4": "TCP", "stream_rate_type": "Packets Per Second (PPS)",
          "stream_pps_rate": 500,
          "protocol_data": { ...IPv4+TCP... } }
      ],
      "Port:enp181s0f1np1": [
        { "name": "v6-udp", "enabled": true, "frame_size": 512,
          "L3": "IPv6", "L4": "UDP",
          "protocol_data": { ...IPv6+UDP... } }
      ]
    }
  }'</pre>
<p class="muted">Streams on different interfaces start in parallel. Streams
on the same interface run concurrently in separate threads.</p>

<h2>5. Rate types</h2>

<table>
  <tr><th><code>stream_rate_type</code></th><th>Companion field</th><th>Effect</th></tr>
  <tr><td>"Line Rate"</td><td>—</td>
      <td>Flood. DPDK auto-picks <code>tx_cores</code> from link speed.
          Scapy floods at kernel speed (~1M pps cap).</td></tr>
  <tr><td>"Packets Per Second (PPS)"</td>
      <td><code>stream_pps_rate</code></td>
      <td>Pace at the requested PPS. tx_worker uses TSC pacing
          per-burst; Scapy uses time.sleep().</td></tr>
  <tr><td>"Bit Rate (Mbps)"</td>
      <td><code>stream_bit_rate</code></td>
      <td>Server converts to PPS using <code>bps / ((frame_size + 20) * 8)</code>.</td></tr>
  <tr><td>"Load (%)"</td>
      <td><code>stream_load_percentage</code></td>
      <td>% of link rate. Useful for "fill 50% of the pipe" tests.</td></tr>
</table>

<h2>6. Stream control</h2>

<h3>Stop by stream id</h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/traffic/stop \
  -H "Content-Type: application/json" \
  -d '{
    "streams": [
      { "interface": "enp181s0f0np0",
        "stream_id": "&lt;uuid-from-start-response&gt;" }
    ]
  }'

# Response — verify the stop was honoured:
# { "ok": true,
#   "stopped": [
#     { "interface": "enp181s0f0np0",
#       "stream_id": "&lt;uuid&gt;",
#       "status": "Stopped" }
#   ],
#   "not_found": [] }</pre>

<div class="warn"><b>Schema note (v0.3.11):</b> <code>streams</code> is
a <b>LIST</b> of <code>{interface, stream_id}</code> entries —
asymmetric with <code>/api/traffic/start</code>, which keys streams by
interface in a DICT. Sending the start-style dict here causes the
server to iterate keys silently and the request returns
<code>ok=true</code> with an empty <code>stopped</code> list. Always
verify the response's <code>stopped</code> array has an entry for each
requested stream — a no-op stop indicates schema confusion or a
stale stream_id.</div>

<p>Behavior:</p>
<ul>
  <li>Sets the stream's <code>stop_event</code>.</li>
  <li>Waits up to 2s for the launcher's graceful drain.</li>
  <li>If still alive, force-kills any tx_worker matching the stream id
      via <code>pkill -- "--stream-id &lt;sid&gt;"</code> (defense in depth
      against orphan tx_worker leaks).</li>
  <li>Marks the stream as Stopped in the SQLite database.</li>
</ul>

<h3>Live stats</h3>
<pre>curl -G http://&lt;server&gt;:5050/api/streams/stats \
  --data-urlencode "status=Running"</pre>

<p>Response:</p>
<pre>{
  "active_streams": [{
    "stream_id":      "&lt;uuid&gt;",
    "stream_name":    "ipv4-udp-blast",
    "interface":      "enp181s0f0np0",
    "tg_id":          0,
    "status":         "Running",
    "tx_count":       12345678901,    // cumulative since stream start
    "rx_count":       0,              // 0 if flow tracking off
    "tx_rate":        32794646.5,     // pps (delta-based)
    "rx_rate":        0.0,
    "frame_size":     1500,
    "dpdk_enable":    true,            // what the OPERATOR asked for
    "actual_engine":  "DPDK",          // what's REALLY running — see below
    "fallback_reason": null,           // populated if engine differs from request
    "dpdk_tx_cores":  12,
    "per_core_tx_count": [3086419725, 3086419725, 3086419725, 3086419726],
    "engine_version":  "tx_worker-1.4.2 / DPDK 23.11.1",
    "started_at":     "2026-05-10T07:05:10+00:00",
    "updated_at":     "2026-05-10T07:05:38+00:00"
  }]
}</pre>

<p class="muted"><b>v0.3.11:</b> <code>actual_engine</code> +
<code>fallback_reason</code> let the client surface silent engine
downgrades. Example: operator asked for DPDK on a TCP stream (DPDK
tx_worker is UDP-only) → server falls back to Scapy and reports
<code>actual_engine=Scapy, fallback_reason="DPDK supports UDP only,
got TCP"</code>. The client renders this as an amber chip on the
stream so the operator notices.</p>

<p class="muted"><code>per_core_tx_count</code> is a per-lcore array
(one entry per <code>dpdk_tx_cores</code>) for diagnosing imbalanced
RSS / steering — added in v0.2.77.</p>

<p class="muted">Bit rate (bps) isn't returned directly — derive it as
<code>tx_rate * frame_size * 8</code>. The client does this in the
Stream Statistics tab's TX Bit Rate column.</p>

<h2>7. DPDK helper endpoints</h2>

<h3>Get tx_cores recommendation</h3>
<pre>curl -G http://&lt;server&gt;:5050/api/dpdk/recommend \
  --data-urlencode "iface=enp181s0f0np0" \
  --data-urlencode "frame_size=1500" \
  --data-urlencode "pps=0"      # 0 = line rate

# →
# {
#   "ok": true,
#   "iface": "enp181s0f0np0",
#   "link_speed_mbps": 400000,
#   "frame_size": 1500,
#   "target_pps": 32894736,
#   "line_rate_pps": 32894736,
#   "estimated_pps_per_core": 4100000,
#   "recommended_tx_cores": 12,
#   "explanation": "Link 400 Gbps; line rate at 1500B = 32,894,736 pps. ..."
# }</pre>

<h3>DPDK runtime status</h3>
<pre>curl http://&lt;server&gt;:5050/api/dpdk/status
# returns hugepage state, IOMMU state, kernel-driver presence,
# bound vfio-pci interfaces, etc.</pre>

<h3>Bind / unbind a NIC <span class="muted">(Broadcom/Intel only — Mellanox doesn't need this)</span></h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/dpdk/bind \
  -H "Content-Type: application/json" \
  -d '{ "interface": "enp65s0f0np0", "force": false }'

# Unbind:
curl -X POST http://&lt;server&gt;:5050/api/dpdk/unbind \
  -H "Content-Type: application/json" \
  -d '{ "interface": "enp65s0f0np0", "kernel_driver": "bnxt_en" }'</pre>

<h3>Allocate hugepages <span class="muted">(v0.3.11 schema fix)</span></h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/dpdk/hugepages \
  -H "Content-Type: application/json" \
  -d '{ "num_pages": 1024, "page_size": "2MB" }'

# →
# { "ok": true, "allocated": 1024, "page_size": "2MB",
#   "total_bytes": 2147483648 }</pre>
<p class="muted">Schema correction in v0.3.11: keys are
<code>num_pages</code> (int) and <code>page_size</code> (string
<code>"2MB"</code> or <code>"1GB"</code>). Earlier clients sent
<code>count</code>/<code>size_kb</code> which the server silently
ignored — hugepages never allocated, DPDK init failed downstream.
Server currently allocates <code>"2MB"</code> only at runtime;
<code>"1GB"</code> needs a kernel boot-time reservation.</p>

<h3>Bind history <span class="muted">(GUI helper for vfio-bound NICs)</span></h3>
<pre>curl http://&lt;server&gt;:5050/api/admin/bind_history

# →
# { "history": {
#     "0000:b5:00.0": { "name": "enp181s0f0np0",
#                       "kernel_driver": "mlx5_core" },
#     "0000:b5:00.1": { "name": "enp181s0f1np1",
#                       "kernel_driver": "mlx5_core" }
# }}</pre>
<p class="muted">When a NIC is bound to <code>vfio-pci</code>, the
kernel no longer exposes its netdev name — <code>/proc/net/dev</code>
loses the entry. Without this map, the GUI's NIC picker can't show
operators a recognisable name for the bound device (only the bare
pci_bdf). The Blast a Flow + Make DPDK Ready dialogs merge this map
into the interface list at render time so a bound NIC reads
"eth0 (DPDK)" instead of "0000:b5:00.0".</p>

<h2>8. Polling pattern</h2>

<p>Standard "kick off + poll" pattern in bash:</p>
<pre>#!/bin/bash
SERVER=svl-d-ai-srv01:5050
IFACE=enp181s0f0np0
SID=$(uuidgen)

# Start
curl -s -X POST http://$SERVER/api/traffic/start \
  -H "Content-Type: application/json" \
  -d "{ \"streams\": { \"Port:$IFACE\": [{
        \"name\": \"blast\", \"enabled\": true, \"stream_id\": \"$SID\",
        \"frame_size\": 1500, \"L4\": \"UDP\",
        \"stream_rate_type\": \"Line Rate\", \"dpdk_enable\": true,
        \"protocol_data\": { ... } }] } }" &gt; /dev/null

# Poll every 2 seconds
for i in $(seq 1 30); do
  curl -s "http://$SERVER/api/streams/stats?status=Running" \
    | jq -r ".active_streams[] | select(.stream_id == \"$SID\")
             | \"\\(.tx_rate / 1e6 | floor) Mpps  \\(.tx_count) frames\""
  sleep 2
done

# Stop
curl -s -X POST http://$SERVER/api/traffic/stop \
  -H "Content-Type: application/json" \
  -d "{ \"streams\": [{ \"interface\": \"$IFACE\", \"stream_id\": \"$SID\" }] }"</pre>

<h2>9. RFC 2544 throughput test (§26.1)</h2>

<p>Binary-search the maximum no-drop rate at each frame size, classic IETF
throughput methodology. The server runs the search in a background thread;
the client kicks it off and polls for progress.</p>

<h3>9a. Start a test</h3>

<pre>curl -X POST http://&lt;server&gt;:5050/api/rfc2544/start \
  -H "Content-Type: application/json" \
  -d '{
    "tx_iface": "enp181s0f0np0",
    "rx_iface": "enp181s0f1np1",
    "frame_sizes": [64, 128, 256, 512, 1024, 1280, 1518],
    "duration_per_step": 10,
    "target_loss_pct": 0.0,
    "resolution_pps": 100000,
    "mac_src": "aa:bb:cc:dd:ee:01",
    "mac_dst": "aa:bb:cc:dd:ee:02",
    "ip_src":  "10.0.0.1",
    "ip_dst":  "10.0.0.2",
    "dpdk_enable": true
  }'</pre>

<p>Response: <code>{"ok": true, "started_at": "&lt;ISO timestamp&gt;"}</code>.
Returns 409 if a test is already running.</p>

<table>
  <tr><th>Field</th><th>Type</th><th>Notes</th></tr>
  <tr><td><code>tx_iface</code></td><td>string (required)</td>
      <td>TX interface name.</td></tr>
  <tr><td><code>rx_iface</code></td><td>string (optional)</td>
      <td>RX interface name. Defaults to <code>tx_iface</code> for loopback.</td></tr>
  <tr><td><code>frame_sizes</code></td><td>list[int]</td>
      <td>Defaults to the IETF set <code>[64,128,256,512,1024,1280,1518]</code>.</td></tr>
  <tr><td><code>duration_per_step</code></td><td>int (seconds)</td>
      <td>How long each binary-search iteration sends traffic. Default 10.
          Bump to 60 for stable lab measurements; drop to 2 for smoke tests.</td></tr>
  <tr><td><code>target_loss_pct</code></td><td>float</td>
      <td>Acceptable loss. RFC 2544 §26.1 defines 0%; some labs use 0.001%.</td></tr>
  <tr><td><code>resolution_pps</code></td><td>int</td>
      <td>Binary-search precision floor. Default 100000 (100K pps).
          Smaller = more iterations = longer test.</td></tr>
  <tr><td><code>mac_src</code> / <code>mac_dst</code></td><td>string (required)</td>
      <td>Source / destination MAC for the test frames.</td></tr>
  <tr><td><code>ip_src</code> / <code>ip_dst</code></td><td>string (required)</td>
      <td>Source / destination IPv4 for the test frames.</td></tr>
  <tr><td><code>dpdk_enable</code></td><td>bool</td>
      <td>Default true. Use DPDK tx_worker for accurate line-rate sends.</td></tr>
  <tr><td><code>dpdk_tx_cores</code></td><td>int</td>
      <td>Optional. Forwarded as the stream's <code>dpdk_tx_cores</code>.
          Leave unset to let the server auto-pick per <code>/api/dpdk/recommend</code>.</td></tr>
</table>

<h3>9b. Poll for progress</h3>

<pre>curl http://&lt;server&gt;:5050/api/rfc2544/progress</pre>

<p>Response shape:</p>

<pre>{
  "running": true,
  "started_at": "2026-05-10T13:01:12+00:00",
  "finished_at": null,
  "params": { ...the request body... },
  "current_step": {
    "frame_size": 64,
    "trying_pps": 37202380,
    "phase": "testing 37,202,380 pps for 10s"
  },
  "progress": [
    {
      "frame_size": 64,
      "max_no_drop_pps": 35850000,
      "max_no_drop_gbps": 24.09,
      "line_rate_pps": 37202380,
      "pct_of_line_rate": 96.4,
      "attempts": [
        {"pps": 18601190, "tx": 186011900, "rx": 186011900, "loss_pct": 0.0},
        {"pps": 27901785, "tx": 279017850, "rx": 279017850, "loss_pct": 0.0},
        ...
      ]
    }
  ],
  "error": null
}</pre>

<p><code>progress[]</code> grows by one entry per converged frame size. While the
search for the current frame size is in progress, <code>current_step</code>
shows what rate it's testing right now.</p>

<p class="muted"><b>Expected timing:</b> each binary-search iteration is
<code>duration_per_step + 1.5s settle + 0.5s stats read</code>. To converge
from 0 to 100K-pps resolution typically takes ~10 iterations × 12s =
<b>~2 minutes per frame size</b>. Full IETF set ≈ 14 minutes.</p>

<h3>9c. Stop a running test</h3>

<pre>curl -X POST http://&lt;server&gt;:5050/api/rfc2544/stop</pre>

<p>Cooperative cancel — flips <code>stop_requested=true</code>, halts the
in-flight stream, and the runner thread aborts within ~0.5s. Response:
<code>{"ok": true, "was_running": true}</code>. Idempotent (returns
<code>was_running: false</code> if no test is active).</p>

<h3>10d. Comparison with hardware test equipment (Ixia, Spirent)</h3>

<p>Operators familiar with Ixia <i>IxNetwork-RoCEv2</i> or
Spirent <i>TestCenter-RoCE</i> often ask whether netgen can match
the N×M endpoint-group topology testing those tools provide. Short
answer: <b>not at hardware scale, but the topology semantics are
available via v0.4.0+ Topology Mode</b> (Tools → RDMA →
Topology Test…). The architectural difference is worth understanding
before picking the tool for a given lab.</p>

<table>
  <tr><th>Capability</th>
      <th>Ixia / Spirent (hardware)</th>
      <th>netgen (software, perftest-based)</th></tr>
  <tr><td><b>Endpoints per port</b></td>
      <td>~1000+ — emulated in ASIC</td>
      <td>~10s per host — each pair is a real OS-level perftest process pair</td></tr>
  <tr><td><b>Topology object model</b></td>
      <td>Endpoint Groups + Traffic Items + Workload Profiles</td>
      <td>v0.4.0+: endpoint groups + topology shape (single / fan-in / fan-out / mesh / pairwise) + shared workload opts. Same conceptual model</td></tr>
  <tr><td><b>Mesh / fan-in / fan-out</b></td>
      <td>One Traffic Item with src×dst groups → N×M flows auto-generated</td>
      <td>v0.4.0+: pick a shape, pick endpoint lists, dialog spawns the N×M perftest pairs</td></tr>
  <tr><td><b>Per-flow stats aggregation</b></td>
      <td>Always-on hardware counters; rolls up flow → port → chassis in real time</td>
      <td>Per-pair BW from perftest summary at test end; client-side roll-up shows TOTAL across pairs</td></tr>
  <tr><td><b>Line rate at high scale</b></td>
      <td>Sustained 400 Gb/s per port × many ports</td>
      <td>Per-host limited by Linux kernel + RDMA HCA; verified 392 Gb/s on a single ConnectX-7 loopback pair</td></tr>
  <tr><td><b>Mixed protocols on same port</b></td>
      <td>RoCE + TCP + UDP simultaneously</td>
      <td>One perftest process per RDMA pair; mix achieved by combining with the Streams tab (DPDK/Scapy) on other interfaces</td></tr>
  <tr><td><b>Workload profiles</b></td>
      <td>Pre-canned SEND / WRITE / READ / NVMe-oF / custom</td>
      <td>SEND / WRITE / READ + _lat variants via the test-type combo. NVMe-oF would require a different orchestrator (perftest doesn't speak NVMe)</td></tr>
  <tr><td><b>Hardware cost</b></td>
      <td>$$$$ — dedicated chassis + per-port licences</td>
      <td>$ — runs on any Linux box with an RDMA-capable NIC</td></tr>
  <tr><td><b>Control plane</b></td>
      <td>IxNetwork Python/TCL API; IxLoad; IxOS</td>
      <td>Flask REST API at <code>/api/rdma/perftest/*</code> and <code>/api/rdma/topology/*</code>; GUI from this same client</td></tr>
</table>

<h4>Why the architectural difference</h4>

<p><b>Ixia/Spirent run the RDMA QP state machine in dedicated ASICs
on the test chassis ports.</b> One physical port can emulate thousands
of independent RDMA endpoints simultaneously because each endpoint's
QP state lives in silicon, not as a Linux process. This is what
enables 1000-endpoint stress tests from a single hardware chassis.</p>

<p><b>netgen orchestrates real perftest processes on real Linux hosts.</b>
Each test pair = one server-side perftest process + one client-side
perftest process, each holding real kernel QPs via libibverbs.
Scale is bounded by host CPU and kernel resources rather than ASIC
state-machine count. The trade-off: lower max endpoint count, but
zero hardware cost and bit-perfect fidelity (you're running THE
canonical RDMA tool against THE production kernel verbs stack).</p>

<h4>When to pick which</h4>

<table>
  <tr><th>Use case</th><th>Pick</th></tr>
  <tr><td>Validate one link can hit line rate</td>
      <td>Either — netgen's Blast RDMA dialog gives Ixia-level results</td></tr>
  <tr><td>10–50 pair stress test (fan-in, mesh)</td>
      <td>netgen Topology Mode (v0.4.0+) — sufficient + free</td></tr>
  <tr><td>1000+ endpoint stress / fabric scaling</td>
      <td>Ixia/Spirent — hardware count required</td></tr>
  <tr><td>Production firmware/driver acceptance testing</td>
      <td>netgen — runs against the actual kernel stack you ship</td></tr>
  <tr><td>Continuous CI traffic generation in lab</td>
      <td>netgen — REST API + free / no licence accounting</td></tr>
  <tr><td>RFC-compliance protocol fuzzing</td>
      <td>Ixia/Spirent — purpose-built field manipulation</td></tr>
  <tr><td>NVMe-oF over RDMA testing</td>
      <td>Ixia/Spirent — perftest doesn't speak NVMe (yet)</td></tr>
</table>

<p class="muted">Roadmap: closing the topology-model gap is the
v0.4.0 milestone. NVMe-oF and per-flow real-time counters are
candidates for v0.5+ if there's operator demand — neither is a
fundamental architectural blocker, just additional orchestrator code
(NVMe-oF would shell out to <code>nvme-cli</code> + <code>nvmetcli</code>
rather than perftest; per-flow counters would parse perftest's
<code>--out_json</code> mode and stream samples instead of waiting
for the end-of-test summary).</p>

<h2>10e. RDMA Topology Test (multi-pair, v0.4.0+)</h2>

<p>The single-pair Blast a RDMA Flow dialog forces operators to open
N separate windows to drive N test pairs. Topology Test
(<b>Tools → RDMA → Topology Test…</b>) collapses that into one
dialog with endpoint <b>groups</b> + a <b>topology shape</b>, mirroring
Ixia's IxNetwork Topology + Traffic Item model (see §10d for the
detailed comparison).</p>

<h3>10e·1. Topology shapes</h3>

<table>
  <tr><th>Shape</th><th>Required endpoints</th><th>Generates</th><th>Use case</th></tr>
  <tr><td><b>Single</b></td><td>1 server + 1 client</td><td>1 pair</td>
      <td>Identical to the v0.3.x Blast dialog. Provided for spec
          parity / scripted topology runs that want one code path.</td></tr>
  <tr><td><b>Fan-in</b></td><td>1 server + N clients</td><td>N pairs</td>
      <td>"Can one box receive from many" stress test. The classic
          RoCE switch / fabric stress pattern.</td></tr>
  <tr><td><b>Fan-out</b></td><td>N servers + 1 client</td><td>N pairs</td>
      <td>"Can one box send to many." Useful when the client is the
          sender (the role names follow perftest convention — server
          listens, client drives traffic).</td></tr>
  <tr><td><b>Mesh</b></td><td>N servers + M clients</td><td>N×M pairs</td>
      <td>Full cross-product. Every server endpoint paired with every
          client endpoint. Good fabric-wide saturation test.</td></tr>
  <tr><td><b>Pairwise</b></td><td>N servers + N clients (equal)</td><td>N pairs</td>
      <td>Parallel index-aligned pairing (s0↔c0, s1↔c1, …) — tests
          parallel link saturation <i>without</i> cross-traffic
          between pairs.</td></tr>
</table>

<h3>10e·2. Endpoint line format</h3>

<p>The Server endpoints / Client endpoints text boxes accept one
endpoint per line in the shape:</p>

<pre>&lt;tg_url&gt; &lt;device&gt; [port=N] [gid=N] [label=NAME]</pre>

<p>Examples:</p>

<pre># Bare minimum — defaults: port=1, gid=3
http://srv01:5050 mlx5_0

# Override port + GID (e.g. older ConnectX-3 in dual-port mode,
# or RoCEv2-IPv6)
http://srv01:5050 mlx5_0 port=2 gid=4

# Friendly label for the stats grid
http://srv01:5050 mlx5_0 label=primary-rx

# Comments start with #
# Blank lines are ignored</pre>

<p>Parse errors surface in the live "X pairs" preview label so
operators see them BEFORE clicking Start.</p>

<h3>10e·3. Listen-port allocation</h3>

<p>Each pair needs a unique TCP port for perftest's control channel.
The dialog allocates <code>base_listen_port + pair_index</code> for
each pair (default base = 18516, perftest's own default 18515 + 1).
So a 20-pair mesh occupies ports 18516–18535 on the server side(s).
Operators running this in parallel with an ad-hoc Blast a RDMA Flow
dialog can bump the base port to avoid collisions.</p>

<h3>10e·4. Stats aggregation</h3>

<p>The per-pair grid shows one row per pair: Server, Client, State,
BW, MsgRate. The TOTAL row sums BW + MsgRate across all pairs that
have reported data. Latency tests aggregate as iteration-weighted
mean (a 10-iter pair shouldn't drag the average around as much as
a 10K-iter one).</p>

<p>perftest is batch-mode by default — the data row appears only at
test end. While running, the row shows "running"; numbers appear
once perftest emits the summary. See §10·notes for the timing
breakdown.</p>

<h3>10e·5. Sanity-check the spawned pairs</h3>

<p>While a topology is running, use <b>Tools → RDMA → RDMA Jobs…</b>
on each TG to see all the perftest processes spawned. Each pair has
a unique <code>handshake_id</code>; the two halves (server-side +
client-side) share that id so you can match them.</p>

<h3>10e·6. Failure isolation</h3>

<p>If one pair fails to start (server unreachable, perftest not
installed, etc.), the dialog marks ONLY that row as
<code>FAILED:&nbsp;&lt;reason&gt;</code> and continues running the rest.
The TOTAL row aggregates over the surviving pairs.</p>

<h3>10e·7. When NOT to use Topology Test</h3>

<ul>
  <li>One pair only — use Blast a RDMA Flow for the simpler UI.</li>
  <li>1000+ endpoints — netgen tops out around 50 pairs per TG
      before CPU starts dominating; for higher scale you need
      hardware emulation (Ixia / Spirent — see §10d).</li>
  <li>NVMe-oF / SR-IOV emulation — perftest doesn't speak those;
      use the appropriate vendor tool.</li>
</ul>

<h2>10. One-way latency sampler</h2>

<p>For streams sent with <code>"enable_timestamps": true</code>, tx_worker
embeds a 16-byte NLAT header at the start of each UDP payload. A
per-interface RX-side sampler decodes those headers and computes
min/avg/p50/p99/max latency over a rolling sample window.</p>

<h3>10a. Get latency stats for an interface</h3>

<pre>curl 'http://&lt;server&gt;:5050/api/latency/stats?iface=enp181s0f1np1'</pre>

<p>Response:</p>

<pre>{
  "ok": true,
  "iface": "enp181s0f1np1",
  "udp_port": 4791,
  "samples_seen": 1502341,
  "samples_decoded": 1502340,
  "samples_skipped": 1,
  "window_samples": 10000,
  "min_us": 1.18,
  "avg_us": 2.41,
  "p50_us": 2.32,
  "p99_us": 4.18,
  "max_us": 12.04
}</pre>

<table>
  <tr><th>Query param</th><th>Notes</th></tr>
  <tr><td><code>iface</code></td>
      <td>Required. The RX-side interface to sniff.</td></tr>
  <tr><td><code>udp_port</code></td>
      <td>Optional. UDP destination port to filter. Default 4791.</td></tr>
</table>

<p>The sampler is started lazily on first query for an iface and stays
running until <code>/api/latency/stop</code> is called or the server
restarts. The 10000-sample rolling window slides forward as new
NLAT-tagged frames arrive.</p>

<div class="warn"><b>Same-host loopback gives accurate one-way numbers.</b>
Cross-host requires PTP- or NTP-synced clocks for absolute accuracy —
without that, only relative drift across the window is meaningful.</div>

<h3>10b. Stop a sampler</h3>

<pre># Stop one iface
curl -X POST http://&lt;server&gt;:5050/api/latency/stop \
  -H "Content-Type: application/json" \
  -d '{"iface": "enp181s0f1np1"}'

# Stop all running samplers
curl -X POST http://&lt;server&gt;:5050/api/latency/stop \
  -H "Content-Type: application/json" \
  -d '{}'</pre>

<p>Response: <code>{"ok": true, "stopped": ["enp181s0f1np1"]}</code>.</p>

<h2>11. Common gotchas</h2>

<table>
  <tr><th>Symptom</th><th>Likely cause</th></tr>
  <tr><td>Stream "starts" but tx_count stays 0</td>
      <td>Wheel-shipped stale tx_worker binary, or DPDK not installed.
          Check <code>/api/dpdk/status</code>.</td></tr>
  <tr><td>DPDK stream actually sends ICMP/TCP</td>
      <td>tx_worker is UDP-only. Set L4=UDP or remove dpdk_enable.</td></tr>
  <tr><td>Line Rate request only hits a fraction of link speed</td>
      <td>Default <code>dpdk_tx_cores=1</code>. Auto-pick fires only when
          <code>dpdk_tx_cores</code> is unset; if you sent
          <code>"dpdk_tx_cores": 1</code>, you get one TX queue. Either
          omit the field or call /api/dpdk/recommend first.</td></tr>
  <tr><td>tx_rate / Loss% values look wrong after restart</td>
      <td>Tracker cumulative counter resets on stream restart. The
          client's "Clear Stats" applies a baseline tare — do that, then
          restart the stream.</td></tr>
  <tr><td>"Read timed out (read timeout=15)" on stop</td>
      <td>Server's stop endpoint takes &lt;3s normally. A 15s timeout
          means the launcher hung AND the backstop pkill missed —
          extremely rare; check journalctl for matching errors.</td></tr>
</table>

<h2>12. Device lifecycle</h2>

<p>A <b>device</b> is an emulated router: a VLAN subif on a physical NIC,
its own Linux VRF, plus an FRR container that owns the BGP / OSPF /
IS-IS state. Each device is identified by a UUID.</p>

<h3>Apply (create or reconfigure)</h3>
<pre><code>curl -s -X POST http://&lt;server&gt;:5050/api/device/apply \
  -H 'Content-Type: application/json' -d '{
    "device_id":   "47dce96a-348a-43ec-9eed-8223c67378b1",
    "device_name": "device1",
    "interface":   "enp181s0f0np0",
    "vlan":        "10",
    "ipv4":        "192.168.0.2", "ipv4_mask": "24", "ipv4_gateway": "192.168.0.1",
    "ipv6":        "2001:db8::2", "ipv6_mask": "64", "ipv6_gateway": "2001:db8::1",
    "protocols":   ["BGP"],
    "bgp_config":  { "bgp_asn": 65000, "bgp_remote_asn": 65000,
                     "bgp_neighbor_ipv4": "192.168.0.1",
                     "bgp_update_source_ipv4": "192.168.0.2",
                     "ipv4_enabled": true }
  }'
</code></pre>

<p><b>What apply does, in order:</b> create the VLAN subif if needed →
flip <code>net.ipv6.conf.&lt;iface&gt;.disable_ipv6=0</code> →
add the IPv4 / IPv6 address →
provision <code>vrf-&lt;short-id&gt;</code> and move the iface into it →
start the FRR container (<code>ostg-frr-&lt;device-id&gt;</code>, <code>--net=host</code>) →
push vtysh config scoped to the device's VRF.</p>

<div class="warn"><b>Same iface + same VLAN is rejected with HTTP 409.</b>
Two devices on the same physical NIC must use different VLAN tags so
they end up on distinct VLAN subifs and distinct VRFs. Use the increment
options in the GUI's Add Device dialog to spread VLANs across a batch.</div>

<h3>Stop / Start</h3>
<p><code>POST /api/device/stop</code> sends SIGTERM to the FRR container —
container goes <code>Exited</code>, VRF is <b>preserved</b>, DB status
flips to <code>Stopped</code>. <code>POST /api/device/start</code> on a
Stopped device runs <code>docker start</code> on the existing container
(fast, ~2 s); on a never-applied device it falls through to the apply
path.</p>

<h3>Remove</h3>
<p><code>POST /api/device/remove</code> tears down everything: container
removed, VLAN subif optional (kept by default to avoid disturbing the
parent NIC), VRF deleted, DB row dropped. The periodic cleanup timer
will only remove containers whose device row is gone from the DB — your
Stopped devices are safe.</p>

<h2>13. Control-plane protocols (BGP, OSPF, IS-IS)</h2>

<p>The FRR container inside each device runs <b>bgpd, ospfd, ospf6d,
isisd, zebra</b>. Every protocol instance is scoped to that device's
VRF — e.g. the config inside the container reads:</p>

<pre><code>router bgp 65000 vrf vrf-47dce96a348
 bgp router-id 192.168.0.2
 neighbor 192.168.0.1 remote-as 65000
 address-family ipv4 unicast
  neighbor 192.168.0.1 next-hop-self
 exit-address-family
exit
!
interface vlan10
 vrf vrf-47dce96a348
 ip address 192.168.0.2/24
 ipv6 address 2001:db8::2/64
exit
</code></pre>

<p>So multiple devices on the same host don't collide on TCP/179, the
OSPF raw-89 socket, or per-iface PF_PACKET binds — each lives in its
own VRF table.</p>

<h3>Configure / Start / Stop per protocol</h3>
<pre><code># BGP — push config (idempotent; safe to re-call)
curl -X POST http://&lt;server&gt;:5050/api/device/bgp/configure -d '{...}'

# Start one neighbor only (selective)
curl -X POST http://&lt;server&gt;:5050/api/device/bgp/start \
  -d '{"device_id":"...", "bgp_config":{...},
       "selected_neighbors":["192.168.0.1"]}'

# Stop all neighbors
curl -X POST http://&lt;server&gt;:5050/api/device/bgp/stop \
  -d '{"device_id":"...", "bgp_config":{...}}'
</code></pre>

<p>Same shape for <code>ospf</code> and <code>isis</code> — substitute the
protocol name in the URL.</p>

<h3>Status (per-device)</h3>
<pre><code>curl http://&lt;server&gt;:5050/api/bgp/status/&lt;device_id&gt;
# → { "bgp_established": true, "bgp_ipv4_established": true,
#     "bgp_ipv6_established": false, "bgp_state": "Established",
#     "neighbors": [ {"neighbor_ip":"192.168.0.1","state":"Established",...} ] }
</code></pre>

<p>OSPF: <code>/api/ospf/status/&lt;id&gt;</code> · IS-IS:
<code>/api/isis/status/&lt;id&gt;</code> — same response shape with
protocol-specific fields.</p>

<h3>Refresh (force-check)</h3>
<p>Each protocol has a <code>force-check</code> endpoint that re-probes
every running device synchronously and writes fresh state to the DB.
GUI Refresh buttons hit these:</p>
<pre><code>curl -X POST http://&lt;server&gt;:5050/api/arp/monitor/force-check
curl -X POST http://&lt;server&gt;:5050/api/bgp/monitor/force-check
curl -X POST http://&lt;server&gt;:5050/api/ospf/monitor/force-check
curl -X POST http://&lt;server&gt;:5050/api/isis/monitor/force-check
</code></pre>

<p>Without a force-check, the periodic monitor refreshes the DB every
30 s. Reads of <code>/api/device/database/devices/&lt;id&gt;</code>
return whatever was last written.</p>

<h2>14. ARP / ND status</h2>

<p>Each device exposes a live ARP/ND probe at
<code>/api/device/arp/&lt;device_id&gt;</code>. The server runs the
pings inside the device's VRF for external targets (gateway) and in
the default netns for self-pings (the kernel's "local" route for the
device's own IP points at global <code>lo</code>, which sits outside
the VRF).</p>

<pre><code>curl http://&lt;server&gt;:5050/api/device/arp/&lt;device_id&gt;
# → { "arp_resolved": true, "arp_status": "Resolved",
#     "arp_ipv4_resolved": true, "arp_ipv6_resolved": true,
#     "arp_gateway_resolved": true,
#     "details": { "ipv4_ping":"success", "ipv6_ping":"success",
#                  "gateway_ping":"success", "vrf":"vrf-47dce96a348" } }
</code></pre>

<p>The periodic ARP monitor calls this endpoint every 30 s for every
Running device and persists the result.</p>

<h2>15. Authentication</h2>

<p>Off by default — fine for the lab. Two opt-in modes, both via
env vars on the server:</p>

<h3>Single token (back-compat, 0.2.0)</h3>
<pre><code># systemd EnvironmentFile or launch-script export
NETGEN_AUTH_TOKEN=8c2f4e9a-3d1b-46f7-…</code></pre>

<p>Every <code>/api/*</code> request must carry
<code>Authorization: Bearer &lt;secret&gt;</code> or it returns
<code>401</code>. The token resolves to <strong>admin</strong> role —
full access everywhere.</p>

<h3>Per-role tokens (0.2.1+)</h3>
<pre><code>NETGEN_AUTH_TOKENS_JSON='{
  "abc...":"admin",
  "def...":"operator",
  "ghi...":"viewer"
}'</code></pre>

<p>Multiple tokens, each mapped to a role. Three roles in a strict
hierarchy:</p>

<table>
  <tr><th>Role</th><th>Can call</th></tr>
  <tr><td><strong>viewer</strong></td>
      <td>Read-only — status / history / export endpoints, SSE
          stream, stateful-TCP stats.</td></tr>
  <tr><td><strong>operator</strong></td>
      <td>Everything viewer can, plus mutating ops — device apply /
          start / stop, traffic start / stop, BGP / OSPF / IS-IS
          configure, force-checks, stateful-TCP start / stop.</td></tr>
  <tr><td><strong>admin</strong></td>
      <td>Everything — including destructive: device remove,
          fabric-wide cleanup.</td></tr>
</table>

<p>Insufficient role returns <code>403</code> (vs <code>401</code> for
"wrong token") so the client can distinguish identity vs permission.</p>

<p>40 endpoints are currently role-annotated; older unannotated
endpoints still gate on token presence but don't role-check yet
(incremental migration).</p>

<h3>Exempt paths (no token required)</h3>
<ul>
  <li><code>/admin</code> — single-page web UI handles its own session</li>
  <li><code>/api/health</code> — for k8s / HAProxy / Consul probes</li>
</ul>

<h3>Client (curl)</h3>
<pre><code>curl -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     http://&lt;server&gt;:5050/api/interfaces</code></pre>

<h3>Client (GUI)</h3>
<p>Set <code>NETGEN_AUTH_TOKEN</code> in the environment before launching
the client; a bootstrap hook in <code>run_tgen_client.py</code> auto-
injects the header into every <code>requests.{get,post,put,…}</code>
call so the existing 100+ HTTP sites Just Work.</p>

<h3>What's still not handled</h3>
<p>No CORS preflight handling. No rate limiting. Tokens are shared
secrets — no per-token rotation tooling. Don't expose port 5050
to the public internet; the auth layer guards against accidental
discovery, not against a hardened attacker.</p>

<h2>16. Monitor health</h2>

<p>Each protocol monitor (ARP / BGP / OSPF / ISIS / DHCP) runs in its
own background thread polling the FRR containers and writing state to
the device DB. <code>/api/monitors/health</code> is the one-shot
aggregator — useful for "are my monitors still alive?" dashboards and
the inline badge in the Devices tab.</p>

<pre><code>curl -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     http://&lt;server&gt;:5050/api/monitors/health

{
  "ok": true,
  "monitors": {
    "arp":  {"running": true, "stale_secs": 12, "stale": false},
    "bgp":  {"running": true, "stale_secs": 3,  "stale": false},
    "ospf": {"running": true, "stale_secs": 8,  "stale": false},
    "isis": {"running": true, "stale_secs": 9,  "stale": false},
    "dhcp": {"running": true}
  },
  "checked_at": "2026-05-11T22:13:04Z"
}</code></pre>

<p><code>ok=false</code> when any monitor isn't running or its DB
heartbeat is &gt; 90 seconds stale. Each monitor also exposes a
<code>force-check</code> endpoint:</p>

<ul>
  <li><code>/api/arp/monitor/force-check</code></li>
  <li><code>/api/bgp/monitor/force-check</code></li>
  <li><code>/api/ospf/monitor/force-check</code></li>
  <li><code>/api/isis/monitor/force-check</code></li>
  <li><code>/api/dhcp/monitor/force-check</code></li>
</ul>

<p>POST with an empty body — they all run synchronously and return a
fresh status snapshot.</p>

<p><code>/api/health</code> is the bare liveness probe and is
<strong>auth-exempt by design</strong> so k8s / HAProxy / Consul can
probe the server without owning the token:</p>

<pre><code>curl http://&lt;server&gt;:5050/api/health
{"status":"ok","version":"0.2.0","uptime_s":143}</code></pre>

<h2>17. Device export &amp; import</h2>

<p>Round-trip the entire device topology as JSON. Useful for
"snapshot this lab, restore on another box", topology version-control
in git, or seeding a fresh server from a known-good config.</p>

<h3>Export</h3>
<pre><code>curl -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     http://&lt;server&gt;:5050/api/devices/export &gt; devices.json

{
  "count": 3,
  "exported_at": "2026-05-11T22:14:00Z",
  "devices": [ { "device_id": "...", "device_name": "...", ... }, ... ]
}</code></pre>

<h3>Import</h3>
<pre><code>curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d @devices.json \
     http://&lt;server&gt;:5050/api/devices/import

{ "imported": 3, "failed": 0, "total": 3, "errors": [] }</code></pre>

<p>Import is idempotent on <code>device_id</code> — a row that already
exists is updated in place; new rows are inserted. Runtime state
(ARP / BGP / OSPF / IS-IS state, last-check timestamps) is never
exported so you can move topologies between hosts cleanly.</p>

<p>From the CLI: <code>netgen-cli export -o devices.json</code> /
<code>netgen-cli import -f devices.json --wait</code> — see §20.</p>

<h2>18. State-history timeline</h2>

<p>Every protocol monitor records a row each time it observes a state
change for one of its devices. Rows are de-duped against the previous
row, so steady-state polls don't bloat the table while a device sits
in <code>Established</code>. The table powers the
<strong>Ctrl+H</strong> dialog in the GUI and the "Recent transitions"
block on the Topology tab's property panel.</p>

<h3>All protocols, interleaved</h3>
<pre><code>curl -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     "http://&lt;server&gt;:5050/api/device/database/devices/&lt;id&gt;/history?limit=20"</code></pre>

<h3>One protocol</h3>
<pre><code>curl -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     "http://&lt;server&gt;:5050/api/device/database/devices/&lt;id&gt;/history/bgp?limit=20"

{
  "device_id": "47dce96a-...",
  "protocol":  "bgp",
  "count": 4,
  "history": [
    { "id": 412, "timestamp": "2026-05-11T22:13:04Z",
      "protocol": "bgp", "state": "Established",
      "detail": { "ipv4": "Established", "ipv6": "Established",
                  "neighbors": 2 } },
    { "id": 387, "timestamp": "2026-05-11T22:11:48Z",
      "protocol": "bgp", "state": "Active",
      "detail": { "ipv4": "Active", "ipv6": null, "neighbors": 1 } },
    ...
  ]
}</code></pre>

<p>Valid <code>&lt;proto&gt;</code> values: <code>bgp</code>,
<code>ospf</code>, <code>isis</code>, <code>arp</code>,
<code>dhcp</code>. Anything else returns HTTP 400 with the valid list
— silent-empty was a footgun.</p>

<h2>19. Stateful TCP</h2>

<p>Real-socket TCP traffic, parallel to the scapy / DPDK stateless
streams. Sessions here complete actual 3-way handshakes, so middleboxes,
NAT, proxies, and load balancers see real connection state. Each
session is owned by an in-process worker that loops connect → send →
recv → close for the configured duration; counters update on every
connection.</p>

<h3>Start a server</h3>
<pre><code>curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{ "role": "server",
           "listen_ip": "0.0.0.0",
           "listen_port": 5001,
           "mode": "echo" }' \
     http://&lt;server&gt;:5050/api/stateful_tcp/start

{ "session_id": "9286ba6e-...", "role": "server" }</code></pre>

<h3>Start a client</h3>
<pre><code>curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{ "role": "client",
           "dst_ip": "10.0.0.5",
           "dst_port": 5001,
           "src_ip": "10.0.0.10",
           "vrf": "vrf-abc12345",
           "duration_s": 30,
           "payload_bytes": 4096,
           "concurrency": 4,
           "protocol": "raw" }' \
     http://&lt;server&gt;:5050/api/stateful_tcp/start

{ "session_id": "47dce96a-...", "role": "client" }</code></pre>

<p>Valid <code>protocol</code> values: <code>raw</code> (default —
byte echo), <code>http</code> (HTTP/1.1 framing), <code>dns</code>
(DNS-over-TCP per RFC 7766), <code>sip</code> (SIP-over-TCP REGISTER
per RFC 3261). See per-protocol examples below.</p>

<h3>Poll counters</h3>
<pre><code>curl -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     http://&lt;server&gt;:5050/api/stateful_tcp/stats/&lt;session_id&gt;

{
  "session_id": "47dce96a-...",
  "role": "client",
  "running": true,
  "counters": {
    "uptime_s": 12.3,
    "conns_attempted": 412, "conns_established": 410, "conns_failed": 2,
    "bytes_tx": 419840,  "bytes_rx": 419840,
    "avg_handshake_ms": 0.92,
    "avg_rtt_ms": 1.43,           "rtt_samples": 410,
    "avg_kernel_rtt_us": 920.4,   "kernel_rtt_samples": 410,
    "retransmits_total": 0,
    "http_status_2xx": 0,         "http_status_other": 0,
    "dns_noerror": 0, "dns_nxdomain": 0, "dns_servfail": 0, "dns_other": 0,
    "sip_2xx": 0,  "sip_3xx": 0, "sip_4xx": 0, "sip_5xx": 0, "sip_other": 0,
    "last_error": null
  }
}</code></pre>

<h3>L7 protocols</h3>

<h4>DNS-over-TCP (RFC 7766)</h4>
<p>Builds a 2-byte length-prefixed DNS query for <code>dns_qname</code>
(default <code>netgen.test</code>); server answers with the configured
<code>dns_response_rcode</code> (default 3 = NXDOMAIN). Useful for
testing DNS proxies, recursive resolvers handling TCP fallback,
DNS-aware load balancers. Counters bin per RCODE:
<code>dns_noerror</code> (0), <code>dns_nxdomain</code> (3),
<code>dns_servfail</code> (2), <code>dns_other</code>.</p>

<pre><code>curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"role":"server","listen_port":5353,
          "protocol":"dns","dns_response_rcode":0}' \
     http://&lt;server&gt;:5050/api/stateful_tcp/start

curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"role":"client","dst_ip":"127.0.0.1","dst_port":5353,
          "protocol":"dns","duration_s":10,"concurrency":4}' \
     http://&lt;server&gt;:5050/api/stateful_tcp/start</code></pre>

<h4>SIP-over-TCP (RFC 3261)</h4>
<p>Sends well-formed SIP REGISTER messages over TCP and bins the
response by status class. Server-side simulator mirrors Via / From /
To / Call-ID / CSeq per §8.2.6.2 so real SIP clients accept the
responses. Useful for testing SBCs, SIP registrars, reverse proxies
that gate on SIP transactions.</p>

<pre><code># Registrar simulator returning 401 Unauthorized (auth-retry test)
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"role":"server","listen_port":5060,"protocol":"sip",
          "sip_response_status":401,"sip_response_reason":"Unauthorized"}' \
     http://&lt;server&gt;:5050/api/stateful_tcp/start

curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"role":"client","dst_ip":"127.0.0.1","dst_port":5060,
          "protocol":"sip","sip_host":"registrar.example.com",
          "duration_s":10,"concurrency":2,"interval_s":0.01}' \
     http://&lt;server&gt;:5050/api/stateful_tcp/start</code></pre>

<h3>Optional flags</h3>

<table>
  <tr><th>Flag</th><th>Side</th><th>Behavior</th></tr>
  <tr><td><code>vrf</code></td><td>both</td>
      <td>Linux interface name for <code>SO_BINDTODEVICE</code>
          (typically a per-device VRF or master link). No-op +
          warning on macOS / Windows.</td></tr>
  <tr><td><code>protocol</code></td><td>both</td>
      <td><code>raw</code> | <code>http</code> | <code>dns</code> |
          <code>sip</code>. Per-protocol counters bin responses.</td></tr>
  <tr><td><code>tls: true</code></td><td>both</td>
      <td>Wrap each connection in TLS. Server requires
          <code>tls_cert</code> + <code>tls_key</code> paths; client
          defaults to <code>tls_verify=false</code> for self-signed
          test envs (set <code>true</code> to enforce CA + hostname).</td></tr>
  <tr><td><code>dns_qname</code> / <code>dns_response_rcode</code></td><td>both</td>
      <td>Client: query name. Server: rcode to return (0=NOERROR,
          3=NXDOMAIN, 2=SERVFAIL, …).</td></tr>
  <tr><td><code>sip_host</code> / <code>sip_user</code></td><td>client</td>
      <td>Registrar host (defaults to <code>dst_ip</code>) and user
          part of the REGISTER URI.</td></tr>
  <tr><td><code>sip_response_status</code> / <code>sip_response_reason</code></td><td>server</td>
      <td>Status code + reason phrase the SIP simulator returns to
          every REGISTER. Use 401/Unauthorized to drive auth-retry.</td></tr>
  <tr><td><code>expect_echo: false</code></td><td>client</td>
      <td>Send + close immediately without waiting for an echo.
          Reduces handshake-dominated overhead when measuring tx
          throughput in isolation.</td></tr>
  <tr><td><code>concurrency: N</code></td><td>client</td>
      <td>Spawn N parallel sender threads against the same target.</td></tr>
</table>

<h3>Counters glossary</h3>

<table>
  <tr><th>Counter</th><th>Source</th></tr>
  <tr><td><code>avg_handshake_ms</code></td>
      <td>Userspace timer around <code>connect()</code>.</td></tr>
  <tr><td><code>avg_rtt_ms</code></td>
      <td>Userspace timer around the full connect → send → recv → close.</td></tr>
  <tr><td><code>avg_kernel_rtt_us</code></td>
      <td><code>TCP_INFO.rtt_us</code> — smoothed RTT from the kernel.
          Linux only; <code>0</code> elsewhere.</td></tr>
  <tr><td><code>retransmits_total</code></td>
      <td><code>TCP_INFO.total_retrans</code>. Linux only.</td></tr>
  <tr><td><code>http_status_2xx</code> / <code>_other</code></td>
      <td>Per HTTP/1.1 status when <code>protocol=http</code>.</td></tr>
  <tr><td><code>dns_noerror / nxdomain / servfail / other</code></td>
      <td>Per RCODE bin when <code>protocol=dns</code>.</td></tr>
  <tr><td><code>sip_2xx / 3xx / 4xx / 5xx / other</code></td>
      <td>Per status-class bin when <code>protocol=sip</code>.</td></tr>
</table>

<div class="warn">
  <strong>When to use stateful vs stateless:</strong> for line-rate
  forwarding tests stick with the scapy/DPDK streams (§4) — they hit
  400&nbsp;Gbps. The stateful path is for &quot;does my middlebox
  proxy TCP correctly?&quot;, &quot;does my reverse proxy handle a
  WAF check?&quot;, &quot;does my load balancer pin a TLS session?&quot;
  — workloads that require an actual handshake.
</div>

<h2>20. netgen-cli (headless companion)</h2>

<p>The headless companion to the GUI. Same REST surface, no X display
required — drops into CI pipelines, tmux panes, SSH sessions. Installs
as the <code>netgen-cli</code> entry point alongside the GUI client.</p>

<h3>Common workflows</h3>
<pre><code># Health + monitor status (§16)
netgen-cli health

# Topology snapshot / restore (§17)
netgen-cli export -o devices.json
netgen-cli import -f devices.json --wait

# Apply one device and block until ARP resolves
netgen-cli apply -f single_device.json --wait

# Per-device + protocol status
netgen-cli status -i 47dce96a-...
netgen-cli wait   -i 47dce96a-... --timeout 60

# List all devices
netgen-cli list</code></pre>

<h3>Stateful TCP (§19)</h3>
<pre><code>netgen-cli tcp start-server --port 5001 --bind 127.0.0.1
netgen-cli tcp start-client --dst-ip 127.0.0.1 --dst-port 5001 \
    --duration 30 --concurrency 4 --payload-bytes 4096 \
    --protocol http --vrf vrf-abc12345
netgen-cli tcp list
netgen-cli tcp stats --session-id &lt;id&gt;
netgen-cli tcp stop   --session-id &lt;id&gt;     # or `stop` alone to stop all</code></pre>

<h3>Server URL &amp; auth</h3>
<p>The server URL is read from <code>$NETGEN_SERVER_URL</code>
(default <code>http://localhost:5050</code>) — override with
<code>-s URL</code> on any subcommand. If
<code>$NETGEN_AUTH_TOKEN</code> is set, every request gets
<code>Authorization: Bearer …</code> automatically, mirroring the GUI's
behaviour. Exit codes are bash-friendly:
<code>0</code> success, <code>1</code> bad args, <code>3</code> HTTP
error, <code>4</code> partial-failure on bulk operations,
<code>5</code> timeout on <code>--wait</code> — gate merges on those.</p>

<h2>21. Live event stream (Server-Sent Events)</h2>

<p>Long-lived HTTP stream that pushes operator-visible events to
subscribers in real time — replaces polling for state changes.
Used internally by the Topology tab and the Devices tab to live-
refresh on protocol transitions and device-lifecycle changes.
External consumers can subscribe too.</p>

<h3>Subscribe</h3>
<pre><code>curl -N -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     http://&lt;server&gt;:5050/api/events/stream</code></pre>

<p>Wire format (one event per blank-line-terminated block):</p>

<pre><code>event: state_transition
data: {"event_type":"state_transition","ts":1747044723.4,
       "device_id":"47dce96a-...","protocol":"bgp",
       "state":"Established","detail":{...}}

event: heartbeat
data: {"event_type":"heartbeat","ts":1747044738.4,"subscriber_count":1}</code></pre>

<p>Heartbeats fire every 15 s during idle windows so proxies don't
time the connection out. Both <code>EventSource</code> in browsers
and the GUI's <code>utils.sse_client.SSEWorker</code> auto-reconnect
on drop.</p>

<h3>Event types emitted today</h3>

<table>
  <tr><th>Type</th><th>Fires after</th><th>Payload keys</th></tr>
  <tr><td><code>state_transition</code></td>
      <td>Any protocol monitor sees a state change.</td>
      <td><code>device_id</code>, <code>protocol</code>
          (bgp/ospf/isis/arp/dhcp), <code>state</code>,
          <code>detail</code></td></tr>
  <tr><td><code>device_applied</code></td>
      <td><code>POST /api/device/apply</code> success</td>
      <td><code>device_id</code>, <code>device_name</code>,
          <code>interface</code></td></tr>
  <tr><td><code>device_apply_failed</code></td>
      <td><code>POST /api/device/apply</code> exception</td>
      <td><code>device_id</code>, <code>device_name</code>,
          <code>error</code></td></tr>
  <tr><td><code>device_started</code></td>
      <td><code>POST /api/device/start</code> success</td>
      <td><code>device_id</code></td></tr>
  <tr><td><code>device_stopped</code></td>
      <td><code>POST /api/device/stop</code> success</td>
      <td><code>device_id</code></td></tr>
  <tr><td><code>device_removed</code></td>
      <td><code>POST /api/device/remove</code> success</td>
      <td><code>device_id</code>, <code>device_name</code>,
          <code>db_removed</code></td></tr>
  <tr><td><code>stream_started</code></td>
      <td><code>POST /api/traffic/start</code> success</td>
      <td><code>count</code>, <code>streams</code></td></tr>
  <tr><td><code>stream_stopped</code></td>
      <td><code>POST /api/traffic/stop</code> success</td>
      <td><code>count</code>, <code>stream_ids</code></td></tr>
  <tr><td><code>stream_restarted</code></td>
      <td><code>POST /api/traffic/restart</code> success</td>
      <td><code>interface</code>, <code>count</code></td></tr>
  <tr><td><code>heartbeat</code></td>
      <td>Every ~15 s during idle</td>
      <td><code>subscriber_count</code></td></tr>
</table>

<p>Every envelope also carries <code>event_type</code> and
<code>ts</code> (unix-epoch float) — consumers can rely on those
two keys always being present.</p>

<h3>Subscriber count</h3>
<pre><code>curl -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     http://&lt;server&gt;:5050/api/events/status
# {"subscribers": 2}</code></pre>

<h3>Adding a new event type</h3>
<p>Producer code anywhere in the server calls
<code>utils.event_bus.publish(event_type, {…})</code>. The envelope
gets the canonical <code>event_type</code> + <code>ts</code> keys
added automatically. Publication is non-blocking and a no-op when
nobody is subscribed — safe to drop into hot paths.</p>

<h2>22. Templates &amp; bulk-edit (GUI features)</h2>

<p>Two operator-facing workflow shortcuts that don't have dedicated
REST endpoints — they live in the GUI but warrant mention here so
operators know they exist.</p>

<h3>One-click device templates</h3>
<p>The Add Device dialog opens with a <strong>"Quick start from
template"</strong> dropdown. Eight pre-baked profiles ship today:</p>

<ul>
  <li>Bare host (L2/L3 only — no routing)</li>
  <li>iBGP peer (loopback router-id, peer-AS = local-AS)</li>
  <li>eBGP peer (AS 65000 ↔ AS 65001 defaults)</li>
  <li>OSPFv2 area-0 backbone router</li>
  <li>OSPFv2 + OSPFv3 dual-stack</li>
  <li>IS-IS Level-1-2 router (default NET 49.0001.…)</li>
  <li>DHCP client</li>
  <li>VXLAN tunnel endpoint</li>
</ul>

<p>Picking a template pre-fills the form; operator tweaks IPs / VLAN
to match their lab and clicks Apply. Skip-applicable: missing
widgets are silently ignored so templates survive form
rearrangements.</p>

<h3>One-click traffic templates</h3>
<p>The Add Stream dialog opens with a similar <strong>Template</strong>
dropdown. Seven pre-baked traffic profiles:</p>

<ul>
  <li>UDP line-rate · 64 B (64-byte DPDK blast)</li>
  <li>UDP IMIX (three-frame mix; tweak frame_size or modifiers for true IMIX)</li>
  <li>LAG / RSS / ECMP hash test (modifiers cycle src/dst IP + L4 ports)</li>
  <li>Latency probe (NLAT, 1000 pps small frames)</li>
  <li>VXLAN-encapsulated UDP (inner Ether+IP+UDP wrapped in UDP/4789)</li>
  <li>ICMP echo flood (scapy path)</li>
  <li>VLAN-tagged UDP (802.1Q over IPv4/UDP)</li>
</ul>

<h3>Multi-device bulk-edit (⧉ toolbar button)</h3>
<p>Select N rows in the Devices tab → click ⧉ → set start value +
step for any of VLAN / IPv4 / IPv4 Gateway / Loopback / MAC. First
row gets <code>start</code>, second gets <code>start + step</code>,
etc. Protocol checkboxes are tri-state — Checked enables across the
selection, Unchecked disables, Partial leaves each device's setting
alone. Live preview shows the first 3 + last computed plan before
commit. Marks every touched device for re-apply; next ✓ Apply
pushes the new values to the server.</p>

<h2>23. L2 frame generators &amp; multicast</h2>

<p>Periodic-frame emitters for the protocols every datacenter and
enterprise lab tests. Built on scapy's existing layer definitions
so the wire format follows the contrib package's current shape.
Each session is a session-registered worker thread that <code>sendp</code>'s
the configured frame on the chosen interface at the protocol's
standard cadence.</p>

<p><strong>Requires <code>CAP_NET_RAW</code> or root.</strong>
<code>sendp</code> opens an AF_PACKET socket on Linux; raw sockets
are root-only on macOS BSD. The worker surfaces
<code>PermissionError</code> on <code>last_error</code> and stops
the session — silent-fail isn't useful when the operator's first
question is &quot;why are no frames hitting the wire?&quot;</p>

<h3>Supported protocols</h3>

<table>
  <tr><th>Protocol</th><th>Standard</th><th>Default cadence</th></tr>
  <tr><td><strong>LACP</strong></td>
      <td>IEEE 802.1AX (Slow Protocols, ethertype 0x8809)</td>
      <td>30 s (fast=true → 1 s)</td></tr>
  <tr><td><strong>LLDP</strong></td>
      <td>IEEE 802.1AB (ethertype 0x88cc)</td>
      <td>30 s</td></tr>
  <tr><td><strong>VRRP v2</strong></td>
      <td>RFC 3768 (IPv4, multicast 224.0.0.18)</td>
      <td>1 s</td></tr>
  <tr><td><strong>VRRP v3</strong></td>
      <td>RFC 5798 (IPv4 + IPv6 via <code>family</code>)</td>
      <td>1 s (centi-seconds on the wire)</td></tr>
  <tr><td><strong>IGMP v2</strong></td>
      <td>RFC 2236 (type 0x16 Report; 0x17 Leave; 0x11 Query)</td>
      <td>60 s</td></tr>
  <tr><td><strong>IGMP v3</strong></td>
      <td>RFC 3376 (type 0x22, sent to 224.0.0.22)</td>
      <td>60 s</td></tr>
  <tr><td><strong>PIM Hello</strong></td>
      <td>RFC 7761 §4.3 (224.0.0.13, IP-proto 103)</td>
      <td>30 s</td></tr>
  <tr><td><strong>BFD</strong> <span class="muted">(0.2.61+)</span></td>
      <td>RFC 5880 / 5881 (single-hop UDP/3784, IP TTL=255)</td>
      <td>1 s (sub-second supported via <code>interval_s=0.1</code>)</td></tr>
</table>

<p><strong>Inline 802.1Q + 802.1ad (QinQ)</strong> <span class="muted">(0.2.41 / 0.2.60+)</span>:
every protocol accepts <code>vlan_id</code> / <code>vlan_pcp</code> (inner) and
<code>outer_vlan_id</code> / <code>outer_vlan_pcp</code> (outer S-VLAN). Untagged when
both are 0; single 802.1Q when only inner is set; 802.1ad QinQ
(<code>Ether(type=0x88a8) / Dot1Q(outer, type=0x8100) / Dot1Q(inner,
type=&lt;protocol&gt;)</code>) when both are set. No <code>vlanN</code> subif
needed.</p>

<h3>Start examples</h3>

<pre><code># LACP fast-cadence LAG partner emulation
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"iface":"eth0","fast":true,
          "system_mac":"00:11:22:33:44:01","key":1}' \
     http://&lt;server&gt;:5050/api/l2/lacp/start

# LLDP advertiser
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"iface":"eth0","chassis_id":"netgen-host","port_id":"eth0",
          "ttl_s":120,"system_name":"netgen"}' \
     http://&lt;server&gt;:5050/api/l2/lldp/start

# VRRP v3 IPv4 master at priority 200
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"iface":"eth0","version":3,"vrid":42,"priority":200,
          "virtual_ips":["192.168.1.254"],"src_ip":"10.0.0.1"}' \
     http://&lt;server&gt;:5050/api/l2/vrrp/start

# IGMP v2 membership report for a group
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"iface":"eth0","version":2,"group":"239.1.1.1"}' \
     http://&lt;server&gt;:5050/api/l2/igmp/start

# IGMP v2 Leave (override type byte)
curl -X POST ... -d '{"iface":"eth0","version":2,
                      "group":"239.1.1.1","type_code":23}' ...

# PIM Hello — registers us as a neighbour without doing Join/Prune
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"iface":"eth0","hold_time":105,"dr_priority":1}' \
     http://&lt;server&gt;:5050/api/l2/pim/start

# BFD single-hop async — keep a peer's session Up by asserting liveness
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"iface":"eth0","src_ip":"10.0.0.1","dst_ip":"10.0.0.2",
          "my_discriminator":"0x11111111","state":3,
          "detect_mult":3,"interval_s":1.0}' \
     http://&lt;server&gt;:5050/api/l2/bfd/start

# Any protocol + QinQ (S-VLAN 200 outer, C-VLAN 100 inner)
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"iface":"eth0","chassis_id":"netgen-host","port_id":"eth0",
          "vlan_id":100,"vlan_pcp":3,
          "outer_vlan_id":200,"outer_vlan_pcp":5}' \
     http://&lt;server&gt;:5050/api/l2/lldp/start</code></pre>

<h3>Stats snapshot</h3>

<pre><code>curl http://&lt;server&gt;:5050/api/l2/stats/&lt;session_id&gt;

{
  "session_id": "...",
  "protocol": "lacp",
  "iface": "eth0",
  "running": true,
  "counters": {
    "uptime_s": 14.2,
    "frames_sent": 14, "frames_failed": 0,
    "bytes_sent": 1736,
    "last_send_at": 1747044738.4,
    "last_error": null
  }
}</code></pre>

<h3>From the CLI</h3>

<pre><code>netgen-cli l2 start-lacp --iface eth0 --fast
netgen-cli l2 start-lldp --iface eth0 --chassis-id netgen-host --port-id eth0
netgen-cli l2 start-vrrp --iface eth0 --vrid 42 --priority 200 \
                          --virtual-ips 192.168.1.254
netgen-cli l2 start-igmp --iface eth0 --group 239.1.1.1
netgen-cli l2 start-pim  --iface eth0
netgen-cli l2 list
netgen-cli l2 stats --session-id &lt;id&gt;
netgen-cli l2 stop   --session-id &lt;id&gt;     # or just `stop` to stop all</code></pre>

<div class="warn">
  <strong>Full PIM-SM / multicast-routing flows are not (yet) supported</strong> —
  this surface only emulates the Hello-side of PIM neighbour discovery,
  not Join/Prune state-machine state. IGMP is membership reports, not
  full querier behaviour. Enough for switch IGMP-snooping tests and
  for proving PIM adjacency on a real router; not enough to be a full
  multicast control plane.
</div>

<h2>24. EVPN bulk inject (Type-2 / Type-5)</h2>

<p>Scale-test EVPN peers by <strong>manufacturing N synthetic
entries</strong> in the host kernel; FRR's zebra picks them up and BGP
advertises them under the existing
<code>address-family l2vpn evpn</code> (which
<code>configure_bgp_for_device</code> auto-enables when a device has
VXLAN config). The injection skips the data-plane learning step that
real EVPN routers depend on — handy for soak-testing a partner's
ability to handle 10 000 MAC routes or 100 IP-prefix routes without
having to plumb that many real flows.</p>

<h3>Type-2 (MAC / MAC+IP)</h3>

<p>Populates the bridge FDB + ip-neigh tables on a VXLAN interface.
Optional <code>base_ip</code> generates the MAC+IP sub-routes; optional
<code>remote_vtep_ip</code> attaches the FDB entry to a specific remote
VTEP (becomes the Type-2 next-hop).</p>

<pre><code># Inject 1000 MAC+IP entries on vxlan100, advertising via VTEP 192.0.2.5
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"iface":"vxlan100","base_mac":"aa:bb:cc:00:00:01",
          "base_ip":"10.100.0.1","count":1000,
          "remote_vtep_ip":"192.0.2.5","l3_iface":"br100"}' \
     http://&lt;server&gt;:5050/api/evpn/type2/inject

# Response:
# {"inject_id":"&lt;uuid&gt;","kind":"type2","iface":"vxlan100",
#  "count":1000,"ok_count":2000,"failed_count":0,
#  "entries":[{"mac":"aa:bb:cc:00:00:01","ip":"10.100.0.1"},…],
#  "errors":[]}

# Clean up
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"inject_id":"&lt;uuid&gt;"}' \
     http://&lt;server&gt;:5050/api/evpn/type2/clear</code></pre>

<h3>Type-5 (IP Prefix)</h3>

<p>Adds N consecutive IPv4 prefixes as kernel routes (optionally in a
VRF table). Requires FRR to have
<code>advertise ipv4 unicast</code> set under the VRF's
<code>address-family l2vpn evpn</code> so the routes are exported as
Type-5.</p>

<pre><code># 100 /24s starting at 10.100.0.0/24, in VRF table 1001, via 192.168.1.1
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"dev":"eth0","base_prefix":"10.100.0.0","prefix_len":24,
          "count":100,"gateway":"192.168.1.1","vrf_table":1001}' \
     http://&lt;server&gt;:5050/api/evpn/type5/inject

# Response — same shape as Type-2 but with `prefixes` instead of `entries`
# Use the returned inject_id with /api/evpn/type5/clear to undo.</code></pre>

<h3>Listing active injections (both kinds)</h3>

<pre><code>curl http://&lt;server&gt;:5050/api/evpn/type2/list \
     -H "Authorization: Bearer $NETGEN_AUTH_TOKEN"

{
  "injections": [
    {"inject_id":"…","kind":"type2","iface":"vxlan100",
     "l3_iface":"br100","remote_vtep_ip":"192.0.2.5","count":1000},
    {"inject_id":"…","kind":"type5","iface":"eth0",
     "count":100}
  ]
}</code></pre>

<div class="warn">
  Each <code>inject_id</code> is tagged with <code>kind</code>; the
  matching <code>/api/evpn/{kind}/clear</code> endpoint is the right
  cleaner. Calling the wrong cleaner is rejected with a warning
  message and the record stays registered (defensive — the wrong
  cleaner would build wrong kernel commands and leak state).
</div>

<h2>25. Preflight checks</h2>

<p>Pre-Apply sanity report. Surfaces common bad-config shapes
(<strong>BGP without remote ASN</strong>,
<strong>VXLAN missing required fields</strong>,
<strong>OSPF / IS-IS without area</strong>,
<strong>cross-device IPv4 collisions</strong>) BEFORE the Apply
round-trip so the operator sees the issue first. Heads-up surface —
NOT a gate; Apply still runs regardless.</p>

<pre><code>curl http://&lt;server&gt;:5050/api/preflight/check \
     -H "Authorization: Bearer $NETGEN_AUTH_TOKEN"

{
  "summary": {"error":2,"warning":1,"ok":5,"total":8},
  "findings": [
    {"level":"error",  "code":"BGP_NO_REMOTE_ASN", "device_name":"R3",
     "interface":"ens1f0",
     "message":"BGP is enabled but bgp_remote_asn is empty — no
                neighbour session will form."},
    {"level":"error",  "code":"DUPLICATE_IPV4",    "device_name":"R7",
     "message":"IPv4 10.0.0.5 is also configured on: R8 — this will
                wedge forwarding and flap ARP."},
    {"level":"warning","code":"BGP_NO_LOOPBACK",   "device_name":"R3",
     "message":"BGP is enabled but Loopback IPv4 is empty — the
                router-id will be derived from a transient interface
                address."}
  ],
  "by_device": { "R3": [...], "R7": [...], "R8": [...] }
}</code></pre>

<p>Codes today: <code>BGP_NO_REMOTE_ASN</code> /
<code>BGP_NO_LOOPBACK</code> / <code>VXLAN_MISSING_FIELDS</code> /
<code>VXLAN_EMPTY</code> / <code>OSPF_NO_AREA</code> /
<code>ISIS_NO_AREA</code> / <code>DUPLICATE_IPV4</code>. Every check
is a pure function in <code>utils/preflight.py</code> — adding a new
one is purely additive.</p>

<h2>26. RFC 2544 latency + HTML report</h2>

<p>The §26.1 throughput test (binary-search max-no-drop rate per RFC
2544 frame size) gains an opt-in <strong>latency-capture</strong>
pass (RFC 2544 §26.2) in 0.2.59. Send <code>capture_latency:true</code>
on <code>/api/rfc2544/start</code> to embed NLAT timestamps in each
trial; <code>/api/rfc2544/progress</code> then returns
<code>latency: {min_us, avg_us, p50_us, p95_us, p99_us, max_us}</code>
per frame-size entry. <strong>p95</strong> was added to the latency
sampler in 0.2.58 — most SLAs are stated in p95 rather than p50 or
p99, so it gets its own field. The GUI dialog renders a self-contained
HTML report (test parameters + per-frame-size table + best-throughput
summary) that any browser can Print → Save as PDF for a formal
deliverable.</p>

<h2>27. SR-MPLS label stack on the scapy tx path</h2>

<p>The existing single-label MPLS branch in
<code>utils/generic.py</code> became a true label <em>stack</em> in
0.2.64 (RFC 8660). Set <code>protocol_data.mpls.mpls_labels:
[16000, 16001, 16002]</code> on a stream and frames carry that SID
list with ethertype <code>0x8847</code>, only the bottom label's
<code>S</code> bit set, and a uniform Traffic Class + TTL across the
stack. The Stream Edit dialog has a "Label stack" text field (0.2.65)
that accepts decimal or hex, comma-separated. The legacy scalar
<code>mpls_label</code> field still works — pre-0.2.64 streams produce
bit-identical bytes.</p>

<h2>28. RDMA perftest endpoints <span style="background:#dcfce7; color:#166534;
     padding:1px 8px; border-radius:10px; font-size:10px; font-weight:600;">0.3.12</span></h2>

<p>Eight endpoints back the <code>Tools → RDMA</code> submenu and the
per-stream <code>engine = rdma</code> path. All shell out to the
standard <code>perftest</code> suite (<code>ib_*_bw</code>,
<code>ib_*_lat</code>); the server registers each invocation in a
per-TG job table and pairs related invocations via a
<code>handshake_id</code>. See <strong>Help → Install Guide §10</strong>
for the prereqs.</p>

<h3>28a. Enumerate RDMA devices</h3>
<pre>curl http://&lt;server&gt;:5050/api/rdma/devices

# →
# { "devices": [
#     { "name": "mlx5_0",
#       "vendor": "MT_0000000838",
#       "fw_version": "28.36.1010",
#       "node_guid": "0x9803:9b03:00d0:1234",
#       "ports": [
#         { "port": 1, "state": "ACTIVE",
#           "physical_state": "LinkUp",
#           "link_layer": "Ethernet",      // RoCEv2-capable
#           "rate": "100 Gb/sec (4X EDR)",
#           "mtu": 4096, "lid": 1,
#           "gids": ["fe80::9803:9bff:fe03:1234",
#                    "::ffff:10.0.0.1"] }
#       ] } ] }</pre>

<p>Empty <code>devices</code> list (not an error) when the kernel has
no RDMA support loaded or <code>/sys/class/infiniband</code> isn't
mounted in the container.</p>

<h3>28b. Probe perftest install state</h3>
<pre>curl http://&lt;server&gt;:5050/api/rdma/perftest/installed

# →
# { "installed": true,
#   "tools": {
#     "send_bw":  "/usr/bin/ib_send_bw",
#     "write_bw": "/usr/bin/ib_write_bw",
#     "read_bw":  "/usr/bin/ib_read_bw",
#     "send_lat": "/usr/bin/ib_send_lat",
#     "write_lat":"/usr/bin/ib_write_lat",
#     "read_lat": "/usr/bin/ib_read_lat",
#     "atomic_bw":null, "atomic_lat":null
#   },
#   "version": "6.2-1" }</pre>

<h3>28c. Start a two-sided test (server then client)</h3>

<p>Generate a UUID on the caller side, pass it as
<code>handshake_id</code> to BOTH start calls so the two halves can
be correlated via <code>/api/rdma/handshakes/&lt;id&gt;</code>.</p>

<pre>HID=$(uuidgen)

# Step 1 — start the server-side perftest on TG-A.
curl -X POST http://tg-a:5050/api/rdma/perftest/start \
  -H "Content-Type: application/json" \
  -d "{
    \"role\":\"server\",
    \"test\":\"send_bw\",
    \"device\":\"mlx5_0\",
    \"ib_port\":1,
    \"msg_size\":65536,
    \"qp_count\":1,
    \"duration\":30,
    \"gid_index\":3,
    \"handshake_id\":\"$HID\",
    \"note\":\"BW between TG-A and TG-B\"
  }"

# → { "status":"started",
#     "job_id":"&lt;uuid&gt;",
#     "handshake_id":"&lt;HID&gt;",
#     "listen_addr":"10.0.0.1",
#     "listen_port":18515,
#     "tool":"/usr/bin/ib_send_bw",
#     "cmd":["/usr/bin/ib_send_bw","-p","18515","-d","mlx5_0",...],
#     "record":{ ... } }

# Step 2 — start the client-side perftest on TG-B, pointing at TG-A.
curl -X POST http://tg-b:5050/api/rdma/perftest/start \
  -H "Content-Type: application/json" \
  -d "{
    \"role\":\"client\",
    \"test\":\"send_bw\",
    \"device\":\"mlx5_0\",
    \"peer_addr\":\"10.0.0.1\",
    \"listen_port\":18515,
    \"msg_size\":65536,
    \"duration\":30,
    \"gid_index\":3,
    \"handshake_id\":\"$HID\"
  }"

# → { "status":"started", "job_id":"&lt;uuid&gt;",
#     "handshake_id":"&lt;HID&gt;", ... }</pre>

<table>
  <tr><th>Field</th><th>Type</th><th>Notes</th></tr>
  <tr><td><code>role</code></td><td>"server" | "client" (required)</td>
      <td>Client requires <code>peer_addr</code>.</td></tr>
  <tr><td><code>test</code></td><td>str (required)</td>
      <td>One of: <code>send_bw / write_bw / read_bw / send_lat /
          write_lat / read_lat / atomic_bw / atomic_lat</code>.</td></tr>
  <tr><td><code>device</code></td><td>str</td>
      <td>e.g. <code>"mlx5_0"</code>. Defaults to perftest's
          first-discovered HCA when omitted.</td></tr>
  <tr><td><code>ib_port</code></td><td>int (default 1)</td>
      <td>Physical port on the HCA.</td></tr>
  <tr><td><code>peer_addr</code></td><td>str (client only)</td>
      <td>IP of the server-side perftest.</td></tr>
  <tr><td><code>listen_port</code></td><td>int</td>
      <td>perftest's <code>-p</code> control port. Server allocates
          from 18515 up when omitted.</td></tr>
  <tr><td><code>msg_size</code></td><td>int (bytes)</td>
      <td>Per-op payload (<code>-s</code>). Default 65536.</td></tr>
  <tr><td><code>qp_count</code></td><td>int</td>
      <td>Parallel QPs (<code>-q</code>). >1 changes BW row to per-QP totals.</td></tr>
  <tr><td><code>duration</code></td><td>int (sec)</td>
      <td><code>-D</code>. Mutually exclusive with
          <code>iterations</code>; duration wins.</td></tr>
  <tr><td><code>iterations</code></td><td>int</td>
      <td><code>-n</code>. Used only when duration is absent.</td></tr>
  <tr><td><code>mtu</code></td><td>int 1..5</td>
      <td>perftest <code>-m</code> encoding: 1=256B, 2=512, 3=1024,
          4=2048, 5=4096.</td></tr>
  <tr><td><code>tx_depth</code></td><td>int</td>
      <td>TX queue depth (<code>-t</code>). Default 128.</td></tr>
  <tr><td><code>gid_index</code></td><td>int</td>
      <td><code>-x</code>. RoCEv2-IPv4 on Mellanox is usually 3.
          Native IB is 0. <code>show_gids</code> on the host
          enumerates the slots.</td></tr>
  <tr><td><code>bidirectional</code></td><td>bool</td>
      <td><code>-b</code>. Only meaningful for the <code>_bw</code> tests.</td></tr>
  <tr><td><code>use_event</code></td><td>bool</td>
      <td><code>-e</code> — interrupt mode instead of polling.</td></tr>
  <tr><td><code>cpu_util</code></td><td>bool</td>
      <td><code>--cpu_util</code> — adds CPU% to perftest's row.</td></tr>
  <tr><td><code>handshake_id</code></td><td>str (UUID)</td>
      <td>Pairing tag. Auto-allocated server-side when absent.</td></tr>
  <tr><td><code>note</code></td><td>str</td>
      <td>Operator label stored on the handshake record.</td></tr>
  <tr><td><code>perf_extra</code></td><td>str | [str]</td>
      <td>Escape hatch — appended verbatim to argv. Use sparingly.</td></tr>
</table>

<h3>28d. Poll a job's live stats</h3>

<pre>curl http://tg-a:5050/api/rdma/perftest/job/&lt;job_id&gt;

# →
# { "job": {
#     "job_id":"...", "handshake_id":"...",
#     "role":"server", "test":"send_bw",
#     "tool":"/usr/bin/ib_send_bw",
#     "device":"mlx5_0", "ib_port":1, "listen_port":18515,
#     "running": true,
#     "started_at": 1717440000.0,
#     "finished_at": null,
#     "local_qpn":"0x000014", "local_psn":"0x9b8a5f",
#     "remote_qpn":"0x000015","remote_psn":"0x88af3c",
#     // Populated once perftest emits its data row:
#     "final_msg_size_bytes": 65536,
#     "final_iterations":    1500000,
#     "final_bw_avg_gbps":   96.40,
#     "final_bw_peak_gbps":  96.43,
#     "final_msg_rate_mpps": 0.183,
#     // For _lat tests instead:
#     "final_lat_min_us":  null, "final_lat_avg_us": null,
#     "final_lat_max_us":  null, "final_lat_p99_us": null,
#     "stdout_tail": [ ... last 5000 lines, capped ... ],
#     "error": null
# }}</pre>

<h3>28e. List all jobs on this TG</h3>
<pre>curl http://&lt;server&gt;:5050/api/rdma/perftest/jobs
# → { "jobs": [ &lt;job dict&gt;, ... ] }</pre>

<h3>28f. Stop a job</h3>
<pre>curl -X POST http://&lt;server&gt;:5050/api/rdma/perftest/stop \
  -H "Content-Type: application/json" \
  -d '{ "job_id":"&lt;uuid&gt;", "forget_pair":true }'

# → { "status":"stopped", "job":{...},
#     "forgot_handshake_id":"..."  (when forget_pair was true) }</pre>

<p>Stop is SIGTERM (with a 3 s grace before SIGKILL).
<code>forget_pair</code> additionally drops the pairing record from
the handshake registry, but does <b>not</b> contact the other half on
the peer TG — caller must call stop on each side independently.</p>

<h3>28g. Handshake pairing registry</h3>
<pre># List all active pairings on this TG.
curl http://&lt;server&gt;:5050/api/rdma/handshakes

# Get one pairing (both halves, if both registered here).
curl http://&lt;server&gt;:5050/api/rdma/handshakes/&lt;id&gt;

# Drop a pairing tag (does NOT stop the underlying jobs).
curl -X DELETE http://&lt;server&gt;:5050/api/rdma/handshakes/&lt;id&gt;</pre>

<p class="muted"><b>What the handshake_id buys you:</b> when the
two halves of one Blast RDMA Flow are on different TGs, the GUI
queries each TG's <code>/api/rdma/perftest/jobs</code> and groups by
shared <code>handshake_id</code> to render both sides in one row.
Stop a pair from the Blast RDMA dialog → it issues
<code>stop</code> on each side with <code>forget_pair: true</code>,
then DELETEs the pairing record so it disappears from the registry.</p>

<h3>28h. Polling pattern for a two-sided test</h3>
<pre>#!/bin/bash
HID=$(uuidgen)
SRV=tg-a:5050
CLI=tg-b:5050

# Start server, capture job_id + listen_addr.
S=$(curl -s -X POST http://$SRV/api/rdma/perftest/start \
  -H "Content-Type: application/json" \
  -d "{\"role\":\"server\",\"test\":\"send_bw\",\"device\":\"mlx5_0\",
       \"duration\":30,\"handshake_id\":\"$HID\"}")
SJID=$(echo $S | jq -r .job_id)
LADDR=$(echo $S | jq -r .listen_addr)
LPORT=$(echo $S | jq -r .listen_port)

# Start client pointing at the server.
C=$(curl -s -X POST http://$CLI/api/rdma/perftest/start \
  -H "Content-Type: application/json" \
  -d "{\"role\":\"client\",\"test\":\"send_bw\",\"device\":\"mlx5_0\",
       \"peer_addr\":\"$LADDR\",\"listen_port\":$LPORT,
       \"duration\":30,\"handshake_id\":\"$HID\"}")
CJID=$(echo $C | jq -r .job_id)

# Poll every 2 s for 35 s.
for i in $(seq 1 18); do
  echo "--- t=$((i*2))s"
  curl -s http://$SRV/api/rdma/perftest/job/$SJID | jq '.job
    | "server  bw_avg=\(.final_bw_avg_gbps) Gbps  mpps=\(.final_msg_rate_mpps)"'
  curl -s http://$CLI/api/rdma/perftest/job/$CJID | jq '.job
    | "client  bw_avg=\(.final_bw_avg_gbps) Gbps  mpps=\(.final_msg_rate_mpps)"'
  sleep 2
done</pre>
"""


def show_api_guide(parent=None):
    """Open the REST API Guide dialog. Reachable from Help → API Guide."""
    _open_help_dialog(parent, "Netgen Server — REST API Guide", _API_GUIDE_HTML)


_FEATURE_GUIDE_HTML = r"""
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
         sans-serif; color: #1f2937; line-height: 1.55; font-size: 12px; }
  h1 { color: #1e40af; font-size: 20px; margin: 0 0 8px 0; }
  h2 { color: #374151; font-size: 15px; margin-top: 22px;
       border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
  h3 { color: #4b5563; font-size: 13px; margin-top: 14px; }
  p, li { color: #374151; }
  code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
         background: #f3f4f6; padding: 1px 5px; border-radius: 3px;
         font-size: 11px; color: #1e3a8a; }
  .ver { display: inline-block; background: #e0e7ff; color: #3730a3;
         padding: 1px 8px; border-radius: 10px; font-size: 10px;
         font-weight: 600; margin-right: 6px; }
  .new { background: #dcfce7; color: #166534; }
  .fix { background: #fef3c7; color: #92400e; }
  .muted { color: #6b7280; font-size: 11px; }
  ul { margin: 4px 0; padding-left: 22px; }
  li { margin: 3px 0; }
  .where { color: #1d4ed8; font-weight: 600; font-size: 11px; }
  table { border-collapse: collapse; margin-top: 6px; font-size: 11px; }
  th, td { border: 1px solid #d1d5db; padding: 5px 9px; text-align: left;
           vertical-align: top; }
  th { background: #f3f4f6; color: #374151; font-weight: 600; }
</style>

<h1>Netgen — What's New (Feature Guide)</h1>
<p class="muted">A tour of the user-visible features added since 0.2.41,
organised so the menu / tab where you'll find each one is obvious. For
the REST endpoint signatures see <strong>Help → API Guide</strong>.</p>

<h2><span class="ver new">0.3.15</span> highlights — RDMA QP ceiling visibility</h2>
<p>Operators kept asking "how many QPs can my HCA actually handle?"
The GUI had no answer — both Blast a RDMA Flow and the per-stream
RDMA params capped at 1024 (a netgen-side guess) and the device
viewer didn't show the hardware's real <code>max_qp</code>. Now it
does:</p>
<ul>
  <li><b>HCA caps shown per device</b> in
      <code>Tools → RDMA → RDMA Devices…</code> — new line per HCA:
      <code>HCA caps: max_qp=262,144 max_qp_wr=32,768 max_cq=16.8M
      max_mr=16.8M max_pd=8.4M max_sge=30</code>. Pulled from
      <code>ibv_devinfo -v -d &lt;dev&gt;</code>, ~30 ms per probe.
      Graceful "(ibv_devinfo not available)" when the binary isn't
      installed or perms blocked the probe.</li>
  <li><b>GUI QP-count ceiling raised 1024 → 131,072</b> in both
      Blast a RDMA Flow + per-stream RDMA params. Matches typical
      Mellanox ConnectX-7 <code>max_qp</code>. Operators who
      legitimately want to stress test 64K+ QPs no longer have to
      patch source.</li>
  <li><b>New API field set</b> in <code>/api/rdma/devices</code>:
      each device dict now carries <code>max_qp</code> plus 6
      sibling cap fields. See API Guide for the full schema.</li>
  <li><b>11 new tests</b> cover the parser (hex bitfield ignored,
      decorated values stripped, partial output preserved across rc
      ≠ 0), graceful failure paths (binary missing, subprocess
      timeout, empty device name), and the end-to-end integration
      via <code>list_rdma_devices</code> with mocked sysfs + mocked
      ibv_devinfo.</li>
</ul>
<p class="muted">Practical envelope reminder: 1–16 QPs covers most
BW tests, 32–128 for queue saturation, 256+ is synthetic stress.
The new GUI ceiling is a safety net, not a recommendation.</p>

<h2><span class="ver fix">0.3.13</span> highlights — RDMA parser fixes (lab-verified)</h2>
<p>Two parsing gaps from v0.3.12 surfaced during bring-up on a
Mellanox lab box (svl-d-ai-srv01, 6 mlx5 HCAs, perftest 24.04.0-0.41).
Functional RDMA worked end-to-end (verified <b>316.48 Gbps</b> loopback
Send-BW on a 400 G port + clean SIGTERM on Stop), but the
<b>RDMA Devices</b> viewer was showing misleading info:</p>
<ul>
  <li><b>MTU correctly reported per port.</b> Modern Mellanox kernels
      (5.x+) write <code>/sys/class/infiniband/&lt;dev&gt;/ports/&lt;n&gt;/
      active_mtu</code> as a bare IB MTU enum digit (<code>1</code> →
      256 B … <code>5</code> → 4096 B per IBA §3.5.3). v0.3.12's
      regex required ≥3 digits and returned 0 for every NIC.
      Now handles bare-enum / "enum: bytes" / raw-bytes / "[B]"-suffix
      formats; 16 parameterised regression tests pin every variant.</li>
  <li><b>perftest version detection actually works now.</b> The newer
      Ubuntu 24.04 + Mellanox MOFED perftest builds (e.g.
      <code>24.04.0-0.41</code>) don't print a useful version on
      <code>--version</code>. v0.3.13 falls through:
      <code>--version → -V → dpkg -s → rpm -q → apk info</code> —
      first hit wins. RDMA Devices viewer now shows
      <code>perftest 24.04.0-0.41 present</code> instead of
      <code>perftest installed (?)</code>.</li>
</ul>
<p class="muted">No API surface change. Pure parser + Help-guide
polish. Upgrade the wheel to see the corrected MTU + version in the
RDMA Devices view.</p>

<h2><span class="ver new">0.3.12</span> highlights — RDMA traffic generation</h2>
<p>Brand-new <strong>RDMA</strong> submenu under <code>Tools → RDMA</code>,
backed by <code>perftest</code> (the standard RDMA / RoCEv2 benchmarking
suite). Covers both client-side and server-side orchestration across
one or two TGs:</p>
<ul>
  <li><b>Blast a RDMA Flow</b> (<code>Tools → RDMA → Blast a RDMA Flow…</code>)
      — pick a server-side TG + RDMA device, a client-side TG + device,
      a test type (<b>Send / RDMA Write / RDMA Read × Bandwidth / Latency</b>),
      and a parameter set (msg size, QP count, duration, MTU, GID
      index). Both halves spawn in parallel; the dialog brokers the
      peer-address handshake, polls live BW (Gbps) / message rate
      (Mpps) / latency (µs / p99) from each side. Single-TG selection
      runs a loopback test. Non-modal — open multiple to fan out across
      NIC pairs.</li>
  <li><b>RDMA as a per-stream engine</b> — Add Stream → Runtime Engine
      tab now has a 3-way <b>Engine</b> dropdown: <code>Scapy</code> /
      <code>DPDK (tx_worker)</code> / <code>RDMA (perftest)</code>.
      Picking RDMA reveals a params group (test type, device, peer
      address, msg size, QP count, duration, GID index, bidirectional)
      that's saved/loaded with the stream. Starting an RDMA stream
      spawns the perftest client and routes stats through the same
      Streams-tab telemetry as Scapy/DPDK streams.</li>
  <li><b>RDMA Devices</b> viewer (<code>Tools → RDMA → RDMA Devices…</code>)
      — multi-server enumeration of <code>/sys/class/infiniband</code>:
      per-port state (ACTIVE / DOWN), link_layer (Ethernet vs
      InfiniBand), rate, active MTU, GIDs. Also shows whether perftest
      is installed on each selected TG. Use as a pre-flight before
      Blast a RDMA Flow.</li>
  <li><b>RDMA Jobs</b> viewer (<code>Tools → RDMA → RDMA Jobs…</code>)
      — list every active and recently-finished perftest invocation
      per TG, annotated with <code>handshake_id</code> so the two
      halves of one Blast RDMA Flow appear as a pair.</li>
  <li><b>Eight new <code>/api/rdma/*</code> endpoints</b> — see Help
      → API Guide for the full list, including device discovery,
      perftest install probe, start/stop/list jobs, and the
      handshake-pairing registry.</li>
</ul>
<p class="muted"><b>Prerequisites:</b>
<code>apt install perftest rdma-core</code> on each TG. RDMA-capable NIC
(Mellanox / Intel iRDMA / Broadcom). RoCEv2 requires a routable subnet
between the two TGs.</p>

<h2><span class="ver new">0.3.11</span> highlights</h2>
<p>Major operator-facing additions this release:</p>
<ul>
  <li><b>Blast a DPDK Flow</b> dialog (DPDK menu → "Blast a Flow") —
      one-click: bind a NIC → fire a 1500-byte UDP flow at line rate.
      Sized for ACTUAL wire-speed: 100 G needs only 8.2 Mpps at MTU,
      well inside a single tx_worker core. Override to 64 B for the
      max-pps stress test. Now <b>non-modal</b> so the main window
      stays interactive, with <b>multi-NIC parallel blast</b>
      support — open one dialog per interface; windows cascade so
      they don't stack.</li>
  <li><b>Stream templates library</b> (Add Stream → Template dropdown) —
      14 ready-made profiles covering line-rate (64 B / 1500 B
      sibling), IMIX, LAG / RSS / ECMP hash test, latency probe,
      VXLAN encap, ICMP flood, VLAN-tagged, and 6 new
      <b>scaling</b> templates (MAC sweep 1024, IPv4 dst /24, IPv4
      src 256, IPv6 dst 64, 5-tuple RSS, VLAN 4094). All use unified
      <code>02:xx</code> locally-administered MAC defaults that
      match the Blast a Flow dialog.</li>
  <li><b>Add Stream dialog polish</b> — Protocol Selection: dead L1
      group and Payload Random radio removed; second "L4" group
      relabelled <i>Encap (over UDP)</i>; jumbo frames (>1518)
      finally enterable; VLAN ID validator; tab labels stop eliding;
      compact layout. Protocol Data: RoCEv2 "Use Performance Server"
      checkbox and Payload data bytes now round-trip correctly
      (silent loss fixed); RoCEv2 GID mode dropdowns finally enable
      step/count fields; ARP MAC/IP fields gain live red-border
      validators; TCP/UDP override-uncheck clears stale value;
      MPLS template int → str coercion guard. PCAP fields restore
      on edit-existing-stream (was silent loss). Packet View tree
      finally shows custom payload bytes (wrong-key-path bug).</li>
  <li><b>Cross-layer save guards</b> — accept() now warns on:
      L2=None + L3=IP, Frame Type=Random with Min ≥ Max, PCAP
      enabled + protocol stack picks both set.</li>
  <li><b>Settings dialog crash fixed</b> (File → Settings) — opening
      it used to throw <code>OverflowError</code> on PyQt5 because
      the BGP-ASN spinbox tried to set a uint32 range. Now a
      QLineEdit + validator that supports the full 4-byte ASN range.</li>
  <li><b>API Guide</b> updated with <code>/api/dpdk/hugepages</code>
      (corrected v0.3.11 schema), <code>/api/admin/bind_history</code>,
      <code>/api/dpdk/interfaces</code> + <code>verify</code> +
      <code>iommu</code> + <code>load_modules</code>, plus the
      <code>actual_engine</code> / <code>fallback_reason</code> /
      <code>per_core_tx_count</code> fields in stream stats, plus a
      callout on the asymmetric stop-payload schema (LIST not DICT).
      <span class="where">Where: Help → API Guide.</span></li>
</ul>

<h2>Streams tab</h2>

<h3><span class="ver new">0.2.46</span> Live "running / total" status chip</h3>
<p>Right end of the Streams action bar shows
<code>● N running · M total</code> — green when anything's emitting,
slate when idle. Updates every time the table repopulates, which now
happens on every start / stop / apply / refresh. <span class="where">Where: Streams tab,
right of the search box.</span></p>

<h3><span class="ver new">0.2.65</span> SR-MPLS label stack</h3>
<p>MPLS section in Stream Edit gains a "Label stack" text field —
comma-separated SIDs (decimal or hex, e.g. <code>16000, 16001, 16002</code>).
When set, the frame carries a multi-label MPLS stack (ethertype
<code>0x8847</code>, BOS bit auto-handled). Legacy single-label streams
still work unchanged. <span class="where">Where: Stream Edit → Protocol Data → MPLS.</span></p>

<h3><span class="ver fix">0.2.57</span> No more 2-second table flicker</h3>
<p>The Streams table used to fully rebuild every 2 s (driven by the
stats poll) just to repaint a status icon. Now an in-place updater
touches only the col-0 Status cell when something actually changed.
Inline editing is unconditionally safe across polls; selection never
gets wiped; rows don't flicker. <span class="where">Where: throughout the Streams tab —
the change is invisible until you notice it stopped happening.</span></p>

<h2>L2 / Multicast Emulation tab</h2>

<h3><span class="ver new">0.2.41 → 0.2.45</span> Spirent-style redesign</h3>
<p>Compact one-row header, devices-tab-style action bar with primary
Start / neutral Stop / red Stop-all, plain-default-Qt table matching
the rest of the app, status pill + protocol badge per row,
human-readable byte / pps / uptime formatting. <span class="where">Where: L2 Emulation tab.</span></p>

<h3><span class="ver new">0.2.60</span> QinQ (802.1ad) double-tagging</h3>
<p>Start-session dialog gains <strong>Outer VLAN ID</strong> +
<strong>Outer VLAN PCP</strong> spinners. Default 0 → single-tagged or
untagged (unchanged from 0.2.41). When set, the frame egresses with
TPID <code>0x88a8</code> outer + <code>0x8100</code> inner; the
sessions table's VLAN column shows <code>&lt;outer&gt; » &lt;inner&gt;</code> with
the full S-/C-VLAN semantics in the tooltip. No <code>vlanN</code>
subinterface needed. <span class="where">Where: L2 Emulation → Start → Common settings.</span></p>

<h3><span class="ver new">0.2.61</span> BFD (RFC 5880) emitter</h3>
<p>Sixth L2 protocol after LACP / LLDP / VRRP / IGMP / PIM. Sends RFC
5880 control packets at the configured cadence — enough to keep a
peer's session Up (default State=Up, detect_mult=3, 1 Hz) or
deliberately tear one down (State=Down). Sub-second cadence supported
for aggressive BFD. <span class="where">Where: L2 Emulation → Start → protocol = BFD.</span></p>

<h2>Devices tab → VXLAN sub-tab</h2>

<h3><span class="ver new">0.2.62 → 0.2.67</span> EVPN bulk-inject (Type-2 / Type-5)</h3>
<p>Backend landed first (Type-2 inject in 0.2.62, Type-5 in 0.2.66 —
both scriptable via curl); the GUI arrived in 0.2.63 (Type-2 form)
and 0.2.67 (Type-5 form + tab selector + kind-aware Clear). New
"EVPN Inject" button (right of Apply / Refresh on the VXLAN sub-tab
action bar) opens a tabbed dialog:</p>
<ul>
  <li><strong>Type-2 (MAC / IP)</strong>: pick VXLAN iface, base MAC,
      optional base IP + remote VTEP + L3 iface, count. Inject N
      synthetic entries that FRR/BGP advertises as Type-2 routes.</li>
  <li><strong>Type-5 (IP Prefix)</strong>: pick egress dev, base
      prefix + length, optional gateway + VRF table, count. Inject N
      kernel routes that FRR advertises as Type-5 (RFC 7432).</li>
</ul>
<p>Active-injections table at the bottom shows every running inject
on this server (Type-2 blue, Type-5 violet, with iface / count /
remote VTEP) and a per-row <strong>Clear</strong> button that routes
to the right cleaner endpoint based on the row's kind.
<span class="where">Where: Devices → VXLAN sub-tab → EVPN Inject button.</span></p>

<h2>Statistics dock</h2>

<h3><span class="ver new">0.2.58</span> Latency p95 + Export CSV</h3>
<p>The latency tooltip on the Stream Statistics table now shows the
<strong>p95 latency</strong> alongside the existing p50 / p99
(most SLAs are stated in p95). A new <strong>Export CSV</strong>
button next to "Clear Stats" dumps both stats tables (Interface +
Stream) to a single CSV with a header block and per-section markers
— attach to a test report without screenshotting.
<span class="where">Where: Statistics dock — Stream Statistics tab (tooltip on Latency cell);
Clear Stats row (Export CSV button).</span></p>

<h2>Tools menu</h2>

<h3><span class="ver new">0.2.59</span> RFC 2544 latency + HTML report</h3>
<p>The RFC 2544 throughput dialog (Tools → RFC 2544 Throughput Test)
gains:</p>
<ul>
  <li>A <strong>"Capture latency (RFC 2544 §26.2)"</strong> checkbox
      — embeds NLAT timestamps in each binary-search trial so the
      results table populates three new columns:
      <strong>Lat p50 / p95 / p99 (µs)</strong>.</li>
  <li>An <strong>"Export HTML Report"</strong> button — single
      self-contained HTML file with test parameters, full results
      table, and a best-throughput summary. Open in any browser and
      Print → Save-as-PDF for a formal deliverable.</li>
</ul>
<p>Old runs still show "—" in the latency columns; pre-0.2.59 servers
work too (the client falls back gracefully).
<span class="where">Where: Tools → RFC 2544 Throughput Test.</span></p>

<h2>Devices tab → Preflight bar</h2>

<h3><span class="ver new">0.2.70</span> Preflight findings bar</h3>
<p>A thin colour-coded strip at the top of the Devices sub-tab tells
you whether your config is going to fly <em>before</em> you click
Apply. Three pills — <code>● N errors · ● N warnings · ● N OK</code>
— with the bar background tinted by worst severity (red / amber /
green / neutral).</p>
<ul>
  <li><strong>Details…</strong> button opens a modal table listing
      every finding: Level / Code / Device / Interface / Message,
      with the level column colour-coded so the eye picks up errors
      first.</li>
  <li><strong>↻</strong> button forces an immediate re-check; the
      bar also auto-polls every 60 s so unsolicited edits (someone
      hitting the API directly) still show up.</li>
  <li>Catches today: BGP without remote ASN / loopback, VXLAN
      missing VNI / local IP / remote peers, OSPF + IS-IS without
      area, cross-device IPv4 collisions. (Heads-up surface — Apply
      still runs whether you address the findings or not.)</li>
  <li>Defensively quiet: HTTP failures, no-server-selected, malformed
      JSON all leave the previous pill values intact and log a debug
      line. No modal pop-ups on flaky links.</li>
</ul>
<p><span class="where">Where: Devices tab → top of the sub-tab, above the filter row.</span></p>

<h3><span class="ver new">0.2.71</span> Bar refreshes immediately after Apply</h3>
<p>When you click Apply (single-device or the multi-device toolbar
batch), the bar repaints within a second — no waiting up to 60 s for
the next poll. Fires on both success and failure so you also get the
"all clean" confirmation, not only new findings.
<span class="where">Where: Devices tab — invisible until you notice the bar updates the moment your Apply completes.</span></p>

<h2>Server / API surface</h2>

<h3><span class="ver new">0.2.68</span> Preflight checks endpoint</h3>
<p>The backend behind the bar above — <code>GET /api/preflight/check</code>
returns the findings as JSON for scripts that want to wire their own
gates (CI checks, pre-deploy hooks). See the API Guide §25 for the
exact response shape.</p>


<h2>DPDK telemetry &amp; admin</h2>

<h3><span class="ver new">0.2.75</span> Pre-flight DPDK fallback warnings</h3>
<p>Enabling Use-DPDK on a stream that isn't DPDK-compatible (TCP,
IPv6, MPLS stack, QinQ) used to silently fall back to Scapy. Now the
start endpoint pre-flights the engine decision and the GUI shows ONE
end-of-batch dialog naming every affected stream + the reason. The
Use-DPDK checkbox tooltip also spells out the supported envelope
(IPv4 + UDP + ≤1 VLAN tag + no MPLS). <span class="where">Where:
Stream Edit → Variable Fields → Use DPDK tooltip; warning dialog
fires after Start.</span></p>

<h3><span class="ver new">0.2.76</span> DPDK readiness chip in the main status bar</h3>
<p>A small colour-coded indicator (right-aligned in the
QMainWindow status bar) tells you at a glance whether DPDK will
work on the currently-selected server BEFORE you enable Use-DPDK
on a stream. Green = ready; amber = degraded (Mellanox / mlx5
still works); red = unavailable; gray = no server selected.
Hover for per-subsystem detail (libdpdk, tx_worker, hugepages,
IOMMU, vfio-pci) and — since 0.2.77 — the libdpdk version + the
binary's build date so ABI drift after a server upgrade is
visible at a glance. <span class="where">Where: bottom-right of
the main window.</span></p>

<h3><span class="ver new">0.2.76</span> NIC bind safety guards</h3>
<p>Binding the management interface to vfio-pci used to lock
operators out of the host. Now <code>/api/dpdk/bind</code> refuses
with HTTP 409 + a specific reason when the candidate NIC carries
the default route, is the SSH session's interface, or has an
active traffic stream. The GUI surfaces the reason verbatim with
a <strong>Bind anyway</strong> destructive-role button for the
"I really mean it" case (e.g. binding from console with a spare
NIC for SSH). <span class="where">Where: Tools → DPDK → Bind
Interface.</span></p>

<h3><span class="ver new">0.2.77</span> Runtime DPDK fallback surfaces in the Engine column</h3>
<p>When the launcher has to swap engines mid-flight (tx_worker
exits with the Broadcom ULP rc=100, or an exception during DPDK
handoff), the Engine column now renders <strong>"Scapy ⚠ (was
DPDK)"</strong> in amber with the reason in the cell tooltip.
No more grep-journalctl to figure out why throughput halved.
<span class="where">Where: Statistics dock → Stream Statistics →
Engine column.</span></p>

<h3><span class="ver new">0.2.77</span> Hugepage allocation feedback</h3>
<p>The Configure Hugepages dialog now reports both the requested
AND the actually-allocated count. When the kernel caps the
request (memory fragmentation, NUMA imbalance), you see
<strong>"⚠ Requested 8192, Actually allocated 4096"</strong>
instead of a misleading "success" toast.
<span class="where">Where: Tools → DPDK → Configure Hugepages.</span></p>

<h3><span class="ver new">0.2.77</span> Inline Unbind button in DPDK Status</h3>
<p>Every vfio-pci-bound interface in the DPDK Status dialog gets
a per-row <strong>Unbind</strong> button. No more trip to the
separate Tools → Unbind Interface menu action.
<span class="where">Where: Tools → DPDK → Show Status.</span></p>


<h2>Devices tab → Preflight surfaces</h2>

<h3><span class="ver new">0.2.70 → 0.2.71</span> Preflight findings bar + Apply hook</h3>
<p>The thin colour-coded strip at the top of the Devices sub-tab
shows three pills (errors / warnings / OK), tints the bar
background by worst severity, and opens a sortable Details modal
on click. Polls every 60 s and refreshes immediately after Apply.
Defensively quiet — HTTP failures hold previous values, never
modal-alert. <span class="where">Where: Devices tab → top of the
sub-tab.</span></p>

<h3><span class="ver new">0.2.74</span> Export findings + Details modal sortable</h3>
<p>Details modal gains <strong>Export CSV</strong> / <strong>Export
JSON</strong> buttons (timestamped filenames) and header-click
sorting. Operators paste into tickets without screenshotting.
<span class="where">Where: Preflight bar → Details…</span></p>

<h3><span class="ver new">0.2.78</span> Per-device dot in Devices table</h3>
<p>The bar tells you N errors total; the per-device dot tells you
<em>which</em>. A red/amber/green prefix on each Device Name cell
driven by the bar's <code>by_device</code> breakdown. Severity
wins (error > warning > clean); tooltip rolls in alongside any
existing one. <span class="where">Where: Devices tab → Device
Name column.</span></p>

<h3><span class="ver new">0.2.78</span> Pill-click filter on preflight bar</h3>
<p>Click the red errors pill → Details modal opens
<strong>pre-filtered to errors only</strong>. Same for warnings.
Saves the "open Details, then mentally filter" step.
<span class="where">Where: Preflight bar pills.</span></p>


<h2>VXLAN sub-tab additions</h2>

<h3><span class="ver new">0.2.74</span> EVPN active-injections row tooltips</h3>
<p>Hover any cell in the active-injections table to see kind,
count, iface, base MAC / prefix, remote VTEP, VRF table — no
need to open the per-row Details to know what's running.
<span class="where">Where: VXLAN sub-tab → EVPN Inject button →
Active table.</span></p>

<h3><span class="ver new">0.2.78</span> EVPN active-injections chip</h3>
<p>A small <strong>⚡ EVPN: N active</strong> chip on the VXLAN
sub-tab action bar (right side) polls
<code>/api/evpn/type2/list</code> every 30 s and shows the
combined Type-2 + Type-5 count. Click opens the EVPN Inject
dialog directly. Operators stop having to open the dialog just to
check whether anything's running.
<span class="where">Where: VXLAN sub-tab action bar.</span></p>


<h2>Streams tab additions</h2>

<h3><span class="ver new">0.2.78</span> SR-MPLS row badge</h3>
<p>Streams with an MPLS label stack are now suffixed with
<strong>[MPLS ×N]</strong> (or <strong>[MPLS]</strong> for a
single label / legacy single-label field) in the Details column.
Scan the table for MPLS streams without opening each editor.
<span class="where">Where: Streams tab → Details column.</span></p>


<h2>L2 Emulation additions</h2>

<h3><span class="ver new">0.2.74</span> Full BFD RFC 5880 field set</h3>
<p>The BFD panel gains <strong>Diagnostic</strong> (combo of the
9 RFC 5880 §4.1 codes) and <strong>Required Min Echo RX</strong>
(µs spinner). The backend accepted both as kwargs since 0.2.61
but silently defaulted them to 0; operators exercising specific
peer behaviours (echo mode, simulated path-down) can now reach
them without curl. <span class="where">Where: L2 Emulation →
Start → protocol = BFD.</span></p>

<h3><span class="ver new">0.2.81</span> Submit-time MAC + IP validators</h3>
<p>Every MAC / IP field across LACP / LLDP / VRRP / IGMP / PIM /
BFD now validates at submit time. Typos like
<code>00:11:22:33:44:ZZ</code> or <code>192.168.1.999</code>
surface as a dialog warning naming the field, not as opaque
"invalid MAC" deep in the sessions table's Last Error column an
hour later. Also: VRRP v2 + IPv6 mismatch rejected up-front (was
silently reverting to v3); PIM <code>generation_id</code> bounds-
checked to 32 bits per RFC 7761 §4.9.5; IGMP <code>group</code>
field tooltip clarifies <code>0.0.0.0</code> is the General Query
group per RFC 2236 §3.</p>

<h3><span class="ver new">0.2.82</span> IGMPv1 (RFC 1112) support</h3>
<p>Legacy IGMPv1 query / report generation for testing older
multicast routers or IGMP-snooping fallback on switches that still
accept v1. Type code default <code>0x12</code> (Membership Report)
→ IP dst = group; override to <code>0x11</code> (Query) → IP dst
= <code>224.0.0.1</code> (ALL-SYSTEMS), L2 dst auto-mapped to
<code>01:00:5e:00:00:01</code>. <span class="where">Where: L2
Emulation → Start → protocol = IGMP → Version = v1.</span></p>

<h3><span class="ver new">0.2.83</span> VRRPv2 authentication (RFC 3768 §5.3.6)</h3>
<p>New <strong>Auth type</strong> combo (0 = None / 1 = Simple Text
Password / 2 = IPAH) + <strong>Auth data</strong> field (up to 8
ASCII bytes, packed into auth1 + auth2 NUL-padded). When Version =
v3 both fields disable with a tooltip citing RFC 5798 §5.1 — auth
was removed from v3 entirely, so the GUI surfaces that intent
rather than silently ignoring the fields.</p>

<h3><span class="ver new">0.2.84</span> Sessions-table polish + Frame preview</h3>
<p>Four POLISH items closed in one bundle:</p>
<ul>
  <li><strong>Last Error column</strong> truncation 120 → 200 chars
      (full text still in tooltip). Scapy tracebacks fit now.</li>
  <li><strong>Filter QLineEdit</strong> in the action bar — substring
      match on protocol / iface / session_id, case-insensitive.
      Sorting deliberately left off (Qt's setSortingEnabled doesn't
      move cellWidgets so a sort would orphan the per-row Stop
      buttons).</li>
  <li><strong>Per-row Stop button</strong> on every running session.
      One click → POST /api/l2/stop with that session_id, refresh
      150 ms later.</li>
  <li><strong>Frame preview</strong> button in the Start dialog —
      pure <code>build_preview_frame()</code> helper renders the
      first frame each protocol would emit; modal shows scapy
      <code>summary()</code> + tcpdump-style hex without sending
      anything. <span class="where">Where: L2 Emulation → Start →
      Preview frame…</span></li>
</ul>


<h2>Devices tab additions</h2>

<h3><span class="ver new">0.2.85</span> Right-click menu + Delete-key shortcut</h3>
<p>Devices table gets a context menu (Apply selected / Copy /
Paste / Delete — mirrors the toolbar) that selects the row under
the cursor first. The Delete key is bound to
<code>remove_selected_device</code> with <code>Qt.WidgetShortcut</code>
context so it doesn't fire during inline editing.</p>

<h3><span class="ver new">0.2.85</span> DHCP apply now refreshes preflight</h3>
<p>Every other protocol's apply path kicked the preflight bar;
DHCP was the lone outlier. <code>DUPLICATE_IPV4</code> finding
state can flip when a DHCP-assigned address collides with a
static one, so the bar now repaints. Closes the 5-protocol
consistency loop opened in v0.2.71.</p>

<h3><span class="ver new">0.2.86</span> ISIS NET-ID validation (RFC 1195 §3.1)</h3>
<p>New <code>utils/isis_net.py</code> helper accepts variable-length
area IDs (AFI + Area(1–13) + SysID(6) + NSEL = 8–20 bytes total)
and enforces NSEL = 00 — the old inline check was hardcoded to one
specific length and didn't enforce NSEL. Wired into the inline-edit
path AND the Add Device dialog (with <code>allow_short_area=True</code>
so the <code>49.0001</code> AFI-only shortcut still pads to a
full NET).</p>

<h3><span class="ver new">0.2.87</span> OSPF area-id validation + normalisation</h3>
<p>New <code>utils/ospf_area.py</code> validates both forms (integer
0–4294967295 OR dotted-decimal A.B.C.D) and <strong>normalises</strong>
to the dotted form FRR puts on the wire. Operator typing
<code>1</code> for area 1 now gets <code>0.0.0.1</code> stored,
matching what <code>show ip ospf</code> reports later — eliminates
the silent mismatch that used to trigger apply-storms when
subsequent inline edits saw "1 → 0.0.0.1" as a change.</p>

<h3><span class="ver new">0.2.89</span> Empty-state placeholders on every protocol sub-tab</h3>
<p>BGP / OSPF / IS-IS / VXLAN / DHCP sub-tabs used to render as
blank rectangles on a fresh session — operators couldn't tell
"empty by design" from "broken connection" or "still loading".
New <code>EmptyStateOverlay</code> widget shows a centred dimmed
placeholder with a per-protocol "do this to add the first row"
hint. Hides the instant the first row arrives.</p>


<h2>Stateful TCP tab</h2>

<h3><span class="ver new">0.2.88</span> First-class Stateful TCP GUI tab</h3>
<p>The stateful-TCP feature (previously API/CLI-only) gets a
dedicated tab next to L2 Emulation, modelled on the L2 tab's
action-bar / sessions-table / poll-worker shape. Highlights:</p>
<ul>
  <li><strong>Roles</strong>: client + server in one dialog,
      switched via a button group driving a <code>QStackedWidget</code>.</li>
  <li><strong>Protocols</strong>: <code>raw</code> + <code>http</code>
      in v1 (<code>dns</code> + <code>sip</code> deferred to v2
      alongside matching <code>netgen-cli tcp</code> flag adds).</li>
  <li><strong>TLS both directions</strong>: client gets verify +
      SNI inputs; server gets cert + key file pickers with file-
      exists validation.</li>
  <li><strong>VRF passthrough</strong>: degrades gracefully on
      macOS / non-root Linux (last_error carries the reason;
      traffic still flows via the default routing table).</li>
  <li><strong>Loopback warning</strong>: inline amber callout
      when <code>dst_ip</code> parses as loopback and the interval
      is sub-5 ms — guards against the EADDRNOTAVAIL trap the
      v0.2.88 suite-flake fix addresses.</li>
  <li><strong>Sessions table</strong>: 10 columns + per-row Stop
      button + Status-cell tooltip with the full counter dump
      (per-protocol bins, last_error, retransmits, kernel RTT).</li>
  <li><strong>Graceful 404 degrade</strong>: same pattern as the
      L2 tab — slowed poll + amber chip + intercepted Start +
      auto-recovery when the endpoint returns.</li>
</ul>
<p><span class="where">Where: top tab bar → "Stateful TCP" (right
of L2 Emulation).</span></p>


<h2>Tools menu additions</h2>

<h3><span class="ver new">0.2.74</span> RFC 2544 timestamped export filenames</h3>
<p>Both the CSV and HTML exports now pre-populate the Save dialog
with <code>rfc2544_results_YYYY-MM-DD_HH-MM-SS.{csv,html}</code>
(same convention as the preflight export). Hit Save without
typing; re-exports never collide.
<span class="where">Where: Tools → RFC 2544 Throughput Test →
Export CSV / Export HTML Report.</span></p>


<h2>Reliability fixes worth knowing about</h2>

<h3><span class="ver fix">0.2.49 → 0.2.55</span> Streams table interaction fixes</h3>
<ul>
  <li>Inline editing no longer interrupted by the 2 s stats refresh.</li>
  <li>Delete actually removes the row from view (was stuck showing
      after model removal).</li>
  <li>Copy + Paste actually find the source stream (the bare-iface vs
      full-key mismatch is resolved via stream_id lookup).</li>
  <li><strong>Start All</strong> no longer silently skips every
      stream — the "stale/unknown ports" filter was comparing
      bare-iface cell text to full <code>"TG N - Port: iface"</code>
      keys.</li>
</ul>

<h3><span class="ver fix">0.2.88</span> Stateful-TCP suite-flake fix (TIME_WAIT / EADDRNOTAVAIL)</h3>
<p>Five stateful-TCP / DNS / SIP tests were running their client
with the default <code>interval_s=0</code>. On loopback a single
sender thread fires ~5 000 connect()s/sec, each leaving a 30–60 s
TIME_WAIT; the macOS ephemeral-port pool (~16 k) emptied inside
the same test → <code>OSError [Errno 49] Can't assign requested
address</code>. Subsequent tests inherited the dry port pool and
flaked sporadically — including <code>test_vrf_bind_no_op_on_non_linux</code>,
which was the long-standing user-visible symptom. Throttle to
<code>interval_s=0.02</code> (≈50 conns/sec) keeps the assertions
intact and the port pool healthy. 20 consecutive full-suite runs
green after the fix.</p>

<p>Each fix has its own regression test pinned in the test suite
(now at <strong>622 tests</strong> vs 103 before this push).</p>

<h2>RDMA workflow <span class="ver new">0.3.12</span> <span class="ver fix">refreshed 0.3.13</span></h2>
<p>Two complementary entry points for RDMA / RoCEv2 traffic generation,
both backed by <code>perftest</code>. <b>Lab-verified end-to-end on a
6-NIC Mellanox box (mlx5 driver, perftest 24.04.0-0.41):</b> 316.48 Gbps
Send-BW loopback on a 400 G port, clean SIGTERM on Stop, dialog
populated with all per-port state badges.</p>

<h3>Pre-flight (one-time per TG)</h3>
<ol>
  <li>perftest + rdma-core are <b>auto-installed</b> by v0.3.12+
      installs (via <code>install_ostg_complete.py</code> on every
      path, plus <code>install_dpdk.sh</code> for DPDK hosts).
      Pre-0.3.12 installs: <code>apt install perftest rdma-core</code>
      manually — Install Guide §10·m has per-distro one-liners.</li>
  <li>Open <code>Tools → RDMA → RDMA Devices…</code> for each TG.
      Confirm each port you intend to use shows
      <code>state = ACTIVE</code> + <code>link_layer = Ethernet</code>
      (RoCEv2) or <code>InfiniBand</code>. Bottom of the per-server
      block should say <code>[ok] perftest &lt;version&gt; present</code>.
      <span class="muted">(v0.3.13: shows real version e.g.
      <code>24.04.0-0.41</code> instead of <code>?</code>; shows real
      MTU per port instead of <code>0 B</code>.)</span></li>
  <li>For two-TG RoCEv2: an IPv4 subnet must be configured on the
      RDMA NICs and reachable both ways. <code>ping</code> the peer
      first. perftest exchanges QP info over a TCP socket on its
      <code>-p</code> port (default 18515; netgen allocates upward
      per concurrent job).</li>
</ol>

<h3>A — Ad-hoc benchmark (Blast a RDMA Flow)</h3>
<p><code>Tools → RDMA → Blast a RDMA Flow…</code> — the orchestrator
dialog. Select <b>1 TG</b> (loopback test) or <b>2 TGs</b>
(first = server, second = client) in the server tree first, then
open the dialog. The device combos auto-populate from
<code>/api/rdma/devices</code> on each selected TG, with per-port
state badges so you can pick ACTIVE ports at a glance:
<code>mlx5_3 [Ethernet, 400 Gb/sec (4X NDR), port1=ACTIVE]</code>.
Pick devices, pick a test (Send / Write / Read × BW / Latency),
tweak params, click <b>Start</b>. The dialog brokers the handshake
and polls live stats from both halves every 2 s.</p>
<p>Non-modal — open multiple to fan out across NIC pairs in parallel.
Stop is wired end-to-end (lab-verified rc=-15 SIGTERM, pairing
record dropped on Stop with <code>forget_pair=true</code>).</p>

<h3>B — Per-stream RDMA (Streams tab)</h3>
<p>For RDMA traffic in the same workflow as your other streams:</p>
<ol>
  <li>Add Stream → Runtime Engine tab → set <b>Engine</b> dropdown to
      <code>RDMA (perftest)</code>. This replaces the legacy
      <code>Use DPDK</code> checkbox semantics with a 3-way picker
      (Scapy / DPDK / RDMA).</li>
  <li>RDMA params group becomes visible below — fill <b>device</b>
      (e.g. <code>mlx5_3</code>), <b>peer address</b> (server-side
      TG's IP from the GID list in Devices view), test type, msg
      size, QP count, duration, GID index.</li>
  <li>Start a perftest <b>server</b> on the peer first (via Blast a
      RDMA Flow with role=server, or via another RDMA stream on the
      peer TG).</li>
  <li>Start your RDMA stream. The Streams-tab Engine column renders
      it in <b>purple</b> as <code>RDMA Send</code> / <code>RDMA Write</code> /
      <code>RDMA ReadL</code> etc. (distinct from DPDK blue).
      <code>Tools → RDMA → RDMA Jobs…</code> shows the pairing tagged
      <code>stream:&lt;your-stream-id&gt;</code>.</li>
</ol>
<p class="muted">Limitations of the per-stream RDMA path:
  perftest reports terminal-style stats only (no per-packet TX
  counter), so the Streams-tab TX bytes/sec shows polled bytes
  synthesised from perftest's data row, refreshed every 2 s — not
  real-time per-frame data like Scapy/DPDK streams. Latency tests
  report min / avg / p99 in the Live-stats panel of Blast a RDMA
  Flow (not the Streams-tab row).</p>

<h3>Common pitfalls + which GUI signal surfaces them</h3>
<table>
  <tr><th>Symptom in GUI</th><th>Likely cause</th><th>Fix</th></tr>
  <tr><td>Device combo says "(no RDMA devices)"</td>
      <td>Kernel missing RDMA support, or container has no
          <code>/sys/class/infiniband</code> mount</td>
      <td>Bare metal, or mount sysfs into the container</td></tr>
  <tr><td><code>[!] perftest tools NOT installed</code> in Devices view</td>
      <td>Pre-v0.3.12 install OR <code>--no-dpdk</code> with stale
          installer</td>
      <td>Upgrade wheel to v0.3.12+, OR
          <code>apt install perftest</code> manually</td></tr>
  <tr><td>Device shows <code>MTU: 0 B</code></td>
      <td>Pre-v0.3.13 server (sysfs <code>active_mtu</code> parser
          missed the bare IB MTU enum format)</td>
      <td>Upgrade server wheel to v0.3.13+</td></tr>
  <tr><td>Devices view says <code>perftest installed (?)</code></td>
      <td>Pre-v0.3.13 client probed only
          <code>&lt;tool&gt; --version</code> which returns nothing on
          newer perftest builds</td>
      <td>Upgrade client wheel to v0.3.13+ (adds dpkg/rpm/apk fallback)</td></tr>
  <tr><td>BW reads as 0 mid-test</td>
      <td>MTU mismatch — picked MTU &gt; <code>active_mtu</code></td>
      <td>Devices view shows real <code>active_mtu</code> per port
          (v0.3.13+); pick smaller</td></tr>
  <tr><td>Error: <code>"Unable to perform connection"</code></td>
      <td>Wrong GID index for the link_layer</td>
      <td>Default 3 = RoCEv2-IPv4 on Mellanox; try 1 (RoCEv1) or
          0 (InfiniBand)</td></tr>
  <tr><td>Error: <code>"Failed to modify QP to RTR"</code></td>
      <td>Peer not reachable at L2/L3/RoCEv2 layer (lab-confirmed
          on cross-subnet test)</td>
      <td><code>ping</code> the peer; fix routing or use a peer on
          the same subnet</td></tr>
  <tr><td>Start fails with <code>"Address already in use"</code></td>
      <td>Another perftest already bound to that <code>-p</code> port
          (raw shell launch, not via netgen)</td>
      <td>Stop the stray process or let netgen allocate a different
          port (allocator runs from 18515 upward)</td></tr>
</table>

<h2>Help &amp; reference</h2>
<p class="muted">Listed in the same order they appear in the Help
menu — first group is reference / "what's this?", second is
operational, third is the DPDK deep-dive.</p>
<table>
  <tr><th>Menu entry</th><th>Covers</th></tr>
  <tr><td>Install Guide</td>
      <td>End-to-end provisioning (apt + Docker + FRR + DPDK +
          systemd); the <code>install_ostg_complete.py</code> driver.</td></tr>
  <tr><td>API Guide</td>
      <td>Full REST endpoint reference with curl examples for every
          packet type, plus the new EVPN-inject (§24), Preflight
          (§25), latency / HTML-report (§26), and SR-MPLS stack
          (§27) sections.</td></tr>
  <tr><td><strong>Supported Features</strong></td>
      <td>The full capability matrix — every packet type, protocol,
          emulator, backend the app supports. Answers "can the app
          do X?" without scanning the codebase. (Added 0.2.73.)</td></tr>
  <tr><td><strong>What's New (this dialog)</strong></td>
      <td>You're reading it.</td></tr>
  <tr><td colspan="2" style="background:#f9fafb; color:#9ca3af;
       font-size:10px; text-align:center;">— separator —</td></tr>
  <tr><td>Install / Upgrade Server</td>
      <td>In-app wheel upgrade for a running server, or SSH-based
          first-install on a fresh Linux host.</td></tr>
  <tr><td colspan="2" style="background:#f9fafb; color:#9ca3af;
       font-size:10px; text-align:center;">— separator —</td></tr>
  <tr><td>DPDK Traffic Blast Workflow</td>
      <td>Line-rate DPDK tx_worker walkthrough: TX core sizing,
          calibrated numbers, troubleshooting.</td></tr>
</table>

<p class="muted">For the version-by-version detail see
<code>CHANGELOG.md</code> at the repo root.</p>
"""


def show_feature_guide(parent=None):
    """Open the What's-New / Feature Guide dialog. Reachable from
    Help → What's New."""
    _open_help_dialog(parent, "Netgen — What's New (Feature Guide)",
                      _FEATURE_GUIDE_HTML)


# ─────────────────────────────────────────── Capabilities (what we support)
# Distinct from What's-New: this is the comprehensive feature matrix — every
# packet type, every protocol, every emulator, every backend. Operators
# answer "can this app do X?" from this dialog; "what changed in 0.2.N?"
# from What's-New; "what curl command?" from API Guide.
_CAPABILITIES_GUIDE_HTML = r"""
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
         sans-serif; color: #1f2937; line-height: 1.5; font-size: 12px; }
  h1 { color: #1e40af; font-size: 20px; margin: 0 0 6px 0; }
  h2 { color: #374151; font-size: 15px; margin-top: 22px;
       border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
  h3 { color: #4b5563; font-size: 13px; margin-top: 14px; }
  p, li { color: #374151; }
  code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
         background: #f3f4f6; padding: 1px 5px; border-radius: 3px;
         font-size: 11px; color: #1e3a8a; }
  .where { color: #1d4ed8; font-weight: 600; font-size: 11px; }
  .muted { color: #6b7280; font-size: 11px; }
  table { border-collapse: collapse; margin-top: 6px; font-size: 11px;
          width: 100%; }
  th, td { border: 1px solid #d1d5db; padding: 5px 9px; text-align: left;
           vertical-align: top; }
  th { background: #f3f4f6; color: #374151; font-weight: 600; }
  td.yes  { color: #166534; font-weight: 600; }
  td.no   { color: #b91c1c; font-weight: 600; }
  td.partial { color: #b45309; font-weight: 600; }
  ul { margin: 4px 0; padding-left: 22px; }
  li { margin: 3px 0; }
</style>

<h1>Netgen — Supported Features</h1>
<p class="muted">The complete capability matrix: every packet type the
generator can emit, every protocol the emulator can speak, every
backend the wire-side runs on. For version-by-version detail see
<strong>Help → What's New</strong>; for the curl commands see
<strong>Help → API Guide</strong>.</p>

<h2>0. Quick-launch workflows <span class="ver new">0.3.11</span> <span class="ver new">0.3.12</span></h2>
<p>One-click paths — pick the right depth for the operator:</p>
<table>
  <tr><th>Workflow</th><th>Path</th><th>Use when</th></tr>
  <tr><td><b>Blast a DPDK Flow</b></td>
      <td>Tools → DPDK → "Blast a Flow"</td>
      <td>Smoke-test a NIC or demo line rate. One click does:
          bind a NIC → start a 1500 B UDP flow at line rate →
          show live tx_count / tx_rate. Non-modal so the main
          window stays usable; open multiple dialogs for
          parallel multi-NIC blasts.</td></tr>
  <tr><td><b>Blast a RDMA Flow</b> <span class="ver new">0.3.12</span></td>
      <td>Tools → RDMA → "Blast a RDMA Flow"</td>
      <td>Run perftest between two TGs (or loopback on one).
          Pick devices on each side, test type (Send / Write /
          Read × BW / Latency), msg size / QPs / duration; the
          dialog brokers the peer handshake and polls live BW
          (Gbps), msg rate (Mpps), or latency (µs/p99). Non-modal
          so multiple RDMA pairs can run in parallel across NIC
          pairs. Requires <code>perftest + rdma-core</code> on
          each TG — see Install Guide §10.</td></tr>
  <tr><td><b>Stream templates</b></td>
      <td>Add Stream → Template dropdown</td>
      <td>Standardised stream profiles for repeatable benchmarks.
          14 templates: UDP line rate (64 B / 1500 B), IMIX, LAG /
          RSS hash, latency probe, VXLAN encap, ICMP flood,
          VLAN-tagged, plus 6 scaling profiles (MAC sweep 1024,
          IPv4 dst /24, IPv4 src 256, IPv6 dst 64, 5-tuple RSS,
          VLAN 4094). All use unified <code>02:xx</code> locally-
          administered MAC defaults that match Blast a Flow.</td></tr>
</table>

<h2>1. Stream packet builder (Streams tab)</h2>
<p>Per-stream packet construction. Each stream emits a single shape;
combine streams for mixed traffic. Pick the backend via the
<b>Engine</b> dropdown on the Runtime Engine tab — defaults to Scapy.
DPDK <code>tx_worker</code> for line-rate UDP/IPv4; <b>RDMA
(perftest)</b> for ib_*_bw / ib_*_lat against a peer TG.</p>

<h3>L2 framing</h3>
<table>
  <tr><th>Variant</th><th>How</th><th>Notes</th></tr>
  <tr><td>Untagged Ethernet II</td><td>Default</td><td>—</td></tr>
  <tr><td>Single 802.1Q tag</td><td>Set <code>VLAN ID</code> + PCP</td>
      <td>TPID <code>0x8100</code></td></tr>
  <tr><td>802.1ad QinQ (S+C)</td><td>Set <strong>Outer</strong> +
      <strong>Inner</strong> VLAN ID/PCP</td>
      <td>Outer TPID <code>0x88a8</code> / inner <code>0x8100</code></td></tr>
</table>

<h3>L3 / L4 protocol stack — per-engine support</h3>
<table>
  <tr><th>Layer</th><th>Scapy</th><th>DPDK</th><th>RDMA <span class="muted">(0.3.12)</span></th></tr>
  <tr><td>IPv4 (DSCP/ECN/TTL/flags)</td>
      <td class="yes">✓</td><td class="yes">✓</td>
      <td class="partial">via RoCEv2</td></tr>
  <tr><td>IPv6 (TC, flow label, hop limit)</td>
      <td class="yes">✓</td><td class="no">—</td>
      <td class="partial">via RoCEv2 GID</td></tr>
  <tr><td>UDP</td>
      <td class="yes">✓</td><td class="yes">✓</td>
      <td class="partial">UDP/4791 (RoCEv2 transport)</td></tr>
  <tr><td>TCP (full flags / seq / ack / window)</td>
      <td class="yes">✓</td><td class="no">—</td>
      <td class="no">—</td></tr>
  <tr><td>ICMP / ICMPv6 (echo, type/code)</td>
      <td class="yes">✓</td><td class="no">—</td>
      <td class="no">—</td></tr>
  <tr><td>IGMP v2 / v3 (membership reports)</td>
      <td class="yes">✓</td><td class="no">—</td>
      <td class="no">—</td></tr>
  <tr><td>RDMA Send (BTH/GRH on UDP/4791)</td>
      <td class="partial">packet-craft only</td>
      <td class="no">—</td>
      <td class="yes">✓ ib_send_bw / ib_send_lat</td></tr>
  <tr><td>RDMA Write (one-sided)</td>
      <td class="no">—</td><td class="no">—</td>
      <td class="yes">✓ ib_write_bw / ib_write_lat</td></tr>
  <tr><td>RDMA Read (one-sided)</td>
      <td class="no">—</td><td class="no">—</td>
      <td class="yes">✓ ib_read_bw / ib_read_lat</td></tr>
</table>
<p class="muted"><b>RDMA column key:</b> "✓" means a real RDMA verbs
operation goes on the wire (perftest spawned, QPs allocated, MRs
registered). "via RoCEv2" means the underlying transport is L3/L4
but the operator doesn't construct headers directly. "packet-craft
only" (Scapy column) means we can emit BTH/GRH bytes via the L4=RoCEv2
selector in Add Stream, but no RDMA queue is actually opened on the
HCA — useful for fuzz / line-tester scenarios, not for measuring
real RDMA bandwidth or latency.</p>

<h3>Encapsulations</h3>
<ul>
  <li><strong>MPLS (single label)</strong> — TTL / TC controllable.
      Ethertype <code>0x8847</code>.</li>
  <li><strong>SR-MPLS label stack</strong> — comma-separated SID list
      (e.g. <code>16000, 16001, 16002</code>); BOS bit auto-set on
      the innermost. RFC 8660. Legacy single-label streams keep
      working unchanged.</li>
  <li><strong>VXLAN</strong> — inner Ether+IP+UDP wrapped in UDP/4789.
      Stateless decap on RX too, so a stream sent <em>into</em> a
      VXLAN tunnel can be counted on the other side.</li>
</ul>

<h3>Frame sizing</h3>
<ul>
  <li><strong>Fixed</strong> — 64–9216 bytes.</li>
  <li><strong>Random</strong> — uniform random between min / max.</li>
  <li><strong>IMIX</strong> — RFC 3139 three-frame mix (58.3% × 64 B,
      33.3% × 594 B, 8.3% × 1518 B).</li>
</ul>

<h3>Payload modes</h3>
<ul>
  <li><strong>None</strong> — bare L4 header only.</li>
  <li><strong>Pattern</strong> — repeating hex pattern (e.g.
      <code>DEADBEEF</code>).</li>
  <li><strong>Random</strong> — random bytes (re-randomised per packet).</li>
  <li><strong>Custom hex / file</strong> — paste a hex blob or load
      from disk; sent verbatim.</li>
</ul>

<h3>Latency timestamping</h3>
<p><strong>NLAT</strong> — 16-byte header (<code>magic 0x4e4c4154</code>
+ <code>tx_ns</code>) prepended to the UDP payload. Both Scapy and
DPDK backends embed it; the receiver decodes it for one-way latency
samples (p50 / p95 / p99 reported in the stats dock + RFC 2544
results).</p>


<h2>2. L2 / Multicast emulation (continuous protocol senders)</h2>
<p>Distinct from the Streams builder — these are <em>protocol
emulators</em> that hold a session open and send keepalives /
hellos on a cadence. All accept inline 802.1Q + 802.1ad tags.</p>
<table>
  <tr><th>Protocol</th><th>RFC</th><th>What it does</th></tr>
  <tr><td><strong>LACP</strong></td><td>802.1AX</td>
      <td>LACPDU slow-protocol frames; fast (1 s) or long (30 s)
          timeout; full state-bit control.</td></tr>
  <tr><td><strong>LLDP</strong></td><td>802.1AB</td>
      <td>Chassis ID, port ID, system name + description, TTL.</td></tr>
  <tr><td><strong>VRRP</strong> v2 + v3</td><td>3768 / 5798</td>
      <td>Master advertisements; IPv4 <code>224.0.0.18</code> +
          IPv6 <code>ff02::12</code>. v2 supports the RFC 3768 §5.3.6
          authentication TLVs (None / Simple Text Password / IPAH)
          since 0.2.83; v3 deprecated auth per RFC 5798 §5.1 so
          those fields disable when v3 is picked.</td></tr>
  <tr><td><strong>IGMP</strong> v1 + v2 + v3</td><td>1112 / 2236 / 3376</td>
      <td>Membership reports + Leave + General Query.
          v1 (RFC 1112) added in 0.2.82 for legacy multicast-router
          tests; v2 default; v3 source-aware (Mode-Is-Exclude).</td></tr>
  <tr><td><strong>PIM Hello</strong></td><td>7761 §4.3</td>
      <td>Neighbour discovery probe for PIM-SM / SSM.</td></tr>
  <tr><td><strong>BFD</strong></td><td>5880 / 5881</td>
      <td>Single-hop UDP/3784 with TTL=255; sub-second cadence
          supported; State = Up / Down for keepalive vs. tear-down.</td></tr>
</table>
<p><span class="where">Where: L2 Emulation tab → Start session.</span></p>


<h2>3. Routing / control plane (per-device FRR)</h2>
<p>Each device runs a per-instance FRR Docker container; protocols
auto-start when the device is Applied. VRF-isolated per device.</p>
<table>
  <tr><th>Protocol</th><th>What you configure</th></tr>
  <tr><td><strong>BGP</strong> (RFC 4271)</td>
      <td>Local AS, router-id / loopback, neighbours (IPv4 + IPv6
          AFs).</td></tr>
  <tr><td><strong>BGP EVPN</strong> (RFC 7432)</td>
      <td>Auto-enabled with VXLAN. Type-2 (MAC/IP) and Type-5 (IP
          Prefix) routes — populate via bulk-inject (see §4).</td></tr>
  <tr><td><strong>OSPF</strong> (RFC 2328)</td>
      <td>Area ID, ABR/ASBR roles, loopback router-id.</td></tr>
  <tr><td><strong>IS-IS</strong> (RFC 1195)</td>
      <td>Area ID, integrated IPv4 + IPv6, multi-area.</td></tr>
  <tr><td><strong>VRF</strong></td>
      <td>Per-device VRF isolation on the host; one FRR instance per
          VRF per device.</td></tr>
</table>
<p><span class="where">Where: Devices tab → Add/Edit device → Protocols selector.</span></p>


<h2>4. VXLAN / EVPN data plane</h2>
<ul>
  <li><strong>VXLAN tunnels</strong> — VNI, local + remote VTEPs,
      UDP 4789, optional VLAN mapping.</li>
  <li><strong>EVPN Type-2 bulk inject</strong> — synthesise N
      MAC/IP entries on a VXLAN interface; FRR/BGP advertises them
      as Type-2 routes. Used for scale testing without N real
      hosts.</li>
  <li><strong>EVPN Type-5 bulk inject</strong> — synthesise N IP
      prefixes; advertised as Type-5 (IP Prefix) routes. Optional
      gateway + VRF table ID.</li>
  <li><strong>Active-injections table</strong> — every inject on
      the server, kind-tagged (Type-2 blue / Type-5 violet), per-row
      Clear button routes to the right cleaner.</li>
</ul>
<p><span class="where">Where: Devices tab → VXLAN sub-tab → EVPN Inject button.</span></p>


<h2>5. DHCP</h2>
<table>
  <tr><th>Variant</th><th>Server</th><th>Client</th></tr>
  <tr><td>DHCPv4</td><td class="yes">✓ (dnsmasq pool)</td>
      <td class="yes">✓ (dhclient DISCOVER/REQUEST/RENEW)</td></tr>
  <tr><td>DHCPv6</td><td class="partial">stateless mode</td>
      <td class="partial">solicit/advertise</td></tr>
</table>
<p>v4 server lets you configure pool start/end, DNS, gateway, lease
time. v4 client tracks bound lease in the device row.
<span class="where">Where: Devices tab → DHCP sub-tab.</span></p>


<h2>6. Compliance test methodologies</h2>
<ul>
  <li><strong>RFC 2544 Throughput</strong> — binary-search to no-loss
      rate at the 7 standard frame sizes (64, 128, 256, 512, 1024,
      1280, 1518 B). Configurable loss tolerance, per-step duration,
      and resolution.</li>
  <li><strong>RFC 2544 §26.2 Latency</strong> — optional per-step
      NLAT capture; results table populates p50 / p95 / p99
      latency columns alongside throughput.</li>
  <li><strong>HTML test report</strong> — single self-contained HTML
      with parameters + full results table + best-throughput summary.
      Open in any browser → Print → Save-as-PDF for a formal
      deliverable.</li>
</ul>
<p><span class="where">Where: Tools menu → RFC 2544 Throughput Test.</span></p>


<h2>7. Preflight checks</h2>
<p>Pre-Apply sanity surface. Current finding codes:</p>
<table>
  <tr><th>Code</th><th>Level</th><th>What's wrong</th></tr>
  <tr><td><code>BGP_NO_REMOTE_ASN</code></td><td>error</td>
      <td>BGP neighbour configured, no remote AS.</td></tr>
  <tr><td><code>BGP_NO_LOOPBACK</code></td><td>warning</td>
      <td>iBGP / EVPN device with no loopback IP.</td></tr>
  <tr><td><code>VXLAN_EMPTY</code></td><td>warning</td>
      <td>VXLAN enabled but no tunnels configured.</td></tr>
  <tr><td><code>VXLAN_MISSING_FIELDS</code></td><td>error</td>
      <td>VXLAN tunnel missing VNI / local IP / remote peers.</td></tr>
  <tr><td><code>OSPF_NO_AREA</code></td><td>warning</td>
      <td>OSPF enabled but no area ID.</td></tr>
  <tr><td><code>ISIS_NO_AREA</code></td><td>warning</td>
      <td>IS-IS enabled but no area ID.</td></tr>
  <tr><td><code>DUPLICATE_IPV4</code></td><td>error</td>
      <td>Same IPv4 on two devices.</td></tr>
</table>
<p>Heads-up surface — Apply still runs whether you address findings
or not.
<span class="where">Where: Devices tab → preflight bar (top of sub-tab) → Details…</span></p>

<h3>Findings surfaces</h3>
<ul>
  <li><strong>Aggregate pills</strong> in the bar — errors / warnings
      / OK counts with severity tint on the bar background.</li>
  <li><strong>Per-device dot</strong> in front of each Device Name
      cell (red / amber / green; severity wins). Answers "which
      device has problems?" without opening Details. (v0.2.78)</li>
  <li><strong>Click pills → filtered Details modal</strong> — click
      red opens errors-only, amber opens warnings-only. (v0.2.78)</li>
  <li><strong>Sortable Details modal</strong> with column-click sort
      + <strong>Export CSV / Export JSON</strong> buttons
      (timestamped filenames). (v0.2.74)</li>
  <li><strong>Auto-refresh after Apply</strong> — bar repaints
      immediately after device / BGP / OSPF / IS-IS / VXLAN / DHCP
      apply, not on the 60 s poll. (v0.2.71 + v0.2.74 + v0.2.85;
      DHCP coverage closed the 5-protocol consistency loop.)</li>
</ul>

<h3>Catch-bad-config-early validators (v0.2.86 + v0.2.87)</h3>
<p>Two pure-function helpers stop common BGP/OSPF/IS-IS typos at
submit time instead of letting FRR reject them five seconds later
inside the container:</p>
<ul>
  <li><code>utils/isis_net.py:validate_isis_net</code> — RFC 1195
      §3.1: AFI + Area(1–13) + SysID(6) + NSEL=00 = 8–20 bytes
      total. Accepts variable-length area IDs. Wired into the
      inline-edit path + the Add Device dialog (the latter with
      <code>allow_short_area=True</code> so the
      <code>49.0001</code> shortcut still pads).</li>
  <li><code>utils/ospf_area.py:validate_ospf_area_id</code> —
      RFC 2328 §6: 32-bit integer, displayed as IPv4 dotted-
      decimal. Accepts both forms; <em>normalises</em> integer
      input to the dotted form FRR puts on the wire so operator-
      typed "1" becomes "0.0.0.1" stored, matching
      <code>show ip ospf</code> output.</li>
</ul>


<h2>8. Statistics + reporting</h2>
<ul>
  <li><strong>Per-interface RX counters</strong> — bytes / packets /
      drops, in-place repaint (no flicker).</li>
  <li><strong>Per-stream TX + RX counters</strong> with status badge
      (running / idle).</li>
  <li><strong>Latency percentiles</strong> — p50 / p95 / p99 from
      NLAT decode, in stats tooltip + RFC 2544 results.</li>
  <li><strong>CSV export</strong> — both stats tables (Interface +
      Stream) to a single CSV with header block + per-section
      markers.</li>
  <li><strong>HTML report</strong> — RFC 2544 results (see §6).</li>
  <li><strong>Device import / export</strong> — JSON save/load of the
      full device config set (round-trippable).</li>
</ul>
<p><span class="where">Where: Statistics dock + Tools menu (RFC 2544 export).</span></p>


<h2>9. Backends</h2>
<table>
  <tr><th>Backend</th><th>Covers</th><th>Caveats</th></tr>
  <tr><td><strong>Scapy</strong> (default)</td>
      <td>Every L2/L3/L4 combination above; full protocol breadth.</td>
      <td>CPU-bound; no line-rate guarantee.</td></tr>
  <tr><td><strong>DPDK <code>tx_worker</code></strong></td>
      <td>Line-rate UDP (100G / 400G); per-core TX queues; NLAT
          timestamps.</td>
      <td>UDP + IPv4 only (TCP / ICMP / IPv6 / MPLS / QinQ fall
          back to Scapy — <em>surfaced explicitly</em> since 0.2.75
          as a Start-time warning + Engine column annotation). Needs
          hugepages + IOMMU + NIC bound to <code>vfio-pci</code>
          (Mellanox uses <code>mlx5</code> direct). One-click admin
          UI handles all of this.</td></tr>
</table>

<h3>DPDK fallback telemetry (v0.2.75 → v0.2.77)</h3>
<p>Two-layer feedback when DPDK doesn't take:</p>
<ul>
  <li><strong>Pre-flight</strong> — Start endpoint pre-checks the
      stream against the DPDK envelope and decorates its response
      with <code>actual_engine</code> + <code>fallback_reason</code>.
      Client shows a consolidated end-of-batch dialog naming every
      affected stream + reason.</li>
  <li><strong>Runtime</strong> — when the launcher has to swap
      engines mid-flight (tx_worker rc=100 Broadcom ULP error,
      DPDK handoff exception), the swap is recorded on the stream
      tracker; <code>/api/traffic/stats</code> surfaces
      <code>runtime_engine</code> + <code>runtime_fallback_reason</code>;
      the Engine column shows <strong>"Scapy ⚠ (was DPDK)"</strong>
      in amber with the reason in the cell tooltip.</li>
</ul>
<p class="muted">"Per-core TX stats" remains the one explicitly
deferred audit item — needs a <code>tx_worker.c</code> change to
emit per-queue STAT lines, bundled with future C-side work.</p>


<h2>10. Server / API / operations</h2>
<h3>REST API</h3>
<ul>
  <li><strong>Multi-server (TGen chassis)</strong> — single client
      manages many servers; topology import/export carries the
      chassis list.</li>
  <li><strong>Bearer-token auth (optional)</strong> —
      <code>NETGEN_AUTH_TOKEN</code> env var; single token = admin.</li>
  <li><strong>Role-based auth (optional)</strong> —
      <code>NETGEN_AUTH_TOKENS_JSON</code> mapping; hierarchy
      <code>admin &gt; operator &gt; viewer</code> with per-endpoint
      <code>@require_role</code> gates.</li>
</ul>

<h3>Install / Upgrade</h3>
<ul>
  <li><strong>SSH-based install</strong> — fresh Linux host →
      <code>install_ostg_complete.py</code> (apt + Docker + FRR +
      DPDK + systemd) driven from the GUI.</li>
  <li><strong>In-app wheel upgrade</strong> — upload a wheel to a
      running server, inline log stream, auto-restart. Falls back to
      SSH-based reinstall on pre-0.2.34 servers.</li>
</ul>

<h3>DPDK admin portal (browser)</h3>
<p>Visit <code>http://server:5050/admin</code> for one-click DPDK
runtime install, hugepages allocation, IOMMU toggle (GRUB edit +
reboot), NIC bind/unbind to <code>vfio-pci</code> /
<code>mlx5</code>, TX-core recommender, and live tx_worker status.</p>

<h3>DPDK admin in the main GUI (v0.2.76 → v0.2.77)</h3>
<ul>
  <li><strong>Readiness chip</strong> — small status-bar indicator
      (green / amber / red / gray) polling
      <code>/api/dpdk/status</code> every 30 s; tooltip lists every
      subsystem + libdpdk version + tx_worker build date.</li>
  <li><strong>NIC bind safety</strong> — <code>/api/dpdk/bind</code>
      refuses (HTTP 409) when the candidate iface carries the
      default route, is the SSH session's iface, or has an active
      stream. GUI surfaces the reason with a <strong>Bind anyway</strong>
      escape hatch.</li>
  <li><strong>Hugepage allocation feedback</strong> — Configure
      Hugepages dialog reports requested-vs-actually-allocated
      (catches kernel-capped requests due to memory fragmentation
      or NUMA imbalance).</li>
  <li><strong>Inline Unbind</strong> — DPDK Status dialog has a
      per-row Unbind button next to every vfio-pci-bound interface.</li>
  <li><strong>Active EVPN injections chip</strong> — VXLAN sub-tab
      shows <code>⚡ EVPN: N active</code> driven by
      <code>/api/evpn/type2/list</code> (counts both kinds).
      Click opens the EVPN Inject dialog.</li>
</ul>


<h2>11. Stateful TCP (real-socket traffic generator)</h2>
<p>Parallel to the stateless stream builder (§1): sessions complete
<strong>real 3-way handshakes</strong>, so middleboxes / NAT / proxies
/ TLS terminators see actual connection state. Sessions live
server-side — close the GUI and they keep running until they hit
their Duration or you stop them.
<span class="where">Where: Stateful TCP tab.</span></p>

<h3>11a. What's supported in the GUI</h3>
<table>
  <tr><th>Knob</th><th>v1</th><th>Notes</th></tr>
  <tr><td>Role</td><td class="yes">Client + Server</td>
      <td>Run server on box A, client on box B.</td></tr>
  <tr><td>Protocol</td><td class="yes">raw + http</td>
      <td>DNS / SIP available via API today; GUI exposure deferred to v2.</td></tr>
  <tr><td>TLS</td><td class="yes">Both sides</td>
      <td>Client: verify + SNI. Server: cert + key file pickers.</td></tr>
  <tr><td>VRF</td><td class="yes">Linux SO_BINDTODEVICE</td>
      <td>Graceful no-op on macOS / non-root Linux — falls back to default route.</td></tr>
  <tr><td>Counters</td><td class="yes">Live, 3 s poll</td>
      <td>conns_*, bytes_*, RTT (kernel TCP_INFO on Linux), per-protocol status bins.</td></tr>
</table>

<h3>11b. Scale &amp; limits</h3>
<p>What the per-session knobs accept, and what the server-side
process will actually do under load. Numbers below are the
<em>operative ceilings</em> — beyond them the GUI rejects the value
or the kernel does.</p>
<table>
  <tr><th>Knob</th><th>Range</th><th>Default</th><th>Notes</th></tr>
  <tr><td>Destination / Listen port</td><td>1 – 65535</td><td>5001</td>
      <td>Port 0 (OS-allocated) is intentionally rejected — almost
          always a leftover from a cleared field.</td></tr>
  <tr><td>Duration</td><td>0.1 s – 86400 s (24 h)</td><td>30 s</td>
      <td>Server-side worker exits cleanly at the deadline; row stays
          in the table as <code>● Stopped</code>.</td></tr>
  <tr><td>Payload bytes (client)</td><td>0 – 1,048,576 (1 MB)</td><td>1024 B</td>
      <td>Per-connection payload — bytes_tx grows
          <code>conns × payload</code> for echo / raw.</td></tr>
  <tr><td>Response bytes (HTTP server)</td><td>0 – 1,048,576 (1 MB)</td><td>1024 B</td>
      <td>HTTP body size; ignored for <code>protocol=raw</code>
          (grey in the dialog).</td></tr>
  <tr><td>Concurrency (client)</td><td>1 – 256 sender threads</td><td>1</td>
      <td>Each thread runs its own connect→send→recv→close loop;
          effective rate ≈ <code>concurrency × (1 / max(interval_s,
          connect_RTT))</code>.</td></tr>
  <tr><td>Interval per conn</td><td>0 – 60 s</td><td>0 s</td>
      <td><strong>Set ≥ 0.02 s for loopback</strong> — see §11f. On
          real RTT-bound targets the round-trip itself paces you.</td></tr>
  <tr><td>Connect timeout</td><td>0.1 s – 120 s</td><td>5 s</td>
      <td>Per-attempt; <code>conns_failed++</code> on timeout with
          <code>last_error</code> set.</td></tr>
</table>

<h4>Server-side process limits (not exposed in the GUI)</h4>
<table>
  <tr><th>Setting</th><th>Value</th><th>Where it matters</th></tr>
  <tr><td>Listen backlog</td><td>128</td>
      <td>SYN queue depth. Beyond this, the kernel drops half-open
          connects until accept() catches up. Override via API
          (<code>backlog</code> field) for stress tests.</td></tr>
  <tr><td>Accept timeout</td><td>0.5 s</td>
      <td>Determines how fast Stop is observed by the server thread
          — bounded shutdown latency.</td></tr>
  <tr><td>Per-conn handler timeout</td><td>10 s</td>
      <td>Each accepted connection has 10 s to complete its
          read/echo/close cycle before being torn down.</td></tr>
  <tr><td>HTTP message read cap</td><td>1 MB</td>
      <td>Single request/response body limit; over-sized payloads
          truncate at the receiver.</td></tr>
  <tr><td>DNS / SIP message read cap</td><td>65,535 B</td>
      <td>Per the wire format (DNS length prefix is 16-bit).</td></tr>
  <tr><td>Stop join deadlines</td><td>3 s supervisor + 2 s handlers</td>
      <td>Bounded — a misbehaving peer can't stall a stop
          indefinitely.</td></tr>
  <tr><td>Session registry</td><td>In-memory dict, no cap</td>
      <td>Bounded only by Python memory. Typical operator load: under
          a dozen simultaneous sessions; the GUI table handles 100+
          cleanly.</td></tr>
</table>

<h4>Realistic throughput envelope (observed)</h4>
<table>
  <tr><th>Scenario</th><th>Per sender</th><th>Notes</th></tr>
  <tr><td>Loopback, interval=0</td><td>~5,000 conns/sec</td>
      <td>Hard ceiling: kernel ephemeral-port pool empties out in
          seconds → EADDRNOTAVAIL on every subsequent connect.
          <strong>Don't do this.</strong></td></tr>
  <tr><td>Loopback, interval=0.02 s</td><td>~50 conns/sec</td>
      <td>Sane loopback rate — well under the TIME_WAIT recycle
          window.</td></tr>
  <tr><td>Cross-host, sub-millisecond RTT</td><td>~1,000 conns/sec</td>
      <td>Per sender, naturally rate-limited by RTT × handshake.
          Scale by raising <code>concurrency</code>.</td></tr>
  <tr><td>Cross-host, 10–50 ms RTT (WAN)</td><td>~20–100 conns/sec</td>
      <td>RTT dominates. Raise concurrency to fill the pipe.</td></tr>
  <tr><td>TLS handshakes</td><td>~30–60% of TCP rate</td>
      <td>RSA / ECDHE handshake adds CPU + RTTs. Server-side cert
          loaded once per session; client-side ctx built once.</td></tr>
</table>

<h4>Ephemeral-port ceilings (the EADDRNOTAVAIL math)</h4>
<table>
  <tr><th>OS</th><th>Default port range</th><th>Effective ceiling</th></tr>
  <tr><td>macOS</td><td>49152 – 65535</td><td>~16,000 ports</td></tr>
  <tr><td>Linux</td><td>32768 – 60999</td><td>~28,000 ports</td></tr>
</table>
<p class="muted">Each completed TCP close → TIME_WAIT for 30–60 s.
Sustained connect rate above
<code>ports / time_wait_seconds</code> hits the ceiling: <strong>~265
conns/sec on macOS, ~470 conns/sec on Linux</strong> if every
connection runs to completion. Real workloads rarely sustain this for
long; if yours does, raise the ephemeral range
(<code>sysctl net.inet.ip.portrange.first</code> on macOS,
<code>net.ipv4.ip_local_port_range</code> on Linux) or use multiple
source IPs.</p>


<h3>11c. Workflow A — "Does my middlebox forward TCP correctly?" (two-machine)</h3>
<p><strong>On box A (target side):</strong></p>
<ol>
  <li><strong>Start session…</strong> → Role: <strong>Server</strong>
      → Protocol <code>raw</code> → Listen IP <code>0.0.0.0</code>,
      Port <code>5001</code> → Mode <code>echo</code> → <strong>Start</strong>.</li>
  <li>Row appears: <code>● Running · SERVER · RAW · 0.0.0.0:5001</code>.
      Conns stays at 0 until traffic arrives.</li>
</ol>
<p><strong>On box B (driver):</strong></p>
<ol>
  <li><strong>Start session…</strong> → Role: <strong>Client</strong>
      → Protocol <code>raw</code> → Destination IP = box A's IP, Port
      <code>5001</code> → Duration <code>30 s</code> → Concurrency
      <code>4</code> → Payload <code>1024</code> B → <strong>Start</strong>.</li>
  <li>Watch the client row: <strong>Conns</strong> climbs,
      <strong>Bytes TX</strong> ≈ <strong>Bytes RX</strong> (echo),
      <strong>Avg RTT</strong> settles. Hover the
      <strong>Status</strong> cell → tooltip dumps
      <code>conns_attempted</code>, <code>conns_failed</code>,
      <code>retransmits_total</code> (Linux only), <code>last_error</code>.</li>
  <li>Server row on box A mirrors: <code>bytes_rx</code> on the server
      equals <code>bytes_tx</code> on the client.</li>
</ol>
<p class="muted">If the middlebox drops or NATs badly:
<code>conns_failed</code> climbs on the client and <code>last_error</code>
names the syscall failure (e.g. <code>ConnectionResetError</code>,
<code>TimeoutError: connect timed out</code>).</p>

<h3>11d. Workflow B — "Does my WAF still terminate TLS?"</h3>
<p><strong>Target box:</strong> Start session → Server → Protocol
<code>http</code> → Listen Port <code>8443</code> → check
<strong>☑ TLS</strong> → Browse… to a cert + key PEM (paths are
read by the <em>server</em>, not the GUI host) → Response bytes
<code>4096</code> → Start.</p>
<p><strong>Driver box:</strong> Start session → Client → Protocol
<code>http</code> → Destination IP/Port <code>8443</code> → check
<strong>☑ TLS</strong> → leave <strong>Verify cert + hostname</strong>
unchecked for self-signed test certs → Duration <code>30 s</code>,
Concurrency <code>8</code> → Start.</p>
<p>Status tooltip on the client row tells the story:</p>
<ul>
  <li><code>http: 2xx=410 other=0</code> → handshake + HTTP both healthy.</li>
  <li><code>http: 2xx=0 other=410</code> → connection works, WAF
      rejects payloads. <code>last_error</code> carries the rejection.</li>
  <li>No conns at all → TLS handshake failing. Tooltip names the
      OpenSSL error.</li>
</ul>

<h3>11e. Workflow C — "Pin client traffic onto a Linux VRF"</h3>
<p>Start session → Client → fill destination → <strong>VRF:
<code>vrf-blue</code></strong> → optional Source IP → Start.
Behavior depends on what the server-side worker can do:</p>
<table>
  <tr><th>Server platform</th><th>Result</th><th>last_error</th></tr>
  <tr><td>Linux as root (or CAP_NET_RAW)</td>
      <td class="yes">VRF bind applied — packets egress via the VRF</td>
      <td><code>null</code></td></tr>
  <tr><td>Linux non-root</td><td class="partial">Falls back to default routing table</td>
      <td><code>vrf=X requires CAP_NET_RAW / root</code></td></tr>
  <tr><td>macOS / Windows server</td><td class="partial">Falls back to default routing table</td>
      <td><code>vrf=X ignored (SO_BINDTODEVICE only on Linux)</code></td></tr>
</table>
<p class="muted">The session <strong>never fails outright</strong> on
VRF bind issues — it always falls back so you can develop on a laptop
and only see the real VRF effect in the lab.</p>

<h3>11f. Workflow D — Loopback dev smoke (and the EADDRNOTAVAIL trap)</h3>
<p>Start session → Client → Destination IP <code>127.0.0.1</code>.
The moment you type the loopback IP, an <strong>amber warning</strong>
appears under the Interval field:</p>
<blockquote style="margin:6px 0; padding:6px 10px; background:#fef3c7;
border:1px solid #fde68a; border-radius:4px; color:#b45309;">
⚠ Loopback + interval=0 will exhaust the kernel ephemeral-port range
within seconds (EADDRNOTAVAIL). Set interval to ≥ 0.02 s for loopback
runs.
</blockquote>
<p>Set <strong>Interval per conn: <code>0.02 s</code></strong> →
warning disappears → Start. If you ignore the warning, the session
row will land at <code>Conns=0 Conns_failed=5000+</code> and the
tooltip will read
<code>last_error: OSError: [Errno 49] Can't assign requested address</code>.</p>
<p class="muted">This is the same kernel exhaustion that earlier
caused intermittent failures across the stateful-TCP <em>pytest</em>
suite — the dialog surface here is the GUI-side guard so operators
don't have to learn it the hard way.</p>

<h3>11g. Reading the session table</h3>
<table>
  <tr><th>Cell</th><th>What it tells you</th></tr>
  <tr><td><strong>Status pill</strong></td>
      <td>● Green = running, ● grey = stopped. <em>Hover</em> for the
          full counter dump: per-protocol bins, kernel RTT samples,
          retransmits, last_error.</td></tr>
  <tr><td><strong>Role badge</strong></td>
      <td>CLIENT (blue) / SERVER (violet) so a mixed table reads fast.</td></tr>
  <tr><td><strong>Protocol</strong></td>
      <td><code>RAW</code> / <code>HTTP</code> / <code>HTTP+TLS</code>
          — TLS gets suffixed onto the protocol so you don't need a
          separate column.</td></tr>
  <tr><td><strong>Target</strong></td>
      <td><code>dst:port</code> for clients; <code>listen_ip:port</code>
          for servers. Hover → VRF + src_ip (client) or VRF + mode
          (server).</td></tr>
  <tr><td><strong>Conns</strong></td>
      <td><code>conns_established</code> — climbs only on
          <em>successful</em> 3-way handshakes.</td></tr>
  <tr><td><strong>Bytes TX / RX</strong></td>
      <td>Formatted KB / MB; hover for exact byte count. Echo sessions
          should show TX ≈ RX.</td></tr>
  <tr><td><strong>Avg RTT</strong></td>
      <td>Kernel <code>TCP_INFO.rtt_us</code> if available (Linux),
          else userspace <code>connect()+send+recv</code>.
          <code>—</code> if no samples yet.</td></tr>
  <tr><td><strong>Session ID</strong></td>
      <td>First 10 chars + <code>…</code>; full ID in the tooltip,
          plugs straight into
          <code>netgen-cli tcp stats --session-id …</code>.</td></tr>
</table>
<p>The <strong>Filter</strong> box (top-right) does case-insensitive
substring match on role / protocol / target / session-ID — useful
once you have a dozen sessions running.</p>

<h3>11h. Stop operations</h3>
<ul>
  <li><strong>Per-row Stop</strong> — the <code>×</code> button on
      every running row. Single session, instant; row flips to Stopped
      150 ms later on the next forced refresh.</li>
  <li><strong>Stop selected</strong> — multi-select rows (Cmd-click /
      Shift-click), then the action-bar button. Each gets a separate
      POST so a slow stop on one doesn't block the others.</li>
  <li><strong>Stop all</strong> — confirmation modal first ("affects
      every operator on this server"), then one POST with an empty
      body that the server fans out to every session.</li>
  <li><strong>Implicit stop</strong> — when <code>Duration</code>
      elapses, the worker exits on its own. The row stays in the
      table as <code>● Stopped</code> so you can still hover the
      Status cell and read final counters.</li>
</ul>

<h3>11i. When something looks wrong</h3>
<ol>
  <li><strong>Hover the Status cell</strong> — 90% of "why isn't this
      working?" answers itself in the tooltip. Per-protocol counter
      bins (<code>http: 2xx=…</code>, <code>dns: nxdomain=…</code>,
      <code>sip: 4xx=…</code>) and the full <code>last_error</code>
      live there.</li>
  <li><strong>Conns vs Conns_failed</strong> — Conns climbing but
      Bytes at 0 → handshake works but data doesn't (firewall
      mid-stream, TLS reject mid-handshake). Conns flat and
      Conns_failed high → target unreachable / refusing.</li>
  <li><strong>Action bar's right-side info label</strong> turns amber
      on transient fetch failures. <code>401</code>/<code>403</code> →
      set <code>NETGEN_AUTH_TOKEN</code> client-side + restart.
      <code>404</code> → upgrade the netgen-server.</li>
  <li><strong>Drop to CLI</strong> if you need to script the same
      run: the Session ID from the tooltip plugs into
      <code>netgen-cli tcp stats --session-id &lt;id&gt;</code> or
      the HTTP API directly.</li>
</ol>

<p class="muted"><strong>Mental model:</strong> the tab is a thin REST
viewer over <code>/api/stateful_tcp/*</code>. Sessions live on the
<em>server</em>, not in the client. Closing the GUI doesn't stop your
sessions; you can re-open on another machine and the same table is
there.</p>


<h2>What this app is not</h2>
<p class="muted">A short honest list so you don't go hunting:</p>
<ul>
  <li><strong>No GRE / GTP packet builders</strong> — workaround:
      assemble in custom-hex payload or send via the API with a
      Scapy expression.</li>
  <li><strong>No SRv6 / SRH</strong> (SR-MPLS yes, IPv6 SR no).</li>
  <li><strong>DPDK is UDP-only</strong> — TCP / ICMP / IPv6 fall
      back to Scapy automatically per-stream.</li>
  <li><strong>No real-time RFC 2889 / 3918 / 8239 test profiles</strong>
      yet — currently only RFC 2544 is automated; the others can be
      composed manually with stream batches.</li>
</ul>

<p class="muted">For the curl commands behind any of the above see
<strong>Help → API Guide</strong>. For the version each feature
shipped in see <code>CHANGELOG.md</code> or <strong>Help → What's
New</strong>.</p>
"""


def show_capabilities_guide(parent=None):
    """Open the comprehensive Supported Features / Capabilities
    Guide dialog. Reachable from Help → Supported Features.
    Distinct from the What's-New guide — this answers 'can the app
    do X?', What's-New answers 'what changed in 0.2.N?'."""
    _open_help_dialog(parent, "Netgen — Supported Features",
                      _CAPABILITIES_GUIDE_HTML)


def _open_help_dialog(parent, title, html):
    """Shared QTextBrowser-in-a-QDialog shell used by all Help entries.
    Keeps the look + close-button behavior consistent."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setGeometry(250, 200, 880, 760)
    dialog.setMinimumSize(720, 500)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(12, 12, 12, 12)

    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    browser.setHtml(html)
    layout.addWidget(browser, 1)

    button_row = QHBoxLayout()
    button_row.addStretch(1)
    close_btn = QPushButton("Close")
    close_btn.setDefault(True)
    close_btn.clicked.connect(dialog.accept)
    button_row.addWidget(close_btn)
    layout.addLayout(button_row)

    dialog.exec()


class AddStreamDialog(QDialog):
    def __init__(self, parent=None, interface=None, stream_data=None, server_interfaces=None):
        super().__init__(parent)
        self.tx_port = interface or ""
        self.tx_port_name = self.tx_port.split(" - Port:")[-1].strip() if self.tx_port else ""
        self.stream_data = stream_data or {}
        self.server_interfaces = server_interfaces or []

        self.setWindowTitle("Add/Edit Traffic Stream")
        # v0.3.11: default height sized for the tallest tab's
        # content. Measured per-tab sizeHints:
        #   Protocol Selection 445  Protocol Data 465 (tallest)
        #   Variable Fields    434  Stream Control 374
        #   Packet View        232  PCAP Replay    259
        # 465 content + tab bar (30) + template combo (~50) +
        # button bar (42) + margins (~30) = ~617 px needed for
        # the tallest tab to fit without scrolling. 640 default
        # gives a small buffer above the buttons without the
        # 130+ px of dead absorber space the old 720 had.
        self.setGeometry(200, 200, 1000, 640)
        self.setMinimumSize(900, 500)
        self.setMaximumSize(1200, 900)
        # Set smaller base font size
        font = self.font()
        font.setPointSize(10)  # Reduced from default 13
        self.setFont(font)
        
        # Apply professional styling
        self._apply_professional_styling()

        # Tabs
        self.tabs = QTabWidget()
        # Enable scroll buttons for main tabs if they overflow
        self.tabs.setUsesScrollButtons(True)
        # v0.3.11: enforce a minimum width per tab so the labels
        # render in full ("Protocol Selection" not "rotocol Selectio").
        # macOS-styled QTabBar tends to elide long labels first;
        # styleSheet forces a minimum width that fits the widest
        # tab text (~140 px is enough for the current titles).
        # v0.3.11 compact + professional: tabs auto-fit content
        # width so all 6 fit in the dialog without scroll arrows.
        # 140 px min-width was too aggressive — total tab bar width
        # 140 × 6 = 840 px overflowed even at 1000 px dialog width.
        # 90 px keeps the shorter labels ("PCAP Replay") readable
        # while letting the longer ones ("Protocol Selection") size
        # naturally. Tighter padding (2px vertical, 10px horizontal)
        # for a sleeker look; bold-on-selected + subtle hover
        # highlight makes the active tab pop without heavy chrome.
        self.tabs.setStyleSheet("""
            QTabBar::tab {
                min-width: 118px;
                padding: 4px 10px;
                font-size: 11px;
                color: #4b5563;
                background: #f3f4f6;
                border: 1px solid #d1d5db;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 1px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #1e40af;
                font-weight: 600;
                border-bottom: 1px solid #ffffff;
            }
            QTabBar::tab:hover:!selected {
                background: #e5e7eb;
                color: #1f2937;
            }
        """)
        # v0.3.11: disable QTabBar's equal-share expansion so each
        # tab takes its NATURAL content width (Protocol Selection
        # needs ~120 px, PCAP Replay only ~85 px). With expanding,
        # Qt averaged all 6 to dialog_width/6 = 113 px which
        # truncated the longest label.
        self.tabs.tabBar().setExpanding(False)

        # Protocol Selection Tab
        self.protocol_tab = QWidget()
        self.protocol_tab_layout = QVBoxLayout()
        self.protocol_tab.setLayout(self.protocol_tab_layout)
        self.setup_protocol_selection_tab()

        # Protocol Data Tab
        self.protocol_data_tab = QWidget()
        self.protocol_data_layout = QVBoxLayout()
        self.protocol_data_tab.setLayout(self.protocol_data_layout)
        self.setup_protocol_data_tab()

        # Packet View Tab
        self.packet_view_tab = QWidget()
        self.packet_view_layout = QVBoxLayout()
        self.packet_view_tab.setLayout(self.packet_view_layout)
        self.setup_packet_view_tab()

        # Stream Control Tab
        self.stream_control_tab = QWidget()
        self.setup_stream_control_tab()

        # Variable Fields Tab (placeholder)
        self.variable_fields_tab = QWidget()
        self.setup_variable_fields_tab()

        # PCAP Replay Tab
        self.pcap_tab = QWidget()
        self.setup_pcap_tab()

        # Add tabs (order matters for initial wiring)
        self.tabs.addTab(self.protocol_tab, "Protocol Selection")
        self.tabs.addTab(self.protocol_data_tab, "Protocol Data")
        self.tabs.addTab(self.variable_fields_tab, "Variable Fields")
        self.tabs.addTab(self.stream_control_tab, "Stream Control")
        self.tabs.addTab(self.packet_view_tab, "Packet View")
        self.tabs.addTab(self.pcap_tab, "PCAP Replay")

        # Scroll Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.tabs)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Main Layout
        self.main_layout = QVBoxLayout()

        # Template picker — appears above the tabs in "add new stream"
        # mode (not when editing an existing one) so operators can
        # pick a role (UDP line rate, LAG hash test, VXLAN encap, ...)
        # and have all six tabs pre-populate in one click. The picker
        # uses the same populate_stream_fields() path the edit case
        # already drives, so anything the dialog can show via Edit
        # can be a template.
        try:
            from utils.traffic_templates import list_templates
            self._traffic_templates_meta = list_templates()
        except Exception:
            self._traffic_templates_meta = []
        # v0.3.11 fix: the original gate was `not self.stream_data`,
        # but the Add Stream launcher (stream_control.py:956) ALWAYS
        # pre-seeds stream_data with `{"stream_id": uuid}` for new
        # streams — so the gate evaluated False and the dropdown was
        # never built. Symptom: operator clicks Add Stream, sees the
        # dialog with no Template: row. The intent of the gate is
        # "skip when EDITING an existing stream" — those carry a
        # full populated dict (name, frame_size, protocol_data, …).
        # A fresh-new-stream payload contains only stream_id (and
        # maybe protocol_selection scaffolding), nothing operator-
        # facing. Detect that by checking for the operator-facing
        # keys a real stream always has.
        _existing_stream_keys = {
            "name", "frame_size", "protocol_data", "L3", "L4",
            "stream_rate_type", "dpdk_enable", "enabled",
        }
        _is_editing_existing = bool(
            self.stream_data
            and (_existing_stream_keys & set(self.stream_data.keys()))
        )
        if self._traffic_templates_meta and not _is_editing_existing:
            template_bar = QHBoxLayout()
            template_bar.setContentsMargins(8, 6, 8, 0)
            template_bar.addWidget(QLabel("Template:"))
            self.template_combo = QComboBox()
            # v0.3.11: show ALL entries without scrolling. Qt's
            # QComboBox default maxVisibleItems is 10 — with 14
            # templates + Custom = 15 entries, the last 5 sit below
            # the fold and operators don't realize the dropdown
            # scrolls. Force Qt's own popup (combobox-popup: 0
            # disables the macOS native popup, which IGNORES
            # maxVisibleItems) and set the limit high enough that
            # adding a few more templates won't re-introduce the
            # cutoff.
            self.template_combo.setStyleSheet(
                "QComboBox { combobox-popup: 0; }"
            )
            self.template_combo.setMaxVisibleItems(30)
            self.template_combo.addItem("— Custom (no template) —", "")
            for t in self._traffic_templates_meta:
                self.template_combo.addItem(t["title"], t["key"])
            self.template_combo.currentIndexChanged.connect(
                self._on_traffic_template_changed
            )
            template_bar.addWidget(self.template_combo, 1)
            # v0.3.11 compact: the summary label starts EMPTY (no
            # default "Pick a template to pre-fill all tabs." row)
            # — the dropdown's own "— Custom (no template) —"
            # placeholder already conveys that. When the operator
            # picks a real template, _on_traffic_template_changed
            # fills the label with the template's summary, which
            # expands the row at that point.
            self._traffic_template_summary = QLabel("")
            self._traffic_template_summary.setStyleSheet(
                "color: #6b7280; font-size: 11px;"
            )
            self._traffic_template_summary.setWordWrap(True)
            self.main_layout.addLayout(template_bar)
            self.main_layout.addWidget(self._traffic_template_summary)

        self.main_layout.addWidget(self.scroll_area)

        # Buttons with professional styling
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        
        # Style the buttons - more compact
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        cancel_button = self.buttons.button(QDialogButtonBox.Cancel)
        if ok_button:
            ok_button.setText("Save")
            ok_button.setMinimumWidth(80)
            ok_button.setMinimumHeight(26)
        if cancel_button:
            cancel_button.setText("Cancel")
            cancel_button.setMinimumWidth(80)
            cancel_button.setMinimumHeight(26)
        
        self.main_layout.addWidget(self.buttons)
        self.setLayout(self.main_layout)

        # v0.2.96: Ctrl+Return = Save. Standard Qt dialog shortcut
        # that was missing — operators expect it from the main window
        # (Ctrl+S Save Session, Ctrl+Return Apply Stream) and every
        # other modal in the app.
        try:
            _save_sc = QShortcut(
                QKeySequence(Qt.CTRL + Qt.Key_Return), self,
            )
            _save_sc.setContext(Qt.WindowShortcut)
            _save_sc.activated.connect(self.accept)
        except Exception:
            pass  # shortcut is convenience; never block dialog open

        # v0.2.96: live red-border feedback on MAC + IP fields.
        # Wired AFTER setup_*_section methods have run (everything
        # in __init__ before this line builds the fields).
        self._wire_live_validators()

        # Populate RX list after protocol tab exists
        self.populate_rx_ports(self.tx_port_name)

        # Populate existing data (edit case)
        if self.stream_data:
            self.populate_stream_fields(self.stream_data)

        # Dynamic updates for Packet View
        self.connect_protocol_data_to_packet_view()

    def _apply_professional_styling(self):
        """Apply professional styling to the dialog."""
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                font-size: 10px;
            }
            
            QTabWidget::pane {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: #ffffff;
                top: -1px;
            }
            
            QTabBar::tab {
                background-color: #f3f4f6;
                color: #4b5563;
                border: 1px solid #d1d5db;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                padding: 4px 8px;
                margin-right: 1px;
                font-weight: 500;
                font-size: 9px;
                min-width: 60px;
                max-width: 80px;
            }
            
            QTabWidget::pane {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: #ffffff;
                top: -1px;
            }
            
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #1f2937;
                border-bottom: 2px solid #3b82f6;
                font-weight: 600;
            }
            
            QTabBar::tab:hover:!selected {
                background-color: #e5e7eb;
                color: #374151;
            }
            
            QGroupBox {
                font-weight: 600;
                font-size: 11px;
                color: #1f2937;
                border: 1px solid #e5e7eb;
                border-radius: 5px;
                margin-top: 5px;          /* v0.3.11 compact: was 8 */
                padding-top: 4px;         /* v0.3.11 compact: was 10 */
                padding-bottom: 2px;      /* v0.3.11 compact: was 8 */
                background-color: #f9fafb;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 8px;
                background-color: #ffffff;
                color: #1f2937;
            }
            
            QFormLayout {
                spacing: 12px;
            }
            
            QFormLayout QLabel {
                color: #374151;
                font-weight: 500;
                font-size: 10px;
                min-width: 120px;
            }
            
            QLineEdit, QComboBox, QSpinBox, QTextEdit {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 4px 8px;
                background-color: #ffffff;
                color: #1f2937;
                font-size: 10px;
                min-height: 18px;
            }
            
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {
                border: 2px solid #3b82f6;
                background-color: #f0f9ff;
            }
            
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover {
                border: 1px solid #9ca3af;
            }
            
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid #6b7280;
                width: 0;
                height: 0;
            }
            
            QComboBox QAbstractItemView {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: #ffffff;
                selection-background-color: #dbeafe;
                selection-color: #1e40af;
                padding: 4px;
            }
            
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-weight: 600;
                font-size: 10px;
                min-height: 28px;
            }
            
            QPushButton:hover {
                background-color: #2563eb;
            }
            
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            
            QPushButton:disabled {
                background-color: #9ca3af;
                color: #d1d5db;
            }
            
            QDialogButtonBox QPushButton[text="Cancel"] {
                background-color: #ffffff;
                color: #374151;
                border: 1px solid #d1d5db;
            }
            
            QDialogButtonBox QPushButton[text="Cancel"]:hover {
                background-color: #f9fafb;
                border-color: #9ca3af;
            }
            
            QDialogButtonBox QPushButton[text="Save"] {
                background-color: #3b82f6;
                color: #ffffff;
            }
            
            QDialogButtonBox QPushButton[text="Save"]:hover {
                background-color: #2563eb;
            }
            
            QCheckBox, QRadioButton {
                color: #374151;
                font-weight: 500;
                font-size: 10px;
                spacing: 6px;
            }
            
            QCheckBox::indicator, QRadioButton::indicator {
                width: 14px;
                height: 14px;
                border: 2px solid #d1d5db;
                border-radius: 3px;
                background-color: #ffffff;
            }
            
            QCheckBox::indicator:checked, QRadioButton::indicator:checked {
                background-color: #3b82f6;
                border-color: #3b82f6;
            }
            
            QCheckBox::indicator:checked {
                background-color: #3b82f6;
                border-color: #3b82f6;
            }
            
            QCheckBox::indicator:hover, QRadioButton::indicator:hover {
                border-color: #3b82f6;
            }
            
            QScrollArea {
                border: none;
                background-color: #ffffff;
            }
            
            QScrollBar:vertical {
                border: none;
                background-color: #f3f4f6;
                width: 12px;
                border-radius: 6px;
            }
            
            QScrollBar::handle:vertical {
                background-color: #d1d5db;
                border-radius: 6px;
                min-height: 30px;
            }
            
            QScrollBar::handle:vertical:hover {
                background-color: #9ca3af;
            }
            
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                background-color: #ffffff;
                gridline-color: #f3f4f6;
            }
            
            QTableWidget::item {
                padding: 4px;
                border: none;
                font-size: 10px;
            }
            
            QTableWidget::item:selected {
                background-color: #dbeafe;
                color: #1e40af;
            }
            
            QHeaderView::section {
                background-color: #f9fafb;
                color: #374151;
                padding: 6px;
                border: none;
                border-bottom: 2px solid #e5e7eb;
                font-weight: 600;
                font-size: 10px;
            }
        """)

    # ----------------------------- Tabs & Sections -----------------------------

    '''def setup_variable_fields_tab(self):
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Variable Fields configuration goes here."))
        self.variable_fields_tab.setLayout(layout)'''

    def setup_variable_fields_tab(self):
        """
        Variable Fields: add a simple engine selector toggle that maps to
        stream_data['dpdk_enable'] (bool). Default is Scapy/kernel path.
        """
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header
        header = QLabel("<h3 style='color: #1f2937; margin-bottom: 8px;'>Runtime Engine</h3>")
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        # v0.3.12: engine picker — supersedes the binary DPDK checkbox
        # below. The checkbox is retained as a backward-compat bridge
        # (load reads dpdk_enable when engine isn't set; save writes
        # both keys so old servers keep working). New code should read
        # stream_data["engine"] which is one of:
        #   "scapy"  → Python / scapy.sendp fallback
        #   "dpdk"   → tx_worker C binary
        #   "rdma"   → ib_*_bw / ib_*_lat via utils/rdma_perf (v0.3.12)
        engine_row = QWidget()
        engine_layout = QHBoxLayout(engine_row)
        engine_layout.setContentsMargins(0, 0, 0, 0)
        engine_layout.addWidget(QLabel("Engine:"))
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Scapy (kernel)",       "scapy")
        self.engine_combo.addItem("DPDK (tx_worker)",     "dpdk")
        self.engine_combo.addItem("RDMA (perftest)",      "rdma")
        self.engine_combo.setToolTip(
            "Pick how this stream gets pushed onto the wire.\n\n"
            "  • Scapy   — Python frame builder. Works for every L3/L4 "
            "combo the GUI supports. ~50–200 kpps single-core.\n"
            "  • DPDK    — tx_worker C binary. UDP/IPv4 only, line "
            "rate on 100G+. See the checkbox below for sub-options.\n"
            "  • RDMA    — perftest (ib_*_bw / ib_*_lat). Requires "
            "RDMA-capable NIC + perftest installed. Uses peer addr, "
            "msg size, QPs from the RDMA params group below."
        )
        engine_layout.addWidget(self.engine_combo)
        engine_layout.addStretch(1)
        layout.addWidget(engine_row)
        # Bidirectional sync with dpdk_enable_checkbox + show/hide RDMA
        # params group. Wired below once all widgets exist.
        self.engine_combo.currentIndexChanged.connect(
            self._on_engine_combo_changed,
        )

        # DPDK toggle
        self.dpdk_enable_checkbox = QCheckBox("Use DPDK (tx_worker)")
        self.dpdk_enable_checkbox.setToolTip(
            "Enable the high-performance DPDK-based tx_worker backend.\n"
            "\n"
            "Supported by tx_worker (everything else silently falls back\n"
            "to Scapy — the Start response will flag it):\n"
            "  • L3: IPv4 only (no IPv6)\n"
            "  • L4: UDP only (no TCP / ICMP / IGMP)\n"
            "  • Tags: untagged or single 802.1Q (no 802.1ad / QinQ)\n"
            "  • No MPLS / SR-MPLS label stack\n"
            "\n"
            "NIC hints:\n"
            "  • mlx5 / NVIDIA: runs with the kernel driver (no vfio).\n"
            "  • Broadcom NetXtreme-E / Thor2: binds to vfio-pci.\n"
            "  • 100Gbps+: auto-enables multi-instance mode.\n"
            "  • 400Gbps: uses 8–16 instances for line rate.\n"
            "\n"
            "See Help → DPDK Traffic Blast Workflow for prerequisites."
        )
        layout.addWidget(self.dpdk_enable_checkbox)

        # v0.3.11: pre-validation banner — when the main window's
        # cached DPDK chip says the server isn't ready, surface
        # that here so the operator doesn't tick "Use DPDK" only
        # to watch the stream silently fall back to Scapy. Clickable
        # link opens the Make DPDK Ready orchestrator. Falls back
        # to a static note when we can't reach the main window
        # (e.g. dialog opened from a context that doesn't expose it).
        self._dpdk_not_ready_banner = QLabel("")
        self._dpdk_not_ready_banner.setOpenExternalLinks(False)
        self._dpdk_not_ready_banner.setTextFormat(Qt.RichText)
        self._dpdk_not_ready_banner.setWordWrap(True)
        self._dpdk_not_ready_banner.setStyleSheet(
            "QLabel { background: #fef3c7; border: 1px solid #f59e0b; "
            "border-radius: 4px; padding: 4px 8px; color: #92400e; "
            "font-size: 11px; }"
        )
        self._dpdk_not_ready_banner.hide()
        self._dpdk_not_ready_banner.linkActivated.connect(
            self._on_dpdk_make_ready_link,
        )
        layout.addWidget(self._dpdk_not_ready_banner)
        # Populate the banner based on the host main window's chip
        # state (cached snapshot — no extra HTTP). Best-effort; if
        # the main window or chip is missing, banner stays hidden.
        self._refresh_dpdk_readiness_banner()

        # Multi-instance DPDK option (for high rates)
        self.dpdk_multi_instance_checkbox = QCheckBox("Force Multi-Instance DPDK (100Gbps+)")
        self.dpdk_multi_instance_checkbox.setToolTip(
            "Force multi-instance mode for high-rate traffic.\n"
            "Automatically enabled for rates > 50M pps or line rate.\n"
            "Launches multiple tx_worker instances to saturate high-speed NICs.\n"
            "Recommended for 100Gbps and 400Gbps ports."
        )
        layout.addWidget(self.dpdk_multi_instance_checkbox)

        # TX Cores (multi-queue inside one tx_worker process)
        tx_cores_row = QWidget()
        tx_cores_layout = QHBoxLayout(tx_cores_row)
        tx_cores_layout.setContentsMargins(0, 0, 0, 0)
        tx_cores_label = QLabel("TX Cores (queues):")
        tx_cores_label.setStyleSheet("color: #1f2937;")
        self.dpdk_tx_cores_combo = QComboBox()
        for n in (1, 2, 4, 8, 12, 16):
            self.dpdk_tx_cores_combo.addItem(str(n), n)
        self.dpdk_tx_cores_combo.setCurrentIndex(0)  # default: 1
        self.dpdk_tx_cores_combo.setToolTip(
            "Number of TX queues / worker lcores inside the DPDK tx_worker process.\n"
            "Higher values let one NIC port saturate higher line rates.\n\n"
            "Tested on Mellanox CX-7 (100G):\n"
            "  • 1 core  ≈ 4.5 Mpps (64B)  /  49 Gbps (1500B)\n"
            "  • 2 cores ≈ 9.1 Mpps (64B)  /  100 Gbps (1500B) — saturates 100G\n"
            "  • 4 cores ≈ 18 Mpps  (64B)  /  200 Gbps (1500B)\n"
            "  • 8 cores ≈ 36 Mpps  (64B)  /  385 Gbps (1500B) — approaches 400G\n\n"
            "Cost: each core consumes one CPU thread on the NIC's NUMA node.\n"
            "Default 1 = backwards-compatible single-queue behavior."
        )
        self.dpdk_tx_cores_recommend_btn = QPushButton("Recommend")
        self.dpdk_tx_cores_recommend_btn.setToolTip(
            "Ask the server to recommend a TX core count for this stream's\n"
            "interface, frame size, and target rate."
        )
        self.dpdk_tx_cores_recommend_btn.setStyleSheet(
            "QPushButton { padding: 4px 10px; color: #1e40af; "
            "border: 1px solid #93c5fd; border-radius: 4px; background: #eff6ff; } "
            "QPushButton:hover { background: #dbeafe; }"
        )
        self.dpdk_tx_cores_recommend_btn.clicked.connect(self._recommend_tx_cores)

        self.dpdk_tx_cores_hint = QLabel("")
        self.dpdk_tx_cores_hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.dpdk_tx_cores_hint.setWordWrap(True)

        tx_cores_layout.addWidget(tx_cores_label)
        tx_cores_layout.addWidget(self.dpdk_tx_cores_combo)
        tx_cores_layout.addWidget(self.dpdk_tx_cores_recommend_btn)
        tx_cores_layout.addStretch(1)
        layout.addWidget(tx_cores_row)
        layout.addWidget(self.dpdk_tx_cores_hint)

        # One-way latency: embed a CLOCK_MONOTONIC timestamp at the start
        # of each UDP payload. Server-side LatencySampler decodes the
        # NLAT magic on the RX side, computes per-packet latency, and
        # exposes min/avg/p99 via /api/latency/stats. The Stream
        # Statistics tab renders these as a "Latency (us)" column.
        self.enable_timestamps_checkbox = QCheckBox(
            "Enable timestamps (one-way latency)"
        )
        self.enable_timestamps_checkbox.setToolTip(
            "Embeds a 16-byte NLAT timestamp at the start of each UDP\n"
            "payload. The server's latency sampler decodes it and reports\n"
            "min / avg / p50 / p99 / max one-way latency in microseconds.\n\n"
            "Same-host (loopback) gives accurate one-way numbers. Cross-host\n"
            "requires PTP-synced clocks for absolute accuracy; without that\n"
            "only relative drift is meaningful.\n\n"
            "Requires frame size >= 60B (16 bytes for the header + L2/3/4)."
        )
        layout.addWidget(self.enable_timestamps_checkbox)

        # Small helper text
        hint = QLabel(
            "When enabled, this stream will be transmitted by the DPDK worker.\n"
            "Multi-instance mode is auto-enabled for high rates (100Gbps+).\n"
            "Otherwise the Scapy/kernel path is used."
        )
        hint.setStyleSheet("color: #6b7280; font-size: 12px; padding-top: 8px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        
        # Read More button — opens the DPDK Workflow Guide
        # (also reachable from main window: Help → DPDK Traffic Blast Workflow)
        # v0.3.6: previous stylesheet set `color: #3b82f6` (blue) but
        # left the background unspecified, so the button inherited the
        # dialog's primary-button background (also blue, set by the
        # dialog-wide stylesheet earlier in __init__) → blue text on
        # blue background = invisible button. Operator saw a solid
        # blue bar with no readable label. Explicit background + a
        # contrasting border + cursor + hover state — same shape as
        # the "neutral white" buttons elsewhere in the app (Stats
        # dock Clear/Export, DPDK Status Unbind, etc.).
        read_more_button = QPushButton("📖  Read More: DPDK Traffic Blast Workflow")
        read_more_button.setCursor(Qt.PointingHandCursor)
        read_more_button.setStyleSheet(
            "QPushButton {"
            "  text-align: left;"
            "  padding: 6px 12px;"
            "  color: #1d4ed8;"            # deeper blue for AA contrast on white
            "  background-color: #ffffff;"
            "  border: 1px solid #93c5fd;"
            "  border-radius: 5px;"
            "  font-weight: 500;"
            "}"
            "QPushButton:hover {"
            "  background-color: #eff6ff;"
            "  border-color: #2563eb;"
            "  color: #1e40af;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #dbeafe;"
            "}"
        )
        read_more_button.clicked.connect(self._show_dpdk_usage_guide)
        layout.addWidget(read_more_button)

        # v0.3.12 — RDMA params group. Visible only when Engine = RDMA
        # (controlled by _on_engine_combo_changed). Holds the perftest
        # invocation params for the in-Streams-tab RDMA flow.
        from PyQt5.QtWidgets import QGroupBox, QFormLayout
        self.rdma_params_group = QGroupBox("RDMA (perftest) parameters")
        rdma_form = QFormLayout(self.rdma_params_group)
        rdma_form.setLabelAlignment(Qt.AlignRight)
        rdma_form.setHorizontalSpacing(8)
        rdma_form.setVerticalSpacing(6)

        self.rdma_test_combo = QComboBox()
        for tid, label in [
            ("send_bw",   "Send — Bandwidth"),
            ("write_bw",  "RDMA Write — Bandwidth"),
            ("read_bw",   "RDMA Read — Bandwidth"),
            ("send_lat",  "Send — Latency"),
            ("write_lat", "RDMA Write — Latency"),
            ("read_lat",  "RDMA Read — Latency"),
        ]:
            self.rdma_test_combo.addItem(label, tid)
        self.rdma_test_combo.setToolTip(
            "ib_*_bw drive bandwidth tests; ib_*_lat drive latency "
            "tests (single-op ping-pong). The stream starts the client "
            "side of perftest — pair it with a server-side perftest "
            "running on the peer TG (start that via Tools → RDMA → "
            "Blast a RDMA Flow with role=server, or from a separate "
            "stream on the peer)."
        )
        rdma_form.addRow("Test type:", self.rdma_test_combo)

        self.rdma_device_field = QLineEdit("mlx5_0")
        self.rdma_device_field.setPlaceholderText("mlx5_0")
        self.rdma_device_field.setToolTip(
            "ib device name. Check Tools → RDMA → RDMA Devices on the "
            "target server."
        )
        rdma_form.addRow("Device (-d):", self.rdma_device_field)

        self.rdma_peer_field = QLineEdit("")
        self.rdma_peer_field.setPlaceholderText("10.0.0.2 (server-side IP)")
        self.rdma_peer_field.setToolTip(
            "IP address of the peer TG running perftest in server mode."
        )
        rdma_form.addRow("Peer address:", self.rdma_peer_field)

        self.rdma_msg_size_spin = QSpinBox()
        self.rdma_msg_size_spin.setRange(2, 16 * 1024 * 1024)
        self.rdma_msg_size_spin.setSingleStep(1024)
        self.rdma_msg_size_spin.setValue(65536)
        self.rdma_msg_size_spin.setSuffix(" B")
        rdma_form.addRow("Message size (-s):", self.rdma_msg_size_spin)

        self.rdma_qp_count_spin = QSpinBox()
        # v0.3.15: raised 1024 → 131072 to match the typical Mellanox
        # ConnectX-7 max_qp ceiling. See Tools → RDMA → RDMA Devices
        # for the per-HCA max_qp on your hardware.
        self.rdma_qp_count_spin.setRange(1, 131072)
        self.rdma_qp_count_spin.setValue(1)
        self.rdma_qp_count_spin.setToolTip(
            "Parallel QPs (-q). 1–16 typical for BW; 32–128 for "
            "queue saturation. Per-HCA max_qp visible in Tools → "
            "RDMA → RDMA Devices (v0.3.15+)."
        )
        rdma_form.addRow("QP count (-q):", self.rdma_qp_count_spin)

        self.rdma_duration_spin = QSpinBox()
        self.rdma_duration_spin.setRange(1, 3600)
        self.rdma_duration_spin.setValue(30)
        self.rdma_duration_spin.setSuffix(" sec")
        rdma_form.addRow("Duration (-D):", self.rdma_duration_spin)

        self.rdma_gid_index_spin = QSpinBox()
        self.rdma_gid_index_spin.setRange(0, 255)
        self.rdma_gid_index_spin.setValue(3)
        rdma_form.addRow("GID index (-x):", self.rdma_gid_index_spin)

        self.rdma_bidir_check = QCheckBox("Bidirectional (-b)")
        rdma_form.addRow("", self.rdma_bidir_check)

        self.rdma_params_group.setVisible(False)  # shown when engine=rdma
        layout.addWidget(self.rdma_params_group)

        layout.addStretch(1)
        self.variable_fields_tab.setLayout(layout)
        # Wire the dpdk_enable checkbox back into the combo so the two
        # stay synced regardless of which the operator interacts with.
        # Adding here (post-construction) so both widgets exist by now.
        self.dpdk_enable_checkbox.toggled.connect(
            self._on_dpdk_checkbox_toggled,
        )
    
    def _resolve_server_address_for_tx_port(self) -> str:
        """
        Map this dialog's TX port (e.g. 'TG 0 - Port: enp181s0f0np0') to the
        matching server address in self.server_interfaces. Returns 'host:port'
        suitable for http://<host:port>/api/... or '' if unknown.
        """
        if not self.tx_port:
            return ""
        # Pull "TG N" out of the port label
        try:
            tg_part = self.tx_port.split("-")[0].strip()  # 'TG 0'
            tg_num = tg_part.replace("TG", "").strip()
        except Exception:
            tg_num = ""

        # The dialog is given a locally-built server_interfaces list (just
        # tg_id + ports) for the RX-port dropdown — it usually has no
        # 'address' field. The main window's self.server_interfaces is the
        # authoritative list that does carry the address. Search both, in
        # priority order: dialog list (if it happens to include addresses),
        # then the main window's list reached via parent().
        candidate_lists = []
        if self.server_interfaces:
            candidate_lists.append(self.server_interfaces)
        try:
            parent = self.parent()
            parent_servers = getattr(parent, "server_interfaces", None) if parent else None
            if parent_servers:
                candidate_lists.append(parent_servers)
        except Exception:
            pass

        for servers in candidate_lists:
            for server in servers:
                sid = str(server.get("tg_id", "")).strip()
                if sid == tg_num:
                    addr = str(server.get("address", "")).strip()
                    if addr:
                        return addr

        # Fallback: first server in any list that has an address
        for servers in candidate_lists:
            for server in servers:
                addr = str(server.get("address", "")).strip()
                if addr:
                    return addr
        return ""

    def _recommend_tx_cores(self):
        """
        Ask the server for a recommended dpdk_tx_cores based on this stream's
        interface, frame size, and target rate, then update the combo box.
        """
        try:
            import requests
        except ImportError:
            self.dpdk_tx_cores_hint.setText("requests module not available")
            return

        addr = self._resolve_server_address_for_tx_port()
        if not addr:
            self.dpdk_tx_cores_hint.setText(
                "Could not resolve server address for this TX port"
            )
            return

        # Best-effort frame_size + target pps from the current dialog state
        try:
            frame_size = int(self.frame_size.text().strip() or "64")
        except Exception:
            frame_size = 64
        try:
            target_pps = int(self.stream_pps_field.text().strip() or "0") if hasattr(self, "stream_pps_field") else 0
        except Exception:
            target_pps = 0

        iface = self.tx_port_name or ""
        # The main window's server entries already include a scheme
        # (e.g. "http://svl-d-ai-srv01:5050"). Only prepend http:// when
        # the address comes from somewhere that didn't include one.
        if addr.startswith("http://") or addr.startswith("https://"):
            base = addr.rstrip("/")
        else:
            base = "http://" + addr.rstrip("/")
        url = f"{base}/api/dpdk/recommend"
        params = {"iface": iface, "frame_size": frame_size, "pps": target_pps}

        try:
            r = requests.get(url, params=params, timeout=3)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self.dpdk_tx_cores_hint.setText(f"Recommendation request failed: {e}")
            return

        if not data.get("ok"):
            self.dpdk_tx_cores_hint.setText(
                f"Server error: {data.get('error', 'unknown')}"
            )
            return

        rec = int(data.get("recommended_tx_cores") or 1)
        idx = self.dpdk_tx_cores_combo.findData(rec)
        if idx >= 0:
            self.dpdk_tx_cores_combo.setCurrentIndex(idx)
        explanation = data.get("explanation") or f"Recommended: {rec}"
        self.dpdk_tx_cores_hint.setText(explanation)

    def _show_dpdk_usage_guide(self):
        """Open the DPDK Workflow Guide. Same dialog used by the Help menu."""
        show_dpdk_usage_guide(self)

    def _refresh_dpdk_readiness_banner(self):
        """v0.3.11: surface a warning when the server's DPDK isn't
        ready, so ticking 'Use DPDK' doesn't silently fall back to
        Scapy at runtime.

        Reads the cached chip state from the main window (no extra
        HTTP). Banner stays hidden when the chip is green or
        unavailable. Single-line nudge with a 'Make DPDK Ready'
        link that opens the orchestrator dialog.
        """
        try:
            mw = self.parent()
            # Walk up to find the main window — dialog may be
            # nested inside a tab or other widget.
            while mw is not None and not hasattr(mw, "dpdk_chip"):
                mw = mw.parent() if hasattr(mw, "parent") else None
            chip = getattr(mw, "dpdk_chip", None) if mw else None
            state = chip.state() if chip and hasattr(chip, "state") else "gray"
        except Exception:
            state = "gray"

        if state == "green":
            self._dpdk_not_ready_banner.hide()
            return
        if state == "gray":
            # No server selected / poll failed — don't shout. The
            # operator may genuinely not need DPDK on this stream.
            self._dpdk_not_ready_banner.hide()
            return

        if state == "amber":
            msg = (
                "<b>⚠ DPDK is partially set up</b> on the selected "
                "server (missing one or more of hugepages / IOMMU / "
                "vfio-pci). Some NICs (Mellanox mlx5) work anyway, "
                "but Intel/Broadcom NICs will fall back to Scapy.<br/>"
                "<a href='make-ready'>Open Make DPDK Ready…</a> to fix."
            )
        else:  # red
            msg = (
                "<b>⛔ DPDK is not usable</b> on the selected server "
                "(libdpdk or tx_worker missing). Ticking 'Use DPDK' "
                "below will silently fall back to Scapy at start "
                "time.<br/>"
                "<a href='make-ready'>Open Make DPDK Ready…</a> to fix."
            )
        self._dpdk_not_ready_banner.setText(msg)
        self._dpdk_not_ready_banner.show()

    def _on_dpdk_make_ready_link(self, href: str):
        """Bridge the banner's 'Make DPDK Ready' link to the
        orchestrator dialog. Resolves the main window the same way
        the banner builder does — if anything's missing, silently
        no-op (the operator can still use Tools→DPDK manually)."""
        if href != "make-ready":
            return
        try:
            mw = self.parent()
            while mw is not None and not hasattr(mw, "show_dpdk_make_ready_dialog"):
                mw = mw.parent() if hasattr(mw, "parent") else None
            if mw is not None:
                mw.show_dpdk_make_ready_dialog()
        except Exception:
            pass

    def _apply_rate_type_ui_state(self):
        """Enable only the input relevant to current Rate Type."""
        if not hasattr(self, "rate_type_dropdown"):
            return
        rt = self.rate_type_dropdown.currentText()

        # default: everything off
        self.stream_pps_rate.setEnabled(False)
        self.stream_bit_rate.setEnabled(False)
        self.stream_load_percentage.setEnabled(False)

        if rt == "Packets Per Second (PPS)":
            self.stream_pps_rate.setEnabled(True)
        elif rt == "Bit Rate":
            self.stream_bit_rate.setEnabled(True)
        elif rt == "Load (%)":
            self.stream_load_percentage.setEnabled(True)
        elif rt == "Line Rate":
            # keep all disabled
            pass

    def build_rate_control(self) -> dict:
        """Return a normalized rate control dict for the server/client logic."""
        kind = self.rate_type_dropdown.currentText()
        mode_map = {
            "Packets Per Second (PPS)": "pps",
            "Bit Rate": "bitrate",
            "Load (%)": "load",
            "Line Rate": "line",
        }
        mode = mode_map.get(kind, "pps")

        # Safe parsing
        def as_int(widget, default=0):
            txt = (widget.text() or "").strip()
            try:
                return int(txt)
            except Exception:
                return default

        rc = {"mode": mode}
        if mode == "pps":
            rc["pps"] = as_int(self.stream_pps_rate, 0)
        elif mode == "bitrate":
            rc["mbps"] = as_int(self.stream_bit_rate, 0)  # keep as Mbps in UI
        elif mode == "load":
            rc["percent"] = as_int(self.stream_load_percentage, 0)
        else:  # line
            rc["line_rate"] = True

        # Duration
        dur_mode = self.duration_mode_dropdown.currentText()
        if dur_mode == "Seconds":
            rc["duration"] = {"mode": "seconds", "seconds": as_int(self.stream_duration_field, 10)}
        else:
            rc["duration"] = {"mode": "continuous"}

        return rc



    def setup_stream_control_tab(self):
        """Sets up the Stream Control Tab with rate control and duration settings."""
        control_layout = QVBoxLayout()
        control_layout.setSpacing(20)
        control_layout.setContentsMargins(20, 20, 20, 20)

        # --- Rate Control ---
        rate_group = QGroupBox("Rate Control")
        rate_layout = QFormLayout()
        rate_layout.setSpacing(12)
        rate_layout.setContentsMargins(16, 20, 16, 16)

        self.rate_type_dropdown = QComboBox()
        self.rate_type_dropdown.addItems([
            "Packets Per Second (PPS)",
            "Bit Rate",
            "Load (%)",
            "Line Rate"
        ])
        self.rate_type_dropdown.setToolTip(
            "How the stream rate is interpreted:\n"
            " • Packets Per Second (PPS) — explicit packet count "
            "per second; the Stream PPS Rate field is used.\n"
            " • Bit Rate — explicit Mbps; the Stream Bit Rate "
            "(Mbps) field is used. Frame size determines the pps.\n"
            " • Load (%) — percent of port line rate.\n"
            " • Line Rate — saturate the link (DPDK only — Scapy "
            "tops out around 1-2 Mpps regardless of frame size)."
        )
        rate_layout.addRow("Rate Type:", self.rate_type_dropdown)

        self.stream_pps_rate = QLineEdit("1000")
        self.stream_pps_rate.setValidator(QIntValidator(1, 1_000_000_000))
        rate_layout.addRow("Packets Per Second (PPS):", self.stream_pps_rate)

        self.stream_bit_rate = QLineEdit("100")  # Mbps
        self.stream_bit_rate.setValidator(QIntValidator(1, 1_000_000))
        rate_layout.addRow("Bit Rate (Mbps):", self.stream_bit_rate)

        self.stream_load_percentage = QLineEdit("50")
        self.stream_load_percentage.setValidator(QIntValidator(1, 100))
        rate_layout.addRow("Load (%):", self.stream_load_percentage)

        def _apply_rate_type_ui_state():
            """Enable only the input relevant to current Rate Type."""
            rt = self.rate_type_dropdown.currentText()

            # Turn everything off first
            self.stream_pps_rate.setEnabled(False)
            self.stream_bit_rate.setEnabled(False)
            self.stream_load_percentage.setEnabled(False)

            if rt == "Packets Per Second (PPS)":
                self.stream_pps_rate.setEnabled(True)
            elif rt == "Bit Rate":
                self.stream_bit_rate.setEnabled(True)
            elif rt == "Load (%)":
                self.stream_load_percentage.setEnabled(True)
            elif rt == "Line Rate":
                # all remain disabled
                pass

        self.rate_type_dropdown.currentTextChanged.connect(lambda _: _apply_rate_type_ui_state())

        rate_group.setLayout(rate_layout)
        control_layout.addWidget(rate_group)

        # --- Duration Control ---
        duration_group = QGroupBox("Duration Control")
        duration_layout = QFormLayout()
        duration_layout.setSpacing(12)
        duration_layout.setContentsMargins(16, 20, 16, 16)

        self.duration_mode_dropdown = QComboBox()
        self.duration_mode_dropdown.addItems(["Continuous", "Seconds"])
        duration_layout.addRow("Duration Mode:", self.duration_mode_dropdown)

        self.stream_duration_field = QLineEdit("10")
        self.stream_duration_field.setValidator(QIntValidator(1, 86_400))
        duration_layout.addRow("Duration (Seconds):", self.stream_duration_field)

        def _apply_duration_ui_state():
            """Enable seconds field only when Duration Mode == Seconds."""
            self.stream_duration_field.setEnabled(self.duration_mode_dropdown.currentText() == "Seconds")

        self.duration_mode_dropdown.currentTextChanged.connect(lambda _: _apply_duration_ui_state())

        duration_group.setLayout(duration_layout)
        control_layout.addWidget(duration_group)

        control_layout.addStretch(1)
        self.stream_control_tab.setLayout(control_layout)

        # Initialize UI states after widgets exist
        QTimer.singleShot(0, _apply_rate_type_ui_state)
        QTimer.singleShot(0, _apply_duration_ui_state)

    def setup_protocol_selection_tab(self):
        # v0.3.11 compact pass: tighten outer + per-section spacing.
        self.protocol_tab_layout.setContentsMargins(6, 4, 8, 4)
        self.protocol_tab_layout.setSpacing(3)  # was 6 — section gap

        # Basics - Improved layout with better organization
        basics_group = QGroupBox("Basics")
        basics_layout = QGridLayout()
        basics_layout.setContentsMargins(8, 4, 8, 4)  # v0.3.11 compact
        basics_layout.setHorizontalSpacing(6)
        basics_layout.setVerticalSpacing(3)  # was 6 — tighter Name/Details/Flow rows
        basics_layout.setColumnStretch(1, 1)
        basics_layout.setColumnStretch(3, 1)
        basics_layout.setColumnStretch(5, 1)

        self.stream_name = QLineEdit()
        self.stream_name.setMinimumWidth(200)
        self.enabled_checkbox = QCheckBox("Enabled")
        self.details_field = QLineEdit()
        self.details_field.setMinimumWidth(200)

        self.rx_port_dropdown = QComboBox()
        self.rx_port_dropdown.setMinimumWidth(200)
        self.rx_port_dropdown.addItem("Same as TX Port")

        self.flow_tracking_checkbox = QCheckBox("Enable Flow Tracking")
        self.flow_tracking_checkbox.setChecked(False)

        # Note: DPDK checkbox state will be set in populate_stream_fields()
        # when stream_data is available

        # Row 0: Name and Enabled
        basics_layout.addWidget(QLabel("Name:"), 0, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.stream_name, 0, 1)
        basics_layout.addWidget(QLabel("Enabled:"), 0, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.enabled_checkbox, 0, 3)
        
        # Row 1: Details and RX Port
        basics_layout.addWidget(QLabel("Details:"), 1, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.details_field, 1, 1)
        basics_layout.addWidget(QLabel("RX Port:"), 1, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.rx_port_dropdown, 1, 3)
        
        # Row 2: Flow Tracking
        basics_layout.addWidget(QLabel("Flow Tracking:"), 2, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.flow_tracking_checkbox, 2, 1)

        basics_group.setLayout(basics_layout)
        self.protocol_tab_layout.addWidget(basics_group)

        # Frame Length - Improved layout with reduced spacing
        frame_length_group = QGroupBox("Frame Length (including FCS)")
        frame_length_layout = QGridLayout()
        frame_length_layout.setContentsMargins(0, 2, 8, 2)  # v0.3.11 compact pass 2
        frame_length_layout.setSpacing(1)  # was 3 — tightest row stack
        frame_length_layout.setHorizontalSpacing(0)  # No horizontal spacing between columns
        # Don't stretch label columns (0, 2) - keep them compact
        frame_length_layout.setColumnStretch(0, 0)  # Label column - no stretch
        frame_length_layout.setColumnStretch(1, 1)  # Input column - stretch
        frame_length_layout.setColumnStretch(2, 0)  # Label column - no stretch
        frame_length_layout.setColumnStretch(3, 1)  # Input column - stretch
        # Remove minimum width - let labels size naturally to their content

        self.frame_type = QComboBox()
        self.frame_type.addItems(["Fixed", "Random", "IMIX"])
        self.frame_type.setMinimumWidth(120)
        self.frame_type.setMaximumWidth(200)
        self.frame_min = QLineEdit("64")
        self.frame_min.setMinimumWidth(80)
        self.frame_min.setMaximumWidth(120)
        self.frame_max = QLineEdit("1518")
        self.frame_max.setMinimumWidth(80)
        self.frame_max.setMaximumWidth(120)
        self.frame_size = QLineEdit("64")
        self.frame_size.setMinimumWidth(80)
        self.frame_size.setMaximumWidth(120)
        # v0.3.11: bumped 1518 → 9216 so operators can enter jumbo
        # frames. Was a silent blocker — keystrokes >1518 were
        # rejected by the validator even though tx_worker, the
        # server, and the Blast a Flow dialog all accept up to
        # 9216. Same operator, two dialogs, two ceilings → fixed.
        self.frame_min.setValidator(QIntValidator(64, 9216))
        self.frame_max.setValidator(QIntValidator(64, 9216))
        self.frame_size.setValidator(QIntValidator(64, 9216))

        # Create labels with minimal spacing
        frame_type_label = QLabel("Frame Type:")
        frame_type_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        frame_length_layout.addWidget(frame_type_label, 0, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        frame_length_layout.addWidget(self.frame_type, 0, 1)
        
        fixed_size_label = QLabel("Fixed Size:")
        fixed_size_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        frame_length_layout.addWidget(fixed_size_label, 0, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        frame_length_layout.addWidget(self.frame_size, 0, 3)
        
        min_label = QLabel("Min:")
        min_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        frame_length_layout.addWidget(min_label, 1, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        frame_length_layout.addWidget(self.frame_min, 1, 1)
        
        max_label = QLabel("Max:")
        max_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        frame_length_layout.addWidget(max_label, 1, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        frame_length_layout.addWidget(self.frame_max, 1, 3)
        
        frame_length_group.setLayout(frame_length_layout)
        # Remove any extra margins from the group box
        frame_length_group.setContentsMargins(0, 0, 0, 0)
        self.protocol_tab_layout.addWidget(frame_length_group)
        
        # Connect frame_type change to enable/disable relevant fields
        def _apply_frame_type_ui_state():
            """Enable/disable frame size fields based on selected frame type."""
            frame_type = self.frame_type.currentText()
            if frame_type == "Fixed":
                # Fixed: Enable Fixed Size, disable Min/Max
                self.frame_size.setEnabled(True)
                self.frame_min.setEnabled(False)
                self.frame_max.setEnabled(False)
            elif frame_type == "Random":
                # Random: Enable Min/Max, disable Fixed Size
                self.frame_size.setEnabled(False)
                self.frame_min.setEnabled(True)
                self.frame_max.setEnabled(True)
            elif frame_type == "IMIX":
                # IMIX: Disable all (uses fixed distribution)
                self.frame_size.setEnabled(False)
                self.frame_min.setEnabled(False)
                self.frame_max.setEnabled(False)
        
        self.frame_type.currentTextChanged.connect(lambda _: _apply_frame_type_ui_state())
        # Initialize UI state
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, _apply_frame_type_ui_state)

        # Protocol Stack Sections - Improved layout with better spacing and alignment
        protocol_stack_group = QGroupBox("Protocol Stack")
        protocol_stack_layout = QGridLayout()
        protocol_stack_layout.setContentsMargins(8, 4, 8, 4)  # v0.3.11 compact
        protocol_stack_layout.setSpacing(4)
        protocol_stack_layout.setHorizontalSpacing(6)
        protocol_stack_layout.setVerticalSpacing(4)  # was 6 — tighter top/bottom rows
        # v0.3.11: column stretch was set up for 5 boxes per row
        # (back when L1 was a separate group). L1 is gone; top row
        # now has 4 boxes (VLAN | L2 | L4 | Payload), so use 4
        # equal-weight columns. The bottom row's 2 boxes (L3 +
        # Encap) just leave columns 2 and 3 visually empty — that's
        # fine; aligning the bottom-row boxes to the top-row column
        # widths keeps the grid visually coherent.
        for col in range(4):
            protocol_stack_layout.setColumnStretch(col, 1)

        # Helper function to create consistent group boxes
        def create_protocol_group(title, options, checked_index=0):
            group = QGroupBox(title)
            layout = QVBoxLayout()
            layout.setContentsMargins(6, 4, 6, 4)  # v0.3.11 compact — was 8/8
            layout.setSpacing(1)  # was 3 — tighter radio stack
            radio_buttons = []
            for i, option in enumerate(options):
                rb = QRadioButton(option)
                rb.setStyleSheet("font-size: 10px; padding: 1px;")  # Reduced padding
                if i == checked_index:
                    rb.setChecked(True)
                layout.addWidget(rb)
                radio_buttons.append(rb)
            group.setLayout(layout)
            group.setMinimumWidth(120)
            group.setMaximumWidth(180)
            return group, radio_buttons

        # v0.3.11 Protocol Stack cleanup:
        #   • L1 group ("None / MAC / RAW") was dead UI — the value
        #     was written to the stream dict + shown in the streams
        #     table but no packet builder ever read it. MAC/RAW are
        #     also L2 things, not L1. Removed the group entirely.
        #   • Payload "Random" was a dead radio — no encoder path
        #     ever produced random payload from it. Removed it from
        #     the list.
        #   • L4 group 2 (RoCEv2 / UEC) relabeled "Encap (over UDP)"
        #     so operators stop seeing two boxes both labeled "L4."
        # Backward compat: L1 still emitted as "None" in
        # get_stream_details so saved sessions / templates carrying
        # an L1 field keep their shape.

        # VLAN
        vlan_group, vlan_buttons = create_protocol_group("VLAN", ["Untagged", "Tagged", "Stacked"])
        self.vlan_untagged, self.vlan_tagged, self.vlan_stacked = vlan_buttons
        protocol_stack_layout.addWidget(vlan_group, 0, 0)

        # L2
        l2_group, l2_buttons = create_protocol_group("L2", ["None", "Ethernet II", "MPLS"])
        self.l2_none, self.l2_ethernet, self.l2_mpls = l2_buttons
        protocol_stack_layout.addWidget(l2_group, 0, 1)

        # L4 (transport: TCP / UDP / IP-protocol-style ICMP / IGMP)
        l4_group_1, l4_buttons_1 = create_protocol_group("L4", ["None", "ICMP", "IGMP", "TCP", "UDP"])
        self.l4_none_1, self.l4_icmp, self.l4_igmp, self.l4_tcp, self.l4_udp = l4_buttons_1
        protocol_stack_layout.addWidget(l4_group_1, 0, 2)

        # Payload (Random removed — no encoder ever produced random
        # bytes from it; kept None / Pattern / From File).
        payload_group, payload_buttons = create_protocol_group(
            "Payload", ["None", "Pattern", "From File"]
        )
        self.payload_none, self.payload_pattern, self.payload_from_file = payload_buttons
        self.payload_hex = self.payload_from_file  # Map From File to hex for compatibility
        protocol_stack_layout.addWidget(payload_group, 0, 3)

        # L3 (second row)
        l3_group, l3_buttons = create_protocol_group("L3", ["None", "ARP", "IPv4", "IPv6"])
        self.l3_none, self.l3_arp, self.l3_ipv4, self.l3_ipv6 = l3_buttons
        protocol_stack_layout.addWidget(l3_group, 1, 0)

        # Encap (over UDP) — was "L4" group 2, which read confusingly
        # as a second L4 picker. These options layer ON TOP of UDP
        # (RoCEv2 is UDP/4791, UEC is UDP/9999). Operator should
        # pick L4=UDP above THEN tick an encap here.
        l4_group_2, l4_buttons_2 = create_protocol_group(
            "Encap (over UDP)", ["None", "RoCEv2", "UEC"],
        )
        self.l4_none_2, self.l4_rocev2, self.l4_uec = l4_buttons_2
        protocol_stack_layout.addWidget(l4_group_2, 1, 1)

        protocol_stack_group.setLayout(protocol_stack_layout)
        self.protocol_tab_layout.addWidget(protocol_stack_group)
        # v0.3.11 compact: ABSORB extra dialog height into a bottom
        # spacer instead of letting QVBoxLayout stretch the three
        # groupboxes (Basics / Frame Length / Protocol Stack) to
        # fill it. Without this stretch, the Frame Length section
        # ended up 147 px tall to fit ~60 px of content because
        # QVBoxLayout splits leftover height across all expanding
        # widgets. The old comment ("Don't add stretch to prevent
        # content from being cut off") was wrong — content is in a
        # scroll area; the stretch absorbs OVERFLOW, doesn't clip.
        self.protocol_tab_layout.addStretch(1)

        # VLAN Toggle section
        for rb in [self.vlan_untagged, self.vlan_tagged, self.vlan_stacked]:
            rb.toggled.connect(self.refresh_vlan_section)

        QTimer.singleShot(0, self.refresh_vlan_section)  # initial sync

        # L3 Toggle section
        for rb in [self.l3_none, self.l3_arp, self.l3_ipv4, self.l3_ipv6]:
            rb.toggled.connect(self.refresh_l3_sections)
        QTimer.singleShot(0, self.refresh_l3_sections)

        # L4 toggle section - connect both L4 groups
        # Create a unified l4_none reference for backward compatibility
        self.l4_none = self.l4_none_1  # Use first L4 None as primary
        for rb in (self.l4_none_1, self.l4_icmp, self.l4_igmp, self.l4_tcp, self.l4_udp):
            rb.toggled.connect(self.refresh_l4_sections)
        for rb in (self.l4_none_2, self.l4_rocev2, self.l4_uec):
            rb.toggled.connect(self.refresh_l4_sections)
        QTimer.singleShot(0, self.refresh_l4_sections)

    def _create_scrollable_tab(self, section_group, tab_name):
        """Helper method to create a scrollable tab with a section group."""
        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Create content widget - remove width constraints to fill available space
        content_widget = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(8)
        
        # Set size policy for the group box to expand and fill available width
        section_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        
        # Add the section group
        content_layout.addWidget(section_group)
        content_layout.addStretch()
        
        content_widget.setLayout(content_layout)
        scroll_area.setWidget(content_widget)
        
        return scroll_area
    
    def setup_protocol_data_tab(self):
        self.protocol_data_layout.setSpacing(8)
        self.protocol_data_layout.setContentsMargins(10, 10, 10, 10)
        
        # Create a tab widget for sub-tabs under Protocol Data
        self.protocol_data_tabs = QTabWidget()
        self.protocol_data_tabs.setTabPosition(QTabWidget.North)
        # Enable scrolling for tabs when they overflow
        self.protocol_data_tabs.setUsesScrollButtons(True)
        # Set tab bar to wrap or use elide mode
        tab_bar = self.protocol_data_tabs.tabBar()
        tab_bar.setElideMode(Qt.ElideRight)  # Elide text if too long
        tab_bar.setExpanding(False)  # Don't expand tabs to fill space
        
        # Create individual scrollable tabs for each section
        # MAC Tab
        self.add_mac_section()
        mac_scroll = self._create_scrollable_tab(self.mac_group, "MAC")
        self.protocol_data_tabs.addTab(mac_scroll, "MAC")
        
        # ARP Tab
        self.add_arp_section()
        arp_scroll = self._create_scrollable_tab(self.arp_group, "ARP")
        self.protocol_data_tabs.addTab(arp_scroll, "ARP")
        
        # VLAN Tab
        self.add_vlan_section()
        vlan_scroll = self._create_scrollable_tab(self.vlan_group, "VLAN")
        self.protocol_data_tabs.addTab(vlan_scroll, "VLAN")
        
        # IPv4 Tab
        self.add_ipv4_section()
        ipv4_scroll = self._create_scrollable_tab(self.ipv4_group, "IPv4")
        self.protocol_data_tabs.addTab(ipv4_scroll, "IPv4")
        
        # IPv6 Tab
        self.add_ipv6_section()
        ipv6_scroll = self._create_scrollable_tab(self.ipv6_group, "IPv6")
        self.protocol_data_tabs.addTab(ipv6_scroll, "IPv6")
        
        # TCP Tab
        self.add_tcp_section()
        tcp_scroll = self._create_scrollable_tab(self.tcp_group, "TCP")
        self.protocol_data_tabs.addTab(tcp_scroll, "TCP")
        
        # UDP Tab
        self.add_udp_section()
        udp_scroll = self._create_scrollable_tab(self.udp_group, "UDP")
        self.protocol_data_tabs.addTab(udp_scroll, "UDP")
        
        # MPLS Tab
        self.add_mpls_section()
        mpls_scroll = self._create_scrollable_tab(self.mpls_group, "MPLS")
        self.protocol_data_tabs.addTab(mpls_scroll, "MPLS")
        
        # Payload Tab
        self.add_payload_data_section()
        payload_scroll = self._create_scrollable_tab(self.payload_group, "Payload")
        self.protocol_data_tabs.addTab(payload_scroll, "Payload")
        
        # RoCEv2 Tab
        self.add_rocev2_section()
        rocev2_scroll = self._create_scrollable_tab(self.rocev2_group, "RoCEv2")
        self.protocol_data_tabs.addTab(rocev2_scroll, "RoCEv2")
        
        # UEC Tab
        self.add_uec_section()
        uec_scroll = self._create_scrollable_tab(self.uec_group, "UEC")
        self.protocol_data_tabs.addTab(uec_scroll, "UEC")
        
        # Apply compact styling to protocol data tabs to prevent overflow
        self.protocol_data_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: #ffffff;
            }
            QTabBar::tab {
                background-color: #f3f4f6;
                color: #4b5563;
                border: 1px solid #d1d5db;
                border-bottom: none;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
                padding: 3px 6px;
                margin-right: 1px;
                font-weight: 500;
                font-size: 9px;
                min-width: 45px;
                max-width: 65px;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #1f2937;
                border-bottom: 2px solid #3b82f6;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: #e5e7eb;
                color: #374151;
            }
            QTabBar::scroller {
                width: 20px;
            }
        """)
        
        # Add the tab widget to the main protocol data layout
        self.protocol_data_layout.addWidget(self.protocol_data_tabs)

    # ----------------------------- RX ports -----------------------------

    def populate_rx_ports(self, tx_port_name):
        try:
            if not hasattr(self, 'rx_port_dropdown'):
                return

            self.rx_port_dropdown.clear()
            self.rx_port_dropdown.addItem("Same as TX Port")

            tx_clean = tx_port_name.split(" - Port:")[-1].strip()

            full_rx_ports = []
            for server in self.server_interfaces:
                tg_id = server.get("tg_id", "0")
                ports = server.get("ports", [])
                for port in ports:
                    port_clean = port.split(" - Port:")[-1].strip()
                    if port_clean != tx_clean:
                        full_rx_ports.append(f"TG {tg_id} - Port: {port_clean}")

            for label in sorted(full_rx_ports):
                self.rx_port_dropdown.addItem(label)

            # Preselect existing
            if self.stream_data:
                rx_port = self.stream_data.get("rx_port")
                if rx_port:
                    idx = self.rx_port_dropdown.findText(rx_port)
                    if idx != -1:
                        self.rx_port_dropdown.setCurrentIndex(idx)
        except Exception as e:
            logger.error("populate_rx_ports: %s", e)

    # ----------------------------- MAC / VLAN / IPv4 / IPv6 / TCP / UDP / MPLS / Payload -----------------------------

    def toggle_mac_fields(self, mode, count_field, step_field):
        if mode == "Fixed":
            count_field.setText("1")
            step_field.setText("1")
            count_field.setDisabled(True)
            step_field.setDisabled(True)
        else:
            count_field.setEnabled(True)
            step_field.setEnabled(True)

    def add_mac_section(self):
        mac_group = QGroupBox("MAC (Media Access Protocol)")
        mac_layout = QGridLayout()
        mac_layout.setSpacing(8)
        # Make columns expand to fill available space
        mac_layout.setColumnStretch(0, 0)  # Label column - fixed
        mac_layout.setColumnStretch(1, 0)  # Mode dropdown - fixed
        mac_layout.setColumnStretch(2, 2)  # Address field - expand more
        mac_layout.setColumnStretch(3, 0)  # Count label - fixed
        mac_layout.setColumnStretch(4, 0)  # Count field - fixed
        mac_layout.setColumnStretch(5, 0)  # Step label - fixed
        mac_layout.setColumnStretch(6, 0)  # Step field - fixed
        mac_layout.setColumnMinimumWidth(2, 140)  # Minimum width for address field

        # Destination - reorganize to fit better
        mac_layout.addWidget(QLabel("Destination:"), 0, 0)
        self.mac_destination_mode = QComboBox()
        self.mac_destination_mode.addItems(["Fixed", "Increment", "Decrement"])
        self.mac_destination_mode.setMinimumWidth(90)
        self.mac_destination_mode.setMaximumWidth(120)
        self.mac_destination_address = QLineEdit("00:00:00:00:00:00")
        self.mac_destination_address.setMinimumWidth(140)
        self.mac_destination_count = QLineEdit("16")
        self.mac_destination_count.setValidator(QIntValidator(1, 1_000_000))  # Allow up to 1 million MAC addresses
        self.mac_destination_count.setMinimumWidth(50)
        self.mac_destination_count.setMaximumWidth(80)
        self.mac_destination_step = QLineEdit("1")
        self.mac_destination_step.setValidator(QIntValidator(1, 16777215))  # v0.3.11: must be >= 1
        self.mac_destination_step.setMinimumWidth(50)
        self.mac_destination_step.setMaximumWidth(80)
        mac_layout.addWidget(self.mac_destination_mode, 0, 1)
        mac_layout.addWidget(self.mac_destination_address, 0, 2)
        mac_layout.addWidget(QLabel("Count:"), 0, 3)
        mac_layout.addWidget(self.mac_destination_count, 0, 4)
        mac_layout.addWidget(QLabel("Step:"), 0, 5)
        mac_layout.addWidget(self.mac_destination_step, 0, 6)
        self.mac_destination_mode.currentTextChanged.connect(
            lambda mode: self.toggle_mac_fields(mode, self.mac_destination_count, self.mac_destination_step)
        )

        # Source - reorganize to fit better
        mac_layout.addWidget(QLabel("Source:"), 1, 0)
        self.mac_source_mode = QComboBox()
        self.mac_source_mode.addItems(["Fixed", "Increment", "Decrement", "Resolve"])
        self.mac_source_mode.setMinimumWidth(90)
        self.mac_source_mode.setMaximumWidth(120)
        self.mac_source_address = QLineEdit("00:00:00:00:00:00")
        self.mac_source_address.setMinimumWidth(140)
        self.mac_source_count = QLineEdit("16")
        self.mac_source_count.setValidator(QIntValidator(1, 1_000_000))  # Allow up to 1 million MAC addresses
        self.mac_source_count.setMinimumWidth(50)
        self.mac_source_count.setMaximumWidth(80)
        self.mac_source_step = QLineEdit("1")
        self.mac_source_step.setValidator(QIntValidator(1, 16777215))  # v0.3.11: must be >= 1
        self.mac_source_step.setMinimumWidth(50)
        self.mac_source_step.setMaximumWidth(80)
        mac_layout.addWidget(self.mac_source_mode, 1, 1)
        mac_layout.addWidget(self.mac_source_address, 1, 2)
        mac_layout.addWidget(QLabel("Count:"), 1, 3)
        mac_layout.addWidget(self.mac_source_count, 1, 4)
        mac_layout.addWidget(QLabel("Step:"), 1, 5)
        mac_layout.addWidget(self.mac_source_step, 1, 6)
        self.mac_source_mode.currentTextChanged.connect(
            lambda mode: self.toggle_mac_fields(mode, self.mac_source_count, self.mac_source_step)
        )

        # Info
        mac_info_label = QLabel(
            "To use MAC resolution, configure a corresponding device on the port with matching VLAN and IP."
        )
        mac_info_label.setWordWrap(True)
        mac_layout.addWidget(mac_info_label, 2, 0, 1, 7)

        mac_group.setLayout(mac_layout)
        # Store reference for grid layout organization
        self.mac_group = mac_group

    def add_arp_section(self):
        """Add ARP (L2.5) configuration group."""
        self.arp_group = QGroupBox("ARP")
        arp_layout = QGridLayout()
        arp_layout.setSpacing(8)
        # Make columns expand to fill available space
        arp_layout.setColumnStretch(0, 0)  # Label column - fixed
        arp_layout.setColumnStretch(1, 2)  # MAC/IP fields - expand more
        arp_layout.setColumnStretch(2, 0)  # Label column - fixed
        arp_layout.setColumnStretch(3, 2)  # IP fields - expand more
        arp_layout.setColumnMinimumWidth(1, 140)
        arp_layout.setColumnMinimumWidth(3, 110)

        # Operation: Request/Reply
        arp_layout.addWidget(QLabel("Operation:"), 0, 0)
        self.arp_operation = QComboBox()
        self.arp_operation.addItems(["Request", "Reply"])
        self.arp_operation.setMinimumWidth(100)
        self.arp_operation.setMaximumWidth(120)
        arp_layout.addWidget(self.arp_operation, 0, 1)

        # Sender MAC / IP
        arp_layout.addWidget(QLabel("Sender MAC:"), 1, 0)
        self.arp_sender_mac = QLineEdit("00:11:22:33:44:55")
        self.arp_sender_mac.setMinimumWidth(140)
        arp_layout.addWidget(self.arp_sender_mac, 1, 1)

        arp_layout.addWidget(QLabel("Sender IP (IPv4):"), 1, 2)
        self.arp_sender_ip = QLineEdit("0.0.0.0")
        self.arp_sender_ip.setMinimumWidth(110)
        self.arp_sender_ip.setMaximumWidth(180)
        arp_layout.addWidget(self.arp_sender_ip, 1, 3)

        # Target MAC / IP
        arp_layout.addWidget(QLabel("Target MAC:"), 2, 0)
        self.arp_target_mac = QLineEdit("ff:ff:ff:ff:ff:ff")
        self.arp_target_mac.setMinimumWidth(140)
        arp_layout.addWidget(self.arp_target_mac, 2, 1)

        arp_layout.addWidget(QLabel("Target IP (IPv4):"), 2, 2)
        self.arp_target_ip = QLineEdit("0.0.0.0")
        self.arp_target_ip.setMinimumWidth(110)
        self.arp_target_ip.setMaximumWidth(180)
        arp_layout.addWidget(self.arp_target_ip, 2, 3)

        # (Optional) Add validators for MAC/IPv4 here

        self.arp_group.setLayout(arp_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        # Initial enabled state
        try:
            self.arp_group.setEnabled(self.l3_arp.isChecked())
        except Exception:
            pass
    def add_mpls_section(self):
        mpls_group = QGroupBox("MPLS")
        mpls_layout = QGridLayout()
        mpls_layout.setContentsMargins(12, 12, 12, 12)
        mpls_layout.setSpacing(8)
        # Make columns expand to fill available space
        for col in range(6):
            if col % 2 == 0:  # Label columns
                mpls_layout.setColumnStretch(col, 0)
            else:  # Input columns
                mpls_layout.setColumnStretch(col, 1)

        self.mpls_label_field = QLineEdit("16")
        self.mpls_label_field.setValidator(QIntValidator(0, 1_048_575))
        self.mpls_ttl_field = QLineEdit("64")
        self.mpls_ttl_field.setValidator(QIntValidator(0, 255))
        self.mpls_experimental_field = QLineEdit("0")
        self.mpls_experimental_field.setValidator(QIntValidator(0, 7))

        mpls_layout.addWidget(QLabel("Label:"), 0, 0)
        mpls_layout.addWidget(self.mpls_label_field, 0, 1)
        mpls_layout.addWidget(QLabel("TTL:"), 0, 2)
        mpls_layout.addWidget(self.mpls_ttl_field, 0, 3)
        mpls_layout.addWidget(QLabel("Experimental:"), 0, 4)
        mpls_layout.addWidget(self.mpls_experimental_field, 0, 5)

        # Add minimum size constraints to MPLS fields (no max to allow expansion)
        self.mpls_label_field.setMinimumWidth(60)
        self.mpls_ttl_field.setMinimumWidth(60)
        self.mpls_experimental_field.setMinimumWidth(60)

        # SR-MPLS label-stack field (0.2.65). Comma-separated SIDs build
        # a multi-label stack with auto bottom-of-stack handling (see
        # utils/mpls.build_mpls_stack). When set, this OVERRIDES the
        # single Label field above — extract_mpls_labels in
        # utils/mpls.py prefers `mpls_labels` over `mpls_label`. TC and
        # TTL above apply uniformly to every label in the stack.
        self.mpls_labels_field = QLineEdit("")
        self.mpls_labels_field.setPlaceholderText(
            "(optional — comma-separated label stack, e.g. 16000, 16001, 16002)"
        )
        self.mpls_labels_field.setToolTip(
            "SR-MPLS label stack — comma-separated SIDs, top-of-stack "
            "first. When set, the Label field above is ignored and the "
            "full stack is emitted with ethertype 0x8847 and the bottom-"
            "of-stack bit set on the last label. Leave blank for a "
            "single-label MPLS frame using the Label field above."
        )
        self.mpls_labels_field.setMinimumWidth(220)
        mpls_layout.addWidget(QLabel("Label stack:"), 1, 0)
        mpls_layout.addWidget(self.mpls_labels_field, 1, 1, 1, 5)

        mpls_group.setLayout(mpls_layout)
        # Two-row layout now; the old 70px cap clipped the second row.
        mpls_group.setMaximumHeight(110)
        self.mpls_group = mpls_group

    def add_vlan_section(self):
        """Adds the VLAN section to the Protocol Data tab and wires enable/disable."""
        self.vlan_group = QGroupBox("VLAN")
        vlan_layout = QGridLayout()
        vlan_layout.setSpacing(8)
        # Make columns expand to fill available space
        for col in range(8):
            if col % 2 == 0:  # Label columns
                vlan_layout.setColumnStretch(col, 0)
            else:  # Input columns
                vlan_layout.setColumnStretch(col, 1)
        vlan_layout.setColumnMinimumWidth(1, 60)
        vlan_layout.setColumnMinimumWidth(3, 60)
        vlan_layout.setColumnMinimumWidth(5, 60)

        # VLAN ID, Priority, CFI/DEI, and Override TPID in the same row
        vlan_layout.addWidget(QLabel("VLAN ID:"), 0, 0)
        self.vlan_id_field = QLineEdit("10")
        self.vlan_id_field.setMinimumWidth(60)
        # v0.3.11: pin the 802.1Q VLAN ID range (1..4094). 0 and 4095
        # are reserved (priority-tagged-only and reserved respectively)
        # — server-side validation rejects them but the dialog used to
        # accept any string and lose the edit on reopen. Tooltip
        # documents the range so the validator's red border has context.
        self.vlan_id_field.setValidator(QIntValidator(1, 4094))
        self.vlan_id_field.setToolTip(
            "802.1Q VLAN ID. Range 1..4094. "
            "0 and 4095 are reserved per IEEE 802.1Q."
        )
        vlan_layout.addWidget(self.vlan_id_field, 0, 1)

        vlan_layout.addWidget(QLabel("Priority:"), 0, 2)
        self.priority_field = QComboBox()
        self.priority_field.addItems([str(i) for i in range(8)])
        self.priority_field.setMinimumWidth(60)
        vlan_layout.addWidget(self.priority_field, 0, 3)

        vlan_layout.addWidget(QLabel("CFI/DEI:"), 0, 4)
        self.cfi_dei_field = QComboBox()
        self.cfi_dei_field.addItems(["0", "1"])
        self.cfi_dei_field.setMinimumWidth(60)
        vlan_layout.addWidget(self.cfi_dei_field, 0, 5)

        self.override_tpid_checkbox = QCheckBox("Override TPID")
        vlan_layout.addWidget(self.override_tpid_checkbox, 0, 6)

        self.tpid_field = QLineEdit("81 00")
        self.tpid_field.setDisabled(True)
        self.tpid_field.setMinimumWidth(60)
        vlan_layout.addWidget(self.tpid_field, 0, 7)

        # Connect checkbox to enable/disable TPID field
        self.override_tpid_checkbox.toggled.connect(self.tpid_field.setEnabled)

        # Increment VLAN Option
        self.vlan_increment_checkbox = QCheckBox("Increment VLAN")
        vlan_layout.addWidget(self.vlan_increment_checkbox, 1, 0)

        self.vlan_increment_value = QLineEdit("1")
        self.vlan_increment_value.setValidator(QIntValidator(1, 4094))
        self.vlan_increment_value.setDisabled(True)
        vlan_layout.addWidget(QLabel("Increment Value"), 1, 1)
        vlan_layout.addWidget(self.vlan_increment_value, 1, 2)

        self.vlan_increment_count = QLineEdit("1")
        self.vlan_increment_count.setValidator(QIntValidator(1, 4094))
        self.vlan_increment_count.setDisabled(True)
        vlan_layout.addWidget(QLabel("Increment Count"), 1, 3)
        vlan_layout.addWidget(self.vlan_increment_count, 1, 4)

        # Enable increment fields only when the checkbox is checked
        self.vlan_increment_checkbox.toggled.connect(
            lambda checked: (
                self.vlan_increment_value.setEnabled(checked),
                self.vlan_increment_count.setEnabled(checked),
            )
        )

        self.vlan_group.setLayout(vlan_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        # Initial enabled state (enabled only if Tagged or Stacked)
        try:
            self.vlan_group.setEnabled(self.vlan_tagged.isChecked() or self.vlan_stacked.isChecked())
        except Exception:
            self.vlan_group.setEnabled(False)

    def refresh_vlan_section(self):
        """Enable VLAN config only when VLAN selection is Tagged or Stacked."""
        enabled = False
        try:
            enabled = self.vlan_tagged.isChecked() or self.vlan_stacked.isChecked()
        except Exception:
            pass
        if hasattr(self, "vlan_group"):
            self.vlan_group.setEnabled(bool(enabled))

    def add_ipv4_section(self):
        """Adds the IPv4 section to the Protocol Data tab."""
        self.ipv4_group = QGroupBox("Internet Protocol ver 4")
        ipv4_layout = QGridLayout()
        ipv4_layout.setSpacing(8)
        ipv4_layout.setColumnStretch(1, 1)  # Allow IP field to expand
        ipv4_layout.setColumnMinimumWidth(1, 110)  # Minimum width for IP field

        # Source IP
        ipv4_layout.addWidget(QLabel("Source IP:"), 0, 0)
        self.source_field = QLineEdit("0.0.0.0")
        self.source_field.setMinimumWidth(110)
        ipv4_layout.addWidget(self.source_field, 0, 1)

        self.source_mode_dropdown = QComboBox()
        self.source_mode_dropdown.addItems(["Fixed", "Increment"])
        self.source_mode_dropdown.setMinimumWidth(90)
        self.source_mode_dropdown.setMaximumWidth(110)
        ipv4_layout.addWidget(self.source_mode_dropdown, 0, 2)

        self.source_increment_step = QLineEdit("1")
        self.source_increment_step.setValidator(QIntValidator(1, 255))
        self.source_increment_step.setMinimumWidth(50)
        self.source_increment_step.setMaximumWidth(70)
        ipv4_layout.addWidget(QLabel("Step:"), 0, 3)
        ipv4_layout.addWidget(self.source_increment_step, 0, 4)

        self.source_increment_count = QLineEdit("1")
        self.source_increment_count.setValidator(QIntValidator(1, 255))
        self.source_increment_count.setMinimumWidth(50)
        self.source_increment_count.setMaximumWidth(70)
        ipv4_layout.addWidget(QLabel("Count:"), 0, 5)
        ipv4_layout.addWidget(self.source_increment_count, 0, 6)

        self.source_mode_dropdown.currentIndexChanged.connect(
            lambda idx: (
                self.source_increment_step.setEnabled(idx == 1),
                self.source_increment_count.setEnabled(idx == 1)
            )
        )

        # Destination IP
        ipv4_layout.addWidget(QLabel("Destination IP:"), 1, 0)
        self.destination_field = QLineEdit("0.0.0.0")
        self.destination_field.setMinimumWidth(110)
        ipv4_layout.addWidget(self.destination_field, 1, 1)

        self.destination_mode_dropdown = QComboBox()
        self.destination_mode_dropdown.addItems(["Fixed", "Increment"])
        self.destination_mode_dropdown.setMinimumWidth(90)
        self.destination_mode_dropdown.setMaximumWidth(110)
        ipv4_layout.addWidget(self.destination_mode_dropdown, 1, 2)

        self.destination_increment_step = QLineEdit("1")
        self.destination_increment_step.setValidator(QIntValidator(1, 255))
        self.destination_increment_step.setMinimumWidth(50)
        self.destination_increment_step.setMaximumWidth(70)
        ipv4_layout.addWidget(QLabel("Step:"), 1, 3)
        ipv4_layout.addWidget(self.destination_increment_step, 1, 4)

        self.destination_increment_count = QLineEdit("1")
        self.destination_increment_count.setValidator(QIntValidator(1, 255))
        self.destination_increment_count.setMinimumWidth(50)
        self.destination_increment_count.setMaximumWidth(70)
        ipv4_layout.addWidget(QLabel("Count:"), 1, 5)
        ipv4_layout.addWidget(self.destination_increment_count, 1, 6)

        self.destination_mode_dropdown.currentIndexChanged.connect(
            lambda idx: (
                self.destination_increment_step.setEnabled(idx == 1),
                self.destination_increment_count.setEnabled(idx == 1)
            )
        )

        # Misc
        ipv4_layout.addWidget(QLabel("TTL"), 2, 0)
        self.ttl_field = QLineEdit("64")
        self.ttl_field.setValidator(QIntValidator(1, 255))
        ipv4_layout.addWidget(self.ttl_field, 2, 1)

        self.df_checkbox = QCheckBox("Don't Fragment (DF)")
        ipv4_layout.addWidget(self.df_checkbox, 2, 2)

        self.mf_checkbox = QCheckBox("More Fragments (MF)")
        ipv4_layout.addWidget(self.mf_checkbox, 2, 3)

        ipv4_layout.addWidget(QLabel("Fragment Offset"), 2, 4)
        self.fragment_offset_field = QLineEdit("0")
        self.fragment_offset_field.setValidator(QIntValidator(0, 8191))
        ipv4_layout.addWidget(self.fragment_offset_field, 2, 5)

        ipv4_layout.addWidget(QLabel("Identification"), 2, 6)
        self.identification_field = QLineEdit("0000")
        self.identification_field.setValidator(QIntValidator(0, 65535))
        ipv4_layout.addWidget(self.identification_field, 2, 7)

        # ToS / DSCP / Custom
        tos_label = QLabel("ToS/DSCP Mode")
        tos_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        ipv4_layout.addWidget(tos_label, 3, 0)

        self.tos_dscp_custom_mode = QComboBox()
        self.tos_dscp_custom_mode.addItems(["TOS", "DSCP", "Custom"])
        self.tos_dscp_custom_mode.setFixedWidth(100)
        ipv4_layout.addWidget(self.tos_dscp_custom_mode, 3, 1)

        self.tos_dscp_custom_stack = QStackedWidget()
        ipv4_layout.addWidget(self.tos_dscp_custom_stack, 3, 2, 1, 3)

        ecn_label = QLabel("ECN")
        ecn_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        ipv4_layout.addWidget(ecn_label, 3, 5)

        self.ecn_dropdown = QComboBox()
        self.ecn_dropdown.addItems(["CE", "Not-ECT", "ECT(1)", "ECT(0)"])
        self.ecn_dropdown.setFixedWidth(100)
        ipv4_layout.addWidget(self.ecn_dropdown, 3, 6)

        # TOS widget
        tos_widget = QWidget()
        tos_layout = QHBoxLayout(tos_widget)
        tos_layout.setContentsMargins(0, 0, 0, 0)
        self.tos_dropdown = QComboBox()
        self.tos_dropdown.addItems([
            "Routine", "Priority", "Immediate", "Flash", "Flash Override",
            "Critical", "Internetwork Control", "Network Control"
        ])
        self.tos_dropdown.setFixedWidth(150)
        tos_layout.addWidget(self.tos_dropdown)

        # DSCP widget
        dscp_widget = QWidget()
        dscp_layout = QHBoxLayout(dscp_widget)
        dscp_layout.setContentsMargins(0, 0, 0, 0)
        self.dscp_dropdown = QComboBox()
        self.dscp_dropdown.addItems([
            "cs0", "cs1", "cs2", "cs3", "cs4", "cs5", "cs6", "cs7",
            "af11", "af12", "af13", "af21", "af22", "af23",
            "af31", "af32", "af33", "af41", "af42", "af43", "ef"
        ])
        self.dscp_dropdown.setFixedWidth(150)
        dscp_layout.addWidget(self.dscp_dropdown)

        # Custom widget
        custom_widget = QWidget()
        custom_layout = QHBoxLayout(custom_widget)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_tos_field = QLineEdit("")
        self.custom_tos_field.setPlaceholderText("Custom ToS (0-255)")
        self.custom_tos_field.setValidator(QIntValidator(0, 255))
        self.custom_tos_field.setFixedWidth(150)
        custom_layout.addWidget(self.custom_tos_field)

        self.tos_dscp_custom_stack.addWidget(tos_widget)  # index 0
        self.tos_dscp_custom_stack.addWidget(dscp_widget)  # index 1
        self.tos_dscp_custom_stack.addWidget(custom_widget)  # index 2

        self.tos_dscp_custom_mode.currentIndexChanged.connect(
            lambda idx: self.tos_dscp_custom_stack.setCurrentIndex(idx)
        )

        # Assemble group
        self.ipv4_group.setLayout(ipv4_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        # Initial enable/disable of increment fields (Fixed by default)
        self.source_increment_step.setEnabled(False)
        self.source_increment_count.setEnabled(False)
        self.destination_increment_step.setEnabled(False)
        self.destination_increment_count.setEnabled(False)

        # Initial enabled state of the whole IPv4 group per L3 radios
        try:
            self.ipv4_group.setEnabled(self.l3_ipv4.isChecked())
        except Exception:
            self.ipv4_group.setEnabled(False)

    def add_ipv6_section(self):
        """Adds the IPv6 section to the Protocol Data tab."""
        self.ipv6_group = QGroupBox("IPv6")
        ipv6_layout = QGridLayout()

        # Source Address + mode
        ipv6_layout.addWidget(QLabel("Source Address"), 0, 0)
        self.ipv6_source_field = QLineEdit("2001:db8::1")
        ipv6_layout.addWidget(self.ipv6_source_field, 0, 1)

        ipv6_layout.addWidget(QLabel("Source Mode"), 0, 2)
        self.ipv6_source_mode_dropdown = QComboBox()
        self.ipv6_source_mode_dropdown.addItems(["Fixed", "Increment"])
        ipv6_layout.addWidget(self.ipv6_source_mode_dropdown, 0, 3)

        ipv6_layout.addWidget(QLabel("Source Step"), 0, 4)
        self.ipv6_source_increment_step = QLineEdit("1")
        self.ipv6_source_increment_step.setDisabled(True)
        ipv6_layout.addWidget(self.ipv6_source_increment_step, 0, 5)

        ipv6_layout.addWidget(QLabel("Source Count"), 0, 6)
        self.ipv6_source_increment_count = QLineEdit("1")
        self.ipv6_source_increment_count.setDisabled(True)
        ipv6_layout.addWidget(self.ipv6_source_increment_count, 0, 7)

        # Destination Address + mode
        ipv6_layout.addWidget(QLabel("Destination Address"), 1, 0)
        self.ipv6_destination_field = QLineEdit("2001:db8::2")
        ipv6_layout.addWidget(self.ipv6_destination_field, 1, 1)

        ipv6_layout.addWidget(QLabel("Destination Mode"), 1, 2)
        self.ipv6_destination_mode_dropdown = QComboBox()
        self.ipv6_destination_mode_dropdown.addItems(["Fixed", "Increment"])
        ipv6_layout.addWidget(self.ipv6_destination_mode_dropdown, 1, 3)

        ipv6_layout.addWidget(QLabel("Destination Step"), 1, 4)
        self.ipv6_destination_increment_step = QLineEdit("1")
        self.ipv6_destination_increment_step.setDisabled(True)
        ipv6_layout.addWidget(self.ipv6_destination_increment_step, 1, 5)

        ipv6_layout.addWidget(QLabel("Destination Count"), 1, 6)
        self.ipv6_destination_increment_count = QLineEdit("1")
        self.ipv6_destination_increment_count.setDisabled(True)
        ipv6_layout.addWidget(self.ipv6_destination_increment_count, 1, 7)

        # Misc
        ipv6_layout.addWidget(QLabel("Traffic Class"), 2, 0)
        self.ipv6_traffic_class_field = QLineEdit("0")
        self.ipv6_traffic_class_field.setValidator(QIntValidator(0, 255))
        ipv6_layout.addWidget(self.ipv6_traffic_class_field, 2, 1)

        ipv6_layout.addWidget(QLabel("Flow Label"), 2, 2)
        self.ipv6_flow_label_field = QLineEdit("0")
        self.ipv6_flow_label_field.setValidator(QIntValidator(0, 1_048_575))
        ipv6_layout.addWidget(self.ipv6_flow_label_field, 2, 3)

        ipv6_layout.addWidget(QLabel("Hop Limit"), 2, 4)
        self.ipv6_hop_limit_field = QLineEdit("64")
        self.ipv6_hop_limit_field.setValidator(QIntValidator(0, 255))
        ipv6_layout.addWidget(self.ipv6_hop_limit_field, 2, 5)

        # Mode toggles for increments
        self.ipv6_source_mode_dropdown.currentTextChanged.connect(
            lambda mode: self.update_increment_fields(
                mode, self.ipv6_source_increment_step, self.ipv6_source_increment_count
            )
        )
        self.ipv6_destination_mode_dropdown.currentTextChanged.connect(
            lambda mode: self.update_increment_fields(
                mode, self.ipv6_destination_increment_step, self.ipv6_destination_increment_count
            )
        )

        # Assemble group
        self.ipv6_group.setLayout(ipv6_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        # Initial enabled state of the whole IPv6 group per L3 radios
        try:
            self.ipv6_group.setEnabled(self.l3_ipv6.isChecked())
        except Exception:
            self.ipv6_group.setEnabled(False)

    def refresh_l3_sections(self):
        ipv4_on = hasattr(self, "l3_ipv4") and self.l3_ipv4.isChecked()
        ipv6_on = hasattr(self, "l3_ipv6") and self.l3_ipv6.isChecked()
        arp_on = hasattr(self, "l3_arp") and self.l3_arp.isChecked()

        if hasattr(self, "ipv4_group"):
            self.ipv4_group.setEnabled(ipv4_on)
        if hasattr(self, "ipv6_group"):
            self.ipv6_group.setEnabled(ipv6_on)
        if hasattr(self, "arp_group"):
            self.arp_group.setEnabled(arp_on)

        # v0.3.11: when L3 leaves IPv4/IPv6, snap the scale-mode
        # dropdowns back to "Fixed". Without this, switching L3
        # IPv4 → None → IPv4 left the source/destination_mode in
        # "Increment" (disabled while away) — confusing on re-enable
        # because the operator saw "Increment" but the step/count
        # fields were empty / inconsistent with the dropdown state.
        if not ipv4_on:
            for _attr in ("source_mode_dropdown", "destination_mode_dropdown"):
                if hasattr(self, _attr):
                    getattr(self, _attr).setCurrentIndex(0)  # "Fixed"
        if not ipv6_on:
            for _attr in ("ipv6_source_mode_dropdown",
                          "ipv6_destination_mode_dropdown"):
                if hasattr(self, _attr):
                    getattr(self, _attr).setCurrentIndex(0)  # "Fixed"

    def update_increment_fields(self, mode, step_field, count_field):
        is_increment = mode == "Increment"
        step_field.setEnabled(is_increment)
        count_field.setEnabled(is_increment)

    def add_tcp_section(self):
        def validate_u32(field):
            try:
                v = int(field.text())
                if not (0 <= v <= 0xFFFFFFFF):
                    raise ValueError
            except ValueError:
                field.setText("0")

        self.tcp_group = QGroupBox("Transmission Control Protocol (stateless)")
        tcp_layout = QGridLayout()

        # Src port override + increment
        self.override_source_port_checkbox = QCheckBox("Override Source Port")
        tcp_layout.addWidget(self.override_source_port_checkbox, 0, 0)
        self.source_port_field = QLineEdit("0")
        self.source_port_field.setValidator(QIntValidator(0, 65535))
        self.source_port_field.setDisabled(True)
        tcp_layout.addWidget(self.source_port_field, 0, 1)
        # v0.3.11: ALSO clear the value when unchecking so the
        # stale port doesn't get serialized despite override=False.
        self.override_source_port_checkbox.toggled.connect(
            lambda c, f=self.source_port_field:
                (f.setEnabled(c), c or f.setText("0"))
        )

        self.increment_tcp_source_checkbox = QCheckBox("Increment Source Port")
        tcp_layout.addWidget(self.increment_tcp_source_checkbox, 0, 2)
        self.tcp_source_increment_step = QLineEdit("1")
        self.tcp_source_increment_step.setValidator(QIntValidator(1, 65535))
        self.tcp_source_increment_step.setDisabled(True)
        tcp_layout.addWidget(QLabel("Step"), 0, 3)
        tcp_layout.addWidget(self.tcp_source_increment_step, 0, 4)
        self.tcp_source_increment_count = QLineEdit("1")
        self.tcp_source_increment_count.setValidator(QIntValidator(1, 65535))
        self.tcp_source_increment_count.setDisabled(True)
        tcp_layout.addWidget(QLabel("Count"), 0, 5)
        tcp_layout.addWidget(self.tcp_source_increment_count, 0, 6)
        self.increment_tcp_source_checkbox.toggled.connect(
            lambda checked: [
                self.tcp_source_increment_step.setEnabled(checked),
                self.tcp_source_increment_count.setEnabled(checked),
            ]
        )

        # Dst port override + increment
        self.override_destination_port_checkbox = QCheckBox("Override Destination Port")
        tcp_layout.addWidget(self.override_destination_port_checkbox, 1, 0)
        self.destination_port_field = QLineEdit("0")
        self.destination_port_field.setValidator(QIntValidator(0, 65535))
        self.destination_port_field.setDisabled(True)
        tcp_layout.addWidget(self.destination_port_field, 1, 1)
        self.override_destination_port_checkbox.toggled.connect(
            lambda c, f=self.destination_port_field:
                (f.setEnabled(c), c or f.setText("0"))
        )

        self.increment_tcp_destination_checkbox = QCheckBox("Increment Destination Port")
        tcp_layout.addWidget(self.increment_tcp_destination_checkbox, 1, 2)
        self.tcp_destination_increment_step = QLineEdit("1")
        self.tcp_destination_increment_step.setValidator(QIntValidator(1, 65535))
        self.tcp_destination_increment_step.setDisabled(True)
        tcp_layout.addWidget(QLabel("Step"), 1, 3)
        tcp_layout.addWidget(self.tcp_destination_increment_step, 1, 4)
        self.tcp_destination_increment_count = QLineEdit("1")
        self.tcp_destination_increment_count.setValidator(QIntValidator(1, 65535))
        self.tcp_destination_increment_count.setDisabled(True)
        tcp_layout.addWidget(QLabel("Count"), 1, 5)
        tcp_layout.addWidget(self.tcp_destination_increment_count, 1, 6)
        self.increment_tcp_destination_checkbox.toggled.connect(
            lambda checked: [
                self.tcp_destination_increment_step.setEnabled(checked),
                self.tcp_destination_increment_count.setEnabled(checked),
            ]
        )

        # Seq/Ack/Window/Checksum
        tcp_layout.addWidget(QLabel("Seq No"), 2, 0)
        self.sequence_number_field = QLineEdit("129018")
        tcp_layout.addWidget(self.sequence_number_field, 2, 1)
        self.sequence_number_field.editingFinished.connect(lambda: validate_u32(self.sequence_number_field))

        tcp_layout.addWidget(QLabel("Ack No"), 2, 2)
        self.acknowledgement_number_field = QLineEdit("0")
        tcp_layout.addWidget(self.acknowledgement_number_field, 2, 3)
        self.acknowledgement_number_field.editingFinished.connect(lambda: validate_u32(self.acknowledgement_number_field))

        tcp_layout.addWidget(QLabel("Window"), 2, 4)
        self.window_field = QLineEdit("1024")
        self.window_field.setValidator(QIntValidator(1, 65535))
        tcp_layout.addWidget(self.window_field, 2, 5)

        self.override_checksum_checkbox = QCheckBox("Override Checksum")
        tcp_layout.addWidget(self.override_checksum_checkbox, 2, 6)
        self.tcp_checksum_field = QLineEdit("B3 E7")
        # v0.3.11: hex pairs ("00 00" through "FF FF"). Allow
        # optional whitespace between octets so operators can paste
        # captures verbatim.
        self.tcp_checksum_field.setValidator(
            QRegExpValidator(QRegExp(r"[0-9a-fA-F]{2}\s?[0-9a-fA-F]{2}"))
        )
        self.tcp_checksum_field.setDisabled(True)
        tcp_layout.addWidget(self.tcp_checksum_field, 2, 7)
        self.override_checksum_checkbox.toggled.connect(self.tcp_checksum_field.setEnabled)

        # Flags
        flags_group = QGroupBox("Flags")
        flags_layout = QGridLayout()
        self.flag_urg = QCheckBox("URG")
        self.flag_ack = QCheckBox("ACK")
        self.flag_psh = QCheckBox("PSH")
        self.flag_rst = QCheckBox("RST")
        self.flag_syn = QCheckBox("SYN")
        self.flag_fin = QCheckBox("FIN")
        for i, w in enumerate([self.flag_urg, self.flag_ack, self.flag_psh, self.flag_rst, self.flag_syn, self.flag_fin]):
            flags_layout.addWidget(w, i // 3, i % 3)
        flags_group.setLayout(flags_layout)
        tcp_layout.addWidget(flags_group, 4, 0, 1, 6)

        self.tcp_group.setLayout(tcp_layout)
        try:
            self.tcp_group.setEnabled(self.l4_tcp.isChecked())
        except AttributeError:
            self.tcp_group.setEnabled(False)
        # Widget will be added to grid layout in setup_protocol_data_tab

    def add_udp_section(self):
        self.udp_group = QGroupBox("User Datagram Protocol (stateless)")
        layout = QGridLayout()

        # Src override + increment
        self.override_udp_source_port_checkbox = QCheckBox("Override Source Port")
        layout.addWidget(self.override_udp_source_port_checkbox, 0, 0)
        self.udp_source_port_field = QLineEdit("0")
        self.udp_source_port_field.setValidator(QIntValidator(0, 65535))
        self.udp_source_port_field.setDisabled(True)
        layout.addWidget(self.udp_source_port_field, 0, 1)
        self.override_udp_source_port_checkbox.toggled.connect(
            lambda c, f=self.udp_source_port_field:
                (f.setEnabled(c), c or f.setText("0"))
        )

        self.udp_increment_source_checkbox = QCheckBox("Increment Source Port")
        layout.addWidget(self.udp_increment_source_checkbox, 0, 2)
        self.udp_source_increment_step = QLineEdit("1")
        self.udp_source_increment_step.setValidator(QIntValidator(1, 65535))
        self.udp_source_increment_step.setDisabled(True)
        layout.addWidget(QLabel("Step"), 0, 3)
        layout.addWidget(self.udp_source_increment_step, 0, 4)
        self.udp_source_increment_count = QLineEdit("1")
        self.udp_source_increment_count.setValidator(QIntValidator(1, 65535))
        self.udp_source_increment_count.setDisabled(True)
        layout.addWidget(QLabel("Count"), 0, 5)
        layout.addWidget(self.udp_source_increment_count, 0, 6)
        self.udp_increment_source_checkbox.toggled.connect(
            lambda checked: [
                self.udp_source_increment_step.setEnabled(checked),
                self.udp_source_increment_count.setEnabled(checked),
            ]
        )

        # Dst override + increment
        self.override_udp_destination_port_checkbox = QCheckBox("Override Destination Port")
        layout.addWidget(self.override_udp_destination_port_checkbox, 1, 0)
        self.udp_destination_port_field = QLineEdit("0")
        self.udp_destination_port_field.setValidator(QIntValidator(0, 65535))
        self.udp_destination_port_field.setDisabled(True)
        layout.addWidget(self.udp_destination_port_field, 1, 1)
        self.override_udp_destination_port_checkbox.toggled.connect(
            lambda c, f=self.udp_destination_port_field:
                (f.setEnabled(c), c or f.setText("0"))
        )

        self.udp_increment_destination_checkbox = QCheckBox("Increment Destination Port")
        layout.addWidget(self.udp_increment_destination_checkbox, 1, 2)
        self.udp_destination_increment_step = QLineEdit("1")
        self.udp_destination_increment_step.setValidator(QIntValidator(1, 65535))
        self.udp_destination_increment_step.setDisabled(True)
        layout.addWidget(QLabel("Step"), 1, 3)
        layout.addWidget(self.udp_destination_increment_step, 1, 4)
        self.udp_destination_increment_count = QLineEdit("1")
        self.udp_destination_increment_count.setValidator(QIntValidator(1, 65535))
        self.udp_destination_increment_count.setDisabled(True)
        layout.addWidget(QLabel("Count"), 1, 5)
        layout.addWidget(self.udp_destination_increment_count, 1, 6)
        self.udp_increment_destination_checkbox.toggled.connect(
            lambda checked: [
                self.udp_destination_increment_step.setEnabled(checked),
                self.udp_destination_increment_count.setEnabled(checked),
            ]
        )

        # Checksum
        self.override_udp_checksum_checkbox = QCheckBox("Override Checksum")
        layout.addWidget(self.override_udp_checksum_checkbox, 2, 0)
        self.udp_checksum_field = QLineEdit("")
        self.udp_checksum_field.setDisabled(True)
        layout.addWidget(self.udp_checksum_field, 2, 1)
        self.override_udp_checksum_checkbox.toggled.connect(self.udp_checksum_field.setEnabled)

        # Presets
        layout.addWidget(QLabel("Preset:"), 2, 2)
        self.udp_preset_combo = QComboBox()
        self.udp_preset_combo.addItems([
            "Custom",
            "BOOTP/DHCPv4 (client→server 68→67)",
            "BOOTP/DHCPv4 (server→client 67→68)",
            "DHCPv6 (546→547)",
            "DNS (53)",
            "TFTP (69)",
            "NTP (123)",
            "RADIUS Auth (1812)",
            "RADIUS Acct (1813)",
            "SIP (5060)",
            "VXLAN (4789)",
            "QUIC (443/UDP)",
            "Syslog (514)"
        ])
        layout.addWidget(self.udp_preset_combo, 2, 3, 1, 2)

        def apply_udp_preset(_):
            preset = self.udp_preset_combo.currentText()
            is_custom = preset.startswith("Custom")
            self.override_udp_source_port_checkbox.setChecked(not is_custom)
            self.override_udp_destination_port_checkbox.setChecked(not is_custom)

            mapping = {
                "BOOTP/DHCPv4 (client→server 68→67)": (68, 67),
                "BOOTP/DHCPv4 (server→client 67→68)": (67, 68),
                "DHCPv6 (546→547)": (546, 547),
                "DNS (53)": (0, 53),
                "TFTP (69)": (0, 69),
                "NTP (123)": (0, 123),
                "RADIUS Auth (1812)": (0, 1812),
                "RADIUS Acct (1813)": (0, 1813),
                "SIP (5060)": (0, 5060),
                "VXLAN (4789)": (0, 4789),
                "QUIC (443/UDP)": (0, 443),
                "Syslog (514)": (0, 514),
            }
            if preset in mapping:
                s, d = mapping[preset]
                if s == 0:
                    s = int(self.udp_source_port_field.text() or "0")
                self.udp_source_port_field.setText(str(s))
                self.udp_destination_port_field.setText(str(d))

            self.udp_bootp_enable_checkbox.setChecked(
                preset.startswith("BOOTP") or preset.startswith("DHCPv6")
            )

        self.udp_preset_combo.currentIndexChanged.connect(apply_udp_preset)

        # BOOTP/DHCP helper
        bootp_group = QGroupBox("BOOTP / DHCP Options (optional)")
        bootp_layout = QGridLayout()

        self.udp_bootp_enable_checkbox = QCheckBox("Enable BOOTP/DHCP template")
        bootp_layout.addWidget(self.udp_bootp_enable_checkbox, 0, 0, 1, 2)

        bootp_layout.addWidget(QLabel("Message Type"), 1, 0)
        self.bootp_msg_type = QComboBox()
        self.bootp_msg_type.addItems(
            ["DHCPDISCOVER", "DHCPOFFER", "DHCPREQUEST", "DHCPACK", "DHCPNAK", "BOOTREQUEST", "BOOTREPLY"])
        bootp_layout.addWidget(self.bootp_msg_type, 1, 1)

        bootp_layout.addWidget(QLabel("Transaction ID (hex)"), 1, 2)
        self.bootp_xid = QLineEdit("0x12345678")
        bootp_layout.addWidget(self.bootp_xid, 1, 3)

        bootp_layout.addWidget(QLabel("Client MAC"), 2, 0)
        self.bootp_client_mac = QLineEdit("00:11:22:33:44:55")
        bootp_layout.addWidget(self.bootp_client_mac, 2, 1)

        bootp_layout.addWidget(QLabel("Flags (hex)"), 2, 2)
        self.bootp_flags = QLineEdit("0x0000")
        bootp_layout.addWidget(self.bootp_flags, 2, 3)

        labels = ["ciaddr", "yiaddr", "siaddr", "giaddr"]
        defaults = ["0.0.0.0", "0.0.0.0", "0.0.0.0", "0.0.0.0"]
        self.bootp_addrs = {}
        row = 3
        for i, (lab, dflt) in enumerate(zip(labels, defaults)):
            bootp_layout.addWidget(QLabel(lab.upper()), row + i // 2, (i % 2) * 2)
            field = QLineEdit(dflt)
            self.bootp_addrs[lab] = field
            bootp_layout.addWidget(field, row + i // 2, (i % 2) * 2 + 1)

        bootp_layout.addWidget(QLabel("Hostname (opt 12)"), 5, 0)
        self.bootp_hostname = QLineEdit("")
        bootp_layout.addWidget(self.bootp_hostname, 5, 1)

        bootp_layout.addWidget(QLabel("Param Req List (opt 55, CSV)"), 5, 2)
        self.bootp_prl = QLineEdit("1,3,6,15,28,51,58,59")
        bootp_layout.addWidget(self.bootp_prl, 5, 3)

        bootp_group.setLayout(bootp_layout)
        # default disabled until checkbox ticked
        for w in bootp_group.findChildren(QWidget):
            if w is not self.udp_bootp_enable_checkbox:
                w.setEnabled(False)

        def toggle_bootp(enabled: bool):
            for w in bootp_group.findChildren(QWidget):
                if w is not self.udp_bootp_enable_checkbox:
                    w.setEnabled(enabled)

        self.udp_bootp_enable_checkbox.toggled.connect(toggle_bootp)

        layout.addWidget(bootp_group, 3, 0, 1, 7)

        self.udp_group.setLayout(layout)
        self.protocol_data_layout.addWidget(self.udp_group)

    def refresh_l4_sections(self):
        tcp_on = hasattr(self, "l4_tcp") and self.l4_tcp.isChecked()
        udp_on = hasattr(self, "l4_udp") and self.l4_udp.isChecked()
        roce_on = hasattr(self, "l4_rocev2") and self.l4_rocev2.isChecked()
        uec_on = hasattr(self, "l4_uec") and self.l4_uec.isChecked()
        embed_roce = hasattr(self, "uec_enable_rocev2_checkbox") and self.uec_enable_rocev2_checkbox.isChecked()

        if hasattr(self, "tcp_group"):    self.tcp_group.setEnabled(tcp_on)
        if hasattr(self, "udp_group"):    self.udp_group.setEnabled(udp_on)
        if hasattr(self, "rocev2_group"): self.rocev2_group.setEnabled(roce_on or (uec_on and embed_roce))
        if hasattr(self, "uec_group"):    self.uec_group.setEnabled(uec_on)
        # If ARP is selected at L3, disable all explicit L4 groups (no TCP/UDP over ARP).
        # v0.3.11: also UNCHECK the L4 radios — disabling the groupbox
        # alone left the radio in its prior state, so an operator who
        # picked UDP and then switched to ARP saved a stream with
        # both L3=ARP and L4=UDP. The server has no path for that
        # combo; the stream silently failed to transmit.
        if hasattr(self, "l3_arp") and self.l3_arp.isChecked():
            if hasattr(self, "tcp_group"):  self.tcp_group.setEnabled(False)
            if hasattr(self, "udp_group"):  self.udp_group.setEnabled(False)
            # Clear stale L4 selections so the saved stream stays
            # coherent. l4_none_1 is the primary "None" radio.
            for _attr in ("l4_tcp", "l4_udp", "l4_icmp", "l4_igmp",
                          "l4_rocev2", "l4_uec"):
                if hasattr(self, _attr):
                    getattr(self, _attr).setChecked(False)
            if hasattr(self, "l4_none_1"):
                self.l4_none_1.setChecked(True)
            if hasattr(self, "l4_none_2"):
                self.l4_none_2.setChecked(True)
    def add_rocev2_section(self):
        self.rocev2_group = QGroupBox("RoCEv2 (RDMA over Converged Ethernet v2)")
        rocev2_layout = QGridLayout()

        rocev2_layout.addWidget(QLabel("Traffic Class (0–7):"), 0, 0)
        self.rocev2_traffic_class = QComboBox()
        self.rocev2_traffic_class.addItems([str(i) for i in range(8)])
        rocev2_layout.addWidget(self.rocev2_traffic_class, 0, 1)

        rocev2_layout.addWidget(QLabel("Flow Label (Hex):"), 0, 2)
        self.rocev2_flow_label = QLineEdit("000000")
        self.rocev2_flow_label.setMaxLength(6)
        rocev2_layout.addWidget(self.rocev2_flow_label, 0, 3)

        rocev2_layout.addWidget(QLabel("Source QP:"), 0, 4)
        self.rocev2_source_qp = QLineEdit("0")
        self.rocev2_source_qp.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_source_qp, 0, 5)

        rocev2_layout.addWidget(QLabel("Destination QP:"), 0, 6)
        self.rocev2_destination_qp = QLineEdit("0")
        self.rocev2_destination_qp.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_destination_qp, 0, 7)

        rocev2_layout.addWidget(QLabel("Source GID:"), 1, 0)
        self.rocev2_source_gid = QLineEdit("0:0:0:0:0:ffff:192.168.0.2")
        rocev2_layout.addWidget(self.rocev2_source_gid, 1, 1, 1, 3)

        rocev2_layout.addWidget(QLabel("Source GID Step:"), 1, 4)
        self.rocev2_source_gid_step = QLineEdit("0:0:0:0:0:0:0:1")
        rocev2_layout.addWidget(self.rocev2_source_gid_step, 1, 5, 1, 3)

        rocev2_layout.addWidget(QLabel("GID Source Mode:"), 2, 0)
        self.rocev2_gid_source_mode = QComboBox()
        self.rocev2_gid_source_mode.addItems(["Fixed", "Increment"])
        rocev2_layout.addWidget(self.rocev2_gid_source_mode, 2, 1)

        rocev2_layout.addWidget(QLabel("GID Source Step:"), 2, 2)
        self.rocev2_gid_source_step = QLineEdit("1")
        self.rocev2_gid_source_step.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_gid_source_step, 2, 3)

        rocev2_layout.addWidget(QLabel("GID Source Count:"), 2, 4)
        self.rocev2_gid_source_count = QLineEdit("1")
        self.rocev2_gid_source_count.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_gid_source_count, 2, 5)

        rocev2_layout.addWidget(QLabel("Destination GID:"), 3, 0)
        self.rocev2_destination_gid = QLineEdit("0:0:0:0:0:ffff:192.168.0.3")
        rocev2_layout.addWidget(self.rocev2_destination_gid, 3, 1, 1, 3)

        rocev2_layout.addWidget(QLabel("Destination GID Step:"), 3, 4)
        self.rocev2_destination_gid_step = QLineEdit("0:0:0:0:0:0:0:1")
        rocev2_layout.addWidget(self.rocev2_destination_gid_step, 3, 5, 1, 3)

        rocev2_layout.addWidget(QLabel("GID Destination Mode:"), 4, 0)
        self.rocev2_gid_destination_mode = QComboBox()
        self.rocev2_gid_destination_mode.addItems(["Fixed", "Increment"])
        rocev2_layout.addWidget(self.rocev2_gid_destination_mode, 4, 1)

        rocev2_layout.addWidget(QLabel("GID Destination Step:"), 4, 2)
        self.rocev2_gid_destination_step = QLineEdit("1")
        self.rocev2_gid_destination_step.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_gid_destination_step, 4, 3)

        rocev2_layout.addWidget(QLabel("GID Destination Count:"), 4, 4)
        self.rocev2_gid_destination_count = QLineEdit("1")
        self.rocev2_gid_destination_count.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_gid_destination_count, 4, 5)

        # v0.3.11: wire the GID source/dest mode dropdowns to
        # enable/disable their corresponding step+count fields.
        # Without these connections, picking "Increment" left the
        # fields disabled (they default to disabled) → operator
        # couldn't actually configure the sweep. Mirror the IPv4
        # / IPv6 pattern in `update_increment_fields`.
        self.rocev2_gid_source_step.setEnabled(False)
        self.rocev2_gid_source_count.setEnabled(False)
        self.rocev2_gid_destination_step.setEnabled(False)
        self.rocev2_gid_destination_count.setEnabled(False)
        self.rocev2_gid_source_mode.currentTextChanged.connect(
            lambda mode: self.update_increment_fields(
                mode, self.rocev2_gid_source_step,
                self.rocev2_gid_source_count,
            )
        )
        self.rocev2_gid_destination_mode.currentTextChanged.connect(
            lambda mode: self.update_increment_fields(
                mode, self.rocev2_gid_destination_step,
                self.rocev2_gid_destination_count,
            )
        )

        rocev2_layout.addWidget(QLabel("Opcode:"), 5, 0)
        self.rocev2_opcode = QComboBox()
        self.rocev2_opcode.addItems([
            "SendOnly", "SendOnlySolicited", "SendLast", "SendLastSolicited",
            "RDMAWrite", "RDMAWriteOnlyImm", "RDMAReadRequest", "RDMAReadResponse",
            "AtomicCompareSwap", "AtomicFetchAdd", "CNP"
        ])
        rocev2_layout.addWidget(self.rocev2_opcode, 5, 1)

        rocev2_layout.addWidget(QLabel("Solicited Event:"), 5, 2)
        self.rocev2_solicited_event = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_solicited_event, 5, 3)

        rocev2_layout.addWidget(QLabel("Migration Req:"), 5, 4)
        self.rocev2_migration_req = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_migration_req, 5, 5)

        rocev2_layout.addWidget(QLabel("QP Count:"), 5, 6)
        self.rocev2_qp_count = QLineEdit("1")
        self.rocev2_qp_count.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_qp_count, 5, 7)

        rocev2_layout.addWidget(QLabel("Increment QP:"), 6, 0)
        self.rocev2_qp_increment = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_qp_increment, 6, 1)

        rocev2_layout.addWidget(QLabel("QP Increment Step:"), 6, 2)
        self.rocev2_qp_increment_step = QLineEdit("1")
        self.rocev2_qp_increment_step.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_qp_increment_step, 6, 3)

        rocev2_layout.addWidget(QLabel("Send CNP:"), 6, 4)
        self.rocev2_send_cnp = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_send_cnp, 6, 5)

        rocev2_layout.addWidget(QLabel("Increment Source GID:"), 6, 6)
        self.rocev2_increment_source_gid = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_increment_source_gid, 6, 7)

        rocev2_layout.addWidget(QLabel("Increment Destination GID:"), 7, 0)
        self.rocev2_increment_destination_gid = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_increment_destination_gid, 7, 1)

        self.rocev2_use_perf_server = QCheckBox("Use RoCEv2 Performance Server (ib_write_bw)")
        rocev2_layout.addWidget(self.rocev2_use_perf_server, 7, 2, 1, 3)

        self.rocev2_group.setLayout(rocev2_layout)
        self.protocol_data_layout.addWidget(self.rocev2_group)

        try:
            roce_selected = self.l4_rocev2.isChecked()
            uec_selected_and_embed = hasattr(self, "uec_enable_rocev2_checkbox") and \
                                     self.l4_uec.isChecked() and self.uec_enable_rocev2_checkbox.isChecked()
            self.rocev2_group.setEnabled(bool(roce_selected or uec_selected_and_embed))
        except Exception:
            self.rocev2_group.setEnabled(False)

    def add_uec_section(self):
        self.uec_group = QGroupBox("Ultra Ethernet Consortium (UEC)")
        uec_layout = QGridLayout()

        self.uec_qp_start_field = QLineEdit("1000")
        self.uec_qp_end_field = QLineEdit("1010")
        self.uec_qp_start_field.setValidator(QIntValidator(0, 2 ** 24 - 1))
        self.uec_qp_end_field.setValidator(QIntValidator(0, 2 ** 24 - 1))
        uec_layout.addWidget(QLabel("QP Start:"), 0, 0)
        uec_layout.addWidget(self.uec_qp_start_field, 0, 1)
        uec_layout.addWidget(QLabel("QP End:"), 0, 2)
        uec_layout.addWidget(self.uec_qp_end_field, 0, 3)

        self.uec_pasid_start_field = QLineEdit("5000")
        self.uec_pasid_end_field = QLineEdit("5010")
        self.uec_pasid_start_field.setValidator(QIntValidator(0, 2 ** 20 - 1))
        self.uec_pasid_end_field.setValidator(QIntValidator(0, 2 ** 20 - 1))
        uec_layout.addWidget(QLabel("PASID Start:"), 1, 0)
        uec_layout.addWidget(self.uec_pasid_start_field, 1, 1)
        uec_layout.addWidget(QLabel("PASID End:"), 1, 2)
        uec_layout.addWidget(self.uec_pasid_end_field, 1, 3)

        self.uec_ecn_combo_box = QComboBox()
        self.uec_ecn_combo_box.addItems(["Not-ECT", "ECT(1)", "ECT(0)", "CE"])
        uec_layout.addWidget(QLabel("ECN:"), 2, 0)
        uec_layout.addWidget(self.uec_ecn_combo_box, 2, 1)

        self.uec_flow_label_field = QLineEdit("0")
        self.uec_flow_label_field.setValidator(QIntValidator(0, 1_048_575))
        uec_layout.addWidget(QLabel("Flow Label:"), 2, 2)
        uec_layout.addWidget(self.uec_flow_label_field, 2, 3)

        self.uec_enable_spray_checkbox = QCheckBox("Enable QP/PASID Spray")
        uec_layout.addWidget(self.uec_enable_spray_checkbox, 3, 0, 1, 2)

        self.uec_enable_rocev2_checkbox = QCheckBox("Include RoCEv2 inside UEC frame")
        uec_layout.addWidget(self.uec_enable_rocev2_checkbox, 3, 2, 1, 2)
        self.uec_enable_rocev2_checkbox.toggled.connect(self.refresh_l4_sections)

        self.uec_group.setLayout(uec_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        try:
            self.uec_group.setEnabled(self.l4_uec.isChecked())
        except Exception:
            self.uec_group.setEnabled(False)

    def add_payload_data_section(self):
        payload_group = QGroupBox("Payload Data")
        payload_layout = QVBoxLayout()
        self.payload_data_field = QLineEdit("0000")
        payload_layout.addWidget(QLabel("Data:"))
        payload_layout.addWidget(self.payload_data_field)
        payload_group.setLayout(payload_layout)
        self.payload_group = payload_group

    # ----------------------------- PCAP Tab -----------------------------

    def setup_pcap_tab(self):
        outer_layout = QVBoxLayout()
        outer_layout.setSpacing(20)
        outer_layout.setContentsMargins(20, 20, 20, 20)
        pcap_group = QGroupBox("PCAP Replay Settings")
        pcap_form_layout = QFormLayout()
        pcap_form_layout.setSpacing(12)
        pcap_form_layout.setContentsMargins(16, 20, 16, 16)

        self.enable_pcap_checkbox = QCheckBox("Enable PCAP Replay")
        self.enable_pcap_checkbox.stateChanged.connect(self.toggle_pcap_controls)
        outer_layout.addWidget(self.enable_pcap_checkbox)

        pcap_file_layout = QHBoxLayout()
        self.pcap_file_path = QLineEdit()
        self.pcap_file_path.setPlaceholderText("Path to PCAP file")
        self.browse_pcap_button = QPushButton("Browse")
        self.browse_pcap_button.clicked.connect(self.browse_pcap_file)
        pcap_file_layout.addWidget(self.pcap_file_path)
        pcap_file_layout.addWidget(self.browse_pcap_button)
        pcap_form_layout.addRow("PCAP File:", pcap_file_layout)

        self.pcap_metadata_label = QLabel()
        self.pcap_metadata_label.setWordWrap(True)
        self.pcap_metadata_label.setStyleSheet("color: gray;")
        pcap_form_layout.addRow("", self.pcap_metadata_label)

        self.pcap_loop_count = QSpinBox()
        self.pcap_loop_count.setRange(1, 1_000_000)
        self.pcap_loop_count.setValue(1)
        pcap_form_layout.addRow("Loop Count:", self.pcap_loop_count)

        self.pcap_rate_mode = QComboBox()
        self.pcap_rate_mode.addItems(["Original Timing", "Fixed Delay", "Inter-Packet Gap"])
        pcap_form_layout.addRow("Replay Rate Mode:", self.pcap_rate_mode)

        pcap_group.setLayout(pcap_form_layout)
        outer_layout.addWidget(pcap_group)
        self.pcap_tab.setLayout(outer_layout)
        self.toggle_pcap_controls()

        self.pcap_file_path.textChanged.connect(self.validate_pcap_file)

    def toggle_pcap_controls(self):
        enabled = self.enable_pcap_checkbox.isChecked()
        for w in (self.pcap_file_path, self.browse_pcap_button, self.pcap_loop_count, self.pcap_rate_mode):
            w.setEnabled(enabled)
        self.pcap_metadata_label.setVisible(enabled)
        if not enabled:
            self.pcap_metadata_label.clear()

    def browse_pcap_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PCAP File", "", "PCAP Files (*.pcap *.pcapng);;All Files (*)")
        if path:
            self.pcap_file_path.setText(path)

    def validate_pcap_file(self):
        path = self.pcap_file_path.text().strip()
        if os.path.isfile(path):
            size = os.path.getsize(path)
            modified = QDateTime.fromSecsSinceEpoch(int(os.path.getmtime(path))).toString("yyyy-MM-dd HH:mm:ss")
            name = os.path.basename(path)
            self.pcap_metadata_label.setText(f"📄 <b>{name}</b> — {size:,} bytes, modified: {modified}")
        else:
            self.pcap_metadata_label.setText("❌ File not found or invalid.")

    # ----------------------------- Packet View -----------------------------

    def setup_packet_view_tab(self):
        self.packet_view_layout.setSpacing(20)
        self.packet_view_layout.setContentsMargins(20, 20, 20, 20)
        self.packet_tree = QTreeWidget()
        self.packet_tree.setHeaderLabels(["Protocol Layer", "Configuration Details"])
        self.packet_view_layout.addWidget(self.packet_tree)
        self.tabs.currentChanged.connect(self.handle_tab_changed)

    def handle_tab_changed(self, index):
        if self.tabs.tabText(index) == "Packet View":
            self.populate_packet_view(self.get_stream_details())

    def connect_protocol_data_to_packet_view(self):
        # Basic lightweight strategy: refresh tree when the Packet View tab is shown (above),
        # and also on some common edits to keep it reasonably live.
        for w in [
            self.stream_name, self.details_field, self.frame_min, self.frame_max, self.frame_size,
            self.source_field, self.destination_field, self.ttl_field, self.identification_field,
            self.ipv6_source_field, self.ipv6_destination_field, self.ipv6_hop_limit_field,
            self.source_port_field, self.destination_port_field, self.udp_source_port_field, self.udp_destination_port_field
        ]:
            try:
                w.textChanged.connect(lambda *_: self._refresh_packet_view_if_visible())
            except Exception:
                pass
        for w in [
            # L1 + payload_random removed in v0.3.11 (dead widgets).
            self.vlan_untagged, self.vlan_tagged, self.vlan_stacked,
            self.l2_none, self.l2_ethernet, self.l2_mpls,
            self.l3_none, self.l3_arp, self.l3_ipv4, self.l3_ipv6,
            self.l4_none_1, self.l4_none_2, self.l4_icmp, self.l4_igmp, self.l4_tcp, self.l4_udp, self.l4_rocev2, self.l4_uec,
            self.payload_none, self.payload_pattern, self.payload_from_file,
        ]:
            try:
                w.toggled.connect(lambda *_: self._refresh_packet_view_if_visible())
            except Exception:
                pass

    def _refresh_packet_view_if_visible(self):
        if self.tabs.tabText(self.tabs.currentIndex()) == "Packet View":
            self.populate_packet_view(self.get_stream_details())

    # ----------------------------- Populate / Collect -----------------------------

    def _resolve_dpdk_enable(self, stream_data: dict) -> bool:
        """Return True if any legacy or new field says 'use DPDK'."""

        def truthy(v):
            s = str(v).strip().lower()
            return s in ("1", "true", "yes", "on", "dpdk")

        return any((
            truthy(stream_data.get("dpdk_enable", False)),
            truthy(stream_data.get("engine", "")),  # "dpdk" supported
            truthy(stream_data.get("protocol_selection", {}).get("dpdk_enable", False)),
            truthy(stream_data.get("variable_fields", {}).get("dpdk_enable", False)),
        ))

    def _resolve_engine(self, stream_data: dict) -> str:
        """v0.3.12: read stream_data and pick one of {scapy, dpdk, rdma}.

        Forward-compat: prefer the explicit ``engine`` key when set.
        Backward-compat: if engine is absent, fall back to the legacy
        ``dpdk_enable`` boolean (set by pre-v0.3.12 saves and by the
        retained checkbox in the dialog). Default → 'scapy'.
        """
        for key_path in (
            ("engine",),
            ("protocol_selection", "engine"),
            ("variable_fields", "engine"),
        ):
            cur = stream_data
            ok = True
            for k in key_path:
                if not isinstance(cur, dict):
                    ok = False
                    break
                cur = cur.get(k)
            if ok and isinstance(cur, str) and cur.strip().lower() in ("scapy", "dpdk", "rdma"):
                return cur.strip().lower()
        # Legacy fallback.
        return "dpdk" if self._resolve_dpdk_enable(stream_data) else "scapy"

    def _on_engine_combo_changed(self, _idx: int) -> None:
        """Engine combo is authoritative. Sync the DPDK checkbox and
        show/hide the RDMA params group accordingly."""
        engine = (self.engine_combo.currentData() or "scapy")
        # Block recursive toggle while we mirror the checkbox state.
        if hasattr(self, "dpdk_enable_checkbox"):
            blocker = self.dpdk_enable_checkbox.blockSignals(True)
            try:
                self.dpdk_enable_checkbox.setChecked(engine == "dpdk")
                self.dpdk_enable_checkbox.setEnabled(engine != "rdma")
            finally:
                self.dpdk_enable_checkbox.blockSignals(blocker)
        if hasattr(self, "rdma_params_group"):
            self.rdma_params_group.setVisible(engine == "rdma")

    def _on_dpdk_checkbox_toggled(self, checked: bool) -> None:
        """When the operator toggles the legacy DPDK checkbox directly,
        keep the engine combo in sync so save / display stay coherent.

        Doesn't fire when engine is RDMA — the checkbox is disabled in
        that case so the toggle physically can't happen."""
        if not hasattr(self, "engine_combo"):
            return
        current = self.engine_combo.currentData()
        if current == "rdma":
            return  # combo wins; checkbox shouldn't be toggleable anyway
        want = "dpdk" if checked else "scapy"
        if current == want:
            return
        idx = self.engine_combo.findData(want)
        if idx >= 0:
            # Set without triggering _on_engine_combo_changed → checkbox
            # round-trip (we got here because the checkbox changed).
            blocker = self.engine_combo.blockSignals(True)
            try:
                self.engine_combo.setCurrentIndex(idx)
            finally:
                self.engine_combo.blockSignals(blocker)
            # Still show/hide the RDMA group based on final state.
            if hasattr(self, "rdma_params_group"):
                self.rdma_params_group.setVisible(want == "rdma")

    def _on_traffic_template_changed(self, idx: int):
        """Apply the selected traffic template to all tabs.

        The template registry returns a `stream_data` dict in the
        same shape `populate_stream_fields()` already consumes, so we
        reuse that single canonical path — no per-tab juggling. The
        'Custom' option leaves all fields alone for from-scratch entry.
        """
        key = self.template_combo.itemData(idx)
        if not key:
            # v0.3.11 compact: clear the summary line instead of
            # showing the verbose "Pick a template..." hint. The
            # dropdown's own "— Custom (no template) —" entry
            # already conveys the action.
            self._traffic_template_summary.setText("")
            return
        meta = next(
            (t for t in self._traffic_templates_meta if t["key"] == key),
            None,
        )
        if meta:
            self._traffic_template_summary.setText(meta["summary"])
        try:
            from utils.traffic_templates import get_stream_data
            data = get_stream_data(key)
            if data:
                self.populate_stream_fields(data)
                # v0.3.11: kick the Packet View tab so the operator
                # sees the freshly-templated packet, not the stale one
                # from before. populate_stream_fields fires many
                # textChanged signals that DO call this refresh, but
                # the radio button / combobox changes from a template
                # apply DON'T all chain through textChanged — without
                # this explicit kick, Packet View tab stays stale
                # until the operator types in any field.
                if hasattr(self, "_refresh_packet_view_if_visible"):
                    self._refresh_packet_view_if_visible()
        except Exception as exc:
            self._traffic_template_summary.setText(
                f"Template '{key}' failed to apply: {exc}"
            )

    # ─────────────────────────────── v0.3.11: pre-save validation

    def accept(self):
        """Override QDialog.accept to run cross-layer validation
        before the dialog closes. Catches invalid combos that
        single-field validators (validators on individual QLineEdits)
        can't see — like 'L2=None + L3=IPv4' which is fine field-by-
        field but produces a headerless IP frame the server drops.

        On any validation failure: show a QMessageBox describing
        the problem AND its fix, then keep the dialog open so the
        operator can correct it without losing all their other
        edits. Only call super().accept() when everything's clean.

        Skip-on-edit-existing: if stream_data was passed in
        (operator is editing a previously-saved stream), validation
        still runs — better to surface long-standing bugs in
        existing saved streams than to ship them unchanged.
        """
        problems = self._validate_cross_layer()
        if problems:
            from PyQt5.QtWidgets import QMessageBox
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Stream has invalid combinations")
            box.setText(
                "The stream you're saving has fields that don't "
                "form a transmittable packet:"
            )
            box.setInformativeText("\n".join(f"• {p}" for p in problems))
            box.setStandardButtons(
                QMessageBox.Cancel | QMessageBox.Save
            )
            box.setDefaultButton(QMessageBox.Cancel)
            box.button(QMessageBox.Save).setText("Save Anyway")
            box.button(QMessageBox.Cancel).setText("Fix First")
            choice = box.exec_()
            if choice != QMessageBox.Save:
                # Operator chose to fix — keep dialog open. Their
                # other edits are preserved (this is the whole point
                # of validating in accept() vs at field-edit time).
                return
        super().accept()

    def _validate_cross_layer(self) -> list:
        """Run every cross-layer / multi-field sanity check.
        Returns a list of human-readable problem strings (empty
        if clean). Pure-function-ish — only reads widget state,
        no side effects, so it's safe to call from a test."""
        problems = []

        # ─── Frame Type = Random: Min must be < Max ───
        if hasattr(self, "frame_type") and self.frame_type.currentText() == "Random":
            try:
                fmin = int(self.frame_min.text().strip() or "0")
                fmax = int(self.frame_max.text().strip() or "0")
                if fmin >= fmax:
                    problems.append(
                        f"Frame Type=Random needs Min < Max — got "
                        f"Min={fmin}, Max={fmax}. Either widen the "
                        f"range or switch to Frame Type=Fixed."
                    )
            except (ValueError, AttributeError):
                problems.append(
                    "Frame Type=Random has non-numeric Min/Max — "
                    "fix the values or switch to Fixed."
                )

        # ─── L2=None but L3 picks an Ethernet-payload protocol ───
        l3 = self._selected_l3() if hasattr(self, "_selected_l3") else "None"
        l2 = self._selected_l2() if hasattr(self, "_selected_l2") else "None"
        if l2 == "None" and l3 in ("IPv4", "IPv6", "ARP"):
            problems.append(
                f"L2=None with L3={l3} produces a frame with no "
                f"Ethernet header — the NIC will drop it. Set "
                f"L2=Ethernet II to wrap the {l3} payload."
            )

        # ─── PCAP enabled + protocol-stack picks both set ───
        # PCAP replay transmits the file's contents verbatim — the
        # protocol-stack fields are ignored. Warn before the operator
        # spends 20 minutes wondering why their custom UDP fields
        # don't show up in the capture.
        pcap_on = (
            hasattr(self, "enable_pcap_checkbox")
            and self.enable_pcap_checkbox.isChecked()
        )
        stack_set = (l3 != "None") or (
            hasattr(self, "_selected_l4")
            and self._selected_l4() != "None"
        )
        if pcap_on and stack_set:
            problems.append(
                "PCAP Replay is enabled AND protocol-stack fields "
                "(L3/L4) are set. PCAP transmits the file's frames "
                "verbatim — the L3/L4 picks will be silently ignored. "
                "Either uncheck PCAP or clear L3/L4 to confirm intent."
            )

        return problems

    # ─────────────────────────────────────── v0.2.96: input validation
    def _wire_live_validators(self):
        """Bind ``textChanged`` on the MAC + IP QLineEdits so the
        operator sees a red border the moment they type something
        the server will reject. Pure-function validators live in
        ``utils/stream_input.py`` so the parsing has its own tests
        without spinning up Qt.

        Defensive: any missing attribute (e.g. the IPv6 section
        wasn't built because the protocol-data tab was lazy-loaded
        on a tab change) is silently skipped — the dialog stays
        usable; the accept() backstop catches it at submit.
        """
        pairs = [
            ("mac_source_address",       validate_mac),
            ("mac_destination_address",  validate_mac),
            ("source_field",             validate_ipv4),  # IPv4 src
            ("destination_field",        validate_ipv4),  # IPv4 dst
            ("ipv6_source_field",        validate_ipv6),
            ("ipv6_destination_field",   validate_ipv6),
            # v0.3.11: ARP also has MAC + IPv4 fields that were
            # bypassing live validation. Same red-border feedback
            # so the operator sees the moment they type "0.0.0.999"
            # or "GG:..." into an ARP form.
            ("arp_sender_mac",           validate_mac),
            ("arp_target_mac",           validate_mac),
            ("arp_sender_ip",            validate_ipv4),
            ("arp_target_ip",            validate_ipv4),
        ]
        for attr, validator in pairs:
            line = getattr(self, attr, None)
            if line is None:
                continue
            try:
                # Closure over validator + line — default-arg trick
                # to capture per-iteration value instead of last-loop.
                def _on_change(_text="", _l=line, _v=validator):
                    err = _v(_l.text())
                    _l.setStyleSheet(_LIVE_BAD_QSS if err else _LIVE_OK_QSS)
                    # Tooltip carries the explanatory error so the
                    # operator can hover to see why it's red.
                    _l.setToolTip(err or "")
                line.textChanged.connect(_on_change)
                # Run once now so a pre-populated edit-mode value
                # is evaluated immediately instead of waiting for
                # the operator to touch the field.
                _on_change()
            except Exception:
                # Don't let a single field's wiring break the dialog.
                continue

    def accept(self):
        """v0.2.96: gate Save behind a full-form validation pass.

        Pre-v0.2.96 the buttons.accepted signal connected straight
        to ``QDialog.accept`` — Save would close the dialog with
        any garbage in any field. The server's /api/stream/add
        rejected later, but the operator had already lost the modal
        and could only see the failure as a generic toast.

        Collects every per-field error AND the cross-field frame-
        size constraint into a single QMessageBox so the operator
        sees the full picture instead of dismissing one popup at
        a time. Returns without calling super().accept() so the
        dialog stays open on failure.
        """
        # Per-field MAC + IP validation. Skip absent fields
        # (lazy-loaded protocol sub-sections, edit mode without
        # certain protocols selected) — accept() shouldn't reject
        # a stream because a field it doesn't use is unset.
        check_pairs = []
        for label, attr, validator in [
            ("Source MAC",        "mac_source_address",       validate_mac),
            ("Destination MAC",   "mac_destination_address",  validate_mac),
            ("Source IPv4",       "source_field",             validate_ipv4),
            ("Destination IPv4",  "destination_field",        validate_ipv4),
            ("Source IPv6",       "ipv6_source_field",        validate_ipv6),
            ("Destination IPv6",  "ipv6_destination_field",   validate_ipv6),
        ]:
            line = getattr(self, attr, None)
            if line is None:
                continue
            # An IPv6 field that's part of the dialog but the
            # operator hasn't enabled the IPv6 group for: skip the
            # check rather than force them to fix a field they
            # won't ship. We use isEnabled() as the proxy because
            # the dialog disables sections when the protocol combo
            # excludes them.
            try:
                if not line.isEnabled():
                    continue
            except Exception:
                pass
            check_pairs.append((label, line.text(), validator))

        errors = collect_errors(check_pairs)

        # Cross-field: frame_min ≤ frame_max (only when both fields
        # exist + their parent random/imix mode is active). Read
        # the frame_type combo to know which check applies.
        try:
            frame_type = "fixed"
            ft_combo = getattr(self, "frame_type_dropdown", None) \
                or getattr(self, "frame_type", None)
            if ft_combo is not None and hasattr(ft_combo, "currentText"):
                frame_type = (ft_combo.currentText() or "fixed").lower()
            def _maybe_int(attr):
                w = getattr(self, attr, None)
                if w is None:
                    return None
                try:
                    return int(w.text())
                except (ValueError, TypeError):
                    return None
            fs_err = validate_frame_sizes(
                fixed=_maybe_int("frame_size"),
                minimum=_maybe_int("frame_min"),
                maximum=_maybe_int("frame_max"),
                frame_type=frame_type,
            )
            if fs_err is not None:
                errors.append(("Frame size", fs_err))
        except Exception:
            # Frame-section absent in some dialog modes — skip.
            pass

        if errors:
            lines = ["The following fields need attention:", ""]
            for label, err in errors:
                lines.append(f"  • {label}: {err}")
            lines.append("")
            lines.append(
                "Fix the highlighted (red-border) fields and click "
                "Save again."
            )
            QMessageBox.warning(
                self, "Stream configuration invalid", "\n".join(lines),
            )
            return  # stay open
        super().accept()

    def populate_stream_fields(self, stream_data=None):
        stream_data = stream_data or {}
        # Basics
        self.stream_name.setText(stream_data.get("name", ""))
        self.enabled_checkbox.setChecked(stream_data.get("enabled", False))
        self.details_field.setText(stream_data.get("details", ""))
        self.flow_tracking_checkbox.setChecked(stream_data.get("flow_tracking_enabled", False))

        # restore DPDK toggle from any supported location
        if hasattr(self, "dpdk_enable_checkbox"):
            self.dpdk_enable_checkbox.setChecked(self._resolve_dpdk_enable(stream_data))
        # v0.3.12: engine combo + RDMA params group restore.
        if hasattr(self, "engine_combo"):
            engine = self._resolve_engine(stream_data)
            idx = self.engine_combo.findData(engine)
            if idx >= 0:
                self.engine_combo.setCurrentIndex(idx)
            # Restore RDMA params if engine was rdma.
            rdma_cfg = stream_data.get("rdma") or {}
            if isinstance(rdma_cfg, dict):
                test = rdma_cfg.get("test", "send_bw")
                ti = self.rdma_test_combo.findData(test) if hasattr(self, "rdma_test_combo") else -1
                if ti >= 0:
                    self.rdma_test_combo.setCurrentIndex(ti)
                if hasattr(self, "rdma_device_field"):
                    self.rdma_device_field.setText(str(rdma_cfg.get("device", "mlx5_0")))
                if hasattr(self, "rdma_peer_field"):
                    self.rdma_peer_field.setText(str(rdma_cfg.get("peer_addr", "")))
                if hasattr(self, "rdma_msg_size_spin"):
                    try:
                        self.rdma_msg_size_spin.setValue(int(rdma_cfg.get("msg_size", 65536)))
                    except (TypeError, ValueError):
                        pass
                if hasattr(self, "rdma_qp_count_spin"):
                    try:
                        self.rdma_qp_count_spin.setValue(int(rdma_cfg.get("qp_count", 1)))
                    except (TypeError, ValueError):
                        pass
                if hasattr(self, "rdma_duration_spin"):
                    try:
                        self.rdma_duration_spin.setValue(int(rdma_cfg.get("duration", 30)))
                    except (TypeError, ValueError):
                        pass
                if hasattr(self, "rdma_gid_index_spin"):
                    try:
                        self.rdma_gid_index_spin.setValue(int(rdma_cfg.get("gid_index", 3)))
                    except (TypeError, ValueError):
                        pass
                if hasattr(self, "rdma_bidir_check"):
                    self.rdma_bidir_check.setChecked(bool(rdma_cfg.get("bidirectional", False)))
            # Trigger group show/hide based on restored engine.
            self._on_engine_combo_changed(self.engine_combo.currentIndex())
        if hasattr(self, "dpdk_multi_instance_checkbox"):
            self.dpdk_multi_instance_checkbox.setChecked(
                bool(stream_data.get("dpdk_multi_instance", False))
            )
        if hasattr(self, "dpdk_tx_cores_combo"):
            try:
                want = int(stream_data.get("dpdk_tx_cores") or 1)
            except (TypeError, ValueError):
                want = 1
            # Find the closest matching item; fall back to index 0 (=1)
            idx = self.dpdk_tx_cores_combo.findData(want)
            self.dpdk_tx_cores_combo.setCurrentIndex(idx if idx >= 0 else 0)

        if hasattr(self, "enable_timestamps_checkbox"):
            self.enable_timestamps_checkbox.setChecked(
                bool(stream_data.get("enable_timestamps") or
                     stream_data.get("latency_enabled") or False)
            )


        # Frame Length
        self.frame_type.setCurrentText(stream_data.get("frame_type", "Fixed"))
        # v0.3.11 fix: every traffic template stores frame_size /
        # frame_min / frame_max as INT (Pythonic), but QLineEdit.setText
        # requires str — without str() coercion, applying any template
        # raises TypeError and the dialog never repaints. Round-trip
        # through str() works for both str and int input.
        self.frame_min.setText(str(stream_data.get("frame_min", "64")))
        self.frame_max.setText(str(stream_data.get("frame_max", "1518")))
        self.frame_size.setText(str(stream_data.get("frame_size", "64")))

        # L2/L3/L4/Payload — L1 group removed in v0.3.11 (was dead UI).
        # We still ACCEPT a saved L1 field in stream_data so existing
        # session.json files load without warnings, but there are no
        # widgets to write the value to anymore.

        vlan_sel = stream_data.get("VLAN", "Untagged")
        self.vlan_untagged.setChecked(vlan_sel == "Untagged")
        self.vlan_tagged.setChecked(vlan_sel == "Tagged")
        self.vlan_stacked.setChecked(vlan_sel == "Stacked")

        l2 = stream_data.get("L2", "None")
        self.l2_none.setChecked(l2 == "None")
        self.l2_ethernet.setChecked(l2 == "Ethernet II")
        self.l2_mpls.setChecked(l2 == "MPLS")

        l3 = stream_data.get("L3", "None")
        self.l3_none.setChecked(l3 == "None")
        self.l3_arp.setChecked(l3 == "ARP")
        self.l3_ipv4.setChecked(l3 == "IPv4")
        self.l3_ipv6.setChecked(l3 == "IPv6")

        l4 = stream_data.get("L4", "None")
        # Update both L4 groups
        self.l4_none_1.setChecked(l4 == "None")
        self.l4_none_2.setChecked(l4 == "None")
        self.l4_icmp.setChecked(l4 == "ICMP")
        self.l4_igmp.setChecked(l4 == "IGMP")
        self.l4_tcp.setChecked(l4 == "TCP")
        self.l4_udp.setChecked(l4 == "UDP")
        self.l4_rocev2.setChecked(l4 == "RoCEv2")
        self.l4_uec.setChecked(l4 == "UEC")


        # MAC detailed
        mac_data = stream_data.get("protocol_data", {}).get("mac", {})
        try:
            # Destination
            self.mac_destination_mode.setCurrentText(mac_data.get("mac_destination_mode", "Fixed"))
            self.mac_destination_address.setText(mac_data.get("mac_destination_address", "00:00:00:00:00:00"))
            self.mac_destination_count.setText(mac_data.get("mac_destination_count", "16"))
            self.mac_destination_step.setText(mac_data.get("mac_destination_step", "1"))
            # enable/disable count/step per mode
            self.toggle_mac_fields(self.mac_destination_mode.currentText(),
                                   self.mac_destination_count, self.mac_destination_step)

            # Source
            self.mac_source_mode.setCurrentText(mac_data.get("mac_source_mode", "Fixed"))
            self.mac_source_address.setText(mac_data.get("mac_source_address", "00:00:00:00:00:00"))
            self.mac_source_count.setText(mac_data.get("mac_source_count", "16"))
            self.mac_source_step.setText(mac_data.get("mac_source_step", "1"))
            # enable/disable count/step per mode
            self.toggle_mac_fields(self.mac_source_mode.currentText(),
                                   self.mac_source_count, self.mac_source_step)
        except Exception as e:
            logger.warning("populate_stream_fields: failed to load MAC section: %s", e)

        # ARP restore
        arp = (stream_data.get("protocol_data", {}) or {}).get("arp", {})
        self.arp_operation.setCurrentText(arp.get("arp_operation", "Request"))
        self.arp_sender_mac.setText(arp.get("arp_sender_mac", "00:11:22:33:44:55"))
        self.arp_sender_ip.setText(arp.get("arp_sender_ip", "0.0.0.0"))
        self.arp_target_mac.setText(arp.get("arp_target_mac", "ff:ff:ff:ff:ff:ff"))
        self.arp_target_ip.setText(arp.get("arp_target_ip", "0.0.0.0"))
        # VLAN detailed
        vlan_data = stream_data.get("protocol_data", {}).get("vlan", {})
        self.vlan_increment_checkbox.setChecked(vlan_data.get("vlan_increment", False))
        self.vlan_increment_value.setText(vlan_data.get("vlan_increment_value", "1"))
        self.vlan_increment_count.setText(vlan_data.get("vlan_increment_count", "1"))
        self.vlan_increment_value.setEnabled(vlan_data.get("vlan_increment", False))
        self.vlan_increment_count.setEnabled(vlan_data.get("vlan_increment", False))
        self.priority_field.setCurrentText(vlan_data.get("vlan_priority", "0"))
        self.cfi_dei_field.setCurrentText(vlan_data.get("vlan_cfi_dei", "0"))
        self.vlan_id_field.setText(vlan_data.get("vlan_id", "1"))
        self.tpid_field.setText(vlan_data.get("vlan_tpid", "81 00"))
        self.override_tpid_checkbox.setChecked(stream_data.get("override_settings", {}).get("override_vlan_tpid", False))

        # MPLS
        mpls_data = stream_data.get("protocol_data", {}).get("mpls", {})
        # v0.3.11: str() coerce so an int from a future template
        # (e.g., {"mpls_label": 16}) doesn't raise TypeError —
        # same trap we fixed for frame_size.
        self.mpls_label_field.setText(str(mpls_data.get("mpls_label", "16")))
        self.mpls_ttl_field.setText(str(mpls_data.get("mpls_ttl", "64")))
        self.mpls_experimental_field.setText(str(mpls_data.get("mpls_experimental", "0")))
        # SR-MPLS label stack (0.2.65). Stored either as a list or a
        # comma-separated string — normalise back to the dialog's
        # comma-separated text form so the user sees what they typed.
        raw_labels = mpls_data.get("mpls_labels", "")
        if isinstance(raw_labels, list):
            self.mpls_labels_field.setText(", ".join(str(x) for x in raw_labels))
        else:
            self.mpls_labels_field.setText(str(raw_labels or ""))

        # TCP
        tcp_data = stream_data.get("protocol_data", {}).get("tcp", {})
        ov = stream_data.get("override_settings", {})
        self.override_source_port_checkbox.setChecked(ov.get("override_source_tcp_port", False))
        self.source_port_field.setText(tcp_data.get("tcp_source_port", "0"))
        self.source_port_field.setEnabled(self.override_source_port_checkbox.isChecked())

        self.increment_tcp_source_checkbox.setChecked(tcp_data.get("tcp_increment_source_port", False))
        self.tcp_source_increment_step.setText(tcp_data.get("tcp_source_port_step", "1"))
        self.tcp_source_increment_count.setText(tcp_data.get("tcp_source_port_count", "1"))
        self.tcp_source_increment_step.setEnabled(self.increment_tcp_source_checkbox.isChecked())
        self.tcp_source_increment_count.setEnabled(self.increment_tcp_source_checkbox.isChecked())

        self.override_destination_port_checkbox.setChecked(ov.get("override_destination_tcp_port", False))
        self.destination_port_field.setText(tcp_data.get("tcp_destination_port", "0"))
        self.destination_port_field.setEnabled(self.override_destination_port_checkbox.isChecked())

        self.increment_tcp_destination_checkbox.setChecked(tcp_data.get("tcp_increment_destination_port", False))
        self.tcp_destination_increment_step.setText(tcp_data.get("tcp_destination_port_step", "1"))
        self.tcp_destination_increment_count.setText(tcp_data.get("tcp_destination_port_count", "1"))
        self.tcp_destination_increment_step.setEnabled(self.increment_tcp_destination_checkbox.isChecked())
        self.tcp_destination_increment_count.setEnabled(self.increment_tcp_destination_checkbox.isChecked())

        self.override_checksum_checkbox.setChecked(ov.get("override_checksum", False))
        self.tcp_checksum_field.setText(tcp_data.get("tcp_checksum", "B3 E7"))
        self.tcp_checksum_field.setEnabled(self.override_checksum_checkbox.isChecked())

        flags = [f.strip().upper() for f in tcp_data.get("tcp_flags", "").split(",")] if tcp_data.get("tcp_flags") else []
        self.flag_urg.setChecked("URG" in flags)
        self.flag_ack.setChecked("ACK" in flags)
        self.flag_psh.setChecked("PSH" in flags)
        self.flag_rst.setChecked("RST" in flags)
        self.flag_syn.setChecked("SYN" in flags)
        self.flag_fin.setChecked("FIN" in flags)

        # UDP
        udp = stream_data.get("protocol_data", {}).get("udp", {})
        self.override_udp_source_port_checkbox.setChecked(ov.get("override_source_udp_port", False))
        self.udp_source_port_field.setText(udp.get("udp_source_port", "0"))
        self.udp_source_port_field.setEnabled(self.override_udp_source_port_checkbox.isChecked())

        self.udp_increment_source_checkbox.setChecked(udp.get("udp_increment_source_port", False))
        self.udp_source_increment_step.setText(udp.get("udp_source_port_step", "1"))
        self.udp_source_increment_count.setText(udp.get("udp_source_port_count", "1"))
        self.udp_source_increment_step.setEnabled(self.udp_increment_source_checkbox.isChecked())
        self.udp_source_increment_count.setEnabled(self.udp_increment_source_checkbox.isChecked())

        self.override_udp_destination_port_checkbox.setChecked(ov.get("override_destination_udp_port", False))
        self.udp_destination_port_field.setText(udp.get("udp_destination_port", "0"))
        self.udp_destination_port_field.setEnabled(self.override_udp_destination_port_checkbox.isChecked())

        self.udp_increment_destination_checkbox.setChecked(udp.get("udp_increment_destination_port", False))
        self.udp_destination_increment_step.setText(udp.get("udp_destination_port_step", "1"))
        self.udp_destination_increment_count.setText(udp.get("udp_destination_port_count", "1"))
        self.udp_destination_increment_step.setEnabled(self.udp_increment_destination_checkbox.isChecked())
        self.udp_destination_increment_count.setEnabled(self.udp_increment_destination_checkbox.isChecked())

        self.override_udp_checksum_checkbox.setChecked(ov.get("override_udp_checksum", False))
        self.udp_checksum_field.setText(udp.get("udp_checksum", ""))
        self.udp_checksum_field.setEnabled(self.override_udp_checksum_checkbox.isChecked())

        self.udp_preset_combo.setCurrentText(udp.get("udp_preset", "Custom"))
        self.udp_bootp_enable_checkbox.setChecked(udp.get("udp_bootp_enabled", False))
        self.bootp_msg_type.setCurrentText(udp.get("bootp_msg_type", "DHCPDISCOVER"))
        self.bootp_xid.setText(udp.get("bootp_xid", "0x12345678"))
        self.bootp_client_mac.setText(udp.get("bootp_client_mac", "00:11:22:33:44:55"))
        self.bootp_flags.setText(udp.get("bootp_flags", "0x0000"))
        self.bootp_addrs["ciaddr"].setText(udp.get("bootp_ciaddr", "0.0.0.0"))
        self.bootp_addrs["yiaddr"].setText(udp.get("bootp_yiaddr", "0.0.0.0"))
        self.bootp_addrs["siaddr"].setText(udp.get("bootp_siaddr", "0.0.0.0"))
        self.bootp_addrs["giaddr"].setText(udp.get("bootp_giaddr", "0.0.0.0"))
        self.bootp_hostname.setText(udp.get("bootp_hostname", ""))
        self.bootp_prl.setText(udp.get("bootp_prl", "1,3,6,15,28,51,58,59"))

        # RoCEv2
        rocev2 = stream_data.get("protocol_data", {}).get("rocev2", {})
        self.rocev2_traffic_class.setCurrentText(rocev2.get("rocev2_traffic_class", "0"))
        self.rocev2_flow_label.setText(rocev2.get("rocev2_flow_label", "000000"))
        self.rocev2_source_gid.setText(rocev2.get("rocev2_source_gid", "0:0:0:0:0:ffff:192.168.0.2"))
        self.rocev2_destination_gid.setText(rocev2.get("rocev2_destination_gid", "0:0:0:0:0:ffff:192.168.0.3"))
        self.rocev2_increment_source_gid.setChecked(rocev2.get("rocev2_increment_source_gid", False))
        self.rocev2_source_gid_step.setText(rocev2.get("rocev2_source_gid_step", "1"))
        self.rocev2_increment_destination_gid.setChecked(rocev2.get("rocev2_increment_destination_gid", False))
        self.rocev2_destination_gid_step.setText(rocev2.get("rocev2_destination_gid_step", "1"))
        self.rocev2_source_qp.setText(rocev2.get("rocev2_source_qp", "0"))
        self.rocev2_destination_qp.setText(rocev2.get("rocev2_destination_qp", "0"))
        self.rocev2_opcode.setCurrentText(rocev2.get("rocev2_opcode", "SendOnly"))
        self.rocev2_solicited_event.setChecked(rocev2.get("rocev2_solicited_event", False))
        self.rocev2_migration_req.setChecked(rocev2.get("rocev2_migration_req", False))
        self.rocev2_qp_count.setText(rocev2.get("rocev2_qp_count", "1"))
        self.rocev2_qp_increment.setChecked(rocev2.get("rocev2_qp_increment", False))
        self.rocev2_qp_increment_step.setText(rocev2.get("rocev2_qp_increment_step", "1"))
        self.rocev2_gid_source_mode.setCurrentText(rocev2.get("rocev2_gid_source_mode", "Fixed"))
        self.rocev2_gid_source_step.setText(rocev2.get("rocev2_gid_source_step", "1"))
        self.rocev2_gid_source_count.setText(rocev2.get("rocev2_gid_source_count", "1"))
        self.rocev2_gid_destination_mode.setCurrentText(rocev2.get("rocev2_gid_destination_mode", "Fixed"))
        self.rocev2_gid_destination_step.setText(rocev2.get("rocev2_gid_destination_step", "1"))
        self.rocev2_gid_destination_count.setText(rocev2.get("rocev2_gid_destination_count", "1"))
        self.rocev2_send_cnp.setChecked(rocev2.get("send_cnp", False))

        # UEC
        uec = stream_data.get("protocol_data", {}).get("uec", {})
        self.uec_qp_start_field.setText(uec.get("qp_start", "1000"))
        self.uec_qp_end_field.setText(uec.get("qp_end", "1010"))
        self.uec_pasid_start_field.setText(uec.get("pasid_start", "5000"))
        self.uec_pasid_end_field.setText(uec.get("pasid_end", "5010"))
        self.uec_ecn_combo_box.setCurrentText(uec.get("ecn", "Not-ECT"))
        self.uec_flow_label_field.setText(uec.get("flow_label", "0"))
        self.uec_enable_spray_checkbox.setChecked(uec.get("enable_spray", False))
        self.uec_enable_rocev2_checkbox.setChecked(uec.get("enable_rocev2", False))

        # Payload
        payload_value = stream_data.get("Payload", "None")
        self.payload_none.setChecked(payload_value == "None")
        self.payload_pattern.setChecked(payload_value == "Pattern")
        # Map "Hex Dump" to "From File" for backward compatibility
        self.payload_from_file.setChecked(payload_value == "Hex Dump" or payload_value == "From File")
        # payload_random widget removed in v0.3.11 (dead) — silently
        # ignore stored "Random" payloads; they fall through to None.
        # v0.3.11 round-trip: restore payload bytes from the new
        # protocol_data["payload"] slot that _collect now persists.
        _payload_pd = stream_data.get("protocol_data", {}).get("payload", {})
        if hasattr(self, "payload_data_field"):
            self.payload_data_field.setText(
                str(_payload_pd.get("payload_data", "0000"))
            )

        # IPv4 detailed
        ipv4_data = stream_data.get("protocol_data", {}).get("ipv4", {})
        self.source_field.setText(ipv4_data.get("ipv4_source", "10.0.0.1"))
        self.destination_field.setText(ipv4_data.get("ipv4_destination", "11.0.0.2"))
        self.ttl_field.setText(ipv4_data.get("ipv4_ttl", "64"))
        self.identification_field.setText(ipv4_data.get("ipv4_identification", "0000"))
        self.df_checkbox.setChecked(ipv4_data.get("ipv4_df", False))
        self.mf_checkbox.setChecked(ipv4_data.get("ipv4_mf", False))
        self.fragment_offset_field.setText(ipv4_data.get("ipv4_fragment_offset", "0"))

        src_mode = ipv4_data.get("ipv4_source_mode", "Fixed")
        self.source_mode_dropdown.setCurrentText(src_mode)
        self.source_increment_step.setEnabled(src_mode == "Increment")
        self.source_increment_count.setEnabled(src_mode == "Increment")
        self.source_increment_step.setText(ipv4_data.get("ipv4_source_increment_step", "1"))
        self.source_increment_count.setText(ipv4_data.get("ipv4_source_increment_count", "1"))

        dst_mode = ipv4_data.get("ipv4_destination_mode", "Fixed")
        self.destination_mode_dropdown.setCurrentText(dst_mode)
        self.destination_increment_step.setEnabled(dst_mode == "Increment")
        self.destination_increment_count.setEnabled(dst_mode == "Increment")
        self.destination_increment_step.setText(ipv4_data.get("ipv4_destination_increment_step", "1"))
        self.destination_increment_count.setText(ipv4_data.get("ipv4_destination_increment_count", "1"))

        tos_dscp_mode = ipv4_data.get("tos_dscp_mode", "TOS")
        self.tos_dscp_custom_mode.setCurrentText(tos_dscp_mode)
        if tos_dscp_mode == "TOS":
            self.tos_dropdown.setCurrentText(ipv4_data.get("ipv4_tos", "Routine"))
        elif tos_dscp_mode == "DSCP":
            self.dscp_dropdown.setCurrentText(ipv4_data.get("ipv4_dscp", "cs0"))
            self.ecn_dropdown.setCurrentText(ipv4_data.get("ipv4_ecn", "Not-ECT"))
        elif tos_dscp_mode == "Custom":
            self.custom_tos_field.setText(ipv4_data.get("ipv4_custom_tos", ""))
        self.ecn_dropdown.setCurrentText(ipv4_data.get("ipv4_ecn", "Not-ECT"))

        # IPv6 detailed
        ipv6_data = stream_data.get("protocol_data", {}).get("ipv6", {})
        self.ipv6_source_field.setText(ipv6_data.get("ipv6_source", "2001:db8::1"))
        s_mode = ipv6_data.get("ipv6_source_mode", "Fixed")
        self.ipv6_source_mode_dropdown.setCurrentText(s_mode)
        self.ipv6_source_increment_step.setEnabled(s_mode == "Increment")
        self.ipv6_source_increment_count.setEnabled(s_mode == "Increment")
        self.ipv6_source_increment_step.setText(ipv6_data.get("ipv6_source_increment_step", "1"))
        self.ipv6_source_increment_count.setText(ipv6_data.get("ipv6_source_increment_count", "1"))
        self.ipv6_destination_field.setText(ipv6_data.get("ipv6_destination", "2001:db8::2"))
        d_mode = ipv6_data.get("ipv6_destination_mode", "Fixed")
        self.ipv6_destination_mode_dropdown.setCurrentText(d_mode)
        self.ipv6_destination_increment_step.setEnabled(d_mode == "Increment")
        self.ipv6_destination_increment_count.setEnabled(d_mode == "Increment")
        self.ipv6_destination_increment_step.setText(ipv6_data.get("ipv6_destination_increment_step", "1"))
        self.ipv6_destination_increment_count.setText(ipv6_data.get("ipv6_destination_increment_count", "1"))
        self.ipv6_traffic_class_field.setText(ipv6_data.get("ipv6_traffic_class", "0"))
        self.ipv6_flow_label_field.setText(ipv6_data.get("ipv6_flow_label", "0"))
        self.ipv6_hop_limit_field.setText(ipv6_data.get("ipv6_hop_limit", "64"))

        # Rate/Duration
        self.rate_type_dropdown.setCurrentText(stream_data.get("stream_rate_type", "Packets Per Second (PPS)"))
        self.stream_pps_rate.setText(stream_data.get("stream_pps_rate", "1000"))
        self.stream_bit_rate.setText(stream_data.get("stream_bit_rate", "100"))
        self.stream_load_percentage.setText(stream_data.get("stream_load_percentage", "50"))

        duration_mode = stream_data.get("stream_duration_mode", "Continuous")
        self.duration_mode_dropdown.setCurrentText(duration_mode)
        if duration_mode == "Seconds":
            self.stream_duration_field.setText(stream_data.get("stream_duration_seconds", "10"))
        else:
            self.stream_duration_field.clear()

        # RX port
        rx_port_value = stream_data.get("rx_port", "Same as TX Port")
        idx = self.rx_port_dropdown.findText(rx_port_value)
        if idx != -1:
            self.rx_port_dropdown.setCurrentIndex(idx)
        else:
            self.rx_port_dropdown.setCurrentText("Same as TX Port")

        # v0.3.11 round-trip fix: PCAP Replay fields were COLLECTED
        # by get_stream_details (top-level `pcap_stream` dict + a
        # duplicate inside protocol_data) but never RESTORED in
        # populate. Operator edited a stream with PCAP enabled,
        # reopened it, saw every PCAP field reset — silent loss of
        # file path / loop count / rate mode. Try the top-level
        # key first (current shape) then fall back to the
        # protocol_data nested copy (older saves).
        pcap_stream = (
            stream_data.get("pcap_stream")
            or stream_data.get("protocol_data", {}).get("pcap_stream", {})
            or {}
        )
        if hasattr(self, "enable_pcap_checkbox"):
            self.enable_pcap_checkbox.setChecked(
                bool(pcap_stream.get("pcap_enabled", False))
            )
        if hasattr(self, "pcap_file_path"):
            self.pcap_file_path.setText(
                str(pcap_stream.get("pcap_file_path", ""))
            )
        if hasattr(self, "pcap_loop_count"):
            try:
                self.pcap_loop_count.setValue(
                    int(pcap_stream.get("pcap_loop_count", 1))
                )
            except (TypeError, ValueError):
                self.pcap_loop_count.setValue(1)
        if hasattr(self, "pcap_rate_mode"):
            self.pcap_rate_mode.setCurrentText(
                str(pcap_stream.get("pcap_rate_mode", "Original Timing"))
            )

        QTimer.singleShot(0, self.refresh_l4_sections)

    # ----------------------------- Build stream dict -----------------------------

    def _selected_l1(self):
        # L1 group removed in v0.3.11 (was dead UI — value was
        # written but no packet builder ever read it). Hard-coded
        # to "None" so saved stream shape stays unchanged.
        return "None"

    def _selected_vlan(self):
        if self.vlan_tagged.isChecked(): return "Tagged"
        if self.vlan_stacked.isChecked(): return "Stacked"
        return "Untagged"

    def _selected_l2(self):
        if self.l2_ethernet.isChecked(): return "Ethernet II"
        if self.l2_mpls.isChecked(): return "MPLS"
        return "None"

    def _selected_l3(self):
        if self.l3_ipv4.isChecked(): return "IPv4"
        if self.l3_ipv6.isChecked(): return "IPv6"
        if self.l3_arp.isChecked():  return "ARP"
        return "None"

    def _selected_l4(self):
        # Check both L4 groups
        if self.l4_tcp.isChecked():    return "TCP"
        if self.l4_udp.isChecked():    return "UDP"
        if self.l4_rocev2.isChecked(): return "RoCEv2"
        if self.l4_uec.isChecked():    return "UEC"
        if self.l4_icmp.isChecked():   return "ICMP"
        if self.l4_igmp.isChecked():   return "IGMP"
        return "None"

    def _selected_payload(self):
        if self.payload_pattern.isChecked(): return "Pattern"
        if self.payload_from_file.isChecked(): return "From File"  # Map to "Hex Dump" for backward compatibility if needed
        # payload_random removed in v0.3.11 (dead — no encoder path).
        return "None"

    def _collect_vlan_pd(self):
        return {
            "vlan_id": self.vlan_id_field.text().strip() or "1",
            "vlan_priority": self.priority_field.currentText(),
            "vlan_cfi_dei": self.cfi_dei_field.currentText(),
            "vlan_tpid": self.tpid_field.text().strip() or "81 00",
            "vlan_increment": self.vlan_increment_checkbox.isChecked(),
            "vlan_increment_value": self.vlan_increment_value.text().strip() or "1",
            "vlan_increment_count": self.vlan_increment_count.text().strip() or "1",
        }

    def _collect_ipv4_pd(self):
        return {
            "ipv4_source": self.source_field.text().strip() or "0.0.0.0",
            "ipv4_source_mode": self.source_mode_dropdown.currentText(),
            "ipv4_source_increment_step": self.source_increment_step.text().strip() or "1",
            "ipv4_source_increment_count": self.source_increment_count.text().strip() or "1",
            "ipv4_destination": self.destination_field.text().strip() or "0.0.0.0",
            "ipv4_destination_mode": self.destination_mode_dropdown.currentText(),
            "ipv4_destination_increment_step": self.destination_increment_step.text().strip() or "1",
            "ipv4_destination_increment_count": self.destination_increment_count.text().strip() or "1",
            "ipv4_ttl": self.ttl_field.text().strip() or "64",
            "ipv4_df": self.df_checkbox.isChecked(),
            "ipv4_mf": self.mf_checkbox.isChecked(),
            "ipv4_fragment_offset": self.fragment_offset_field.text().strip() or "0",
            "ipv4_identification": self.identification_field.text().strip() or "0000",
            "tos_dscp_mode": self.tos_dscp_custom_mode.currentText(),
            "ipv4_tos": self.tos_dropdown.currentText() if self.tos_dscp_custom_mode.currentText() == "TOS" else "",
            "ipv4_dscp": self.dscp_dropdown.currentText() if self.tos_dscp_custom_mode.currentText() == "DSCP" else "",
            "ipv4_custom_tos": self.custom_tos_field.text().strip() if self.tos_dscp_custom_mode.currentText() == "Custom" else "",
            "ipv4_ecn": self.ecn_dropdown.currentText(),
        }

    def _collect_ipv6_pd(self):
        return {
            "ipv6_source": self.ipv6_source_field.text().strip() or "2001:db8::1",
            "ipv6_source_mode": self.ipv6_source_mode_dropdown.currentText(),
            "ipv6_source_increment_step": self.ipv6_source_increment_step.text().strip() or "1",
            "ipv6_source_increment_count": self.ipv6_source_increment_count.text().strip() or "1",
            "ipv6_destination": self.ipv6_destination_field.text().strip() or "2001:db8::2",
            "ipv6_destination_mode": self.ipv6_destination_mode_dropdown.currentText(),
            "ipv6_destination_increment_step": self.ipv6_destination_increment_step.text().strip() or "1",
            "ipv6_destination_increment_count": self.ipv6_destination_increment_count.text().strip() or "1",
            "ipv6_traffic_class": self.ipv6_traffic_class_field.text().strip() or "0",
            "ipv6_flow_label": self.ipv6_flow_label_field.text().strip() or "0",
            "ipv6_hop_limit": self.ipv6_hop_limit_field.text().strip() or "64",
        }

    def _collect_arp_pd(self):
        """Collect ARP fields."""
        return {
            "arp_operation": self.arp_operation.currentText() if hasattr(self, "arp_operation") else "Request",
            "arp_sender_mac": self.arp_sender_mac.text().strip() if hasattr(self, "arp_sender_mac") else "",
            "arp_sender_ip": self.arp_sender_ip.text().strip() if hasattr(self, "arp_sender_ip") else "0.0.0.0",
            "arp_target_mac": self.arp_target_mac.text().strip() if hasattr(self, "arp_target_mac") else "",
            "arp_target_ip": self.arp_target_ip.text().strip() if hasattr(self, "arp_target_ip") else "0.0.0.0",
        }
    def _collect_tcp_pd(self):
        flags = []
        if self.flag_urg.isChecked(): flags.append("URG")
        if self.flag_ack.isChecked(): flags.append("ACK")
        if self.flag_psh.isChecked(): flags.append("PSH")
        if self.flag_rst.isChecked(): flags.append("RST")
        if self.flag_syn.isChecked(): flags.append("SYN")
        if self.flag_fin.isChecked(): flags.append("FIN")
        return {
            "tcp_source_port": self.source_port_field.text().strip() or "0",
            "tcp_source_port_step": self.tcp_source_increment_step.text().strip() or "1",
            "tcp_source_port_count": self.tcp_source_increment_count.text().strip() or "1",
            "tcp_increment_source_port": self.increment_tcp_source_checkbox.isChecked(),
            "tcp_destination_port": self.destination_port_field.text().strip() or "0",
            "tcp_destination_port_step": self.tcp_destination_increment_step.text().strip() or "1",
            "tcp_destination_port_count": self.tcp_destination_increment_count.text().strip() or "1",
            "tcp_increment_destination_port": self.increment_tcp_destination_checkbox.isChecked(),
            "tcp_sequence_number": self.sequence_number_field.text().strip() or "0",
            "tcp_acknowledgement_number": self.acknowledgement_number_field.text().strip() or "0",
            "tcp_window": self.window_field.text().strip() or "1024",
            "tcp_checksum": self.tcp_checksum_field.text().strip(),
            "tcp_flags": ",".join(flags),
        }

    def _collect_udp_pd(self):
        return {
            "udp_source_port": self.udp_source_port_field.text().strip() or "0",
            "udp_source_port_step": self.udp_source_increment_step.text().strip() or "1",
            "udp_source_port_count": self.udp_source_increment_count.text().strip() or "1",
            "udp_increment_source_port": self.udp_increment_source_checkbox.isChecked(),
            "udp_destination_port": self.udp_destination_port_field.text().strip() or "0",
            "udp_destination_port_step": self.udp_destination_increment_step.text().strip() or "1",
            "udp_destination_port_count": self.udp_destination_increment_count.text().strip() or "1",
            "udp_increment_destination_port": self.udp_increment_destination_checkbox.isChecked(),
            "udp_checksum": self.udp_checksum_field.text().strip(),
            "udp_preset": self.udp_preset_combo.currentText(),
            "udp_bootp_enabled": self.udp_bootp_enable_checkbox.isChecked(),
            "bootp_msg_type": self.bootp_msg_type.currentText(),
            "bootp_xid": self.bootp_xid.text().strip(),
            "bootp_client_mac": self.bootp_client_mac.text().strip(),
            "bootp_flags": self.bootp_flags.text().strip(),
            "bootp_ciaddr": self.bootp_addrs["ciaddr"].text().strip(),
            "bootp_yiaddr": self.bootp_addrs["yiaddr"].text().strip(),
            "bootp_siaddr": self.bootp_addrs["siaddr"].text().strip(),
            "bootp_giaddr": self.bootp_addrs["giaddr"].text().strip(),
            "bootp_hostname": self.bootp_hostname.text().strip(),
            "bootp_prl": self.bootp_prl.text().strip(),
        }

    def _collect_rocev2_pd(self):
        return {
            "rocev2_traffic_class": self.rocev2_traffic_class.currentText(),
            "rocev2_flow_label": self.rocev2_flow_label.text().strip() or "000000",
            "rocev2_source_gid": self.rocev2_source_gid.text().strip(),
            "rocev2_destination_gid": self.rocev2_destination_gid.text().strip(),
            "rocev2_increment_source_gid": self.rocev2_increment_source_gid.isChecked(),
            "rocev2_source_gid_step": self.rocev2_source_gid_step.text().strip() or "1",
            "rocev2_increment_destination_gid": self.rocev2_increment_destination_gid.isChecked(),
            "rocev2_destination_gid_step": self.rocev2_destination_gid_step.text().strip() or "1",
            "rocev2_source_qp": self.rocev2_source_qp.text().strip() or "0",
            "rocev2_destination_qp": self.rocev2_destination_qp.text().strip() or "0",
            "rocev2_opcode": self.rocev2_opcode.currentText(),
            "rocev2_solicited_event": self.rocev2_solicited_event.isChecked(),
            "rocev2_migration_req": self.rocev2_migration_req.isChecked(),
            "rocev2_qp_count": self.rocev2_qp_count.text().strip() or "1",
            "rocev2_qp_increment": self.rocev2_qp_increment.isChecked(),
            "rocev2_qp_increment_step": self.rocev2_qp_increment_step.text().strip() or "1",
            "rocev2_gid_source_mode": self.rocev2_gid_source_mode.currentText(),
            "rocev2_gid_source_step": self.rocev2_gid_source_step.text().strip() or "1",
            "rocev2_gid_source_count": self.rocev2_gid_source_count.text().strip() or "1",
            "rocev2_gid_destination_mode": self.rocev2_gid_destination_mode.currentText(),
            "rocev2_gid_destination_step": self.rocev2_gid_destination_step.text().strip() or "1",
            "rocev2_gid_destination_count": self.rocev2_gid_destination_count.text().strip() or "1",
            "send_cnp": self.rocev2_send_cnp.isChecked(),
            # v0.3.11 round-trip fix: this checkbox was BUILT in
            # setup but never collected — operator ticked it, saved,
            # reopened, and the box was empty again. Silent loss.
            "rocev2_use_perf_server": (
                self.rocev2_use_perf_server.isChecked()
                if hasattr(self, "rocev2_use_perf_server") else False
            ),
        }

    def _collect_uec_pd(self):
        return {
            "qp_start": self.uec_qp_start_field.text().strip() or "1000",
            "qp_end": self.uec_qp_end_field.text().strip() or "1010",
            "pasid_start": self.uec_pasid_start_field.text().strip() or "5000",
            "pasid_end": self.uec_pasid_end_field.text().strip() or "5010",
            "ecn": self.uec_ecn_combo_box.currentText(),
            "flow_label": self.uec_flow_label_field.text().strip() or "0",
            "enable_spray": self.uec_enable_spray_checkbox.isChecked(),
            "enable_rocev2": self.uec_enable_rocev2_checkbox.isChecked(),
        }

    def _collect_mac_pd(self):
        return {
            "mac_destination_mode": self.mac_destination_mode.currentText(),
            "mac_destination_address": self.mac_destination_address.text().strip(),
            "mac_destination_count": self.mac_destination_count.text().strip() or "16",
            "mac_destination_step": self.mac_destination_step.text().strip() or "1",
            "mac_source_mode": self.mac_source_mode.currentText(),
            "mac_source_address": self.mac_source_address.text().strip(),
            "mac_source_count": self.mac_source_count.text().strip() or "16",
            "mac_source_step": self.mac_source_step.text().strip() or "1",
        }

    def get_stream_details(self):
        """Collect all dialog fields into a single stream_details dict."""
        # ---------- basics ----------
        name = (self.stream_name.text().strip() if hasattr(self, "stream_name") else "") or "Stream"
        enabled = self.enabled_checkbox.isChecked() if hasattr(self, "enabled_checkbox") else False
        details = self.details_field.text().strip() if hasattr(self, "details_field") else ""
        rx_pick = self.rx_port_dropdown.currentText().strip() if hasattr(self,
                                                                         "rx_port_dropdown") else "Same as TX Port"
        flow_tracking = self.flow_tracking_checkbox.isChecked() if hasattr(self, "flow_tracking_checkbox") else False

        # frame
        frame_type = self.frame_type.currentText() if hasattr(self, "frame_type") else "Fixed"
        frame_min = self.frame_min.text().strip() if hasattr(self, "frame_min") else "64"
        frame_max = self.frame_max.text().strip() if hasattr(self, "frame_max") else "1518"
        frame_size = self.frame_size.text().strip() if hasattr(self, "frame_size") else "64"

        # helpers
        def chosen(pairs):
            for label, w in pairs:
                if hasattr(self, w) and getattr(self, w).isChecked():
                    return label
            return pairs[0][0]

        # v0.3.11: L1 group removed (dead UI). Field stays in
        # stream_data for back-compat with saved sessions.
        L1 = "None"
        VLAN_sel = chosen([("Untagged", "vlan_untagged"), ("Tagged", "vlan_tagged"), ("Stacked", "vlan_stacked")])
        L2 = chosen([("None", "l2_none"), ("Ethernet II", "l2_ethernet"), ("MPLS", "l2_mpls")])
        L3 = chosen([("None", "l3_none"), ("ARP", "l3_arp"), ("IPv4", "l3_ipv4"), ("IPv6", "l3_ipv6")])
        # Check both L4 groups
        L4 = None
        for name, attr in [("ICMP", "l4_icmp"), ("IGMP", "l4_igmp"), ("TCP", "l4_tcp"), ("UDP", "l4_udp")]:
            if getattr(self, attr).isChecked():
                L4 = name
                break
        if not L4:
            for name, attr in [("RoCEv2", "l4_rocev2"), ("UEC", "l4_uec")]:
                if getattr(self, attr).isChecked():
                    L4 = name
                    break
        if not L4:
            L4 = "None"
        # v0.3.11: payload_random removed (dead widget). Note the
        # tuple order now matches the radio button order in
        # setup_protocol_selection_tab (None / Pattern / From File).
        Payload = chosen([("None", "payload_none"), ("Pattern", "payload_pattern"), ("From File", "payload_from_file")])

        # ---------- PCAP ----------
        pcap_stream = {
            "pcap_enabled": getattr(self, "enable_pcap_checkbox", None).isChecked() if hasattr(self,
                                                                                               "enable_pcap_checkbox") else False,
            "pcap_file_path": getattr(self, "pcap_file_path", None).text().strip() if hasattr(self,
                                                                                              "pcap_file_path") else "",
            "pcap_loop_count": getattr(self, "pcap_loop_count", None).value() if hasattr(self,
                                                                                         "pcap_loop_count") else 1,
            "pcap_rate_mode": getattr(self, "pcap_rate_mode", None).currentText() if hasattr(self,
                                                                                             "pcap_rate_mode") else "Original Timing",
        }

        # ---------- protocol_data (only fill what exists to avoid AttributeError) ----------
        protocol_data = {}

        # MAC
        if hasattr(self, "mac_destination_address"):
            protocol_data["mac"] = self._collect_mac_pd()


        # VLAN
        if hasattr(self, "vlan_id_field"):
            protocol_data["vlan"] = {
                "vlan_id": self.vlan_id_field.text().strip(),
                "vlan_priority": self.priority_field.currentText() if hasattr(self, "priority_field") else "0",
                "vlan_cfi_dei": self.cfi_dei_field.currentText() if hasattr(self, "cfi_dei_field") else "0",
                "vlan_increment": self.vlan_increment_checkbox.isChecked() if hasattr(self,
                                                                                      "vlan_increment_checkbox") else False,
                "vlan_increment_value": self.vlan_increment_value.text().strip() if hasattr(self,
                                                                                            "vlan_increment_value") else "1",
                "vlan_increment_count": self.vlan_increment_count.text().strip() if hasattr(self,
                                                                                            "vlan_increment_count") else "1",
                "vlan_tpid": self.tpid_field.text().strip() if hasattr(self, "tpid_field") else "81 00",
            }

        # MPLS
        if hasattr(self, "mpls_label_field"):
            mpls_block = {
                "mpls_label": self.mpls_label_field.text().strip(),
                "mpls_ttl": self.mpls_ttl_field.text().strip(),
                "mpls_experimental": self.mpls_experimental_field.text().strip(),
            }
            # SR-MPLS stack (0.2.65). Persist as a parsed list when the
            # text parses cleanly — that's the canonical shape the
            # generic builder + tests expect. Empty/blank → omit, so
            # legacy single-label streams stay bit-identical.
            stack_text = self.mpls_labels_field.text().strip() \
                if hasattr(self, "mpls_labels_field") else ""
            if stack_text:
                try:
                    from utils.mpls import extract_mpls_labels
                    parsed = extract_mpls_labels({"mpls_labels": stack_text})
                    if parsed:
                        mpls_block["mpls_labels"] = parsed
                except Exception:
                    # Helper raised on bad input — drop the stack and
                    # let the user see the validation feedback elsewhere
                    # (the generic builder will refuse and log).
                    pass
            protocol_data["mpls"] = mpls_block

        # IPv4
        if hasattr(self, "source_field"):
            protocol_data["ipv4"] = {
                "ipv4_source": self.source_field.text().strip(),
                "ipv4_destination": self.destination_field.text().strip() if hasattr(self,
                                                                                     "destination_field") else "0.0.0.0",
                "ipv4_source_mode": self.source_mode_dropdown.currentText() if hasattr(self,
                                                                                       "source_mode_dropdown") else "Fixed",
                "ipv4_source_increment_step": self.source_increment_step.text().strip() if hasattr(self,
                                                                                                   "source_increment_step") else "1",
                "ipv4_source_increment_count": self.source_increment_count.text().strip() if hasattr(self,
                                                                                                     "source_increment_count") else "1",
                "ipv4_destination_mode": self.destination_mode_dropdown.currentText() if hasattr(self,
                                                                                                 "destination_mode_dropdown") else "Fixed",
                "ipv4_destination_increment_step": self.destination_increment_step.text().strip() if hasattr(self,
                                                                                                             "destination_increment_step") else "1",
                "ipv4_destination_increment_count": self.destination_increment_count.text().strip() if hasattr(self,
                                                                                                               "destination_increment_count") else "1",
                "ipv4_ttl": self.ttl_field.text().strip() if hasattr(self, "ttl_field") else "64",
                "ipv4_df": self.df_checkbox.isChecked() if hasattr(self, "df_checkbox") else False,
                "ipv4_mf": self.mf_checkbox.isChecked() if hasattr(self, "mf_checkbox") else False,
                "ipv4_fragment_offset": self.fragment_offset_field.text().strip() if hasattr(self,
                                                                                             "fragment_offset_field") else "0",
                "ipv4_identification": self.identification_field.text().strip() if hasattr(self,
                                                                                           "identification_field") else "0000",
                "tos_dscp_mode": self.tos_dscp_custom_mode.currentText() if hasattr(self,
                                                                                    "tos_dscp_custom_mode") else "TOS",
                "ipv4_tos": self.tos_dropdown.currentText() if hasattr(self, "tos_dropdown") else "Routine",
                "ipv4_dscp": self.dscp_dropdown.currentText() if hasattr(self, "dscp_dropdown") else "cs0",
                "ipv4_custom_tos": self.custom_tos_field.text().strip() if hasattr(self, "custom_tos_field") else "",
                "ipv4_ecn": self.ecn_dropdown.currentText() if hasattr(self, "ecn_dropdown") else "Not-ECT",
            }

        # IPv6
        if hasattr(self, "ipv6_source_field"):
            protocol_data["ipv6"] = {
                "ipv6_source": self.ipv6_source_field.text().strip(),
                "ipv6_destination": self.ipv6_destination_field.text().strip() if hasattr(self,
                                                                                          "ipv6_destination_field") else "2001:db8::2",
                "ipv6_source_mode": self.ipv6_source_mode_dropdown.currentText() if hasattr(self,
                                                                                            "ipv6_source_mode_dropdown") else "Fixed",
                "ipv6_source_increment_step": self.ipv6_source_increment_step.text().strip() if hasattr(self,
                                                                                                        "ipv6_source_increment_step") else "1",
                "ipv6_source_increment_count": self.ipv6_source_increment_count.text().strip() if hasattr(self,
                                                                                                          "ipv6_source_increment_count") else "1",
                "ipv6_destination_mode": self.ipv6_destination_mode_dropdown.currentText() if hasattr(self,
                                                                                                      "ipv6_destination_mode_dropdown") else "Fixed",
                "ipv6_destination_increment_step": self.ipv6_destination_increment_step.text().strip() if hasattr(self,
                                                                                                                  "ipv6_destination_increment_step") else "1",
                "ipv6_destination_increment_count": self.ipv6_destination_increment_count.text().strip() if hasattr(
                    self, "ipv6_destination_increment_count") else "1",
                "ipv6_traffic_class": self.ipv6_traffic_class_field.text().strip() if hasattr(self,
                                                                                              "ipv6_traffic_class_field") else "0",
                "ipv6_flow_label": self.ipv6_flow_label_field.text().strip() if hasattr(self,
                                                                                        "ipv6_flow_label_field") else "0",
                "ipv6_hop_limit": self.ipv6_hop_limit_field.text().strip() if hasattr(self,
                                                                                      "ipv6_hop_limit_field") else "64",
            }

        # TCP
        if hasattr(self, "source_port_field"):
            flags = []
            for label, attr in [("URG", "flag_urg"), ("ACK", "flag_ack"), ("PSH", "flag_psh"),
                                ("RST", "flag_rst"), ("SYN", "flag_syn"), ("FIN", "flag_fin")]:
                if hasattr(self, attr) and getattr(self, attr).isChecked():
                    flags.append(label)
            protocol_data["tcp"] = {
                "tcp_source_port": self.source_port_field.text().strip(),
                "tcp_destination_port": self.destination_port_field.text().strip() if hasattr(self,
                                                                                              "destination_port_field") else "0",
                "tcp_increment_source_port": self.increment_tcp_source_checkbox.isChecked() if hasattr(self,
                                                                                                       "increment_tcp_source_checkbox") else False,
                "tcp_source_port_step": self.tcp_source_increment_step.text().strip() if hasattr(self,
                                                                                                 "tcp_source_increment_step") else "1",
                "tcp_source_port_count": self.tcp_source_increment_count.text().strip() if hasattr(self,
                                                                                                   "tcp_source_increment_count") else "1",
                "tcp_increment_destination_port": self.increment_tcp_destination_checkbox.isChecked() if hasattr(self,
                                                                                                                 "increment_tcp_destination_checkbox") else False,
                "tcp_destination_port_step": self.tcp_destination_increment_step.text().strip() if hasattr(self,
                                                                                                           "tcp_destination_increment_step") else "1",
                "tcp_destination_port_count": self.tcp_destination_increment_count.text().strip() if hasattr(self,
                                                                                                             "tcp_destination_increment_count") else "1",
                "tcp_sequence_number": self.sequence_number_field.text().strip() if hasattr(self,
                                                                                            "sequence_number_field") else "0",
                "tcp_acknowledgement_number": self.acknowledgement_number_field.text().strip() if hasattr(self,
                                                                                                          "acknowledgement_number_field") else "0",
                "tcp_window": self.window_field.text().strip() if hasattr(self, "window_field") else "1024",
                "tcp_checksum": self.tcp_checksum_field.text().strip() if hasattr(self, "tcp_checksum_field") else "",
                "tcp_flags": ", ".join(flags),
            }

        # UDP
        if hasattr(self, "udp_source_port_field"):
            protocol_data["udp"] = {
                "udp_source_port": self.udp_source_port_field.text().strip(),
                "udp_destination_port": self.udp_destination_port_field.text().strip() if hasattr(self,
                                                                                                  "udp_destination_port_field") else "0",
                "udp_increment_source_port": self.udp_increment_source_checkbox.isChecked() if hasattr(self,
                                                                                                       "udp_increment_source_checkbox") else False,
                "udp_source_port_step": self.udp_source_increment_step.text().strip() if hasattr(self,
                                                                                                 "udp_source_increment_step") else "1",
                "udp_source_port_count": self.udp_source_increment_count.text().strip() if hasattr(self,
                                                                                                   "udp_source_increment_count") else "1",
                "udp_increment_destination_port": self.udp_increment_destination_checkbox.isChecked() if hasattr(self,
                                                                                                                 "udp_increment_destination_checkbox") else False,
                "udp_destination_port_step": self.udp_destination_increment_step.text().strip() if hasattr(self,
                                                                                                           "udp_destination_increment_step") else "1",
                "udp_destination_port_count": self.udp_destination_increment_count.text().strip() if hasattr(self,
                                                                                                             "udp_destination_increment_count") else "1",
                "udp_checksum": self.udp_checksum_field.text().strip() if hasattr(self, "udp_checksum_field") else "",
                "udp_preset": self.udp_preset_combo.currentText() if hasattr(self, "udp_preset_combo") else "Custom",
                "udp_bootp_enabled": self.udp_bootp_enable_checkbox.isChecked() if hasattr(self,
                                                                                           "udp_bootp_enable_checkbox") else False,
                "bootp_msg_type": self.bootp_msg_type.currentText() if hasattr(self,
                                                                               "bootp_msg_type") else "DHCPDISCOVER",
                "bootp_xid": self.bootp_xid.text().strip() if hasattr(self, "bootp_xid") else "",
                "bootp_client_mac": self.bootp_client_mac.text().strip() if hasattr(self, "bootp_client_mac") else "",
                "bootp_flags": self.bootp_flags.text().strip() if hasattr(self, "bootp_flags") else "0x0000",
                "bootp_ciaddr": self.bootp_addrs["ciaddr"].text().strip() if hasattr(self,
                                                                                     "bootp_addrs") else "0.0.0.0",
                "bootp_yiaddr": self.bootp_addrs["yiaddr"].text().strip() if hasattr(self,
                                                                                     "bootp_addrs") else "0.0.0.0",
                "bootp_siaddr": self.bootp_addrs["siaddr"].text().strip() if hasattr(self,
                                                                                     "bootp_addrs") else "0.0.0.0",
                "bootp_giaddr": self.bootp_addrs["giaddr"].text().strip() if hasattr(self,
                                                                                     "bootp_addrs") else "0.0.0.0",
                "bootp_hostname": self.bootp_hostname.text().strip() if hasattr(self, "bootp_hostname") else "",
                "bootp_prl": self.bootp_prl.text().strip() if hasattr(self, "bootp_prl") else "",
            }

        # RoCEv2
        if hasattr(self, "rocev2_traffic_class"):
            protocol_data["rocev2"] = {
                "rocev2_traffic_class": self.rocev2_traffic_class.currentText(),
                "rocev2_flow_label": self.rocev2_flow_label.text().strip(),
                "rocev2_source_gid": self.rocev2_source_gid.text().strip(),
                "rocev2_destination_gid": self.rocev2_destination_gid.text().strip(),
                "rocev2_increment_source_gid": self.rocev2_increment_source_gid.isChecked(),
                "rocev2_source_gid_step": self.rocev2_source_gid_step.text().strip(),
                "rocev2_increment_destination_gid": self.rocev2_increment_destination_gid.isChecked(),
                "rocev2_destination_gid_step": self.rocev2_destination_gid_step.text().strip(),
                "rocev2_source_qp": self.rocev2_source_qp.text().strip(),
                "rocev2_destination_qp": self.rocev2_destination_qp.text().strip(),
                "rocev2_opcode": self.rocev2_opcode.currentText(),
                "rocev2_solicited_event": self.rocev2_solicited_event.isChecked(),
                "rocev2_migration_req": self.rocev2_migration_req.isChecked(),
                "rocev2_qp_count": self.rocev2_qp_count.text().strip(),
                "rocev2_qp_increment": self.rocev2_qp_increment.isChecked(),
                "rocev2_qp_increment_step": self.rocev2_qp_increment_step.text().strip(),
                "rocev2_gid_source_mode": self.rocev2_gid_source_mode.currentText(),
                "rocev2_gid_source_step": self.rocev2_gid_source_step.text().strip(),
                "rocev2_gid_source_count": self.rocev2_gid_source_count.text().strip(),
                "rocev2_gid_destination_mode": self.rocev2_gid_destination_mode.currentText(),
                "rocev2_gid_destination_step": self.rocev2_gid_destination_step.text().strip(),
                "rocev2_gid_destination_count": self.rocev2_gid_destination_count.text().strip(),
                "send_cnp": self.rocev2_send_cnp.isChecked() if hasattr(self, "rocev2_send_cnp") else False,
                # v0.3.11 round-trip fix: checkbox was BUILT in setup
                # but never collected — operator ticked it, saved,
                # reopened, and the box was empty again. Silent loss.
                # NOTE: there's also a _collect_rocev2_pd helper with
                # this field added but that helper isn't called from
                # this inline collect path; both are kept in sync as
                # a defense-in-depth measure.
                "rocev2_use_perf_server": (
                    self.rocev2_use_perf_server.isChecked()
                    if hasattr(self, "rocev2_use_perf_server") else False
                ),
            }

        # UEC
        if hasattr(self, "uec_qp_start_field"):
            protocol_data["uec"] = {
                "qp_start": self.uec_qp_start_field.text().strip(),
                "qp_end": self.uec_qp_end_field.text().strip(),
                "pasid_start": self.uec_pasid_start_field.text().strip(),
                "pasid_end": self.uec_pasid_end_field.text().strip(),
                "ecn": self.uec_ecn_combo_box.currentText() if hasattr(self, "uec_ecn_combo_box") else "Not-ECT",
                "flow_label": self.uec_flow_label_field.text().strip() if hasattr(self,
                                                                                  "uec_flow_label_field") else "0",
                "enable_spray": self.uec_enable_spray_checkbox.isChecked() if hasattr(self,
                                                                                      "uec_enable_spray_checkbox") else False,
                "enable_rocev2": self.uec_enable_rocev2_checkbox.isChecked() if hasattr(self,
                                                                                        "uec_enable_rocev2_checkbox") else False,
            }
        # ARP
        if hasattr(self, "arp_group"):
            protocol_data["arp"] = self._collect_arp_pd()

        # Payload — v0.3.11 round-trip fix: the payload data field
        # (hex bytes for Pattern / From File modes) was built in
        # setup but never collected. Operator typed a custom
        # payload, saved, reopened → field reset to default "0000".
        # Silent loss; now persisted under protocol_data["payload"].
        if hasattr(self, "payload_data_field"):
            protocol_data["payload"] = {
                "payload_data": self.payload_data_field.text().strip() or "0000",
            }

        # override flags
        override_settings = {
            "override_source_tcp_port": getattr(self, "override_source_port_checkbox", None).isChecked() if hasattr(
                self, "override_source_port_checkbox") else False,
            "override_destination_tcp_port": getattr(self, "override_destination_port_checkbox",
                                                     None).isChecked() if hasattr(self,
                                                                                  "override_destination_port_checkbox") else False,
            "override_checksum": getattr(self, "override_checksum_checkbox", None).isChecked() if hasattr(self,
                                                                                                          "override_checksum_checkbox") else False,
            "override_source_udp_port": getattr(self, "override_udp_source_port_checkbox", None).isChecked() if hasattr(
                self, "override_udp_source_port_checkbox") else False,
            "override_destination_udp_port": getattr(self, "override_udp_destination_port_checkbox",
                                                     None).isChecked() if hasattr(self,
                                                                                  "override_udp_destination_port_checkbox") else False,
            "override_udp_checksum": getattr(self, "override_udp_checksum_checkbox", None).isChecked() if hasattr(self,
                                                                                                                  "override_udp_checksum_checkbox") else False,
        }

        # rate controls (flat, plus a nested summary)
        rate_type = self.rate_type_dropdown.currentText() if hasattr(self,
                                                                     "rate_type_dropdown") else "Packets Per Second (PPS)"
        pps = (self.stream_pps_rate.text().strip() if hasattr(self, "stream_pps_rate") else "1000")
        br_mbps = (self.stream_bit_rate.text().strip() if hasattr(self, "stream_bit_rate") else "100")
        load_pct = (self.stream_load_percentage.text().strip() if hasattr(self, "stream_load_percentage") else "50")
        duration_mode = self.duration_mode_dropdown.currentText() if hasattr(self,
                                                                             "duration_mode_dropdown") else "Continuous"
        duration_seconds = (self.stream_duration_field.text().strip() if hasattr(self,
                                                                                 "stream_duration_field") else "10") if duration_mode == "Seconds" else None

        # v0.3.12: resolve engine from the combo (authoritative).
        # When absent (test contexts that construct the dialog without
        # the variable-fields tab fully built), fall back to the
        # checkbox state for backward compatibility.
        engine = "scapy"
        if hasattr(self, "engine_combo"):
            engine = (self.engine_combo.currentData() or "scapy")
        elif getattr(self, "dpdk_enable_checkbox", None) and self.dpdk_enable_checkbox.isChecked():
            engine = "dpdk"
        # Compose RDMA sub-dict only when the operator picked rdma —
        # always-empty otherwise so save shape stays compact.
        rdma_cfg = None
        if engine == "rdma" and hasattr(self, "rdma_test_combo"):
            rdma_cfg = {
                "test":         self.rdma_test_combo.currentData() or "send_bw",
                "device":       self.rdma_device_field.text().strip() or "mlx5_0",
                "peer_addr":    self.rdma_peer_field.text().strip(),
                "msg_size":     int(self.rdma_msg_size_spin.value()),
                "qp_count":     int(self.rdma_qp_count_spin.value()),
                "duration":     int(self.rdma_duration_spin.value()),
                "gid_index":    int(self.rdma_gid_index_spin.value()),
                "bidirectional": bool(self.rdma_bidir_check.isChecked()),
            }
        # final object
        stream_details = {
            "name": name,
            "enabled": enabled,
            "details": details,
            "rx_port": rx_pick,  # "Same as TX Port" is OK; caller can replace with TX when needed
            "flow_tracking_enabled": flow_tracking,
            # v0.3.12: engine key is the new authoritative field; keep
            # legacy dpdk_enable in sync so older servers + saved files
            # remain compatible.
            "engine": engine,
            "rdma": rdma_cfg,
            "dpdk_enable": bool(engine == "dpdk"),
            "dpdk_multi_instance": bool(getattr(self, "dpdk_multi_instance_checkbox", None) and self.dpdk_multi_instance_checkbox.isChecked()),
            "dpdk_tx_cores": int(self.dpdk_tx_cores_combo.currentData() or 1) if hasattr(self, "dpdk_tx_cores_combo") else 1,
            "enable_timestamps": bool(getattr(self, "enable_timestamps_checkbox", None) and self.enable_timestamps_checkbox.isChecked()),
            "frame_type": frame_type,
            "frame_min": frame_min,
            "frame_max": frame_max,
            "frame_size": frame_size,

            "L1": L1,
            "VLAN": VLAN_sel,
            "L2": L2,
            "L3": L3,
            "L4": L4,
            "Payload": Payload,

            "pcap_stream": pcap_stream,  # top-level for server-side convenience
            "protocol_data": protocol_data,
            "override_settings": override_settings,

            # keep the historical flat fields too (some code reads these)
            "stream_rate_type": rate_type,
            "stream_pps_rate": pps,
            "stream_bit_rate": br_mbps,
            "stream_load_percentage": load_pct,
            "stream_duration_mode": duration_mode,
            "stream_duration_seconds": duration_seconds if duration_mode == "Seconds" else None,
        }

        if duration_mode == "Seconds":
            stream_details["stream_duration_seconds"] = duration_seconds

        # duplicate a light 'protocol_selection' view (several callers expect it)
        stream_details["protocol_selection"] = {
            "name": name,
            "enabled": enabled,
            "details": details,
            "frame_type": frame_type,
            "frame_min": frame_min,
            "frame_max": frame_max,
            "frame_size": frame_size,
            "L1": L1,
            "VLAN": VLAN_sel,
            "L2": L2,
            "L3": L3,
            "L4": L4,
            "Payload": Payload,
            "flow_tracking_enabled": flow_tracking,
            "dpdk_enable": stream_details["dpdk_enable"],
            "pcap_stream": pcap_stream,  # keep here too for backward-compat populate
        }

        return stream_details

    # ----------------------------- Packet View rendering -----------------------------

    def populate_packet_view(self, stream_data=None):
        self.packet_tree.clear()
        if not isinstance(stream_data, dict):
            return

        # Show engine selection
        engine_label = "DPDK (tx_worker)" if stream_data.get("dpdk_enable") else "Scapy / Kernel"
        if stream_data.get("dpdk_enable"):
            try:
                tx_cores = int(stream_data.get("dpdk_tx_cores") or 1)
            except (TypeError, ValueError):
                tx_cores = 1
            if tx_cores > 1:
                engine_label = f"DPDK (tx_worker) — {tx_cores} TX queues"
        engine_item = QTreeWidgetItem(["Engine", engine_label])
        self.packet_tree.addTopLevelItem(engine_item)

        def getpd(section, key, default=None):
            return stream_data.get("protocol_data", {}).get(section, {}).get(key, default)

        # Shortcuts
        L2 = stream_data.get("L2", "None")
        VLAN_sel = stream_data.get("VLAN", "Untagged")
        L3 = stream_data.get("L3", "None")
        L4 = stream_data.get("L4", "None")
        Payload = stream_data.get("Payload", "None")

        # MAC
        if L2 != "None":
            mac_item = QTreeWidgetItem(["MAC (Media Access)", ""])
            mac_item.addChild(QTreeWidgetItem([
                "Destination",
                f"{getpd('mac','mac_destination_mode','Fixed')} - {getpd('mac','mac_destination_address','00:00:00:00:00:00')}"
            ]))
            mac_item.addChild(QTreeWidgetItem([
                "Source",
                f"{getpd('mac','mac_source_mode','Fixed')} - {getpd('mac','mac_source_address','00:00:00:00:00:00')}"
            ]))
            self.packet_tree.addTopLevelItem(mac_item)

        # VLAN
        if VLAN_sel != "Untagged":
            vlan_item = QTreeWidgetItem(["VLAN", f"{VLAN_sel}"])
            vlan_item.addChild(QTreeWidgetItem(["VLAN ID", getpd("vlan", "vlan_id", "1")]))
            vlan_item.addChild(QTreeWidgetItem(["Priority", getpd("vlan", "vlan_priority", "0")]))
            vlan_item.addChild(QTreeWidgetItem(["CFI/DEI", getpd("vlan", "vlan_cfi_dei", "0")]))
            vlan_item.addChild(QTreeWidgetItem(["TPID", getpd("vlan", "vlan_tpid", "81 00")]))
            self.packet_tree.addTopLevelItem(vlan_item)

        # MPLS
        if L2 == "MPLS":
            mpls_item = QTreeWidgetItem(["MPLS", ""])
            mpls_item.addChild(QTreeWidgetItem(["Label", getpd("mpls", "mpls_label", "16")]))
            mpls_item.addChild(QTreeWidgetItem(["TTL", getpd("mpls", "mpls_ttl", "64")]))
            mpls_item.addChild(QTreeWidgetItem(["Experimental", getpd("mpls", "mpls_experimental", "0")]))
            self.packet_tree.addTopLevelItem(mpls_item)

        # L3
        if L3 != "None":
            l3_item = QTreeWidgetItem(["L3 (Network Layer)", L3])
            if L3 == "IPv4":
                l3_item.addChild(QTreeWidgetItem(["Source", getpd("ipv4", "ipv4_source", "0.0.0.0")]))
                l3_item.addChild(QTreeWidgetItem(["Destination", getpd("ipv4", "ipv4_destination", "0.0.0.0")]))
                l3_item.addChild(QTreeWidgetItem(["ToS/DSCP Mode", getpd("ipv4", "tos_dscp_mode", "TOS")]))
                l3_item.addChild(QTreeWidgetItem(["ECN", getpd("ipv4", "ipv4_ecn", "Not-ECT")]))
                l3_item.addChild(QTreeWidgetItem(["TTL", getpd("ipv4", "ipv4_ttl", "64")]))
            elif L3 == "IPv6":
                l3_item.addChild(QTreeWidgetItem(["Source", getpd("ipv6", "ipv6_source", "2001:db8::1")]))
                l3_item.addChild(QTreeWidgetItem(["Destination", getpd("ipv6", "ipv6_destination", "2001:db8::2")]))
                l3_item.addChild(QTreeWidgetItem(["Traffic Class", getpd("ipv6", "ipv6_traffic_class", "0")]))
                l3_item.addChild(QTreeWidgetItem(["Flow Label", getpd("ipv6", "ipv6_flow_label", "0")]))
                l3_item.addChild(QTreeWidgetItem(["Hop Limit", getpd("ipv6", "ipv6_hop_limit", "64")]))
            elif L3 == "ARP":
                l3_item.addChild(QTreeWidgetItem(["Operation", getpd("arp", "arp_operation", "Request")]))
                l3_item.addChild(QTreeWidgetItem(["Sender MAC", getpd("arp", "arp_sender_mac", "00:11:22:33:44:55")]))
                l3_item.addChild(QTreeWidgetItem(["Sender IP", getpd("arp", "arp_sender_ip", "0.0.0.0")]))
                l3_item.addChild(QTreeWidgetItem(["Target MAC", getpd("arp", "arp_target_mac", "ff:ff:ff:ff:ff:ff")]))
                l3_item.addChild(QTreeWidgetItem(["Target IP", getpd("arp", "arp_target_ip", "0.0.0.0")]))
            self.packet_tree.addTopLevelItem(l3_item)

        # L4
        if L4 != "None":
            l4_item = QTreeWidgetItem(["L4 (Transport Layer)", L4])
            if L4 == "TCP":
                l4_item.addChild(QTreeWidgetItem(["Src Port", getpd("tcp", "tcp_source_port", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Dst Port", getpd("tcp", "tcp_destination_port", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Flags", getpd("tcp", "tcp_flags", "") or "—"]))
            elif L4 == "UDP":
                l4_item.addChild(QTreeWidgetItem(["Src Port", getpd("udp", "udp_source_port", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Dst Port", getpd("udp", "udp_destination_port", "0")]))
                preset = getpd("udp", "udp_preset", "Custom")
                l4_item.addChild(QTreeWidgetItem(["Preset", preset]))
                if getpd("udp", "udp_bootp_enabled", False):
                    l4_item.addChild(QTreeWidgetItem(["BOOTP/DHCP", getpd("udp", "bootp_msg_type", "DHCPDISCOVER")]))
            elif L4 == "RoCEv2":
                l4_item.addChild(QTreeWidgetItem(["Traffic Class", getpd("rocev2", "rocev2_traffic_class", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Flow Label", getpd("rocev2", "rocev2_flow_label", "000000")]))
                l4_item.addChild(QTreeWidgetItem(["Src GID", getpd("rocev2", "rocev2_source_gid", "")]))
                l4_item.addChild(QTreeWidgetItem(["Dst GID", getpd("rocev2", "rocev2_destination_gid", "")]))
                l4_item.addChild(QTreeWidgetItem(["Src QP", getpd("rocev2", "rocev2_source_qp", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Dst QP", getpd("rocev2", "rocev2_destination_qp", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Send CNP", "Yes" if getpd("rocev2", "send_cnp", False) else "No"]))
            elif L4 == "UEC":
                l4_item.addChild(QTreeWidgetItem(["QP Range", f"{getpd('uec','qp_start','1000')}–{getpd('uec','qp_end','1010')}"]))
                l4_item.addChild(QTreeWidgetItem(["PASID Range", f"{getpd('uec','pasid_start','5000')}–{getpd('uec','pasid_end','5010')}"]))
                l4_item.addChild(QTreeWidgetItem(["ECN", getpd("uec", "ecn", "Not-ECT")]))
                l4_item.addChild(QTreeWidgetItem(["Flow Label", getpd("uec", "flow_label", "0")]))
                if getpd("uec", "enable_rocev2", False):
                    l4_item.addChild(QTreeWidgetItem(["Embedded", "RoCEv2"]))
            self.packet_tree.addTopLevelItem(l4_item)

        # Payload
        if Payload != "None":
            p = QTreeWidgetItem(["Payload", Payload])
            # v0.3.11 fix: was looking up the wrong key path
            # (`payload_data.data` — never populated). Actual collect
            # writes `protocol_data.payload.payload_data`. Without
            # this fix, custom payload bytes never showed in the
            # Packet View tree even though they were saved correctly.
            p.addChild(QTreeWidgetItem([
                "Data",
                str(stream_data.get("protocol_data", {})
                    .get("payload", {}).get("payload_data", ""))
            ]))
            self.packet_tree.addTopLevelItem(p)
