- 链路：
1. 请求进来 → trace_middleware 生成 trace_id=c3d8...，绑进上下文。
2. 建 run → create_run 从上下文拿到 trace_id，生成 run_id，store.insert 往 runs 表插一行 status=pending，然后后台启动 worker。
3. worker 开跑 → 把 status 改成 running，用 context.bind_run 绑上 run/trace/thread/user 四个 id。
4. 每调一次模型/工具 → trace.py 开一条 span 塞进内存采集器，结束时回填耗时/token/成本，同时 get_observability_logger("prism.llm").info("llm_call_finished", ...) 打一条结构化日志（这条日志会被 _inject_context 自动带上 trace/run/span，然后文件 + 队列双写）。
5. run 结束 → _finalize 把 status 改成 finished、回填 finished_at/message_count、把思考链事件写进 runs.events、把 span 从内存 flush 进 spans 表、用 summarize_spans 算总 token/成本回写 runs 表。
6. db_sink 后台线程 → 把队列里的日志批量写进 logs 表。
- 观测台 → 打开 /observability，后端 observability.py 路由直接查这三张表 + 实时 SQL 聚合，前端渲染。