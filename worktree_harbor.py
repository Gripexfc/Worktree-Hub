#!/usr/bin/env python3
"""Worktree Harbor: a safe, local dashboard for Git worktrees."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("WORKTREE_HARBOR_DATA_DIR", str(APP_DIR / ".data"))).expanduser().resolve()
CONFIG_PATH = DATA_DIR / "config.json"
DEFAULT_SKIPS = {
    ".git", ".idea", ".vscode", "node_modules", "dist", "build", "target",
    ".next", ".nuxt", ".turbo", ".cache", "vendor", "coverage", "__pycache__",
}
BRANCH_RE = re.compile(r"^(?!/)(?!.*(?:\.\.|//|@\{|\\))[^\s~^:?*\[]+(?<![/.])$")
EDITOR_CANDIDATES = (
    {"id": "cursor", "label": "Cursor", "app_names": ("Cursor",), "bundle_ids": ("com.todesktop.230313mzl4w4u92",)},
    {"id": "vscode", "label": "Visual Studio Code", "app_names": ("Visual Studio Code",), "bundle_ids": ("com.microsoft.VSCode",)},
    {"id": "webstorm", "label": "WebStorm", "app_names": ("WebStorm", "WebStorm EAP"), "bundle_ids": ("com.jetbrains.WebStorm", "com.jetbrains.WebStorm-EAP")},
    {"id": "intellij", "label": "IntelliJ IDEA", "app_names": ("IntelliJ IDEA", "IntelliJ IDEA CE"), "bundle_ids": ("com.jetbrains.intellij", "com.jetbrains.intellij.ce")},
)


def classify_branch(branch: str) -> str:
    """Classify only canonical environment branches, not similarly named feature branches."""
    name = branch.strip().lower()
    if name == "detached":
        return "detached"
    if "/" in name:
        return "other"
    if name in {"main", "master"} or name.endswith(("_master", "-master")):
        return "main"
    if name in {"test", "testing", "release_test", "uat", "staging"}:
        return "test"
    if re.search(r"(?:^|[-_])pre(?:[-_]|$)", name):
        return "pre"
    if name.endswith(("_test", "-test")) or name.startswith(("test_", "test-")):
        return "test"
    return "other"


class HarborError(Exception):
    pass


@dataclass
class CmdResult:
    code: int
    stdout: str
    stderr: str


def run(argv: Sequence[str], cwd: Optional[Path] = None, timeout: int = 15) -> CmdResult:
    try:
        process = subprocess.run(
            list(argv), cwd=str(cwd) if cwd else None, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
        return CmdResult(process.returncode, process.stdout.strip(), process.stderr.strip())
    except subprocess.TimeoutExpired as exc:
        raise HarborError(f"命令执行超过 {timeout} 秒：{' '.join(argv)}") from exc
    except OSError as exc:
        raise HarborError(f"系统操作失败：{exc}") from exc


def git(repo: Path, *args: str, timeout: int = 15, check: bool = False) -> CmdResult:
    result = run(("git", "-C", str(repo), *args), timeout=timeout)
    if check and result.code != 0:
        raise HarborError(result.stderr or result.stdout or "版本库命令执行失败")
    return result


def clean_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def read_config() -> Dict[str, object]:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "roots": [],
        "max_depth": 5,
        "stale_days": 30,
        "onboarding_version": 0,
    }


def write_config(data: Dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(CONFIG_PATH)


def discover_repositories(roots: Iterable[Path], max_depth: int) -> List[Path]:
    """Return one representative path per Git common directory."""
    repos: Dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        root = root.resolve()
        for current, dirs, files in os.walk(root):
            here = Path(current)
            depth = len(here.relative_to(root).parts)
            has_git_marker = ".git" in dirs or ".git" in files
            dirs[:] = [name for name in dirs if name not in DEFAULT_SKIPS]
            if depth >= max_depth:
                dirs[:] = []
            if not has_git_marker:
                continue
            top = git(here, "rev-parse", "--show-toplevel")
            common = git(here, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if top.code == 0 and common.code == 0:
                repos.setdefault(str(Path(common.stdout).resolve()), Path(top.stdout).resolve())
            # A repository's linked worktrees are returned by `git worktree list`.
            # Avoid traversing its ordinary source tree, but retain direct nested
            # repositories and repository containers. Aggregate project folders
            # are sometimes Git repositories themselves.
            nested_candidates = []
            for name in dirs:
                child = here / name
                if (child / ".git").exists() or name == ".worktrees":
                    nested_candidates.append(name)
                    continue
                try:
                    if any(entry.is_dir() and (Path(entry.path) / ".git").exists() for entry in os.scandir(child)):
                        nested_candidates.append(name)
                except OSError:
                    pass
            dirs[:] = nested_candidates
    return sorted(repos.values(), key=lambda path: str(path).lower())


def parse_worktree_porcelain(text: str) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    current: Dict[str, object] = {}
    for line in text.splitlines() + [""]:
        if not line:
            if current:
                rows.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key in {"detached", "bare"}:
            current[key] = True
        elif key in {"locked", "prunable"}:
            current[key] = value or True
        else:
            current[key] = value
    return rows


def status_for(path: Path) -> Dict[str, object]:
    result = git(path, "status", "--porcelain=v2", "--branch", timeout=20)
    if result.code != 0:
        return {"available": False, "error": "无法读取该目录的版本状态"}
    changed = 0
    untracked = 0
    ahead = 0
    behind = 0
    upstream = ""
    for line in result.stdout.splitlines():
        if line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream "):]
        elif line.startswith("# branch.ab "):
            match = re.search(r"\+(\d+)\s+-(\d+)", line)
            if match:
                ahead, behind = int(match.group(1)), int(match.group(2))
        elif line.startswith("? "):
            untracked += 1
        elif line and not line.startswith("#"):
            changed += 1
    dirty = bool(changed or untracked)
    dirty_timestamp = 0
    if dirty:
        changed_paths = set()
        for command in (
            ("diff", "--name-only", "-z"),
            ("diff", "--cached", "--name-only", "-z"),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        ):
            names = git(path, *command, timeout=20)
            if names.code == 0:
                changed_paths.update(name for name in names.stdout.split("\x00") if name)
        for name in changed_paths:
            candidate = path / name
            try:
                dirty_timestamp = max(dirty_timestamp, int(candidate.stat().st_mtime))
            except OSError:
                pass
        if not dirty_timestamp:
            index = git(path, "rev-parse", "--path-format=absolute", "--git-path", "index")
            if index.code == 0:
                try:
                    dirty_timestamp = int(Path(index.stdout).stat().st_mtime)
                except OSError:
                    pass
    dirty_age_days = int((time.time() - dirty_timestamp) / 86400) if dirty_timestamp else None
    return {
        "available": True,
        "dirty": dirty,
        "changed": changed,
        "untracked": untracked,
        "dirty_timestamp": dirty_timestamp,
        "dirty_age_days": dirty_age_days,
        "ahead": ahead,
        "behind": behind,
        "upstream": upstream,
    }


def commit_for(path: Path) -> Dict[str, object]:
    result = git(path, "show", "-s", "--format=%ct%x00%h%x00%s", "HEAD")
    if result.code != 0:
        return {"timestamp": 0, "short": "", "subject": ""}
    parts = result.stdout.split("\x00", 2)
    if len(parts) != 3:
        return {"timestamp": 0, "short": "", "subject": result.stdout}
    return {"timestamp": int(parts[0]), "short": parts[1], "subject": parts[2]}


def package_scripts(path: Path) -> Dict[str, str]:
    package_path = path / "package.json"
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
    return {str(key): str(value) for key, value in scripts.items()} if isinstance(scripts, dict) else {}


def preview_kind_for(path: Path) -> str:
    """Return the one preview surface that best matches this worktree."""
    scripts = package_scripts(path)
    if any(name.startswith(("dev:mp-weixin", "build:mp-weixin")) for name in scripts):
        return "wechat"
    if "dev" in scripts:
        return "web"
    if (path / "project.config.json").is_file():
        return "wechat"
    return ""


def web_preview_script(path: Path, environment: str) -> Tuple[str, List[str]]:
    """Choose an environment-aware development server without installing anything."""
    scripts = package_scripts(path)
    if environment == "production":
        candidates = ("dev:prod", "dev:production", "serve:prod", "serve:production")
        mode = "production"
    else:
        candidates = ("dev:test", "serve:test", "test")
        mode = "test"
    for name in candidates:
        command = scripts.get(name, "").lower()
        if name in scripts and (name != "test" or ("vite" in command and "vitest" not in command)):
            return name, []
    if "dev" in scripts:
        return "dev", ["--mode", mode]
    environment_label = "生产" if environment == "production" else "测试"
    raise HarborError(f"这个工作树没有可用的{environment_label}环境网页启动脚本")


def miniapp_project_for(path: Path, environment: str = "test") -> Optional[Path]:
    """Return a generated Mini Program project for one explicit environment."""
    relatives = {
        "test": (
            "dist/test/mp-weixin/project.config.json",
        ),
        "production": (
            "dist/build/mp-weixin/project.config.json",
            "dist/production/mp-weixin/project.config.json",
            "dist/prod/mp-weixin/project.config.json",
        ),
    }
    candidates = [path / relative for relative in relatives.get(environment, ())]
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if not existing:
        return None
    newest = max(existing, key=lambda candidate: candidate.stat().st_mtime)
    return newest.parent


def miniapp_environments_for(path: Path) -> Dict[str, bool]:
    return {
        "test": miniapp_project_for(path, "test") is not None,
        "production": miniapp_project_for(path, "production") is not None,
    }


def repo_identity(repo: Path) -> Tuple[str, str]:
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir", check=True)
    common_path = str(Path(common.stdout).resolve())
    repo_id = hashlib.sha256(common_path.encode("utf-8")).hexdigest()[:16]
    return common_path, repo_id


def base_candidates_for(repo: Path) -> List[str]:
    result = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin")
    if result.code != 0:
        return []
    candidates = []
    for ref in result.stdout.splitlines():
        short = ref.removeprefix("origin/")
        if ref != "origin/HEAD" and classify_branch(short) in {"main", "test"}:
            candidates.append(ref)
    return candidates


def core_branches_for(repo: Path) -> Dict[str, str]:
    """Resolve canonical production/test branch names without using current checkout state."""
    result = git(repo, "for-each-ref", "--format=%(refname:short)%09%(committerdate:unix)", "refs/heads", "refs/remotes/origin")
    if result.code != 0:
        return {}
    timestamps: Dict[str, int] = {}
    for line in result.stdout.splitlines():
        ref, _, raw_timestamp = line.partition("\t")
        if ref == "origin/HEAD":
            continue
        name = ref.removeprefix("origin/")
        try:
            timestamps[name] = max(timestamps.get(name, 0), int(raw_timestamp or 0))
        except ValueError:
            timestamps.setdefault(name, 0)

    def newest(role: str) -> str:
        available = [name for name in timestamps if classify_branch(name) == role]
        return max(available, key=lambda name: timestamps[name]) if available else ""

    main = newest("main")
    test = newest("test")
    if not main or not test:
        current = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        if current.code == 0:
            role = classify_branch(current.stdout)
            if role == "main" and not main:
                main = current.stdout
            elif role == "test" and not test:
                test = current.stdout
    return {role: name for role, name in (("main", main), ("test", test)) if name}


def containing_refs_for(repo: Path, head: str) -> List[str]:
    if not head:
        return []
    result = git(repo, "for-each-ref", f"--contains={head}", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin")
    return result.stdout.splitlines() if result.code == 0 else []


def infer_base_branch(repo: Path, row: Dict[str, object], candidates: Sequence[str], containing_refs: Sequence[str] = ()) -> Dict[str, str]:
    """Best-effort base branch. Git stores creation source only in a local reflog."""
    branch = str(row.get("branch", ""))
    if row.get("is_main") or classify_branch(branch) in {"main", "test"}:
        return {"name": "", "source": "core"}
    creation_head = str(row.get("head", ""))
    if branch and branch != "DETACHED":
        reflog = git(repo, "reflog", "show", "--format=%H%x09%gs", f"refs/heads/{branch}")
        if reflog.code == 0 and reflog.stdout:
            oldest = reflog.stdout.splitlines()[-1]
            creation_head, _, subject = oldest.partition("\t")
            match = re.match(r"branch: Created from (.+)", subject)
            if match:
                source = match.group(1)
                if source not in {"HEAD", "head"} and not re.fullmatch(r"[0-9a-fA-F]{7,40}", source):
                    return {"name": source, "source": "recorded"}

    refs = list(containing_refs) if creation_head == str(row.get("head", "")) else containing_refs_for(repo, creation_head)
    matches = [ref for ref in refs if ref in candidates and ref not in {branch, f"origin/{branch}"}]
    if matches:
        lower = branch.lower()
        wants_test = any(token in lower for token in ("test", "pre", "uat", "staging"))
        matches.sort(key=lambda ref: (
            0 if (classify_branch(ref.removeprefix("origin/")) == "test") == wants_test else 1,
            0 if not ref.startswith("origin/") else 1,
            len(ref),
        ))
        return {"name": matches[0], "source": "inferred"}
    return {"name": "", "source": "unknown"}


def enrich_worktree(row: Dict[str, object], stale_days: int, repo: Optional[Path] = None, base_candidates: Sequence[str] = (), core_branches: Optional[Dict[str, str]] = None) -> None:
    path = Path(str(row["path"]))
    available = path.is_dir()
    status = status_for(path) if available else {"available": False, "error": "目录不存在"}
    commit = commit_for(path) if available else {"timestamp": 0, "short": "", "subject": ""}
    age_days = int((time.time() - int(commit["timestamp"])) / 86400) if commit["timestamp"] else None
    containing_refs = containing_refs_for(repo, str(row.get("head", ""))) if repo else []
    merged_into = {}
    for role, branch in (core_branches or {}).items():
        merged_into[role] = branch in containing_refs or f"origin/{branch}" in containing_refs
    preview_kind = preview_kind_for(path) if available else ""
    row.update({
        "status": status,
        "commit": commit,
        "age_days": age_days,
        "stale": age_days is not None and age_days >= stale_days,
        "idle_dirty": bool(status.get("dirty")) and status.get("dirty_age_days") is not None and int(status["dirty_age_days"]) >= stale_days,
        "base_branch": infer_base_branch(repo, row, base_candidates, containing_refs) if repo else {"name": "", "source": "unknown"},
        "merged_into": merged_into,
        "preview_kind": preview_kind,
        "preview_environments": miniapp_environments_for(path) if preview_kind == "wechat" else {},
    })


class Harbor:
    def __init__(self, config: Dict[str, object]):
        self.config = config
        self.lock = threading.Lock()
        self.repo_paths: Dict[str, Path] = {}
        self.web_preview: Optional[Tuple[subprocess.Popen, str, Path, str, Optional[Path]]] = None
        self.wechat_project: Optional[Path] = None

    def roots(self) -> List[Path]:
        return [clean_path(str(value)) for value in self.config.get("roots", [])]

    def workspace_for(self, path: Path) -> Dict[str, str]:
        """Describe the configured workspace that owns a repository path."""
        resolved = path.resolve()
        matches = []
        for root in self.roots():
            try:
                relative = resolved.relative_to(root)
                matches.append((len(root.parts), root, relative))
            except ValueError:
                continue
        if not matches:
            root, relative = resolved.parent, Path(resolved.name)
        else:
            _, root, relative = max(matches, key=lambda item: item[0])
        if resolved == root:
            kind = "project" if (root / ".git").exists() else "aggregate"
        elif ".worktrees" in relative.parts:
            kind = "temporary"
        else:
            kind = "project"
        return {
            "id": hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12],
            "name": root.name or str(root),
            "root": str(root),
            "kind": kind,
            "relative_path": str(relative) if str(relative) != "." else "",
        }

    def scan(self) -> Dict[str, object]:
        started = time.time()
        max_depth = int(self.config.get("max_depth", 5))
        stale_days = int(self.config.get("stale_days", 30))
        repositories = []
        workspaces: Dict[str, Dict[str, str]] = {}
        new_mapping: Dict[str, Path] = {}
        all_worktrees: List[Tuple[Dict[str, object], Path, Sequence[str], Dict[str, str]]] = []
        for repo in discover_repositories(self.roots(), max_depth):
            try:
                common_path, repo_id = repo_identity(repo)
                listing = git(repo, "worktree", "list", "--porcelain", check=True)
            except HarborError:
                continue
            listed_worktrees = parse_worktree_porcelain(listing.stdout)
            listed_main = Path(str(listed_worktrees[0].get("worktree", ""))) if listed_worktrees else repo
            git_root = listed_main if listed_main.is_dir() else repo
            worktrees = []
            base_candidates = base_candidates_for(git_root)
            core_branches = core_branches_for(git_root)
            for index, item in enumerate(listed_worktrees):
                raw_path = str(item.get("worktree", ""))
                path = Path(raw_path)
                branch_ref = str(item.get("branch", ""))
                branch = branch_ref.removeprefix("refs/heads/") if branch_ref else "DETACHED"
                row = {
                    "path": raw_path,
                    "branch": branch,
                    "branch_role": classify_branch(branch),
                    "head": item.get("HEAD", ""),
                    "detached": bool(item.get("detached")),
                    "bare": bool(item.get("bare")),
                    "locked": item.get("locked", False),
                    "prunable": item.get("prunable", False),
                    "is_main": index == 0,
                }
                worktrees.append(row)
                all_worktrees.append((row, git_root, base_candidates, core_branches))
            remote = git(git_root, "remote", "get-url", "origin")
            main_path = Path(str(worktrees[0]["path"])) if worktrees else git_root
            workspace = self.workspace_for(main_path)
            workspaces[workspace["id"]] = {
                "id": workspace["id"],
                "name": workspace["name"],
                "root": workspace["root"],
            }
            repositories.append({
                "id": repo_id,
                "name": main_path.name or repo.name,
                "path": str(main_path),
                "common_dir": common_path,
                "remote": remote.stdout if remote.code == 0 else "",
                "workspace_id": workspace["id"],
                "workspace_name": workspace["name"],
                "workspace_root": workspace["root"],
                "kind": workspace["kind"],
                "relative_path": workspace["relative_path"],
                "core_branches": core_branches,
                "worktrees": worktrees,
            })
            # Actions must use the stable main checkout. A linked worktree may be
            # the first path discovered and can disappear during batch removal.
            new_mapping[repo_id] = git_root
        worker_count = min(24, max(4, len(all_worktrees)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(enrich_worktree, row, stale_days, repo, candidates, core) for row, repo, candidates, core in all_worktrees]
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    # Keep the dashboard usable if one worktree changes mid-scan.
                    # The next refresh will retry it.
                    pass
        with self.lock:
            self.repo_paths = new_mapping
        root_order = {str(root): index for index, root in enumerate(self.roots())}
        kind_order = {"project": 0, "temporary": 1, "aggregate": 2}
        repositories.sort(key=lambda row: (
            root_order.get(str(row["workspace_root"]), 999),
            kind_order.get(str(row["kind"]), 9),
            str(row["name"]).lower(),
            str(row["path"]),
        ))
        editors = self.detected_editors()
        for repository in repositories:
            editor = self.preferred_editor(editors, str(repository["id"]))
            repository["editor_id"] = editor["id"] if editor else ""
            repository["editor_label"] = editor["label"] if editor else "未检测到编辑器"
        return {
            "roots": [str(path) for path in self.roots()],
            "workspaces": sorted(workspaces.values(), key=lambda row: root_order.get(row["root"], 999)),
            "repositories": repositories,
            "editors": editors,
            "settings": {
                "max_depth": max_depth,
                "stale_days": stale_days,
                "onboarding_version": int(self.config.get("onboarding_version", 0)),
            },
            "scan_ms": round((time.time() - started) * 1000),
        }

    def set_config(self, payload: Dict[str, object]) -> Dict[str, object]:
        roots_raw = payload.get("roots")
        if not isinstance(roots_raw, list) or not roots_raw:
            raise HarborError("至少需要填写一个扫描根目录")
        roots = []
        for value in roots_raw:
            path = clean_path(str(value))
            if not path.is_dir():
                raise HarborError(f"目录不存在：{path}")
            roots.append(str(path))
        max_depth = int(payload.get("max_depth", 5))
        stale_days = int(payload.get("stale_days", 30))
        if not 1 <= max_depth <= 12:
            raise HarborError("扫描深度必须在 1 到 12 之间")
        if not 1 <= stale_days <= 3650:
            raise HarborError("陈旧天数必须在 1 到 3650 天之间")
        self.config = {**self.config, "roots": roots, "max_depth": max_depth, "stale_days": stale_days}
        write_config(self.config)
        return self.scan()

    @staticmethod
    def app_available(name: str) -> bool:
        return any(path.is_dir() for path in (
            Path("/Applications") / f"{name}.app",
            Path.home() / "Applications" / f"{name}.app",
        ))

    @staticmethod
    def editor_path(candidate: Dict[str, object]) -> Optional[Path]:
        for app_name in candidate["app_names"]:
            for path in (Path("/Applications") / f"{app_name}.app", Path.home() / "Applications" / f"{app_name}.app"):
                if path.is_dir():
                    return path
        for bundle_id in candidate["bundle_ids"]:
            result = run(("/usr/bin/mdfind", f"kMDItemCFBundleIdentifier == '{bundle_id}'"), timeout=5)
            if result.code == 0:
                for raw_path in result.stdout.splitlines():
                    path = Path(raw_path)
                    if path.suffix == ".app" and path.is_dir():
                        return path
        return None

    def detected_editors(self) -> List[Dict[str, str]]:
        editors = []
        for candidate in EDITOR_CANDIDATES:
            path = self.editor_path(candidate)
            if path:
                editors.append({"id": str(candidate["id"]), "label": str(candidate["label"]), "app_name": path.stem, "path": str(path)})
        return editors

    def preferred_editor(self, editors: Optional[List[Dict[str, str]]] = None, repo_id: str = "") -> Optional[Dict[str, str]]:
        editors = self.detected_editors() if editors is None else editors
        repo_editors = self.config.get("repo_editors", {})
        preferred = str(repo_editors.get(repo_id, "")) if repo_id and isinstance(repo_editors, dict) else ""
        if not preferred:
            preferred = str(self.config.get("editor", ""))
        if preferred:
            configured = next((editor for editor in editors if editor["id"] == preferred), None)
            if configured:
                return configured
        return editors[0] if editors else None

    def set_repo_editor(self, repo_id: str, editor_id: str) -> Dict[str, object]:
        self.repo(repo_id)
        editors = self.detected_editors()
        editor = next((item for item in editors if item["id"] == editor_id), None)
        if not editor:
            raise HarborError("所选编辑器未安装或当前不可用")
        configured = self.config.get("repo_editors", {})
        repo_editors = dict(configured) if isinstance(configured, dict) else {}
        repo_editors[repo_id] = editor_id
        self.config = {**self.config, "repo_editors": repo_editors}
        write_config(self.config)
        return {"message": f"该仓库将使用 {editor['label']}", "state": self.scan()}

    @staticmethod
    def command_status(name: str) -> Tuple[bool, str]:
        try:
            executable = Harbor.command_path(name)
        except HarborError:
            return False, "未安装"
        result = run((executable, "--version"), timeout=8)
        detail = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else executable
        return result.code == 0, detail

    def environment_status(self) -> Dict[str, object]:
        git_ok, git_detail = self.command_status("git")
        node_ok, node_detail = self.command_status("node")
        pnpm_ok, pnpm_detail = self.command_status("pnpm")
        npm_ok, npm_detail = self.command_status("npm")
        detected_editors = self.detected_editors()
        default_editor = self.preferred_editor(detected_editors)
        try:
            wechat_path = self.wechat_cli_path()
            wechat_ok, wechat_detail = True, str(Path(wechat_path).parents[2].name)
        except HarborError:
            wechat_ok, wechat_detail = False, "未安装"
        package_ok = pnpm_ok or npm_ok
        package_detail = " · ".join(part for part in (
            f"pnpm {pnpm_detail}" if pnpm_ok else "",
            f"npm {npm_detail}" if npm_ok else "",
        ) if part) or "未安装 pnpm/npm"
        items = [
            {"key": "git", "label": "Git", "description": "扫描和管理工作树", "required": True, "available": git_ok, "detail": git_detail},
        ]
        detected_by_id = {editor["id"]: editor for editor in detected_editors}
        for candidate in EDITOR_CANDIDATES:
            editor = detected_by_id.get(str(candidate["id"]))
            is_default = bool(editor and default_editor and editor["id"] == default_editor["id"])
            items.append({
                "key": f"editor_{candidate['id']}", "label": candidate["label"], "description": "默认编辑器" if is_default else "可用于打开工作目录",
                "required": False, "available": bool(editor), "detail": "已安装 · 默认" if is_default else "已安装" if editor else "未安装",
            })
        items.extend([
            {"key": "node", "label": "Node.js", "description": "运行 Web 和小程序构建", "required": False, "available": node_ok, "detail": node_detail},
            {"key": "package", "label": "包管理器", "description": "执行项目中的测试/生产脚本", "required": False, "available": package_ok, "detail": package_detail},
            {"key": "wechat", "label": "微信开发者工具", "description": "打开小程序测试/生产产物", "required": False, "available": wechat_ok, "detail": wechat_detail},
        ])
        return {"ready": git_ok, "items": items}

    def complete_onboarding(self) -> Dict[str, object]:
        self.config = {**self.config, "onboarding_version": 1}
        write_config(self.config)
        return {"message": "环境检测已完成"}

    def import_root(self, raw_path: str) -> Dict[str, object]:
        path = clean_path(raw_path)
        if not path.is_dir():
            raise HarborError(f"目录不存在：{path}")
        for root in self.roots():
            try:
                path.relative_to(root)
                return {"message": f"{path.name} 已在扫描范围内", "state": self.scan()}
            except ValueError:
                continue
        roots = [str(root) for root in self.roots()]
        roots.append(str(path))
        self.config = {**self.config, "roots": roots}
        write_config(self.config)
        return {"message": f"已导入目录：{path}", "state": self.scan()}

    @staticmethod
    def choose_import_directory() -> Optional[Path]:
        result = run((
            "osascript",
            "-e", 'set selectedFolder to choose folder with prompt "选择要导入的 Git 项目或工作区目录"',
            "-e", "POSIX path of selectedFolder",
        ), timeout=3600)
        if result.code != 0:
            if "User canceled" in result.stderr or "-128" in result.stderr:
                return None
            raise HarborError(result.stderr or "无法打开文件夹选择器")
        return clean_path(result.stdout)

    def repo(self, repo_id: str) -> Path:
        with self.lock:
            repo = self.repo_paths.get(repo_id)
        if not repo:
            self.scan()
            with self.lock:
                repo = self.repo_paths.get(repo_id)
        if not repo:
            raise HarborError("仓库已不可用，请刷新后重试")
        return repo

    @staticmethod
    def registered_worktree(repo: Path, raw_path: str) -> Path:
        target = clean_path(raw_path)
        listing = git(repo, "worktree", "list", "--porcelain", check=True)
        registered = {clean_path(str(row.get("worktree"))) for row in parse_worktree_porcelain(listing.stdout)}
        if target not in registered:
            raise HarborError("目标目录不是该仓库登记的工作树")
        return target

    @staticmethod
    def command_path(name: str) -> str:
        located = shutil.which(name)
        if located:
            return located
        candidates = [
            Path("/usr/local/bin") / name,
            Path("/opt/homebrew/bin") / name,
            Path.home() / ".local" / "bin" / name,
            Path.home() / ".local" / "share" / "pnpm" / name,
        ]
        if name in {"pnpm", "npm", "node"}:
            candidates.extend(sorted((Path.home() / ".nvm" / "versions" / "node").glob(f"*/bin/{name}"), reverse=True))
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise HarborError(f"缺少所需命令：{name}")

    @staticmethod
    def wechat_cli_path() -> str:
        candidates = [
            Path("/Applications/wechatwebdevtools.app/Contents/MacOS/cli"),
            Path.home() / "Applications" / "wechatwebdevtools.app" / "Contents" / "MacOS" / "cli",
        ]
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise HarborError("未找到微信开发者工具，请先安装后再试")

    def open_wechat_project(self, target: Path, project: Path, environment_label: str) -> str:
        executable = self.wechat_cli_path()
        with self.lock:
            previous_project = self.wechat_project
        replaced = previous_project is not None and previous_project != project
        if replaced:
            # Close only the project opened through Harbor. Keep WeChat DevTools running.
            run((executable, "close", "--project", str(previous_project)), timeout=30)
        result = run((executable, "open", "--project", str(project)), timeout=30)
        if result.code != 0:
            raise HarborError(result.stderr or result.stdout or "无法打开微信开发者工具")
        with self.lock:
            self.wechat_project = project
        verb = "替换为" if replaced else "打开"
        return f"已在微信开发者工具{verb}{environment_label}环境：{project.relative_to(target)}"

    @staticmethod
    def borrow_main_dependencies(target: Path, main_root: Path) -> Optional[Path]:
        """Link the main checkout's dependencies into a linked worktree when needed."""
        dependency_link = target / "node_modules"
        if dependency_link.is_dir():
            return None
        if dependency_link.exists() or dependency_link.is_symlink():
            raise HarborError(f"当前工作树的 node_modules 不可用：{dependency_link}")
        main_dependencies = main_root / "node_modules"
        if not main_dependencies.is_dir():
            raise HarborError("当前工作树没有依赖，且仓库主目录也没有可复用的 node_modules")
        if target.resolve() == main_root.resolve():
            raise HarborError("仓库主目录尚未安装依赖")
        dependency_link.symlink_to(main_dependencies.resolve(), target_is_directory=True)
        return dependency_link

    @staticmethod
    def release_borrowed_dependencies(dependency_link: Optional[Path]) -> None:
        if dependency_link and dependency_link.name == "node_modules" and dependency_link.is_symlink():
            dependency_link.unlink()

    def ensure_miniapp_project(self, target: Path, environment: str, main_root: Optional[Path] = None) -> Tuple[Path, bool]:
        project = miniapp_project_for(target, environment)
        if project is not None:
            return project, False
        environment_label = "生产" if environment == "production" else "测试"
        script = "build:mp-weixin" if environment == "production" else "build:mp-weixin-test"
        scripts = package_scripts(target)
        if script not in scripts:
            raise HarborError(f"当前项目没有 {script} 脚本，无法生成{environment_label}环境产物")
        borrowed_dependencies = self.borrow_main_dependencies(target, main_root or target)
        manager_name = "pnpm" if (target / "pnpm-lock.yaml").is_file() else "npm"
        manager = self.command_path(manager_name)
        try:
            result = run((manager, "run", script), cwd=target, timeout=300)
            if result.code != 0:
                detail = (result.stderr or result.stdout or "未知错误")[-1200:]
                raise HarborError(f"{environment_label}环境构建失败：{detail}")
            project = miniapp_project_for(target, environment)
            if project is None:
                raise HarborError(f"{script} 已执行，但没有生成预期的{environment_label}环境产物")
            return project, True
        finally:
            self.release_borrowed_dependencies(borrowed_dependencies)

    def open_web_preview(self, target: Path, environment: str = "test", main_root: Optional[Path] = None) -> str:
        environment_label = "生产" if environment == "production" else "测试"
        script, script_args = web_preview_script(target, environment)
        with self.lock:
            existing = self.web_preview
        if existing and existing[0].poll() is None and existing[2] == target and existing[3] == environment:
            url = existing[1]
            result = run(("open", url))
            if result.code != 0:
                raise HarborError(result.stderr or "无法打开浏览器")
            return f"已重新打开{environment_label}环境网页：{url}"

        replacing_live_preview = bool(existing and existing[0].poll() is None)
        reused_port = None
        if existing:
            reused_port = urlparse(existing[1]).port
            self.stop_preview_process(existing[0])
            self.release_borrowed_dependencies(existing[4])
            with self.lock:
                if self.web_preview == existing:
                    self.web_preview = None

        borrowed_dependencies = self.borrow_main_dependencies(target, main_root or target)
        manager_name = "pnpm" if (target / "pnpm-lock.yaml").is_file() else "npm"
        manager = self.command_path(manager_name)
        if reused_port is None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                port = int(listener.getsockname()[1])
        else:
            port = reused_port
        command = [manager, "run", script]
        if manager_name == "npm":
            command.append("--")
        command.extend(script_args)
        command.extend(["--host", "127.0.0.1", "--port", str(port)])
        try:
            process = subprocess.Popen(
                command,
                cwd=str(target),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env={**os.environ, "BROWSER": "none"},
            )
        except Exception:
            self.release_borrowed_dependencies(borrowed_dependencies)
            raise
        url = f"http://127.0.0.1:{port}/"
        with self.lock:
            self.web_preview = (process, url, target, environment, borrowed_dependencies)
        deadline = time.time() + 15
        while time.time() < deadline:
            if process.poll() is not None:
                with self.lock:
                    if self.web_preview and self.web_preview[0] is process:
                        self.web_preview = None
                self.release_borrowed_dependencies(borrowed_dependencies)
                raise HarborError(f"{environment_label}环境网页启动失败，请在编辑器终端运行 pnpm run {script} 查看具体错误")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    break
            except OSError:
                time.sleep(0.15)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            with self.lock:
                if self.web_preview and self.web_preview[0] is process:
                    self.web_preview = None
            self.release_borrowed_dependencies(borrowed_dependencies)
            raise HarborError(f"{environment_label}环境网页启动超时，请在编辑器终端运行 pnpm run {script} 检查")
        if not replacing_live_preview:
            result = run(("open", url))
            if result.code != 0:
                raise HarborError(result.stderr or "无法打开浏览器")
        if replacing_live_preview:
            return f"已用当前工作树的{environment_label}环境替换上一个网页预览：{url}"
        return f"已启动当前工作树的{environment_label}环境网页：{url}"

    @staticmethod
    def stop_preview_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

    def close(self) -> None:
        """Stop only the Web preview server launched by this Harbor process."""
        with self.lock:
            preview = self.web_preview
            self.web_preview = None
        if preview:
            self.stop_preview_process(preview[0])
            self.release_borrowed_dependencies(preview[4])

    def action(self, payload: Dict[str, object]) -> Dict[str, object]:
        action = str(payload.get("action", ""))
        if action == "check_environment":
            return {"message": "环境检测完成", "environment": self.environment_status()}
        if action == "complete_onboarding":
            return self.complete_onboarding()
        if action == "set_repo_editor":
            return self.set_repo_editor(str(payload.get("repo_id", "")), str(payload.get("editor_id", "")))
        if action == "import_directory":
            selected = self.choose_import_directory()
            if selected is None:
                return {"message": "已取消导入"}
            return self.import_root(str(selected))
        if action == "remove_many":
            items = payload.get("items")
            if not isinstance(items, list) or not items or len(items) > 200:
                raise HarborError("请选择 1 到 200 个工作树")
            if payload.get("confirm") != f"REMOVE_{len(items)}":
                raise HarborError("批量删除确认信息无效")
            prepared = []
            for item in items:
                if not isinstance(item, dict):
                    raise HarborError("工作树选择信息无效")
                item_repo = self.repo(str(item.get("repo_id", "")))
                target = self.registered_worktree(item_repo, str(item.get("path", "")))
                listing = parse_worktree_porcelain(git(item_repo, "worktree", "list", "--porcelain", check=True).stdout)
                if listing and clean_path(str(listing[0].get("worktree"))) == target:
                    raise HarborError(f"仓库主目录不能删除：{target}")
                row = next((entry for entry in listing if clean_path(str(entry.get("worktree"))) == target), {})
                target_state = status_for(target) if target.is_dir() else {"available": False, "dirty": False}
                prepared.append((item_repo, target, row, target_state))

            removed = 0
            pruned_repos = set()
            for item_repo, target, row, target_state in prepared:
                if not target.is_dir():
                    pruned_repos.add(item_repo)
                    continue
                if row.get("locked"):
                    git(item_repo, "worktree", "unlock", str(target), check=True)
                args = ["worktree", "remove", "--force", str(target)]
                git(item_repo, *args, check=True, timeout=60)
                removed += 1
            for item_repo in pruned_repos:
                git(item_repo, "worktree", "prune", "--verbose", check=True)
            pruned = len(prepared) - removed
            detail = f"，并清理 {pruned} 条失效记录" if pruned else ""
            return {"message": f"已删除 {removed} 个工作树{detail}；分支均已保留"}

        repo = self.repo(str(payload.get("repo_id", "")))
        if action in {"open_cursor", "open_editor"}:
            target = self.registered_worktree(repo, str(payload.get("path", "")))
            editor = self.preferred_editor(repo_id=str(payload.get("repo_id", "")))
            if not editor:
                raise HarborError("未找到可用编辑器，请安装 Cursor、Visual Studio Code、WebStorm 或 IntelliJ IDEA")
            result = run(("open", "-a", editor["app_name"], str(target)))
            if result.code != 0:
                raise HarborError(result.stderr or "无法在编辑器中打开")
            return {"message": f"已使用 {editor['label']} 打开 {target.name}"}
        if action == "open_folder":
            target = self.registered_worktree(repo, str(payload.get("path", "")))
            result = run(("open", str(target)))
            if result.code != 0:
                raise HarborError(result.stderr or "无法打开工作树目录")
            return {"message": f"已打开 {target.name} 文件夹"}
        if action in {"open_wechat", "open_wechat_test", "open_wechat_production"}:
            target = self.registered_worktree(repo, str(payload.get("path", "")))
            environment = "production" if action == "open_wechat_production" else "test"
            environment_label = "生产" if environment == "production" else "测试"
            project, built = self.ensure_miniapp_project(target, environment, repo)
            message = self.open_wechat_project(target, project, environment_label)
            return {"message": f"已自动构建；{message}" if built else message}
        if action in {"open_web", "open_web_test", "open_web_production"}:
            target = self.registered_worktree(repo, str(payload.get("path", "")))
            environment = "production" if action == "open_web_production" else "test"
            return {"message": self.open_web_preview(target, environment, repo)}
        if action == "reveal":
            target = self.registered_worktree(repo, str(payload.get("path", "")))
            result = run(("open", "-R", str(target)))
            if result.code != 0:
                raise HarborError(result.stderr or "无法在访达中显示目录")
            return {"message": "已在访达中显示工作树目录"}
        if action == "open_terminal":
            target = self.registered_worktree(repo, str(payload.get("path", "")))
            result = run(("open", "-a", "Terminal", str(target)))
            if result.code != 0:
                raise HarborError(result.stderr or "无法打开终端")
            return {"message": f"已在 {target.name} 目录打开终端"}
        if action in {"lock", "unlock"}:
            target = self.registered_worktree(repo, str(payload.get("path", "")))
            args = ["worktree", action]
            if action == "lock" and payload.get("reason"):
                args.extend(["--reason", str(payload["reason"])])
            args.append(str(target))
            git(repo, *args, check=True)
            return {"message": "工作树已锁定" if action == "lock" else "工作树已解锁", "state": self.scan()}
        if action == "remove":
            target = self.registered_worktree(repo, str(payload.get("path", "")))
            listing = parse_worktree_porcelain(git(repo, "worktree", "list", "--porcelain", check=True).stdout)
            if listing and clean_path(str(listing[0].get("worktree"))) == target:
                raise HarborError("仓库主目录不能删除")
            if str(payload.get("confirm", "")) != target.name:
                raise HarborError(f"请输入 {target.name} 确认删除")
            if not target.is_dir():
                raise HarborError("目录已经不存在，请改用“清理记录”")
            state = status_for(target)
            row = next((item for item in listing if clean_path(str(item.get("worktree"))) == target), {})
            if row.get("locked"):
                git(repo, "worktree", "unlock", str(target), check=True)
            args = ["worktree", "remove", "--force", str(target)]
            git(repo, *args, check=True, timeout=30)
            message = "已强制删除工作树；分支仍保留" if state.get("dirty") else "已删除干净的工作树；分支仍保留"
            return {"message": message}
        if action == "prune_preview":
            result = git(repo, "worktree", "prune", "--dry-run", "--verbose", check=True)
            return {"message": result.stdout or "没有发现失效记录", "preview": result.stdout}
        if action == "prune":
            if payload.get("confirm") != "PRUNE":
                raise HarborError("请输入确认文字后再清理记录")
            preview = git(repo, "worktree", "prune", "--dry-run", "--verbose", check=True).stdout
            if not preview:
                return {"message": "没有发现失效记录", "state": self.scan()}
            git(repo, "worktree", "prune", "--verbose", check=True)
            return {"message": "已清理失效的工作树记录", "state": self.scan()}
        if action == "create":
            branch = str(payload.get("branch", "")).strip()
            base = str(payload.get("base", "")).strip()
            if not branch or not BRANCH_RE.match(branch):
                raise HarborError("分支名为空或格式无效")
            existing = git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").code == 0
            main_root = Path(git(repo, "worktree", "list", "--porcelain", check=True).stdout.splitlines()[0][9:])
            default_parent = main_root.parent / ".worktrees" / main_root.name
            raw_destination = str(payload.get("destination", "")).strip()
            destination = clean_path(raw_destination) if raw_destination else default_parent / branch.replace("/", "-")
            if destination.exists():
                raise HarborError(f"目标目录已存在：{destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if existing:
                git(repo, "worktree", "add", str(destination), branch, check=True, timeout=60)
            else:
                if not base:
                    raise HarborError("新建分支必须填写基准引用")
                if git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").code != 0:
                    raise HarborError(f"本地无法解析基准引用：{base}")
                git(repo, "worktree", "add", "-b", branch, str(destination), base, check=True, timeout=60)
            return {"message": f"已在 {destination} 创建工作树", "state": self.scan()}
        raise HarborError("无法识别该操作")


MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


def make_handler(harbor: Harbor, csrf_token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WorktreeHarbor/1.0"

        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

        def send_json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/state":
                try:
                    self.send_json({"ok": True, "csrf": csrf_token, "state": harbor.scan()})
                except HarborError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 500)
                return
            relative = "index.html" if path == "/" else path.lstrip("/")
            candidate = (STATIC_DIR / relative).resolve()
            if STATIC_DIR.resolve() not in candidate.parents and candidate != STATIC_DIR.resolve():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not candidate.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(candidate.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.headers.get("X-Harbor-Token") != csrf_token:
                self.send_json({"ok": False, "error": "请求验证信息无效"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1_000_000:
                    raise HarborError("请求内容过大")
                payload = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/config":
                    state = harbor.set_config(payload)
                    self.send_json({"ok": True, "message": "设置已保存", "state": state})
                elif path == "/api/action":
                    self.send_json({"ok": True, **harbor.action(payload)})
                else:
                    self.send_json({"ok": False, "error": "未找到请求内容"}, 404)
            except (HarborError, ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地工作树管理面板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", action="append", help="扫描根目录，可重复指定")
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = read_config()
    if args.root:
        config["roots"] = [str(clean_path(value)) for value in args.root]
    harbor = Harbor(config)
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(harbor, token))
    actual_port = int(server.server_address[1])
    url = f"http://{args.host}:{actual_port}"
    print(f"工作树管家正在运行：{url}")
    print("按 Ctrl+C 停止。")
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止工作树管家。")
    finally:
        harbor.close()
        server.server_close()


if __name__ == "__main__":
    main()
