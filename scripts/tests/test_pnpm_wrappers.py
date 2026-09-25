"""Offline wrapper regression tests, executed with the system /bin/bash."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid


REPO = Path(__file__).resolve().parents[2]
WRAPPERS = REPO / "scripts/package-manager"
MOCK_PM = """#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["MOCK_PM_LOG"], "a") as log:
    log.write(json.dumps({"pm": name, "cwd": os.getcwd(), "args": args}) + "\\n")

if name == "npm" and args[:1] == ["view"]:
    versions = json.loads(os.environ.get("MOCK_VERSIONS", "{}"))
    if len(args) != 4 or args[2:] != ["version", "--json"] or args[1] not in versions:
        sys.exit("Unexpected registry lookup (network is disabled)")
    print(json.dumps(versions[args[1]]))
elif name == "pnpm" and "add" in args:
    targeted = any(
        arg in ("--workspace-root", "-w", "--filter", "-F", "--dir", "-C",
                "--global", "-g", "--ignore-workspace-root-check")
        or arg.startswith(("--filter=", "--dir="))
        for arg in args
    )
    if Path("pnpm-workspace.yaml").exists() and not targeted:
        sys.exit("ERR_PNPM_ADDING_TO_ROOT")
"""


class PnpmWrapperTests(unittest.TestCase):
    def setUp(self):
        # Keep generated fixtures inside the checkout, never in the system temp dir.
        self.fixture = Path(f".pnpm-wrapper-tests-{uuid.uuid4().hex}")
        self.fixture.mkdir()
        self.addCleanup(shutil.rmtree, self.fixture)
        self.root = self.fixture / "workspace"
        self.member = self.root / "packages/a"
        self.other = self.root / "packages/b"
        self.bin = self.fixture / "bin"
        self.bin.mkdir()
        self.log = self.fixture / "calls.jsonl"
        for pm in ("pnpm", "npm", "bun"):
            executable = self.bin / pm
            executable.write_text(MOCK_PM)
            executable.chmod(0o755)
        self.write_package(self.root, name="workspace", packageManager="pnpm@10.0.0",
                           dependencies={"root-only": "^1.0.0"})
        self.write_package(self.member, name="app-a", dependencies={
            "member-only": "^1.0.0", "shared": "^1.0.0",
            "catalog-dep": "catalog:", "@scope/catalog-dep": "catalog:",
        }, devDependencies={"member-only": "^1.0.0"})
        self.write_package(self.other, name="app-b", dependencies={
            "other-only": "^1.0.0", "shared": "^1.0.0",
        })
        self.workspace = self.root / "pnpm-workspace.yaml"
        self.workspace.write_text(
            "packages:\n  - 'packages/*'\n"
            "catalog:\n  catalog-dep: '^1.0.0'\n"
            "  '@scope/catalog-dep': '1.0.0'\n"
            "overrides:\n  unrelated: '1.0.0'\n"
        )
        self.original_workspace = self.workspace.read_text()
        home = self.fixture / "home"
        home.mkdir()
        self.env = dict(os.environ)
        for key in ("BASH_ENV", "ENV"):
            self.env.pop(key, None)
        self.env.update(
            PATH=str(self.bin.resolve()) + os.pathsep + os.environ["PATH"],
            HOME=str(home.resolve()),
            MOCK_PM_LOG=str(self.log.resolve()),
            MOCK_VERSIONS="{}",
            COREPACK_ENABLE_NETWORK="0",
        )

    def write_package(self, directory, **manifest):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "package.json").write_text(json.dumps(manifest))

    def invoke(self, wrapper, *args, cwd=None, versions=None):
        self.log.unlink(missing_ok=True)
        env = dict(self.env)
        env["MOCK_VERSIONS"] = json.dumps(versions or {})
        return subprocess.run(
            ["/bin/bash", str(WRAPPERS / f"{wrapper}.sh"), *args],
            cwd=cwd or self.root, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def assert_dispatch(self, result, args, cwd=None, pm="pnpm"):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), [{
            "pm": pm, "cwd": str((cwd or self.root).resolve()), "args": args,
        }])

    def assert_rejected(self, result, message):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_explicit_root_from_member_overrides_inference_and_unknown_options(self):
        nested = self.member / "src"
        nested.mkdir()
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            with self.subTest(wrapper=wrapper):
                result = self.invoke(wrapper, "shared", "--root", "--reporter=silent",
                                     cwd=nested)
                prefix = ["--workspace-root"] if wrapper == "install" else []
                self.assert_dispatch(result, prefix + [operation, "shared", "--reporter=silent"])
        self.assertEqual(self.workspace.read_text(), self.original_workspace)

    def test_default_member_target_is_preserved(self):
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            with self.subTest(wrapper=wrapper):
                result = self.invoke(wrapper, "shared", "--reporter=silent", cwd=self.member)
                self.assert_dispatch(result, [
                    "--dir", str(self.member.resolve()), operation, "shared", "--reporter=silent",
                ])

    def test_member_package_manager_markers_do_not_hide_workspace_root(self):
        for marker in ("packageManager", "lockfile"):
            self.write_package(self.member, name="app-a", dependencies={"shared": "^1.0.0"},
                               **({"packageManager": "pnpm@10.0.0"} if marker == "packageManager" else {}))
            if marker == "lockfile":
                (self.member / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
            for wrapper, operation in (("install", "add"), ("remove", "remove")):
                with self.subTest(marker=marker, wrapper=wrapper):
                    prefix = ["--workspace-root"] if wrapper == "install" else []
                    self.assert_dispatch(self.invoke(wrapper, "--root", "shared", cwd=self.member),
                                         prefix + [operation, "shared"])
                    self.assert_dispatch(self.invoke(wrapper, "shared", cwd=self.member),
                                         ["--dir", str(self.member.resolve()), operation, "shared"])

    def test_explicit_targets_keep_original_directory_and_bypass_inference(self):
        targets = (
            ["--filter", "app-b"], ["--filter=app-b"], ["-F", "app-b"],
            ["--dir", "../b"], ["--dir=../b"], ["-C", "../b"],
            ["--workspace-root"], ["-w"], ["--global"], ["-g"],
        )
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            for target in targets:
                with self.subTest(wrapper=wrapper, target=target):
                    args = ["shared", *target, "--reporter=silent"]
                    result = self.invoke(wrapper, *args, cwd=self.member)
                    self.assert_dispatch(result, [operation, *args], cwd=self.member)

    def test_explicit_target_at_root_bypasses_unknown_options(self):
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            with self.subTest(wrapper=wrapper):
                args = ["shared", "--filter", "app-b", "--reporter=silent"]
                self.assert_dispatch(self.invoke(wrapper, *args), [operation, *args])

    def test_root_infers_member_and_deduplicates_dependency_sections(self):
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            with self.subTest(wrapper=wrapper):
                self.assert_dispatch(self.invoke(wrapper, "member-only"), [
                    "--filter", "app-a", operation, "member-only",
                ])

    def test_root_infers_unnamed_member_directory(self):
        self.write_package(self.other, dependencies={"unnamed-only": "^1.0.0"})
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            with self.subTest(wrapper=wrapper):
                self.assert_dispatch(self.invoke(wrapper, "unnamed-only"), [
                    "--dir", str(self.other.resolve()), operation, "unnamed-only",
                ])

    def test_root_infers_root_dependency_with_add_opt_in(self):
        self.assert_dispatch(self.invoke("install", "root-only@2"), [
            "--workspace-root", "add", "root-only@2",
        ])
        self.assert_dispatch(self.invoke("remove", "root-only"), ["remove", "root-only"])

    def test_unknown_install_falls_back_to_root_but_remove_errors(self):
        self.assert_dispatch(self.invoke("install", "new-package"), [
            "--workspace-root", "add", "new-package",
        ])
        self.assert_rejected(self.invoke("remove", "new-package"),
                             "Could not infer where to remove")

    def test_ambiguous_workspace_declarations_error(self):
        for wrapper in ("install", "remove"):
            with self.subTest(wrapper=wrapper):
                self.assert_rejected(self.invoke(wrapper, "shared"),
                                     f"Found multiple {wrapper} targets")

    def test_different_package_targets_error_before_dispatch(self):
        for wrapper in ("install", "remove"):
            with self.subTest(wrapper=wrapper):
                self.assert_rejected(self.invoke(wrapper, "member-only", "other-only"),
                                     "Packages resolve to different targets")

    def test_unknown_options_only_error_when_inferring(self):
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            with self.subTest(wrapper=wrapper):
                self.assert_rejected(self.invoke(wrapper, "member-only", "--reporter=silent"),
                                     "Cannot safely infer a workspace target")
                args = ["member-only", "--root", "--reporter=silent"]
                prefix = ["--workspace-root"] if wrapper == "install" else []
                self.assert_dispatch(self.invoke(wrapper, *args),
                                     prefix + [operation, "member-only", "--reporter=silent"])

    def test_standalone_project_does_not_infer_or_require_root_opt_in(self):
        self.workspace.unlink()
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            for root_flag in ([], ["--root"]):
                with self.subTest(wrapper=wrapper, root_flag=root_flag):
                    self.assert_dispatch(self.invoke(wrapper, "root-only", *root_flag,
                                                     "--reporter=silent"),
                                         [operation, "root-only", "--reporter=silent"])

    def test_catalog_without_version_falls_back_without_rewriting(self):
        self.assert_dispatch(self.invoke("install", "catalog-dep"), [
            "--workspace-root", "add", "catalog-dep",
        ])
        self.assertEqual(self.workspace.read_text(), self.original_workspace)

    def test_explicit_catalog_versions_rewrite_then_install(self):
        result = self.invoke("install", "catalog-dep@^2", "@scope/catalog-dep@3.2.0",
                             versions={"catalog-dep@^2": ["2.1.0", "2.2.0"],
                                       "@scope/catalog-dep@3.2.0": "3.2.0"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([(call["pm"], call["args"]) for call in self.calls()], [
            ("npm", ["view", "catalog-dep@^2", "version", "--json"]),
            ("npm", ["view", "@scope/catalog-dep@3.2.0", "version", "--json"]),
            ("pnpm", ["install"]),
        ])
        self.assertTrue(all(call["cwd"] == str(self.root.resolve()) for call in self.calls()))
        self.assertEqual(self.workspace.read_text(), self.original_workspace.replace(
            "catalog-dep: '^1.0.0'", "catalog-dep: '2.2.0'"
        ).replace("'@scope/catalog-dep': '1.0.0'", "'@scope/catalog-dep': '3.2.0'"))

    def test_workspace_section_updates_preserve_header_whitespace(self):
        for section in ("catalog", "overrides"):
            for whitespace in ("", "  ", "\t", " \t"):
                for final_newline in ("", "\n"):
                    with self.subTest(section=section, whitespace=whitespace,
                                      final_newline=final_newline):
                        source = (
                            f"catalog:{whitespace}\n  '@scope/pkg': '1.0.0'\n"
                            f"overrides:{whitespace}\n  '@scope/pkg': '1.0.0'"
                            + final_newline
                        )
                        self.workspace.write_text(source)
                        helper = ("pnpm_apply_catalog_version" if section == "catalog"
                                  else "pnpm_apply_override_version")
                        result = subprocess.run(
                            ["/bin/bash", "-c",
                             'source "$1"; ROOT="$PWD"; "$2" @scope/pkg 2.0.0',
                             "test", str(WRAPPERS / "_update-lib.sh"), helper],
                            cwd=self.root, env=self.env, text=True,
                            capture_output=True, timeout=20,
                        )
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(self.workspace.read_text(), source.replace(
                            f"{section}:{whitespace}\n  '@scope/pkg': '1.0.0'",
                            f"{section}:{whitespace}\n  '@scope/pkg': '2.0.0'",
                        ))

    def test_explicit_root_bypasses_catalog_rewrite(self):
        self.assert_dispatch(self.invoke("install", "catalog-dep@2", "--root", cwd=self.member),
                             ["--workspace-root", "add", "catalog-dep@2"])
        self.assertEqual(self.workspace.read_text(), self.original_workspace)

    def test_catalog_remove_targets_declarations_not_catalog(self):
        self.assert_dispatch(self.invoke("remove", "catalog-dep"), [
            "--filter", "app-a", "remove", "catalog-dep",
        ])
        self.write_package(self.other, name="app-b", dependencies={"catalog-dep": "catalog:"})
        self.assert_rejected(self.invoke("remove", "catalog-dep"), "Found multiple remove targets")
        self.assertEqual(self.workspace.read_text(), self.original_workspace)

    def test_catalog_and_member_targets_error_without_writes(self):
        self.assert_rejected(self.invoke("install", "catalog-dep@2", "member-only"),
                             "Packages resolve to different targets")
        self.assertEqual(self.workspace.read_text(), self.original_workspace)

    def test_no_argument_install_targets_root_or_current_member(self):
        self.assert_dispatch(self.invoke("install"), ["install"])
        self.assert_dispatch(self.invoke("install", cwd=self.member),
                             ["--dir", str(self.member.resolve()), "install"])
        self.assert_dispatch(self.invoke("install", "--root", cwd=self.member), ["install"])
        self.assert_rejected(self.invoke("remove", "--root", cwd=self.member),
                             "pmr requires at least one package")

    def test_non_registry_specs_need_explicit_or_member_target(self):
        for wrapper, operation in (("install", "add"), ("remove", "remove")):
            with self.subTest(wrapper=wrapper):
                self.assert_rejected(self.invoke(wrapper, "file:../local"),
                                     "Cannot infer a workspace target")
                self.assert_dispatch(self.invoke(wrapper, "file:../local", cwd=self.member), [
                    "--dir", str(self.member.resolve()), operation, "file:../local",
                ])

    def test_missing_explicit_target_value_errors(self):
        for wrapper in ("install", "remove"):
            with self.subTest(wrapper=wrapper):
                self.assert_rejected(self.invoke(wrapper, "member-only", "--filter"),
                                     "Missing value for --filter")

    def test_npm_and_bun_dispatch_are_unchanged(self):
        for pm in ("npm", "bun"):
            self.write_package(self.root, name="other-pm", packageManager=f"{pm}@1.0.0")
            for wrapper in ("install", "remove"):
                with self.subTest(pm=pm, wrapper=wrapper):
                    operation = ("install" if pm == "npm" else "add") if wrapper == "install" else (
                        "uninstall" if pm == "npm" else "remove"
                    )
                    self.assert_dispatch(self.invoke(wrapper, "pkg", "--root", cwd=self.member),
                                         [operation, "pkg", "--root"], cwd=self.member, pm=pm)
            self.assert_dispatch(self.invoke("install"), ["install"], pm=pm)

    @unittest.skipUnless(sys.platform == "darwin", "Apple Bash compatibility check")
    def test_system_bash_is_macos_bash_3_2(self):
        version = subprocess.run(["/bin/bash", "--version"], text=True, capture_output=True,
                                 check=True)
        self.assertIn("version 3.2.", version.stdout)


if __name__ == "__main__":
    unittest.main()
