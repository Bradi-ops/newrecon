from agents import Agent, function_tool, RunContextWrapper
from recrew.context import ReconContext
from recrew.tools.endpoint_prober import probe_endpoints
import json


def build_endpoint_agent(model: str) -> Agent:
    @function_tool
    async def run_endpoint_probe(ctx: RunContextWrapper[ReconContext]) -> str:
        """Probe all discovered endpoints + 40 common interesting paths
        with passive GET requests. Identifies accessible, forbidden,
        and interesting resources (Swagger, admin, .git, .env, etc.)."""
        result = await probe_endpoints(ctx.context)
        return json.dumps(result, indent=2)

    return Agent(
        name="EndpointAgent",
        instructions=(
            "You are EndpointAgent. Call run_endpoint_probe. "
            "Flag: API docs exposed, admin panels, .git/.env exposure, "
            "401/403 paths (they exist even if protected), server headers. "
            "Only GET. No fuzzing."
        ),
        model=model,
        tools=[run_endpoint_probe],
    )