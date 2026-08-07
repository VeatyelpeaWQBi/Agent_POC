"""处理工具审核，以及 AgentScope 的“思考—工具—结果”循环。

除了敏感工具审核，这里还实现了 L03 风格的 nag reminder（催促提醒）：
模型在单轮回复里连续 ``NAG_ROUNDS`` 轮推理都没有调用任何任务工具
（TaskCreate / TaskGet / TaskList / TaskUpdate）、且当前还有未完成任务时，
程序会向 AgentScope 上下文注入一条
``<reminder>Update your todos.</reminder>``，下一轮推理的模型就能看到。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    ModelCallEndEvent,
    RequireUserConfirmEvent,
    ToolCallStartEvent,
    ToolResultStartEvent,
    UserConfirmResultEvent,
)
from agentscope.message import HintBlock, Msg, ToolCallBlock, UserMsg

from task_broadcaster import TaskBroadcaster


APPROVE_WORDS = {"y", "yes", "是", "同意", "允许", "批准"}
DENY_WORDS = {"n", "no", "否", "拒绝", "不允许", "取消"}

# AgentScope 内置任务工具的四个名字，与 InjectionConfig.task_tool_names 默认值一致。
TASK_TOOL_NAMES = ("TaskCreate", "TaskGet", "TaskList", "TaskUpdate")

# L03 页面：模型连续 3 轮推理没有更新任务列表就注入催促提醒。
NAG_ROUNDS = 3
NAG_REMINDER = "<reminder>Update your todos.</reminder>"


def _has_uncompleted_tasks(agent: Agent) -> bool:
    """AgentScope 的 AgentState 里是否存在未完成任务。"""
    return any(
        getattr(task, "state", None) in ("pending", "in_progress")
        for task in agent.state.tasks_context.tasks
    )


def _pretty_tool_input(raw_input: str) -> str:
    """把模型生成的 JSON 参数格式化成人类容易阅读的形式。"""

    try:
        parsed = json.loads(raw_input)
    except (json.JSONDecodeError, TypeError):
        return raw_input
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _print_review_card(tool_call: ToolCallBlock) -> None:
    """在终端展示待审核工具及参数。"""

    print("\n" + "=" * 60)
    print("⚠ 检测到需要人工审核的工具调用")
    print(f"工具：{tool_call.name}")
    print("参数：")
    print(_pretty_tool_input(tool_call.input))
    print("=" * 60)


async def ask_for_approval(tool_call: ToolCallBlock) -> bool:
    """反复询问，直到主人明确批准或拒绝。"""

    _print_review_card(tool_call)

    while True:
        answer = (
            await asyncio.to_thread(
                input,
                "是否允许执行？[y=允许 / n=拒绝]：",
            )
        ).strip().lower()

        if answer in APPROVE_WORDS:
            print("已批准，本次工具调用将继续执行。")
            return True
        if answer in DENY_WORDS:
            print("已拒绝，本次工具调用不会执行。")
            return False

        print("请输入 y 或 n（也可以输入“允许”或“拒绝”）。")


async def _build_confirm_event(
    event: RequireUserConfirmEvent,
) -> UserConfirmResultEvent:
    """把人的审核选择转换成 AgentScope 能理解的事件。"""

    results: list[ConfirmResult] = []
    for tool_call in event.tool_calls:
        approved = await ask_for_approval(tool_call)
        results.append(
            ConfirmResult(
                confirmed=approved,
                tool_call=tool_call,
                # rules=None 表示只批准当前这一次，不永久放行同类操作。
                rules=None,
            ),
        )

    return UserConfirmResultEvent(
        reply_id=event.reply_id,
        confirm_results=results,
    )


async def run_agent_turn(
    agent: Agent,
    user_text: str,
    task_broadcaster: TaskBroadcaster | None = None,
) -> Msg:
    """执行完整的一轮对话，包括可能出现的多次工具调用。

    循环过程：
    用户消息 -> 模型思考 -> 工具请求 -> 人工审核 -> 工具结果 -> 模型继续思考

    AgentScope 在需要确认时会暂停回复。程序提交 UserConfirmResultEvent 后，
    同一个回复会从暂停的位置继续，直到得到最终 Msg。

    同时这里实现了 L03 风格的 nag reminder：跟踪模型每轮推理是否更新了
    任务列表，连续 NAG_ROUNDS 轮没有更新时向上下文注入催促提醒。

    若传入 ``task_broadcaster``，每次任务工具（TaskCreate/TaskGet/
    TaskList/TaskUpdate）执行完成后，会把当前任务状态渲染成快照广播给
    所有订阅渠道（终端 / 未来的 Web UI、远程通知等）。
    """

    # 每轮开始时让任务面板定稿，避免上一轮的残留在原地刷新时被错误覆盖。
    if task_broadcaster is not None:
        task_broadcaster.finalize()

    next_input: Any = UserMsg(name="user", content=user_text)

    while True:
        confirm_event: RequireUserConfirmEvent | None = None
        final_message: Msg | None = None
        rounds_since_todo = 0
        todo_used_this_round = False

        async for event_or_message in agent.reply_stream(
            next_input,
            yield_final_msg=True,
        ):
            if isinstance(event_or_message, RequireUserConfirmEvent):
                confirm_event = event_or_message

            elif isinstance(event_or_message, ToolCallStartEvent):
                # 模型每轮推理后发起的工具调用；记下本轮是否更新了任务。
                if event_or_message.tool_call_name in TASK_TOOL_NAMES:
                    todo_used_this_round = True

            elif (
                isinstance(event_or_message, ToolResultStartEvent)
                and event_or_message.tool_call_name in TASK_TOOL_NAMES
                and task_broadcaster is not None
            ):
                # 任务工具已执行完成（此时 AgentState.tasks_context
                # 已更新），把状态快照广播给所有订阅渠道。
                await task_broadcaster.broadcast(agent.state.tasks_context)

            elif isinstance(event_or_message, ModelCallEndEvent):
                # 一轮推理结束：检查是否连续多轮没有更新任务列表。
                rounds_since_todo = (
                    0 if todo_used_this_round else rounds_since_todo + 1
                )
                todo_used_this_round = False
                if (
                    rounds_since_todo >= NAG_ROUNDS
                    and _has_uncompleted_tasks(agent)
                ):
                    # 注入催促提醒（与 AgentScope 内部运行时状态注入
                    # 同机制），下一轮推理的模型就能看到。
                    agent.state.append_context(
                        agent.name,
                        [HintBlock(hint=NAG_REMINDER)],
                    )
                    rounds_since_todo = 0

            elif isinstance(event_or_message, Msg):
                final_message = event_or_message

        if confirm_event is not None:
            # 人工审核卡片即将打印到终端：先让任务面板定稿，
            # 避免后续原地刷新覆盖审核提示。
            if task_broadcaster is not None:
                task_broadcaster.finalize()
            next_input = await _build_confirm_event(confirm_event)
            continue

        if final_message is None:
            raise RuntimeError("Agent 没有返回最终消息。")

        # 模型最终回复即将由 main.py 打印：让任务面板定稿。
        if task_broadcaster is not None:
            task_broadcaster.finalize()
        return final_message
