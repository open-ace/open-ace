"""Shared constants for the Open ACE remote agent."""

# Environment variable keys that contain API credentials.
# These must NEVER be written to settings.json — they are injected
# via environment variables at process launch time.
SENSITIVE_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    }
)
