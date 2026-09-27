#!/usr/bin/env python3
"""Run the real Botainer CLI for the separately prepared local shell trial.

Only config input and a before/after launch receipt are bridged. Botainer owns
all user-facing warnings, prompts, consent, composition and cleanup. This is a
temporary private integration for a pinned source, not a public Botainer API.
"""
from pathlib import Path
import hashlib
import os
import stat
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from botainer_dashboard.native_cli_runtime import NativeSandboxRuntime, canonical_request
from botainer_dashboard.operations import OperationStore
from botainer_dashboard.proof_backend import ProofBackend, digest, private_json, read_json, validate_spec
from botainer_dashboard.sandbox_runtime import validate_shell_config


def load_exact_config(backend, expected_revision):
    import yaml
    from botainer.core import config
    path = backend.project / ".botainer/config.yaml"
    if path.resolve() != path or path.is_symlink():
        raise ValueError("Config must be a regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Config must be a regular, unlinked file")
        data = stream.read(65537)
    if len(data) > 65536 or hashlib.sha256(data).hexdigest() != expected_revision:
        raise ValueError("Config revision changed before the CLI opened")
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
    validate_shell_config(raw, backend.plan)
    return config.ProjectConfig.model_validate(raw)


def native_start(backend, request_id, revision, *, cli_main, config_module,
                 composition, adapter_class, cfg):
    """Instrument runtime dispatch; never substitute for native consent."""
    receipt_path = backend._receipt(request_id)
    common = {"request_id": request_id, "config_revision": revision,
              "plan_sha256": digest(backend.plan_path)}
    original_load = config_module.load_config
    original_launch = composition.launch
    entered = False
    launched = None
    exit_code = 1

    def exact_config(root):
        if Path(root).resolve() != backend.project:
            raise ValueError("Unexpected project config")
        return cfg.model_copy(deep=True)

    def guarded_launch(spec, *, detach=False):
        nonlocal entered, launched
        if entered:
            raise RuntimeError("A launch request cannot dispatch twice")
        entered = True
        if detach is not True:
            raise RuntimeError("Only the approved detached owner is supported")
        validate_spec(spec.model_dump(mode="json"), backend.plan, backend.project_uuid)
        argv = adapter_class().render_argv(spec, detach=True)
        if (argv[:4] != ["docker", "run", "--rm", "-dit"]
                or "--pull" not in argv or argv[argv.index("--pull") + 1] != "never"):
            raise RuntimeError("Unexpected Docker command")
        backend.verify_files(); backend.verify_daemon()
        private_json(receipt_path, {**common, "phase": "before-dispatch", "session_id": spec.session_id})
        handle = original_launch(spec, detach=True)
        launched = {"session_id": spec.session_id, "container_id": handle.id, "runtime": spec.runtime,
                    "project_root": spec.project_root,
                    "session_record": str(Path(spec.state_dir) / "sessions" / spec.session_id)}
        private_json(receipt_path, {**common, "phase": "runtime-returned", "result": launched})
        return handle

    config_module.load_config = exact_config
    composition.launch = guarded_launch
    try:
        # This is the same CLI a person runs. No --yes, --quiet, --json or
        # custom confirmation: stdin/stdout/stderr are a real cooked terminal.
        exit_code = cli_main(["start", "--runtime=docker", "--detach", "--no-auto-onboard"])
        if exit_code is None:
            exit_code = 0
        return exit_code
    except SystemExit as error:
        exit_code = error.code if type(error.code) is int else 1
        raise
    except KeyboardInterrupt:
        exit_code = 130
        raise
    finally:
        config_module.load_config = original_load
        composition.launch = original_launch
        if not entered:
            private_json(receipt_path, {**common, "phase": "not-dispatched", "exit_code": exit_code})
        elif launched is not None:
            private_json(receipt_path, {**common, "phase": "completed", "exit_code": exit_code, "result": launched})
        # No result after entering the guard remains ambiguous. Do not overwrite
        # before-dispatch with a reassuring failure or repeat the operation.


def main():
    if len(sys.argv) != 4:
        raise RuntimeError("Expected a pinned plan, request UUID and config revision")
    plan_path = Path(sys.argv[1])
    request_id = canonical_request(sys.argv[2])
    revision = sys.argv[3]
    if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise RuntimeError("Invalid config revision")
    if plan_path.resolve() != plan_path or plan_path.is_symlink():
        raise RuntimeError("Invalid private plan")
    plan = read_json(plan_path)
    if plan.get("native_cli") is not True:
        raise RuntimeError("A separate native CLI preparation is required")
    baseline = ProofBackend(Path(plan["baseline_plan"]))
    owner = NativeSandboxRuntime(Path(plan["workspace_root"]), baseline)
    backend = owner.backend({"id": plan["project_id"], "uuid": plan["project_uuid"], "path": plan["project"]})
    backend.verify_files(); backend.verify_daemon()
    if backend.plan_path != plan_path or Path.cwd() != backend.project:
        raise RuntimeError("Project target changed")
    with OperationStore(backend.database) as store:
        operation = store.get(request_id)
        if (operation.state != "dispatching" or operation.checkout != str(backend.project)
                or operation.context_id != backend.namespace or operation.config_revision != revision):
            raise RuntimeError("Launch intent does not match the private target")
    sys.path.insert(0, plan["source"]["private_copy"])
    from botainer.core import config, composition
    from botainer.adapters.docker import DockerAdapter
    from botainer.cli.main import main as cli_main
    try:
        cfg = load_exact_config(backend, revision)
    except Exception:
        private_json(backend._receipt(request_id), {"request_id": request_id,
                     "config_revision": revision, "plan_sha256": digest(plan_path),
                     "phase": "not-dispatched", "exit_code": 3})
        raise
    return native_start(backend, request_id, revision, cli_main=cli_main,
                        config_module=config, composition=composition,
                        adapter_class=DockerAdapter, cfg=cfg)


if __name__ == "__main__":
    raise SystemExit(main())
