"""AgentScope 2.0 命令行聊天 Agent 的程序入口。"""

from __future__ import annotations

import asyncio
from typing import Any

from approval import run_agent_turn
from agent_factory import build_agent
from config import load_config


EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit", "退出"}


def extract_text(message: Any) -> str:
    """从 AgentScope 消息的文本块中提取最终回答。"""

    text_parts: list[str] = []
    for block in message.content:
        if isinstance(block, str):
            text_parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            text_parts.append(str(getattr(block, "text", "")))
    return "".join(text_parts).strip()


async def chat_loop() -> None:
    """持续接收终端输入，并保留当前进程内的多轮对话上下文。"""

    config = load_config()
    agent = build_agent(config)

    print(
        f"{agent.name} 已启动。输入 /exit、/quit 或“退出”结束对话。\n"
        f"工作目录：{config.workspace}\n"
        f"模型：{config.model_card.label}（{config.model_name}，"
        f"状态 {config.model_card.status}，上下文 {config.model_card.context_size} tokens）\n"
        "安全策略：只读工具自动执行；命令、写入和编辑操作需要人工确认。",
    )

    while True:
        try:
            user_input = (await asyncio.to_thread(input, "\n你：")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n对话已结束。")
            return

        if not user_input:
            continue
        if user_input.lower() in EXIT_COMMANDS:
            print("对话已结束。")
            return

        try:
            reply = await run_agent_turn(agent, user_input)
            answer = extract_text(reply)
            print(f"\n{agent.name}：{answer or '[模型没有返回文本内容]'}")
        except KeyboardInterrupt:
            print("\n本轮请求已中断。")
        except Exception as exc:
            # CLI 边界：单次 API 或工具错误不应终止整个聊天程序。
            print(f"\n请求失败：{exc}")


if __name__ == "__main__":
    try:
        asyncio.run(chat_loop())
    except RuntimeError as exc:
        print(f"启动失败：{exc}")
