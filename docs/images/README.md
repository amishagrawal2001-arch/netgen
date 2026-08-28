# Screenshots for the README

The `../README.md` "Screenshots" section references the six PNGs
below. Drop the actual files in this directory with these exact
filenames and the README renders them inline on GitHub and on the
GitHub Pages site.

| Filename | Suggested content |
|---|---|
| `streams-tab.png` | Streams subtab with a few running streams — Interface / Frame Type / Fixed size / VLAN / L2 / L3 columns populated and the running-count pill (`N running · N total`) visible. |
| `add-stream.png` | Add/Edit Traffic Stream dialog with the six-tab bar visible (Protocol Selection / Protocol Data / Variable Fields / Stream Control / Packet View / PCAP Replay) and the Protocol Stack section (VLAN / L2 / L3 / L4 / Payload / Encap) populated. |
| `stream-statistics.png` | Bottom-panel Stream Statistics tab with a few running streams — Engine (Scapy vs DPDK ×N), TX/RX pps, TX/RX bit rate, latency, loss, Status columns. |
| `live-chart.png` | Bottom-panel Live Chart tab, Aggregate TX Bit Rate over the last 60s, one series per active interface. |
| `devices-tab.png` | Devices tab with a few rows, one Running (green), one Starting (yellow), one Stopped (red). Shows the toolbar (Add / Edit / Delete / Apply / Start / Stop / Ping / ARP / Refresh). |
| `add-device.png` | Add Device wizard mid-way — MAC + IPv4/IPv6 + VLAN fields visible, with the DHCP or BGP checkbox ticked to show a protocol path. |
| `ospf-tab.png` | OSPF subtab with a real Full/Backup neighbor row, both IPv4 + IPv6, showing the Route Pools column and Attach/Detach buttons. |
| `bgp-tab.png` | BGP subtab with one Established neighbor, per-AF row split, and the Neighbor IP / Source IP / Local AS columns populated. |
| `isis-tab.png` | IS-IS subtab with a Level-2 adjacency, Route Pools column populated, Apply progress dialog optional (screenshot mid-apply is nice). |
| `dhcp-tab.png` | DHCP subtab with a Client row (Leased state, lease IP populated) and a Server row (Server Running or Failed with the tooltip visible). |
| `topology-tab.png` | Topology tab with a couple of device cards laid out on the canvas, per-protocol status pills visible on each card, and the legend + Properties panel on the right. |
| `l2emulation-tab.png` | L2 Emulation tab showing the Start / Stop / Refresh toolbar and the empty session table with column headers (Protocol / Interface / VLAN / Frames TX / Uptime / Session ID / Last Error). |
| `stateful-tcp-tab.png` | Stateful TCP tab showing the Start session / Stop / Refresh toolbar and the empty session table with column headers (Role / Protocol / Target / Conns / Bytes TX / RX / Avg RTT / Uptime). |

## How to capture on macOS

1. Launch the netgen client.
2. Get the state you want in view (a real device, a running protocol).
3. Cmd-Shift-4, drag over the window region → drops a PNG on the
   Desktop.
4. Move the PNG to `docs/images/<filename>.png` (use the names above).
5. `git add docs/images/*.png && git commit && git push`.

## How to capture on Linux (turnkey lab-in-a-box)

- GNOME: Screenshot tool → Selection → save as `devices-tab.png`.
- Or `gnome-screenshot -a -f devices-tab.png` for a rectangle capture.

## Size guidance

- Aim for ~1200-1600px wide. Anything larger blows up the GitHub
  Pages page; anything smaller looks blurry on retina displays.
- PNG (not JPG) — the UI is line-art / text, JPG artifacts read as
  broken pixels.
- Keep filenames lowercase-with-dashes so the Markdown references
  match without case-sensitivity surprises on Linux.
