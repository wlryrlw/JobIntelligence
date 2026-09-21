# 阶段四离线演示脚本

这份脚本用于面试演示或录屏准备；所有数据来自合成 fixture，不需要 BOSS 登录态、实时爬取或真实模型 Key。

## 1. 准备岗位数据

```bash
cd /Users/w/code/OfferAgent
make setup
```

## 2. 启动服务

分别启动后端和前端：

```bash
make backend
make frontend
```

打开 <http://127.0.0.1:5173> 后，可以登录本地演示账号并执行已有的自然语言查询；结果支持澄清、执行轨迹、Markdown 报告、ECharts 图表和表格导出。

## 3. 展示任务 SSE

前端右侧的“离线演示任务”面板会创建阶段 0 任务并订阅事件。也可以用 curl 直接验证协议：

```bash
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H 'content-type: application/json' \
  -d '{"text":"阶段四离线演示","session_id":"stage4-demo"}'

curl -N -H 'Accept: text/event-stream' \
  http://127.0.0.1:8000/api/tasks/<task_id>/events

curl http://127.0.0.1:8000/api/tasks/<task_id>
```

录屏时重点展示 `task.created → task.started → step.started/heartbeat/step.completed → task.completed` 的顺序，以及断开后用 `Last-Event-ID` 重连时只收到后续事件。

## 4. 展示评测和消融

```bash
make eval
make eval-ablation
```

结果会写入被 Git 忽略的 `data/local/eval/`。可以对比纠错开启/关闭和 few-shot 开启/关闭时的最终准确率、恢复率、尝试次数与 token 估算。
