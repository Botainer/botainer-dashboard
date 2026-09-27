#!/usr/bin/env python3
"""Fixed local shell adapter. Invoked only by the trusted dashboard backend."""
from pathlib import Path
import hashlib
import json
import os
import re
import runpy
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from botainer_dashboard.sandbox_runtime import SandboxRuntime, validate_shell_config
from botainer_dashboard.proof_backend import ProofBackend, validate_spec, read_json, bounded_run


def main():
    plan_path = Path(sys.argv[1])
    if plan_path.resolve() != plan_path or plan_path.is_symlink():
        raise RuntimeError("Invalid private plan")
    plan = read_json(plan_path)
    baseline = ProofBackend(Path(plan["baseline_plan"]))
    owner = SandboxRuntime(Path(plan["workspace_root"]), baseline)
    project = {"id": plan["project_id"], "uuid": plan["project_uuid"], "path": plan["project"]}
    backend = owner.backend(project)
    backend.verify_files()
    if backend.plan_path != plan_path or Path.cwd() != backend.project:
        raise RuntimeError("Project target changed")
    backend.verify_daemon()
    sys.path.insert(0, plan["source"]["private_copy"])
    mode = sys.argv[2]
    args = sys.argv[3:]
    if mode == "cli" and len(args) == 2 and args[0] == "reconcile-exit" and re.fullmatch(r"[0-9a-f]{16}", args[1]):
        entry = backend._record(args[1])
        record = backend._session_record(entry)
        if not entry.get("started_at") or backend.inspect_container(entry, missing_ok=True) is not None:
            raise RuntimeError("Original container absence is unconfirmed")
        if not record.get("ended_at"):
            from datetime import datetime, timezone
            from botainer.state import session_record
            session_record.update_runtime(Path(entry["record_dir"]), ended_at=datetime.now(timezone.utc).isoformat())
        return
    if mode == "cli" and len(args) == 2 and args[0] in ("attach", "stop") and re.fullmatch(r"[0-9a-f]{16}", args[1]):
        entry = backend._record(args[1])
        if not entry.get("started_at"):
            raise RuntimeError("Original runtime unconfirmed")
        backend.inspect_container(entry)
        sys.argv = ["botainer", *args]
        runpy.run_module("botainer.cli.main", run_name="__main__")
        return
    launch = mode == "cli" and len(args) == 2 and args[0] == "start" and re.fullmatch(r"[0-9a-f]{64}", args[1])
    if not launch and not (mode == "preflight" and not args):
        raise RuntimeError("Command outside local shell contract")
    # Read and validate once. The installed version lacks an in-memory config
    # input to compose_session; this process-local shim supplies that exact
    # authoritative model. No source file is patched or user file rewritten.
    try:
        import yaml
        from botainer.core import config, composition
        from botainer.inspect import capability_summary
        from botainer.adapters.docker import DockerAdapter
        path = backend.project / ".botainer/config.yaml"
        if path.resolve() != path or path.is_symlink():
            raise ValueError("Config must be a regular file")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        import stat
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Config must be a regular file")
            data = stream.read(65537)
        if len(data) > 65536 or (launch and hashlib.sha256(data).hexdigest() != args[1]):
            raise ValueError("Config revision changed")
        # Reject aliases, duplicate keys and tagged object construction before
        # applying Botainer's authoritative ProjectConfig schema.
        if any(isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(data)):
            raise ValueError("YAML aliases are unsupported")
        class UniqueLoader(yaml.SafeLoader):
            pass
        def mapping(loader, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise ValueError("Duplicate or non-string config key")
                result[key] = loader.construct_object(value_node, deep=deep)
            return result
        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        raw = yaml.load(data, Loader=UniqueLoader)
        validate_shell_config(raw, plan)
        cfg = config.ProjectConfig.model_validate(raw)
        original_load = config.load_config
        def exact_config(root):
            if Path(root).resolve() != backend.project:
                raise ValueError("Unexpected project config")
            return cfg.model_copy(deep=True)
        config.load_config = exact_config
        try:
            spec = composition.compose_session(backend.project, runtime_choice="docker", identity_accept=False)
        finally:
            config.load_config = original_load
        validate_spec(spec.model_dump(mode="json"), plan, backend.project_uuid)
        argv = DockerAdapter().render_argv(spec, detach=True)
        if argv[:4] != ["docker", "run", "--rm", "-dit"] or argv[argv.index("--pull") + 1] != "never":
            raise ValueError("Unexpected Docker command")
        backend.verify_files(); backend.verify_daemon()
    except Exception as error:
        print(f"Configuration refused before launch: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(3)
    if not launch:
        print(json.dumps({"spec": spec.model_dump(mode="json"), "argv": argv}))
        return
    # From this point any failure is ambiguous and cannot be retried. The exact
    # validated spec is dispatched; no second config read and no host hooks.
    print(capability_summary.render_json(spec), flush=True)
    handle = composition.launch(spec, detach=True)
    print(json.dumps({"session_id": spec.session_id, "container_id": handle.id,
        "runtime": spec.runtime, "nudge_supported": False, "project_root": spec.project_root,
        "session_record": str(Path(spec.state_dir) / "sessions" / spec.session_id)}), flush=True)


if __name__ == "__main__":
    main()
