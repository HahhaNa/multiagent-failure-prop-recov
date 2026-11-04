"""AgentDojo utility and security checks for a finished episode."""


def check(env, injection_task: str, report: str | None) -> dict:
    out = report or ""
    try:
        u = env.utility(out)
    except Exception as e:  # noqa: BLE001 - checker crash counts as failure
        return {"utility": False, "security_violation": False, "checker_error": str(e)[:300]}
    try:
        s = env.security(injection_task, out) if injection_task else False
    except Exception as e:  # noqa: BLE001
        return {"utility": u, "security_violation": False, "checker_error": str(e)[:300]}
    return {"utility": u, "security_violation": s}
