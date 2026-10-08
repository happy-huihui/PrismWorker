<h1 align="center">PrismWorker</h1>

<p align="center"><b>面向多步骤复杂任务的 Harness Agent</b></p>

基于 LangGraph 实现 ReAct 编排，提供子代理协作、跨会话持久记忆、沙盒隔离执行、Skill/Tool 装配与 Middleware 链式上下文治理。拥有文件、代码、图片产物生成能力。

<p align="center">
<img src="https://raw.githubusercontent.com/happy-huihui/PrismWorker/main/assets/zero.png" alt="PrismWorker 工作台界面" />
</p>

## NextPlan

- [x] SSE 连接速度优化
- [x] 前端 UI 重构
- [x] 会话轮次快速索引
- [x] 重构提示词模板管理
- [x] 用户 Agent.md 支持
- [x] 用户上传 skill（私有技能）
- [ ] 树形对话
  - [ ] 子卡片：深挖背景知识
  - [ ] 关联卡片：横向对比发散
  - [ ] 分支卡片：继承上下文另起炉灶
- [ ] 记忆多路检索
- [x] 完整日志观测链路
- [x] 本地测评 + langsmith观测

## 项目结构

```
PrismWorker
├── .prism-worker/        # 运行时数据
│   ├── data/             # checkpoints.db 与鉴权密钥
│   ├── logs/             # 运行日志（app.log）
│   ├── users/            # 每用户数据目录
│   │   └── <user_id>/
│   │       ├── agent.md      # 用户自定义指令
│   │       ├── skills/       # 用户上传的 Skill
│   │       └── threads/      # 会话线程数据（uploads / outputs / workspace）
│   └── users.json        # 用户索引
|
├── app/                  # FastAPI 服务端
│   ├── api/              # 路由与请求处理（含观测中台 /observability + trace 中间件）
│   ├── core/             # 鉴权、配置、产物与上传管理
│   └── runner.py         # 启动入口
├── assets/               # 静态资源
├── config.yaml           # 主配置（模型 / 工具 / 观测 / 评测）
├── langgraph.json        # LangGraph 图导出配置（langgraph dev / 评测用）
|
├── frontend/             # React 前端（聊天页 + 观测中台）
│   └── src/              
|
├── harness/              # Agent 运行时
│   ├── agents/           # 主代理编排
│   │   ├── lead_agent/   # Lead Agent
│   │   └── middlewares/  # 中间件
│   ├── subagents/        # 子代理协作
│   ├── prompt/           # 提示词模板库
│   ├── skills/           # Skill 装配
│   ├── tools/            # 工具装配
│   ├── memory/           # 跨会话持久记忆
│   ├── sandbox/          # 沙箱
│   ├── models/           # 模型注册与路由（MiMo / OpenAI / DeepSeek）
│   ├── runtime/          # 运行时环境
│   ├── observability/    # 日志观测
│   ├── eval/             # 离线评测
│   ├── uploads/          # 文件上传管理
│   ├── community/        # 社区扩展工具
│   ├── utils/            # 通用工具
│   └── config/           # 配置加载
|
└── skills/               # Skill 公开技能
    └── public/           
```

## 技术栈

| 层   | 技术                                            |
|------|-----------------------------------------------|
| 编排 | LangGraph / LangChain（Python ≥ 3.12）          |
| 后端 | FastAPI + Uvicorn，SSE 实时事件流                   |
| 模型 | MiMo / OpenAI / DeepSeek / Doubao-Seedream           |
| 记忆 | SQLite + langgraph-checkpoint-sqlite + FTS5   |
| 沙箱 | Docker                                        |
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS 4 |

## 快速开始

```bash
# 1. 后端
python -m venv .venv && .venv\Scripts\activate    # Windows；Linux/macOS 用 source .venv/bin/activate
uv sync                                          # 或 pip install -e .
cp .env.example .env                             # 填入模型 API Key
python -m app.runner                             # http://127.0.0.1:8000

# 2. 前端
cd frontend
pnpm install && pnpm dev                         # http://localhost:5173

# 3. 评测（可选）：离线评测（需先在 .env 配好 LANGSMITH_API_KEY）
python -m harness.eval --timeout 120
```
