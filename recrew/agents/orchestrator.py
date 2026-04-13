"""
Orchestrator — usa agent.as_tool() (no handoffs).
El LLM del Orchestrator decide cuándo y en qué orden llamar
a cada especialista, recibe el resultado y planifica el siguiente paso.
Esto da agencia real sin loops incontrolados.
"""
from agents import Agent


ORCHESTRATOR_INSTRUCTIONS = """
You are ReconOrchestrator — master coordinator of a passive web recon mission.
You coordinate specialist agents by calling them as tools.

## Available tools (call in this order):
1. authenticate        → FIRST if auth is needed
2. crawl_target        → Playwright spider with JS rendering
3. analyze_javascript  → Mine JS for endpoints and API calls
4. scan_secrets        → Detect credentials, keys, tokens
5. probe_endpoints     → Passive GET probe all discovered URLs
6. generate_report     → LAST — produces the HTML report

## Rules:
- Complete ALL steps. Never stop early.
- Only passive GET. No exploitation. No fuzzing.
- After each tool returns, analyze its output and decide next step.
- Flag interesting findings in your reasoning (admin panels, exposed secrets,
  API documentation, .git/.env exposure, authentication endpoints).
- Final output: executive summary with stats + top findings + report path.
"""


def build_orchestrator(model: str) -> Agent:
    """Factory que importa agentes aquí para evitar imports circulares."""
    from recrew.agents.auth_agent import build_auth_agent
    from recrew.agents.spider_agent import build_spider_agent
    from recrew.agents.js_agent import build_js_agent
    from recrew.agents.secrets_agent import build_secrets_agent
    from recrew.agents.endpoint_agent import build_endpoint_agent
    from recrew.agents.reporter_agent import build_reporter_agent

    return Agent(
        name="ReconOrchestrator",
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        model=model,
        tools=[
            build_auth_agent(model).as_tool(
                tool_name="authenticate",
                tool_description=(
                    "Authenticate to the target. Supports form login (Playwright), "
                    "cookie injection, and bearer token. Call FIRST if auth is needed."
                ),
            ),
            build_spider_agent(model).as_tool(
                tool_name="crawl_target",
                tool_description=(
                    "Crawl the target using Playwright with full JavaScript rendering. "
                    "Works on SPAs (React, Angular, Vue). Discovers pages, links, "
                    "forms, scripts, and HTML comments."
                ),
            ),
            build_js_agent(model).as_tool(
                tool_name="analyze_javascript",
                tool_description=(
                    "Download and analyze all discovered JavaScript files. "
                    "Extracts hidden API endpoints, fetch/axios calls, "
                    "and interesting TODO/DEBUG comments."
                ),
            ),
            build_secrets_agent(model).as_tool(
                tool_name="scan_secrets",
                tool_description=(
                    "Scan all collected HTML and JS for secrets: API keys, JWTs, "
                    "AWS credentials, database URLs, private keys, bearer tokens."
                ),
            ),
            build_endpoint_agent(model).as_tool(
                tool_name="probe_endpoints",
                tool_description=(
                    "Probe all discovered endpoints + common paths (Swagger, admin, "
                    "graphql, .env, .git, actuator...) with passive GET requests. "
                    "Identifies accessible and interesting resources."
                ),
            ),
            build_reporter_agent(model).as_tool(
                tool_name="generate_report",
                tool_description=(
                    "Generate the final interactive HTML report with ALL raw data: "
                    "pages, endpoints, secrets, forms, JS findings. Call LAST."
                ),
            ),
        ],
    )