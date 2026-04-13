"""
Secrets scanner — regex-based detection.
Fixes:
- BASIC_AUTH_IN_URL: excluye comillas para no matchear JSON-LD
- INTERNAL_IP: requiere 4 octetos reales
"""
import re
import logging
from recrew.context import ReconContext, Secret

logger = logging.getLogger("recrew.secrets")

SECRET_PATTERNS: dict[str, str] = {
    "AWS_ACCESS_KEY_ID":   r"AKIA[0-9A-Z]{16}",
    "AWS_SECRET_KEY":      r"(?i)aws.{0,20}secret.{0,10}['\"][0-9a-zA-Z/+=]{40}['\"]",
    "JWT_TOKEN":           r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+",
    "PRIVATE_KEY":         r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
    "GOOGLE_API_KEY":      r"AIza[0-9A-Za-z\-_]{35}",
    "STRIPE_LIVE_KEY":     r"sk_live_[0-9a-zA-Z]{24,}",
    "STRIPE_TEST_KEY":     r"sk_test_[0-9a-zA-Z]{24,}",
    "GITHUB_TOKEN":        r"gh[pousr]_[0-9a-zA-Z]{36,}",
    "SLACK_TOKEN":         r"xox[baprs]-[0-9a-zA-Z\-]{10,}",
    "SLACK_WEBHOOK":       r"https://hooks\.slack\.com/services/[A-Z0-9/]{20,}",
    "SENDGRID_KEY":        r"SG\.[a-zA-Z0-9\-_]{22,}\.[a-zA-Z0-9\-_]{22,}",
    "DATABASE_URL":        r"(?i)(?:mysql|postgres|mongodb|redis|mssql)://[^\s'\"<>\n]{8,}",
    "GENERIC_API_KEY":     r"(?i)(?:api[_-]?key|apikey)\s*[:=]\s*['\"]([a-zA-Z0-9_\-]{20,})['\"]",
    "GENERIC_SECRET":      r"(?i)(?:client_secret|app_secret|secret_key)\s*[:=]\s*['\"]([a-zA-Z0-9!@#$%^&*_\-]{10,})['\"]",
    # FIX: excluir comillas y caracteres JSON para no matchear JSON-LD estructurado
    "BASIC_AUTH_IN_URL":   r"https?://[^\"'`\s\n:@]{3,}:[^\"'`\s\n@]{3,}@[^\"'`\s\n/]{3,}",
    # FIX: requiere exactamente 4 octetos (sin esto matcheaba "10.0.4" = versiones)
    "INTERNAL_IP":         r"\b(?:10\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}|192\.168\.\d{1,3})\.\d{1,3}\b",
}

_NOISE = [
    "example", "placeholder", "your_", "<your", "INSERT_",
    "REPLACE_", "changeme", "xxxxxxxxx", "000000000000",
    "localhost", "your-api-key", "sk-your", "API_KEY_HERE",
    "XXXX", "test_key", "dummy",
    # FIX: versiones de paquetes npm/composer que matchean INTERNAL_IP
    "10.0.4", "10.12.6", "10.0.0", "10.1.0",
]

# Comentarios/scripts donde no buscar secrets (demasiado ruido)
_SKIP_SOURCES = [
    "googletagmanager", "google-analytics", "facebook.net",
    "cdn.cookielaw", "pinimg.com", "jquery.min.js",
    "wp-emoji", "font-awesome", "elementor-icons",
]


def scan_secrets(ctx: ReconContext) -> dict:
    sources: list[tuple[str, str]] = []

    for page in ctx.pages:
        # Solo escanear comentarios HTML y scripts inline pequeños
        for c in page.comments:
            sources.append((page.url, c))
        for s in page.inline_scripts:
            if len(s) < 5000:  # scripts inline cortos son más interesantes
                sources.append((page.url, s))

    for js in ctx.js_files:
        # Solo JS del mismo dominio y no del CDN
        if not any(skip in js.url for skip in _SKIP_SOURCES):
            sources.append((js.url, js.content))

    seen: set[str] = set()
    found: list[Secret] = []

    for source_url, text in sources:
        if not text:
            continue
        # Saltar fuentes ruidosas
        if any(skip in source_url for skip in _SKIP_SOURCES):
            continue
        for stype, pattern in SECRET_PATTERNS.items():
            for match in re.finditer(pattern, text):
                val = match.group(0)
                if _is_noise(val):
                    continue
                # FIX adicional: no reportar IPs que sean versiones semver
                if stype == "INTERNAL_IP" and _is_version_number(val, text, match.start()):
                    continue
                key = f"{stype}:{val[:40]}"
                if key in seen:
                    continue
                seen.add(key)
                start = max(0, match.start() - 60)
                snippet = text[start: match.end() + 60].replace("\n", " ").strip()
                s = Secret(type=stype, value=val,
                           source_url=source_url, context_snippet=snippet)
                found.append(s)
                ctx.secrets.append(s)

    by_type: dict[str, int] = {}
    for s in found:
        by_type[s.type] = by_type.get(s.type, 0) + 1

    logger.info(f"Secrets: {len(found)} found — {by_type}")
    return {
        "total": len(found),
        "by_type": by_type,
        "secrets": [{"type": s.type, "source": s.source_url,
                     "preview": s.value[:80], "context": s.context_snippet[:120]}
                    for s in found],
    }


def _is_noise(val: str) -> bool:
    low = val.lower()
    return any(n.lower() in low for n in _NOISE)


def _is_version_number(ip: str, text: str, pos: int) -> bool:
    """Detecta si el IP-like string es en realidad un número de versión."""
    # Contexto: buscar si está precedido por '=' o '"' típico de versiones
    context_before = text[max(0, pos - 20): pos]
    version_indicators = ["ver=", "version=", "?ver", "ver:", '"version":', "'version':"]
    return any(v in context_before.lower() for v in version_indicators)
