"""
HTML report generator — Bootstrap5 + DataTables.
Incluye TODO el raw data. Nada filtrado por la IA.
"""
import html
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from recrew.config import config
from recrew.context import ReconContext

logger = logging.getLogger("recrew.report")


def generate_report(ctx: ReconContext) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    domain = (ctx.target_url.replace("://", "_")
              .replace("/", "_").replace(":", "_"))[:50]
    out_dir = Path(config.REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"recon_{domain}_{ts}.html"
    path.write_text(_build(ctx, ts), encoding="utf-8")
    logger.info(f"Report: {path}")
    return str(path)


def _e(s) -> str:
    return html.escape(str(s))


def _build(ctx: ReconContext, ts: str) -> str:
    # ---- Rows ----
    p_rows = ""
    for p in ctx.pages:
        bc = ("success" if p.status == 200 else
              "warning" if p.status in (301,302,307) else
              "danger" if p.status >= 400 else "secondary")
        p_rows += (f"<tr><td><a href='{_e(p.url)}' target='_blank'>"
                   f"{_e(p.url)}</a></td>"
                   f"<td><span class='badge bg-{bc}'>{p.status}</span></td>"
                   f"<td>{_e(p.title)}</td><td>{len(p.forms)}</td>"
                   f"<td>{len(p.scripts)+len(p.inline_scripts)}</td>"
                   f"<td>{len(p.comments)}</td></tr>\n")

    ep_rows = ""
    for e in sorted(ctx.endpoints, key=lambda x: (-x.interesting, x.status)):
        bc = ("success" if e.status == 200 else
              "warning" if e.status in (301,302,401,403) else "secondary")
        fire = "🔥 " if e.interesting else ""
        ep_rows += (f"<tr><td><a href='{_e(e.url)}' target='_blank'>"
                    f"{_e(e.url[:90])}</a></td>"
                    f"<td><span class='badge bg-{bc}'>{e.status}</span></td>"
                    f"<td>{fire}{_e(e.notes[:100])}</td>"
                    f"<td>{_e(e.server)}</td></tr>\n")

    sec_rows = ""
    for s in ctx.secrets:
        sec_rows += (f"<tr><td><span class='badge bg-danger'>{_e(s.type)}</span></td>"
                     f"<td><code>{_e(s.value[:90])}</code></td>"
                     f"<td><a href='{_e(s.source_url)}' target='_blank'>"
                     f"{_e(s.source_url[:60])}</a></td>"
                     f"<td><small>{_e(s.context_snippet[:120])}</small></td></tr>\n")

    js_rows = ""
    for js in ctx.js_files:
        js_rows += (f"<tr><td><a href='{_e(js.url)}' target='_blank'>"
                    f"{_e(js.url[:70])}</a></td>"
                    f"<td>{len(js.endpoints)}</td><td>{len(js.api_calls)}</td>"
                    f"<td>{len(js.comments)}</td></tr>\n")

    form_rows = ""
    for pg in ctx.pages:
        for f in pg.forms:
            fnames = ", ".join(x.get("name","") for x in f.get("fields",[]) if x.get("name"))
            form_rows += (f"<tr><td><a href='{_e(pg.url)}' target='_blank'>"
                          f"{_e(pg.url[:50])}</a></td>"
                          f"<td>{_e(f.get('action','')[:50])}</td>"
                          f"<td><span class='badge bg-primary'>{f.get('method','GET')}</span></td>"
                          f"<td>{len(f.get('fields',[]))}</td>"
                          f"<td><small>{_e(fnames[:80])}</small></td></tr>\n")

    comments_html = "".join(
        f"<div class='cmtblk'><code>{_e(c[:400])}</code>"
        f"<small class='d-block text-muted mt-1'>→ <a href='{_e(pg.url)}'>"
        f"{_e(pg.url)}</a></small></div>"
        for pg in ctx.pages for c in pg.comments
    ) or "<span class='text-muted'>No HTML comments found.</span>"

    all_js_eps = sorted({ep for js in ctx.js_files for ep in js.endpoints})
    ep_badges = "".join(f"<span class='epb'>{_e(ep)}</span>" for ep in all_js_eps)

    # Raw JSON (cap 200KB)
    raw_json = json.dumps({
        "target": ctx.target_url, "generated": ts,
        "pages": [asdict(p) for p in ctx.pages],
        "endpoints": [asdict(e) for e in ctx.endpoints],
        "secrets": [asdict(s) for s in ctx.secrets],
        "js_files": [asdict(j) for j in ctx.js_files],
    }, default=str, indent=2)

    n_int = len([e for e in ctx.endpoints if e.interesting])
    n_forms = sum(len(p.forms) for p in ctx.pages)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReconCrew v4 — {_e(ctx.target_url)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.8/css/dataTables.bootstrap5.min.css">
<style>
:root{{--bg:#0d1117;--bg2:#161b22;--bg3:#1c2128;--bg4:#21262d;
--bd:#30363d;--tx:#c9d1d9;--mu:#8b949e;
--gr:#3fb950;--ye:#e3b341;--re:#f85149;--bl:#58a6ff;}}
body{{background:var(--bg);color:var(--tx);font-family:'Segoe UI',system-ui,sans-serif;}}
.navbar{{background:linear-gradient(135deg,#1a1a2e,#16213e);border-bottom:1px solid var(--bd);}}
.card{{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;}}
.card-header{{background:var(--bg3);border-color:var(--bd);}}
.stat-card{{background:var(--bg3);border:1px solid var(--bd);border-radius:8px;padding:20px;text-align:center;}}
.sn{{font-size:2.4rem;font-weight:700;}}
.c-gr{{color:var(--gr)}} .c-ye{{color:var(--ye)}} .c-re{{color:var(--re)}} .c-bl{{color:var(--bl)}}
table{{color:var(--tx)!important;}}
.dataTables_wrapper,.dataTables_wrapper *{{color:var(--tx);}}
.table-dark{{--bs-table-bg:var(--bg2);--bs-table-border-color:var(--bd);}}
.table-hover tbody tr:hover{{--bs-table-accent-bg:var(--bg4);}}
.nav-pills .nav-link{{color:var(--mu);}}
.nav-pills .nav-link.active{{background:#238636;color:#fff;}}
code{{color:var(--ye);background:var(--bg3);padding:2px 6px;border-radius:3px;}}
a{{color:var(--bl);}}
.cmtblk{{background:var(--bg3);border-left:3px solid var(--bl);padding:10px 14px;margin:6px 0;border-radius:4px;}}
.epb{{display:inline-block;background:var(--bg4);color:var(--bl);padding:2px 8px;margin:3px;border-radius:4px;font-family:monospace;font-size:.82rem;}}
.dataTables_filter input,.dataTables_length select{{background:var(--bg4)!important;color:var(--tx)!important;border:1px solid var(--bd)!important;border-radius:4px;padding:3px 8px;}}
.page-link{{background:var(--bg4);border-color:var(--bd);color:var(--tx);}}
.stitle{{border-left:4px solid #238636;padding-left:10px;}}
</style></head><body>
<nav class="navbar navbar-dark px-4 py-2 mb-4">
<span class="navbar-brand fw-bold">🕵️ ReconCrew v4</span>
<span class="text-muted small"><strong>{_e(ctx.target_url)}</strong> — {ts}</span>
</nav>
<div class="container-fluid px-4">
<div class="row g-3 mb-4">
<div class="col"><div class="stat-card"><div class="sn c-bl">{len(ctx.pages)}</div><div class="text-muted small">Pages</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-gr">{len(ctx.endpoints)}</div><div class="text-muted small">Endpoints</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-ye">{len(ctx.js_files)}</div><div class="text-muted small">JS Files</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-re">{len(ctx.secrets)}</div><div class="text-muted small">Secrets</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-bl">{n_forms}</div><div class="text-muted small">Forms</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-ye">{n_int}</div><div class="text-muted small">🔥 Interesting</div></div></div>
</div>
<ul class="nav nav-pills mb-4" role="tablist">
<li class="nav-item"><button class="nav-link active" data-bs-toggle="pill" data-bs-target="#tp">📄 Pages ({len(ctx.pages)})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#te">🔗 Endpoints ({len(ctx.endpoints)})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#ts">🔑 Secrets {"⚠️ "+str(len(ctx.secrets)) if ctx.secrets else "(0)"}</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tj">⚙️ JS ({len(ctx.js_files)})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tf">📝 Forms ({n_forms})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tc">💬 Comments</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tr">📦 Raw JSON</button></li>
</ul>
<div class="tab-content">
<div class="tab-pane fade show active" id="tp">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">Crawled Pages</h5></div>
<div class="card-body"><table id="tP" class="table table-dark table-hover w-100">
<thead><tr><th>URL</th><th>Status</th><th>Title</th><th>Forms</th><th>Scripts</th><th>Comments</th></tr></thead>
<tbody>{p_rows}</tbody></table></div></div></div>

<div class="tab-pane fade" id="te">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">Probed Endpoints</h5></div>
<div class="card-body"><table id="tE" class="table table-dark table-hover w-100">
<thead><tr><th>URL</th><th>Status</th><th>Notes</th><th>Server</th></tr></thead>
<tbody>{ep_rows}</tbody></table></div></div></div>

<div class="tab-pane fade" id="ts">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">⚠️ Secrets & Sensitive Data</h5></div>
<div class="card-body">
{"<div class='alert alert-success'>No secrets detected.</div>" if not ctx.secrets else ""}
<table id="tS" class="table table-dark table-hover w-100">
<thead><tr><th>Type</th><th>Value</th><th>Source</th><th>Context</th></tr></thead>
<tbody>{sec_rows}</tbody></table></div></div></div>

<div class="tab-pane fade" id="tj">
<div class="card mb-3"><div class="card-header"><h5 class="mb-0 stitle">JavaScript Files</h5></div>
<div class="card-body"><table id="tJ" class="table table-dark table-hover w-100">
<thead><tr><th>File</th><th>Endpoints</th><th>API Calls</th><th>Comments</th></tr></thead>
<tbody>{js_rows}</tbody></table></div></div>
<div class="card"><div class="card-header"><h6 class="mb-0">Discovered JS Endpoints</h6></div>
<div class="card-body">{ep_badges or "<span class='text-muted'>None found</span>"}</div></div></div>

<div class="tab-pane fade" id="tf">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">Discovered Forms</h5></div>
<div class="card-body"><table id="tF" class="table table-dark table-hover w-100">
<thead><tr><th>Page</th><th>Action</th><th>Method</th><th>Fields #</th><th>Field Names</th></tr></thead>
<tbody>{form_rows}</tbody></table></div></div></div>

<div class="tab-pane fade" id="tc">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">HTML Comments</h5></div>
<div class="card-body">{comments_html}</div></div></div>

<div class="tab-pane fade" id="tr">
<div class="card"><div class="card-header d-flex justify-content-between align-items-center">
<h5 class="mb-0 stitle">Raw Data Export</h5>
<button class="btn btn-sm btn-outline-success" onclick="dlJson()">⬇️ Download JSON</button>
</div><div class="card-body">
<pre style="max-height:500px;overflow:auto;color:var(--mu);font-size:.8rem"
>{_e(raw_json[:10000])}{"\\n... (truncated — download for full data)" if len(raw_json)>10000 else ""}</pre>
</div></div></div>
</div></div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.8/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.8/js/dataTables.bootstrap5.min.js"></script>
<script>
const RAW_JSON={json.dumps(raw_json[:300_000])};
$(function(){{
const o={{pageLength:25,language:{{search:"Filter:"}}}};
['#tP','#tE','#tS','#tJ','#tF'].forEach(id=>{{try{{$(id).DataTable(o)}}catch(e){{}}}});
}});
function dlJson(){{
const b=new Blob([RAW_JSON],{{type:'application/json'}});
const a=document.createElement('a');
a.href=URL.createObjectURL(b);
a.download='recrew_{ts}.json';
a.click();
}}
</script></body></html>"""