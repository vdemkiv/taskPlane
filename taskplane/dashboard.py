"""Self-contained document shell for the shared delivery dashboard."""
from __future__ import annotations

from html import escape
from typing import Any

def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

_DOC_VARS = """
  :root{
    --font-sans:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,sans-serif;
    --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    --surface-0:#f0efea;--surface-1:#f7f6f3;--surface-2:#ffffff;
    --border:#dcd9d2;--border-strong:#b6b2aa;--border-danger:#a8331f;
    --line:#b6b2aa;--changed-bg:#eceae4;--accent:#1f1e1c;
    --text-primary:#1f1e1c;--text-secondary:#55524c;
    --text-muted:#8b877f;--text-danger:#a8331f;--bg-danger:#f6e3df;
    --danger:#a8331f;--danger-bg:#f6e3df;
    --text-warning:#8a5a10;--bg-warning:#f8ecd6;
  }
  @media (prefers-color-scheme:dark){
    :root{
      --surface-0:#232220;--surface-1:#2b2a27;--surface-2:#141412;
      --border:#3b3934;--border-strong:#57544e;--border-danger:#e8836d;
      --line:#57544e;--changed-bg:#302e2a;--accent:#f2f1ed;
      --text-primary:#f2f1ed;--text-secondary:#b9b5ad;
      --text-muted:#87837b;--text-danger:#e8836d;--bg-danger:#3a231e;
      --danger:#e8836d;--danger-bg:#3a231e;
      --text-warning:#e0b062;--bg-warning:#3a2f1a;
    }
  }
  html,body{margin:0;background:var(--surface-2);color:var(--text-primary);
    font-family:var(--font-sans);-webkit-font-smoothing:antialiased;}
  .wrap{max-width:940px;margin:0 auto;padding:26px 22px 56px;}
  .sr-only{position:absolute;width:1px;height:1px;overflow:hidden;
    clip:rect(0,0,0,0);}
  .tp-sec{border-top:1px solid var(--border);margin-top:28px;padding-top:14px;}
  .tp-kicker{font-family:var(--font-mono);font-size:10px;letter-spacing:1.6px;
    text-transform:uppercase;color:var(--text-muted);margin:0 0 4px;}
  .tp-lede{font-size:13px;color:var(--text-secondary);line-height:1.65;margin:2px 0 0;}
  #tp-dashboard-freshness-status{position:sticky;top:0;z-index:10;
    padding:9px 14px;border-bottom:1px solid var(--border-strong);
    background:var(--surface-1);color:var(--text-secondary);
    font:600 12px/1.4 var(--font-mono);overflow-wrap:anywhere;}
  #tp-dashboard-freshness-status[data-status="stale"],
  #tp-dashboard-freshness-status[data-status="unverified"]{
    color:var(--text-warning);background:var(--bg-warning);}
  #dashboard-snapshot{box-sizing:border-box;max-width:940px;margin:0 auto;
    padding:26px 22px 56px;overflow:hidden;}
  .tp-binding{display:grid;grid-template-columns:minmax(9rem,auto) minmax(0,1fr);
    gap:6px 14px;margin:10px 0 0;}
  .tp-binding dt{font:600 10.5px/1.4 var(--font-mono);color:var(--text-muted);
    text-transform:uppercase;letter-spacing:.7px;}
  .tp-binding dd{margin:0;font:12px/1.5 var(--font-mono);
    overflow-wrap:anywhere;word-break:break-word;}
  .tp-phase-graph{box-sizing:border-box;max-width:100%;overflow-x:auto;
    overflow-y:hidden;overscroll-behavior-inline:contain;padding:10px 0;}
  .tp-phase-graph svg{display:block;width:100%;height:auto;min-height:104px;
    overflow:visible;}
  .tp-phase-graph text{paint-order:stroke;stroke:var(--surface-1);
    stroke-width:.35px;}
  @media (max-width:767px){
    #dashboard-snapshot{padding:18px 14px 40px;}
    .tp-binding{grid-template-columns:1fr;gap:2px;}
    .tp-binding dd{margin-bottom:7px;}
    .tp-phase-graph svg{width:max(100%,680px);min-width:680px;min-height:112px;}
  }
  hr.pg{border:0;border-top:1px solid var(--border);margin:26px 0 0;}
"""

def standalone_document(fragments: list[str], title: str = "Taskplane delivery") -> str:
    """The paged fragments as ONE self-contained HTML document.

    Every fragment this module emits is written for an inline widget host
    that already defines the palette. Saved to a file it renders as unstyled
    text — so a review whose host could not show widgets inline had no
    legible artifact at all, and the reviewer had to hand-wrap the pages
    before anything could be read. The fragments are embedded VERBATIM; only
    the document shell around them is new.
    """
    body = '<hr class="pg">'.join(fragments or [])
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,'
        'initial-scale=1">\n<title>' + _esc(title) + "</title>\n"
        "<style>" + _DOC_VARS + "</style>\n</head>\n<body>\n"
        '<div class="wrap">\n' + body + "\n</div>\n</body>\n</html>\n"
    )


def report_widget(workspace: str) -> str:
    from taskplane import flow_dashboard
    return flow_dashboard.render(workspace)


def telemetry_sections(model: dict[str, Any]) -> str:
    """Static, escaped telemetry details shared by live and frozen documents."""
    parts = []
    boundary = model.get("final_observation") or {}
    if boundary:
        parts.append('<section class="tp-sec" data-telemetry="final"><h3>'
                     + _esc(boundary.get("label", "Final observation")) + '</h3><p class="tp-lede">'
                     + 'Observed ' + _esc(boundary.get("observed_at", "unknown"))
                     + ' · Cutoff ' + _esc(boundary.get("cutoff", "unknown"))
                     + '. This immutable usage view records a committed transition.</p></section>')
    final_snapshots = model.get("final_snapshots")
    if isinstance(final_snapshots, list) and final_snapshots:
        from .snapshot_retention import final_snapshot_links
        snapshot = model.get("snapshot") or {}
        links = (final_snapshot_links(snapshot.get("workspace"), snapshot.get("root"),
                                      model.get("run"), final_snapshots)
                 if snapshot.get("schema") == "taskplane.dashboard-snapshot/v1"
                 and snapshot.get("run") == model.get("run") else [])
        items = ''.join('<li><a href="' + escape(row["href"], quote=True) + '">'
                        + _esc(row["label"]) + '</a> · Cutoff ' + _esc(row["cutoff"]) + '</li>'
                        for row in links)
        parts.append('<section class="tp-sec" data-telemetry="final-snapshots"><h3>'
                     'Frozen usage snapshots</h3><p class="tp-lede">Original observations remain '
                     'separate from later follow-up. Only verified local artifacts are linked.</p>'
                     + ('<ul>' + items + '</ul>' if items else '<p class="tp-lede">'
                        'Snapshot artifacts unavailable or unverified.</p>') + '</section>')
    observations = model.get("post_completion_observations") or []
    if observations:
        items = ''.join('<li>' + _esc(row.get("at", "Time unknown")) + ' · '
                        + _esc(row.get("kind", "observation")) + ': '
                        + _esc(row.get("note") or row.get("outcome") or "Observed") + '</li>'
                        for row in observations[-32:] if isinstance(row, dict))
        parts.append('<section class="tp-sec" data-telemetry="follow-up"><h3>'
                     'Post-completion observations</h3><p class="tp-lede">Later evidence is separate '
                     'from the accepted phase record.</p><ul>' + items + '</ul></section>')
    storage = model.get("workflow", {}).get("storage") or {}
    store = storage.get("store")
    if store:
        size = store.get("bytes")
        files = store.get("files")
        measured = (f'{size:,} bytes across {files:,} files' if type(size) is int and type(files) is int
                    else 'Unknown size')
        if not store.get("complete"):
            measured += ' · Partial inventory; observed lower bound'
        else:
            measured += ' · Complete inventory'
        parts.append('<section class="tp-sec" data-telemetry="storage"><h3>Whole-store storage</h3>'
                     '<p class="tp-lede">' + _esc(measured) + '. Logical bytes count each path; '
                     'this is separate from the controller JSON capacity.</p>')
        snapshots = store.get("snapshots") or {}
        if snapshots.get("status") == "catalogued":
            parts.append('<dl class="tp-binding">' + ''.join(
                '<dt>' + label + '</dt><dd>' + _esc(f'{snapshots.get(key, 0):,}') + ' bytes</dd>'
                for key, label in (("pinned_bytes", "Pinned evidence"), ("transient_bytes", "Managed transients"),
                                   ("protected_bytes", "Protected snapshots"), ("legacy_bytes", "Preserved legacy snapshots")))
                + '</dl>')
        parts.append('</section>')
    return ''.join(parts)
