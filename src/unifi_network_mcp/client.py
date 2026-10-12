import asyncio
import base64
import json
import re
import ssl
from urllib.parse import unquote

import httpx

from .config import Config


SECRET_KEY = re.compile(r"password|passphrase|secret|token|private.?key|api.?key|community|pre.?shared", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_KEY.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class UniFiError(RuntimeError):
    pass


class UniFiRejected(UniFiError):
    """An explicit API rejection, rather than an ambiguous transport/server failure."""


def validate_path(path: str, adapter: str):
    prefix = "/proxy/network/integration/v1/" if adapter == "official" else "/proxy/network/"
    if not path.startswith(prefix) or re.search(r"[%\\?#\x00-\x20]", path):
        raise ValueError("Only literal Network API paths on the configured console are allowed")
    if any(part in (".", "..", "") for part in path.split("/")[1:]):
        raise ValueError("Invalid API path segments")
    if adapter == "internal" and not re.match(r"^/proxy/network/(?:api/(?:s/[A-Za-z0-9_-]+/|self/sites$)|v2/api/site/[A-Za-z0-9_-]+/)", path):
        raise ValueError("Internal route must stay within Network site APIs")


class UniFiClient:
    def __init__(self, config: Config, transport=None):
        self.config = config
        context = ssl.create_default_context(cafile=config.ca_cert or None)
        async def configure_tls_name(request):
            if config.tls_server_name:
                request.extensions["sni_hostname"] = config.tls_server_name
        opts = dict(base_url=config.url, verify=context, timeout=config.timeout,
                    follow_redirects=False, trust_env=False, transport=transport,
                    event_hooks={"request": [configure_tls_name]})
        # Separate clients isolate session cookies; internal keys require explicit api_key mode.
        self.official = httpx.AsyncClient(**opts, headers={"X-API-Key": config.api_key})
        self.internal = httpx.AsyncClient(**opts, headers={"X-API-Key": config.api_key}
                                         if config.internal_auth == "api_key" else {})
        self.logged_in = False
        self.login_lock = asyncio.Lock()
        self.csrf = ""

    async def close(self):
        await self.official.aclose()
        await self.internal.aclose()

    def capture_csrf(self, response):
        header = response.headers.get("x-csrf-token")
        if header:
            self.csrf = header
        else:
            for cookie in self.internal.cookies.jar:
                if cookie.name == "TOKEN":
                    try:
                        part = unquote(cookie.value).split(".")[1]
                        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
                        self.csrf = claims.get("csrfToken", "")
                    except (ValueError, IndexError, TypeError):
                        pass

    async def login(self):
        async with self.login_lock:
            if self.logged_in:
                return
            self.internal.cookies.clear()
            self.csrf = ""
            response = await self.internal.post("/api/auth/login", json={
                "username": self.config.username, "password": self.config.password})
            self.decode(response)
            self.capture_csrf(response)
            if not self.internal.cookies:
                raise UniFiError("Login returned no session cookie; use a local automation account")
            self.logged_in = True

    def invalidate_session(self):
        self.logged_in = False
        self.csrf = ""
        self.internal.cookies.clear()

    @staticmethod
    def decode(response):
        if not 200 <= response.status_code < 300:
            # Never echo response bodies: errors may contain credentials or submitted data.
            error = UniFiRejected if 400 <= response.status_code < 500 and response.status_code != 408 else UniFiError
            raise error(f"UniFi HTTP {response.status_code}; check permissions, version, and API availability")
        if response.status_code == 204 or not response.content:
            return None
        try:
            value = response.json()
        except ValueError:
            raise UniFiError("UniFi returned a non-JSON response") from None
        if isinstance(value, dict) and value.get("meta", {}).get("rc") == "error":
            raise UniFiRejected("UniFi rejected the operation (legacy meta.rc=error)")
        return value

    async def request(self, adapter, method, path, query=None, body=None):
        validate_path(path, adapter)
        if adapter == "official":
            if not self.config.api_key:
                raise UniFiError("Set UNIFI_API_KEY for official Network operations")
            client = self.official
        elif adapter == "internal":
            if not self.config.internal_enabled:
                raise UniFiError("Internal adapter is disabled; set UNIFI_INTERNAL_ENABLED=true")
            if self.config.internal_auth == "session":
                await self.login()
            client = self.internal
        else:
            raise ValueError("Unknown adapter")
        headers = {}
        session_auth = adapter == "internal" and self.config.internal_auth == "session"
        if session_auth and method != "GET":
            if not self.csrf:
                raise UniFiError("No CSRF token was provided; refusing an internal write")
            headers["X-CSRF-Token"] = self.csrf
        try:
            response = await client.request(method, path, params=query, json=body, headers=headers)
            if session_auth:
                self.capture_csrf(response)
                if response.status_code == 401:
                    self.invalidate_session()
                    # Invalidation applies to all methods; only reads may retry.
                    if method == "GET":
                        await self.login()
                        response = await client.request(method, path, params=query)
                        self.capture_csrf(response)
                        if response.status_code == 401:
                            self.invalidate_session()
            return self.decode(response)
        except httpx.RequestError:
            raise UniFiError("Console request failed or timed out. A submitted write may have applied; inspect state before retrying.") from None
