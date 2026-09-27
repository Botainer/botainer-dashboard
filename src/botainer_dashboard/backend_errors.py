"""Dependency-free fixed public backend errors shared by service and adapters."""

import re


class BackendUnavailable(RuntimeError):
    """A fixed public code, never raw command output, credentials or paths."""

    def __init__(self, code: str = "capability-unavailable"):
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", code):
            raise ValueError("backend error must be a fixed code")
        self.code = code
        super().__init__(code)
