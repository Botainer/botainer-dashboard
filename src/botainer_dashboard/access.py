"""Pure checks for the future loopback HTTP/WebSocket boundary.

This is not a server or authentication bootstrap implementation. The HTTP adapter
must reject duplicate security headers, extract credentials without URL tokens,
and invoke these checks before accepting a WebSocket or dispatching an action.
Do not add permissive CORS. GET handlers must remain side-effect free.
"""

from dataclasses import dataclass, field
import hmac
import secrets
from typing import Optional


class AccessDenied(PermissionError):
    """A fixed reason code only; never echo credentials or untrusted headers."""


@dataclass(frozen=True)
class LoopbackAccess:
    port: int
    token: str = field(repr=False)

    def __post_init__(self):
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be the actual bound TCP port")
        if (not isinstance(self.token, str) or len(self.token) < 43
                or len(self.token) > 128 or not self.token.isascii()
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                       for c in self.token)):
            raise ValueError("token must be a strong URL-safe secret")

    @classmethod
    def create(cls, port: int):
        return cls(port, secrets.token_urlsafe(32))

    @property
    def authority(self):
        return f"127.0.0.1:{self.port}"

    @property
    def origin(self):
        return f"http://{self.authority}"

    def authorize(self, *, host: str, origin: Optional[str], token: str,
                  require_origin: bool) -> None:
        """Require exact host and secret; origin mandatory for writes and WS.

        Origin-less reads may be accepted only by read-only routes. Any supplied
        origin must match, even on reads. The adapter decides route category,
        never a value received from the browser. No forwarded-host headers or
        alternate localhost names are trusted by this first loopback profile.
        """
        if type(require_origin) is not bool:
            raise ValueError("route origin policy must be explicit")
        if host != self.authority:
            raise AccessDenied("invalid-host")
        if (origin is None and require_origin) or (origin is not None and origin != self.origin):
            raise AccessDenied("invalid-origin")
        if (not isinstance(token, str) or not token.isascii()
                or len(token) != len(self.token)
                or not hmac.compare_digest(token, self.token)):
            raise AccessDenied("invalid-credential")
