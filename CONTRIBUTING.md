# 贡献指南

感谢参与 Worktree Hub。

## 开发环境

- Python 3
- Git
- Node.js（仅用于检查前端 JavaScript 语法）
- Xcode Command Line Tools（仅在构建 macOS 应用时需要）

项目没有第三方 Python 运行时依赖。

## 提交修改

1. Fork 仓库并创建独立分支。
2. 保持修改范围清晰，避免提交本机配置、真实项目路径、账号或密钥。
3. 为行为变化补充测试。
4. 在提交前执行：

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
python3 -m py_compile worktree_harbor.py
```

## 隐私要求

Issue、截图、测试夹具和日志中请使用虚构路径，例如 `/Users/example/projects`。提交前请删除：

- 真实用户名和绝对路径
- 私有仓库、分支和提交信息
- `.env`、令牌、Cookie、SSH 密钥及内部地址
- `.data/config.json` 和构建生成的 `.app`

## 产品原则

- 默认本地运行，不新增遥测或云端依赖
- 不在用户未明确操作时执行 Git 网络命令
- 破坏性操作必须展示清晰风险并进行确认
- 主目录与 linked worktree 必须明确区分
- 界面文案优先使用可验证事实，避免把推测显示为确定结果
