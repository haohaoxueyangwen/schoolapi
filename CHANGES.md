# GenAI Stack 变更日志

## v5 (2026-04-30)

### 重构：双代理链 → 第三方单代理

**删除** 自写双代理链（CCP claude-code-proxy + GenAI2OpenAI main.py），替换为两个第三方项目：

| 旧 | 新 | 用途 |
|----|-----|------|
| claude-code-proxy (CCP) | SHTUClaudeProxy | genai-api 模式（API key） |
| GenAI2OpenAI main.py | shanghaitech-genai2api | genai-token 模式（JWT） |

两个新代理都**原生暴露 Anthropic `/v1/messages`**，单层代理即可，不再需要 Anthropic→OpenAI 翻译。

### switch-model.sh 改动

- `write_ccp_env` → `write_shtu_config`：生成 `~/.config/SHTUClaudeProxy/config.json`
- `start_ccp/start_main_py/start_proxy` → `start_shtu_proxy/start_genai2api`
- `stop_ccp/stop_main_py/stop_proxy` → `stop_shtu_proxy/stop_genai2api/stop_all_proxies`
- `ensure_venvs` → `ensure_genai2api`（SHTUClaudeProxy 纯 stdlib 无需 venv）
- `apply_profile_*` 全部重写，统一用 `ANTHROPIC_AUTH_TOKEN=local-proxy`
- 删除 `ANTHROPIC_API_KEY=dummy` hack
- 变量重命名：`CCP_DIR/GENAI_DIR` → `SHTU_PROXY_DIR/GENAI2API_DIR`

### cc-launch 改动

- 健康检查：`ccp_up/main_up` → `shtu_up/genai2api_up`
- `print_header`：显示对应代理名和端口
- `ensure_proxy`：按 profile type 启动正确代理

### 关键修复

- SHTUClaudeProxy 需用 `responses` API 格式（非 `chat_completions`），否则流式解析失败（deltas=0）
- GPT 系列模型 endpoint 为 `/api/v1/response`，非 `/api/v1/start`

---

## v4 (2026-04-30)

### 新增：cc-launch 一键启动器
- `~/.local/bin/cc-launch` (237 行)，alias `ccl`（`cc` 撞 C compiler）
- 交互菜单：切 profile / 改 token / 切模型 / 重启代理 / 启动
- 直达模式：`ccl claude`、`ccl genai`
- 启动前 `unset ANTHROPIC_*`，杜绝 Auth conflict（env 残留问题彻底解决）
- 用 `exec claude` 替换进程，退出 claude 回到 shell

### 架构升级：双代理链 → 三模式

原来只有 anthropic + genai（双代理链）两种模式，现拆分为三种：

| 模式 | 后端 | 认证 | 代理 |
|------|------|------|------|
| anthropic | Anthropic (sssaicode 中转) | AUTH_TOKEN (sk-...) | 无 |
| genai-api | genaiapi.shanghaitech.edu.cn/api/v1 | API key | CCP 一层 |
| genai-token | genai.shanghaitech.edu.cn/htk | JWT token | CCP + main.py 两层 |

genai-api 是新增模式：GenAI 平台提供 OpenAI 兼容入口 (`genaiapi.shanghaitech.edu.cn`)，用 API key 认证，后端已是 OpenAI 格式，只需 CCP 做 Anthropic→OpenAI 翻译，不需要 main.py。

### 待实现
- [ ] profile JSON 支持 `genai-api` type
- [ ] `switch-model.sh` 新增 `apply_profile_genai_api` 分支（只启 CCP，`.env` 指向 genaiapi URL）
- [ ] `cc-launch` 的 `ensure_proxy` 识别 genai-api（只检查 CCP，不检查 main.py）
- [ ] `cc-launch` 的 `print_header` 显示 genai-api 状态
- [ ] 测试三模式切换流程

---

## v3 (2026-04-29)

### 重构
- **目录迁移：** `~/genai-stack` → `~/vscodespace/genai-stack`，与其他项目同级
- **Claude 单实例：** 删除旧版 `~/vscodespace/claudecode/` (v2.1.111)，删除空壳 `~/vscodespace/hermes/`
- **Claude/opencode 迁出 anaconda：** 用 anaconda npm（prefix 改为 `~/.local`）将 `@anthropic-ai/claude-code` (升 2.1.123)、`opencode-ai` (1.14.29) 装到 `~/.local/lib/node_modules/`；物理删除 `anaconda3/lib/node_modules/{@anthropic-ai,opencode-ai}` 及 `anaconda3/bin/{claude,opencode}` 残留。Node 工具与 Python 环境彻底分离。
- **PATH 清理：** `.bashrc` 中过滤 stale `claudecode/bin` 路径

### 修复
- **Auth 防呆 (F1+F2)：** `switch-model.sh` 始终 emit 完整 `unset` 列表；新增 `~/.bashrc` shell wrapper 函数 `sm()` 与 `switch-model.sh()`，任意调用方式都自动 eval，彻底消除 `Auth conflict` 报警

### 垃圾清理（回收 ≈3GB）
- VSCode 旧扩展 .119/.120 (487M)
- bun cache `@anthropic-ai/` (1.5G → 残 34M)
- anaconda3 残留 node_modules (784M)

### 备份
- 关键文件保留在 `~/migration-backup-2026-04-29/`
- 旧目录重命名为 `.deleted-*-2026-04-29/`，7 天后清理

---

# Claude Code 模型切换系统改进

**日期:** 2026-04-28

## 背景
用户有 GenAI 代理（上海科技大学 GenAI 平台），想在 Claude Code 中使用。
下载了开源项目 `GenAI2OpenAI`（位于 `hermes/GenAI2OpenAI/`），将 GenAI API 转成 OpenAI 兼容格式。
Claude Code 支持 OpenAI 兼容 API，所以链路为：

```
Claude Code ---(OpenAI 格式)---> GenAI2OpenAI 代理 (localhost:5000) ---(GenAI 格式)---> GenAI API
```

同时还有 Claude 官方代理（node-hk.sssaicode.com），两种模式需要切换。
`switch-model.sh` 负责切换 settings.json 中的代理配置。

## 问题
1. Claude Code 只认 Anthropic 模型名（opus/sonnet/haiku），GenAI 模型名（GPT-5.5/deepseek-pro）会报错
2. 两种模式切换时 ANTHROPIC_API_KEY 和 ANTHROPIC_AUTH_TOKEN 残留导致 auth 冲突
3. GenAI2OpenAI 代理是独立进程，settings.json 里的 env 变量（如 GENAI_REAL_MODEL）传不到代理

## 解决方案
- 统一伪装：Claude Code 永远看到 `model=opus`
- 配置文件传递真实模型：`~/.claude/genai-model.txt`，代理每次请求读取（支持热切换，不用重启代理）
- Auth 互斥：GenAI 用 API_KEY，Claude 用 AUTH_TOKEN，切换时删除对方

## 修改内容

### 1. switch-model.sh

#### switch_to_genai()
- 写真实模型到 `~/.claude/genai-model.txt`
- settings.json 固定 `model=opus` + `API_KEY=dummy`
- 删除 `ANTHROPIC_AUTH_TOKEN`
- 删除 `GENAI_REAL_MODEL`（不再用环境变量传递）

#### switch_to_claude()
- 删除 `~/.claude/genai-model.txt`
- 删除 `ANTHROPIC_API_KEY`
- 恢复 `ANTHROPIC_AUTH_TOKEN`

### 2. hermes/GenAI2OpenAI/main.py

- 添加 `_read_real_model()` 函数，从 `~/.claude/genai-model.txt` 读取真实模型
- 每次请求都读文件（支持热切换，不用重启代理）
- 如果文件不存在/为空，回退到请求中的 model 字段

## 使用方式

```bash
# 切换到 GenAI + GPT-5.5
./switch-model.sh genai GPT-5.5

# 切换到 GenAI + deepseek
./switch-model.sh genai deepseek-pro

# 切回 Claude 官方
./switch-model.sh claude
```

**切换模型不需要重启代理**（代理每次请求读文件）。
**切换代理模式仍需重启 Claude Code**（settings.json 环境变量变了）。

## 测试步骤

1. `./switch-model.sh genai GPT-5.5` → 重启 Claude Code → 验证能用
2. 不重启代理，直接 `./switch-model.sh genai deepseek-pro` → 新 Claude Code session 应该用 deepseek
3. `./switch-model.sh claude` → 重启 Claude Code → 验证 Claude 官方正常
4. 检查无 auth 冲突警告

---

# v2 改进 — 2026-04-28（第三次会话）

## 新发现的致命问题
原架构 `Claude Code → main.py:5000` 从未真正工作过。

**根因**：Claude Code 用 **Anthropic Messages API** (`POST /v1/messages` + content blocks)，main.py 只暴露 **OpenAI Chat Completions** (`POST /v1/chat/completions` + choices array)。两者格式不兼容 → 永远 404 → "model not found" 误报。

## 新架构（双代理链）
```
Claude Code ──Anthropic──→ :8082 (claude-code-proxy)
                        ──OpenAI──→ :5000 (main.py)
                                  ──GenAI──→ HTK API
```

CCP (claude-code-proxy / fuergaosi233) 做 Anthropic↔OpenAI 翻译，main.py 保持原职责（OpenAI↔GenAI）。

## 工作空间整理
所有相关代码 + 脚本搬到 `~/genai-stack/`：
- `GenAI2OpenAI/` (原 ~/vscodespace/hermes/GenAI2OpenAI/)
- `claude-code-proxy/` (新克隆)
- `switch-model.sh`, `CLAUDE.md`, `CHANGES.md`

`~/.local/bin/switch-model.sh` symlink 已重指向新位置。

## switch-model.sh v2 新增
- `start`/`stop`/`restart` 同时管理 CCP + main.py 两个进程
- `status` 同时查看双代理 + shell env 残留
- 切换模式时输出 `unset` + `export` 命令 → 用户可 `eval "$(switch-model.sh genai GPT-5.5)"` 同步清 env
- 首次运行自动 `uv sync` 安装两个项目依赖

## 关键配置变更
**GenAI 模式下 `ANTHROPIC_BASE_URL` 现在指 `:8082`**（CCP 入口），不再指 `:5000`（main.py）。
原因：Claude Code 必须先经过协议翻译层。

## 验证（已通过）
- 非流式：`POST :8082/v1/messages` 返回 Anthropic content blocks
- 流式 SSE：完整 `message_start` → `content_block_delta` → `message_stop` 事件链
