TOOL_NAME_ALIASES = {
    "qwen": ["qwen", "qwen-code", "qwen-code-cli"],
    "claude": ["claude", "claude-code"],
    "openclaw": ["openclaw"],
    "codex": ["codex", "codex-cli"],
    "zcode": ["zcode", "zcode-code", "zcode-cli"],
}

CANONICAL_TOOL_NAMES = {
    "qwen-code": "qwen",
    "qwen-code-cli": "qwen",
    "claude-code": "claude",
    "codex-cli": "codex",
    "zcode-code": "zcode",
    "zcode-cli": "zcode",
}


def normalize_tool_name(name: str) -> str:
    return CANONICAL_TOOL_NAMES.get(name, name)
