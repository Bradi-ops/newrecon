from agents import Agent, function_tool, RunContextWrapper
from recrew.context import ReconContext
from recrew.tools.auth_tool import authenticate
import json


def build_auth_agent(model: str) -> Agent:
    @function_tool
    async def perform_authentication(ctx: RunContextWrapper[ReconContext]) -> str:
        """Authenticate using configured method (form/cookie/bearer).
        Stores auth_cookies and auth_headers in ReconContext."""
        result = await authenticate(ctx.context)
        return json.dumps(result, indent=2)

    return Agent(
        name="AuthAgent",
        instructions=(
            "You are AuthAgent. Call perform_authentication, report the result "
            "(success/failure, method, cookies captured), and return."
        ),
        model=model,
        tools=[perform_authentication],
    )