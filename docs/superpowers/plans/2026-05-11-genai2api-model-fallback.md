# genai2api 模型名 fallback 修复与端到端验证

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `_resolve_model` 对非 Anthropic 前缀/非 GenAI 模型名的透传问题，加上 fallback 到 genai-model.txt 的逻辑

**Architecture:** genai2api 的 `_resolve_model` 原来只判断 `claude-*`/`anthropic-*` 前缀，其他名字全部透传。当 Claude Code 发来 `deepseek-v4-pro`（DeepSeek API 模型的残留名），透传到 GenAI 后端导致 500。修复后三级判断：Anthropic 前缀 → 映射 / 合法 GenAI 模型 → 透传 / 未知 → fallback

**Tech Stack:** Python, Flask, model_registry from config.py

---

### Task 1: 验证 `_resolve_model` 代码正确性

**Files:**
- Verify: `proxies/genai2api/provider/anthropic.py:24-59`
- Verify: `proxies/genai2api/provider/anthropic.py:213` (call site)

- [ ] **Step 1: 运行语法检查**

```bash
cd ~/vscodespace/genai-stack/proxies/genai2api && .venv/bin/python -c "
from provider.anthropic import _resolve_model
# 1. Anthropic prefix → should map to genai-model.txt
print('Test 1 claude-opus-4-7:', _resolve_model('claude-opus-4-7'))
# 2. Unknown model → should fallback to genai-model.txt
print('Test 2 deepseek-v4-pro:', _resolve_model('deepseek-v4-pro'))
# 3. No prefix, no token → should fallback
print('Test 3 unknown:', _resolve_model('unknown-model'))
print('OK - syntax valid')
"
```

Expected: All three return contents of `~/.claude/genai-model.txt` or `deepseek-chat`

- [ ] **Step 2: 确认 call site 传递 token 参数**

```bash
grep -n '_resolve_model' ~/vscodespace/genai-stack/proxies/genai2api/provider/anthropic.py
```

Expected: Line 213 shows `model = _resolve_model(body.get("model", "GPT-5.5"), token)`

---

### Task 2: 重启 genai2api 使修改生效

**Files:**
- Modify: `~/.claude/settings.json` (switch to genai)
- No file changes, process management only

- [ ] **Step 1: 切换到 genai profile**

```bash
cd ~/vscodespace/genai-stack && eval "$(bash switch-model.sh use genai 2>&1 | tail -20)"
```

- [ ] **Step 2: 停止旧的 genai2api 进程**

```bash
pkill -f "proxies/genai2api/main.py" 2>/dev/null && sleep 1 && echo "stopped" || echo "not running"
```

- [ ] **Step 3: 启动新 genai2api**

```bash
cd ~/vscodespace/genai-stack && bash switch-model.sh start 2>&1
```

- [ ] **Step 4: 验证健康检查**

```bash
curl -s http://localhost:5000/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 5: 验证模型列表可访问**

```bash
curl -s http://localhost:5000/v1/models -H "x-api-key: local-proxy" | python3 -c "import sys,json; data=json.load(sys.stdin); print(f'{len(data[\"data\"])} models'); [print(f'  - {m[\"id\"]}') for m in data['data']]"
```

Expected: 11 models listed

---

### Task 3: curl 端到端测试模型名解析

- [ ] **Step 1: 测试 Anthropic 前缀自动映射**

```bash
curl -s -X POST http://localhost:5000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: local-proxy" \
  -d '{
    "model": "claude-opus-4-7",
    "stream": false,
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "Say hello in one word"}]
  }' 2>&1 | head -5
```

Expected: 正常返回 JSON 响应（非 SSE 格式），说明 `claude-opus-4-7` 被映射到 `genai-model.txt` 中的模型

- [ ] **Step 2: 测试未知模型名 fallback**

```bash
curl -s -X POST http://localhost:5000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: local-proxy" \
  -d '{
    "model": "deepseek-v4-pro",
    "stream": false,
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "Say hello in one word"}]
  }' 2>&1 | head -5
```

Expected: 正常返回 JSON 响应（不再报 500 `未找到对应节点信息`），`deepseek-v4-pro` 被 fallback 到 genai-model.txt

- [ ] **Step 3: 测试合法 GenAI 模型名透传**

```bash
# 先确认 genai-model.txt 的内容
MODEL=$(cat ~/.claude/genai-model.txt)
echo "Current genai model: $MODEL"

# 用另一个合法 GenAI 模型名测试（不是 genai-model.txt 里的）
curl -s -X POST http://localhost:5000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: local-proxy" \
  -d "{
    \"model\": \"GPT-5.5\",
    \"stream\": false,
    \"max_tokens\": 50,
    \"messages\": [{\"role\": \"user\", \"content\": \"Say hello in one word\"}]
  }" 2>&1 | head -5
```

Expected: 正常返回，`GPT-5.5` 作为合法 GenAI 模型名被透传

- [ ] **Step 4: 确认 genai2api 日志无 500 错误**

```bash
tail -20 ~/.claude/genai-stack/logs/genai2api.log | grep -v health
```

Expected: 看到 POST 请求返回 200，无 `GenAI error (code=500)` 或 `未找到对应节点信息`

---

### Task 4: 模型热切换测试

- [ ] **Step 1: 切换模型并立即测试**

```bash
# 切换到 deepseek-pro
echo "deepseek-pro" > ~/.claude/genai-model.txt
echo "Switched to deepseek-pro"

# 立即发送请求（不重启 genai2api）
curl -s -X POST http://localhost:5000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: local-proxy" \
  -d '{
    "model": "claude-opus-4-7",
    "stream": false,
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "Say hello in one word"}]
  }' 2>&1 | head -5
```

Expected: 正常返回，热切换生效无需重启

- [ ] **Step 2: 切回 deepseek-chat**

```bash
echo "deepseek-chat" > ~/.claude/genai-model.txt
echo "Restored to deepseek-chat"
```

---

### Task 5: 清理环境

- [ ] **Step 1: 确认 settings.json 为 genai 模式**

```bash
python3 -c "
import json
s = json.load(open('$HOME/.claude/settings.json'))
env = s.get('env', {})
assert env.get('ANTHROPIC_BASE_URL') == 'http://localhost:5000', f'Wrong URL: {env.get(\"ANTHROPIC_BASE_URL\")}'
assert env.get('ANTHROPIC_AUTH_TOKEN') == 'local-proxy', f'Wrong token: {env.get(\"ANTHROPIC_AUTH_TOKEN\")}'
assert 'ANTHROPIC_MODEL' not in env, f'ANTHROPIC_MODEL should not be set: {env.get(\"ANTHROPIC_MODEL\")}'
print('OK - settings.json is correct for genai mode')
"
```

- [ ] **Step 2: 确认 active-profile 为 genai**

```bash
cat ~/.claude/active-profile
```

Expected: `genai`

- [ ] **Step 3: 列出所有 curl 测试结果摘要**

```bash
echo "=== Test Summary ==="
echo "1. _resolve_model syntax: PASS"
echo "2. genai2api restart: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:5000/health)"
echo "3. Model list: $(curl -s http://localhost:5000/v1/models -H 'x-api-key: local-proxy' | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["data"]))') models"
echo "4. genai-model.txt: $(cat ~/.claude/genai-model.txt)"
echo "5. Active profile: $(cat ~/.claude/active-profile)"
```
