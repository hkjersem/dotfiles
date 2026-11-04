"""Global cache cleaning with isolated paths and mocked native tools."""

import contextlib
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "clean-caches.py"
SPEC = importlib.util.spec_from_file_location("clean_caches_command", SCRIPT.parent / "lib/cache_cleaners.py")
CACHES = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CACHES
with patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
    SPEC.loader.exec_module(CACHES)


class CleanCachesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        environment = patch.dict(os.environ, {
            "FNM_DIR": str(self.home / "fnm"), "COREPACK_HOME": str(self.home / "corepack"),
            "BUN_INSTALL": str(self.home / ".bun"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.cache = self.home / "custom-uv-cache"
        self.cache.mkdir()
        (self.cache / "data").write_bytes(b"x" * 10_000)
        home = patch.object(Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        tools = patch.object(CACHES.shutil, "which", side_effect=lambda name: "/mock/" + name)
        tools.start()
        self.addCleanup(tools.stop)
        self.calls = []

    def native(self, command, **kwargs):
        self.calls.append((command, kwargs))
        value = str(self.cache) if command == ["uv", "cache", "dir"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=value, stderr="")

    def run_command(self, *args, answer="yes", native=None):
        stdout = io.StringIO()
        with patch.object(CACHES.subprocess, "run", side_effect=native or self.native), \
                patch.object(CACHES, "confirm_cleanup", return_value=answer.lower() in ("y", "yes")), contextlib.redirect_stdout(stdout):
            code = CACHES.main(list(args))
        return code, stdout.getvalue()

    def test_preview_resolves_configuration_and_never_cleans(self):
        code, text = self.run_command("--only", "uv", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertTrue(text.startswith("Dry run"))
        allocated = (self.cache / "data").stat().st_blocks * 512
        self.assertIn(f"{allocated / 1024:.1f} KiB", text)
        self.assertIn("~/custom-uv-cache", text)
        self.assertEqual([cmd for cmd, _ in self.calls], [["uv", "cache", "dir"]])
        self.assertTrue((self.cache / "data").exists())

    def test_missing_go_reports_default_cache_but_never_cleans_or_confirms(self):
        cache = self.home / "go/pkg/mod"
        cache.mkdir(parents=True)
        payload = cache / "module.zip"
        payload.write_bytes(b"x" * 10000)
        for mode in (("--dry-run",), ()):
            with self.subTest(mode=mode), \
                    patch.object(CACHES.shutil, "which", return_value=None), \
                    patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")), \
                    patch.object(CACHES.subprocess, "run", side_effect=AssertionError("tool ran")), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = CACHES.main(["--only", "go-modules", "--deep", *mode])
            self.assertEqual(code, 1)
            self.assertIn("~/go/pkg/mod", stdout.getvalue())
            expected = CACHES.human_size(payload.stat().st_blocks * 512)
            self.assertIn(expected, stdout.getvalue())
            self.assertIn("Unverified default", stdout.getvalue())
            self.assertIn("cleanup blocked", stdout.getvalue())
            self.assertEqual(payload.read_bytes(), b"x" * 10000)

    def test_missing_go_default_symlink_is_not_scanned(self):
        (self.home / "go/pkg").mkdir(parents=True)
        (self.home / "go/pkg/mod").symlink_to(self.cache, target_is_directory=True)
        with patch.object(CACHES.shutil, "which", return_value=None), \
                patch.object(CACHES, "scan_sizes", side_effect=AssertionError("symlink scanned")):
            code, text = self.run_command("--only", "go-modules", "--deep", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("Symlink default cache skipped", text)
        self.assertEqual(self.calls, [])

    def test_missing_go_and_absent_default_cache_is_a_noop(self):
        for mode in (("--dry-run",), ()):
            with self.subTest(mode=mode), \
                    patch.object(CACHES.shutil, "which", return_value=None), \
                    patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")), \
                    patch.object(CACHES, "scan_sizes", side_effect=AssertionError("missing cache scanned")):
                code, text = self.run_command("--only", "go-modules", "--deep", *mode)
            self.assertEqual(code, 0, text)
            self.assertIn("No cache directory (default location; Go unavailable)", text)
            self.assertNotIn("cleanup blocked", text)
            self.assertEqual(self.calls, [])

    def test_mise_go_is_pinned_for_query_recheck_and_cleanup_without_installing(self):
        executable = self.home / "mise/go/1.27.1/bin/go"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 1\n")
        executable.chmod(0o755)
        discovery = [{"version": "1.27.1", "installed": True,
                      "active": False, "install_path": str(executable.parent.parent)}]

        def native(command, **kwargs):
            self.calls.append((command, kwargs))
            if command == ["/mock/mise", "ls", "--installed", "go", "--json"]:
                import json
                value = json.dumps(discovery)
            elif command == [str(executable), "env", "GOCACHE"]:
                value = str(self.cache)
            elif command == [str(executable), "clean", "-cache"]:
                value = ""
            else:
                raise AssertionError(command)
            self.assertEqual(kwargs["env"]["GOTOOLCHAIN"], "local")
            self.assertEqual(kwargs["env"]["MISE_OFFLINE"], "1")
            return subprocess.CompletedProcess(command, 0, value, "")

        with patch.object(CACHES.shutil, "which",
                          side_effect=lambda name: None if name == "go" else
                          "/mock/mise" if name == "mise" else str(executable)):
            code, text = self.run_command("--only", "go-build", native=native)
        self.assertEqual(code, 0, text)
        self.assertEqual([cmd for cmd, _ in self.calls], [
            ["/mock/mise", "ls", "--installed", "go", "--json"],
            [str(executable), "env", "GOCACHE"],
            [str(executable), "env", "GOCACHE"],
            [str(executable), "clean", "-cache"]])

    def test_mise_discovery_errors_block_cleanup_instead_of_guessing(self):
        with patch.object(CACHES.shutil, "which",
                          side_effect=lambda name: "/mock/mise" if name == "mise" else None), \
                patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")):
            code, text = self.run_command("--only", "go-build", native=lambda cmd, **kw:
                                         subprocess.CompletedProcess(cmd, 0, "{}", ""))
        self.assertEqual(code, 1)
        self.assertIn("invalid installed Go list", text)

    def test_plan_keeps_cache_size_and_folder_on_one_line(self):
        code, text = self.run_command("--only", "uv,xcode", "--dry-run")
        self.assertEqual(code, 0, text)
        allocated = (self.cache / "data").stat().st_blocks * 512
        size = f"{allocated / 1024:.1f} KiB"
        self.assertIn(f"  {'uv':<12}  {size:>10}  ~/custom-uv-cache\n", text)
        self.assertIn(f"  {'xcode':<12}  {'—':>10}  No cache directory\n", text)
        self.assertNotIn("    ~/custom-uv-cache", text)
        self.assertNotIn("• xcode", text)

    def test_size_sort_orders_largest_first_and_missing_last(self):
        sizes = iter((0, 1024, 4096))
        with patch.object(CACHES, "cache_path", side_effect=[
                self.cache, self.home / "missing", self.cache, self.cache]), \
                patch.object(CACHES, "scan_sizes", side_effect=lambda *a, **kw: {
                    Path("."): next(sizes)}):
            code, text = self.run_command(
                "--only", "uv,xcode,pip,npm", "--sort", "size", "--dry-run")
        self.assertEqual(code, 0, text)
        names = [line.split()[0] for line in text.splitlines()
                 if line.split() and line.split()[0] in {"uv", "xcode", "pip", "npm"}]
        self.assertEqual(names, ["npm", "pip", "uv", "xcode"])

    def test_progress_is_cleared_before_the_plan_is_printed(self):
        events = []

        class Terminal(io.StringIO):
            def isatty(self):
                return True

            def write(self, text):
                events.append(text)
                return super().write(text)

        def scan(path, *args, **kwargs):
            kwargs["progress"].update(path / "nested", 1)
            return {Path("."): 1024}

        with patch.object(CACHES.subprocess, "run", side_effect=self.native), \
                patch.object(CACHES, "scan_sizes", side_effect=scan), \
                patch("files.time.monotonic", side_effect=itertools.count(0, 3)), \
                patch.object(CACHES.output, "progress", side_effect=lambda text: events.append("progress")), \
                patch.object(CACHES.output, "progress_done", side_effect=lambda: events.append("cleared")), \
                contextlib.redirect_stdout(Terminal()), contextlib.redirect_stderr(Terminal()):
            self.assertEqual(CACHES.main(["--only", "uv", "--dry-run"]), 0)
        self.assertIn("progress", events)
        self.assertLess(events.index("progress"), events.index("cleared"))
        plan = next(i for i, text in enumerate(events) if "Cache cleanup plan" in text)
        self.assertLess(events.index("cleared"), plan)

    def test_apply_requires_explicit_selection_and_deep_acknowledgement(self):
        for args in ((), ("--only", "gradle"), ("--only", "unknown"),
                     ("--only", ""), ("--only", "go-modules")):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(CACHES.subprocess, "run", side_effect=AssertionError("tool ran")):
                with self.assertRaises(SystemExit) as exc:
                    CACHES.main(list(args))
                self.assertEqual(exc.exception.code, 2)

    def test_apply_runs_native_cleaner_globally_after_confirmation(self):
        code, text = self.run_command("--only", "uv")
        self.assertEqual(code, 0, text)
        self.assertEqual([cmd for cmd, _ in self.calls],
                         [["uv", "cache", "dir"], ["uv", "cache", "dir"], ["uv", "cache", "clean"]])
        self.assertTrue(all(kwargs["cwd"] == self.home for _, kwargs in self.calls))
        self.assertIn("Observed available-space change", text)

    def test_declining_or_failed_queries_never_clean(self):
        for answer in ("", "no"):
            code, _ = self.run_command("--only", "uv", answer=answer)
            self.assertEqual(code, 0)
        code, text = self.run_command("--only", "uv", native=lambda cmd, **kw:
                                      subprocess.CompletedProcess(cmd, 3, "", "query failed"))
        self.assertEqual(code, 1)
        self.assertIn("Nothing cleaned", text)
        self.assertFalse(any(cmd == ["uv", "cache", "clean"] for cmd, _ in self.calls))

    def test_shared_single_key_confirmation_and_closed_input(self):
        for status in (0, 1):
            with patch.object(CACHES.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], status)) as run:
                self.assertEqual(CACHES.confirm_cleanup(), status == 0)
                command = run.call_args.args[0]
                self.assertEqual(command[:2], ["/bin/bash", "-c"])
                self.assertIn('confirm_apply "Clean these global caches?" false false', command[2])
        for answer, expected in (("y", True), ("Y", True), ("n", False), ("\n", False),
                                 ("\x1b", False), ("", False)):
            script = (
                'source "$1" || exit; confirm_apply "Clean these global caches?" false false'
            )
            result = subprocess.run(
                ["/bin/bash", "-c", script, "test",
                 str(SCRIPT.parent / "package-manager/_update-lib.sh")],
                input=answer, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode == 0, expected, result.stdout)
            self.assertIn("[y/N]", result.stdout)

    def test_dry_run_never_requests_confirmation(self):
        with patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")), \
                patch.object(CACHES.subprocess, "run", side_effect=self.native), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CACHES.main(["--only", "uv", "--dry-run"]), 0)
        self.assertEqual([cmd for cmd, _ in self.calls], [["uv", "cache", "dir"]])

    def test_failed_cleaner_is_not_reported_as_success(self):
        def native(command, **kwargs):
            if command[-1] == "clean":
                return subprocess.CompletedProcess(command, 7)
            return self.native(command, **kwargs)
        code, text = self.run_command("--only", "uv", native=native)
        self.assertEqual(code, 1)
        self.assertIn("status 7", text)
        self.assertNotIn("Cleaned", text)

    def test_changed_location_after_confirmation_is_not_cleaned(self):
        count = 0
        def native(command, **kwargs):
            nonlocal count
            count += 1
            return subprocess.CompletedProcess(command, 0,
                                               str(self.cache if count == 1 else self.home / "other"), "")
        code, text = self.run_command("--only", "uv", native=native)
        self.assertEqual(code, 1)
        self.assertIn("changed after preview", text)
        self.assertEqual(count, 2)

    def test_direct_deep_cleanup_only_removes_previewed_cache(self):
        gradle = self.home / ".gradle/caches"
        gradle.mkdir(parents=True)
        (gradle / "data").write_text("rebuild")
        sentinel = self.home / ".gradle/gradle.properties"
        sentinel.write_text("keep")
        with patch.dict(os.environ, {"GRADLE_USER_HOME": str(gradle.parent)}):
            code, text = self.run_command("--only", "gradle", "--deep")
        self.assertEqual(code, 0, text)
        self.assertFalse(gradle.exists())
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(self.calls, [])

    def test_playwright_folder_with_other_entries_is_never_removed(self):
        browsers = self.home / "Library/Caches"
        for entry in ("chromium-1200", "firefox-beta-1490", ".links"):
            (browsers / entry).mkdir(parents=True)
        (browsers / "com.apple.Safari").mkdir()
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(browsers)}):
            code, text = self.run_command("--only", "playwright", "--deep")
            self.assertEqual(code, 1, text)
            self.assertIn("not only Playwright browsers", text)
            self.assertTrue((browsers / "com.apple.Safari").exists())
            (browsers / "com.apple.Safari").rmdir()
            code, text = self.run_command("--only", "playwright", "--deep")
        self.assertEqual(code, 0, text)
        self.assertFalse(browsers.exists())

    def test_playwright_contents_are_rechecked_after_confirmation(self):
        browsers = self.home / "Library/Caches/ms-playwright"
        browser = browsers / "chromium-1200"
        browser.mkdir(parents=True)
        sentinel = browsers / "unrelated.txt"

        def confirm():
            sentinel.write_text("keep")
            return True

        with patch.object(CACHES, "confirm_cleanup", side_effect=confirm), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = CACHES.main(["--only", "playwright", "--deep"])
        self.assertEqual(code, 1, stdout.getvalue())
        self.assertIn("not only Playwright browsers", stdout.getvalue())
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertTrue(browser.is_dir())

    def test_broad_symlink_and_relative_paths_are_refused(self):
        alias = self.home / "alias"
        alias.symlink_to(self.cache)
        for value in (str(self.home), "/", ".", str(alias)):
            with self.subTest(value=value):
                code, text = self.run_command("--only", "uv", native=lambda cmd, **kw:
                                              subprocess.CompletedProcess(cmd, 0, value, ""))
                self.assertEqual(code, 1)
                self.assertIn("Nothing cleaned", text)
        self.assertTrue((self.cache / "data").exists())

    def test_global_cache_is_allowed_when_shell_is_inside_it(self):
        for cwd in (self.cache, self.cache / "nested"):
            with self.subTest(cwd=cwd), patch.object(Path, "cwd", return_value=cwd):
                code, text = self.run_command("--only", "uv", "--dry-run")
            self.assertEqual(code, 0, text)
            self.assertIn("~/custom-uv-cache", text)
            self.assertNotIn("refusing broad", text)
        self.assertTrue((self.cache / "data").exists())

    def test_unreadable_sizes_block_every_cleaner_before_confirmation(self):
        def partial(path, *args, **kwargs):
            kwargs["warnings"][path / "private"] = PermissionError("denied")
            return {Path("."): 0}
        with patch.object(CACHES, "scan_sizes", side_effect=partial):
            code, text = self.run_command("--only", "uv,pip")
        self.assertEqual(code, 1)
        self.assertIn("cache size incomplete", text)
        self.assertIn("Nothing cleaned", text)
        self.assertFalse(any(cmd[-1] in ("clean", "purge") for cmd, _ in self.calls))

    def test_yarn_uses_version_specific_global_location_query(self):
        for version in ("1.22.22", "4.10.0"):
            calls = []
            def native(command, **kwargs):
                calls.append(command)
                value = version if command == ["/mock/yarn", "--version"] else str(self.cache)
                return subprocess.CompletedProcess(command, 0, value, "")
            code, text = self.run_command("--only", "yarn", "--dry-run", native=native)
            self.assertEqual(code, 0, text)
            self.assertIn("~/custom-uv-cache", text)
            expected = (["/mock/yarn", "--silent", "cache", "dir"] if version.startswith("1.")
                        else ["/mock/yarn", "config", "get", "cacheFolder"])
            self.assertEqual(calls, [["/mock/yarn", "--version"], expected])

    def pnpm_fixture(self):
        self.store_root = self.home / "pnpm/store"
        self.pnpm_cache = self.home / "pnpm/cache"
        self.pnpm_cache.mkdir(parents=True)
        (self.pnpm_cache / "metadata").write_bytes(b"metadata" * 1000)
        self.stores = {}
        for generation in ("v10", "v11"):
            path = self.store_root / generation
            path.mkdir(parents=True)
            (path / "data").write_bytes(generation.encode() * 1000)
            self.stores[generation] = path
        self.corepack = self.home / "corepack"
        environment = patch.dict(os.environ, {"COREPACK_HOME": str(self.corepack)})
        environment.start()
        self.addCleanup(environment.stop)
        self.pnpm_entry = self.corepack / "v1/pnpm/10.34.5/bin/pnpm.cjs"
        self.pnpm_entry.parent.mkdir(parents=True)
        self.pnpm_entry.write_text("fixture, never executed")
        (self.pnpm_entry.parent.parent / "package.json").write_text(
            json.dumps({"name": "pnpm", "version": "10.34.5"}))

    def pnpm_native(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == "/mock/pnpm":
            version, generation, args = getattr(self, "primary_pnpm", "12.16.1"), "v11", command[1:]
        elif command[:2] == ["/mock/node", str(self.pnpm_entry)]:
            version, generation, args = self.pnpm_entry.parent.parent.name, "v10", command[2:]
        else:
            raise AssertionError(command)
        if args[:2] == ["--store-dir", str(self.store_root)]:
            args = args[2:]
        if args == ["--version"]:
            value = version
        elif args == ["store", "path"]:
            value = str(self.stores[generation])
        elif args in (["cache", "path"], ["config", "get", "cache-dir"]):
            value = str(self.pnpm_cache)
        elif args == ["store", "prune"]:
            (self.stores[generation] / "data").unlink()
            value = ""
        else:
            raise AssertionError(command)
        self.assertEqual(kwargs["cwd"], self.home)
        self.assertEqual(kwargs["env"]["COREPACK_ENABLE_NETWORK"], "0")
        self.assertEqual(kwargs["env"]["npm_config_manage_package_manager_versions"], "false")
        return subprocess.CompletedProcess(command, 0, value, "")

    def test_pnpm_previews_all_generations_and_counts_shared_cache_once(self):
        self.pnpm_fixture()
        code, text = self.run_command("--only", "pnpm", "--dry-run", native=self.pnpm_native)
        self.assertEqual(code, 0, text)
        self.assertIn("~/pnpm/store/v10", text)
        self.assertIn("~/pnpm/store/v11", text)
        self.assertEqual(text.count("~/pnpm/cache"), 1)
        self.assertTrue(all((path / "data").exists() for path in self.stores.values()))
        self.assertFalse(any("prune" in cmd for cmd, _ in self.calls))
        self.assertIn("expired dlx/pnx", text)

    def test_pnpm_applies_each_generation_with_its_compatible_pinned_tool(self):
        self.pnpm_fixture()
        sentinel = self.home / "pnpm/installed-tool"
        sentinel.write_text("keep")
        code, text = self.run_command("--only", "pnpm", native=self.pnpm_native)
        self.assertEqual(code, 0, text)
        self.assertFalse(any((path / "data").exists() for path in self.stores.values()))
        self.assertEqual(sentinel.read_text(), "keep")
        cleaned = [cmd for cmd, _ in self.calls if "prune" in cmd]
        self.assertEqual(cleaned, [
            ["/mock/pnpm", "store", "prune"],
            ["/mock/node", str(self.pnpm_entry),
             "--store-dir", str(self.store_root), "store", "prune"]])

    def test_pnpm11_and12_share_store_while_pnpm10_versions_cover_the_older_generation(self):
        self.pnpm_fixture()
        for older in ("10.29.3", "10.34.5"):
            package = self.pnpm_entry.parent.parent
            renamed = package.with_name(older)
            package.rename(renamed)
            self.pnpm_entry = renamed / "bin/pnpm.cjs"
            (renamed / "package.json").write_text(json.dumps({"name": "pnpm", "version": older}))
            for current in ("11.28.6", "12.16.1"):
                with self.subTest(current=current, older=older):
                    self.primary_pnpm = current
                    self.calls.clear()
                    code, text = self.run_command("--only", "pnpm", "--dry-run", native=self.pnpm_native)
                    self.assertEqual(code, 0, text)
                    self.assertEqual(text.count("~/pnpm/store/v11"), 1)
                    self.assertEqual(text.count("~/pnpm/store/v10"), 1)
                    self.assertIn(
                        ["/mock/node", str(self.pnpm_entry),
                         "--store-dir", str(self.store_root), "store", "path"],
                        [cmd for cmd, _ in self.calls])

    def test_missing_compatible_pnpm_blocks_all_cleanup(self):
        self.pnpm_fixture()
        with patch.object(CACHES, "installed_pnpm", return_value=[]), \
                patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")):
            code, text = self.run_command("--only", "pnpm", native=self.pnpm_native)
        self.assertEqual(code, 1)
        self.assertIn("no installed compatible pnpm for stores: v10", text)
        self.assertTrue(all((path / "data").exists() for path in self.stores.values()))
        self.assertFalse(any("prune" in cmd for cmd, _ in self.calls))

    def test_pnpm_keeps_both_generations_when_confirmation_declined(self):
        self.pnpm_fixture()
        code, text = self.run_command("--only", "pnpm", answer="no", native=self.pnpm_native)
        self.assertEqual(code, 0, text)
        self.assertTrue(all((path / "data").exists() for path in self.stores.values()))
        self.assertFalse(any("prune" in cmd for cmd, _ in self.calls))

    def test_pnpm_cache_and_version_are_rechecked_before_pruning(self):
        for changed in ("cache", "version"):
            with self.subTest(changed=changed):
                if not hasattr(self, "stores"):
                    self.pnpm_fixture()
                confirmed = False

                def confirm():
                    nonlocal confirmed
                    confirmed = True
                    return True

                def native(command, **kwargs):
                    if confirmed and command == ["/mock/pnpm", "cache", "path"] and changed == "cache":
                        return subprocess.CompletedProcess(command, 0, str(self.home / "other"), "")
                    if confirmed and command == ["/mock/pnpm", "--version"] and changed == "version":
                        return subprocess.CompletedProcess(command, 0, "12.17.0", "")
                    return self.pnpm_native(command, **kwargs)

                with patch.object(CACHES, "confirm_cleanup", side_effect=confirm), \
                        patch.object(CACHES.subprocess, "run", side_effect=native), \
                        contextlib.redirect_stdout(io.StringIO()) as stdout:
                    code = CACHES.main(["--only", "pnpm"])
                text = stdout.getvalue()
                self.assertEqual(code, 1)
                self.assertIn("changed after preview", text)
                self.assertTrue((self.stores["v11"] / "data").exists())
                # Other validated cleaners are still attempted after a failure.
                (self.stores["v10"] / "data").write_bytes(b"fixture")

    def test_pnpm_rejects_symlink_stores_and_overlapping_cache_paths(self):
        self.pnpm_fixture()
        for mode in ("symlink", "overlap"):
            with self.subTest(mode=mode):
                link = self.store_root / "v9"
                if mode == "symlink":
                    link.symlink_to(self.cache, target_is_directory=True)

                def native(command, **kwargs):
                    if mode == "overlap" and command == ["/mock/pnpm", "cache", "path"]:
                        return subprocess.CompletedProcess(command, 0, str(self.store_root), "")
                    return self.pnpm_native(command, **kwargs)

                with patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")):
                    code, text = self.run_command("--only", "pnpm", native=native)
                self.assertEqual(code, 1, text)
                self.assertTrue(all((path / "data").exists() for path in self.stores.values()))
                if mode == "symlink":
                    self.assertIn("symlink cache path", text)
                    link.unlink()
                else:
                    self.assertIn("paths overlap", text)

    def test_installed_pnpm_uses_latest_per_major_without_running_download_wrappers(self):
        self.pnpm_fixture()
        older = self.corepack / "v1/pnpm/10.29.3"
        older.mkdir()
        (older / "package.json").write_text(json.dumps({"name": "pnpm", "version": "10.29.3"}))
        wrapper = self.corepack / "v1/pnpm/12.16.1"
        wrapper.mkdir()
        (wrapper / "package.json").write_text(json.dumps({"name": "pnpm", "version": "12.16.1"}))
        with patch.object(CACHES.subprocess, "run", side_effect=AssertionError("tool ran")):
            self.assertEqual(CACHES.installed_pnpm(), [("/mock/node", (str(self.pnpm_entry),))])
        (older / "package.json").write_text("[]")
        with self.assertRaisesRegex(OSError, "manifest mismatch"):
            CACHES.installed_pnpm()

    def test_pnpm10_default_cache_is_explicitly_included(self):
        self.pnpm_fixture()
        cleaner = CACHES.replace(CACHES.BY_NAME["pnpm"], version="10.34.5")
        with patch.object(CACHES, "query", side_effect=["10.34.5", str(self.stores["v10"]), "undefined"]), \
                patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.home / "xdg")}):
            paths = CACHES.cleaner_paths(cleaner)
        self.assertEqual(paths, [("pnpm", self.stores["v10"]),
                                 ("pnpm-cache", self.home / "xdg/pnpm")])

    def bun_fixture(self):
        executable = self.home / "fnm/node-versions/v24.0.0/installation/bin/bun"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 1\n")
        executable.chmod(0o755)
        return executable

    def test_bun_discovered_in_fnm_is_pinned_and_shared_cache_cleaned_once(self):
        executable = self.bun_fixture()
        standalone = self.home / ".bun/bin/bun"
        standalone.parent.mkdir(parents=True)
        standalone.symlink_to(executable)

        def native(command, **kwargs):
            self.calls.append((command, kwargs))
            self.assertEqual(command[0], str(executable))
            if command[1:] == ["pm", "cache"]:
                return subprocess.CompletedProcess(command, 0, str(self.cache), "")
            self.assertEqual(command[1:], ["pm", "cache", "rm"])
            (self.cache / "data").unlink()
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.dict(os.environ, {"FNM_DIR": str(self.home / "fnm"),
                                    "BUN_INSTALL": str(self.home / ".bun")}), \
                patch.object(CACHES, "mise_executable", return_value=None):
            code, text = self.run_command("--only", "bun", native=native)
        self.assertEqual(code, 0, text)
        self.assertFalse((self.cache / "data").exists())
        self.assertEqual([cmd for cmd, _ in self.calls], [
            [str(executable), "pm", "cache"], [str(executable), "pm", "cache"],
            [str(executable), "pm", "cache", "rm"]])

    def test_distinct_bun_executables_with_same_cache_only_clean_once(self):
        executable = self.bun_fixture()
        other = self.home / ".bun/bin/bun"
        other.parent.mkdir(parents=True)
        other.write_text("#!/bin/sh\nexit 1\n")
        other.chmod(0o755)

        def native(command, **kwargs):
            self.calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, str(self.cache), "")

        with patch.dict(os.environ, {"FNM_DIR": str(self.home / "fnm"),
                                    "BUN_INSTALL": str(self.home / ".bun")}), \
                patch.object(CACHES, "mise_executable", return_value=None):
            code, text = self.run_command("--only", "bun", native=native)
        self.assertEqual(code, 0, text)
        self.assertEqual(text.count("~/custom-uv-cache"), 1)
        self.assertEqual(sum(cmd[-1] == "rm" for cmd, _ in self.calls), 1)
        self.assertIn([str(executable), "pm", "cache"], [cmd for cmd, _ in self.calls])

    def test_bun_mise_discovery_never_installs_or_runs_cleanup_in_preview(self):
        executable = self.bun_fixture()
        record = [{"installed": True, "active": True, "version": "1.3.0",
                   "install_path": str(executable.parent.parent)}]

        def native(command, **kwargs):
            self.calls.append((command, kwargs))
            if command == ["/mock/mise", "ls", "--installed", "bun", "--json"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(record), "")
            self.assertEqual(command, [str(executable), "pm", "cache"])
            return subprocess.CompletedProcess(command, 0, str(self.cache), "")

        with patch.dict(os.environ, {"FNM_DIR": str(self.home / "empty"),
                                    "BUN_INSTALL": str(self.home / "absent")}):
            code, text = self.run_command("--only", "bun", "--dry-run", native=native)
        self.assertEqual(code, 0, text)
        self.assertTrue((self.cache / "data").exists())
        self.assertEqual(len(self.calls), 2)

    def test_missing_tools_without_caches_are_quiet_but_explicit_bun_errors(self):
        with patch.object(CACHES.shutil, "which", return_value=None), \
                patch.object(CACHES, "bun_cleaners", return_value=[]), \
                patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")):
            code, text = self.run_command("--dry-run")
            self.assertEqual(code, 0, text)
            self.assertNotIn("Skipped cleaners", text)
            self.assertNotIn("npm unavailable", text)
            self.assertNotIn("Bun unavailable", text)
            code, text = self.run_command("--only", "bun")
            self.assertEqual(code, 1)
            self.assertIn("Bun unavailable on PATH, in BUN_INSTALL, fnm or mise", text)
            self.assertIn("Nothing cleaned", text)

    def test_missing_bun_size_is_report_only_and_never_prompts_or_deletes(self):
        cache = self.home / ".bun/install/cache"
        cache.mkdir(parents=True)
        payload = cache / "download"
        payload.write_bytes(b"keep" * 10000)
        with patch.object(CACHES.shutil, "which", return_value=None), \
                patch.object(CACHES, "bun_cleaners", return_value=[]), \
                patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")), \
                patch.object(CACHES.subprocess, "run", side_effect=AssertionError("tool ran")):
            code, text = self.run_command("--dry-run", "--sort", "size")
            self.assertEqual(code, 0, text)
            self.assertIn("Report-only storage", text)
            self.assertIn("~/.bun/install/cache", text)
            self.assertIn(CACHES.human_size(payload.stat().st_blocks * 512), text)
            self.assertIn("\n    Tool unavailable. Preview orphan cleanup: "
                          "clean_caches --only bun --orphaned --dry-run\n", text)
            self.assertNotIn("Bun unavailable on PATH", text)
            self.assertIn("Orphan previews verify recognised artifacts separately; unknown entries stay.", text)
            self.assertIn("─ Notes ", text)
            # Explicit selection is still blocked, even though its size is visible.
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = CACHES.main(["--only", "bun"])
            self.assertEqual(code, 1)
            self.assertIn("~/.bun/install/cache", stdout.getvalue())
        self.assertEqual(payload.read_bytes(), b"keep" * 10000)

    def test_report_only_tools_are_not_cleaned_with_selected_available_cleaners(self):
        bun = self.home / ".bun/install/cache"
        bun.mkdir(parents=True)
        payload = bun / "download"
        payload.write_bytes(b"keep")
        # A selected live cleaner must not pick up unrelated known folders.
        code, text = self.run_command("--only", "uv")
        self.assertEqual(code, 0, text)
        self.assertNotIn("~/.bun/install/cache", text)
        self.assertEqual(payload.read_bytes(), b"keep")
        self.assertEqual([cmd for cmd, _ in self.calls],
                         [["uv", "cache", "dir"], ["uv", "cache", "dir"], ["uv", "cache", "clean"]])

    def test_report_only_inventory_is_sorted_and_rejects_symlink_locations(self):
        corepack = self.home / "corepack/v1/pnpm"
        corepack.mkdir(parents=True)
        tool = corepack / "tool"
        tool.write_bytes(b"keep tools")
        bun = self.home / ".bun/install/cache"
        bun.parent.mkdir(parents=True)
        bun.symlink_to(self.cache, target_is_directory=True)
        with patch.object(CACHES.shutil, "which", return_value=None), \
                patch.object(CACHES, "bun_cleaners", return_value=[]):
            code, text = self.run_command("--dry-run", "--sort", "size")
        self.assertEqual(code, 1)
        self.assertIn("refusing symlink cache path", text)
        self.assertIn("~/corepack/v1", text)
        self.assertEqual(tool.read_bytes(), b"keep tools")
        self.assertTrue((self.cache / "data").exists())

    def npm_installation(self, version):
        installation = self.home / "fnm/node-versions" / version / "installation"
        binary = installation / "bin"
        binary.mkdir(parents=True)
        for name in ("node", "npm"):
            (binary / name).write_text("fixture, never executed")
        return installation

    def test_npm_uses_matching_node_and_cleans_each_distinct_cache_once(self):
        installations = [self.npm_installation(version) for version in ("v22.1.0", "v24.1.0", "v26.1.0")]
        second = self.home / "other-npm-cache"
        second.mkdir()
        (second / "data").write_text("download")
        targets = {str(inst / "bin/node"): self.cache if index < 2 else second
                   for index, inst in enumerate(installations)}

        def native(command, **kwargs):
            self.calls.append((command, kwargs))
            target = targets[command[0]]
            self.assertEqual(command[1], str(Path(command[0]).with_name("npm")))
            self.assertEqual(kwargs["env"]["PATH"].split(os.pathsep)[0], str(Path(command[0]).parent))
            if command[2:] == ["config", "get", "cache"]:
                return subprocess.CompletedProcess(command, 0, str(target), "")
            self.assertEqual(command[2:], ["cache", "clean", "--force"])
            (target / "data").unlink()
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(CACHES.shutil, "which", side_effect=lambda name:
                          None if name == "npm" else name if name.startswith("/") else "/mock/" + name):
            code, text = self.run_command("--only", "npm", native=native)
        self.assertEqual(code, 0, text)
        self.assertFalse((self.cache / "data").exists())
        self.assertFalse((second / "data").exists())
        self.assertEqual(sum(cmd[-1] == "--force" for cmd, _ in self.calls), 2)
        self.assertTrue(all((inst / "bin/npm").exists() for inst in installations))

    def test_modern_yarn_only_cleans_forced_global_cache_and_preserves_project_cache(self):
        package = self.home / "corepack/v1/yarn/4.18.0"
        package.mkdir(parents=True)
        entry = package / "yarn.js"
        entry.write_text("fixture, never executed")
        project = self.home / ".yarn/cache"
        project.mkdir(parents=True)
        sentinel = project / "tracked.zip"
        sentinel.write_bytes(b"keep")

        def native(command, **kwargs):
            self.calls.append((command, kwargs))
            self.assertEqual(command[:2], ["/mock/node", str(entry)])
            self.assertEqual(kwargs["env"]["YARN_ENABLE_GLOBAL_CACHE"], "true")
            self.assertEqual(kwargs["env"]["YARN_IGNORE_PATH"], "1")
            if command[2:] == ["--version"]:
                value = "4.18.0"
            elif command[2:] == ["config", "get", "cacheFolder"]:
                value = str(self.cache)
            else:
                self.assertEqual(command[2:], ["cache", "clean"])
                (self.cache / "data").unlink()
                value = ""
            return subprocess.CompletedProcess(command, 0, value, "")

        with patch.object(CACHES.shutil, "which", side_effect=lambda name:
                          None if name == "yarn" else name if name.startswith("/") else "/mock/" + name):
            code, text = self.run_command("--only", "yarn", native=native)
        self.assertEqual(code, 0, text)
        self.assertFalse((self.cache / "data").exists())
        self.assertEqual(sentinel.read_bytes(), b"keep")
        self.assertTrue(entry.exists())

    def test_yarn_preview_distinguishes_existing_and_missing_cache_locations(self):
        classic = CACHES.replace(CACHES.BY_NAME["yarn"], tool="/mock/classic")
        modern = CACHES.replace(CACHES.BY_NAME["yarn"], tool="/mock/modern")
        missing = self.home / ".yarn/berry/cache"

        def native(command, **kwargs):
            if command[-1] == "--version":
                value = "1.22.22" if command[0] == classic.tool else "4.18.0"
            else:
                value = str(self.cache if command[0] == classic.tool else missing)
            return subprocess.CompletedProcess(command, 0, value, "")

        with patch.object(CACHES, "selected_cleaners", return_value=[classic, modern]):
            code, text = self.run_command("--only", "yarn", "--dry-run", native=native)
        self.assertEqual(code, 0, text)
        self.assertIn("~/custom-uv-cache", text)
        self.assertIn("No cache directory\n    Cache location: ~/.yarn/berry/cache\n", text)
        self.assertTrue((self.cache / "data").exists())

    def test_failed_secondary_npm_query_blocks_all_cleaning(self):
        installation = self.npm_installation("v24.1.0")

        def native(command, **kwargs):
            if command[0] == str(installation / "bin/node"):
                return subprocess.CompletedProcess(command, 1, "", "unsupported runtime")
            return subprocess.CompletedProcess(command, 0, str(self.cache), "")

        with patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")), \
                patch.object(CACHES.subprocess, "run", side_effect=native), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = CACHES.main(["--only", "npm"])
        self.assertEqual(code, 1, stdout.getvalue())
        self.assertIn("unsupported runtime", stdout.getvalue())
        self.assertTrue((self.cache / "data").exists())

    def test_package_cache_cannot_enclose_installed_managers(self):
        self.npm_installation("v24.1.0")
        with patch.object(CACHES.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, str(self.home / "fnm"), "")), \
                patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = CACHES.main(["--only", "npm"])
        self.assertEqual(code, 1)
        self.assertIn("would include installed tools", stdout.getvalue())

    def test_configured_inventory_skips_absent_tools_without_running_download_shims(self):
        with patch.object(CACHES.shutil, "which", return_value=None), \
                patch.object(CACHES.subprocess, "run", side_effect=AssertionError("tool ran")):
            locations, skipped, errors = CACHES.configured_locations()
        self.assertEqual(errors, [])
        self.assertTrue({"npm", "yarn", "pnpm", "mise"}.issubset({name for name, _ in skipped}))
        self.assertTrue(all(name in ("gradle", "playwright", "xcode") for name, _, _ in locations))

    def mise_native(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1:] == ["cache", "path"]:
            value = str(self.cache)
            self.assertEqual(kwargs["env"]["MISE_CACHE_PRUNE_AGE"], "0s")
        elif command[1:] == ["settings", "--json"]:
            value = json.dumps({"task": {"cache_dir": str(self.home / "task-cache")}})
        elif command[1:] == ["cache", "prune"]:
            self.assertEqual(kwargs["env"]["MISE_CACHE_PRUNE_AGE"], "14d")
            (self.cache / "data").unlink()
            value = ""
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, value, "")

    def test_mise_native_pruning_is_confirmed_and_preserves_installed_tools_and_state(self):
        installed = self.home / ".local/share/mise/installs/go/bin/go"
        installed.parent.mkdir(parents=True)
        installed.write_text("keep runtime")
        state = self.home / ".local/state/mise"
        (state / "env-cache").mkdir(parents=True)
        sentinel = state / "tracked-configs"
        sentinel.write_text("keep state")
        (self.home / "task-cache").mkdir()
        with patch.dict(os.environ, {"MISE_CACHE_PRUNE_AGE": "14d"}):
            code, text = self.run_command("--only", "mise", "--dry-run", native=self.mise_native)
            self.assertEqual(code, 0, text)
            self.assertTrue((self.cache / "data").exists())
            self.assertFalse(any(cmd[-1] == "prune" for cmd, _ in self.calls))
            self.assertIn("mise-env", text)
            self.assertIn("mise-tasks", text)
            code, text = self.run_command("--only", "mise", native=self.mise_native)
        self.assertEqual(code, 0, text)
        self.assertFalse((self.cache / "data").exists())
        self.assertEqual(installed.read_text(), "keep runtime")
        self.assertEqual(sentinel.read_text(), "keep state")
        self.assertEqual(sum(cmd[-1] == "prune" for cmd, _ in self.calls), 1)

    def test_mise_changed_auxiliary_path_or_overlap_blocks_cleanup(self):
        for mode in ("changed", "overlap", "installed"):
            with self.subTest(mode=mode):
                settings_calls = 0

                def native(command, **kwargs):
                    nonlocal settings_calls
                    if command[1:] == ["settings", "--json"]:
                        settings_calls += 1
                        task = self.home / "task-cache"
                        if mode == "overlap":
                            task = self.home / ".local/share/mise"
                        elif mode == "installed":
                            task = self.home / ".local/share/mise/installs/go"
                        elif settings_calls > 1:
                            task = self.home / "other-task-cache"
                        return subprocess.CompletedProcess(command, 0, json.dumps(
                            {"task": {"cache_dir": str(task)}}), "")
                    return self.mise_native(command, **kwargs)

                with patch.dict(os.environ, {"MISE_CACHE_PRUNE_AGE": "14d"}):
                    code, text = self.run_command("--only", "mise", native=native)
                self.assertEqual(code, 1, text)
                self.assertTrue((self.cache / "data").exists())
                self.assertIn("changed after preview" if mode == "changed" else
                              "is inside" if mode == "installed" else "would include", text)

    def orphan_fixture(self, name):
        root = CACHES.orphan_caches.conventional_path(name)
        root.mkdir(parents=True)
        if name == "bun":
            package = root / "example@1.2.3@@@1"
            package.mkdir()
            (package / "package.json").write_text(json.dumps({"name": "example", "version": "1.2.3"}))
            payload = package / "data"
            aliases = root / "example"
            aliases.mkdir()
            (aliases / "1.2.3@@@1").symlink_to(package)
        elif name == "npm":
            payload = root / "_cacache/content-v2/data"
        elif name == "pip":
            payload = root / "http-v2/data"
        else:
            payload = root / "ab" / ("ab" + "0" * 62 + "-d")
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(b"cache artifacts" * 1000)
        preserved = root / {"npm": "_logs", "pip": "selfcheck", "go-build": "fuzz",
                            "bun": "unrecognised-notes"}[name] / "keep"
        preserved.parent.mkdir()
        preserved.write_bytes(b"keep")
        return root, payload, preserved

    def test_orphan_preview_cancel_and_apply_preserve_noncache_data(self):
        for name in ("bun", "npm", "pip", "go-build"):
            with self.subTest(name=name):
                root, payload, preserved = self.orphan_fixture(name)
                with patch.object(CACHES.shutil, "which", return_value=None):
                    code, text = self.run_command("--only", name, "--orphaned", "--dry-run")
                    self.assertEqual(code, 0, text)
                    self.assertIn("Preserved:", text)
                    self.assertTrue(payload.exists())
                    code, text = self.run_command("--only", name, "--orphaned", answer="no")
                    self.assertEqual(code, 0, text)
                    self.assertTrue(payload.exists())
                    code, text = self.run_command("--only", name, "--orphaned")
                self.assertEqual(code, 0, text)
                self.assertFalse(payload.exists())
                self.assertEqual(preserved.read_bytes(), b"keep")
                self.assertTrue(root.is_dir())
                if name == "bun":
                    self.assertFalse((root / "example").exists())
        self.assertEqual(self.calls, [])

    def test_orphan_go_preserves_fuzz_corpus_and_nonstandard_shards(self):
        root, payload, fuzz = self.orphan_fixture("go-build")
        foreign = root / "cd/my-source.go"
        foreign.parent.mkdir()
        foreign.write_text("keep source")
        with patch.object(CACHES.shutil, "which", return_value=None):
            code, text = self.run_command("--only", "go-build", "--orphaned")
        self.assertEqual(code, 0, text)
        self.assertFalse(payload.exists())
        self.assertEqual(fuzz.read_bytes(), b"keep")
        self.assertEqual(foreign.read_text(), "keep source")
        self.assertIn("Preserved: fuzz", text)
        self.assertIn("Preserved: cd", text)

    def test_orphan_flag_requires_supported_explicit_selection(self):
        for args in (["--orphaned", "--dry-run"], ["--only", "uv", "--orphaned"],
                     ["--only", "bun,pnpm", "--orphaned"], ["--only", "go-modules", "--orphaned", "--deep"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    CACHES.main(args)
                self.assertEqual(exc.exception.code, 2)

    def test_orphan_refuses_installed_tool_or_discovery_error(self):
        root, payload, _ = self.orphan_fixture("bun")
        for failure in (False, True):
            with self.subTest(failure=failure), patch.object(CACHES, "selected_cleaners",
                    side_effect=OSError("discovery failed") if failure else None,
                    return_value=[CACHES.BY_NAME["bun"]]), \
                    patch.object(CACHES, "confirm_cleanup", side_effect=AssertionError("prompted")):
                code, text = self.run_command("--only", "bun", "--orphaned")
            self.assertEqual(code, 1)
            self.assertIn("discovery failed" if failure else "tool is installed", text)
            self.assertTrue(payload.exists())
        self.assertTrue(root.exists())

    def test_orphan_rechecks_every_plan_before_any_deletion(self):
        _, npm, _ = self.orphan_fixture("npm")
        _, pip, _ = self.orphan_fixture("pip")

        def confirm():
            pip.write_bytes(b"changed")
            return True

        with patch.object(CACHES.shutil, "which", return_value=None), \
                patch.object(CACHES, "confirm_cleanup", side_effect=confirm), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = CACHES.main(["--only", "npm,pip", "--orphaned"])
        self.assertEqual(code, 1)
        self.assertIn("changed after preview", stdout.getvalue())
        self.assertTrue(npm.exists())
        self.assertEqual(pip.read_bytes(), b"changed")

    def test_orphan_tool_appearing_after_confirmation_blocks_deletion(self):
        _, payload, _ = self.orphan_fixture("bun")
        with patch.object(CACHES, "selected_cleaners", side_effect=[[], [CACHES.BY_NAME["bun"]]]):
            code, text = self.run_command("--only", "bun", "--orphaned")
        self.assertEqual(code, 1)
        self.assertIn("tool became available", text)
        self.assertTrue(payload.exists())

    def test_orphan_unreadable_or_changed_entry_fails_without_success(self):
        _, payload, _ = self.orphan_fixture("pip")
        with patch.object(CACHES.shutil, "which", return_value=None), \
                patch.object(CACHES.orphan_caches, "remove", side_effect=PermissionError("denied")):
            code, text = self.run_command("--only", "pip", "--orphaned")
        self.assertEqual(code, 1)
        self.assertIn("denied", text)
        self.assertNotIn("artifacts removed", text)
        self.assertTrue(payload.exists())

    def test_orphan_root_symlinks_are_refused_and_internal_links_never_followed(self):
        root = CACHES.orphan_caches.conventional_path("pip")
        root.parent.mkdir(parents=True)
        root.symlink_to(self.cache, target_is_directory=True)
        with patch.object(CACHES.shutil, "which", return_value=None):
            code, text = self.run_command("--only", "pip", "--orphaned")
            self.assertEqual(code, 1)
            self.assertIn("symlink cache path", text)
            root.unlink()
            root.mkdir()
            http = root / "http-v2"
            http.mkdir()
            (http / "outside").symlink_to(self.cache, target_is_directory=True)
            code, text = self.run_command("--only", "pip", "--orphaned")
        self.assertEqual(code, 0, text)
        self.assertFalse(http.exists())
        self.assertTrue((self.cache / "data").exists())

    def test_orphan_unknown_bun_layout_and_npm_entries_are_preserved(self):
        for name in ("bun", "npm"):
            with self.subTest(name=name):
                root, payload, preserved = self.orphan_fixture(name)
                if name == "bun":
                    (payload.parent / "package.json").write_text('{"name":"unrelated","version":"1.2.3"}')
                else:
                    (root / "_cacache/my-source").write_text("keep source")
                with patch.object(CACHES.shutil, "which", return_value=None):
                    code, text = self.run_command("--only", name, "--orphaned")
                self.assertEqual(code, 0, text)
                self.assertTrue(payload.exists())
                self.assertEqual(preserved.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
