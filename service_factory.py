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
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionContext,
    PermissionMode,
)
from agentscope.state import AgentState
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

    DB（会话/凭证/团队记录，含明文 api_key）固定落在
    ``config.agent_data_dir / agentscope_app.db``——非临时目录，属于
    用户数据，任何测试/验证都不得删除；预置与 FastAPI lifespan 用
    同一路径，两处一致。
    """
    config.agent_data_dir.mkdir(parents=True, exist_ok=True)
    # as_posix() 保证 Windows 下也生成规范的 sqlite URL。
    db_url = f"sqlite+aiosqlite:///{(config.agent_data_dir / 'agentscope_app.db').as_posix()}"
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
    from fastapi.middleware import Middleware
    from fastapi.middleware.cors import CORSMiddleware

    storage = build_storage(config)
    workspace_manager = LocalWorkspaceManager(
        str(config.agent_workspace),
    )
    app = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=workspace_manager,
        custom_subagent_templates=custom_subagent_templates or [],
        # Web UI 前端与后端可能不同源（如 dev 端口 5173 / 填写的地址），
        # 官方 create_app 不内置 CORS，这里放开跨域供前端探测与调用。
        extra_middlewares=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            ),
        ],
    )
    _mount_health(app)
    _mount_workspace_status(app, storage, workspace_manager)
    _mount_web_ui(app, config)
    return app, storage


def _mount_health(app: "FastAPI") -> None:
    """提供 GET /health——官方 Web UI 设置页的服务器探测端点。

    官方前端 healthApi.check 探测 `GET /health`，要求 200 + JSON
    `{status, version, components}` 才认为地址正确；本版本 create_app
    没有内置 /health，这里补上（后端确已就绪即返回 ok）。
    """

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {
            "status": "ok",
            "version": "2.0.6dev",
            "components": {"storage": "ok", "message_bus": "ok"},
        }


def _mount_workspace_status(
    app: "FastAPI",
    storage: AsyncSQLAlchemyStorage,
    workspace_manager,
) -> None:
    """补 GET /workspace/status——前端定期轮询的工作区状态端点。

    官方前端（main 分支）会按 UI 节奏轮询 ``GET /workspace/status``，
    返回会话工作目录与 git 状态；2.0.6dev 后端没有该端点，导致每次
    轮询 404 刷日志。这里补齐前端期望的结构：非 git 仓库 / git 不可用
    / 超时均返回 ``git: null``（前端隐藏徽标）。
    """
    import asyncio
    import subprocess
    from fastapi import Header, HTTPException

    def _git_status(workdir: str) -> dict | None:
        """同步解析 git 状态；非仓库/失败/超时返回 None。"""

        def run(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", workdir, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )

        try:
            probe = run("rev-parse", "--is-inside-work-tree")
            if probe.returncode != 0 or probe.stdout.strip() != "true":
                return None

            branch = (
                run("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
                or None
            )
            head = run("rev-parse", "HEAD").stdout.strip() or None

            ahead = behind = None
            r = run("rev-list", "--left-right", "--count", "@{upstream}...HEAD")
            if r.returncode == 0:
                parts = r.stdout.split()
                if len(parts) == 2:
                    behind, ahead = int(parts[0]), int(parts[1])

            insertions = deletions = 0
            for cached in ([], ["--cached"]):
                r = run("diff", "--numstat", *cached)
                for line in r.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                        insertions += int(parts[0])
                        deletions += int(parts[1])

            staged = unstaged = untracked = conflicted = 0
            r = run("status", "--porcelain")
            for line in r.stdout.splitlines():
                if len(line) < 3:
                    continue
                x, y = line[0], line[1]
                if x in "U" or y in "U" or x == y in "AD":
                    conflicted += 1
                elif x not in " ?":
                    staged += 1
                if y not in " ?":
                    unstaged += 1
                if x == "?" or y == "?":
                    untracked += 1

            return {
                "branch": branch,
                "head": head,
                "ahead": ahead,
                "behind": behind,
                "insertions": insertions,
                "deletions": deletions,
                "staged": staged,
                "unstaged": unstaged,
                "untracked": untracked,
                "conflicted": conflicted,
            }
        except Exception:
            return None

    @app.get("/workspace/status", include_in_schema=False)
    async def workspace_status(
        agent_id: str,
        session_id: str,
        x_user_id: str = Header(...),
    ) -> dict:
        session = await storage.get_session(x_user_id, agent_id, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        workspace = await workspace_manager.get_workspace(
            x_user_id,
            agent_id,
            session_id,
            session.config.workspace_id,
        )
        workdir = workspace.workdir
        git = await asyncio.to_thread(_git_status, workdir)
        return {"workdir": workdir, "cwd": workdir, "git": git}


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
    #    YOLO 模式：会话权限切到 ACCEPT_EDITS，项目根目录（config.workspace）
    #    内文件读写（Write/Edit）自动放行；命令行（PowerShell）与
    #    工作区外敏感操作仍走审核。
    session_state: "AgentState | None" = None
    if config.yolo:
        session_state = AgentState(
            permission_context=PermissionContext(
                mode=PermissionMode.ACCEPT_EDITS,
                working_directories={
                    str(config.workspace): AdditionalWorkingDirectory(
                        path=str(config.workspace),
                        source="yolo",
                    ),
                },
            ),
        )
    await storage.upsert_session(
        USER_ID,
        AGENT_ID,
        SessionConfig(
            workspace_id=AGENT_ID,
            name="默认会话",
            chat_model_config=_chat_model_config(config),
        ),
        state=session_state,
        session_id=DEFAULT_SESSION_ID,
        source=SessionSource.USER,
    )
