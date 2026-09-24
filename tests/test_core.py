import tempfile
import os
import time
import unittest
from unittest import mock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worktree_harbor import BRANCH_RE, CmdResult, Harbor, classify_branch, core_branches_for, discover_repositories, enrich_worktree, miniapp_project_for, parse_worktree_porcelain, preview_kind_for, run, status_for, web_preview_script


class CoreTests(unittest.TestCase):
    def test_remove_dialog_cancel_controls_cannot_submit(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text()
        script = (root / "static" / "app.js").read_text()
        self.assertIn('id="close-remove" type="button"', html)
        self.assertIn('id="cancel-remove" type="button"', html)
        self.assertIn('id="confirm-remove" type="button"', html)
        self.assertIn('$("#confirm-remove").addEventListener("click",confirmPendingRemoval)', script)
        self.assertIn('$("#remove-form").addEventListener("submit",e=>e.preventDefault())', script)

    def test_main_directory_is_first_and_base_source_copy_is_explicit(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "app.js").read_text()
        main_group = '["core", "仓库主目录 · 固定在首位"'
        active_group = '["active", "正在开发 · 本地未提交"'
        self.assertLess(script.index(main_group), script.index(active_group))
        self.assertIn("来源分支", script)
        self.assertIn("Git 创建记录", script)
        self.assertIn("提交关系判断", script)

    def test_native_window_has_a_dedicated_drag_strip(self):
        root = Path(__file__).resolve().parents[1]
        swift = (root / "macos" / "WorktreeHarborApp.swift").read_text()
        self.assertIn("window.isMovableByWindowBackground = true", swift)
        self.assertIn("final class WindowDragView: NSView", swift)
        self.assertIn("window?.performDrag(with: event)", swift)
        self.assertIn("container.addSubview(dragView)", swift)

    def test_sidebar_filter_cluster_is_removed(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text()
        script = (root / "static" / "app.js").read_text()
        self.assertNotIn('class="nav-item', html)
        self.assertNotIn("const navCounts=", script)

    def test_brand_styles_define_clear_surface_and_action_hierarchy(self):
        root = Path(__file__).resolve().parents[1]
        styles = (root / "static" / "priorities.css").read_text()
        self.assertIn("Harbor brand system v2", styles)
        self.assertIn("--harbor-brand: #6c7cff", styles)
        self.assertIn("grid-template-columns: minmax(270px,1.3fr)", styles)
        self.assertIn(".worktree { grid-template-columns: minmax(260px,1fr) max-content", styles)
        self.assertIn(".preview-split { padding: 2px", styles)
        self.assertIn(".delete-direct,.delete-force", styles)
        self.assertIn("--worktree-grid: minmax(275px,1.25fr)", styles)
        self.assertIn(".actions-heading,.row-actions", styles)
        self.assertIn("grid-template-columns: 58px 104px minmax(60px,1fr)", styles)

    def test_worktree_folder_links_and_preview_labels_are_explicit(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "app.js").read_text()
        self.assertIn('class="branch-folder-link"', script)
        self.assertIn('class="path-folder-link"', script)
        self.assertIn('data-row-action="open_folder"', script)
        self.assertIn('kinds.has("wechat") ? "开发者工具" : kinds.has("web") ? "网页预览"', script)
        self.assertIn('${actionsHeading(repo)}', script)
        self.assertNotIn('class="preview-group"', script)
        self.assertNotIn('>微信·测试</button>', script)
        self.assertNotIn('>网页·测试</button>', script)

    def test_open_folder_action_opens_the_registered_worktree(self):
        root = Path(__file__).resolve().parents[1]
        backend = (root / "worktree_harbor.py").read_text()
        self.assertIn('if action == "open_folder":', backend)
        self.assertIn('result = run(("open", str(target)))', backend)

    def test_imported_directory_is_persisted_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            project = root / "project"
            project.mkdir(parents=True)
            harbor = Harbor({"roots": [str(root)], "max_depth": 3, "stale_days": 30})
            with mock.patch("worktree_harbor.write_config") as write:
                existing = harbor.import_root(str(project))
                self.assertIn("已在扫描范围内", existing["message"])
                write.assert_not_called()
                external = Path(temp) / "external"
                external.mkdir()
                imported = harbor.import_root(str(external))
                resolved_external = str(external.resolve())
                self.assertIn(resolved_external, imported["message"])
                self.assertIn(resolved_external, harbor.config["roots"])
                write.assert_called_once()

    def test_import_directory_button_uses_native_picker_action(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text()
        script = (root / "static" / "app.js").read_text()
        backend = (root / "worktree_harbor.py").read_text()
        self.assertIn('id="import-directory"', html)
        self.assertIn('action:"import_directory"', script)
        self.assertIn('if action == "import_directory":', backend)
        self.assertIn('choose folder with prompt', backend)

    def test_cleanup_filter_can_exit_and_zero_count_cannot_trap_page(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text()
        script = (root / "static" / "app.js").read_text()
        self.assertIn('id="cleanup-button-label"', html)
        self.assertIn('if(activeFilter==="cleanup"&&c.cleanup===0)activeFilter="all"', script)
        self.assertIn('$("#cleanup-button").disabled=c.cleanup===0', script)
        self.assertIn('setFilter(activeFilter==="cleanup"?"all":"cleanup")', script)
        self.assertIn('if(workspace){activeFilter="all";', script)

    def test_first_launch_environment_check_is_available(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text()
        script = (root / "static" / "app.js").read_text()
        backend = (root / "worktree_harbor.py").read_text()
        self.assertIn('id="environment-dialog"', html)
        self.assertIn('id="environment-check-button"', html)
        self.assertIn("maybeOpenOnboarding();", script)
        self.assertIn('action:"check_environment"', script)
        self.assertIn('if action == "check_environment":', backend)
        self.assertIn('"onboarding_version": 0', backend)
        self.assertNotIn('/Users/example/private-project', backend)
        self.assertNotIn('/Users/example/internal-workspace', backend)

    def test_editors_are_detected_in_priority_order_and_rows_use_auto_editor(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "app.js").read_text()
        harbor = Harbor({"roots": []})
        available = {"cursor": Path("/Applications/Cursor.app"), "intellij": Path("/Applications/IntelliJ IDEA.app")}
        with mock.patch.object(Harbor, "editor_path", side_effect=lambda candidate: available.get(candidate["id"])):
            editors = harbor.detected_editors()
            self.assertEqual([editor["id"] for editor in editors], ["cursor", "intellij"])
            self.assertEqual(harbor.preferred_editor()["id"], "cursor")
        self.assertIn('data-row-action="open_editor"', script)
        self.assertNotIn('data-row-action="open_cursor"', script)

    def test_repository_can_persist_its_own_editor(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "app.js").read_text()
        backend = (root / "worktree_harbor.py").read_text()
        self.assertIn("data-editor-select", script)
        self.assertIn('action:"set_repo_editor"', script)
        self.assertIn("def set_repo_editor", backend)
        self.assertIn("repo_editors[repo_id] = editor_id", backend)
        self.assertIn('self.preferred_editor(editors, str(repository["id"]))', backend)

    def test_parse_worktrees(self):
        data = "worktree /tmp/main\nHEAD abc\nbranch refs/heads/main\n\nworktree /tmp/fix\nHEAD def\ndetached\nlocked building\n\n"
        rows = parse_worktree_porcelain(data)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["branch"], "refs/heads/main")
        self.assertTrue(rows[1]["detached"])
        self.assertEqual(rows[1]["locked"], "building")

    def test_branch_validation(self):
        for valid in ["fix/a", "feature-1", "codex/task_name"]:
            self.assertRegex(valid, BRANCH_RE)
        for invalid in ["", "/bad", "bad..name", "bad name", "bad.lock/"]:
            self.assertNotRegex(invalid, BRANCH_RE)

    def test_classifies_only_canonical_environment_branches(self):
        for branch in ["main", "master", "team_master", "product-master"]:
            self.assertEqual(classify_branch(branch), "main")
        for branch in ["team_test", "product_test", "release_test", "staging"]:
            self.assertEqual(classify_branch(branch), "test")
        for branch in ["product_pre_0910", "team_pre_260724", "pre_20260917"]:
            self.assertEqual(classify_branch(branch), "pre")
        for branch in ["fix/0904-non-auto-p0-p1-test-cleanup", "integration/0915-map-search-team-test", "fix/0903-decoration-bugs-master"]:
            self.assertEqual(classify_branch(branch), "other")

    def test_core_branches_ignore_current_dated_pre_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            self.assertEqual(run(["git", "init", "-q", "-b", "product_pre_0917", str(repo)]).code, 0)
            run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
            run(["git", "-C", str(repo), "config", "user.name", "Test"])
            (repo / "a.txt").write_text("a")
            run(["git", "-C", str(repo), "add", "a.txt"])
            run(["git", "-C", str(repo), "commit", "-qm", "init"])
            run(["git", "-C", str(repo), "branch", "team_master"])
            run(["git", "-C", str(repo), "branch", "team_test"])
            self.assertEqual(core_branches_for(repo), {"main": "team_master", "test": "team_test"})

    def test_marks_committed_head_merged_into_test_but_not_main(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            run(["git", "init", "-q", "-b", "team_master", str(repo)])
            run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
            run(["git", "-C", str(repo), "config", "user.name", "Test"])
            (repo / "a.txt").write_text("base")
            run(["git", "-C", str(repo), "add", "a.txt"])
            run(["git", "-C", str(repo), "commit", "-qm", "base"])
            run(["git", "-C", str(repo), "switch", "-q", "-c", "feature/demo"])
            (repo / "a.txt").write_text("feature")
            run(["git", "-C", str(repo), "commit", "-qam", "feature"])
            head = run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout
            run(["git", "-C", str(repo), "branch", "team_test", head])
            row = {"path": str(repo), "branch": "feature/demo", "branch_role": "other", "head": head, "is_main": False}
            enrich_worktree(row, 30, repo, ["team_master", "team_test"], {"main": "team_master", "test": "team_test"})
            self.assertFalse(row["merged_into"]["main"])
            self.assertTrue(row["merged_into"]["test"])

    def test_discovers_common_repo_once(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            wt = Path(temp) / "wt"
            repo.mkdir()
            self.assertEqual(run(["git", "init", "-q", "-b", "main", str(repo)]).code, 0)
            run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
            run(["git", "-C", str(repo), "config", "user.name", "Test"])
            (repo / "a.txt").write_text("a")
            run(["git", "-C", str(repo), "add", "a.txt"])
            run(["git", "-C", str(repo), "commit", "-qm", "init"])
            self.assertEqual(run(["git", "-C", str(repo), "worktree", "add", "-qb", "fix/a", str(wt)]).code, 0)
            found = discover_repositories([Path(temp)], 4)
            self.assertEqual(len(found), 1)

    def test_repository_actions_are_anchored_to_main_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "z-main-repo"
            wt = root / "a-linked-worktree"
            repo.mkdir()
            self.assertEqual(run(["git", "init", "-q", "-b", "main", str(repo)]).code, 0)
            run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
            run(["git", "-C", str(repo), "config", "user.name", "Test"])
            (repo / "a.txt").write_text("a")
            run(["git", "-C", str(repo), "add", "a.txt"])
            run(["git", "-C", str(repo), "commit", "-qm", "init"])
            self.assertEqual(run(["git", "-C", str(repo), "worktree", "add", "-qb", "fix/a", str(wt)]).code, 0)

            harbor = Harbor({"roots": [str(root)], "max_depth": 4, "stale_days": 30})
            state = harbor.scan()
            managed = next(item for item in state["repositories"] if Path(item["path"]).resolve() == repo.resolve())
            self.assertEqual(harbor.repo_paths[managed["id"]], repo.resolve())

    def test_discovers_repository_inside_aggregate_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            aggregate = Path(temp) / "aggregate"
            nested = aggregate / "nested-project"
            aggregate.mkdir()
            nested.mkdir()
            self.assertEqual(run(["git", "init", "-q", str(aggregate)]).code, 0)
            self.assertEqual(run(["git", "init", "-q", str(nested)]).code, 0)
            found = discover_repositories([aggregate], 4)
            self.assertEqual({path.resolve() for path in found}, {aggregate.resolve(), nested.resolve()})

    def test_classifies_repositories_inside_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            harbor = Harbor({"roots": [str(root)]})
            self.assertEqual(harbor.workspace_for(root)["kind"], "aggregate")
            self.assertEqual(harbor.workspace_for(root / "backend_service")["kind"], "project")
            self.assertEqual(harbor.workspace_for(root / ".worktrees" / "fix-a")["kind"], "temporary")
            self.assertEqual(harbor.workspace_for(root / "backend_service")["name"], "workspace")

    def test_dirty_activity_uses_changed_file_time(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            self.assertEqual(run(["git", "init", "-q", "-b", "main", str(repo)]).code, 0)
            run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
            run(["git", "-C", str(repo), "config", "user.name", "Test"])
            changed = repo / "a.txt"
            changed.write_text("a")
            run(["git", "-C", str(repo), "add", "a.txt"])
            run(["git", "-C", str(repo), "commit", "-qm", "init"])
            changed.write_text("changed")
            old = time.time() - 45 * 86400
            os.utime(changed, (old, old))
            status = status_for(repo)
            self.assertTrue(status["dirty"])
            self.assertGreaterEqual(status["dirty_age_days"], 44)

    def test_detects_preview_surface_and_separates_miniapp_environments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{"scripts":{"dev:mp-weixin":"uni -p mp-weixin","dev":"vite"}}')
            test_project = root / "dist" / "test" / "mp-weixin"
            dev_project = root / "dist" / "dev" / "mp-weixin"
            production_project = root / "dist" / "build" / "mp-weixin"
            for project in (test_project, dev_project, production_project):
                project.mkdir(parents=True)
                (project / "project.config.json").write_text("{}")
            self.assertEqual(preview_kind_for(root), "wechat")
            self.assertEqual(miniapp_project_for(root), test_project)
            self.assertEqual(miniapp_project_for(root, "production"), production_project)

            (root / "package.json").write_text('{"scripts":{"dev":"vite"}}')
            self.assertEqual(preview_kind_for(root), "web")

    def test_web_preview_replaces_previous_worktree_and_reuses_url(self):
        class FakeProcess:
            next_pid = 1000

            def __init__(self):
                self.pid = FakeProcess.next_pid
                FakeProcess.next_pid += 1

            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            for target in (first, second):
                (target / "node_modules").mkdir(parents=True)
                (target / "package.json").write_text('{"scripts":{"dev":"vite"}}')
            harbor = Harbor({"roots": [temp]})
            processes = [FakeProcess(), FakeProcess()]
            open_calls = []

            def fake_run(argv, **kwargs):
                open_calls.append(tuple(argv))
                return CmdResult(0, "", "")

            with mock.patch.object(harbor, "command_path", return_value="/bin/pnpm"), \
                 mock.patch.object(harbor, "stop_preview_process") as stop, \
                 mock.patch("worktree_harbor.subprocess.Popen", side_effect=processes), \
                 mock.patch("worktree_harbor.socket.create_connection"), \
                 mock.patch("worktree_harbor.run", side_effect=fake_run):
                harbor.open_web_preview(first, "test")
                first_url = harbor.web_preview[1]
                message = harbor.open_web_preview(second, "production")
            self.assertEqual(harbor.web_preview[1], first_url)
            self.assertEqual(harbor.web_preview[2], second)
            self.assertEqual(harbor.web_preview[3], "production")
            stop.assert_called_once_with(processes[0])
            self.assertEqual(open_calls, [("open", first_url)])
            self.assertIn("替换上一个", message)

    def test_worktree_can_borrow_and_release_main_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root / "main"
            target = root / "worktree"
            (main / "node_modules").mkdir(parents=True)
            target.mkdir()
            harbor = Harbor({"roots": [str(root)]})
            link = harbor.borrow_main_dependencies(target, main)
            self.assertIsNotNone(link)
            self.assertTrue((target / "node_modules").is_symlink())
            self.assertEqual((target / "node_modules").resolve(), (main / "node_modules").resolve())
            harbor.release_borrowed_dependencies(link)
            self.assertFalse((target / "node_modules").exists())

    def test_web_preview_uses_project_test_and_production_scripts(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "package.json").write_text('{"scripts":{"dev":"vite","test":"vite --mode test","dev:prod":"vite --mode production"}}')
            self.assertEqual(web_preview_script(target, "test"), ("test", []))
            self.assertEqual(web_preview_script(target, "production"), ("dev:prod", []))

    def test_wechat_project_replaces_only_previous_harbor_project(self):
        with tempfile.TemporaryDirectory() as temp:
            first_root = Path(temp) / "first"
            second_root = Path(temp) / "second"
            first = first_root / "dist/dev/mp-weixin"
            second = second_root / "dist/dev/mp-weixin"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            harbor = Harbor({"roots": [temp]})
            calls = []

            def fake_run(argv, **kwargs):
                calls.append(tuple(argv))
                return CmdResult(0, "", "")

            with mock.patch.object(harbor, "wechat_cli_path", return_value="/Applications/DevTools/cli"), \
                 mock.patch("worktree_harbor.run", side_effect=fake_run):
                harbor.open_wechat_project(first_root, first, "测试")
                message = harbor.open_wechat_project(second_root, second, "生产")
            self.assertEqual(calls[0][1], "open")
            self.assertEqual(calls[1][1:3], ("close", "--project"))
            self.assertEqual(calls[2][1], "open")
            self.assertEqual(harbor.wechat_project, second)
            self.assertIn("替换为", message)

    def test_missing_test_artifact_is_built_before_opening(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "node_modules").mkdir()
            (target / "pnpm-lock.yaml").write_text("")
            (target / "package.json").write_text('{"scripts":{"build:mp-weixin-test":"uni build -p mp-weixin --mode testing --outDir=./dist/test/mp-weixin"}}')
            harbor = Harbor({"roots": [temp]})
            calls = []

            def fake_run(argv, cwd=None, **kwargs):
                calls.append((tuple(argv), cwd))
                project = target / "dist/test/mp-weixin"
                project.mkdir(parents=True)
                (project / "project.config.json").write_text("{}")
                return CmdResult(0, "built", "")

            with mock.patch.object(harbor, "command_path", return_value="/bin/pnpm"), \
                 mock.patch("worktree_harbor.run", side_effect=fake_run):
                project, built = harbor.ensure_miniapp_project(target, "test")
            self.assertTrue(built)
            self.assertEqual(project, target / "dist/test/mp-weixin")
            self.assertEqual(calls, [(("/bin/pnpm", "run", "build:mp-weixin-test"), target)])

    def test_confirmed_dirty_worktree_deletes_directly_and_preserves_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            worktree = Path(temp) / "dirty-worktree"
            repo.mkdir()
            self.assertEqual(run(["git", "init", "-q", "-b", "main", str(repo)]).code, 0)
            run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
            run(["git", "-C", str(repo), "config", "user.name", "Test"])
            (repo / "a.txt").write_text("a")
            run(["git", "-C", str(repo), "add", "a.txt"])
            run(["git", "-C", str(repo), "commit", "-qm", "init"])
            self.assertEqual(run(["git", "-C", str(repo), "worktree", "add", "-qb", "fix/dirty", str(worktree)]).code, 0)
            (worktree / "a.txt").write_text("uncommitted")

            harbor = Harbor({"roots": [str(repo.parent)], "max_depth": 3, "stale_days": 30})
            state = harbor.scan()
            managed = next(item for item in state["repositories"] if Path(item["path"]).resolve() == repo.resolve())
            dirty_row = next(item for item in managed["worktrees"] if item["branch"] == "fix/dirty")
            self.assertEqual(dirty_row["base_branch"]["name"], "main")
            payload = {"action": "remove", "repo_id": managed["id"], "path": str(worktree), "confirm": worktree.name}
            result = harbor.action(payload)
            self.assertFalse(worktree.exists())
            self.assertEqual(run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", "refs/heads/fix/dirty"]).code, 0)
            self.assertEqual(result["message"], "已强制删除工作树；分支仍保留")

    def test_batch_remove_supports_clean_and_dirty_worktrees(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            clean_worktree = Path(temp) / "clean-worktree"
            dirty_worktree = Path(temp) / "dirty-worktree"
            repo.mkdir()
            self.assertEqual(run(["git", "init", "-q", "-b", "main", str(repo)]).code, 0)
            run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
            run(["git", "-C", str(repo), "config", "user.name", "Test"])
            (repo / "a.txt").write_text("a")
            run(["git", "-C", str(repo), "add", "a.txt"])
            run(["git", "-C", str(repo), "commit", "-qm", "init"])
            run(["git", "-C", str(repo), "worktree", "add", "-qb", "fix/clean", str(clean_worktree)])
            run(["git", "-C", str(repo), "worktree", "add", "-qb", "fix/dirty", str(dirty_worktree)])
            (dirty_worktree / "a.txt").write_text("dirty")

            harbor = Harbor({"roots": [str(repo.parent)], "max_depth": 3, "stale_days": 30})
            managed = next(item for item in harbor.scan()["repositories"] if Path(item["path"]).resolve() == repo.resolve())
            items = [
                {"repo_id": managed["id"], "path": str(clean_worktree), "force": False},
                {"repo_id": managed["id"], "path": str(dirty_worktree), "force": False},
            ]
            result = harbor.action({"action": "remove_many", "items": items, "confirm": "REMOVE_2"})
            self.assertFalse(clean_worktree.exists())
            self.assertFalse(dirty_worktree.exists())
            self.assertEqual(result["message"], "已删除 2 个工作树；分支均已保留")
            for branch in ["fix/clean", "fix/dirty"]:
                self.assertEqual(run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).code, 0)


if __name__ == "__main__":
    unittest.main()
