#!/bin/bash
# Claude Code 模型切换脚本
# 用法: switch-model.sh {use|model|list|status|token|start|stop|restart|init|add|rm|edit|fix} [args]
#
# 架构（三模式）：
#   anthropic:    Claude Code → sssaicode → Anthropic API
#   genai-token:  Claude Code → genai2api(:5000) → genai.shanghaitech.edu.cn/htk
#   genai-api:    Claude Code → SHTUClaudeProxy(:8082) → genaiapi.shanghaitech.edu.cn/api/v1

SETTINGS_FILE="$HOME/.claude/settings.json"
BACKUP_FILE="$HOME/.claude/settings.json.backup"

# Claude 官方配置
CLAUDE_BASE_URL="${CLAUDE_BASE_URL:-https://node-hk.sssaicode.com/api}"
CLAUDE_AUTH_TOKEN="${CLAUDE_AUTH_TOKEN:-}"

# 代理项目目录
GENAI2API_DIR="$HOME/vscodespace/genai-stack/proxies/genai2api"   # JWT token → Anthropic (端口 5000)
SHTU_PROXY_DIR="$HOME/vscodespace/genai-stack/proxies/SHTUClaudeProxy"         # API key → Anthropic (端口 8082)
GENAI2API_PORT=5000
SHTU_PROXY_PORT=8082
PROXY_AUTH_TOKEN="local-proxy"

STACK_DIR="$HOME/vscodespace/genai-stack"
LOG_DIR="$HOME/.claude/genai-stack/logs"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# 检查 jq
if ! command -v jq &> /dev/null; then
    echo -e "${RED}错误: 需要安装 jq${NC}" >&2
    echo "运行: sudo apt install jq" >&2
    exit 1
fi

# 检查 settings.json 存在
if [ ! -f "$SETTINGS_FILE" ]; then
    echo -e "${RED}错误: $SETTINGS_FILE 不存在${NC}" >&2
    exit 1
fi

# 安全写入 settings.json：写到 tmp 再 mv（原子操作）
safe_write() {
    local tmp="$SETTINGS_FILE.tmp.$$"
    if ! cat > "$tmp"; then
        rm -f "$tmp"
        echo -e "${RED}错误: 写入临时文件失败${NC}" >&2
        exit 1
    fi
    if ! jq empty "$tmp" 2>/dev/null; then
        echo -e "${RED}错误: 生成的 JSON 无效，已回滚${NC}" >&2
        rm -f "$tmp"
        exit 1
    fi
    mv "$tmp" "$SETTINGS_FILE"
}

backup_settings() {
    cp "$SETTINGS_FILE" "$BACKUP_FILE"
    echo -e "${GREEN}✓${NC} 已备份 → $BACKUP_FILE" >&2
}

# ============= Profile helpers =============
PROFILES_DIR="$HOME/.claude/profiles"
ACTIVE_PROFILE_FILE="$HOME/.claude/active-profile"

profile_path() {
    echo "$PROFILES_DIR/$1.json"
}

profile_exists() {
    [ -f "$(profile_path "$1")" ]
}

# 读 profile 字段：read_profile_field <name> <jq-path>
# eg: read_profile_field claude .type
read_profile_field() {
    local name="$1" path="$2"
    jq -r "$path // empty" "$(profile_path "$name")" 2>/dev/null
}

# 写 profile：write_profile <name> <json>  (stdin 或参数)
write_profile() {
    local name="$1" json="$2"
    local path tmp
    path="$(profile_path "$name")"
    tmp="$path.tmp.$$"
    if [ -n "$json" ]; then
        printf '%s' "$json" > "$tmp"
    else
        cat > "$tmp"
    fi
    if ! jq empty "$tmp" 2>/dev/null; then
        echo -e "${RED}错误: profile JSON 无效${NC}" >&2
        rm -f "$tmp"
        return 1
    fi
    mv "$tmp" "$path"
    chmod 600 "$path"
}

list_profiles() {
    [ -d "$PROFILES_DIR" ] || return 0
    find "$PROFILES_DIR" -maxdepth 1 -name '*.json' -type f -printf '%f\n' 2>/dev/null \
        | sed 's/\.json$//' | sort
}

active_profile() {
    [ -f "$ACTIVE_PROFILE_FILE" ] && cat "$ACTIVE_PROFILE_FILE" || echo ""
}

set_active_profile() {
    echo "$1" > "$ACTIVE_PROFILE_FILE"
}

# 脱敏 key：前 12 字符 + ...
mask_key() {
    local key="$1"
    [ -z "$key" ] && return
    if [ ${#key} -le 12 ]; then
        echo "$key"
    else
        echo "${key:0:12}..."
    fi
}

ensure_log_dir() {
    mkdir -p "$LOG_DIR"
    chmod 700 "$HOME/.claude/genai-stack" "$LOG_DIR"
}

# ============= SHTUClaudeProxy config.json 写入 =============
# write_shtu_config <upstream_url> <api_key> <big_model> <mid_model> <small_model> [port]
write_shtu_config() {
    local url="$1" key="$2" big="$3" mid="$4" small="$5" port="${6:-$SHTU_PROXY_PORT}"
    local cfg_dir="$HOME/.config/SHTUClaudeProxy"
    mkdir -p "$cfg_dir"
    cat > "$cfg_dir/config.json" <<EOCFG
{
  "host": "127.0.0.1",
  "port": $port,
  "default_model_id": "$big",
  "models": [
    {
      "name": "$big",
      "model_id": "$big",
      "base_url": "${url}/response",
      "api_key": "$key",
      "upstream_model": "$big",
      "api_format": "responses"
    }
  ]
}
EOCFG
    chmod 600 "$cfg_dir/config.json"
    echo -e "${GREEN}✓ SHTUClaudeProxy config 已写入${NC} ($cfg_dir/config.json)" >&2
}

# 检测当前模式
detect_mode() {
    local base_url
    base_url=$(jq -r '.env.ANTHROPIC_BASE_URL // ""' "$SETTINGS_FILE")
    if [[ "$base_url" == *"localhost"* ]] || [[ "$base_url" == *"127.0.0.1"* ]]; then
        echo "genai"
    else
        echo "claude"
    fi
}

# 检测 settings.json 内冲突
check_conflicts() {
    local has_api_key has_auth_token
    has_api_key=$(jq -r 'has("env") and (.env | has("ANTHROPIC_API_KEY"))' "$SETTINGS_FILE")
    has_auth_token=$(jq -r 'has("env") and (.env | has("ANTHROPIC_AUTH_TOKEN"))' "$SETTINGS_FILE")

    if [ "$has_api_key" = "true" ] && [ "$has_auth_token" = "true" ]; then
        echo -e "${RED}⚠ 冲突: ANTHROPIC_API_KEY 和 ANTHROPIC_AUTH_TOKEN 同时存在!${NC}" >&2
        echo "  运行 'switch-model.sh genai' 或 'switch-model.sh claude' 修复" >&2
        return 1
    fi
    return 0
}

# 提示当前 shell env 是否残留对方变量
warn_env_leak() {
    local mode="$1"
    if [ "$mode" = "claude" ] && [ -n "$ANTHROPIC_API_KEY" ]; then
        echo -e "${YELLOW}⚠ 当前 shell 残留 ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}${NC}" >&2
        echo -e "${YELLOW}  Claude Code 启动会报 Auth conflict。修复方案：${NC}" >&2
        echo -e "${YELLOW}    eval \"\$(switch-model.sh claude)\"   # 使用 eval 自动 unset${NC}" >&2
        echo -e "${YELLOW}  或手动: unset ANTHROPIC_API_KEY${NC}" >&2
    fi
    if [ "$mode" = "genai" ] && [ -n "$ANTHROPIC_AUTH_TOKEN" ]; then
        echo -e "${YELLOW}⚠ 当前 shell 残留 ANTHROPIC_AUTH_TOKEN${NC}" >&2
        echo -e "${YELLOW}  修复: eval \"\$(switch-model.sh genai $2)\" 或 unset ANTHROPIC_AUTH_TOKEN${NC}" >&2
    fi
}

# ============= 切换到 GenAI 代理（旧版兼容，内部转发到 cmd_use genai）=============
switch_to_genai() {
    local model="${1:-deepseek-chat}"
    if profile_exists genai; then
        local path
        path="$(profile_path genai)"
        jq --arg m "$model" '.big_model = $m | .middle_model = $m | .small_model = $m' \
           "$path" > "$path.tmp" && mv "$path.tmp" "$path"
        chmod 600 "$path"
        cmd_use genai
    else
        echo -e "${RED}profile 'genai' 不存在，先运行 sm init 或 sm add genai${NC}" >&2
        return 1
    fi
}

# ============= 切换到 Claude 官方 =============
switch_to_claude() {
    backup_settings

    jq --arg base_url "$CLAUDE_BASE_URL" \
       --arg auth_token "$CLAUDE_AUTH_TOKEN" \
       '
       .env.ANTHROPIC_BASE_URL = $base_url |
       .env.ANTHROPIC_AUTH_TOKEN = $auth_token |
       del(.env.ANTHROPIC_API_KEY) |
       if (.env.API_TIMEOUT_MS | type) == "number" then .env.API_TIMEOUT_MS = (.env.API_TIMEOUT_MS | tostring) else . end
       ' "$SETTINGS_FILE" | safe_write

    echo "" >&2
    echo -e "${GREEN}✓ 已切换到 Claude 官方模式${NC}" >&2
    echo -e "  BASE_URL:   ${CYAN}$CLAUDE_BASE_URL${NC}" >&2
    echo -e "  AUTH_TOKEN: ${CYAN}sk-sssaicode-...${NC}" >&2
    echo -e "  API_KEY:    ${CYAN}(已删除)${NC}" >&2
    echo "" >&2

    # 输出可 eval 的 env 命令（永远全清，避免任何残留）
    echo "unset ANTHROPIC_AUTH_TOKEN"
    echo "unset ANTHROPIC_API_KEY"
    echo "unset ANTHROPIC_BASE_URL"
    echo "export ANTHROPIC_AUTH_TOKEN=$CLAUDE_AUTH_TOKEN"
    echo "export ANTHROPIC_BASE_URL=$CLAUDE_BASE_URL"

    warn_env_leak claude
    echo -e "${YELLOW}⚠ 重启 Claude Code 才能生效（或用 eval \$(switch-model.sh claude)）${NC}" >&2
}

# ============= 热切换 GenAI 模型 =============
switch_model_only() {
    local model="$1"

    if [ -z "$model" ]; then
        echo -e "${RED}错误: 需要指定模型名${NC}" >&2
        show_models
        exit 1
    fi

    local current_mode
    current_mode=$(detect_mode)

    if [ "$current_mode" = "genai" ]; then
        echo "$model" > "$HOME/.claude/genai-model.txt"
        echo -e "${GREEN}✓ GenAI 模型已切换: ${CYAN}$model${NC}" >&2
        echo "  main.py 自动热加载，CCP 无需重启" >&2
    else
        backup_settings
        jq --arg model "$model" '.model = $model' \
           "$SETTINGS_FILE" | safe_write
        echo -e "${GREEN}✓ Claude 模型已切换: ${CYAN}$model${NC}" >&2
        echo -e "${YELLOW}⚠ 重启 Claude Code 才能生效${NC}" >&2
    fi
}

# ============= 工具函数 =============

# 从 genai2api 动态拉取可用模型列表
fetch_live_models() {
    curl -s --max-time 3 "http://localhost:$GENAI2API_PORT/v1/models" 2>/dev/null \
        | jq -r '.data[].id' 2>/dev/null
}

# 从 genai2api 拉取模型列表(含 owned_by 分组信息)
fetch_live_models_full() {
    curl -s --max-time 3 "http://localhost:$GENAI2API_PORT/v1/models" 2>/dev/null \
        | jq -r '.data[] | "\(.owned_by)\t\(.id)"' 2>/dev/null
}

# JWT 过期时间解析
jwt_expiry() {
    local token="$1" payload
    [ -z "$token" ] && return 1
    payload=$(echo "$token" | cut -d. -f2 | tr '_-' '/+')
    # base64 padding
    local pad=$(( 4 - ${#payload} % 4 ))
    [ "$pad" -lt 4 ] && payload="${payload}$(printf '%0.s=' $(seq 1 $pad))"
    echo "$payload" | base64 -d 2>/dev/null | jq -r '.exp // empty' 2>/dev/null
}

jwt_remaining() {
    local exp
    exp=$(jwt_expiry "$1") || return 1
    [ -z "$exp" ] && return 1
    local now=$(date +%s)
    echo $(( exp - now ))
}

fmt_jwt_status() {
    local token="$1" remain
    remain=$(jwt_remaining "$token") || { echo "未知"; return; }
    if [ "$remain" -le 0 ]; then
        echo -e "${RED}已过期 $(( -remain / 60 ))分钟前${NC}"
    elif [ "$remain" -lt 3600 ]; then
        echo -e "${YELLOW}⏳ 剩余$(( remain / 60 ))分钟${NC}"
    else
        echo -e "${GREEN}⏳ 剩余$(( remain / 3600 ))小时${NC}"
    fi
}

# 带延迟的健康检查
health_detail() {
    local url="$1" name="$2"
    local start end ms
    start=$(date +%s%N 2>/dev/null || date +%s)
    if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then
        end=$(date +%s%N 2>/dev/null || date +%s)
        ms=$(( (end - start) / 1000000 ))
        echo -e "${GREEN}✓${NC} ${name} ${DIM}(${ms}ms)${NC}"
    else
        echo -e "${RED}✗${NC} ${name}"
    fi
}

# ============= 显示可用模型 =============
show_models() {
    echo ""
    echo -e "${CYAN}═══ 可用模型列表 ═══${NC}"

    # 尝试从 genai2api 拉取动态列表
    local live_models
    live_models=$(fetch_live_models_full 2>/dev/null)

    if [ -n "$live_models" ]; then
        echo ""
        echo -e "${GREEN}▸ GenAI 平台在线模型${NC} ${DIM}(来自 genai2api :$GENAI2API_PORT)${NC}"
        local last_group="" model_id group
        while IFS=$'\t' read -r group model_id; do
            if [ "$group" != "$last_group" ]; then
                echo -e "  ${DIM}── $group ──${NC}"
                last_group="$group"
            fi
            echo "    $model_id"
        done <<< "$live_models"
    else
        echo ""
        echo -e "${GREEN}【GenAI 免费模型】${NC}"
        echo "  deepseek-pro       DeepSeek-V4-Pro (万亿参数)"
        echo "  deepseek-chat      DeepSeek-V4-Flash (284B, 默认)"
        echo "  deepseek-r1:671b   DeepSeek-R1 (685B, 深度思考)"
        echo "  chatglm            GLM 5.1"
        echo "  qwen-instruct      Qwen3.5-397B"
        echo "  MiniMax-M1         MiniMax 2.7"
        echo ""
        echo -e "${GREEN}【GenAI 付费模型】${NC}"
        echo "  GPT-5.5            OpenAI 旗舰"
        echo "  GPT-5.4"
        echo "  GPT-5.2"
        echo "  GPT-4.1"
        echo "  o3                 推理模型"
        echo ""
        echo -e "${DIM}(genai2api 未运行，显示静态列表)${NC}"
    fi

    echo ""
    echo -e "${GREEN}【Claude 官方模型】${NC}"
    echo "  opus               Claude Opus 4.7"
    echo "  sonnet             Claude Sonnet 4"
}

# ============= 显示当前状态 =============
show_status() {
    local base_url model current_mode genai_model token_status
    base_url=$(jq -r '.env.ANTHROPIC_BASE_URL // "未设置"' "$SETTINGS_FILE")
    model=$(jq -r '.model // "未设置"' "$SETTINGS_FILE")
    current_mode=$(detect_mode)

    echo ""
    echo -e "${CYAN}═══ 当前配置状态 ═══${NC}"
    echo ""

    local active type
    active="$(active_profile)"
    if [ -n "$active" ] && profile_exists "$active"; then
        type=$(read_profile_field "$active" .type)
        echo -e "  Active profile: ${GREEN}$active${NC} (${CYAN}$type${NC})"
    else
        echo -e "  Active profile: ${YELLOW}未设置${NC} (运行 sm init)"
    fi

    if [ "$current_mode" = "genai" ]; then
        echo -e "  模式:     ${GREEN}GenAI 代理链${NC}"
    else
        echo -e "  模式:     ${GREEN}Claude 官方${NC}"
    fi

    echo -e "  BASE_URL: ${CYAN}$base_url${NC}"
    echo -e "  CC 模型:  ${CYAN}$model${NC}"

    if [ -f "$HOME/.claude/genai-model.txt" ]; then
        genai_model=$(cat "$HOME/.claude/genai-model.txt")
        echo -e "  GenAI 模型: ${CYAN}$genai_model${NC}"
    fi

    if [ -f "$HOME/.claude/genai-token.txt" ]; then
        token_status="已设置"
    else
        token_status="未设置"
    fi
    echo -e "  GenAI Token: ${CYAN}$token_status${NC}"

    # 代理状态（带延迟）
    echo ""
    local hc
    hc=$(health_detail "http://localhost:$SHTU_PROXY_PORT/health" "SHTUClaudeProxy :$SHTU_PROXY_PORT")
    echo -e "  $hc"
    hc=$(health_detail "http://localhost:$GENAI2API_PORT/health" "genai2api :$GENAI2API_PORT")
    echo -e "  $hc"

    # JWT 状态
    if [ -f "$HOME/.claude/genai-token.txt" ]; then
        local jwt_token
        jwt_token=$(cat "$HOME/.claude/genai-token.txt")
        echo -e "  JWT: $(fmt_jwt_status "$jwt_token")"
    fi

    echo ""
    check_conflicts

    # env 变量详情（settings.json）
    echo ""
    echo -e "${CYAN}settings.json env:${NC}"
    local has_api_key has_auth_token
    has_api_key=$(jq -r '.env.ANTHROPIC_API_KEY // empty' "$SETTINGS_FILE")
    has_auth_token=$(jq -r '.env.ANTHROPIC_AUTH_TOKEN // empty' "$SETTINGS_FILE")
    [ -n "$has_api_key" ] && echo -e "  ANTHROPIC_API_KEY:    ${CYAN}$(mask_key "$has_api_key")${NC}"
    [ -n "$has_auth_token" ] && echo -e "  ANTHROPIC_AUTH_TOKEN: ${CYAN}$(mask_key "$has_auth_token")${NC}"
    [ -z "$has_api_key" ] && [ -z "$has_auth_token" ] && echo "  (无认证变量 — 异常!)"

    # shell env 残留检查
    echo ""
    echo -e "${CYAN}当前 shell env:${NC}"
    [ -n "$ANTHROPIC_API_KEY" ] && echo -e "  ANTHROPIC_API_KEY=${CYAN}$(mask_key "$ANTHROPIC_API_KEY")${NC}"
    [ -n "$ANTHROPIC_AUTH_TOKEN" ] && echo -e "  ANTHROPIC_AUTH_TOKEN=${CYAN}$(mask_key "$ANTHROPIC_AUTH_TOKEN")${NC}"
    [ -n "$ANTHROPIC_BASE_URL" ] && echo -e "  ANTHROPIC_BASE_URL=${CYAN}$ANTHROPIC_BASE_URL${NC}"
    if [ -n "$ANTHROPIC_API_KEY" ] && [ -n "$ANTHROPIC_AUTH_TOKEN" ]; then
        echo -e "  ${RED}⚠ shell 残留冲突，运行 eval \"\$(switch-model.sh $current_mode)\"${NC}"
    fi

    # SHTUClaudeProxy 配置详情
    if [ "$type" = "genai-api" ] || [ "$type" = "openai" ]; then
        local cfg="$HOME/.config/SHTUClaudeProxy/config.json"
        if [ -f "$cfg" ]; then
            echo ""
            echo -e "${CYAN}SHTUClaudeProxy 配置:${NC}"
            jq '.' "$cfg" 2>/dev/null | sed 's/^/  /'
        fi
    fi

    echo ""
}

# ============= Token 管理 =============
save_token() {
    local token="$1"
    if [ -z "$token" ]; then
        echo -e "${RED}错误: 需要提供 token${NC}" >&2
        echo "用法: $0 token <JWT_TOKEN>" >&2
        exit 1
    fi
    printf '%s' "$token" > "$HOME/.claude/genai-token.txt"
    chmod 600 "$HOME/.claude/genai-token.txt"
    echo -e "${GREEN}✓ Token 已保存${NC} → ~/.claude/genai-token.txt" >&2
    echo "  需重启 genai2api: sm restart" >&2
}

# ============= 代理管理 =============
ensure_genai2api() {
    if [ ! -d "$GENAI2API_DIR/.venv" ]; then
        echo "首次运行：安装 genai2api 依赖..." >&2
        (cd "$GENAI2API_DIR" && uv sync) || { echo -e "${RED}genai2api 依赖安装失败${NC}" >&2; return 1; }
    fi
}

start_genai2api() {
    local token="$1" port="${2:-$GENAI2API_PORT}"
    if curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
        echo -e "${YELLOW}⚠ genai2api 已在 :$port 运行${NC}" >&2
        return 0
    fi
    # 清理端口占用
    fuser -k "$port/tcp" 2>/dev/null; sleep 0.5
    [ -z "$token" ] && token=$(cat "$HOME/.claude/genai-token.txt" 2>/dev/null)
    if [ -z "$token" ]; then
        echo -e "${RED}错误: 无 GenAI token${NC}" >&2
        echo "先运行: $0 token <JWT_TOKEN>" >&2
        return 1
    fi
    ensure_genai2api || return 1
    ensure_log_dir
    local log_file="$LOG_DIR/genai2api.log"
    echo "启动 genai2api (端口: $port)..." >&2
    cd "$GENAI2API_DIR" && GENAI_TOKEN_FILE="$HOME/.claude/genai-token.txt" API_KEY="$PROXY_AUTH_TOKEN" VIRTUAL_ENV= nohup uv run main.py --host 127.0.0.1 --port "$port" --api-format anthropic > "$log_file" 2>&1 &
    echo "  PID: $!  日志: $log_file" >&2
    for i in $(seq 1 20); do
        sleep 0.5
        if curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ genai2api 启动成功${NC}" >&2
            return 0
        fi
    done
    echo -e "${RED}✗ genai2api 启动超时${NC}，查看 tail $LOG_DIR/genai2api.log" >&2
    return 1
}

start_shtu_proxy() {
    local port="${1:-$SHTU_PROXY_PORT}"
    if curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
        echo -e "${YELLOW}⚠ SHTUClaudeProxy 已在 :$port 运行${NC}" >&2
        return 0
    fi
    # 清理端口占用
    fuser -k "$port/tcp" 2>/dev/null; sleep 0.5
    local cfg="$HOME/.config/SHTUClaudeProxy/config.json"
    if [ ! -f "$cfg" ]; then
        echo -e "${RED}错误: $cfg 不存在${NC}" >&2
        echo "先运行 sm use <genai-api profile> 生成配置" >&2
        return 1
    fi
    ensure_log_dir
    local log_file="$LOG_DIR/shtu-proxy.log"
    echo "启动 SHTUClaudeProxy (端口: $port)..." >&2
    CLAUDE_RESPONSES_PROXY_CONFIG="$cfg" nohup python3 "$SHTU_PROXY_DIR/cli.py" serve > "$log_file" 2>&1 &
    echo "  PID: $!  日志: $log_file" >&2
    for i in $(seq 1 20); do
        sleep 0.5
        if curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ SHTUClaudeProxy 启动成功${NC}" >&2
            return 0
        fi
    done
    echo -e "${RED}✗ SHTUClaudeProxy 启动超时${NC}，查看 tail $LOG_DIR/shtu-proxy.log" >&2
    return 1
}

stop_genai2api() {
    if pkill -f "proxies/genai2api/main.py" 2>/dev/null; then
        echo -e "${GREEN}✓ genai2api 已停止${NC}" >&2
    fi
}

stop_shtu_proxy() {
    if pkill -f "proxies/SHTUClaudeProxy/cli.py" 2>/dev/null; then
        echo -e "${GREEN}✓ SHTUClaudeProxy 已停止${NC}" >&2
    fi
}

stop_all_proxies() {
    local stopped=0
    if pkill -f "proxies/genai2api/main.py" 2>/dev/null; then
        echo -e "${GREEN}✓ genai2api 已停止${NC}" >&2
        stopped=1
    fi
    if pkill -f "proxies/SHTUClaudeProxy/cli.py" 2>/dev/null; then
        echo -e "${GREEN}✓ SHTUClaudeProxy 已停止${NC}" >&2
        stopped=1
    fi
    [ $stopped -eq 0 ] && echo "代理未在运行" >&2
}

restart_shtu_proxy() {
    stop_shtu_proxy
    sleep 1
    start_shtu_proxy
}

restart_genai2api() {
    stop_genai2api
    sleep 1
    start_genai2api "$(cat "$HOME/.claude/genai-token.txt" 2>/dev/null)"
}

smart_start() {
    local active type
    active="$(active_profile)"
    if [ -z "$active" ]; then
        echo -e "${RED}无 active profile，先运行 sm init${NC}" >&2
        return 1
    fi
    type=$(read_profile_field "$active" .type)
    case "$type" in
        anthropic)
            echo -e "${GREEN}active=$active (anthropic) — 无需代理${NC}" >&2
            ;;
        openai|genai-api)
            start_shtu_proxy || return 1
            echo -e "${GREEN}✓ SHTUClaudeProxy 已启动 ($type profile $active)${NC}" >&2
            ;;
        genai)
            start_genai2api "" || return 1
            echo -e "${GREEN}✓ genai2api 已启动 (genai profile $active)${NC}" >&2
            ;;
    esac
}

# ============= apply_profile_* — Phase 4 核心切换 =============
apply_profile_anthropic() {
    local name="$1"
    local url key model
    url=$(read_profile_field "$name" .url)
    key=$(read_profile_field "$name" .key)
    model=$(read_profile_field "$name" .model)
    [ -z "$model" ] && model="opus"

    backup_settings
    jq --arg base_url "$url" \
       --arg auth_token "$key" \
       '
       .env.ANTHROPIC_BASE_URL = $base_url |
       .env.ANTHROPIC_AUTH_TOKEN = $auth_token |
       del(.env.ANTHROPIC_API_KEY) |
       del(.env.ANTHROPIC_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_OPUS_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_SONNET_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_HAIKU_MODEL) |
       del(.env.CLAUDE_CODE_SUBAGENT_MODEL) |
       if (.env.API_TIMEOUT_MS | type) == "number" then .env.API_TIMEOUT_MS = (.env.API_TIMEOUT_MS | tostring) else . end
       ' "$SETTINGS_FILE" | safe_write

    stop_genai2api
    stop_shtu_proxy

    echo "" >&2
    echo -e "${GREEN}✓ profile ${CYAN}$name${GREEN} (anthropic) 已激活${NC}" >&2
    echo -e "  BASE_URL:   ${CYAN}$url${NC}" >&2
    echo -e "  AUTH_TOKEN: ${CYAN}$(mask_key "$key")${NC}" >&2
    echo -e "  model:      ${CYAN}$model${NC}" >&2
    echo "" >&2

    echo "unset ANTHROPIC_AUTH_TOKEN"
    echo "unset ANTHROPIC_API_KEY"
    echo "unset ANTHROPIC_BASE_URL"
    echo "export ANTHROPIC_AUTH_TOKEN=$key"
    echo "export ANTHROPIC_BASE_URL=$url"

    warn_env_leak claude
    echo -e "${YELLOW}⚠ 重启 Claude Code 才能生效（或用 eval \$(switch-model.sh use $name)）${NC}" >&2
}

apply_profile_openai() {
    local name="$1"
    local url key big mid small
    url=$(read_profile_field "$name" .url)
    key=$(read_profile_field "$name" .key)
    big=$(read_profile_field "$name" .big_model)
    mid=$(read_profile_field "$name" .middle_model)
    small=$(read_profile_field "$name" .small_model)
    [ -z "$mid" ] && mid="$big"
    [ -z "$small" ] && small="$big"

    backup_settings
    jq --arg base_url "http://localhost:$SHTU_PROXY_PORT" \
       --arg auth_token "$PROXY_AUTH_TOKEN" \
       '
       .env.ANTHROPIC_BASE_URL = $base_url |
       .env.ANTHROPIC_AUTH_TOKEN = $auth_token |
       del(.env.ANTHROPIC_API_KEY) |
       del(.env.ANTHROPIC_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_OPUS_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_SONNET_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_HAIKU_MODEL) |
       del(.env.CLAUDE_CODE_SUBAGENT_MODEL) |
       if (.env.API_TIMEOUT_MS | type) == "number" then .env.API_TIMEOUT_MS = (.env.API_TIMEOUT_MS | tostring) else . end
       ' "$SETTINGS_FILE" | safe_write

    write_shtu_config "$url" "$key" "$big" "$mid" "$small"

    stop_genai2api
    restart_shtu_proxy

    echo "" >&2
    echo -e "${GREEN}✓ profile ${CYAN}$name${GREEN} (openai) 已激活${NC}" >&2
    echo -e "  Claude Code → :$SHTU_PROXY_PORT (SHTUClaudeProxy) → ${CYAN}$url${NC}" >&2
    echo -e "  API_KEY: ${CYAN}$(mask_key "$key")${NC}" >&2
    echo -e "  models:  big=${CYAN}$big${NC} mid=${CYAN}$mid${NC} small=${CYAN}$small${NC}" >&2
    echo "" >&2

    echo "unset ANTHROPIC_AUTH_TOKEN"
    echo "unset ANTHROPIC_API_KEY"
    echo "unset ANTHROPIC_BASE_URL"
    echo "export ANTHROPIC_AUTH_TOKEN=$PROXY_AUTH_TOKEN"
    echo "export ANTHROPIC_BASE_URL=http://localhost:$SHTU_PROXY_PORT"

    warn_env_leak genai
    echo -e "${YELLOW}⚠ 重启 Claude Code 才能生效${NC}" >&2
}

apply_profile_genai() {
    local name="$1"
    local key big mid small
    key=$(read_profile_field "$name" .key)
    big=$(read_profile_field "$name" .big_model)
    mid=$(read_profile_field "$name" .middle_model)
    small=$(read_profile_field "$name" .small_model)
    [ -z "$big" ] && big="GPT-5.5"
    [ -z "$mid" ] && mid="$big"
    [ -z "$small" ] && small="$big"

    backup_settings
    jq --arg base_url "http://localhost:$GENAI2API_PORT" \
       --arg auth_token "$PROXY_AUTH_TOKEN" \
       '
       .env.ANTHROPIC_BASE_URL = $base_url |
       .env.ANTHROPIC_AUTH_TOKEN = $auth_token |
       del(.env.ANTHROPIC_API_KEY) |
       del(.env.ANTHROPIC_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_OPUS_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_SONNET_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_HAIKU_MODEL) |
       del(.env.CLAUDE_CODE_SUBAGENT_MODEL) |
       if (.env.API_TIMEOUT_MS | type) == "number" then .env.API_TIMEOUT_MS = (.env.API_TIMEOUT_MS | tostring) else . end
       ' "$SETTINGS_FILE" | safe_write

    printf '%s' "$key" > "$HOME/.claude/genai-token.txt"
    chmod 600 "$HOME/.claude/genai-token.txt"
    echo "$big" > "$HOME/.claude/genai-model.txt"

    stop_shtu_proxy
    start_genai2api "$key" || echo -e "${YELLOW}⚠ genai2api 启动失败${NC}" >&2

    echo "" >&2
    echo -e "${GREEN}✓ profile ${CYAN}$name${GREEN} (genai) 已激活${NC}" >&2
    echo -e "  Claude Code → :$GENAI2API_PORT (genai2api) → GenAI HTK" >&2
    echo -e "  token:   ${CYAN}$(mask_key "$key")${NC}" >&2
    echo -e "  models:  big=${CYAN}$big${NC} mid=${CYAN}$mid${NC} small=${CYAN}$small${NC}" >&2
    echo "" >&2

    echo "unset ANTHROPIC_AUTH_TOKEN"
    echo "unset ANTHROPIC_API_KEY"
    echo "unset ANTHROPIC_BASE_URL"
    echo "export ANTHROPIC_AUTH_TOKEN=$PROXY_AUTH_TOKEN"
    echo "export ANTHROPIC_BASE_URL=http://localhost:$GENAI2API_PORT"

    warn_env_leak genai "$big"
    echo -e "${YELLOW}⚠ 重启 Claude Code 才能生效${NC}" >&2
}

apply_profile_genai_api() {
    local name="$1"
    local url key big mid small
    url=$(read_profile_field "$name" .url)
    key=$(read_profile_field "$name" .key)
    big=$(read_profile_field "$name" .big_model)
    mid=$(read_profile_field "$name" .middle_model)
    small=$(read_profile_field "$name" .small_model)
    [ -z "$mid" ] && mid="$big"
    [ -z "$small" ] && small="$big"

    backup_settings
    jq --arg base_url "http://localhost:$SHTU_PROXY_PORT" \
       --arg auth_token "$PROXY_AUTH_TOKEN" \
       '
       .env.ANTHROPIC_BASE_URL = $base_url |
       .env.ANTHROPIC_AUTH_TOKEN = $auth_token |
       del(.env.ANTHROPIC_API_KEY) |
       del(.env.ANTHROPIC_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_OPUS_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_SONNET_MODEL) |
       del(.env.ANTHROPIC_DEFAULT_HAIKU_MODEL) |
       del(.env.CLAUDE_CODE_SUBAGENT_MODEL) |
       if (.env.API_TIMEOUT_MS | type) == "number" then .env.API_TIMEOUT_MS = (.env.API_TIMEOUT_MS | tostring) else . end
       ' "$SETTINGS_FILE" | safe_write

    write_shtu_config "$url" "$key" "$big" "$mid" "$small"

    stop_genai2api
    restart_shtu_proxy

    echo "" >&2
    echo -e "${GREEN}✓ profile ${CYAN}$name${GREEN} (genai-api) 已激活${NC}" >&2
    echo -e "  Claude Code → :$SHTU_PROXY_PORT (SHTUClaudeProxy) → ${CYAN}$url${NC}" >&2
    echo -e "  API_KEY: ${CYAN}$(mask_key "$key")${NC}" >&2
    echo -e "  models:  big=${CYAN}$big${NC} mid=${CYAN}$mid${NC} small=${CYAN}$small${NC}" >&2
    echo "" >&2

    echo "unset ANTHROPIC_AUTH_TOKEN"
    echo "unset ANTHROPIC_API_KEY"
    echo "unset ANTHROPIC_BASE_URL"
    echo "export ANTHROPIC_AUTH_TOKEN=$PROXY_AUTH_TOKEN"
    echo "export ANTHROPIC_BASE_URL=http://localhost:$SHTU_PROXY_PORT"

    warn_env_leak genai
    echo -e "${YELLOW}⚠ 重启 Claude Code 才能生效${NC}" >&2
}

cmd_token() {
    local arg1="$1" arg2="$2"
    local target_name new_token
    if [ -n "$arg2" ]; then
        target_name="$arg1"
        new_token="$arg2"
    else
        target_name="$(active_profile)"
        new_token="$arg1"
        if [ -z "$target_name" ]; then
            echo -e "${RED}无 active profile，请用 sm token <name> <token>${NC}" >&2
            return 1
        fi
    fi
    if [ -z "$new_token" ]; then
        echo -e "${RED}用法: sm token [<name>] <token>${NC}" >&2
        return 1
    fi
    if ! profile_exists "$target_name"; then
        echo -e "${RED}profile $target_name 不存在${NC}" >&2
        return 1
    fi
    local path
    path="$(profile_path "$target_name")"
    jq --arg key "$new_token" '.key = $key' "$path" > "$path.tmp" && mv "$path.tmp" "$path"
    chmod 600 "$path"
    echo -e "${GREEN}✓ profile ${CYAN}$target_name${GREEN} token 已更新${NC}" >&2

    local type
    type=$(read_profile_field "$target_name" .type)
    if [ "$type" = "genai" ] && [ "$target_name" = "$(active_profile)" ]; then
        printf '%s' "$new_token" > "$HOME/.claude/genai-token.txt"
        chmod 600 "$HOME/.claude/genai-token.txt"
        echo -e "${GREEN}✓ ~/.claude/genai-token.txt 已同步${NC}" >&2
        echo -e "${YELLOW}⚠ 需重启 genai2api: sm restart${NC}" >&2
    fi
    if [ "$type" = "genai-api" ] && [ "$target_name" = "$(active_profile)" ]; then
        local url big mid small
        url=$(read_profile_field "$target_name" .url)
        big=$(read_profile_field "$target_name" .big_model)
        mid=$(read_profile_field "$target_name" .middle_model)
        small=$(read_profile_field "$target_name" .small_model)
        [ -z "$mid" ] && mid="$big"
        [ -z "$small" ] && small="$big"
        write_shtu_config "$url" "$new_token" "$big" "$mid" "$small"
        echo -e "${GREEN}✓ SHTUClaudeProxy config 已更新${NC} (重启生效: sm restart)" >&2
    fi
}

cmd_model() {
    local model="$1"
    if [ -z "$model" ]; then
        echo -e "${RED}用法: sm model <model>${NC}" >&2
        show_models
        return 1
    fi
    local active type
    active="$(active_profile)"
    if [ -z "$active" ]; then
        echo -e "${RED}无 active profile${NC}" >&2
        return 1
    fi
    type=$(read_profile_field "$active" .type)
    case "$type" in
        genai)
            local path
            path="$(profile_path "$active")"
            jq --arg m "$model" '.big_model = $m | .middle_model = $m | .small_model = $m' \
               "$path" > "$path.tmp" && mv "$path.tmp" "$path"
            chmod 600 "$path"
            echo "$model" > "$HOME/.claude/genai-model.txt"
            echo -e "${GREEN}✓ GenAI 模型已切换: ${CYAN}$model${NC}" >&2
            echo -e "${YELLOW}⚠ 需重启 genai2api: sm restart${NC}" >&2
            ;;
        genai-api)
            local path url key
            path="$(profile_path "$active")"
            jq --arg m "$model" '.big_model = $m | .middle_model = $m | .small_model = $m' \
               "$path" > "$path.tmp" && mv "$path.tmp" "$path"
            chmod 600 "$path"
            url=$(read_profile_field "$active" .url)
            key=$(read_profile_field "$active" .key)
            write_shtu_config "$url" "$key" "$model" "$model" "$model"
            restart_shtu_proxy
            echo -e "${GREEN}✓ GenAI-API 模型已切换: ${CYAN}$model${NC}" >&2
            ;;
        openai)
            local path url key
            path="$(profile_path "$active")"
            jq --arg m "$model" '.big_model = $m | .middle_model = $m | .small_model = $m' \
               "$path" > "$path.tmp" && mv "$path.tmp" "$path"
            chmod 600 "$path"
            url=$(read_profile_field "$active" .url)
            key=$(read_profile_field "$active" .key)
            write_shtu_config "$url" "$key" "$model" "$model" "$model"
            restart_shtu_proxy
            echo -e "${GREEN}✓ OpenAI 模型已切换: ${CYAN}$model${NC}" >&2
            ;;
        anthropic)
            backup_settings
            jq --arg m "$model" '.model = $m' "$SETTINGS_FILE" | safe_write
            echo -e "${GREEN}✓ Claude 模型已切换: ${CYAN}$model${NC}" >&2
            echo -e "${YELLOW}⚠ 重启 Claude Code 才能生效${NC}" >&2
            ;;
    esac
}

cmd_use() {
    local name="$1"
    if [ -z "$name" ]; then
        echo -e "${RED}用法: sm use <profile-name>${NC}" >&2
        cmd_ls
        return 1
    fi
    if ! profile_exists "$name"; then
        echo -e "${RED}profile ${CYAN}$name${RED} 不存在${NC}" >&2
        cmd_ls
        return 1
    fi
    local type
    type=$(read_profile_field "$name" .type)
    case "$type" in
        anthropic)  apply_profile_anthropic "$name" ;;
        openai)     apply_profile_openai "$name" ;;
        genai)      apply_profile_genai "$name" ;;
        genai-api)  apply_profile_genai_api "$name" ;;
        *)
            echo -e "${RED}未知 profile type: $type${NC}" >&2
            return 1
            ;;
    esac
    set_active_profile "$name"
}

# ============= sm init — 迁移现有凭证为 profile =============
cmd_init() {
    mkdir -p "$PROFILES_DIR"
    chmod 700 "$PROFILES_DIR"

    local created=0 skipped=0

    # 1. 从 settings.json 读 anthropic 凭证 → profile claude
    local cur_url cur_token
    cur_url=$(jq -r '.env.ANTHROPIC_BASE_URL // empty' "$SETTINGS_FILE")
    cur_token=$(jq -r '.env.ANTHROPIC_AUTH_TOKEN // empty' "$SETTINGS_FILE")

    if [ -n "$cur_token" ] && [ -n "$cur_url" ] && [[ "$cur_url" != *"localhost"* ]]; then
        if profile_exists "claude"; then
            echo -e "${YELLOW}⚠ profile 'claude' 已存在，跳过${NC}" >&2
            skipped=$((skipped+1))
        else
            local json
            json=$(jq -n \
                --arg url "$cur_url" \
                --arg key "$cur_token" \
                '{type: "anthropic", url: $url, key: $key, model: "opus"}')
            write_profile "claude" "$json" || return 1
            echo -e "${GREEN}✓${NC} 创建 profile ${CYAN}claude${NC} (anthropic, $cur_url)" >&2
            created=$((created+1))
        fi
    elif [ -n "$CLAUDE_AUTH_TOKEN" ]; then
        # settings 里没 anthropic 凭证（可能当前是 genai 模式），用脚本顶部硬编码
        if ! profile_exists "claude"; then
            local json
            json=$(jq -n \
                --arg url "$CLAUDE_BASE_URL" \
                --arg key "$CLAUDE_AUTH_TOKEN" \
                '{type: "anthropic", url: $url, key: $key, model: "opus"}')
            write_profile "claude" "$json" || return 1
            echo -e "${GREEN}✓${NC} 创建 profile ${CYAN}claude${NC} (anthropic, 来自脚本默认 sssaicode)" >&2
            created=$((created+1))
        fi
    fi

    # 2. 从 ~/.claude/genai-token.txt 读 genai 凭证 → profile genai
    if [ -f "$HOME/.claude/genai-token.txt" ]; then
        if profile_exists "genai"; then
            echo -e "${YELLOW}⚠ profile 'genai' 已存在，跳过${NC}" >&2
            skipped=$((skipped+1))
        else
            local jwt big
            jwt=$(cat "$HOME/.claude/genai-token.txt")
            big=$(cat "$HOME/.claude/genai-model.txt" 2>/dev/null || echo "GPT-5.5")
            [ -z "$big" ] && big="GPT-5.5"
            local json
            json=$(jq -n \
                --arg key "$jwt" \
                --arg big "$big" \
                --arg mid "$big" \
                --arg small "$big" \
                '{type: "genai", key: $key, big_model: $big, middle_model: $mid, small_model: $small}')
            write_profile "genai" "$json" || return 1
            echo -e "${GREEN}✓${NC} 创建 profile ${CYAN}genai${NC} (genai, model=$big)" >&2
            created=$((created+1))
        fi
    fi

    # 3. 设置 active-profile：基于 detect_mode
    if [ ! -f "$ACTIVE_PROFILE_FILE" ]; then
        local mode
        mode=$(detect_mode)
        if [ "$mode" = "genai" ] && profile_exists "genai"; then
            set_active_profile "genai"
            echo -e "${GREEN}✓${NC} active profile = ${CYAN}genai${NC}" >&2
        elif profile_exists "claude"; then
            set_active_profile "claude"
            echo -e "${GREEN}✓${NC} active profile = ${CYAN}claude${NC}" >&2
        fi
    else
        echo -e "${YELLOW}⚠ active-profile 已存在 ($(active_profile))，未变更${NC}" >&2
    fi

    echo "" >&2
    echo "创建 $created 个 profile，跳过 $skipped 个" >&2
    echo "运行 ${CYAN}sm ls${NC} 查看所有 profile" >&2
}

# ============= sm add — 创建 profile =============
# 参数解析助手：从 "$@" 抓 --flag value，找到则 echo value
_get_flag() {
    local flag="$1"; shift
    while [ $# -gt 0 ]; do
        if [ "$1" = "$flag" ]; then
            echo "$2"
            return 0
        fi
        shift
    done
}

_prompt_if_empty() {
    local var_name="$1" prompt_text="$2" hidden="$3" current="${!1}"
    if [ -z "$current" ]; then
        if [ "$hidden" = "1" ]; then
            read -rsp "$prompt_text: " current
            echo >&2
        else
            read -rp "$prompt_text: " current
        fi
        printf -v "$var_name" '%s' "$current"
    fi
}

cmd_add() {
    local subtype="$1" name="$2"
    shift 2 || true

    if [ -z "$subtype" ] || [ -z "$name" ]; then
        echo -e "${RED}用法: sm add {anthropic|openai|genai|genai-api} <name> [flags]${NC}" >&2
        echo "  anthropic: --url URL --token TOKEN [--model MODEL]" >&2
        echo "  openai:    --url URL --key KEY --big MODEL [--middle M] [--small M]" >&2
        echo "  genai:     --token JWT [--big MODEL] [--middle M] [--small M]" >&2
        echo "  genai-api: [--url URL] --key KEY --big MODEL [--middle M] [--small M]" >&2
        return 1
    fi

    if profile_exists "$name"; then
        echo -e "${RED}错误: profile '$name' 已存在。用 'sm edit $name' 改它，或 'sm rm $name' 先删${NC}" >&2
        return 1
    fi

    mkdir -p "$PROFILES_DIR"
    chmod 700 "$PROFILES_DIR"

    case "$subtype" in
        anthropic|a)
            local url token model
            url=$(_get_flag --url "$@")
            token=$(_get_flag --token "$@")
            model=$(_get_flag --model "$@")
            _prompt_if_empty url "Anthropic URL (e.g. https://api.anthropic.com)"
            _prompt_if_empty token "Auth token (sk-...)" 1
            [ -z "$model" ] && model="opus"
            local json
            json=$(jq -n --arg u "$url" --arg k "$token" --arg m "$model" \
                '{type:"anthropic", url:$u, key:$k, model:$m}')
            write_profile "$name" "$json" || return 1
            echo -e "${GREEN}✓${NC} 创建 profile ${CYAN}$name${NC} (anthropic)" >&2
            ;;
        openai|o)
            local url key big mid small
            url=$(_get_flag --url "$@")
            key=$(_get_flag --key "$@")
            big=$(_get_flag --big "$@")
            mid=$(_get_flag --middle "$@")
            small=$(_get_flag --small "$@")
            _prompt_if_empty url "OpenAI 兼容 URL (e.g. https://api.school.edu/v1)"
            _prompt_if_empty key "API key" 1
            _prompt_if_empty big "Big model (opus 映射，如 gpt-4o)"
            [ -z "$mid" ] && mid="$big"
            [ -z "$small" ] && small="$big"
            local json
            json=$(jq -n --arg u "$url" --arg k "$key" --arg b "$big" --arg m "$mid" --arg s "$small" \
                '{type:"openai", url:$u, key:$k, big_model:$b, middle_model:$m, small_model:$s}')
            write_profile "$name" "$json" || return 1
            echo -e "${GREEN}✓${NC} 创建 profile ${CYAN}$name${NC} (openai, $url → $big)" >&2
            ;;
        genai|g)
            local token big mid small
            token=$(_get_flag --token "$@")
            big=$(_get_flag --big "$@")
            mid=$(_get_flag --middle "$@")
            small=$(_get_flag --small "$@")
            _prompt_if_empty token "GenAI JWT token" 1
            [ -z "$big" ] && big="GPT-5.5"
            [ -z "$mid" ] && mid="$big"
            [ -z "$small" ] && small="$big"
            local json
            json=$(jq -n --arg k "$token" --arg b "$big" --arg m "$mid" --arg s "$small" \
                '{type:"genai", key:$k, big_model:$b, middle_model:$m, small_model:$s}')
            write_profile "$name" "$json" || return 1
            echo -e "${GREEN}✓${NC} 创建 profile ${CYAN}$name${NC} (genai, $big)" >&2
            ;;
        genai-api|ga)
            local url key big mid small
            url=$(_get_flag --url "$@")
            key=$(_get_flag --key "$@")
            big=$(_get_flag --big "$@")
            mid=$(_get_flag --middle "$@")
            small=$(_get_flag --small "$@")
            [ -z "$url" ] && url="https://genaiapi.shanghaitech.edu.cn/api/v1"
            _prompt_if_empty key "GenAI API key" 1
            _prompt_if_empty big "Big model (opus 映射，如 GPT-5.5)"
            [ -z "$mid" ] && mid="$big"
            [ -z "$small" ] && small="$big"
            local json
            json=$(jq -n --arg u "$url" --arg k "$key" --arg b "$big" --arg m "$mid" --arg s "$small" \
                '{type:"genai-api", url:$u, key:$k, big_model:$b, middle_model:$m, small_model:$s}')
            write_profile "$name" "$json" || return 1
            echo -e "${GREEN}✓${NC} 创建 profile ${CYAN}$name${NC} (genai-api, $url → $big)" >&2
            ;;
        *)
            echo -e "${RED}未知 type: $subtype (期望 anthropic|openai|genai|genai-api)${NC}" >&2
            return 1
            ;;
    esac
}

# ============= sm rm — 删 profile =============
cmd_rm() {
    local name="$1"
    if [ -z "$name" ]; then
        echo -e "${RED}用法: sm rm <name>${NC}" >&2
        return 1
    fi
    if ! profile_exists "$name"; then
        echo -e "${RED}profile '$name' 不存在${NC}" >&2
        return 1
    fi
    if [ "$(active_profile)" = "$name" ]; then
        echo -e "${RED}错误: '$name' 当前 active，先 'sm use <其他>' 切走再删${NC}" >&2
        return 1
    fi
    read -rp "确认删除 profile '$name'? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm -f "$(profile_path "$name")"
        echo -e "${GREEN}✓${NC} 已删 profile ${CYAN}$name${NC}" >&2
    else
        echo "取消" >&2
    fi
}

# ============= sm show — 详情 =============
cmd_show() {
    local name="$1"
    if [ -z "$name" ]; then
        name=$(active_profile)
        if [ -z "$name" ]; then
            echo -e "${RED}用法: sm show <name>  (无 active profile)${NC}" >&2
            return 1
        fi
    fi
    if ! profile_exists "$name"; then
        echo -e "${RED}profile '$name' 不存在${NC}" >&2
        return 1
    fi
    local active_mark=""
    [ "$(active_profile)" = "$name" ] && active_mark=" ${GREEN}(active)${NC}"
    echo "" >&2
    echo -e "${CYAN}═══ profile: $name${NC}$active_mark" >&2
    # 脱敏 key/token，原样显示其它字段
    jq -r '
        to_entries[] |
        if .key == "key" then
            "  \(.key): \(if (.value | length) > 12 then (.value[:12] + "...(" + (.value | length | tostring) + " chars)") else .value end)"
        else
            "  \(.key): \(.value)"
        end
    ' "$(profile_path "$name")" >&2
    echo "" >&2
}

# ============= sm edit — 用 $EDITOR 编辑 =============
cmd_edit() {
    local name="$1"
    if [ -z "$name" ] || ! profile_exists "$name"; then
        echo -e "${RED}用法: sm edit <existing-name>${NC}" >&2
        return 1
    fi
    local path
    path="$(profile_path "$name")"
    "${EDITOR:-vi}" "$path"
    if ! jq empty "$path" 2>/dev/null; then
        echo -e "${RED}警告: 编辑后 JSON 无效。请修复或恢复 .bak${NC}" >&2
        return 1
    fi
    chmod 600 "$path"
    echo -e "${GREEN}✓${NC} 已保存 profile ${CYAN}$name${NC}" >&2
}

# ============= sm ls — 列所有 profile =============
cmd_ls() {
    if [ ! -d "$PROFILES_DIR" ] || [ -z "$(list_profiles)" ]; then
        echo -e "${YELLOW}无 profile。运行 'sm init' 迁现有凭证，或 'sm add' 创建。${NC}" >&2
        return 0
    fi
    local active
    active=$(active_profile)
    echo "" >&2
    echo -e "${CYAN}═══ Profiles ═══${NC}" >&2
    printf "  %-3s %-15s %-12s %-40s %s\n" "" "NAME" "TYPE" "URL/MODEL" "KEY"
    while IFS= read -r name; do
        local type url key big marker tag
        type=$(read_profile_field "$name" .type)
        url=$(read_profile_field "$name" .url)
        key=$(read_profile_field "$name" .key)
        big=$(read_profile_field "$name" .big_model)
        if [ "$name" = "$active" ]; then
            marker="${GREEN}★${NC}"
            tag="${GREEN}*${NC}"
        else
            marker=" "
            tag=" "
        fi
        local display_url
        if [ "$type" = "genai" ]; then
            display_url="(HTK) $big"
        elif [ -n "$big" ]; then
            display_url="$url → $big"
        else
            display_url="$url"
        fi
        printf "  %-3b %-15s %-12s %-40s %s\n" "$marker" "$name" "$type" "$display_url" "$(mask_key "$key")"
    done < <(list_profiles)
    echo "" >&2
    echo "★ = active profile" >&2
}

# ============= 修复冲突状态 =============
fix_conflicts() {
    local current_mode
    current_mode=$(detect_mode)

    if ! check_conflicts 2>/dev/null; then
        backup_settings
        if [ "$current_mode" = "genai" ]; then
            echo "检测到 GenAI 模式，删除 AUTH_TOKEN..." >&2
            jq 'del(.env.ANTHROPIC_AUTH_TOKEN)' "$SETTINGS_FILE" | safe_write
        else
            echo "检测到 Claude 模式，删除 API_KEY..." >&2
            jq 'del(.env.ANTHROPIC_API_KEY)' "$SETTINGS_FILE" | safe_write
        fi
        echo -e "${GREEN}✓ settings.json 冲突已修复${NC}" >&2
    else
        echo -e "${GREEN}✓ settings.json 无冲突${NC}" >&2
    fi

    # shell env 残留 — 输出 unset 命令
    if [ -n "$ANTHROPIC_API_KEY" ] && [ "$current_mode" = "claude" ]; then
        echo "unset ANTHROPIC_API_KEY"
        echo -e "${YELLOW}（已输出 unset 命令；用 eval \"\$(switch-model.sh fix)\" 应用）${NC}" >&2
    fi
    if [ -n "$ANTHROPIC_AUTH_TOKEN" ] && [ "$current_mode" = "genai" ]; then
        echo "unset ANTHROPIC_AUTH_TOKEN"
        echo -e "${YELLOW}（已输出 unset 命令；用 eval \"\$(switch-model.sh fix)\" 应用）${NC}" >&2
    fi
}

# ============= 交互式菜单 =============
show_menu() {
    local current_mode genai_model shtu_status g2a_status
    current_mode=$(detect_mode)
    genai_model=$(cat "$HOME/.claude/genai-model.txt" 2>/dev/null || echo "未设置")

    if curl -s "http://localhost:$SHTU_PROXY_PORT/health" > /dev/null 2>&1; then
        shtu_status="${GREEN}✓${NC}"
    else
        shtu_status="${RED}✗${NC}"
    fi
    if curl -s "http://localhost:$GENAI2API_PORT/health" > /dev/null 2>&1; then
        g2a_status="${GREEN}✓${NC}"
    else
        g2a_status="${RED}✗${NC}"
    fi

    local active type
    active="$(active_profile)"
    type=$(read_profile_field "$active" .type 2>/dev/null)

    echo "" >&2
    echo -e "${CYAN}═══ GenAI Stack 快捷菜单 ═══${NC}" >&2
    echo -e "  Profile: ${GREEN}${active:-无}${NC} (${CYAN}${type:-?}${NC})  SHTUProxy $shtu_status  genai2api $g2a_status" >&2
    echo "" >&2

    echo -e "  ${CYAN}1${NC}) 切到 Claude 官方" >&2
    echo -e "  ${CYAN}2${NC}) 切到 GenAI (选模型)" >&2
    echo -e "  ${CYAN}3${NC}) 热切换模型" >&2
    echo -e "  ${CYAN}4${NC}) 查看状态" >&2
    echo -e "  ${CYAN}5${NC}) 启动代理" >&2
    echo -e "  ${CYAN}6${NC}) 重启代理" >&2
    echo -e "  ${CYAN}7${NC}) 停止代理" >&2
    echo -e "  ${CYAN}8${NC}) 更新 Token" >&2
    echo -e "  ${CYAN}9${NC}) 显示可用模型" >&2
    echo -e "  ${CYAN}0${NC}) 退出" >&2
    echo "" >&2

    read -rp "选择 [0-9]: " choice
    echo "" >&2

    case "$choice" in
        1) cmd_use claude ;;
        2)
            show_models >&2
            echo "" >&2
            read -rp "输入模型名 (默认 deepseek-chat): " model_name
            switch_to_genai "${model_name:-deepseek-chat}"
            ;;
        3)
            show_models >&2
            echo "" >&2
            read -rp "输入模型名: " model_name
            if [ -n "$model_name" ]; then
                cmd_model "$model_name"
            else
                echo -e "${RED}未输入模型名${NC}" >&2
            fi
            ;;
        4) show_status ;;
        5) smart_start ;;
        6)
            stop_all_proxies
            sleep 1
            smart_start
            ;;
        7) stop_all_proxies ;;
        8)
            read -rp "粘贴 Token/Key: " jwt
            if [ -n "$jwt" ]; then
                cmd_token "$jwt"
            else
                echo -e "${RED}未输入${NC}" >&2
            fi
            ;;
        9) show_models ;;
        0) echo "退出" >&2 ;;
        *) echo -e "${RED}无效选项${NC}" >&2 ;;
    esac
}

# ============= 帮助信息 =============
show_help() {
    echo ""
    echo -e "${CYAN}switch-model.sh${NC} — Claude Code 多模式切换"
    echo ""
    echo "架构:"
    echo "  anthropic:   Claude Code → sssaicode → Anthropic API"
    echo "  genai-token: Claude Code → genai2api(:$GENAI2API_PORT) → GenAI HTK"
    echo "  genai-api:   Claude Code → SHTUClaudeProxy(:$SHTU_PROXY_PORT) → genaiapi"
    echo ""
    echo -e "${GREEN}切换模式:${NC}"
    echo "  genai|g  [model]    切到 GenAI 代理 (默认 deepseek-chat)"
    echo "  claude|c            切回 Claude 官方"
    echo "  model|m  <name>     热切换模型"
    echo ""
    echo -e "${GREEN}代理管理:${NC}"
    echo "  start               启动当前 profile 对应代理"
    echo "  stop                停止所有代理"
    echo "  restart|r           重启代理"
    echo "  token|t  <JWT>      保存/更新 GenAI token"
    echo ""
    echo -e "${GREEN}查看信息:${NC}"
    echo "  status|s            settings + 代理 + shell env 状态"
    echo "  list|l              可用模型"
    echo "  fix                 修复 settings 冲突 + 输出 unset 命令"
    echo ""
    echo -e "${GREEN}Profile 管理 (~/.claude/profiles/):${NC}"
    echo "  init                从现有 settings/token 文件迁移生成 profile"
    echo "  ls                  列出所有 profile (★ = active)"
    echo "  use|u    <name>     按 type 切换到指定 profile"
    echo "  add      <name> ... 新建 profile (anthropic/openai/genai/genai-api)"
    echo "  rm       <name>     删除 profile"
    echo "  show     <name>     显示 profile 详情 (脱敏 key)"
    echo "  edit     <name>     用 \$EDITOR 编辑 profile JSON"
    echo ""
    echo -e "${GREEN}交互:${NC}"
    echo "  menu                交互式菜单 (无参数时默认)"
    echo "  help|h              本帮助信息"
    echo ""
    echo -e "${YELLOW}推荐:${NC} 切换时用 ${CYAN}eval \"\$(switch-model.sh genai|claude)\"${NC}"
    echo "  同步清理 shell env 残留，避免 Auth conflict"
    echo ""
}

# ============= 主逻辑 =============
case "$1" in
    genai|g)
        if [ -n "$2" ] && profile_exists genai; then
            path="$(profile_path genai)"
            jq --arg m "$2" '.big_model = $m | .middle_model = $m | .small_model = $m' \
               "$path" > "$path.tmp" && mv "$path.tmp" "$path"
            chmod 600 "$path"
        fi
        cmd_use genai
        ;;
    claude|c)
        cmd_use claude
        ;;
    model|m)
        cmd_model "$2"
        ;;
    list|l)
        show_models
        ;;
    ls)
        cmd_ls
        ;;
    init)
        cmd_init
        ;;
    use|u)
        cmd_use "$2"
        ;;
    add)
        shift
        cmd_add "$@"
        ;;
    rm)
        cmd_rm "$2"
        ;;
    show)
        cmd_show "$2"
        ;;
    edit)
        cmd_edit "$2"
        ;;
    status|s)
        show_status
        ;;
    token|t)
        cmd_token "$2" "$3"
        ;;
    start)
        smart_start "$2"
        ;;
    stop)
        stop_all_proxies
        ;;
    restart|r)
        stop_all_proxies
        sleep 1
        smart_start "$2"
        ;;
    fix)
        fix_conflicts
        ;;
    menu|"")
        show_menu
        ;;
    help|h|-h|--help)
        show_help
        ;;
    *)
        echo -e "${RED}未知命令: $1${NC}" >&2
        echo "运行 switch-model.sh help 查看帮助" >&2
        exit 1
        ;;
esac
