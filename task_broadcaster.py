"""任务状态广播：状态变化 → 渲染快照 → 推送给所有订阅渠道。

把"追踪任务状态变化"与"把状态展示给谁"彻底解耦：

- :class:`TaskStateRenderer`：把 ``AgentState.tasks_context`` 渲染成人类
  可读文本（L03 风格的 ``[ ]`` / ``[>]`` / ``[x]`` 标记 + 完成计数）。
- :class:`TaskChannel`：展示渠道的抽象接口。当前只有一个实现
  :class:`TerminalTaskChannel`（打印到终端）；以后要接入 Web UI、远程
  消息通知等，只需新增一个 ``TaskChannel`` 子类并 ``subscribe``，广播
  器与 approval.py 的触发逻辑完全不用改。
- :class:`TaskBroadcaster`：维护订阅者列表，任务状态每次变化后把渲染
  快照广播给所有订阅渠道。没有订阅者时静默跳过，不影响主流程。

触发时机不在本模块：approval.py 的事件循环在检测到任务工具
（TaskCreate/TaskUpdate/TaskList/TaskGet）执行完成后调用
``await broadcaster.broadcast(agent.state.tasks_context)``。
"""

from __future__ import annotations

import ctypes
import os
import sys
from abc import ABC, abstractmethod

from agentscope.state import TaskContext

# 状态标记，与 AI Agent Learning L03 页面一致。
_MARKERS = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
}

# Windows 控制台启用 VT 转义后，\033[A（上移）/ \033[K（清行）才生效。
_ENABLE_VT = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def _enable_vt() -> None:
    """Windows 下为 stdout 控制台启用 VT 处理（Windows 10+）。"""
    if os.name == "nt" and sys.stdout.isatty():
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | _ENABLE_VT)
        except (AttributeError, OSError):
            pass


class TaskStateRenderer:
    """把任务列表渲染成人类可读的进度文本。"""

    @staticmethod
    def render(tasks: list) -> str:
        """渲染 ``tasks``（AgentState.tasks_context.tasks 的列表）。

        Args:
            tasks (`list`): 一组 agentscope.state.Task。

        Returns:
            `str`: 渲染后的多行文本；空列表返回 ``"No tasks."``。
        """
        if not tasks:
            return "No tasks."

        lines = []
        for task in tasks:
            marker = _MARKERS.get(task.state, "[?]")
            line = f"{marker} #{task.id}: {task.subject}"
            if task.owner:
                line += f" (owner: {task.owner})"
            if task.blocked_by:
                line += f" [blocked by {', '.join(task.blocked_by)}]"
            lines.append(line)

        done = sum(1 for task in tasks if task.state == "completed")
        lines.append(f"\n({done}/{len(tasks)} completed)")
        return "\n".join(lines)


class TaskChannel(ABC):
    """任务面板的展示渠道抽象。

    新增渠道（Web UI / 远程通知 / 日志文件…）时继承并实现
    :meth:`emit`，然后把实例传给 :meth:`TaskBroadcaster.subscribe`。

    若渠道支持"原地刷新"语义（如终端面板），可覆写 :meth:`finalize`，
    在面板之外的其他输出即将出现前被调用，用于结束当前面板的生命周期。
    """

    @abstractmethod
    async def emit(self, text: str) -> None:
        """向该渠道输出一份任务状态快照。

        Args:
            text (`str`): :class:`TaskStateRenderer` 渲染好的文本。
        """

    def finalize(self) -> None:
        """结束当前面板生命周期，后续输出接在面板之后（默认无操作）。"""


class TerminalTaskChannel(TaskChannel):
    """终端渠道：把任务面板打印到 stdout，并在同一位置原地刷新。

    在交互式终端（``stdout.isatty()``）里使用 ANSI 转义序列实现固定位置
    刷新：每次收到快照都上移到上一次面板的顶部、逐行清行后重绘，而不是
    累积打印。调用 :meth:`finalize` 后（面板外的输出即将出现，例如模型
    回复或人工审核卡片），面板定稿，后续输出接在它下方；下一次
    :meth:`emit` 会重新开一块新面板。

    输出被重定向到文件/管道（非 TTY）时自动退化为逐块打印，不写
    ANSI 序列，保证日志和测试环境干净。
    """

    PANEL_TITLE = "── 任务面板 ──────────────────────"
    PANEL_END = "──────────────────────────────────"

    def __init__(self) -> None:
        self._last_height = 0
        self._use_ansi = sys.stdout.isatty()
        if self._use_ansi:
            _enable_vt()

    def _build_panel(self, text: str) -> str:
        return f"{self.PANEL_TITLE}\n{text}\n{self.PANEL_END}"

    async def emit(self, text: str) -> None:
        panel = self._build_panel(text)
        lines = panel.splitlines()

        if self._use_ansi and self._last_height > 0:
            # 上移到旧面板顶部，逐行清行后重绘。
            sys.stdout.write(f"\033[{self._last_height}A")
            for line in lines:
                sys.stdout.write("\033[K" + line + "\n")
            # 新面板比旧面板矮时，清掉旧面板多出的行，并把光标移回
            # 新面板的末尾。
            if self._last_height > len(lines):
                for _ in range(self._last_height - len(lines)):
                    sys.stdout.write("\033[K\n")
                sys.stdout.write(
                    f"\033[{self._last_height - len(lines)}A",
                )
        else:
            # 首次绘制或非交互终端：直接打印，不写 ANSI 序列。
            sys.stdout.write(panel + "\n")

        sys.stdout.flush()
        self._last_height = len(lines)

    def finalize(self) -> None:
        """面板定稿：高度归零，后续输出接在面板下方。"""
        self._last_height = 0


class TaskBroadcaster:
    """维护订阅渠道并在任务状态变化时广播渲染快照。"""

    def __init__(self, renderer: TaskStateRenderer | None = None) -> None:
        self._channels: list[TaskChannel] = []
        self._renderer = renderer or TaskStateRenderer()

    def subscribe(self, channel: TaskChannel) -> None:
        """订阅一个展示渠道。"""
        if channel not in self._channels:
            self._channels.append(channel)

    def unsubscribe(self, channel: TaskChannel) -> None:
        """取消订阅。"""
        if channel in self._channels:
            self._channels.remove(channel)

    @property
    def channel_count(self) -> int:
        """当前订阅的渠道数量。"""
        return len(self._channels)

    async def broadcast(self, task_context: TaskContext) -> None:
        """把任务状态渲染成快照并推送给所有订阅渠道。

        没有订阅者时直接返回，不产生任何输出。

        Args:
            task_context (`TaskContext`): AgentState.tasks_context，
                其 ``tasks`` 属性即当前任务列表。
        """
        if not self._channels:
            return
        text = self._renderer.render(task_context.tasks)
        for channel in self._channels:
            await channel.emit(text)

    def finalize(self) -> None:
        """通知所有渠道结束当前面板生命周期。

        在面板之外的其他终端输出（人工审核卡片、模型回复等）即将出现
        前调用，让支持原地刷新的渠道先定稿，避免后续输出被错误覆盖。
        """
        for channel in self._channels:
            channel.finalize()
