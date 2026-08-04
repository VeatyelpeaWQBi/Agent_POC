"""处理工具审核，以及 AgentScope 的“思考—工具—结果”循环。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import Msg, ToolCallBlock, UserMsg


APPROVE_WORDS = {"y", "yes", "是", "同意", "允许", "批准"}
DENY_WORDS = {"n", "no", "否", "拒绝", "不允许", "取消"}


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


async def run_agent_turn(agent: Agent, user_text: str) -> Msg:
    """执行完整的一轮对话，包括可能出现的多次工具调用。

    循环过程：
    用户消息 -> 模型思考 -> 工具请求 -> 人工审核 -> 工具结果 -> 模型继续思考

    AgentScope 在需要确认时会暂停回复。程序提交 UserConfirmResultEvent 后，
    同一个回复会从暂停的位置继续，直到得到最终 Msg。
    """

    next_input: Any = UserMsg(name="user", content=user_text)

    while True:
        confirm_event: RequireUserConfirmEvent | None = None
        final_message: Msg | None = None

        async for event_or_message in agent.reply_stream(
            next_input,
            yield_final_msg=True,
        ):
            if isinstance(event_or_message, RequireUserConfirmEvent):
                confirm_event = event_or_message
            elif isinstance(event_or_message, Msg):
                final_message = event_or_message

        if confirm_event is not None:
            next_input = await _build_confirm_event(confirm_event)
            continue

        if final_message is None:
            raise RuntimeError("Agent 没有返回最终消息。")

        return final_message
