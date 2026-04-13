from agents import Agent, function_tool, RunContextWrapper
from recrew.context import ReconContext
from recrew.tools.secrets_scanner import scan_secrets
import json


def build_secrets_agent(model: str) -> Agent:
    @function_tool
    def run_secrets_scan(ctx: RunContextWrapper[ReconContext]) -> str:
        """Scan all HTML and JS content for secrets.
        Detects: AWS keys, JWTs, DB URLs, API keys, private keys,
        GitHub tokens, Stripe keys, Slack tokens, internal IPs."""
        result = scan_secrets(ctx.context)
        return json.dumps(result, indent=2)

    return Agent(
        name="SecretsAgent",
        instructions=(
            "You are SecretsAgent. Call run_secrets_scan. Report ALL findings "
            "by type. Do NOT filter or hide any finding. "
            "Highlight the most critical ones."
        ),
        model=model,
        tools=[run_secrets_scan],
    )