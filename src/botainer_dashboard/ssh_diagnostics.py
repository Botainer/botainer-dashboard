"""Bounded SSH failure hints, never lifecycle or dispatch evidence.

SSH stderr is untrusted and can contain private paths, hostnames, banners and
remote output. Match only recognizable failures, return repository-authored
text, and leave raw evidence in the existing private receipt. A remote command
can itself exit 255 or print an SSH-looking message: every result is advisory.
"""
from __future__ import annotations

import json
import re

from .backend_errors import BackendUnavailable


_DIAGNOSTICS = {
    "checking": (
        "Checking the remote connection.",
        "The dashboard uses the configured SSH alias and does not answer login prompts."),
    "ssh-authentication-required": (
        "SSH could not finish authentication.",
        "Open the configured SSH alias in your system terminal and complete its normal key, passphrase or MFA prompts. "
        "Keep that terminal open if SSH connection sharing is not configured to persist after it closes. Then refresh."),
    "ssh-host-key-unverified": (
        "SSH could not verify the server host key.",
        "Verify the host fingerprint through your site's trusted instructions before accepting it in your system terminal. "
        "The dashboard does not accept host keys automatically."),
    "ssh-host-key-changed": (
        "SSH reported a changed server host key.",
        "Verify the change with the site's trusted documentation or administrator. "
        "Do not bypass host-key checking or delete a known key just to reconnect."),
    "ssh-name-resolution-failed": (
        "SSH could not resolve the configured host.",
        "Check the SSH alias, network and any required VPN. Restore the connection, then refresh."),
    "ssh-connection-refused": (
        "The SSH connection was refused.",
        "Check the configured host and port, network access and site status. Then refresh."),
    "ssh-network-unreachable": (
        "SSH could not reach the configured host.",
        "Check network access and any required VPN. Restore the connection, then refresh."),
    "ssh-connection-lost": (
        "The SSH connection or shared connection ended.",
        "Check the network, then reopen the configured SSH alias in your system terminal if login is needed. "
        "Refresh to verify the original sessions; losing this connection does not establish that a job ended."),
    "ssh-timeout": (
        "The remote connection or request exceeded its time limit.",
        "Check the network, required VPN and site status. A slow remote check can also time out. "
        "Refresh to reconcile the original request; do not resubmit an uncertain launch."),
    "ssh-local-client-unavailable": (
        "The local SSH process could not start.",
        "Check that the system SSH client is available and permitted to run. "
        "No software is installed or SSH configuration changed automatically."),
    "ssh-request-failed": (
        "The SSH connection or remote request failed.",
        "Check the configured SSH alias in your system terminal and inspect the private diagnostic receipt. "
        "Refresh to reconcile existing work before starting another session."),
    "remote-source-changed": (
        "SSH reached the remote helper, but the selected Botainer source could not be verified.",
        "The installation may have been updated or moved. Open Settings → Machines → Review Botainer update to inspect "
        "the intended installation and review its changes. Changed code is never approved automatically."),
    "remote-launcher-changed": (
        "SSH reached the remote helper, but the selected Botainer launcher could not be verified.",
        "The launcher may now select a different installation. Open Settings → Machines → Review Botainer update to inspect "
        "the intended installation. A new SSH login alone will not resolve this check."),
    "remote-interpreter-changed": (
        "SSH reached the remote helper, but the selected Botainer Python interpreter could not be verified.",
        "After an intentional update, open Settings → Machines → Review Botainer update and inspect the interpreter used by "
        "that installation. The dashboard never selects another interpreter automatically."),
    "remote-plugins-changed": (
        "SSH reached the remote helper, but the installed Botainer plugins could not be verified.",
        "Review the installed plugins through machine setup in Settings → Machines. The Botainer update review does not "
        "approve changes to installed state plugins or hooks. No plugin is enabled or approved automatically."),
    "remote-installation-unsafe": (
        "Remote installation verification refused unsafe ownership or write permissions.",
        "Selected code or a containing folder is not protected from other accounts, or cannot be read safely. "
        "Use a protected installation or ask its administrator to review ownership and permissions. Do not disable verification."),
    "remote-import-layout-unsupported": (
        "The remote Botainer installation has an unsupported import layout.",
        "Review the selected installation for ambiguous package layouts, linked modules or unreviewed executable files. "
        "Use a supported installation and inspect it again; a new SSH login alone will not resolve this check."),
    "remote-check-failed": (
        "The remote Botainer check did not complete.",
        "Inspect the private diagnostic receipt and selected workspace configuration. "
        "A new SSH login alone may not resolve a remote helper or scheduler problem."),
}


def connection_diagnostic(code: str) -> dict[str, str]:
    """Return fresh fixed text; unknown codes cannot become public text."""
    if code not in _DIAGNOSTICS:
        code = "remote-check-failed"
    message, recovery = _DIAGNOSTICS[code]
    return {"code": code, "message": message, "recovery": recovery}


class SshRequestUnavailable(BackendUnavailable):
    """Retain the existing public failure code with a safe advisory category."""

    def __init__(self, code: str, diagnostic_code: str):
        super().__init__(code)
        self.diagnostic_code = connection_diagnostic(diagnostic_code)["code"]


# This is an advisory protocol, not an attestation or an operation receipt.
# Only the fixed helper produces it, and only complete nonzero exchanges may
# consume it. A remote account can still forge output; no control is enabled
# and no launch outcome is inferred from a diagnostic.
_REMOTE_HELPER_CODES = frozenset({
    "remote-source-changed", "remote-launcher-changed", "remote-interpreter-changed",
    "remote-plugins-changed", "remote-installation-unsafe", "remote-import-layout-unsupported",
    "remote-check-failed",
})


def classify_remote_helper_failure(result: dict, stdout: bytes) -> str | None:
    """Read one bounded, exact error envelope; reject raw or terminal output."""
    if (result.get("transport_status") != "complete" or type(result.get("returncode")) is not int
            or result["returncode"] != 2 or not isinstance(stdout, bytes) or len(stdout) > 512):
        return None

    def unique_object(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError("Duplicate error-envelope member")
            value[name] = item
        return value

    try:
        value = json.loads(stdout, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if (not isinstance(value, dict) or set(value) != {"protocol", "version", "code"}
            or value["protocol"] != "botainer-dashboard.remote-error"
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["code"], str) or value["code"] not in _REMOTE_HELPER_CODES):
        return None
    return value["code"]


def classify_ssh_failure(result: dict, stderr: bytes) -> str:
    """Classify a bounded transport failure without asserting remote outcome.

    Normal remote-helper exit codes are not classified from their stderr. This
    prevents an application error such as 'permission denied' from being
    presented as a login failure. Even exit 255 is only an SSH-like hint.
    """
    status = result.get("transport_status")
    if status == "start-error":
        return "ssh-local-client-unavailable"
    if status == "timeout":
        return "ssh-timeout"
    if status != "complete" or result.get("returncode") != 255:
        return "remote-check-failed"
    # Match only bounded text; never interpolate it in a public result.
    text = stderr[:65536].decode("utf-8", errors="replace").lower() if isinstance(stderr, bytes) else ""
    if re.search(r"(?m)^@*\s*warning: remote host identification has changed!", text):
        return "ssh-host-key-changed"
    if re.search(r"(?m)^host key verification failed\.?\r?$", text):
        return "ssh-host-key-unverified"
    if re.search(r"(?m)^[^\r\n:]+: permission denied \([a-z0-9, -]+\)\.\r?$", text):
        return "ssh-authentication-required"
    if re.search(r"(?m)^ssh: could not resolve hostname [^\r\n]+:", text):
        return "ssh-name-resolution-failed"
    connect = re.search(r"(?m)^ssh: connect to host [^\r\n]+ port [0-9]+: ([^\r\n]+)", text)
    if connect:
        reason = connect.group(1)
        if reason == "connection refused":
            return "ssh-connection-refused"
        if reason in {"no route to host", "network is unreachable"}:
            return "ssh-network-unreachable"
        if reason in {"connection timed out", "operation timed out"}:
            return "ssh-timeout"
    if re.search(r"(?m)^(?:connection (?:closed|reset) by |read from remote host [^\r\n]+: |"
                 r"client_loop: send disconnect: |mux_client_request_session: |"
                 r"control socket connect\([^\r\n]+\): )", text):
        return "ssh-connection-lost"
    return "ssh-request-failed"
