# 本地改动说明（2026-04-23）

## 结论

- 本次会话里，我实际落盘的文件只在当前仓库 `~/Codes/aw-watcher-llm`。
- 真正的 `LLM Rhythm` 实现不在这个仓库里，而是在 `~/Codes/aw-webui`。
- `~/Codes/aw-webui` 在本次会话里没有成功写入。两次写入尝试都停在越权写入授权前，被中断了。
- 因此，当前真正需要修的 bug 还没有被正式落到 `aw-webui`。

## 我本次实际改过的文件

本次会话里，我确认改过这 4 个文件：

- `visualization/dist/app.js`
- `visualization/dist/index.html`
- `visualization/dist/standalone.html`
- `visualization/dist/styles.css`

这些改动的目的，是在 `aw-watcher-llm` 这套静态可视化里尝试加入“按最近 N 天折叠回 24h 的 avg 模式”。  
后续确认后发现：用户截图里的 `LLM Rhythm` 并不是这套页面，所以这些改动属于打到了错误目标上的探索性改动。

## 当前 worktree 快照

### 1. aw-watcher-llm

当前 `git status --short`：

```text
 M README.md
 M aw_watcher_llm/cli.py
 M aw_watcher_llm/demo.py
 M aw_watcher_llm/opencode.py
 M docs/aw-watcher-llm-demo.md
 M docs/custom-visual-design.md
 M visualization/README.md
 M visualization/dist/app.js
 M visualization/dist/index.html
 M visualization/dist/styles.css
?? aw_watcher_llm/codex.py
?? aw_watcher_llm/visualization_server.py
?? docs/product-summary-2026-04-23.md
?? visualization/dist/standalone.html
```

说明：

- 这个仓库在本次会话开始前就已经是 dirty worktree。
- 我没有尝试回滚现有改动。
- 我本次明确碰过的是 `visualization/dist/` 下面那 4 个文件。

### 2. aw-webui

当前 `git status --short`：

```text
 M src/components/SelectableVisualization.vue
 M src/main.js
 M src/stores/views.ts
 M src/views/activity/ActivityView.vue
 M src/visualizations/Summary.vue
 M src/visualizations/summary.ts
?? src/components/LLMSummaryPanel.vue
?? src/util/llm.ts
?? src/visualizations/LLMConcurrencyChart.vue
?? src/visualizations/LLMRhythmChart.vue
```

说明：

- `aw-webui` 也已经是 dirty worktree。
- `LLMSummaryPanel.vue / util/llm.ts / LLMConcurrencyChart.vue / LLMRhythmChart.vue` 当前是未跟踪文件，说明这块 LLM 视图本身就在本地开发态。
- 我在本次会话里没有成功写入 `aw-webui`。

## 真正的 bug 在哪里

用户截图里的 `LLM Rhythm` 对应的是 `aw-webui`，关键文件如下：

- `~/Codes/aw-webui/src/util/llm.ts`
- `~/Codes/aw-webui/src/components/LLMSummaryPanel.vue`
- `~/Codes/aw-webui/src/visualizations/LLMRhythmChart.vue`

### 已确认的问题

`src/util/llm.ts` 里当前逻辑是：

- 单日视图：`15m` buckets
- 多日/周/月视图：改成 `daily buckets` 或 `6h buckets`

也就是说：

- 时间跨度变大后，当前实现只是“换桶宽”
- 没有把多天数据 fold 回“标准 24h”
- 这正是用户前面指出的问题

`src/components/LLMSummaryPanel.vue` 里的 caption 文案也直接跟 `bucketMinutes` 绑定，所以用户会看到：

- `daily buckets`
- `6h buckets`

而不是：

- `avg 24h`
- `15m buckets`
- `N-day window`

`src/visualizations/LLMRhythmChart.vue` 还需要一起调整：

- 如果 rhythm 改成 fold 后的 `15m` 平均桶，y 轴建议上限也应该跟着变得更合理
- 否则曲线会显得过扁

## 原计划修法

原计划只改 `aw-webui` 这 3 个文件：

1. `src/util/llm.ts`
   把大跨度 rhythm 改成：
   - 单日：保持原始 24h 节奏
   - 多日/周/月/年：固定 fold 成 24h 内的 `15m` 桶
   - 每个桶按整个窗口的天数取均值

2. `src/components/LLMSummaryPanel.vue`
   把 caption 从：
   - `daily buckets`
   - `6h buckets`

   改成类似：
   - `avg 24h · 15m buckets · 30d window`
   - 如有平滑，再补 `· 1h smoothing`

3. `src/visualizations/LLMRhythmChart.vue`
   调整 tooltip 与 y 轴建议上限，使 `avg 24h` 视图可读

## 本次未完成的原因

- 当前会话的 writable workspace 只有 `~/Codes/aw-watcher-llm`
- `~/Codes/aw-webui` 在这个会话里属于越权写入目录
- 我两次发起写入授权时都被中断
- 因此没有把补丁真正写进 `aw-webui`

## 后续建议

最稳妥的收口顺序：

1. 先决定是否保留 `aw-watcher-llm/visualization/dist/` 这次探索性改动
2. 真正修 bug 时，只改 `aw-webui` 里的这 3 个文件：
   - `src/util/llm.ts`
   - `src/components/LLMSummaryPanel.vue`
   - `src/visualizations/LLMRhythmChart.vue`
3. 修完后在 `aw-webui` 里至少做一次针对性检查：
   - 打开月视图确认 x 轴回到 24h
   - caption 不再显示 `daily buckets`
   - tooltip 显示平均活跃时长而不是单日累计

## 补充说明

我还额外确认过一件事：

- `aw-webui` 当前源码里不存在 `foldedToDay`
- 说明我之前那两次对 `aw-webui` 的补丁尝试并没有成功写入

