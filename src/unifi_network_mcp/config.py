import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    url: str
    api_key: str = ""
    username: str = ""
    password: str = ""
    ca_cert: str = ""
    allow_writes: bool = False
    internal_enabled: bool = False
    state_dir: Path = Path("work/unifi-state")
    spec_file: str = ""
    internal_manifest: str = ""
    timeout: float = 30
    tls_server_name: str = ""
    internal_auth: str = "session"

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError("UNIFI_URL must be an HTTPS console origin, e.g. https://192.168.1.1")
        if self.internal_auth not in ("session", "api_key"):
            raise ValueError("UNIFI_INTERNAL_AUTH must be session or api_key")
        if self.internal_enabled and self.internal_auth == "session" and not (self.username and self.password):
            raise ValueError("Internal Network APIs need UNIFI_USERNAME and UNIFI_PASSWORD")
        if self.internal_enabled and self.internal_auth == "api_key" and not self.api_key:
            raise ValueError("Internal API-key authentication requires UNIFI_API_KEY")
        if self.tls_server_name and (not self.ca_cert or not re.fullmatch(r"[A-Za-z0-9.-]+", self.tls_server_name)):
            raise ValueError("TLS server name requires a console CA certificate and a DNS name")

    @classmethod
    def from_env(cls):
        return cls(
            url=os.environ["UNIFI_URL"].rstrip("/"),
            api_key=os.getenv("UNIFI_API_KEY", ""),
            username=os.getenv("UNIFI_USERNAME", ""),
            password=os.getenv("UNIFI_PASSWORD", ""),
            ca_cert=os.getenv("UNIFI_CA_CERT", ""),
            allow_writes=os.getenv("UNIFI_ALLOW_WRITES", "false").lower() == "true",
            internal_enabled=os.getenv("UNIFI_INTERNAL_ENABLED", "false").lower() == "true",
            state_dir=Path(os.getenv("UNIFI_STATE_DIR", "work/unifi-state")),
            spec_file=os.getenv("UNIFI_SPEC_FILE", ""),
            internal_manifest=os.getenv("UNIFI_INTERNAL_MANIFEST", ""),
            tls_server_name=os.getenv("UNIFI_TLS_SERVER_NAME", ""),
            internal_auth=os.getenv("UNIFI_INTERNAL_AUTH", "session"),
        )
