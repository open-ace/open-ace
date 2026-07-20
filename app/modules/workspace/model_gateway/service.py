"""Service layer for the model gateway: CRUD + connection test.

Thin orchestration over :class:`ModelGatewayConfigRepository`. The connection test
issues a minimal ``GET /v1/models`` (or ``/models``) probe against the gateway with
the supplied key and reports a sanitized result — the key is never included in the
returned payload.
"""

from __future__ import annotations




import logging
from typing import Any

from app.modules.workspace.model_gateway.repository import get_gateway_repository

logger = logging.getLogger(__name__)


class ModelGatewayService:
    """CRUD + connection-test service for the model gateway config."""

    def __init__(self, repository=None):
        self._repo = repository or get_gateway_repository()

    def get_config(self) -> dict[str, Any] | None:
        return self._repo.get_config()

    def get_config_with_key(self) -> Any | None:
        """Return the decrypted GatewayConfig (runtime use / connection-test fallback)."""
        return self._repo.get_config_with_key()

    def save_config(
        self,
        base_url: str,
        api_key: str,
        model_prefix_mode: bool = False,
        model_prefix: str | None = None,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        base_url = (base_url or "").strip()
        if not base_url:
            raise ValueError("Gateway base URL is required")
        if api_key is None:
            raise ValueError("Gateway API key is required")
        return self._repo.save_config(
            base_url=base_url,
            api_key=api_key,
            model_prefix_mode=model_prefix_mode,
            model_prefix=model_prefix,
            created_by=created_by,
        )

    def delete_config(self) -> bool:
        return self._repo.delete_config()

    def test_connection(self, base_url: str, api_key: str) -> dict[str, Any]:
        """Probe the gateway with a minimal request; never echo the key back."""
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url:
            return {"ok": False, "status": None, "message": "Gateway base URL is required"}

        try:
            import requests as http_requests

            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = http_requests.request(
                method="GET",
                url=f"{base_url}/v1/models",
                headers=headers,
                timeout=15,
                proxies={"http": None, "https": None},  # type: ignore[dict-item]
            )
            ok = resp.status_code < 400
            message = "Gateway reachable" if ok else f"Gateway returned status {resp.status_code}"
            return {"ok": ok, "status": resp.status_code, "message": message}
        except Exception as exc:
            logger.warning("Model gateway connection test failed: %s", exc)
            return {
                "ok": False,
                "status": None,
                "message": "Connection failed (gateway unreachable)",
            }


def get_gateway_service() -> ModelGatewayService:
    """Get a service instance."""
    return ModelGatewayService()
