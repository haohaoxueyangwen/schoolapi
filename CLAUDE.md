# Claude Code 工作环境说明

## 工作空间

GenAI 代理链相关代码、脚本、配置集中在 `~/vscodespace/genai-stack/`：

```
~/vscodespace/genai-stack/
├── switch-model.sh          # 切换/管理脚本（symlink → ~/.local/bin/）
├── CLAUDE.md                # 本文件
├── CHANGES.md               # 变更日志
├── run.py                   # TUI 启动入口
├── pyproject.toml           # uv 项目定义（textual + requests）
├── proxies/
│   ├── genai2api/           # JWT token 代理（Flask+uv，:5000，Anthropic 原生）
│   └── SHTUClaudeProxy/     # API key 代理（纯 stdlib，:8082，Anthropic 原生）
└── tui/                     # 文字界面（Textual TUI）
    ├── app.py               # 主应用入口
    ├── config.py             # Profile CRUD + 配置写入
    ├── models.py             # 模型列表（静态 + 动态拉取 + 按后端智能匹配）
    ├── proxy.py              # 代理进程管理
    ├── token_utils.py        # JWT 工具（过期检测）
    ├── screens/              # 弹窗（Profile 表单、Token 输入）
    ├── widgets/              # 控件（ProfileList、ProfileDetail、ProxyStatus、ModelSelect）
    └── styles/               # TCSS 样式
```

## 架构（三模式）

GenAI 平台有两个入口，加上 Anthropic 官方，共三种模式。
每个代理都直接暴露 Anthropic `/v1/messages` 端点，**单层代理** — 不再需要双代理链。

```
anthropic (API 直连):
  Claude Code ──→ sssaicode 中转 ──→ Anthropic API

genai-api (API key 模式):
  Claude Code ──Anthropic──→ SHTUClaudeProxy(:8082) ──Responses──→ genaiapi.shanghaitech.edu.cn/api/v1/response

genai-token (JWT 模式):
  Claude Code ──Anthropic──→ genai2api(:5000) ──GenAI SSE──→ genai.shanghaitech.edu.cn/htk
```

| 模式 | 后端 | 认证 | 代理 | 说明 |
|------|------|------|------|------|
| anthropic | Anthropic (sssaicode 中转) | AUTH_TOKEN (sk-...) | 无 | 直连 |
| genai-api | genaiapi.shanghaitech.edu.cn | API key | SHTUClaudeProxy(:8082) | OpenAI Responses 格式 |
| genai-token | genai.shanghaitech.edu.cn/htk | JWT token | genai2api(:5000) | 原生 Anthropic 格式 |

关键变化（v6）：
- 两个代理都**原生说 Anthropic Messages API**，Claude Code 直连即可
- 统一用 `ANTHROPIC_AUTH_TOKEN=local-proxy`，不再需要 `ANTHROPIC_API_KEY=dummy`
- SHTUClaudeProxy 用 `responses` API 格式（GPT 系列模型要求），endpoint `/api/v1/response`
- genai2api 模型名三级解析：Anthropic 前缀→映射 / 合法 GenAI→透传 / 未知→fallback genai-model.txt
- genai-model.txt 每次请求读取，支持热切换模型无需重启

## 启动方式

推荐用 `ccl`（cc-launch）一键启动，交互菜单集成 profile 切换、代理管理、token 更新：

```bash
ccl              # 交互菜单
ccl claude       # 直达 claude profile
ccl myapi        # 直达 genai-api profile + 自动启代理
```

脚本位置: `~/.local/bin/cc-launch` (alias `ccl`，定义在 `~/.bashrc`)

## TUI 交互界面

`claudeswitch`（alias，定义在 `~/.bashrc`）启动图形化管理面板：

```bash
claudeswitch        # 等价于 cd ~/vscodespace/genai-stack && uv run python run.py
```

- 左侧：Profile 列表，Enter 键激活切换
- 右侧上方：Profile 详情（类型、URL、认证、模型）
- 右侧中部：代理状态（SHTUClaudeProxy / genai2api 健康检查，5s 自动刷新）
- 右侧下方：模型选择（根据 profile 后端自动匹配可用模型列表）

模型列表智能匹配：
- anthropic + sssaicode → 显示 Claude + GPT 系列（动态拉取 /v1/models）
- anthropic + deepseek → 显示 DeepSeek 模型
- genai / genai-api → 显示 GenAI 平台模型（代理运行时动态拉取）
- anthropic 无 custom model → readonly，由 Claude Code 管理

快捷键：N 新建 | E 编辑 | D 删除 | T 更新 Token | M 刷新模型 | S 启动 | R 重启 | X 停止 | Q 退出

启动前自动 `unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL`，杜绝 Auth conflict。

## 模型切换

`switch-model.sh` 已加入 PATH，任意目录可直接调用。**推荐用 `sm` wrapper**（自动 eval + 清理 shell env 残留，见 `~/.bashrc`）：

```bash
sm use myapi        # 切到 genai-api 模式
sm use claude       # 切回 Anthropic 直连
sm model GPT-5.5    # 热切换模型
sm status           # 查看 settings + 代理状态 + shell env
sm list             # 查看可用模型
sm start            # 启动当前 profile 对应代理
sm stop             # 停止所有代理
sm restart          # 重启当前代理
```

## GenAI 代理完整工作流

### genai-api 模式（API key）

```bash
# 1. 创建 profile（已有则跳过）
sm add myapi genai-api https://genaiapi.shanghaitech.edu.cn/api/v1 <API_KEY> GPT-5.4

# 2. 切换 + 自动启代理
sm use myapi

# 3. 日常使用
sm model deepseek-pro      # 切模型（自动重启 SHTUClaudeProxy）
sm token myapi <新API_KEY>  # 更新 API key
sm restart                  # 重启代理
```

### genai-token 模式（JWT）

```bash
# 1. 保存 token（从浏览器 genai.shanghaitech.edu.cn 抓取 X-Access-Token）
sm token genai <JWT_TOKEN>

# 2. 切换
sm use genai

# 3. Token 过期处理
# token 保存在 ~/.claude/genai-token.txt
# genai2api 支持热更新 — 更新文件即生效，无需重启代理
sm token genai <新JWT_TOKEN>
```

## 已知问题与解决

| 问题 | 原因 | 解决 |
|------|------|------|
| `switch-model.sh: command not found` | symlink 失效 | `ln -sfn ~/vscodespace/genai-stack/switch-model.sh ~/.local/bin/switch-model.sh` |
| `Auth conflict: both token and API key` | shell env 残留 | 用 `ccl` 启动（自动 unset），或 `sm use <profile>` |
| Claude Code 无响应 | 代理未启动 | `sm start` 启动对应代理 |
| Token 过期 | JWT exp 到期 | `sm token genai <新JWT>` |
| 思考后无回复（deltas=0） | API 格式错误 | SHTUClaudeProxy 必须用 `responses` 格式，非 `chat_completions` |
| API 额度上限 | 后端限额 | 换模型或等待配额恢复 |

## settings.json 正确状态

**anthropic 模式**（直连）：
- `ANTHROPIC_BASE_URL`: `https://node-hk.sssaicode.com/api`
- `ANTHROPIC_AUTH_TOKEN`: `sk-sssaicode-...`
- 无 `ANTHROPIC_API_KEY`

**genai-api 模式**（SHTUClaudeProxy）：
- `ANTHROPIC_BASE_URL`: `http://localhost:8082`
- `ANTHROPIC_AUTH_TOKEN`: `local-proxy`
- 无 `ANTHROPIC_API_KEY`
- SHTUClaudeProxy config: `~/.config/SHTUClaudeProxy/config.json`

**genai-token 模式**（genai2api）：
- `ANTHROPIC_BASE_URL`: `http://localhost:5000`
- `ANTHROPIC_AUTH_TOKEN`: `local-proxy`
- 无 `ANTHROPIC_API_KEY`
- JWT token: `~/.claude/genai-token.txt`

## 文件位置

| 文件 | 用途 |
|------|------|
| `~/.claude/genai-token.txt` | GenAI JWT token 或 学号@密码（热更新） |
| `~/.claude/genai-model.txt` | 当前 GenAI 模型名（每次请求读取，热切换） |
| `~/.claude/profiles/*.json` | Profile 配置 |
| `~/.claude/active-profile` | 当前激活 profile 名 |
| `~/.claude/settings.json` | Claude Code 运行时 env |
| `~/.config/SHTUClaudeProxy/config.json` | SHTUClaudeProxy 代理配置 |
| `~/.claude/genai-stack/logs/` | 代理运行日志 |
| `~/vscodespace/genai-stack/proxies/SHTUClaudeProxy/cli.py` | SHTUClaudeProxy 入口 |
| `~/vscodespace/genai-stack/proxies/genai2api/main.py` | genai2api 入口 |
