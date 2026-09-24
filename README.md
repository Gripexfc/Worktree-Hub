# Worktree Hub

一个本地优先的 Git worktree 桌面管理工具。它把多个工作区中的仓库、主目录和 linked worktree 集中展示，并提供编辑器打开、项目预览和安全清理能力。

> 默认不收集、不上传仓库名称、文件路径、分支、提交记录或使用数据。程序没有遥测、登录、云端同步和自动更新检查。

## 适用场景

- 同时维护多个前端、后端或小程序仓库
- 一个仓库包含多个 Git worktree，需要快速确认各目录当前占用的分支
- 希望区分近期未提交、长期搁置、已提交和失效目录
- 为不同仓库选择 Cursor、VS Code、WebStorm 或 IntelliJ IDEA
- 删除 worktree 前确认本地修改风险，但保留对应 Git 分支

## 功能概览

- 按“工作区 → 仓库 → worktree”分层展示，仓库主目录固定在首位
- 显示当前分支、创建来源参考、最近提交、本地修改和合入状态
- 自动检测 Cursor、Visual Studio Code、WebStorm、IntelliJ IDEA；每个仓库可单独选择默认编辑器
- 支持导入任意本地目录，不依赖预设项目路径
- 支持 Web 与微信小程序的测试/生产预览入口
- worktree 缺少依赖时，可临时复用同仓库主目录的 `node_modules`
- 支持搜索、跨仓库多选和批量清理
- 删除前明确提示本地修改风险；不创建备份，Git 分支仍会保留
- 首次启动检查 Git、编辑器、Node.js、包管理器和微信开发者工具

## 隐私与本地运行

Worktree Hub 的界面服务仅监听 `127.0.0.1`，修改请求需要当前进程生成的随机令牌。工具自身不会把扫描结果发送到互联网，也不会执行 `fetch`、`pull`、`push`、`merge` 或 `rebase`。

需要注意：当你主动点击 Web、小程序或编辑器入口时，Worktree Hub 会启动对应的本地第三方软件或项目脚本。这些软件和脚本是否联网由它们自己的实现与配置决定，不属于 Worktree Hub 的数据传输。完整说明见 [隐私说明](docs/PRIVACY.md)。

## 下载安装包

打开 [GitHub Releases](https://github.com/Gripexfc/Worktree-Hub/releases) 下载对应系统版本：

- `Worktree-Hub-Windows-x64-Setup.exe`：Windows 10/11 64 位
- `Worktree-Hub-macOS-arm64.dmg`：Apple 芯片 Mac
- `Worktree-Hub-macOS-x64.dmg`：Intel Mac

Windows 安装版和 macOS 安装版均已包含 Python 运行时，不需要单独安装 Python。当前安装包未购买商业代码签名证书；Windows SmartScreen 或 macOS Gatekeeper 可能显示来源提示，请只从本仓库 Release 页面下载。

## 系统要求

原生桌面版目前面向 macOS：

- macOS 13 或更高版本
- Git
- 从源码运行时需要 Python 3
- 自行构建 `.app` 时需要 Xcode Command Line Tools

以下工具均为可选：

- Cursor、Visual Studio Code、WebStorm、IntelliJ IDEA
- Node.js 与 pnpm/npm（运行项目预览时使用）
- 微信开发者工具（打开小程序产物时使用）

## 从源码运行

```bash
git clone https://github.com/Gripexfc/Worktree-Hub.git
cd Worktree-Hub
python3 worktree_harbor.py
```

浏览器会打开本地页面。也可以指定扫描目录：

```bash
python3 worktree_harbor.py --root /path/to/projects --root /another/path
```

首次打开后，也可以直接点击“导入目录”选择工作区。

## 构建 macOS 应用

```bash
xcode-select --install  # 尚未安装命令行工具时执行
./build-macos-app.sh
open "../Worktree Hub.app"
```

构建脚本会在源码目录的上一级生成 `Worktree Hub.app`，并使用本机临时签名。Release 工作流会额外把 Python 后端打入应用并生成 DMG。临时签名适合测试分发；正式商用分发建议使用 Apple Developer ID 签名并公证。

## 配置位置

- macOS 应用：`~/Library/Application Support/Worktree Hub/config.json`
- Windows 应用：`%LOCALAPPDATA%\Worktree Hub\config.json`
- 直接运行源码：项目目录下的 `.data/config.json`

这些文件包含用户选择的扫描目录和仓库编辑器偏好，不应提交到 Git。仓库已通过 `.gitignore` 排除它们。

## 使用与安全边界

- 主目录不能作为普通 linked worktree 删除
- 有本地修改的 worktree 只有在风险确认后才会强制删除
- 删除不会备份文件；未提交和未跟踪文件将无法恢复
- 删除 worktree 不会删除对应 Git 分支
- ahead/behind 与合入判断基于本地 refs，工具不会联网刷新远端
- Git 不保证永久记录分支创建来源，因此界面中的来源信息只作为参考
- 小程序和 Web 预览只运行仓库已经提供的脚本，不自动安装依赖

## 开发与测试

项目不依赖第三方 Python 包。修改后运行：

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
python3 -m py_compile worktree_harbor.py
```

贡献代码前请阅读 [贡献指南](CONTRIBUTING.md) 和 [安全策略](SECURITY.md)。

## License

[MIT](LICENSE)
