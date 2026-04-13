"""
HTML report generator v4 — con análisis real.

Mejoras sobre v4.0:
- Sección de Executive Summary (análisis del LLM)
- Tech Stack auto-detectado
- Forms deduplicadas (208 → únicas)
- Comments deduplicados (miles → únicos con frecuencia)
- Key Findings destacados
- Secrets sin false positives
"""
import html
import json
import logging
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from recrew.config import config
from recrew.context import ReconContext

logger = logging.getLogger("recrew.report")

# ── Tech stack patterns ────────────────────────────────────────────────────────
_TECH_PATTERNS = [
    # (regex, nombre, categoría)
    (r"WordPress (\d+\.\d+[\.\d]*)", "WordPress", "CMS"),
    (r"WooCommerce (\d+\.\d+[\.\d]*)", "WooCommerce", "eCommerce"),
    (r"Elementor (\d+\.\d+[\.\d]*)", "Elementor", "Page Builder"),
    (r"Elementor Pro", "Elementor Pro", "Page Builder"),
    (r"Yoast SEO plugin v([\d\.]+)", "Yoast SEO", "SEO"),
    (r"OceanWP", "OceanWP", "WordPress Theme"),
    (r"WP Fastest Cache", "WP Fastest Cache", "Caching"),
    (r"jquery\.min\.js\?ver=([\d\.]+)", "jQuery", "JavaScript Library"),
    (r"wordfence", "Wordfence", "Security"),
    (r"woocommerce-payments", "WooCommerce Payments", "Payment"),
    (r"woo-redsys-gateway", "Redsys Gateway", "Payment"),
    (r"sweetalert2", "SweetAlert2", "UI Library"),
    (r"vue\.js", "Vue.js", "JavaScript Framework"),
    (r"imaxel", "Imaxel", "Photo Editor"),
    (r"GoogleTagManager|GTM-[A-Z0-9]+", "Google Tag Manager", "Analytics"),
    (r"google-analytics", "Google Analytics", "Analytics"),
    (r"cookielaw\.org|OneTrust", "OneTrust", "Cookie Consent"),
    (r"connect\.facebook\.net|fbevents", "Meta Pixel", "Marketing"),
    (r"pintrk|pinimg\.com", "Pinterest Tag", "Marketing"),
    (r"Apache/([\d\.]+)", "Apache", "Web Server"),
    (r"nginx/([\d\.]+)", "Nginx", "Web Server"),
    (r"PHP/([\d\.]+)", "PHP", "Backend"),
]

# ── Comentarios sin valor informativo ─────────────────────────────────────────
_BORING_COMMENTS = {
    "#content", "#primary", "#sidebar-inner", "#right-sidebar",
    "#content-wrap", "#main", "#wrap", "#outer-wrap",
    ".woo-entry-image", ".woo-entry-image-swap", ".product-inner .clr",
    "NUEVO MÍO", ".product-entry-out-of-stock-badge", "WPFC_FOOTER_START",
    "close elementor-menu-cart__wrapper", "#sidebar", "OceanWP CSS",
}

_BORING_COMMENT_PATTERNS = [
    r"^Google Tag Manager for WordPress",
    r"^End Google Tag Manager",
    r"^GTM Container placement",
    r"^Fragmento de código de Google",
    r"^Final del fragmento",
    r"^WP Fastest Cache file was created",  # timestamp + cache info
]


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


# ── Tech stack detection ───────────────────────────────────────────────────────
def _detect_tech_stack(ctx: ReconContext) -> list[dict]:
    """Auto-detecta tecnologías del HTML, headers y scripts."""
    corpus = ""
    # Inline scripts de la primera página (más densos en info)
    if ctx.pages:
        first = ctx.pages[0]
        corpus += first.html[:50_000]
        # Headers del servidor
        server = first.headers.get("server", "")
        powered = first.headers.get("x-powered-by", "")
        corpus += f" {server} {powered}"

    # Comentarios HTML
    all_comments = [c for p in ctx.pages for c in p.comments]
    corpus += " ".join(all_comments[:50])

    # Script URLs
    all_scripts = [s for p in ctx.pages for s in p.scripts]
    corpus += " ".join(all_scripts[:30])

    found: dict[str, dict] = {}
    for pattern, name, category in _TECH_PATTERNS:
        m = re.search(pattern, corpus, re.IGNORECASE)
        if m:
            version = m.group(1) if m.lastindex and m.lastindex >= 1 else ""
            if name not in found:
                found[name] = {"name": name, "category": category, "version": version}

    return sorted(found.values(), key=lambda x: x["category"])


# ── Form deduplication ─────────────────────────────────────────────────────────
def _deduplicate_forms(ctx: ReconContext) -> list[dict]:
    """Devuelve forms únicas con el número de páginas donde aparecen."""
    form_map: dict[str, dict] = {}  # key → {form_data, pages, count}

    for page in ctx.pages:
        for form in page.forms:
            field_sig = ",".join(sorted(
                f.get("name", "") for f in form.get("fields", []) if f.get("name")
            ))
            key = f"{form.get('action','')}|{form.get('method','GET')}|{field_sig}"

            if key not in form_map:
                form_map[key] = {
                    "action": form.get("action", ""),
                    "method": form.get("method", "GET"),
                    "fields": form.get("fields", []),
                    "field_names": field_sig,
                    "pages": [page.url],
                    "count": 1,
                }
            else:
                form_map[key]["count"] += 1
                if page.url not in form_map[key]["pages"]:
                    form_map[key]["pages"].append(page.url)

    return sorted(form_map.values(), key=lambda x: -x["count"])


# ── Comment deduplication ──────────────────────────────────────────────────────
def _deduplicate_comments(ctx: ReconContext) -> list[dict]:
    """Devuelve comentarios únicos con frecuencia, filtrando noise."""
    comment_freq: Counter = Counter()
    comment_example_page: dict[str, str] = {}

    for page in ctx.pages:
        for c in page.comments:
            stripped = c.strip()
            # Filtrar boring
            if stripped in _BORING_COMMENTS:
                continue
            if any(re.match(p, stripped, re.IGNORECASE) for p in _BORING_COMMENT_PATTERNS):
                continue
            if len(stripped) < 4:
                continue
            comment_freq[stripped] += 1
            if stripped not in comment_example_page:
                comment_example_page[stripped] = page.url

    # Ordenar: primero los únicos (más interesantes), luego por frecuencia desc
    result = []
    for comment, freq in comment_freq.most_common():
        # Comentarios que aparecen en TODAS las páginas son boilerplate
        is_boilerplate = freq >= len(ctx.pages) * 0.8
        result.append({
            "text": comment,
            "freq": freq,
            "page": comment_example_page.get(comment, ""),
            "boilerplate": is_boilerplate,
        })

    # Mostrar primero los no-boilerplate
    return sorted(result, key=lambda x: (x["boilerplate"], -x["freq"]))


# ── Key findings extractor ─────────────────────────────────────────────────────
def _extract_key_findings(ctx: ReconContext) -> list[dict]:
    """Extrae hallazgos concretos del raw data."""
    findings = []

    # Login forms
    all_forms = _deduplicate_forms(ctx)
    for form in all_forms:
        fnames = form["field_names"]
        if any(kw in fnames.lower() for kw in ["password", "passwd", "pass"]):
            findings.append({
                "severity": "INFO",
                "icon": "🔐",
                "title": f"Login form at {form['action'][:60]}",
                "detail": f"Fields: {fnames} — Method: {form['method']}",
            })

    # Nonces/tokens in inline scripts
    nonce_re = re.compile(r'"nonce"\s*:\s*"([a-f0-9]{8,})"')
    seen_nonces = set()
    for page in ctx.pages:
        for script in page.inline_scripts:
            for m in nonce_re.finditer(script):
                nonce = m.group(1)
                if nonce not in seen_nonces:
                    seen_nonces.add(nonce)
                    findings.append({
                        "severity": "INFO",
                        "icon": "🔑",
                        "title": f"WP Nonce exposed in page source",
                        "detail": f"Value: {nonce[:16]}... at {page.url[:60]}",
                    })

    # Emails in source
    email_re = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    seen_emails = set()
    for page in ctx.pages:
        for m in email_re.finditer(page.html[:30000]):
            email = m.group(0)
            if email not in seen_emails and not any(
                skip in email for skip in ["@schema.org", "@example", "@google", "@w.org", "@sentry"]
            ):
                seen_emails.add(email)
                findings.append({
                    "severity": "INFO",
                    "icon": "📧",
                    "title": f"Email exposed in source",
                    "detail": f"{email} — at {page.url[:60]}",
                })

    # Interesting JS endpoints (not noise)
    all_js_eps = sorted({ep for js in ctx.js_files for ep in js.endpoints})
    boring_ep = {"null", "yes", "no", "true", "false", "/"}
    for ep in all_js_eps:
        if ep not in boring_ep and len(ep) > 3 and not ep.startswith("${"):
            if any(kw in ep.lower() for kw in ["/api/", "/wp-json/", "/admin", "/ajax"]):
                findings.append({
                    "severity": "INFO",
                    "icon": "🔗",
                    "title": f"API endpoint in JS",
                    "detail": ep,
                })

    # Interesting subdomains
    subdomain_re = re.compile(r'https?://([a-z0-9\-]+\.[a-z0-9\-]+\.[a-z]{2,})')
    seen_subdomains = set()
    base_domain = urlparse(ctx.target_url).netloc
    for page in ctx.pages:
        for m in subdomain_re.finditer(page.html[:20000]):
            sd = m.group(1)
            if sd != base_domain and base_domain.split(".")[-2] in sd:
                if sd not in seen_subdomains:
                    seen_subdomains.add(sd)
                    findings.append({
                        "severity": "INFO",
                        "icon": "🌐",
                        "title": f"Subdomain/related domain discovered",
                        "detail": sd,
                    })

    # Secrets
    for s in ctx.secrets:
        findings.append({
            "severity": "HIGH" if s.type not in ["INTERNAL_IP"] else "LOW",
            "icon": "⚠️",
            "title": f"{s.type} found",
            "detail": f"{s.value[:60]} — Source: {s.source_url[:50]}",
        })

    # Dedup findings by title+detail
    seen = set()
    unique = []
    for f in findings:
        key = f"{f['title']}|{f['detail'][:40]}"
        if key not in seen:
            seen.add(key)
            unique.append(f)

    # Sort: HIGH first, then INFO
    return sorted(unique, key=lambda x: (0 if x["severity"] == "HIGH" else 1, x["title"]))


# ── Main builder ───────────────────────────────────────────────────────────────
def _build(ctx: ReconContext, ts: str) -> str:
    tech_stack = _detect_tech_stack(ctx)
    unique_forms = _deduplicate_forms(ctx)
    unique_comments = _deduplicate_comments(ctx)
    key_findings = _extract_key_findings(ctx)

    n_int = len([e for e in ctx.endpoints if e.interesting])
    n_unique_forms = len(unique_forms)
    n_unique_comments = len([c for c in unique_comments if not c["boilerplate"]])

    # ── HTML rows ──────────────────────────────────────────────────────────────

    # Pages
    p_rows = ""
    for p in ctx.pages:
        bc = ("success" if p.status == 200 else
              "warning" if p.status in (301,302,307) else
              "danger" if p.status >= 400 else "secondary")
        p_rows += (f"<tr><td><a href='{_e(p.url)}' target='_blank'>{_e(p.url)}</a></td>"
                   f"<td><span class='badge bg-{bc}'>{p.status}</span></td>"
                   f"<td>{_e(p.title[:60])}</td>"
                   f"<td>{len(p.forms)}</td>"
                   f"<td>{len(p.scripts)+len(p.inline_scripts)}</td>"
                   f"<td>{len(p.comments)}</td></tr>\n")

    # Endpoints
    ep_rows = ""
    for e in sorted(ctx.endpoints, key=lambda x: (-x.interesting, x.status)):
        bc = ("success" if e.status == 200 else
              "warning" if e.status in (301,302,401,403) else "secondary")
        fire = "🔥 " if e.interesting else ""
        ep_rows += (f"<tr><td><a href='{_e(e.url)}' target='_blank'>{_e(e.url[:90])}</a></td>"
                    f"<td><span class='badge bg-{bc}'>{e.status}</span></td>"
                    f"<td>{fire}{_e(e.notes[:100])}</td>"
                    f"<td>{_e(e.server)}</td></tr>\n")

    # Secrets
    sec_rows = ""
    for s in ctx.secrets:
        sev_color = "danger" if s.type not in ["INTERNAL_IP", "BASIC_AUTH_IN_URL"] else "warning"
        sec_rows += (f"<tr><td><span class='badge bg-{sev_color}'>{_e(s.type)}</span></td>"
                     f"<td><code>{_e(s.value[:90])}</code></td>"
                     f"<td><a href='{_e(s.source_url)}' target='_blank'>{_e(s.source_url[:60])}</a></td>"
                     f"<td><small>{_e(s.context_snippet[:120])}</small></td></tr>\n")

    # JS files
    js_rows = ""
    for js in ctx.js_files:
        js_rows += (f"<tr><td><a href='{_e(js.url)}' target='_blank'>{_e(js.url[:70])}</a></td>"
                    f"<td>{len(js.endpoints)}</td><td>{len(js.api_calls)}</td>"
                    f"<td>{len(js.comments)}</td></tr>\n")

    # Forms DEDUPLICADAS
    form_rows = ""
    for form in unique_forms:
        pages_str = ", ".join(form["pages"][:3])
        if len(form["pages"]) > 3:
            pages_str += f" (+{len(form['pages'])-3} more)"
        badge = f"<span class='badge bg-secondary ms-1'>{form['count']}x</span>"
        form_rows += (f"<tr>"
                      f"<td>{_e(form['action'][:60])}{badge}</td>"
                      f"<td><span class='badge bg-primary'>{form['method']}</span></td>"
                      f"<td>{len(form['fields'])}</td>"
                      f"<td><small>{_e(form['field_names'][:80])}</small></td>"
                      f"<td><small class='text-muted'>{_e(pages_str[:80])}</small></td></tr>\n")

    # Comments DEDUPLICADOS
    comments_html = ""
    for c in unique_comments[:100]:  # cap a 100
        bp_class = "text-muted" if c["boilerplate"] else ""
        freq_badge = (f"<span class='badge bg-secondary ms-2'>{c['freq']}x</span>"
                      if c["freq"] > 1 else "")
        comments_html += (
            f"<div class='cmtblk {bp_class}'>"
            f"<code>{_e(c['text'][:400])}</code>{freq_badge}"
            f"<small class='d-block text-muted mt-1'>→ "
            f"<a href='{_e(c['page'])}'>{_e(c['page'])}</a></small></div>\n"
        )
    if not comments_html:
        comments_html = "<span class='text-muted'>No HTML comments found.</span>"

    # Key findings
    findings_html = ""
    if key_findings:
        for f in key_findings[:30]:
            sev_color = "danger" if f["severity"] == "HIGH" else "info"
            findings_html += (
                f"<div class='finding-card border-{sev_color}'>"
                f"<span class='finding-icon'>{f['icon']}</span>"
                f"<div><strong>{_e(f['title'])}</strong>"
                f"<div class='text-muted small'>{_e(f['detail'])}</div></div>"
                f"</div>\n"
            )
    else:
        findings_html = "<p class='text-muted'>No specific findings extracted.</p>"

    # Tech stack
    tech_html = ""
    categories = {}
    for t in tech_stack:
        cat = t["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(t)

    for cat, items in sorted(categories.items()):
        tech_html += f"<div class='tech-category'><span class='tech-cat-label'>{_e(cat)}</span>"
        for item in items:
            ver = f" <small class='text-muted'>v{item['version']}</small>" if item["version"] else ""
            tech_html += f"<span class='tech-badge'>{_e(item['name'])}{ver}</span>"
        tech_html += "</div>"

    # JS endpoints cloud (filtrado)
    all_js_eps = sorted({ep for js in ctx.js_files for ep in js.endpoints
                        if ep and ep not in {"null","yes","no","true","false","/"}
                        and not ep.startswith("${")})
    ep_badges = "".join(f"<span class='epb'>{_e(ep)}</span>" for ep in all_js_eps[:50])

    # Executive summary del LLM
    summary_html = ""
    if ctx.agent_summary:
        # Convertir markdown básico a HTML
        summary_md = ctx.agent_summary
        summary_md = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', summary_md)
        summary_md = re.sub(r'\*(.+?)\*', r'<em>\1</em>', summary_md)
        summary_md = re.sub(r'^## (.+)$', r'<h5>\1</h5>', summary_md, flags=re.MULTILINE)
        summary_md = re.sub(r'^### (.+)$', r'<h6>\1</h6>', summary_md, flags=re.MULTILINE)
        summary_md = re.sub(r'^- (.+)$', r'<li>\1</li>', summary_md, flags=re.MULTILINE)
        summary_md = re.sub(r'(<li>.*</li>\n?)+', r'<ul>\g<0></ul>', summary_md)
        summary_md = re.sub(r'\n\n', r'<br><br>', summary_md)
        summary_html = f"<div class='summary-content'>{summary_md}</div>"
    else:
        summary_html = "<p class='text-muted'>El agente no generó un resumen. Revisa los logs de consola.</p>"

    # Raw JSON
    raw_json = json.dumps({
        "target": ctx.target_url, "generated": ts,
        "pages": [asdict(p) for p in ctx.pages],
        "endpoints": [asdict(e) for e in ctx.endpoints],
        "secrets": [asdict(s) for s in ctx.secrets],
        "js_files": [asdict(j) for j in ctx.js_files],
    }, default=str, indent=2)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReconCrew v4 — {_e(ctx.target_url)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.8/css/dataTables.bootstrap5.min.css">
<style>
:root{{--bg:#0d1117;--bg2:#161b22;--bg3:#1c2128;--bg4:#21262d;
--bd:#30363d;--tx:#c9d1d9;--mu:#8b949e;
--gr:#3fb950;--ye:#e3b341;--re:#f85149;--bl:#58a6ff;--pu:#bc8cff;}}
body{{background:var(--bg);color:var(--tx);font-family:'Segoe UI',system-ui,sans-serif;}}
.navbar{{background:linear-gradient(135deg,#1a1a2e,#16213e);border-bottom:1px solid var(--bd);}}
.card{{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;}}
.card-header{{background:var(--bg3);border-color:var(--bd);}}
.stat-card{{background:var(--bg3);border:1px solid var(--bd);border-radius:8px;padding:20px;text-align:center;}}
.sn{{font-size:2.4rem;font-weight:700;}}
.c-gr{{color:var(--gr)}} .c-ye{{color:var(--ye)}} .c-re{{color:var(--re)}} 
.c-bl{{color:var(--bl)}} .c-pu{{color:var(--pu)}}
table{{color:var(--tx)!important;}}
.dataTables_wrapper,.dataTables_wrapper *{{color:var(--tx);}}
.table-dark{{--bs-table-bg:var(--bg2);--bs-table-border-color:var(--bd);}}
.table-hover tbody tr:hover{{--bs-table-accent-bg:var(--bg4);}}
.nav-pills .nav-link{{color:var(--mu);}}
.nav-pills .nav-link.active{{background:#238636;color:#fff;}}
code{{color:var(--ye);background:var(--bg3);padding:2px 6px;border-radius:3px;word-break:break-all;}}
a{{color:var(--bl);}}
.cmtblk{{background:var(--bg3);border-left:3px solid var(--bl);padding:10px 14px;margin:6px 0;border-radius:4px;}}
.cmtblk.text-muted{{border-left-color:var(--bd);opacity:0.6;}}
.epb{{display:inline-block;background:var(--bg4);color:var(--bl);padding:2px 8px;margin:3px;border-radius:4px;font-family:monospace;font-size:.82rem;}}
.dataTables_filter input,.dataTables_length select{{background:var(--bg4)!important;color:var(--tx)!important;border:1px solid var(--bd)!important;border-radius:4px;padding:3px 8px;}}
.page-link{{background:var(--bg4);border-color:var(--bd);color:var(--tx);}}
.stitle{{border-left:4px solid #238636;padding-left:10px;}}
/* Summary */
.summary-content{{background:var(--bg3);padding:20px;border-radius:8px;line-height:1.8;border-left:4px solid var(--bl);}}
.summary-content ul{{padding-left:20px;margin:8px 0;}}
.summary-content li{{margin:4px 0;}}
/* Key findings */
.finding-card{{display:flex;align-items:flex-start;gap:12px;background:var(--bg3);border-left:4px solid;padding:12px 16px;margin:8px 0;border-radius:4px;}}
.finding-card.border-danger{{border-left-color:var(--re)!important;}}
.finding-card.border-info{{border-left-color:var(--bl)!important;}}
.finding-icon{{font-size:1.4rem;flex-shrink:0;}}
/* Tech stack */
.tech-category{{margin-bottom:12px;}}
.tech-cat-label{{display:inline-block;color:var(--mu);font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;min-width:140px;}}
.tech-badge{{display:inline-block;background:var(--bg4);color:var(--tx);border:1px solid var(--bd);padding:3px 10px;border-radius:20px;font-size:.83rem;margin:3px;}}
</style></head><body>
<nav class="navbar navbar-dark px-4 py-2 mb-4">
<span class="navbar-brand fw-bold">🕵️ ReconCrew v4</span>
<span class="text-muted small"><strong>{_e(ctx.target_url)}</strong> — {ts}</span>
</nav>
<div class="container-fluid px-4">

<!-- Stats -->
<div class="row g-3 mb-4">
<div class="col"><div class="stat-card"><div class="sn c-bl">{len(ctx.pages)}</div><div class="text-muted small">Pages</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-gr">{len(ctx.endpoints)}</div><div class="text-muted small">Endpoints</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-ye">{len(ctx.js_files)}</div><div class="text-muted small">JS Files</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-re">{len(ctx.secrets)}</div><div class="text-muted small">Secrets</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-bl">{n_unique_forms}</div><div class="text-muted small">Unique Forms</div></div></div>
<div class="col"><div class="stat-card"><div class="sn c-pu">{len(key_findings)}</div><div class="text-muted small">💡 Findings</div></div></div>
</div>

<!-- Tabs -->
<ul class="nav nav-pills mb-4" role="tablist">
<li class="nav-item"><button class="nav-link active" data-bs-toggle="pill" data-bs-target="#ta">🧠 Analysis</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tf">💡 Findings ({len(key_findings)})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tt">🧰 Tech Stack ({len(tech_stack)})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tp">📄 Pages ({len(ctx.pages)})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#te">🔗 Endpoints ({len(ctx.endpoints)})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#ts">🔑 Secrets {"⚠️ "+str(len(ctx.secrets)) if ctx.secrets else "(0)"}</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tj">⚙️ JS ({len(ctx.js_files)})</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tfo">📝 Forms ({n_unique_forms} unique)</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tc">💬 Comments</button></li>
<li class="nav-item"><button class="nav-link" data-bs-toggle="pill" data-bs-target="#tr">📦 Raw JSON</button></li>
</ul>

<div class="tab-content">

<!-- ANALYSIS TAB -->
<div class="tab-pane fade show active" id="ta">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">🧠 Executive Summary — AI Analysis</h5></div>
<div class="card-body">{summary_html}</div></div>
</div>

<!-- FINDINGS TAB -->
<div class="tab-pane fade" id="tf">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">💡 Key Findings</h5></div>
<div class="card-body">{findings_html}</div></div>
</div>

<!-- TECH STACK TAB -->
<div class="tab-pane fade" id="tt">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">🧰 Detected Technology Stack</h5></div>
<div class="card-body">{tech_html if tech_html else "<p class='text-muted'>No technology detected.</p>"}</div></div>
</div>

<!-- PAGES TAB -->
<div class="tab-pane fade" id="tp">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">Crawled Pages</h5></div>
<div class="card-body"><table id="tP" class="table table-dark table-hover w-100">
<thead><tr><th>URL</th><th>Status</th><th>Title</th><th>Forms</th><th>Scripts</th><th>Comments</th></tr></thead>
<tbody>{p_rows}</tbody></table></div></div>
</div>

<!-- ENDPOINTS TAB -->
<div class="tab-pane fade" id="te">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">Probed Endpoints</h5></div>
<div class="card-body">
{"<div class='alert alert-warning'>El endpoint prober no produjo resultados. Revisa los logs — puede que el Orchestrator no lo haya llamado.</div>" if not ctx.endpoints else ""}
<table id="tE" class="table table-dark table-hover w-100">
<thead><tr><th>URL</th><th>Status</th><th>Notes</th><th>Server</th></tr></thead>
<tbody>{ep_rows}</tbody></table></div></div>
</div>

<!-- SECRETS TAB -->
<div class="tab-pane fade" id="ts">
<div class="card"><div class="card-header"><h5 class="mb-0 stitle">⚠️ Secrets & Sensitive Data</h5></div>
<div class="card-body">
{"<div class='alert alert-success'>No secrets detected.</div>" if not ctx.secrets else ""}
<table id="tS" class="table table-dark table-hover w-100">
<thead><tr><th>Type</th><th>Value</th><th>Source</th><th>Context</th></tr></thead>
<tbody>{sec_rows}</tbody></table></div></div>
</div>

<!-- JS TAB -->
<div class="tab-pane fade" id="tj">
<div class="card mb-3"><div class="card-header"><h5 class="mb-0 stitle">JavaScript Files</h5></div>
<div class="card-body"><table id="tJ" class="table table-dark table-hover w-100">
<thead><tr><th>File</th><th>Endpoints</th><th>API Calls</th><th>Comments</th></tr></thead>
<tbody>{js_rows}</tbody></table></div></div>
<div class="card"><div class="card-header"><h6 class="mb-0">Discovered JS Endpoints</h6></div>
<div class="card-body">{ep_badges or "<span class='text-muted'>None found</span>"}</div></div>
</div>

<!-- FORMS TAB (DEDUPLICADAS) -->
<div class="tab-pane fade" id="tfo">
<div class="card"><div class="card-header">
<h5 class="mb-0 stitle">Forms — {n_unique_forms} unique <small class="text-muted fw-normal">(deduplicadas de {sum(len(p.forms) for p in ctx.pages)} totales)</small></h5></div>
<div class="card-body"><table id="tF" class="table table-dark table-hover w-100">
<thead><tr><th>Action</th><th>Method</th><th>Fields #</th><th>Field Names</th><th>Pages</th></tr></thead>
<tbody>{form_rows}</tbody></table></div></div>
</div>

<!-- COMMENTS TAB (DEDUPLICADOS) -->
<div class="tab-pane fade" id="tc">
<div class="card"><div class="card-header">
<h5 class="mb-0 stitle">HTML Comments — {n_unique_comments} unique <small class="text-muted fw-normal">(boilerplate filtrado)</small></h5></div>
<div class="card-body">{comments_html}</div></div>
</div>

<!-- RAW JSON TAB -->
<div class="tab-pane fade" id="tr">
<div class="card"><div class="card-header d-flex justify-content-between align-items-center">
<h5 class="mb-0 stitle">Raw Data Export</h5>
<button class="btn btn-sm btn-outline-success" onclick="dlJson()">⬇️ Download JSON</button>
</div><div class="card-body">
<pre style="max-height:500px;overflow:auto;color:var(--mu);font-size:.8rem"
>{_e(raw_json[:10000])}{"\\n... (truncated — download for full data)" if len(raw_json)>10000 else ""}</pre>
</div></div>
</div>

</div>
</div>

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
