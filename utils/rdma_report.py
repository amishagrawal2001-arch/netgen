"""v0.5.163: shared HTML-report builder for the Blast + Topology
RDMA dialogs.

Operator: "also allow user to generate report for this test both
in via blast test and topology test" — HTML format, scope = all
runs since the dialog opened (append-only session log).

Each dialog maintains a `_run_log: List[Dict]` instance var.
Every completed run (single iteration or one bulk iterate-N
session) appends a dict with the metadata + per-row results.
The Export button passes that list here.

Schema for one run entry:

  {
    "kind":          "blast" | "topology",
    "started_at":    ISO string (operator-provided wall-clock),
    "duration_s":    int (test-param value), -- informational
    "test":          "ib_send_bw" | ... ,
    "params":        {msg_size, qp_count, mtu, tx_depth, gid_index,
                      bidirectional, cpu_util, parallel_workers,
                      iterations},
    "endpoints":     {server: "<host> <hca>", client: "..."} or
                     {pairs: [{idx, server, client}, ...]}
    "rows":          [{label, bw_gbps, msgrate_mpps, lat_avg_us,
                       state}, ...]
    "summary":       {bw_avg, bw_min, bw_max, msgrate_avg, ...} |
                     None
  }

The HTML is self-contained (inline CSS), no external assets.
Operators can email / archive the file as-is.
"""
from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional


def build_html_report(
    *,
    title: str,
    runs: List[Dict[str, Any]],
    generated_at: str,
    client_version: Optional[str] = None,
) -> str:
    """Render the full HTML document. `runs` is the append-only
    session log (oldest first). `generated_at` is the operator-
    side wall-clock at export time (the dialog passes this in so
    the function stays pure / testable)."""
    head = _render_head(title)
    intro = _render_intro(title, generated_at, len(runs), client_version)
    body = "".join(_render_run_section(i, r) for i, r in enumerate(runs))
    if not runs:
        body = (
            "<p class='empty'>No runs recorded yet. Run a test and "
            "click Export Report again.</p>"
        )
    return f"<!DOCTYPE html>\n<html lang='en'><head>{head}</head>" \
           f"<body><main class='container'>{intro}{body}</main>" \
           f"</body></html>"


# ───── HEAD / INTRO ────────────────────────────────────────────────────


def _render_head(title: str) -> str:
    css = """
        :root {
          --fg: #0f172a; --muted: #475569; --line: #cbd5e1;
          --card: #f8fafc; --accent: #1d4ed8; --warn: #b45309;
          --ok: #047857; --err: #b91c1c;
        }
        * { box-sizing: border-box; }
        body {
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                       Roboto, sans-serif;
          color: var(--fg); background: #fff; margin: 0;
          line-height: 1.5;
        }
        .container { max-width: 1100px; margin: 0 auto; padding: 24px; }
        h1 { font-size: 22px; margin: 0 0 4px; }
        h2 {
          font-size: 16px; margin: 24px 0 8px; padding-bottom: 4px;
          border-bottom: 1px solid var(--line);
        }
        h3 { font-size: 14px; margin: 12px 0 6px; color: var(--muted); }
        .meta { color: var(--muted); font-size: 12px; margin-bottom: 16px; }
        .meta strong { color: var(--fg); font-weight: 600; }
        .empty {
          padding: 16px; background: var(--card); border-radius: 6px;
          color: var(--muted); font-style: italic;
        }
        .run-card {
          background: var(--card); border: 1px solid var(--line);
          border-radius: 6px; padding: 16px; margin-bottom: 16px;
        }
        .run-card h2 { margin-top: 0; border: none; }
        .params { display: grid;
          grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
          gap: 6px 12px; font-size: 12px;
        }
        .params dt { color: var(--muted); }
        .params dd {
          margin: 0; font-family: "SF Mono", Menlo, Consolas, monospace;
        }
        table { width: 100%; border-collapse: collapse; margin-top: 8px;
          font-size: 12px;
        }
        th, td { padding: 6px 10px; text-align: left;
          border-bottom: 1px solid var(--line);
        }
        th { color: var(--muted); font-weight: 600; background: #eef2f7; }
        td.num { font-family: "SF Mono", Menlo, Consolas, monospace;
          text-align: right;
        }
        tr.summary { background: #eef2ff; font-weight: 600; }
        tr.summary td { border-top: 2px solid var(--accent); }
        .pill {
          display: inline-block; padding: 1px 8px; border-radius: 10px;
          font-size: 11px; background: #e0e7ff; color: var(--accent);
        }
        footer { color: var(--muted); font-size: 11px; margin-top: 32px;
          padding-top: 12px; border-top: 1px solid var(--line);
        }
    """
    return (
        f"<meta charset='utf-8'>"
        f"<title>{escape(title)}</title>"
        f"<style>{css}</style>"
    )


def _render_intro(
    title: str, generated_at: str, n_runs: int,
    client_version: Optional[str],
) -> str:
    ver = (
        f" &middot; netgen {escape(client_version)}"
        if client_version else ""
    )
    return (
        f"<h1>{escape(title)}</h1>"
        f"<div class='meta'>"
        f"<strong>Generated:</strong> {escape(generated_at)}{ver}"
        f" &middot; <strong>{n_runs} run"
        f"{'s' if n_runs != 1 else ''}</strong>"
        f"</div>"
    )


# ───── PER-RUN SECTION ─────────────────────────────────────────────────


def _render_run_section(idx: int, run: Dict[str, Any]) -> str:
    title = (
        f"Run #{idx + 1}"
        f" &middot; <span class='pill'>{escape(run.get('kind') or '?')}</span>"
        f" &middot; {escape(run.get('test') or '?')}"
    )
    started = run.get("started_at") or "?"
    params = _render_params(run.get("params") or {})
    endpoints = _render_endpoints(run.get("endpoints") or {})
    rows = _render_rows_table(run.get("rows") or [],
                              run.get("summary") or None)
    return (
        f"<section class='run-card'>"
        f"<h2>{title}</h2>"
        f"<div class='meta'>started at {escape(started)}</div>"
        f"<h3>Parameters</h3>{params}"
        f"<h3>Endpoints</h3>{endpoints}"
        f"<h3>Results</h3>{rows}"
        f"</section>"
    )


def _render_params(params: Dict[str, Any]) -> str:
    if not params:
        return "<p class='meta'>—</p>"
    items = "".join(
        f"<dt>{escape(str(k))}</dt>"
        f"<dd>{escape(str(v))}</dd>"
        for k, v in params.items()
    )
    return f"<dl class='params'>{items}</dl>"


def _render_endpoints(eps: Dict[str, Any]) -> str:
    if "pairs" in eps:
        items = "".join(
            f"<li>#{escape(str(p.get('idx', '?')))} "
            f"<code>{escape(str(p.get('server', '?')))}</code> "
            f"&rarr; <code>{escape(str(p.get('client', '?')))}</code>"
            f"</li>"
            for p in eps.get("pairs") or []
        )
        return f"<ul>{items}</ul>" if items else "<p class='meta'>—</p>"
    server = eps.get("server")
    client = eps.get("client")
    if server or client:
        return (
            f"<p><strong>Server:</strong> "
            f"<code>{escape(str(server or '?'))}</code><br>"
            f"<strong>Client:</strong> "
            f"<code>{escape(str(client or '?'))}</code></p>"
        )
    return "<p class='meta'>—</p>"


def _render_rows_table(
    rows: List[Dict[str, Any]],
    summary: Optional[Dict[str, Any]],
) -> str:
    if not rows and not summary:
        return "<p class='meta'>—</p>"
    has_lat = any("lat_avg_us" in r and r["lat_avg_us"] is not None
                  for r in rows)
    if has_lat:
        header = (
            "<tr><th>#</th><th>State</th>"
            "<th class='num'>Iters</th>"
            "<th class='num'>Lat avg (µs)</th>"
            "<th class='num'>Lat p99 (µs)</th></tr>"
        )
        body = "".join(_render_lat_row(r) for r in rows)
        if summary:
            body += _render_lat_summary(summary)
    else:
        header = (
            "<tr><th>#</th><th>State</th>"
            "<th class='num'>BW avg (Gbps)</th>"
            "<th class='num'>MsgRate (Mpps)</th>"
            "<th class='num'>Iters</th></tr>"
        )
        body = "".join(_render_bw_row(r) for r in rows)
        if summary:
            body += _render_bw_summary(summary)
    return f"<table><thead>{header}</thead><tbody>{body}</tbody></table>"


def _render_bw_row(r: Dict[str, Any]) -> str:
    bw = _fmt(r.get("bw_gbps"), 2)
    mr = _fmt(r.get("msgrate_mpps"), 4)
    iters = r.get("iters")
    iters_s = str(iters) if iters is not None else "—"
    return (
        f"<tr><td>{escape(str(r.get('label', '')))}</td>"
        f"<td>{escape(str(r.get('state', '')))}</td>"
        f"<td class='num'>{bw}</td>"
        f"<td class='num'>{mr}</td>"
        f"<td class='num'>{escape(iters_s)}</td></tr>"
    )


def _render_lat_row(r: Dict[str, Any]) -> str:
    avg = _fmt(r.get("lat_avg_us"), 2)
    p99 = _fmt(r.get("lat_p99_us"), 2)
    iters = r.get("iters")
    iters_s = str(iters) if iters is not None else "—"
    return (
        f"<tr><td>{escape(str(r.get('label', '')))}</td>"
        f"<td>{escape(str(r.get('state', '')))}</td>"
        f"<td class='num'>{escape(iters_s)}</td>"
        f"<td class='num'>{avg}</td>"
        f"<td class='num'>{p99}</td></tr>"
    )


def _render_bw_summary(s: Dict[str, Any]) -> str:
    avg = _fmt(s.get("bw_avg_gbps"), 2)
    mn = _fmt(s.get("bw_min_gbps"), 2)
    mx = _fmt(s.get("bw_max_gbps"), 2)
    mr_avg = _fmt(s.get("msgrate_avg_mpps"), 4)
    n = s.get("samples") or s.get("n") or len(s.get("rows") or []) or "?"
    return (
        f"<tr class='summary'><td>Σ</td>"
        f"<td>{escape(str(n))} samples</td>"
        f"<td class='num'>avg {avg} (min {mn}, max {mx})</td>"
        f"<td class='num'>{mr_avg}</td>"
        f"<td class='num'>—</td></tr>"
    )


def _render_lat_summary(s: Dict[str, Any]) -> str:
    avg = _fmt(s.get("lat_avg_us"), 2)
    n = s.get("samples") or s.get("n") or "?"
    return (
        f"<tr class='summary'><td>Σ</td>"
        f"<td>{escape(str(n))} samples</td>"
        f"<td class='num'>—</td>"
        f"<td class='num'>avg {avg}</td>"
        f"<td class='num'>—</td></tr>"
    )


def _fmt(v: Any, places: int) -> str:
    if isinstance(v, (int, float)):
        return f"{float(v):.{places}f}"
    return "—"
