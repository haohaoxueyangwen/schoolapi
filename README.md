# GenAI Stack — Claude Code 多模式代理

让 Claude Code 通过上海科技大学 GenAI 平台使用 GPT-5.5、DeepSeek、GLM 等模型，同时保留一键切回 Claude 官方的能力。

## 架构（三模式）

Claude Code 只会说 Anthropic Messages API 格式，两个代理各自翻译成不同后端格式：

```
anthropic (API 直连):
  Claude Code ──→ sssaicode 中转 ──→ Anthropic API

genai-api (API key 模式):
  Claude Code ──Anthropic──→ SHTUClaudeProxy(:8082) ──Responses──→ genaiapi.shanghaitech.edu.cn/api/v1/response

genai-token (学号密码/JWT 模式):
  Claude Code ──Anthropic──→ genai2api(:5000) ──GenAI SSE──→ genai.shanghaitech.edu.cn/htk
```

| 模式 | 后端 | 认证 | 代理 |
|------|------|------|------|
| anthropic | Anthropic (sssaicode 中转) | AUTH_TOKEN (sk-...) | 无 |
| genai-api | genaiapi.shanghaitech.edu.cn | API key | SHTUClaudeProxy(:8082) |
| genai-token | genai.shanghaitech.edu.cn/htk | JWT token 或 学号@密码 | genai2api(:5000) |

**组件职责：**

| 组件 | 项目 | 端口 | 职责 |
|------|------|------|------|
| SHTUClaudeProxy | `proxies/SHTUClaudeProxy/` | 8082 | Anthropic Messages → OpenAI Responses 翻译 |
| genai2api | `proxies/genai2api/` | 5000 | Anthropic Messages → GenAI HTK 翻译 + 模型名三级解析 |

## 目录结构

```
~/vscodespace/genai-stack/
├── switch-model.sh              # 统一管理脚本（已 symlink → ~/.local/bin/）
├── CLAUDE.md                    # Claude Code 工作指引
├── CHANGES.md                   # 开发变更日志
├── README.md                    # 本文件
├── run.py                       # TUI 启动入口
├── pyproject.toml               # uv 项目定义（textual + requests）
├── tui/                         # Textual TUI 图形管理面板
│   ├── app.py                   # 主应用（Profile 切换、模型切换、代理管理）
│   ├── config.py                # Profile CRUD + 配置文件读写
│   ├── models.py                # 模型列表（静态 + 动态拉取 + 按后端智能匹配）
│   ├── proxy.py                 # 代理进程管理（启停、健康检查）
│   ├── token_utils.py           # JWT 工具（过期检测、剩余时间）
│   ├── screens/                 # 弹窗（Profile 表单、Token 输入）
│   ├── widgets/                 # 控件（ProfileList、ProfileDetail、ProxyStatus、ModelSelect）
│   └── styles/                  # TCSS 样式
│
├── proxies/
│   ├── genai2api/               # genai-token 代理 — Anthropic → GenAI HTK
│   │   ├── main.py              # Flask 服务，端口 5000
│   │   ├── provider/            # 格式转换（anthropic.py 三级模型名解析）
│   │   ├── auth/                # CAS 自动登录 + JWT 管理
│   │   └── pyproject.toml       # uv 依赖定义
│   │
│   └── SHTUClaudeProxy/         # genai-api 代理 — Anthropic → OpenAI Responses
│       ├── cli.py               # CLI 入口 (serve/write-settings/show-config)
│       ├── proxy.py             # 纯 stdlib HTTP 代理 + 流式转发
│       ├── config_store.py      # 模型路由配置（find_model + model_env）
│       └── config.example.json  # 配置模板
│
└── claude-code-proxy/           # (旧版，已弃用)
```

## 关键配置文件

| 文件 | 用途 | 热更新 |
|------|------|--------|
| `~/.claude/settings.json` | Claude Code env 变量 (BASE_URL, AUTH_TOKEN 等) | 需重启 CC |
| `~/.claude/genai-token.txt` | GenAI JWT token 或 学号@密码 | ✓ genai2api 每次请求读 |
| `~/.claude/genai-model.txt` | 当前 GenAI 模型名 | ✓ genai2api 每次请求读 |
| `~/.claude/profiles/*.json` | Profile 配置 | — |
| `~/.config/SHTUClaudeProxy/config.json` | SHTUClaudeProxy 配置 | 需重启 |
| `~/.claude/genai-stack/logs/` | 代理运行日志 | — |

## 三种模式

### anthropic 模式（直连）
```
settings.json:
  ANTHROPIC_BASE_URL  = https://node-hk.sssaicode.com/api
  ANTHROPIC_AUTH_TOKEN = sk-sssaicode-...
  (无 ANTHROPIC_API_KEY)
```

### genai-api 模式（API key, 单层代理）
```
settings.json:
  ANTHROPIC_BASE_URL  = http://localhost:8082
  ANTHROPIC_AUTH_TOKEN = local-proxy
  (无 ANTHROPIC_API_KEY, 无 ANTHROPIC_MODEL)

SHTUClaudeProxy config.json:
  base_url = https://genaiapi.shanghaitech.edu.cn/api/v1/response
  api_key  = <实际 API key>
  upstream_model = GPT-5.5
```
SHTUClaudeProxy 直接翻译 Anthropic→OpenAI Responses 格式，不需要 genai2api。

### genai-token 模式（学号密码/JWT, 单层代理）
```
settings.json:
  ANTHROPIC_BASE_URL  = http://localhost:5000
  ANTHROPIC_AUTH_TOKEN = local-proxy
  (无 ANTHROPIC_API_KEY, 无 ANTHROPIC_MODEL)

genai-token.txt:
  学号@密码           (学号@密码格式，自动 CAS 登录)
  或 eyJ...            (JWT 格式，直接使用)

genai-model.txt:
  deepseek-pro        (当前模型，每次请求读取)
```
genai2api 直接翻译 Anthropic→GenAI HTK 格式。模型名三级解析：
1. Anthropic 前缀 (claude-*, anthropic-*) → 映射到 genai-model.txt
2. 合法 GenAI 模型名 → 透传
3. 未知模型名 (如残留的 deepseek-v4-pro) → fallback 到 genai-model.txt

---

## 快速开始

### 前提
- Python 3.11+, [uv](https://github.com/astral-sh/uv), jq, curl
- Claude Code 已安装
- 有 GenAI 平台账号（从 `genai.shanghaitech.edu.cn` 浏览器抓取 JWT token）

### 0. (推荐) 安装 shell wrapper

`~/.bashrc` 末尾追加（一次性设置，从此 `sm` / `switch-model.sh` 任何调用方式都自动清理 shell env，永不报 Auth conflict）：

```bash
# === GenAI Stack auth wrapper ===
sm() {
    local script="$HOME/.local/bin/switch-model.sh"
    [ ! -x "$script" ] && echo "switch-model.sh 不存在：$script" >&2 && return 1
    case "$1" in
        genai|claude|g|c|use|u) eval "$(command "$script" "$@")" ;;
        *) command "$script" "$@" ;;
    esac
}
switch-model.sh() { sm "$@"; }
alias switch-model='sm'
# === end GenAI Stack auth wrapper ===
```

```bash
source ~/.bashrc
```

安装后，所有示例中的 `eval "$(switch-model.sh ...)"` 可简写为 `sm genai GPT-5.5` / `sm claude`。

### 1. 保存 Token

```bash
# 方式一：学号密码（推荐，自动 CAS 登录刷新 token）
sm token genai 学号@你的密码

# 方式二：JWT token（从浏览器抓取）
sm token genai <JWT_TOKEN>
```

JWT 从浏览器 DevTools → Network → 任意请求的 `X-Access-Token` header 复制。

### 2. 切换到 GenAI 模式

```bash
sm use genai
# 或快捷方式：
sm genai deepseek-pro
```

自动启动 genai2api (:5000)，写入 settings.json。

### 3. 重启 Claude Code

切换模式后需重启 Claude Code 让新 env 生效。

---

## TUI 交互界面

除命令行外，还提供 Textual TUI 图形管理面板：

```bash
claudeswitch        # alias，定义在 ~/.bashrc
# 等价于：cd ~/vscodespace/genai-stack && uv run python run.py
```

`~/.bashrc` 中的 alias 定义：
```bash
alias claudeswitch="cd ~/vscodespace/genai-stack && uv run python run.py"
```

### 界面布局

```
┌─ Profiles ──┬── Profile 详情 ──────────────────┐
│ ★ claude    │  类型: Anthropic 直连             │
│   deepseek  │  URL:  node-hk.sssaicode.com/api │
│   genai     │  模型: claude-opus-4-7            │
│   genai-api ├── 代理状态 ──────────────────────┤
│   codex5.3  │  SHTUClaudeProxy :8082  ● 停止   │
│             │  genai2api       :5000  ● 运行中  │
│             ├── 模型选择 (Enter 切换) ─────────┤
│             │  ── Claude ──                     │
│             │  ▸ claude-opus-4-7                │
│             │    claude-sonnet-4-6              │
│             │  ── GPT ──                        │
│             │    gpt-5.5                        │
└─────────────┴──────────────────────────────────┘
```

### 快捷键

| 键 | 功能 |
|----|------|
| Enter | 激活选中的 Profile（切换模式） |
| N | 新建 Profile |
| E | 编辑 Profile |
| D | 删除 Profile |
| T | 更新 Token |
| M | 刷新模型列表 |
| S | 启动代理 |
| R | 重启代理 |
| X | 停止所有代理 |
| Q | 退出 |

### 模型列表智能匹配

模型选择面板根据所选 profile 的后端自动显示对应可用模型：

| Profile 后端 | 模型列表来源 |
|-------------|-------------|
| sssaicode (Anthropic 中转) | 动态拉取 /v1/models，fallback Claude + GPT 静态列表 |
| DeepSeek API | deepseek-v4-pro, deepseek-v4-flash |
| GenAI 平台 (genai/genai-api) | 代理运行时动态拉取，否则静态 GenAI 模型列表 |
| Anthropic 官方 (无 custom model) | readonly — 由 Claude Code 自行管理 |

---

## 日常使用

### switch-model.sh 命令速查

```
切换模式:
  sm use <name>                   切到指定 profile（按 type 自动启停代理）
  sm genai [model]                切到 genai profile（快捷方式）
  sm claude                       切回 Claude 官方（快捷方式）
  sm model <name>                 热切换 GenAI 模型（无需重启 Claude Code）

代理管理:
  sm start                        启动当前 profile 对应代理
  sm stop                         停止所有代理
  sm restart                      重启当前代理
  sm token <name> <JWT/密码>       保存/更新 token

查看信息:
  sm status                       settings + 代理 + shell env 状态
  sm list                         显示可用模型
  sm show [name]                  显示 profile 详情

Profile 管理（v3+，多账号/多后端）:
  switch-model.sh init              从现有 settings/token 文件迁移生成 profile
  switch-model.sh ls                列出所有 profile（★ = active）
  switch-model.sh use <name>        按 type 切换到指定 profile
  switch-model.sh add <type> <name> ...  新建 profile (anthropic|openai|genai)
  switch-model.sh rm <name>         删除 profile
  switch-model.sh show [name]       显示 profile 详情（脱敏 key；省略 name = active）
  switch-model.sh edit <name>       用 $EDITOR 编辑 profile JSON

短别名:
  g = genai,  c = claude,  m = model,  s = status
  l = list,   t = token,   r = restart,  u = use
```

### 典型工作流

**早上开工 → 用 GenAI 免费模型：**
```bash
sm use genai           # 切到 genai 模式（自动启动 genai2api）
# 重启 Claude Code
```

**需要精确任务 → 换模型：**
```bash
sm model deepseek-pro  # 热切换，不用重启任何东西
```

**需要最强推理 → 切 Claude 官方：**
```bash
sm use claude
# 重启 Claude Code
```

**Token 过期：**
```bash
sm token genai <新JWT或学号@密码>   # genai2api 自动热加载
```

---

## Profile 管理（多账号/多后端）

v3+ 支持把多份后端凭证持久化为 profile，集中放在 `~/.claude/profiles/<name>.json` (mode 600)。`~/.claude/active-profile` 记录当前激活的 profile。

### Profile type

| type | 适用场景 | 认证 | 代理 | 关键字段 |
|------|----------|------|------|----------|
| `anthropic` | 直连 Anthropic 官方或 sssaicode 中转 | AUTH_TOKEN | 无 | `url`, `key`, `model` |
| `genai-api` | GenAI OpenAI 兼容入口 (genaiapi.shanghaitech.edu.cn) | API key | SHTUClaudeProxy | `url`, `key`, `big_model`, `middle_model`, `small_model` |
| `genai-token` | GenAI HTK 入口 (genai.shanghaitech.edu.cn) | JWT token 或 学号@密码 | genai2api | `key` (JWT/学号@密码), `big_model`, `middle_model`, `small_model` |

切换时 `cmd_use` 按 type 分派到 `apply_profile_anthropic` / `apply_profile_genai_api` / `apply_profile_genai`，自动更新 `settings.json`、`~/.claude/genai-{token,model}.txt`、SHTUClaudeProxy config，并按需启停代理。

### 一次性迁移

旧版用户首次升级运行：

```bash
sm init
```

读 `settings.json` 中的 `ANTHROPIC_*` 字段生成 profile `claude`，读 `~/.claude/genai-token.txt` + `genai-model.txt` 生成 profile `genai`，按 `detect_mode` 自动设置 active。已存在的 profile 跳过。

### 新建 profile

```bash
# Anthropic 直连
sm add anthropic mycorp --url https://api.mycorp.com --token sk-... --model opus

# OpenAI 兼容（CCP 翻译）
sm add openai school --url https://api.school.edu/v1 --key sk-xxx --big gpt-4o

# GenAI（双代理链）
sm add genai backup --token <JWT> --big deepseek-pro
```

省略 flag 时交互式询问；`--token`/`--key` 输入隐藏。

### 切换 / 查看 / 编辑

```bash
sm ls                       # 列表 + active 标记
sm use mycorp               # 切到 mycorp（按 type 分派）
sm show mycorp              # 详情，key 脱敏
sm edit mycorp              # $EDITOR 改 JSON（保存时 jq 校验）
sm token mycorp <新JWT>     # 单独换某 profile 的 key；如果是 active genai，同步写 ~/.claude/genai-token.txt
sm rm backup                # 删 profile（active 拒删）
```

### 短命令兼容

`sm genai [model]` / `sm claude` 是 `sm use genai` / `sm use claude` 的语法糖。`sm genai GPT-5.5` 还会顺手把 `genai` profile 的 `big_model/middle_model/small_model` 全改为 `GPT-5.5`。

---

## 可用模型

### GenAI 免费模型
| 名称 | 实际模型 |
|------|----------|
| `deepseek-pro` | DeepSeek-V4-Pro (万亿参数) |
| `deepseek-chat` | DeepSeek-V4-Flash (284B) |
| `deepseek-r1:671b` | DeepSeek-R1 (685B, 深度思考) |
| `chatglm` | GLM 5.1 |
| `qwen-instruct` | Qwen3.5-397B |
| `MiniMax-M1` | MiniMax 2.7 |

### GenAI 付费模型
| 名称 | 说明 |
|------|------|
| `GPT-5.5` | OpenAI 旗舰 |
| `GPT-5.4` / `GPT-5.2` / `GPT-4.1` | OpenAI 系列 |
| `o3` | OpenAI 推理模型 |

### Claude 官方
| 名称 | 说明 |
|------|------|
| `opus` | Claude Opus 4.7 |
| `sonnet` | Claude Sonnet 4 |

---

## 故障排查

| 症状 | 原因 | 解决方案 |
|------|------|----------|
| `Auth conflict: both token and API key` | shell env 残留 | 用 `sm use <name>` 自动清理，或 `sm` shell wrapper |
| 思考后无回复 (deltas=0) | 模型名未被识别 | genai2api 三级解析自动 fallback；确认 genai-model.txt 有值 |
| GenAI 返回 500 "未找到对应节点" | 模型名无效 | `sm model deepseek-pro` 切到合法模型 |
| Token 过期 | JWT exp 到期 | `sm token genai <学号@密码>` 自动 CAS 刷新 |
| 代理未启动 | 进程未运行 | `sm start` 启动对应代理 |
| `switch-model.sh: command not found` | symlink 失效 | `ln -sfn ~/vscodespace/genai-stack/switch-model.sh ~/.local/bin/switch-model.sh` |
| 代理起了但没响应 | 端口被占用 | `fuser 5000/tcp` / `fuser 8082/tcp` 检查 |

### 查看日志

```bash
tail -f ~/.claude/genai-stack/logs/genai2api.log     # genai2api 日志
tail -f ~/.claude/genai-stack/logs/shtu-proxy.log    # SHTUClaudeProxy 日志
```

### 手动测试

```bash
# 测试 genai2api (Anthropic 格式)
curl -X POST http://localhost:5000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: local-proxy" \
  -d '{"model":"claude-opus-4-7","max_tokens":50,"stream":false,"messages":[{"role":"user","content":"hello"}]}'

# 健康检查
curl http://localhost:5000/health
curl http://localhost:8082/health
```

---

## 技术细节

### genai2api (proxies/genai2api)

- Flask 服务，监听 5000 端口
- `POST /v1/messages` — Anthropic Messages API（Claude Code 直连）
- `GET /v1/models` — 列出 GenAI 可用模型
- `GET /health` — 健康检查
- 模型名三级解析：Anthropic 前缀→genai-model.txt / 合法 GenAI→透传 / 未知→fallback
- genai-model.txt 每次请求读取，支持热切换
- Token 支持 JWT 直连或 `学号@密码` 格式（自动 CAS 登录刷新）
- 上游 API: `https://genai.shanghaitech.edu.cn/htk/chat/start/chat`

### SHTUClaudeProxy (proxies/SHTUClaudeProxy)

- 纯 Python stdlib HTTP 代理，监听 8082 端口
- `POST /v1/messages` — Anthropic Messages API（Claude Code 直连）
- 翻译为 OpenAI Responses 格式发送到上游
- 模型路由：`find_model()` 查配置表，未匹配→fallback 到 default_model_id
- 配置文件：`~/.config/SHTUClaudeProxy/config.json`（`CLAUDE_RESPONSES_PROXY_CONFIG` 环境变量）

### switch-model.sh

- 原子写入 settings.json（写 `.tmp` → `jq empty` 验证 → `mv`）
- 输出 `export`/`unset` 命令到 stdout（stderr 给人看），用 `eval` 捕获可同步 shell env
- Auth 互斥：GenAI 用 `ANTHROPIC_API_KEY=dummy`，Claude 用 `ANTHROPIC_AUTH_TOKEN=sk-...`，切换时删除对方
- `detect_mode()` 根据 BASE_URL 是否含 localhost 判断

---

## 开发历史

见 [CHANGES.md](CHANGES.md) 了解完整开发过程，包括：
1. v1: 单层代理 (main.py) — 因协议不兼容失败
2. v2: 双代理链 (CCP + main.py) — 解决 Anthropic↔OpenAI 格式问题
3. v3: 目录迁移 + Auth 防呆 + shell wrapper
4. v4: Profile 管理 + 多后端支持
5. v5: 单层代理重构（CCP/genai2api 各自原生 Anthropic API）
6. v6: genai2api 三级模型名解析 + genai-model.txt 热切换 + 学号@密码 CAS 自动登录
