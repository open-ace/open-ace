"""
Open ACE - Remote Session Manager

Manages remote workspace sessions: creation, message forwarding,
output collection, and session lifecycle. Integrates with existing
SessionManager for persistence and QuotaManager for enforcement.
"""

import contextlib
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.modules.policy import get_evaluator
from app.modules.policy.evaluator import TARGET_MODEL_SELECTION, TARGET_TOOL_ACTION, PolicyContext
from app.modules.workspace.api_key_proxy import APIKeyProxyService
from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.run_timeline import get_run_recorder
from app.modules.workspace.session_manager import SessionManager
from app.repositories.message_repo import MessageRepository
from app.repositories.user_repo import UserRepository
from app.utils.tool_names import normalize_tool_name

logger = logging.getLogger(__name__)

# Reviewer identity recorded for auto (non-human) policy decisions.
_POLICY_SYSTEM_ACTOR = "policy"


class RemoteSessionManager:
    """
    Manages remote workspace sessions.

    Handles:
    - Creating sessions that run on remote machines
    - Forwarding user messages to remote CLI processes
    - Collecting and buffering CLI output
    - Session lifecycle (start, stop, pause, resume)
    - Integration with SessionManager for persistence
    - Integration with QuotaManager for usage tracking
    """

    # Class-level buffer for accumulating assistant text across requests
    _assistant_text_buffer: dict[str, str] = {}
    _content_blocks_buffer: dict[str, list[dict]] = {}
    _buffer_lock = threading.Lock()
    _allowed_request_states = frozenset({"aborted", "abort_failed", "done"})

    def __init__(self):
        self._session_manager = SessionManager()
        self._agent_manager = get_remote_agent_manager()
        self._api_key_proxy = APIKeyProxyService()
        # Cache of session permission modes to avoid unnecessary updates
        self._session_permission_modes: dict[str, str] = {}
        self._message_repo = MessageRepository()
        self._user_repo = UserRepository()
        # Cache user names to avoid repeated lookups
        self._user_name_cache: dict[int, str] = {}
        # Persisted run/event timeline recorder. No-op when the feature is
        # disabled (run_timeline.enabled=false); see app/modules/workspace/run_timeline.
        self._run_recorder = get_run_recorder()
        # Central policy evaluator. NullPolicyEvaluator (is_noop=True) when the
        # feature is disabled (policy.enabled=false), so every call site is
        # unconditional and removing the feature = deleting these references.
        self._policy_evaluator = get_evaluator()

    def _timeline(self, method: str, *args: Any, **kwargs: Any) -> None:
        """Single integration seam to the run-timeline recorder.

        Every lifecycle/output hook funnels through here instead of scattering
        ``if not self._run_recorder.is_noop`` guards across the manager.
        Removing the feature therefore means deleting this method (and the
        ``self._run_recorder`` init above) — there is nothing else to strip. The
        internal short-circuit keeps a disabled recorder free of even building
        the call, and the recorder itself is non-blocking regardless.

        Never raises, even on the hot path: an unknown/typo'd method name or an
        unexpected recorder failure is swallowed so stdout handling is never
        interrupted. (The recorder methods already catch internally; this is the
        belt-and-suspenders for the ``_timeline`` call site itself.)
        """
        recorder = self._run_recorder
        if recorder.is_noop:
            return
        fn = getattr(recorder, method, None)
        if fn is None:
            logger.warning("run_timeline: unknown recorder method %r ignored", method)
            return
        try:
            fn(*args, **kwargs)
        except Exception as e:  # pragma: no cover - defensive, hot path must never raise
            logger.debug("run_timeline: recorder %s failed: %s", method, e)

    # ── Central policy & approval (policy.enabled) ────────────────────
    # All policy logic is guarded by ``self._policy_evaluator.is_noop`` so the
    # whole feature is a no-op when disabled. Removing it = deleting this block
    # plus the ``self._policy_evaluator`` init and the two import lines above.

    def _session_policy_scope(self, session_id: str) -> dict[str, Any]:
        """Collect the scoping attributes for a session's policy evaluation."""
        scope: dict[str, Any] = {
            "tenant_id": None,
            "project_path": None,
            "machine_id": None,
            "user_id": None,
        }
        machine_id = self._get_machine_id(session_id)
        scope["machine_id"] = machine_id
        session = self._session_manager.get_session(session_id)
        if session:
            scope["user_id"] = session.user_id
            scope["project_path"] = session.project_path
        if machine_id:
            machine = self._agent_manager.get_machine(machine_id)
            if machine:
                scope["tenant_id"] = machine.get("tenant_id")
        return scope

    def _evaluate_model_policy(
        self,
        *,
        tenant_id: Optional[int],
        project_path: Optional[str],
        machine_id: Optional[str],
        user_id: Optional[int],
        model: Optional[str],
        provider: Optional[str],
        session_id: Optional[str] = None,
    ):
        """Evaluate model/provider policy. Never raises (evaluator is fail-closed)."""
        ctx = PolicyContext(
            target_kind=TARGET_MODEL_SELECTION,
            tenant_id=tenant_id,
            project_path=project_path,
            machine_id=machine_id,
            user_id=user_id,
            model=model,
            provider=provider,
            session_id=session_id,
            run_id=session_id,
        )
        return self._policy_evaluator.evaluate(ctx)

    def _emit_policy_decision_event(
        self,
        session_id: str,
        result,
        *,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        """Emit a ``policy_decision`` run-timeline event (visibility/audit only)."""
        metadata: dict[str, Any] = {
            "decision": result.decision,
            "reason": result.reason,
            "fell_back": result.fell_back,
        }
        if result.matched_rule:
            metadata["rule_key"] = result.matched_rule.rule_key
            metadata["rule_version"] = result.matched_rule.version
        if result.fingerprint_hash:
            metadata["fingerprint_hash"] = result.fingerprint_hash
        if model:
            metadata["model"] = model
        if provider:
            metadata["provider"] = provider
        if request_id:
            metadata["request_id"] = request_id
        self._timeline("record_event", session_id, "policy_decision", metadata=metadata)

    def _persist_tool_decision(
        self, session_id: str, control_request: dict, result, scope: dict
    ) -> str:
        """SYNCHRONOUSLY persist a tool-action decision row (the gate object).

        Per the persistence invariant (plan §2.7): the decision INSERT is
        synchronous so the consume chokepoint in ``respond_to_permission`` can
        read it immediately — it must NOT go through the async timeline writer.
        Only the visibility *event* is async (``_emit_policy_decision_event``).
        """
        from datetime import timedelta

        from app.modules.policy.repo import PolicyRepository
        from app.utils.config import get_policy_approval_ttl_seconds

        rule = result.matched_rule
        ttl = (
            rule.approval_ttl_seconds
            if rule and rule.approval_ttl_seconds
            else get_policy_approval_ttl_seconds()
        )
        now = datetime.utcnow()
        fp = result.fingerprint
        repo = PolicyRepository()
        request_id = fp.request_id if fp else None
        decision_id = repo.insert_decision(
            request_id=request_id,
            run_id=session_id,
            session_id=session_id,
            tenant_id=scope.get("tenant_id"),
            workspace_scope=scope.get("project_path"),
            machine_id=scope.get("machine_id"),
            tool_name=fp.tool if fp else None,
            action=fp.action if fp else None,
            resource_target=result.resource_target,
            args_digest=result.args_digest,
            normalization_profile_id=fp.normalization_profile_id if fp else None,
            normalization_profile_version=fp.normalization_profile_version if fp else None,
            fingerprint_hash=result.fingerprint_hash,
            policy_rule_id=rule.id if rule else None,
            policy_rule_version=rule.version if rule else None,
            decision=result.decision,
            reason=result.reason,
            expires_at=now + timedelta(seconds=ttl),
        )
        self._emit_policy_decision_event(session_id, result, request_id=request_id)
        return decision_id

    def _buffer_permission_for_human(self, session_id: str, control_request: dict) -> None:
        """Buffer a permission request for SSE delivery to the frontend (manual path)."""
        output_entry = {
            "session_id": session_id,
            "data": json.dumps(control_request),
            "stream": "permission",
            "is_complete": False,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
        self._agent_manager.buffer_output(session_id, output_entry)
        logger.info(
            "Buffered permission request for session %s: %s",
            session_id[:8],
            control_request.get("request", {}).get("subtype"),
        )

    def create_remote_session(
        self,
        user_id: int,
        machine_id: str,
        project_path: str,
        model: Optional[str] = None,
        cli_tool: str = "qwen-code-cli",
        title: str = "",
        tenant_id: Optional[int] = None,
        permission_mode: Optional[str] = None,
        ha_pool_token: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Create a new remote session.

        Args:
            user_id: User ID creating the session.
            machine_id: Target remote machine ID.
            project_path: Working directory on the remote machine.
            model: Optional model name to use.
            cli_tool: CLI tool to run (qwen-code-cli, claude-code, etc.).
            title: Optional session title.
            tenant_id: Tenant ID for API key lookup.

        Returns:
            Dict with session info or None on failure.
        """
        # Verify user has access to this machine
        if not self._agent_manager.check_user_access(machine_id, user_id):
            logger.warning(f"User {user_id} has no access to machine {machine_id}")
            return None

        # Check machine is connected
        if not self._agent_manager.is_connected(machine_id):
            logger.warning(f"Machine {machine_id} is not connected")
            return None

        # Get machine info
        machine = self._agent_manager.get_machine(machine_id)
        if not machine:
            return None

        # Determine provider based on CLI tool
        provider = self._cli_tool_to_provider(cli_tool)
        tool_name = normalize_tool_name(cli_tool)

        effective_tenant_id = tenant_id or machine.get("tenant_id", 1)

        # Central policy gate: a denied model/provider cannot be selected for a
        # remote session (acceptance criterion). No-op when policy is disabled.
        if not self._policy_evaluator.is_noop:
            model_decision = self._evaluate_model_policy(
                tenant_id=effective_tenant_id,
                project_path=project_path,
                machine_id=machine_id,
                user_id=user_id,
                model=model,
                provider=provider,
            )
            if model_decision.is_deny:
                logger.warning(
                    "Policy denied model %s (provider %s) for tenant %s: %s",
                    model,
                    provider,
                    effective_tenant_id,
                    model_decision.reason,
                )
                return None

        ha_pool: Optional[dict[str, Any]] = None
        if tool_name == "qwen":
            if not ha_pool_token:
                logger.warning("Missing ha_pool_token for qwen remote session creation")
                return None
            token_payload = self._api_key_proxy.validate_proxy_token(ha_pool_token)
            if not token_payload:
                logger.warning("Invalid ha_pool_token for qwen remote session creation")
                return None
            if token_payload.get("session_type") != "ha_pool":
                logger.warning("Unexpected session_type in ha_pool_token: %s", token_payload)
                return None
            if token_payload.get("scope") != "remote":
                logger.warning("Unexpected scope in ha_pool_token: %s", token_payload)
                return None
            if token_payload.get("user_id") != user_id:
                logger.warning("ha_pool_token user mismatch: %s != %s", token_payload, user_id)
                return None
            if token_payload.get("tenant_id") != effective_tenant_id:
                logger.warning(
                    "ha_pool_token tenant mismatch: %s != %s",
                    token_payload,
                    effective_tenant_id,
                )
                return None
            if token_payload.get("machine_id") != machine_id:
                logger.warning(
                    "ha_pool_token machine mismatch: %s != %s", token_payload, machine_id
                )
                return None
            ha_pool = {
                "provider": provider,
                "tool_name": "qwen-code",
                "scope": "remote",
                "models": token_payload.get("ha_models", []),
                "candidate_keys": token_payload.get("ha_candidate_keys", []),
                "model_key_ids": token_payload.get("ha_model_key_ids", {}),
                "settings": token_payload.get("ha_settings", {}),
                "empty_reason": token_payload.get("ha_empty_reason"),
            }
            if model and not ha_pool.get("model_key_ids", {}).get(model):
                logger.warning("Requested model %s is not supported by remote HA pool", model)
                return None

        # Generate session ID
        session_id = str(uuid.uuid4())

        # Create session in SessionManager
        session = self._session_manager.create_session(
            tool_name=normalize_tool_name(cli_tool),
            user_id=user_id,
            title=title or f"Remote: {machine.get('machine_name', machine_id[:8])}",
            host_name=machine.get("hostname", machine_id),
            model=model,
            project_path=project_path,
            session_id=session_id,
            workspace_type="remote",
            remote_machine_id=machine_id,
        )

        # Generate proxy token for this session

        proxy_token = self._api_key_proxy.generate_proxy_token(
            user_id=user_id,
            session_id=session_id,
            tenant_id=effective_tenant_id,
            provider=provider,
            extra_payload={
                "scope": "remote",
                "tool_name": "qwen-code" if tool_name == "qwen" else tool_name,
                **(
                    {
                        "ha_candidate_keys": ha_pool.get("candidate_keys", []),
                        "ha_model_key_ids": ha_pool.get("model_key_ids", {}),
                    }
                    if ha_pool
                    else {}
                ),
            },
        )

        # Get CLI settings for this tool
        cli_settings = {}
        # Map normalized tool name to the key used in API key management
        settings_tool_map = {
            "claude": "claude-code",
            "qwen": "qwen-code",
            "codex": "codex-cli",
            "openclaw": "openclaw",
            "zcode": "zcode",
        }
        settings_tool = settings_tool_map.get(tool_name, cli_tool)
        tool_settings = (
            ha_pool.get("settings")
            if ha_pool
            else self._api_key_proxy.get_cli_settings_for_tool(effective_tenant_id, settings_tool)
        )
        if tool_settings:
            cli_settings[settings_tool] = tool_settings

        # Bind session to machine in agent manager
        self._agent_manager.bind_session(session_id, machine_id)

        # Dispatch start_session command to remote agent
        command: dict[str, Any] = {
            "type": "command",
            "command": "start_session",
            "session_id": session_id,
            "project_path": project_path,
            "model": model,
            "cli_tool": cli_tool,
            "proxy_token": proxy_token,
            "cli_settings": cli_settings,
        }
        if permission_mode:
            command["permission_mode"] = permission_mode
        if allowed_tools:
            command["allowed_tools"] = allowed_tools

        self._agent_manager.send_command(machine_id, command)

        # Update session with remote workspace info
        session.context["workspace_type"] = "remote"
        session.context["remote_machine_id"] = machine_id
        session.context["cli_tool"] = cli_tool
        if ha_pool:
            session.context["ha_pool"] = ha_pool
            session.context["ha_current_model_key_ids"] = (
                ha_pool.get("model_key_ids", {}).get(model, []) if model else []
            )
        self._session_manager.update_session(session)

        # Also update the dedicated columns (list_sessions reads from columns, not context JSON)
        try:
            from app.repositories.database import adapt_sql, get_db_connection

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql(
                        "UPDATE agent_sessions SET workspace_type = ?, remote_machine_id = ? WHERE session_id = ?"
                    ),
                    ("remote", machine_id, session_id),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to update workspace_type column: {e}")

        logger.info(f"Created remote session {session_id} on machine {machine_id}")

        # Cache the initial permission mode (default if not specified)
        self._session_permission_modes[session_id] = permission_mode or "default"

        # Record the durable run + session_created event (no-op when disabled).
        # The manager already has full attribution here, so pass it explicitly
        # and let the recorder cache it per-run (plan §2.3 attribution source map).
        self._timeline(
            "record_session_created",
            session_id,
            user_id=user_id,
            tenant_id=effective_tenant_id,
            machine_id=machine_id,
            tool_name=tool_name,
            provider=provider,
            cli_tool=cli_tool,
            model=model,
        )

        return {
            "session_id": session_id,
            "machine_id": machine_id,
            "status": "active",
            "project_path": project_path,
            "cli_tool": cli_tool,
            "model": model,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        }

    def _get_machine_id(self, session_id: str) -> Optional[str]:
        """Get machine_id for a session, with DB fallback on restart.

        Tries in-memory mapping first, then falls back to agent_sessions
        table and session context JSON. Re-binds the mapping on recovery.
        """
        machine_id = self._agent_manager.get_machine_for_session(session_id)
        if machine_id:
            return machine_id

        # Fallback 1: session context JSON
        session = self._session_manager.get_session(session_id)
        if session and session.context:
            machine_id = session.context.get("remote_machine_id")

        # Fallback 2: dedicated DB column
        if not machine_id:
            try:
                from app.repositories.database import adapt_sql, get_db_connection

                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        adapt_sql(
                            "SELECT remote_machine_id FROM agent_sessions WHERE session_id = ?"
                        ),
                        (session_id,),
                    )
                    row = cursor.fetchone()
                    if row:
                        machine_id = (
                            row[0]
                            if isinstance(row, (list, tuple))
                            else row.get("remote_machine_id")
                        )
            except Exception as e:
                logger.warning(f"DB fallback failed for session {session_id}: {e}")

        if machine_id:
            self._agent_manager.bind_session(session_id, machine_id)
            logger.info(f"Recovered machine binding for session {session_id[:8]}: {machine_id[:8]}")

        return machine_id

    def send_message(self, session_id: str, content: str, user_id: Optional[int] = None) -> bool:
        """
        Forward a user message to the remote CLI process.

        Args:
            session_id: Session ID.
            content: Message content.
            user_id: Optional user ID for verification.

        Returns:
            True if message was sent successfully.
        """
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            logger.warning(f"No machine bound for session {session_id}")
            return False

        # Store user message in session
        stored = self._session_manager.append_transcript_message(
            session_id=session_id,
            role="user",
            content=content,
            source="remote_live",
        )
        if getattr(stored, "_was_inserted", False):
            self._session_manager.increment_session_usage(session_id, message_delta=1)
        self._save_to_daily_messages(session_id, "user", content)

        command = {
            "type": "command",
            "command": "send_message",
            "session_id": session_id,
            "content": content,
        }

        self._timeline("record_event", session_id, "user_message", role="user", content=content)

        return self._agent_manager.send_command(machine_id, command)

    def update_permission_mode(self, session_id: str, permission_mode: str) -> bool:
        """Send update_permission_mode command to the remote agent.

        Only sends the command if the permission mode has actually changed
        from the cached value to avoid unnecessary network traffic.
        """
        # Check cached permission mode - skip if unchanged
        cached_mode = self._session_permission_modes.get(session_id)
        # Treat None as equivalent to "default" for consistency
        new_mode = permission_mode or "default"

        # If no cached mode (e.g., after server restart), initialize it
        # and skip the update since we don't know if it's actually changed
        if cached_mode is None:
            self._session_permission_modes[session_id] = new_mode
            logger.debug(f"Initialized permission_mode cache for {session_id[:8]}: {new_mode}")
            return True

        current_mode = cached_mode or "default"

        if current_mode == new_mode:
            logger.debug(
                f"Skipping permission_mode update for {session_id[:8]}: unchanged ({new_mode})"
            )
            return True  # No change needed, consider it successful

        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "update_permission_mode",
            "session_id": session_id,
            "permission_mode": permission_mode,
        }
        self._agent_manager.send_command(machine_id, command)

        # Update cache
        self._session_permission_modes[session_id] = permission_mode or "default"
        logger.info(f"Updated permission_mode for {session_id[:8]}: {new_mode}")

        return True

    def respond_to_permission(
        self,
        session_id: str,
        request_id: Optional[str],
        behavior: str,
        tool_name: str = "",
        message: Optional[str] = None,
        *,
        decided_by: Optional[int] = None,
        decided_by_name: Optional[str] = None,
    ) -> bool:
        """Send a permission response (approve/deny) to the remote agent.

        This is the SINGLE consume chokepoint for policy decisions (plan §2.5,
        review A3): both the human-approval route and the auto allow/deny path
        in ``process_permission_request`` funnel through here. When policy is
        enabled and a decision row exists for ``request_id``, the decision is
        atomically consumed here (single-use + expiry + fingerprint binding). If
        the consume fails (already consumed / expired / drifted), the response
        fails CLOSED to ``deny`` so an approval can neither be replayed nor
        outlive its binding.

        Centralises the response path so the durable approval record and the
        ``permission_answered`` event are recorded alongside the command dispatch
        (the route previously dispatched directly and captured no operator
        identity). ``decided_by`` / ``decided_by_name`` are injected from the
        Flask auth state by the caller.
        """
        machine_id = self._get_machine_id(session_id)

        # Single consume point: enforce the policy decision's binding BEFORE
        # recording the response, so the audited behavior reflects what is
        # actually enforced (consume may fail-closed to deny on replay / expiry
        # / error). No-op when policy is disabled or no decision row exists.
        behavior = self._enforce_policy_consume(
            session_id, request_id, behavior, decided_by, decided_by_name
        )

        # Record the durable approval response with the actually-enforced
        # behavior (no-op when the recorder is disabled).
        self._timeline(
            "record_approval_response",
            session_id,
            request_id,
            behavior,
            decided_by=decided_by,
            decided_by_name=decided_by_name,
            message=message,
        )

        if not machine_id:
            return False

        command: dict[str, Any] = {
            "type": "command",
            "command": "permission_response",
            "session_id": session_id,
            "behavior": behavior,
            "tool_name": tool_name,
        }
        if request_id:
            command["request_id"] = request_id
        if message:
            command["message"] = message

        self._agent_manager.send_command(machine_id, command)
        return True

    def _enforce_policy_consume(
        self,
        session_id: str,
        request_id: Optional[str],
        behavior: str,
        decided_by: Optional[int],
        decided_by_name: Optional[str],
    ) -> str:
        """Atomically consume the policy decision for ``request_id``.

        Returns the (possibly overridden) behavior to dispatch. Fail-closed to
        ``deny`` on consume failure (already-consumed / expired) OR on any
        exception (e.g. transient DB error) — matching the evaluator's
        fail-closed posture (review S2). Returns ``behavior`` unchanged only
        when policy is disabled or no decision row exists (a human approving a
        pre-policy request is not blocked).

        Note (review S3): server-side consume enforces single-use
        (``consumed_at``) + expiry only. The stored ``fingerprint_hash`` is the
        decision's binding-of-record (audit) but is NOT re-verified here —
        ``respond_to_permission`` has no access to the live request args to
        recompute it. Live fingerprint re-verification is the deferred Phase-2
        CLI-side pre-side-effect recheck (honest-client boundary, plan §2.6).
        """
        if self._policy_evaluator.is_noop or not request_id:
            return behavior
        try:
            from app.modules.policy.repo import PolicyRepository

            repo = PolicyRepository()
            decision = repo.get_decision_by_request(request_id)
            if decision is None:
                # No decision row for this request (e.g. created before policy
                # was enabled): don't block a human approval.
                return behavior
            reviewer = decided_by_name or (f"user:{decided_by}" if decided_by else "human")
            ok = repo.consume_decision(
                decision.decision_id,
                resolved_decision=behavior,
                reviewer_identity=reviewer,
            )
            if not ok:
                logger.warning(
                    "Policy consume failed (expired/consumed) for request %s in "
                    "session %s; failing closed to deny",
                    request_id,
                    session_id[:8],
                )
                return "deny"
            return behavior
        except Exception as e:  # pragma: no cover - defensive: fail-closed
            logger.warning(
                "Policy consume raised for request %s: %s; failing closed to deny",
                request_id,
                e,
            )
            return "deny"

    def update_model(self, session_id: str, model: str) -> bool:
        """Switch the model of an active remote session."""
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        # Central policy gate: a denied model/provider cannot be switched to.
        # No-op when policy is disabled.
        if not self._policy_evaluator.is_noop:
            scope = self._session_policy_scope(session_id)
            session = self._session_manager.get_session(session_id)
            cli_tool = (
                session.context.get("cli_tool") if session and session.context else None
            ) or ""
            provider = self._cli_tool_to_provider(cli_tool) if cli_tool else None
            model_decision = self._evaluate_model_policy(
                tenant_id=scope.get("tenant_id"),
                project_path=scope.get("project_path"),
                machine_id=machine_id,
                user_id=scope.get("user_id"),
                model=model,
                provider=provider,
                session_id=session_id,
            )
            if model_decision.is_deny:
                logger.warning(
                    "Policy denied model switch to %s for session %s: %s",
                    model,
                    session_id[:8],
                    model_decision.reason,
                )
                self._emit_policy_decision_event(
                    session_id, model_decision, model=model, provider=provider
                )
                return False

        # Update model in DB
        session = self._session_manager.get_session(session_id)
        if session:
            ha_pool = session.context.get("ha_pool", {}) if session.context else {}
            if ha_pool:
                supported_keys = ha_pool.get("model_key_ids", {}).get(model, [])
                if not supported_keys:
                    logger.warning(
                        "Model %s is not supported by session %s HA pool",
                        model,
                        session_id[:8],
                    )
                    return False
                session.context["ha_current_model_key_ids"] = supported_keys
            session.model = model
            self._session_manager.update_session(session)

        command = {
            "type": "command",
            "command": "update_model",
            "session_id": session_id,
            "model": model,
        }
        return self._agent_manager.send_command(machine_id, command)

    def abort_request(self, session_id: str, reason: str = "user") -> bool:
        """Abort the current in-progress request without stopping the session.

        Sends an interrupt signal (SIGINT/Ctrl+C) to the remote CLI process
        so the user can continue interacting with the session afterwards.
        """
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "abort_request",
            "session_id": session_id,
            "reason": reason,
        }

        self._agent_manager.send_command(machine_id, command)
        logger.info("Sent abort_request for session %s (reason=%s)", session_id[:8], reason)
        self._timeline("record_event", session_id, "request_aborted", metadata={"reason": reason})
        return True

    def stop_session(self, session_id: str) -> bool:
        """Stop a remote session."""
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "stop_session",
            "session_id": session_id,
        }

        self._agent_manager.send_command(machine_id, command)

        # Complete session
        self._session_manager.complete_session(session_id)
        self._agent_manager.unbind_session(session_id)

        logger.info(f"Stopped remote session {session_id}")
        self._timeline("record_run_status", session_id, "completed")
        return True

    def pause_session(self, session_id: str) -> bool:
        """Pause a remote session."""
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "pause_session",
            "session_id": session_id,
        }

        self._agent_manager.send_command(machine_id, command)
        session = self._session_manager.get_session(session_id)
        if session:
            session.status = "paused"
            session.paused_at = datetime.now(timezone.utc).replace(tzinfo=None)
            self._session_manager.update_session(session)

        self._timeline("record_run_status", session_id, "pause")
        return True

    def resume_session(self, session_id: str) -> bool:
        """Resume a paused remote session."""
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "resume_session",
            "session_id": session_id,
        }

        self._agent_manager.send_command(machine_id, command)
        session = self._session_manager.get_session(session_id)
        if session:
            session.status = "active"
            session.paused_at = None
            self._session_manager.update_session(session)

        self._timeline("record_run_status", session_id, "resume")
        return True

    def get_session_status(self, session_id: str) -> Optional[dict[str, Any]]:
        """Get remote session status and recent output."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return None

        machine_id = self._get_machine_id(session_id)
        output = self._agent_manager.get_buffered_output(session_id)

        # Include DB-stored messages for frontend replay on reconnect
        messages = []
        with contextlib.suppress(Exception):
            messages = self._session_manager.get_messages(session_id) or []

        return {
            "session_id": session_id,
            "status": session.status,
            "machine_id": machine_id,
            "project_path": session.project_path,
            "model": session.model,
            "total_tokens": session.total_tokens,
            "total_input_tokens": session.total_input_tokens,
            "total_output_tokens": session.total_output_tokens,
            "message_count": session.message_count,
            "request_count": session.request_count,
            "output": output,
            "messages": messages,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "paused_at": session.paused_at.isoformat() if session.paused_at else None,
        }

    def process_session_output(
        self, session_id: str, data: str, stream: str = "stdout", is_complete: bool = False
    ) -> None:
        """Process output received from a remote session."""
        output_entry = {
            "session_id": session_id,
            "data": data,
            "stream": stream,
            "is_complete": is_complete,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }

        self._agent_manager.buffer_output(session_id, output_entry)

        if stream == "stdout":
            # Parse streaming JSON to accumulate assistant text per turn
            self._accumulate_assistant_text(session_id, data)

            # Flush on completion signal (process exit)
            if is_complete:
                self._flush_assistant_buffer(session_id)
        elif stream == "system" and is_complete and data.strip():
            stored = self._session_manager.append_transcript_message(
                session_id=session_id,
                role="system",
                content=data,
                source="remote_live",
            )
            if getattr(stored, "_was_inserted", False):
                self._session_manager.increment_session_usage(session_id, message_delta=1)
            self._save_to_daily_messages(session_id, "system", data)

    def _accumulate_assistant_text(self, session_id: str, data: str) -> None:
        """Parse streaming JSON and accumulate assistant text for a session."""
        if not data or not data.strip():
            return

        try:
            parsed = json.loads(data.strip())
        except (json.JSONDecodeError, ValueError):
            return

        if not isinstance(parsed, dict):
            return

        msg_type = parsed.get("type")

        if msg_type == "assistant":
            message = parsed.get("message", {})
            if not isinstance(message, dict):
                return
            content = message.get("content", [])
            if not isinstance(content, list):
                return

            text_parts = []
            structured_blocks = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        text_parts.append(text)
                if block_type in ("text", "tool_use", "thinking", "tool_result"):
                    structured_blocks.append(block)

            if text_parts:
                combined = "".join(text_parts)
                with self._buffer_lock:
                    buf = self._assistant_text_buffer.get(session_id, "")
                    self._assistant_text_buffer[session_id] = buf + combined
                    blocks_buf = self._content_blocks_buffer.get(session_id, [])
                    blocks_buf.extend(structured_blocks)
                    self._content_blocks_buffer[session_id] = blocks_buf

        elif msg_type == "message":
            role = parsed.get("role")
            if role == "assistant":
                content = parsed.get("content")
                if isinstance(content, str) and content:
                    with self._buffer_lock:
                        buf = self._assistant_text_buffer.get(session_id, "")
                        self._assistant_text_buffer[session_id] = buf + content
                        blocks_buf = self._content_blocks_buffer.get(session_id, [])
                        blocks_buf.append({"type": "text", "text": content})
                        self._content_blocks_buffer[session_id] = blocks_buf
                elif isinstance(content, list):
                    text_parts = []
                    structured_blocks = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type == "text":
                            text = block.get("text", "")
                            if text:
                                text_parts.append(text)
                        if block_type in ("text", "tool_use", "thinking", "tool_result"):
                            structured_blocks.append(block)
                    if text_parts:
                        with self._buffer_lock:
                            buf = self._assistant_text_buffer.get(session_id, "")
                            self._assistant_text_buffer[session_id] = buf + "".join(text_parts)
                            blocks_buf = self._content_blocks_buffer.get(session_id, [])
                            blocks_buf.extend(structured_blocks)
                            self._content_blocks_buffer[session_id] = blocks_buf

        elif msg_type == "system":
            # System messages (e.g., init) are stored directly, not accumulated
            # Extract meaningful content for storage
            subtype = parsed.get("subtype", "")

            # Get content/message fields with clear priority:
            # - Prefer 'content' if it exists and is non-empty
            # - Fall back to 'message' if content is empty/missing
            # - Both can be str or dict
            raw_content = parsed.get("content")
            raw_message = parsed.get("message")

            # Determine the effective content value
            if raw_content is not None and raw_content != "":
                effective_content = raw_content
            elif raw_message is not None and raw_message != "":
                effective_content = raw_message
            else:
                effective_content = None

            # For init messages, create a summary with key info
            if subtype in ("init", "initialized"):
                init_info = {
                    "session_id": parsed.get("session_id", ""),
                    "model": parsed.get("model", ""),
                    "permission_mode": parsed.get("permission_mode", ""),
                }
                # Remove empty fields
                init_info = {k: v for k, v in init_info.items() if v}

                # Preserve original content/message if present
                if effective_content is not None:
                    init_info["content"] = effective_content

                # Only store if there's meaningful info beyond subtype
                if init_info:
                    init_info["subtype"] = subtype
                    try:
                        content = json.dumps(init_info, ensure_ascii=False)
                    except (TypeError, ValueError) as e:
                        logger.warning(
                            "Failed to serialize system init message for %s: %s",
                            session_id[:8],
                            e,
                        )
                        return
                else:
                    # No meaningful info beyond subtype - skip storage
                    return
            else:
                # Non-init system messages: use effective_content directly
                content = effective_content
                if content is None:
                    return

            # Serialize content to string if needed
            if isinstance(content, dict):
                try:
                    content = json.dumps(content, ensure_ascii=False)
                except (TypeError, ValueError) as e:
                    logger.warning(
                        "Failed to serialize system message content for %s: %s",
                        session_id[:8],
                        e,
                    )
                    return
            elif isinstance(content, (list, int, float, bool)):
                # Convert other JSON-serializable types to string
                try:
                    content = json.dumps(content, ensure_ascii=False)
                except (TypeError, ValueError) as e:
                    logger.warning(
                        "Failed to serialize system message content for %s: %s",
                        session_id[:8],
                        e,
                    )
                    return
            elif not isinstance(content, str):
                # Non-serializable type - convert to string representation
                content = str(content)

            # Store if we have meaningful content
            if content and isinstance(content, str) and content.strip():
                stored = self._session_manager.append_transcript_message(
                    session_id=session_id,
                    role="system",
                    content=content,
                    source="remote_live",
                )
                if getattr(stored, "_was_inserted", False):
                    self._session_manager.increment_session_usage(session_id, message_delta=1)
                self._save_to_daily_messages(session_id, "system", content)

        elif msg_type == "result":
            # End of turn — flush accumulated assistant text
            self._flush_assistant_buffer(session_id)

    def _flush_assistant_buffer(self, session_id: str) -> None:
        """Flush accumulated assistant text to DB."""
        with self._buffer_lock:
            text = self._assistant_text_buffer.pop(session_id, "")
            blocks = self._content_blocks_buffer.pop(session_id, [])

        if text.strip():
            metadata = {}
            if blocks:
                metadata["content_blocks"] = blocks
            stored = self._session_manager.append_transcript_message(
                session_id=session_id,
                role="assistant",
                content=text,
                metadata=metadata if metadata else None,
                source="remote_live",
            )
            if getattr(stored, "_was_inserted", False):
                self._session_manager.increment_session_usage(session_id, message_delta=1)
            self._save_to_daily_messages(session_id, "assistant", text)

            # Record assistant_output once per turn (not per stdout chunk) and
            # derive tool_use events from the accumulated content blocks. These
            # run on the stdout hot path, so the recorder enqueues them to its
            # background writer rather than blocking on DB I/O.
            self._timeline(
                "record_event",
                session_id,
                "assistant_output",
                role="assistant",
                content=text,
                metadata={"content_blocks": blocks} if blocks else None,
            )
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self._timeline(
                        "record_event",
                        session_id,
                        "tool_use",
                        event_subtype=block.get("name"),
                        metadata={
                            "tool_use_id": block.get("id"),
                            "name": block.get("name"),
                            "input": block.get("input"),
                        },
                    )

    def process_usage_report(
        self, session_id: str, tokens: dict[str, int], requests: int = 1
    ) -> None:
        """Process usage report from a remote agent."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return

        # Update session token counts
        input_tokens = tokens.get("input", 0)
        output_tokens = tokens.get("output", 0)
        total = input_tokens + output_tokens

        self._session_manager.increment_session_usage(
            session_id,
            request_delta=requests,
            total_tokens_delta=total,
            total_input_delta=input_tokens,
            total_output_delta=output_tokens,
        )

        # Record the durable usage event with model/provider attribution
        # (key_id is NULL phase 1 — the agent does not report which key it used).
        self._timeline("record_usage", session_id, tokens, requests)

        # Record usage in QuotaManager
        if session.user_id:
            try:
                from app.modules.governance.quota_manager import QuotaManager

                quota_mgr = QuotaManager()
                quota_mgr.record_usage(
                    user_id=session.user_id,
                    tokens=total,
                    requests=requests,
                )
            except Exception as e:
                logger.error(f"Failed to record quota usage: {e}")

            # Refresh user_daily_stats so quota checks see up-to-date data
            try:
                from app.repositories.daily_stats_repo import DailyStatsRepository

                daily_stats_repo = DailyStatsRepository()
                daily_stats_repo.refresh_stats()
            except Exception as e:
                logger.warning(f"Failed to refresh daily stats after usage report: {e}")

    def process_permission_request(self, session_id: str, control_request: dict) -> None:
        """
        Process a permission request from the remote agent.

        The CLI permission request is treated as an INPUT EVENT (plan §0): the
        control plane owns the decision. Behaviour:

        - policy disabled (NullEvaluator): current path — buffer for human review.
        - ``allow`` / ``deny``: auto-resolve. The decision row is persisted
          synchronously and the response is dispatched through the single
          consume chokepoint (``respond_to_permission``); the request never
          reaches the frontend.
        - ``require_human``: persist the decision, then buffer for human review.

        The durable input-event record (``agent_approvals``) is always written.
        """
        # 1. Always record the input event (durable approval request + event).
        self._timeline("record_approval_request", session_id, control_request)

        # 2. Policy disabled → legacy manual-approval path.
        if self._policy_evaluator.is_noop:
            self._buffer_permission_for_human(session_id, control_request)
            return

        # 3. Evaluate tool/file/command policy (fail-closed on error).
        scope = self._session_policy_scope(session_id)
        ctx = PolicyContext(
            target_kind=TARGET_TOOL_ACTION,
            control_request=control_request,
            tenant_id=scope.get("tenant_id"),
            project_path=scope.get("project_path"),
            machine_id=scope.get("machine_id"),
            user_id=scope.get("user_id"),
            session_id=session_id,
            run_id=session_id,
        )
        result = self._policy_evaluator.evaluate(ctx)

        if result.requires_human:
            # Persist the require_human decision (consumed on human approval).
            self._persist_tool_decision(session_id, control_request, result, scope)
            self._buffer_permission_for_human(session_id, control_request)
            return

        # Auto allow/deny: persist the decision row, then resolve via the single
        # consume chokepoint so binding verification + single-use are enforced.
        self._persist_tool_decision(session_id, control_request, result, scope)

        from app.modules.policy.fingerprint import extract_request_fields

        fields = extract_request_fields(control_request)
        request_id = fields.get("request_id")
        tool_name = fields.get("tool") or ""
        behavior = "allow" if result.is_allow else "deny"
        logger.info(
            "Policy auto-%s for session %s tool=%s (rule=%s)",
            behavior,
            session_id[:8],
            tool_name,
            result.matched_rule.rule_key if result.matched_rule else "fallback",
        )
        self.respond_to_permission(
            session_id,
            request_id,
            behavior,
            tool_name,
            message=result.reason,
            decided_by=None,
            decided_by_name=_POLICY_SYSTEM_ACTOR,
        )

    def process_request_state(
        self,
        session_id: str,
        state: str,
        reason: str = "user",
        message: Optional[str] = None,
    ) -> None:
        """Buffer a request lifecycle event for SSE delivery to the frontend."""
        if state not in self._allowed_request_states:
            logger.warning(
                "Ignoring unsupported request_state for session %s: %s",
                session_id[:8],
                state,
            )
            return

        payload: dict[str, Any] = {
            "type": state,
            "reason": reason,
        }
        if message:
            payload["message"] = message

        output_entry = {
            "session_id": session_id,
            "data": json.dumps(payload, ensure_ascii=False),
            "stream": "request_state",
            "is_complete": state == "abort_failed",
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }

        self._agent_manager.buffer_output(session_id, output_entry)
        logger.info(
            "Buffered request_state for session %s: %s (reason=%s)",
            session_id[:8],
            state,
            reason,
        )

    def process_session_status_update(
        self, session_id: str, status: str, pid: Optional[int] = None
    ) -> None:
        """Process a session status update from a remote agent."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return

        if status in ("running", "active"):
            session.status = "active"
            session.paused_at = None
        elif status == "paused":
            session.status = "paused"
            if not session.paused_at:
                session.paused_at = datetime.now(timezone.utc).replace(tzinfo=None)
        elif status == "stopped":
            # User-initiated stop — finalize the session
            session.status = "completed"
            session.paused_at = None
            self._agent_manager.unbind_session(session_id)
            self._agent_manager.mark_session_ended(session_id)
            # Clean up permission mode cache
            self._session_permission_modes.pop(session_id, None)
        elif status in ("completed", "exited"):
            # CLI exited after a response — keep session active so the user
            # can send follow-up messages (executor restarts the CLI).
            session.status = "active"
        elif status == "error":
            session.status = "error"
            session.paused_at = None
            self._agent_manager.mark_session_ended(session_id)
            # Clean up permission mode cache
            self._session_permission_modes.pop(session_id, None)

        self._session_manager.update_session(session)

        # Record terminal lifecycle events (stop / error). The "completed/exited"
        # branch keeps the session active, so only genuine stops/errors are logged.
        if session.status == "completed":
            self._timeline("record_run_status", session_id, "stopped")
        elif session.status == "error":
            self._timeline("record_run_status", session_id, "error")

    def _cli_tool_to_provider(self, cli_tool: str) -> str:
        """Map CLI tool name to LLM provider name."""
        mapping = {
            "qwen-code-cli": "openai",
            "claude-code": "anthropic",
            "openclaw": "openai",
            "codex": "openai",
            "codex-cli": "openai",
            "zcode": "anthropic",
            "zcode-code": "anthropic",
        }
        return mapping.get(cli_tool, "openai")

    def _get_user_name(self, user_id: Optional[int]) -> str:
        """Get user display name with caching."""
        if not user_id:
            return ""
        if user_id in self._user_name_cache:
            return self._user_name_cache[user_id]
        try:
            user = self._user_repo.get_user_by_id(user_id)
            name = str(user.get("display_name") or user.get("username", "")) if user else ""
            self._user_name_cache[user_id] = name
            return name
        except Exception:
            return ""

    def _save_to_daily_messages(
        self, session_id: str, role: str, content: str, tokens_used: int = 0
    ) -> None:
        """Mirror a message to daily_messages so it appears in manage pages."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            self._message_repo.save_message(
                date=now.strftime("%Y-%m-%d"),
                tool_name=normalize_tool_name(session.tool_name or "unknown"),
                message_id=str(uuid.uuid4()),
                role=role,
                host_name=session.host_name or "remote",
                content=content,
                full_entry=json.dumps(
                    {"session_id": session_id, "role": role, "content": content},
                    ensure_ascii=False,
                ),
                tokens_used=tokens_used,
                model=session.model,
                timestamp=now.isoformat(),
                sender_id=str(session.user_id) if session.user_id else None,
                sender_name=self._get_user_name(session.user_id),
                message_source="remote_workspace",
                agent_session_id=session_id,
                conversation_id=session_id,
                user_id=session.user_id,
                project_path=session.project_path,
            )
        except Exception as e:
            logger.warning(f"Failed to mirror message to daily_messages for {session_id[:8]}: {e}")


# Global singleton
_remote_session_manager: Optional["RemoteSessionManager"] = None


def get_remote_session_manager() -> "RemoteSessionManager":
    """Get the global RemoteSessionManager instance."""
    global _remote_session_manager
    if _remote_session_manager is None:
        _remote_session_manager = RemoteSessionManager()
    return _remote_session_manager
