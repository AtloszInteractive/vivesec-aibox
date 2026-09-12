import os


POLICIES = ("locked_grounded", "locked_hybrid", "selectable_grounded", "selectable_hybrid")


def settings():
    policy = os.environ.get("ADAPTER_CHAT_POLICY", "locked_hybrid").strip().lower()
    if policy not in POLICIES:
        policy = "locked_grounded"
    return {"policy": policy,
            "default_profile": "hybrid" if policy in ("locked_hybrid", "selectable_hybrid") else "grounded",
            "allow_switch": policy.startswith("selectable_")}


def resolve(requested=None, action=None):
    config = settings()
    if requested is not None and requested not in ("grounded", "hybrid"):
        raise ValueError("Unknown chat profile")
    if action is not None:
        return "grounded"
    profile = requested or config["default_profile"]
    if not config["allow_switch"] and profile != config["default_profile"]:
        raise PermissionError("Chat profile switching is disabled on this AI Box")
    return profile


def history_scope(scope_key, profile):
    return scope_key if profile == "grounded" else "\x00chat:hybrid\x00" + scope_key