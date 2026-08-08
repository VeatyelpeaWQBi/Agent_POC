"""AgentScope 2.0 AI Agent 程序入口（虾虾子）。

程序即服务：启动 agent service 后端（官方 create_app），通过 Swagger UI
（/docs）交互，自带原生 team/subagent 能力（只读 worker 调查员）。
agent 配置（人格 / 模型 / 凭证）来自 config.py。
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from config import load_config
from service_factory import build_app, build_storage, provision
from subagent_templates import ALL_SUBAGENT_TEMPLATES

# 服务监听地址与端口。
SERVE_HOST = "127.0.0.1"
SERVE_PORT = 8000


def serve() -> None:
    """启动 agent service 后端（uvicorn）。

    启动前先预置凭证 / 虾虾子 agent / 默认会话（幂等），保证服务端口
    的 agent 可直接对话；预置使用独立的临时 storage，app 绑定的 storage
    生命周期完全交给 FastAPI lifespan 管理（官方契约）。
    """
    try:
        config = load_config()
        # 装配服务后端，并注入 team/subagent 工具箱（原生模板）。
        app, _ = build_app(
            config,
            custom_subagent_templates=ALL_SUBAGENT_TEMPLATES,
        )

        async def _provision() -> None:
            async with build_storage(config) as tmp_storage:
                await provision(config, tmp_storage)

        asyncio.run(_provision())
    except Exception as exc:
        print(f"启动失败：{exc}")
        sys.exit(1)

    print(
        f"{config.agent_name} 服务已启动。\n"
        f"  Web UI:     http://{SERVE_HOST}:{SERVE_PORT}/\n"
        f"  API 文档(Swagger): http://{SERVE_HOST}:{SERVE_PORT}/docs\n"
        "按 Ctrl+C 停止。",
    )
    uvicorn.run(app, host=SERVE_HOST, port=SERVE_PORT)


if __name__ == "__main__":
    serve()
