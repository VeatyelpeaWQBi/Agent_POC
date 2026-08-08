"""team/subagent 工具箱配置（单一职责：定义子代理模板）。

本模块只定义子代理模板（原生 ``SubAgentTemplate``），不负责装配——
装配由 ``service_factory.build_app`` 通过 ``custom_subagent_templates``
参数接收，入口 ``main.py`` 启动时总是注入。

worker 模板权限设计：**真正只读**。服务形态没有人工确认界面，若 worker
继承 leader 的写权限（PowerShell / Write / Edit 为 ASK），一旦调用会卡住
等确认。因此这里显式 deny 写工具，allow 只读工具——与"只读调查员"的
描述一致，也避免无界面卡死。
"""

from __future__ import annotations

from agentscope.agent import ReActConfig
from agentscope.app import SubAgentTemplate
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionMode,
    PermissionRule,
)

from config import AppConfig


def _rule(tool_name: str, behavior: PermissionBehavior) -> PermissionRule:
    """构造一条覆盖该工具所有调用的权限规则。"""
    return PermissionRule(
        tool_name=tool_name,
        rule_content=None,
        behavior=behavior,
        source="project",
    )


# worker 只读：allow 只读工具，deny 写/执行工具（服务形态下无确认界面，
# deny 而不是 ask，避免 worker 卡住）。
_READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
_DENY_TOOLS = ("Bash", "PowerShell", "Write", "Edit")

_WORKER_PERMISSION = PermissionContext(
    mode=PermissionMode.DEFAULT,
    allow_rules={
        name: [_rule(name, PermissionBehavior.ALLOW)]
        for name in _READ_ONLY_TOOLS
    },
    deny_rules={
        name: [_rule(name, PermissionBehavior.DENY)]
        for name in _DENY_TOOLS
    },
)

def build_templates(config: AppConfig) -> list[SubAgentTemplate]:
    """构造全部子代理模板（装配时传入 create_app）。

    worker 的 ``react_config.max_iters`` 与主 agent 对齐（读
    ``AGENT_MAX_ITERS``，默认 60）——若不配置，SubAgentTemplate 走
    AgentScope 默认 20，长任务（如多轮小说创作）会先于主 agent 触顶
    中断（日志：``exceeds the max iteration numbers 20``）。
    """
    return [
        SubAgentTemplate(
            type="worker",
            description=(
                "A read-only researcher subagent: explores files and "
                "reports a concise summary. Cannot modify files or run "
                "commands."
            ),
            system_prompt_template=(
                "You are {member_name}, a worker in team '{team_name}' led "
                "by {leader_name}.\n\nTeam purpose: {team_description}\n\n"
                "Your role: {member_description}\n\n"
                "You are read-only. Complete the task you are given, then "
                "use TeamSay to report a concise summary to the leader."
            ),
            # 模板自带只读权限并覆盖 leader 模式：worker 永远不能写文件/
            # 执行命令。
            permission_context=_WORKER_PERMISSION,
            override_leader_mode=True,
            extend_leader_permission_rules=False,
            extend_leader_working_directories=True,
            # 与主 agent 相同的 ReAct 迭代上限，避免长任务先触顶中断。
            react_config=ReActConfig(max_iters=config.max_iters),
        )
    ]
