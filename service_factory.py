"""AgentScope 原生服务层工厂（单一职责：装配 + 预置）。

本模块负责把 ``agentscope.app`` 服务层装配起来，并预置"这一个 AI
AGENT"（虾虾子）到服务中——agent 配置（人格 / 模型 / 凭证）来自
``config.py``，主程序与预置共享同一份。

对外提供：

- :func:`build_storage`：创建服务后端 storage（路径基于 config.workspace）。
- :func:`build_app`：构造官方 ``create_app`` 的 FastAPI app 与底层
  storage，支持子代理模板注入；若 ``web_ui/frontend/dist`` 存在则把
  官方 Web UI 挂到根路径（SPA）。
- :func:`provision`：预置 DeepSeek 凭证 + 虾虾子 agent + 默认会话
  （幂等，可重复调用）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app import SubAgentTemplate, create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    AsyncSQLAlchemyStorage,
    ChatModelConfig,
    SessionConfig,
    SessionSource,
)
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.credential import DeepSeekCredential
from pydantic import SecretStr

from config import AppConfig

if TYPE_CHECKING:
    from fastapi import FastAPI

# 虾虾子的 agent 记录 id 与默认会话 id（固定，便于预置与引用）。
AGENT_ID = "xiashazi"
DEFAULT_SESSION_ID = "session-xiashazi"
# DeepSeek 凭证 id（与 ChatModelConfig.credential_id 对应）。
CREDENTIAL_ID = "deepseek-cred"
USER_ID = "local-user"

# 官方 Web UI 的前端路由前缀（SPA fallback 白名单）。
_SPA_PREFIXES = (
    "chat",
    "schedule",
    "channel",
    "credential",
    "mcp",
    "skill",
    "knowledge",
    "setup",
)


def build_storage(config: AppConfig) -> AsyncSQLAlchemyStorage:
    """创建服务后端的 storage 实例（未进入生命周期）。

    存储固定落在 ``config.workspace / agentscope_app.db``（与主程序同一
    工作目录；预置与 FastAPI lifespan 用同一路径，两处一致）。
    """
    config.workspace.mkdir(parents=True, exist_ok=True)
    # as_posix() 保证 Windows 下也生成规范的 sqlite URL。
    db_url = f"sqlite+aiosqlite:///{(config.workspace / 'agentscope_app.db').as_posix()}"
    return AsyncSQLAlchemyStorage(db_url, create_tables=True)


def build_app(
    config: AppConfig,
    custom_subagent_templates: list[SubAgentTemplate] | None = None,
) -> tuple["FastAPI", AsyncSQLAlchemyStorage]:
    """构造官方 FastAPI app 与底层 storage（官方唯一支持路径）。

    Args:
        config (`AppConfig`): 项目配置（人格 / 模型 / 权限）。
        custom_subagent_templates (`list[SubAgentTemplate] | None`):
            子代理模板注入点（与官方 sample 同款语义）。模板的**定义**
            属于 team/subagent 层，这里只负责透传给 create_app。

    Returns:
        ``(app, storage)`` —— app 供 uvicorn 启动；storage 归 FastAPI
        lifespan 管理，调用方**不要**手动进入/退出它的生命周期。
    """
    storage = build_storage(config)
    app = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=LocalWorkspaceManager(
            str(config.workspace / ".workspaces"),
        ),
        custom_subagent_templates=custom_subagent_templates or [],
    )
    _mount_web_ui(app, config)
    return app, storage


def _mount_web_ui(app: "FastAPI", config: AppConfig) -> None:
    """把官方 Web UI 构建产物（web_ui/frontend/dist）挂到根路径。

    - ``/assets/*``：静态资源（Vite 产物）；
    - 其余 GET：SPA fallback —— 前端路由（/chat、/setup 等）返回
      index.html，未知路径返回 404（保护 API 文档与未匹配路由）。

    若尚未构建前端（dist 不存在），静默跳过，服务仍以纯 API 可用；
    README 的"构建 Web UI"一节说明如何生成 dist。
    """
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    dist_dir = config.workspace / "web_ui" / "frontend" / "dist"
    index_html = dist_dir / "index.html"
    if not index_html.exists():
        print("未找到 Web UI 构建产物，跳过前端挂载；"
              "构建方式见 README『构建 Web UI』。")
        return

    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="ui-assets",
        )

    dist_root = dist_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str) -> "FileResponse":
        if path:
            # 目录穿越防护：候选文件必须解析后仍在 dist 内
            candidate = (dist_dir / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist_root):
                return FileResponse(str(candidate))
            # 精确按首段匹配前端路由，未知路径 404
            if path.split("/", 1)[0] not in _SPA_PREFIXES:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="Not Found")
        # SPA fallback：前端路由统一返回 index.html
        return FileResponse(str(index_html))


def _chat_model_config(config: AppConfig) -> ChatModelConfig:
    """把项目的 DeepSeek 配置映射为原生 ChatModelConfig。"""
    return ChatModelConfig(
        type="deepseek_credential",
        credential_id=CREDENTIAL_ID,
        model=config.model_name,
        parameters={
            "max_tokens": config.max_tokens,
            "thinking_enable": config.thinking,
        },
    )


async def provision(config: AppConfig, storage: AsyncSQLAlchemyStorage) -> None:
    """预置凭证 + 虾虾子 agent + 默认会话（幂等，可重复调用）。

    让服务端口的"这一个 AI AGENT"使用与主程序同一份
    config（人格 / 模型 / 凭证 / 权限），而不是靠 REST 手工重建。
    """
    # 1. DeepSeek 凭证
    await storage.upsert_credential(
        USER_ID,
        DeepSeekCredential(
            id=CREDENTIAL_ID,
            api_key=SecretStr(config.api_key),
            base_url=config.base_url,
        ),
    )

    # 2. 虾虾子 agent 记录
    await storage.upsert_agent(
        USER_ID,
        AgentRecord(
            id=AGENT_ID,
            user_id=USER_ID,
            source="user",
            data=AgentData(
                name=config.agent_name,
                system_prompt=config.system_prompt,
                context_config=ContextConfig(),
                react_config=ReActConfig(max_iters=config.max_iters),
            ),
        ),
    )

    # 3. 默认会话（绑定模型配置，Web UI 打开即可对话）
    await storage.upsert_session(
        USER_ID,
        AGENT_ID,
        SessionConfig(
            workspace_id=AGENT_ID,
            name="默认会话",
            chat_model_config=_chat_model_config(config),
        ),
        session_id=DEFAULT_SESSION_ID,
        source=SessionSource.USER,
    )
