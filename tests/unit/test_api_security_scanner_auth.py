"""Gate tests for the signature-verification authentication recognition."""

import ast
import textwrap

from scripts.lint.api_security_scanner import AUTH_INLINE_CALLS, APISecurityScanner


def _route_has_inline_auth(source: str, func_name: str) -> bool:
    scanner = APISecurityScanner.__new__(APISecurityScanner)
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return scanner._has_auth_in_body(node)
    raise AssertionError(f"function {func_name} not found")


def test_auth_inline_calls_contains_only_one_signature_verifier():
    # Bare-name matching: every entry authenticates anything that calls a
    # function of that name, so additions must stay minimal and justified.
    assert "verify_signed_request" in AUTH_INLINE_CALLS
    assert AUTH_INLINE_CALLS - {
        "require_auth",
        "require_admin",
        "validate_session",
        "get_session",
        "require_upload_auth",
    } == {"verify_signed_request"}


def test_signed_route_counts_as_authenticated():
    source = """
    @external_identity_bp.route("/integrations/external/capabilities", methods=["POST"])
    def capabilities():
        result = verify_signed_request(policy, request.headers, raw, replay, accounts, tenants)
        return jsonify(result)
    """
    assert _route_has_inline_auth(source, "capabilities")


def test_session_reading_helper_does_not_confer_auth():
    # A helper that merely reads the session (get_session is auth-listed for
    # historical reasons) enforces nothing; a route calling it must stay
    # flagged by SEC001.
    source = """
    def _audit_actor():
        s = get_session()
        return s.get("user_id") if s else None

    @bp.route("/demo/danger", methods=["POST"])
    def danger():
        _audit_actor()
        return jsonify({"deleted": True})
    """
    assert not _route_has_inline_auth(source, "danger")
