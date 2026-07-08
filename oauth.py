"""Minimal stateless OAuth 2.1 authorization server for a single-user MCP deployment.

Implements what the MCP authorization spec requires from a remote server so
claude.ai / Claude Desktop can add it as a custom connector:

- RFC 9728 protected-resource metadata and RFC 8414 authorization-server metadata
- RFC 7591 dynamic client registration (accept-all; clients are public and use PKCE)
- Authorization-code flow with PKCE (S256 only) plus refresh tokens

There is no user database: "logging in" means presenting the MCP shared secret
on the consent page. Authorization codes and tokens are self-contained
HMAC-signed blobs, so the server needs no storage and stays valid across
free-tier restarts; rotating MCP_SHARED_SECRET revokes everything at once.
"""

import base64
import hashlib
import hmac
import html
import json
import logging
import secrets
import time
from urllib.parse import urlencode, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

AUTH_CODE_TTL = 300
ACCESS_TOKEN_TTL = 3600
REFRESH_TOKEN_TTL = 30 * 24 * 3600

# Claude's custom-connector OAuth callbacks. Loopback http redirects are also
# accepted (RFC 8252) so Claude Code / MCP Inspector can complete the flow.
DEFAULT_ALLOWED_REDIRECTS = frozenset({
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
})

CONSENT_PAGE = """<!doctype html>
<html><head><title>Authorize google-maps MCP</title>
<style>
  body {{ font-family: system-ui, sans-serif; display: flex; justify-content: center;
         padding-top: 10vh; background: #f5f5f4; color: #1c1917; }}
  form {{ background: #fff; border: 1px solid #d6d3d1; border-radius: 12px;
          padding: 2rem; max-width: 22rem; }}
  input[type=password] {{ width: 100%; padding: .5rem; margin: .75rem 0;
          border: 1px solid #a8a29e; border-radius: 6px; box-sizing: border-box; }}
  button {{ width: 100%; padding: .6rem; border: 0; border-radius: 6px;
          background: #1c1917; color: #fff; font-size: 1rem; cursor: pointer; }}
  .err {{ color: #b91c1c; }}
  .host {{ font-family: monospace; }}
</style></head>
<body><form method="post" action="/authorize">
<h2>google-maps MCP server</h2>
<p><span class="host">{redirect_host}</span> is requesting access.</p>
<p>Enter the server's shared secret to approve:</p>
{error_line}
<input type="password" name="secret" autofocus required>
{hidden_fields}
<button type="submit">Authorize</button>
</form></body></html>
"""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class OAuthProvider:
    def __init__(self, base_url: str, shared_secret: str, extra_redirects: set[str] = frozenset()):
        self.base_url = base_url.rstrip("/")
        self.shared_secret = shared_secret
        self._key = hashlib.sha256(b"mcp-oauth-signing:" + shared_secret.encode()).digest()
        self.allowed_redirects = set(DEFAULT_ALLOWED_REDIRECTS) | set(extra_redirects)
        # Best-effort replay guard; being in-memory it resets on restart, but
        # codes also expire after AUTH_CODE_TTL and require the PKCE verifier.
        self._used_codes: dict[str, float] = {}

    # --- signed blobs -----------------------------------------------------

    def _sign(self, payload: dict) -> str:
        body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        sig = _b64url(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def _verify(self, blob: str, expected_type: str) -> dict | None:
        try:
            body, sig = blob.split(".", 1)
            expected_sig = _b64url(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, expected_sig):
                return None
            payload = json.loads(_b64url_decode(body))
        except (ValueError, TypeError):
            return None
        if payload.get("t") != expected_type or payload.get("exp", 0) < time.time():
            return None
        return payload

    def verify_access_token(self, token: str) -> bool:
        return self._verify(token, "access") is not None

    def _issue_tokens(self) -> dict:
        now = int(time.time())
        return {
            "access_token": self._sign({"t": "access", "exp": now + ACCESS_TOKEN_TTL,
                                        "n": secrets.token_urlsafe(8)}),
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL,
            "refresh_token": self._sign({"t": "refresh", "exp": now + REFRESH_TOKEN_TTL,
                                         "n": secrets.token_urlsafe(8)}),
            "scope": "mcp",
        }

    # --- validation helpers -----------------------------------------------

    def _redirect_allowed(self, uri: str) -> bool:
        if uri in self.allowed_redirects:
            return True
        parts = urlsplit(uri)
        return (parts.scheme == "http"
                and parts.hostname in ("localhost", "127.0.0.1")
                and not parts.fragment)

    # --- endpoints ----------------------------------------------------------

    async def metadata_authorization_server(self, request: Request) -> JSONResponse:
        return JSONResponse({
            "issuer": self.base_url,
            "authorization_endpoint": f"{self.base_url}/authorize",
            "token_endpoint": f"{self.base_url}/token",
            "registration_endpoint": f"{self.base_url}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post",
                                                      "client_secret_basic"],
            "scopes_supported": ["mcp"],
        })

    async def metadata_protected_resource(self, request: Request) -> JSONResponse:
        return JSONResponse({
            "resource": f"{self.base_url}/mcp",
            "authorization_servers": [self.base_url],
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
        })

    async def register(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
        # Public clients with PKCE: nothing to store, any client_id works.
        # Security comes from the consent secret + PKCE + redirect allowlist.
        # Honor the client's requested auth method for compatibility; a
        # client_secret is issued but never validated at /token.
        auth_method = body.get("token_endpoint_auth_method", "none")
        response = {
            "client_id": secrets.token_urlsafe(16),
            "client_id_issued_at": int(time.time()),
            "token_endpoint_auth_method": auth_method,
            "redirect_uris": body.get("redirect_uris", []),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }
        if auth_method != "none":
            response["client_secret"] = secrets.token_urlsafe(32)
            response["client_secret_expires_at"] = 0
        if "client_name" in body:
            response["client_name"] = body["client_name"]
        logger.info("Registered OAuth client %s (%s)",
                    response["client_id"], body.get("client_name", "unnamed"))
        return JSONResponse(response, status_code=201)

    async def authorize(self, request: Request):
        if request.method == "GET":
            params = dict(request.query_params)
        else:
            form = await request.form()
            params = {k: str(v) for k, v in form.items()}

        redirect_uri = params.get("redirect_uri", "")
        problem = None
        if params.get("response_type") != "code":
            problem = "unsupported response_type; only 'code' is supported"
        elif not self._redirect_allowed(redirect_uri):
            problem = "redirect_uri is not on this server's allowlist"
        elif not params.get("code_challenge"):
            problem = "code_challenge (PKCE) is required"
        elif params.get("code_challenge_method", "S256") != "S256":
            problem = "only code_challenge_method=S256 is supported"
        if problem:
            return HTMLResponse(f"<h1>400</h1><p>{html.escape(problem)}</p>", status_code=400)

        error_line = ""
        if request.method == "POST":
            secret = params.pop("secret", "")
            if hmac.compare_digest(secret, self.shared_secret):
                code = self._sign({
                    "t": "code",
                    "exp": int(time.time()) + AUTH_CODE_TTL,
                    "r": redirect_uri,
                    "c": params["code_challenge"],
                    "n": secrets.token_urlsafe(8),
                })
                query = {"code": code}
                if params.get("state"):
                    query["state"] = params["state"]
                separator = "&" if urlsplit(redirect_uri).query else "?"
                logger.info("Issued authorization code for %s", urlsplit(redirect_uri).netloc)
                return RedirectResponse(f"{redirect_uri}{separator}{urlencode(query)}",
                                        status_code=302)
            logger.warning("Consent page rejected: wrong shared secret")
            error_line = '<p class="err">Wrong secret, try again.</p>'

        hidden_fields = "\n".join(
            f'<input type="hidden" name="{html.escape(k, quote=True)}" '
            f'value="{html.escape(v, quote=True)}">'
            for k, v in params.items() if k != "secret"
        )
        page = CONSENT_PAGE.format(
            redirect_host=html.escape(urlsplit(redirect_uri).netloc),
            error_line=error_line,
            hidden_fields=hidden_fields,
        )
        return HTMLResponse(page, status_code=401 if error_line else 200)

    async def token(self, request: Request) -> JSONResponse:
        form = await request.form()
        grant_type = form.get("grant_type")
        headers = {"Cache-Control": "no-store"}

        if grant_type == "authorization_code":
            code = str(form.get("code", ""))
            payload = self._verify(code, "code")
            if payload is None or code in self._used_codes:
                return JSONResponse({"error": "invalid_grant"}, status_code=400, headers=headers)
            sent_redirect = form.get("redirect_uri")
            if sent_redirect and sent_redirect != payload["r"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400, headers=headers)
            verifier = str(form.get("code_verifier", ""))
            challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
            if not hmac.compare_digest(challenge, payload["c"]):
                logger.warning("Token exchange rejected: PKCE verifier mismatch")
                return JSONResponse({"error": "invalid_grant"}, status_code=400, headers=headers)
            now = time.time()
            self._used_codes = {c: exp for c, exp in self._used_codes.items() if exp > now}
            self._used_codes[code] = payload["exp"]
            logger.info("Exchanged authorization code for tokens")
            return JSONResponse(self._issue_tokens(), headers=headers)

        if grant_type == "refresh_token":
            if self._verify(str(form.get("refresh_token", "")), "refresh") is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400, headers=headers)
            return JSONResponse(self._issue_tokens(), headers=headers)

        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400, headers=headers)

    def routes(self) -> list[Route]:
        # Metadata is served both at the root and suffixed with the resource
        # path (/mcp), since RFC 9728/8414 clients derive either form.
        return [
            Route("/.well-known/oauth-authorization-server",
                  self.metadata_authorization_server, methods=["GET"]),
            Route("/.well-known/oauth-authorization-server/mcp",
                  self.metadata_authorization_server, methods=["GET"]),
            Route("/.well-known/oauth-protected-resource",
                  self.metadata_protected_resource, methods=["GET"]),
            Route("/.well-known/oauth-protected-resource/mcp",
                  self.metadata_protected_resource, methods=["GET"]),
            Route("/register", self.register, methods=["POST"]),
            Route("/authorize", self.authorize, methods=["GET", "POST"]),
            Route("/token", self.token, methods=["POST"]),
        ]
