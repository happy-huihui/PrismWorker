<div align="center">

# PrismWorker

</div>

面向多步骤复杂任务的 Harness Agent。基于 LangGraph 实现 ReAct 编排，提供子代理协作、跨会话持久记忆、沙盒隔离执行、Skill/Tool 装配与 Middleware 链式上下文治理。拥有文件、代码、图片产物生成能力。

![PrismWorker 工作台界面](assets/example.png)

## NextPlan

- [x] SSE 连接速度优化
- [x] 前端 UI 重构
- [x] 会话轮次快速索引
- [x] 重构提示词模板管理
- [ ] 用户 Agent.md 支持
- [ ] 支持当前会话使用临时 skill
- [ ] 树形对话
  - [ ] 子卡片：深挖背景知识
  - [ ] 关联卡片：横向对比发散
  - [ ] 分支卡片：继承上下文另起炉灶
- [ ] 记忆多路检索
- [ ] 完整日志观测链路
- [ ] 完整 langsmith 测评

## 项目结构

```
PrismWorker
├── .prism-worker/        # 运行时数据（自动生成，勿提交版本库）
│   ├── data/             # SQLite 检查点（checkpoints.db）与鉴权密钥
│   ├── logs/             # 运行日志（app.log）
│   ├── users/            # 用户数据目录
│   └── users.json        # 用户索引
├── app/                  # FastAPI 服务端
│   ├── api/              # 路由与请求处理
│   ├── core/             # 核心逻辑与启动引导
│   └── runner.py         # 启动入口
├── assets/               # 静态资源
├── frontend/             # React 前端
│   └── src/              
├── harness/              # Agent 运行时
│   ├── agents/           # 主代理编排
│   ├── subagents/        # 子代理协作
│   ├── middleware/       # 链式中间件治理
│   ├── skills/           # Skill 装配
│   ├── tools/            # 工具装配
│   ├── memory/           # 记忆
│   ├── sandbox/          # 沙箱
│   ├── models/           # 模型注册与路由
│   ├── config/           # 配置加载
│   └── runtime/          # 运行时环境
└── skills/               # Skill 文档库
    └── public/           # 公开技能（code-documentation / deep-research 等）
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
```
