import copy
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import tempfile
import unittest

from botainer_dashboard.cluster_profiles import (
    ClusterProfileError, MAX_PROFILE_BYTES, load_cluster_profile,
    parse_cluster_profile, parse_job_defaults, parse_site_preset,
)


def sample_profile():
    return {"version": 1, "id": "primary-trial", "label": "First cluster",
        "preset": {"version": 1, "id": "generic-slurm", "label": "Conservative CPU trial",
            "scheduler": "slurm", "runtime": "apptainer", "attach_route": "slurm-screen",
            "partitions": [{"name": "day", "max_cpus": 2, "max_memory_mib": 2048,
                            "max_time_minutes": 10, "max_gpus": 0}]},
        "connection": {"ssh_alias": "cluster-example", "remote_python": "/opt/botainer/bin/python3",
            "source_root": "/work/example/botainer", "state_root": "/home/example/.botainer",
            "image": "/images/existing.sif", "trial_root": "/home/example/dashboard-trials",
            "launcher": {"mode": "python_module", "path": "/opt/botainer/bin/python3"},
            "file_roots": {"home": "/home/example"}},
        "defaults": {"partition": "day", "cpus": 1, "memory_mib": 1024, "time_minutes": 5, "gpus": 0}}


class ClusterProfileTests(unittest.TestCase):
    def setUp(self):
        self.raw = sample_profile()

    def test_shareable_preset_excludes_private_connection_and_job_account(self):
        self.raw["defaults"]["account"] = "pi_example"
        profile = parse_cluster_profile(self.raw)
        exported = profile.preset.as_dict()
        self.assertEqual(exported, self.raw["preset"])
        text = json.dumps(exported)
        for private in ("cluster-example", "/home/example", "/images", "pi_example"):
            self.assertNotIn(private, text)
        self.assertEqual(parse_site_preset(exported), profile.preset)

    def test_connection_and_defaults_are_immutable_snapshots(self):
        profile = parse_cluster_profile(self.raw)
        original = profile.fingerprint
        self.raw["connection"]["file_roots"]["home"] = "/other"
        self.raw["preset"]["partitions"][0]["name"] = "another"
        with self.assertRaises(TypeError):
            profile.connection.file_roots["home"] = "/other"
        with self.assertRaises(FrozenInstanceError):
            profile.connection.ssh_alias = "another"
        self.assertEqual(profile.fingerprint, original)
        self.assertEqual(profile.connection.file_roots["home"], "/home/example")

    def test_configuration_changes_invalidate_fingerprint_but_key_order_does_not(self):
        profile = parse_cluster_profile(self.raw)
        reordered = json.loads(json.dumps(self.raw, sort_keys=True))
        self.assertEqual(parse_cluster_profile(reordered).fingerprint, profile.fingerprint)
        variants = []
        for scope, key, value in (("connection", "ssh_alias", "other-cluster"),
                                  ("connection", "image", "/images/other.sif"),
                                  ("defaults", "memory_mib", 1536),
                                  ("preset", "id", "next-cluster")):
            changed = copy.deepcopy(self.raw)
            changed[scope][key] = value
            variants.append(changed)
        for raw in variants:
            self.assertNotEqual(parse_cluster_profile(raw).fingerprint, profile.fingerprint)

    def test_legacy_view_contains_only_five_explicit_fields_and_is_detached(self):
        profile = parse_cluster_profile(self.raw)
        legacy = profile.trial_profile()
        self.assertEqual(set(legacy), {"ssh_alias", "remote_python", "trial_root", "source_root", "image"})
        self.assertEqual(legacy["image"], "/images/existing.sif")
        legacy["ssh_alias"] = "another"
        self.assertEqual(profile.connection.ssh_alias, "cluster-example")

    def test_project_job_draft_has_explicit_ceiling_and_does_not_change_site_defaults(self):
        profile = parse_cluster_profile(self.raw)
        draft = dict(self.raw["defaults"], cpus=2, memory_mib=2048, time_minutes=10, account="pi_example", qos="normal")
        parsed = parse_job_defaults(draft, profile.preset)
        self.assertEqual(parsed.cpus, 2)
        self.assertEqual(profile.defaults.cpus, 1)
        for key, value in (("partition", "unlisted"), ("cpus", 3), ("memory_mib", 2049),
                           ("time_minutes", 11), ("gpus", 1), ("cpus", True),
                           ("memory_mib", 1.0), ("time_minutes", 0), ("account", "--account=other"),
                           ("qos", "normal;id")):
            with self.subTest(key=key, value=value), self.assertRaises(ClusterProfileError):
                parse_job_defaults(dict(draft, **{key: value}), profile.preset)

    def test_unknown_executable_fields_and_routes_are_refused_everywhere(self):
        for location in ((), ("preset",), ("connection",), ("connection", "launcher"), ("defaults",)):
            raw = copy.deepcopy(self.raw)
            target = raw
            for key in location:
                target = target[key]
            target["shell"] = "module load anything; exec anything"
            with self.subTest(location=location), self.assertRaises(ClusterProfileError):
                parse_cluster_profile(raw)
        for key, value in (("scheduler", "pbs"), ("runtime", "docker"), ("attach_route", "fresh-agent")):
            raw = copy.deepcopy(self.raw)
            raw["preset"][key] = value
            with self.assertRaises(ClusterProfileError):
                parse_cluster_profile(raw)

    def test_unknown_versions_missing_fields_duplicate_partitions_and_unbounded_lists(self):
        for version in (0, 2, True, 1.0, "1"):
            with self.subTest(version=version), self.assertRaises(ClusterProfileError):
                parse_cluster_profile(dict(self.raw, version=version))
        for field in self.raw["connection"]:
            raw = copy.deepcopy(self.raw)
            del raw["connection"][field]
            with self.assertRaises(ClusterProfileError):
                parse_cluster_profile(raw)
        for count in (0, 2, 65):
            raw = copy.deepcopy(self.raw)
            raw["preset"]["partitions"] *= count
            with self.assertRaises(ClusterProfileError):
                parse_cluster_profile(raw)

    def test_literal_aliases_and_paths_refuse_options_expansion_traversal_and_controls(self):
        for alias in ("-F/tmp/ssh", "user@host", "cluster;id", "cluster name", "cluster\nname"):
            raw = copy.deepcopy(self.raw)
            raw["connection"]["ssh_alias"] = alias
            with self.assertRaises(ClusterProfileError):
                parse_cluster_profile(raw)
        for path in ("relative", "~/trial", "/", "//home/example", "/home/../trial", "/home/./trial",
                     "/home//trial", "/home/trial/", "/home/$USER", "/home/`id`", "/home/\ud800", "/home/a\x00"):
            raw = copy.deepcopy(self.raw)
            raw["connection"]["trial_root"] = path
            with self.subTest(path=repr(path)), self.assertRaises(ClusterProfileError):
                parse_cluster_profile(raw)

    def test_isolated_trial_cannot_overlap_selected_existing_installation_or_leave_roots(self):
        for field, path in (("trial_root", "/work/example/botainer"),
                            ("trial_root", "/home/example/.botainer/test"),
                            ("trial_root", "/home/example"),
                            ("trial_root", "/other/trial"),
                            ("source_root", "/home/example/dashboard-trials/source"),
                            ("image", "/home/example/dashboard-trials/image.sif"),
                            ("remote_python", "/home/example/dashboard-trials/bin/python")):
            raw = copy.deepcopy(self.raw)
            raw["connection"][field] = path
            with self.subTest(field=field, path=path), self.assertRaises(ClusterProfileError):
                parse_cluster_profile(raw)


class ClusterProfileFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.path = self.root / "profile.json"
        self.write(json.dumps(sample_profile()).encode())

    def write(self, content):
        self.path.write_bytes(content)
        self.path.chmod(0o600)

    def test_private_regular_json_load_is_pure(self):
        before = self.path.read_bytes()
        profile = load_cluster_profile(self.path)
        self.assertEqual(profile.id, "primary-trial")
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_nonprivate_file_parent_symlink_and_hardlink_are_refused(self):
        self.path.chmod(0o644)
        with self.assertRaises(ClusterProfileError):
            load_cluster_profile(self.path)
        self.path.chmod(0o600)
        self.root.chmod(0o755)
        with self.assertRaises(ClusterProfileError):
            load_cluster_profile(self.path)
        self.root.chmod(0o700)
        link = self.root / "link.json"
        link.symlink_to(self.path)
        with self.assertRaises(ClusterProfileError):
            load_cluster_profile(link)
        os.link(self.path, self.root / "hard.json")
        with self.assertRaises(ClusterProfileError):
            load_cluster_profile(self.path)

    def test_symlink_ancestor_and_fifo_never_follow_or_block(self):
        nested = self.root / "private"
        nested.mkdir(mode=0o700)
        link = self.root / "alias"
        link.symlink_to(nested, target_is_directory=True)
        target = nested / "profile.json"
        target.write_bytes(self.path.read_bytes())
        target.chmod(0o600)
        with self.assertRaises(ClusterProfileError):
            load_cluster_profile(link / "profile.json")
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ClusterProfileError):
            load_cluster_profile(fifo)

    def test_duplicate_deep_nonfinite_and_oversize_json_are_refused(self):
        for raw in (b'{"version":1,"version":1}', b'{"x":NaN}', b'"\xff"',
                    b'[' * 2000 + b']' * 2000, b' ' * (MAX_PROFILE_BYTES + 1)):
            self.write(raw)
            with self.subTest(prefix=raw[:20]), self.assertRaises(ClusterProfileError):
                load_cluster_profile(self.path)

    def test_missing_explicit_file_is_an_error(self):
        with self.assertRaises(ClusterProfileError):
            load_cluster_profile(self.root / "missing.json")


if __name__ == "__main__":
    unittest.main()
