# Codex Monitor

[English](README.md) | 中文

Codex Monitor 是一个本地只读的 Codex Desktop 监控浮层。它可以在现有 Codex 窗口中显示 context window 使用情况和 token 消耗，不修改客户端安装包，也不会把本地会话数据复制进仓库。

![Codex Monitor demo](assets/codex-monitor-demo.svg)

## 作者

由 Kevin KE 制作。

- GitHub: [KevinKE93](https://github.com/KevinKE93)

## 主要功能

- 在 Codex Desktop 内显示可拖动的 `Monitor` 面板。
- 在每条回复后显示 chip：包含 context 使用量、本轮 token、当前 session 累计 token、user rounds 和 assistant rounds。
- 左侧会话列表 hover 面板显示当前 session 的 total、input、cached input、output 和 reasoning token。
- token 单位支持 raw、K、M，默认使用 K。
- Monitor 收起后只保留紧凑标题和展开按钮，隐藏单位切换控件。
- 通过 Chrome DevTools Protocol 本地运行，不依赖远端服务。

## 使用方式

推荐使用自动重注入模式：

```bash
./scripts/start_codex_monitor.sh 9222
```

这是推荐方式。脚本会用本地 DevTools 端口打开 Codex，并持续运行 injector；当 Codex renderer 重启后，浮层会自动恢复。
injector 默认每 10 秒刷新一次 session payload；普通的页面切换和 DOM 更新由页面内 observer 处理。

手动用本地 DevTools 端口启动 Codex：

```bash
./scripts/reopen_codex_with_debug.sh 9222
```

向当前 Codex 窗口注入 Monitor：

```bash
./run_once.sh 9222
```

Codex 重启后需要重新运行。当前页面保持打开时，已注入的 UI 会持续更新。

## Codex 客户端升级处理

Codex Monitor 注入的是临时 DOM 元素，这是有意设计的：它避免修改 `Codex.app` 或客户端资源。如果 Codex Desktop 升级、重启或替换 renderer，已注入的 UI 会消失，需要重新注入。

自动化方式请使用 `./scripts/start_codex_monitor.sh 9222`。它会用 DevTools 端口重新启动 Codex，并循环运行 injector；当 DevTools endpoint 消失时，会重新打开并注入。如果未来 Codex 改动了侧边栏行或 assistant 消息的 DOM anchor，Monitor 仍然能读取 token 数据，但 chip 挂载位置可能需要更新 selector。

## Codex 插件

这个仓库也已经封装成 Codex plugin：

- 插件清单：`.codex-plugin/plugin.json`
- 插件技能：`skills/codex-monitor/SKILL.md`

插件暴露了本地脚本和使用流程。当前 Codex plugin 还没有提供受支持的原生 sidebar 或消息 DOM render hook，因此可视化浮层仍然通过本地 DevTools injector 按需启用。

## 命令行查看

```bash
python3 ./scripts/context_token_inspector.py --latest --format footer
python3 ./scripts/context_token_inspector.py --latest --format hover
python3 ./scripts/context_token_inspector.py --limit 20 --format table
```

## 显示名词解释

| 名词 | 含义 | 来源或计算方式 |
| --- | --- | --- |
| `context` | 当前请求占用的上下文量。 | `last_token_usage.input_tokens` |
| `context window` | Codex token-count 事件里记录的模型上下文窗口上限。 | `model_context_window` |
| `left` | 当前请求预计剩余 context。 | `context window - context` |
| `turn token` | 当前 assistant 回复这一轮的 token 消耗。 | `last_token_usage.total_tokens` |
| `total token` | 当前 session 的累计 token 消耗。 | `total_token_usage.total_tokens` |
| `session` | Monitor 面板中的当前 session 总消耗，不统计其他会话。 | 最新 session JSONL |
| `in` | Codex 记录的输入 token。在 Monitor 的 session 行里，它表示当前 session 累计输入。 | `input_tokens` |
| `cached` | Codex 记录的 cached input token。当重复上下文被复用时，这个值可能较高。 | `cached_input_tokens` |
| `out` | assistant 生成的输出 token。 | `output_tokens` |
| `reason` | Codex 记录的 reasoning output token。 | `reasoning_output_tokens` |
| `user rounds` | 当前 session 中非环境消息的 user message 数量。这个更接近人理解的“对话轮次”。 | 解析 user messages |
| `assistant rounds` | 当前 session 中带 token-count 记录的 assistant message 数量。一次 user round 里可能出现多个 assistant rounds，因为 Codex 可能先输出进度/状态消息，再输出最终回复。 | 解析带 token usage 的 assistant messages |
| `status` | context 压力提示。低于 70% 为 `OK`，达到 70% 为 `WATCH`，达到 85% 为 `HIGH`。 | `context / context window` |
| `raw / K / M` | token 显示单位。`raw` 显示原始整数，`K` 显示千，`M` 显示百万。默认单位是 `K`。 | UI 设置 |

## 安全边界

Codex Monitor 不会修改：

- `Codex.app`
- `app.asar`
- Codex 会话 JSONL 文件
- Codex 设置或登录认证信息

它只读取本地 Codex session 日志，并向带本地 DevTools 端口启动的 Codex renderer 注入临时 DOM 元素。

## 测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 ./tests/test_context_token_inspector.py
```

## 仓库隐私

本仓库只包含源码、测试和合成示意图，不包含本地 Codex session 数据、生成日志、marketplace 元数据、私人对话截图或对话记录。

## 开源许可

MIT 许可证。详见 [LICENSE](LICENSE)。
