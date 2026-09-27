#!/usr/bin/env python3
"""Fixed, non-launching adapter to the selected Botainer's exact config checks.

Run only via Workspace.BotainerValidator with an identity-checked interpreter,
source and private request file. No packages are installed or downloaded here.
The original project is never written during validation. A staged copy holds
the exact unsaved text; Botainer's loader and config-check command validate it.
"""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys

MAX_TEXT = 64 * 1024


def reject_ambiguous_yaml(text, yaml):
    """Bound YAML structure and reject aliases, tags and duplicate map keys.

    Botainer remains authoritative for schema and policy. These transport guards
    are deliberately stricter: a compact alias graph can expand beyond the text
    budget, and silently keeping a duplicate's last value makes raw edits unclear.
    """
    depth = 0
    count = 0
    for event in yaml.parse(text):
        count += 1
        if count > 12000:
            raise ValueError("YAML contains too many values.")
        if getattr(event, "anchor", None) is not None or isinstance(event, yaml.events.AliasEvent):
            raise ValueError("YAML aliases and anchors are not supported in the dashboard editor.")
        if getattr(event, "tag", None) is not None:
            raise ValueError("Explicit YAML tags are not supported in the dashboard editor.")
        if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
            depth += 1
            if depth > 32:
                raise ValueError("YAML nesting exceeds 32 levels.")
        elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
            depth -= 1
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    stack = [node] if node is not None else []
    while stack:
        current = stack.pop()
        if isinstance(current, yaml.nodes.MappingNode):
            keys = set()
            for key, value in current.value:
                if not isinstance(key, yaml.nodes.ScalarNode):
                    raise ValueError("Config mapping keys must be simple strings.")
                identity = (key.tag, key.value)
                if identity in keys or key.value == "<<":
                    raise ValueError("Duplicate keys and YAML merge keys are not supported.")
                keys.add(identity)
                stack.append(value)
        elif isinstance(current, yaml.nodes.SequenceNode):
            stack.extend(current.value)


def validate(request, folder):
    # These are trusted launcher values, never configurable from an HTTP body.
    source = Path(request["sourceRoot"])
    if not source.is_absolute() or source.resolve(strict=True) != source or not (source / "botainer").is_dir():
        raise ValueError("Reviewed Botainer source is unavailable.")
    sys.path.insert(0, str(source))
    import yaml
    from botainer.core import config as config_module
    from botainer.core.refusal import Refused
    from botainer.cli import _common
    from botainer.cli.config_cmd import check
    from botainer.cli._history_prompt import mode_for_agent

    errors, warnings = [], []
    text = request["text"]
    previous = request["previousText"]
    for value in (text, previous):
        if value is not None and (not isinstance(value, str) or len(value.encode("utf-8")) > MAX_TEXT or "\x00" in value):
            raise ValueError("Config must be UTF-8 text of at most 64 KiB.")
    reject_ambiguous_yaml(text, yaml)
    raw = yaml.safe_load(text) or {}
    # Keep the cross-interpreter message strict JSON, never Python-specific YAML
    # objects such as timestamps. Botainer's typed model is still checked below.
    json.dumps(raw, allow_nan=False)
    project = folder / "candidate"
    config_dir = project / ".botainer"
    config_dir.mkdir(parents=True, mode=0o700)
    config_path = config_dir / "config.yaml"
    config_path.write_text(text, encoding="utf-8")
    config_path.chmod(0o600)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cfg = config_module.load_config(project)
    except Refused as exc:
        # The authenticated editor already has the exact text. Do not persist
        # this diagnostic or let it escape to a shared process log.
        return {"valid": False, "errors": [str(exc)[:6000]], "warnings": [], "scope": "botainer-config-check"}
    if previous is not None:
        prior_project = folder / "previous"
        prior_directory = prior_project / ".botainer"
        prior_directory.mkdir(parents=True, mode=0o700)
        prior_path = prior_directory / "config.yaml"
        prior_path.write_text(previous, encoding="utf-8")
        prior_path.chmod(0o600)
        try:
            reject_ambiguous_yaml(previous, yaml)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                old = config_module.load_config(prior_project)
        except (Refused, ValueError, yaml.YAMLError):
            errors.append("The saved config cannot establish its agent/history identity. Repair it with Botainer before dashboard editing.")
        else:
            changed = []
            if old.agent != cfg.agent:
                changed.append("agent")
            if old.profile != cfg.profile:
                changed.append("profile")
            old_mode = mode_for_agent(old.plugins_enabled, old.agent)
            new_mode = mode_for_agent(cfg.plugins_enabled, cfg.agent)
            if old_mode != new_mode:
                changed.append("authentication mode")
            if changed:
                errors.append("Changing " + ", ".join(changed) + " requires Botainer's explicit history/auth transition workflow; use Botainer for this change.")
    # Run the actual config-check command. Its project lookup is bound to this
    # private staged text, while policy and installed plugins remain those of
    # the selected MY_BOTAINER. This never composes a session or invokes hooks.
    captured = io.StringIO()
    original_lookup = _common.find_project_root
    _common.find_project_root = lambda *args, **kwargs: project
    success = True
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            try:
                check.main(args=[], standalone_mode=False)
            except SystemExit as exc:
                success = exc.code in (None, 0)
            except Exception:
                success = False
    finally:
        _common.find_project_root = original_lookup
    for line in captured.getvalue().splitlines()[:100]:
        if line.startswith("⚠"):
            warnings.append(line[1:].strip()[:1000])
        elif line.startswith("✗"):
            errors.append(line[1:].strip()[:1000])
    if not success and not errors:
        errors.append("Botainer's config check could not complete; the config was not accepted.")
    warnings.append("Validation checks Botainer's schema and config-check policy rules. Launch still requires full composition and runtime preflight.")
    return {"valid": not errors, "errors": errors[:16], "warnings": warnings[:16],
            "scope": "botainer-config-check", "summary": {"agent": cfg.agent,
            "profile": cfg.profile, "runtime": cfg.runtime, "network": cfg.network.mode},
            "model": cfg.model_dump(mode="json"), "raw": raw}


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("A private validator request is required.")
        filename = Path(sys.argv[1])
        if not filename.is_absolute() or filename.is_symlink():
            raise ValueError("A private validator request is required.")
        raw = filename.read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError("Validator request is too large.")
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {"sourceRoot", "text", "previousText"}:
            raise ValueError("Validator request has an unexpected shape.")
        result = validate(request, filename.parent)
    except Exception as exc:
        # Never expose a traceback or environment. Parse errors are useful in
        # the authenticated editor but bounded and not written to normal logs.
        result = {"valid": False, "errors": [str(exc)[:3000] or "Config validation failed."], "warnings": [], "scope": "botainer-config-check"}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
