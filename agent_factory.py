"""创建模型、工具箱和 AgentScope Agent。"""

from __future__ import annotations

from agentscope.agent import Agent, InjectionConfig
from agentscope.credential import DeepSeekCredential
from agentscope.model import DeepSeekChatModel
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionContext,
    PermissionMode,
    PermissionRule,
)
from agentscope.state import AgentState
from agentscope.tool import Edit, Glob, Grep, PowerShell, Read, Toolkit, Write
from pydantic import SecretStr

from config import AppConfig


READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
SENSITIVE_TOOLS = ("PowerShell", "Write", "Edit")

# 运行时状态注入的时间刷新间隔。InjectionConfig.time_interval 的单位是
# 小时：1/60 小时 = 1 分钟，即距上次注入超过 1 分钟就重新注入一次
# 当前时间与时区，让模型始终知道"现在几点"。
TIME_INJECTION_INTERVAL_HOURS = 1 / 60


def _rule(tool_name: str, behavior: PermissionBehavior) -> PermissionRule:
    """创建一条覆盖该工具所有调用的权限规则。"""

    return PermissionRule(
        tool_name=tool_name,
        rule_content=None,
        behavior=behavior,
        source="project",
    )


def build_permission_context(config: AppConfig) -> PermissionContext:
    """定义工具权限。

    DEFAULT 模式下，没有命中 allow 规则的操作默认需要确认。这里仍然把
    只读和敏感工具分别写清楚，便于初学者直接看懂安全边界。
    """

    allow_rules = {
        name: [_rule(name, PermissionBehavior.ALLOW)]
        for name in READ_ONLY_TOOLS
    }
    ask_rules = {
        name: [_rule(name, PermissionBehavior.ASK)]
        for name in SENSITIVE_TOOLS
    }

    workspace_text = str(config.workspace)
    return PermissionContext(
        mode=PermissionMode.DEFAULT,
        working_directories={
            workspace_text: AdditionalWorkingDirectory(
                path=workspace_text,
                source="project",
            ),
        },
        allow_rules=allow_rules,
        ask_rules=ask_rules,
    )


def build_agent(config: AppConfig) -> Agent:
    """根据配置创建一个可以调用工具的 Agent。

    模型构造由 config.model_card（模型卡片）驱动：
    - context_size 来自卡片，供上下文压缩使用；
    - max_tokens / thinking 是 .env 中的可选配置，已按卡片上限校验。
    """

    parameters = DeepSeekChatModel.Parameters(
        max_tokens=config.max_tokens,
        thinking_enable=config.thinking,
    )

    model = DeepSeekChatModel(
        # AgentScope 将密钥声明为 SecretStr：既满足类型检查，也避免日志意外泄露密钥。
        credential=DeepSeekCredential(
            api_key=SecretStr(config.api_key),
            base_url=config.base_url,
        ),
        model=config.model_name,
        parameters=parameters,
        stream=False,
        # 上下文窗口大小来自模型卡片，而不是写死在代码里。
        context_size=config.model_card.context_size,
    )

    # 当前项目运行在 Windows，因此使用 PowerShell，而不是 Bash。
    toolkit = Toolkit(
        tools=[
            Read(),
            Glob(),
            Grep(),
            PowerShell(cwd=config.workspace),
            Write(),
            Edit(),
        ],
    )

    state = AgentState(
        permission_context=build_permission_context(config),
    )

    # 运行时状态注入：把当前时间与时区注入上下文，让模型感知"现在几点"。
    # inject_runtime_state 默认开启；time_interval=1/60 小时 = 每 1 分钟刷新。
    # 注意：注入不是临时的，会随刷新次数累积到上下文中。
    injection_config = InjectionConfig(
        timezone=config.timezone,
        time_interval=TIME_INJECTION_INTERVAL_HOURS,
    )

    return Agent(
        name=config.agent_name,
        system_prompt=config.system_prompt,
        model=model,
        toolkit=toolkit,
        state=state,
        injection_config=injection_config,
    )
