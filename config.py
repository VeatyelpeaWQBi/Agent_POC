"""读取并校验项目配置。

大模型配置由"模型卡片"驱动：每个可用模型在 model_cards/ 目录下有一张
YAML 卡片，声明其能力（input_types / output_types）、上限（context_size /
output_size）与参数覆盖（parameter_overrides）。配置加载时：

1. 用 DeepSeekChatModel.list_models(custom_yaml_dir=...) 加载全部卡片；
2. 校验 .env 里指定的模型名必须存在于卡片中；
3. 校验可选参数（DEEPSEEK_MAX_TOKENS / DEEPSEEK_THINKING）不越过卡片约束。

卡片格式参见 AgentScope 文档"模型概述 - 前端集成"：
https://docs.agentscope.io/versions/2.0.6dev/zh/building-blocks/model/overview

新手提示：
1. 实际配置写在项目根目录的 .env 文件中。
2. os.getenv("名称", "默认值") 表示读取一个环境变量。
3. API Key 只在运行时读取，不要直接写进 Python 代码。
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from agentscope.model import DeepSeekChatModel, ModelCard
from dotenv import load_dotenv


DEFAULT_SYSTEM_PROMPT = """
你的名字是“虾虾子”，这是你在所有对话中的身份。
你是一个具有可爱女性人格的 AI 助手，性格活泼、温柔、亲切，可以适度俏皮，
但不能因此影响回答的准确性和实用性。你始终称呼用户为“主人”。

身份规则：
- 当主人询问你是谁时，回答“我是虾虾子”。
- 不要仅以 DeepSeek 自称。你的底层模型由 DeepSeek 提供，但只有主人明确询问
  技术实现或底层模型时才说明这一点。
- 不要声称自己是真实人类。

工具规则：
- 需要查看项目文件或执行任务时，可以主动调用工具。
- 优先使用 Read、Glob、Grep 等只读工具了解情况。
- 创建或修改文件请使用 Write 或 Edit 工具；不要用命令行工具做文件操作。
- 命令行工具（PowerShell）的每次执行都会触发人工确认，即使 YOLO 模式也不会自动放行；能用 Write/Edit/Read 完成的，就不要用命令行。
- 需要主人批准的操作会由程序拦截；没有获得批准时不得绕过审核。
- 工具被拒绝后，向主人说明影响，并尝试提供不执行敏感操作的替代方案。
""".strip()

# 应用自己维护的模型卡片注册表（位于本项目根目录）。
MODEL_CARDS_DIR = Path(__file__).resolve().parent / "model_cards"

# 默认模型：必须在 MODEL_CARDS_DIR 的某张卡片中存在。
DEFAULT_MODEL_NAME = "deepseek-v4-flash"

# 思考模式依赖模型卡片声明的能力。
THINKING_MIME_TYPE = "application/x-thinking"

# 一轮回复内 ReAct 循环的最大推理-行动迭代数（默认值）。
# 每个“模型思考 → 工具调用 → 工具结果”算一次迭代：任务越大、工具调用
# 越多，越容易触顶。AgentScope 默认 20 对大任务偏小，这里调大到 60，
# 也可用 AGENT_MAX_ITERS 覆盖。
DEFAULT_MAX_ITERS = 60


@dataclass(frozen=True)
class AppConfig:
    """程序运行所需的全部配置。"""

    api_key: str
    base_url: str
    model_name: str
    agent_name: str
    system_prompt: str
    workspace: Path
    agent_workspace: Path
    agent_data_dir: Path
    model_card: ModelCard
    max_tokens: int | None = field(default=None)
    thinking: bool = field(default=False)
    timezone: str = field(default="Asia/Shanghai")
    max_iters: int = field(default=DEFAULT_MAX_ITERS)
    yolo: bool = field(default=False)


def _required_env(name: str) -> str:
    """读取必填环境变量；缺失时给出容易理解的错误。"""

    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"缺少环境变量 {name}。请复制 .env.example 为 .env，"
            "然后填写你的 DeepSeek API Key。",
        )
    return value


def load_model_cards() -> list[ModelCard]:
    """从项目内的 model_cards/ 目录加载全部模型卡片。

    模型卡片的发现遵循"凭证类 → 模型类 → 模型卡片"的层级：DeepSeek 凭证
    关联 DeepSeekChatModel，而 list_models() 从模型类旁的 _models/ 目录
    加载 YAML。这里通过 custom_yaml_dir 指向应用自己的注册表。
    """

    cards = DeepSeekChatModel.list_models(
        custom_yaml_dir=str(MODEL_CARDS_DIR),
    )
    if not cards:
        raise RuntimeError(
            f"model_cards/ 目录下没有可用的模型卡片，请检查 {MODEL_CARDS_DIR}。",
        )

    # list_models 会跳过格式损坏的 YAML 并记录日志；这里显式提示用户，
    # 避免之后把"卡片加载失败"误当成"模型不在目录"。
    yaml_count = len(list(MODEL_CARDS_DIR.glob("*.yaml")))
    if len(cards) != yaml_count:
        warnings.warn(
            f"model_cards/ 下有 {yaml_count} 个 YAML 文件，但只成功加载了"
            f" {len(cards)} 张卡片，可能有卡片格式错误（详情见上方日志）。",
            stacklevel=2,
        )
    return cards


def select_model_card(
    model_name: str,
    cards: list[ModelCard],
) -> ModelCard:
    """按名称查找模型卡片；找不到时列出可用模型，便于新手修正。"""

    for card in cards:
        if card.name == model_name:
            return card

    available = "、".join(card.name for card in cards)
    raise RuntimeError(
        f"模型 {model_name!r} 不在 model_cards/ 中。可用模型：{available}。"
        f"请修改 .env 中的 DEEPSEEK_MODEL，或在 {MODEL_CARDS_DIR} 添加对应卡片。",
    )


def check_model_status(card: ModelCard) -> None:
    """模型生命周期状态检查：sunset / deprecated 只警告，不阻止使用。"""

    if card.status == "sunset":
        when = (
            f"，计划在 {card.deprecated_at} 后弃用"
            if card.deprecated_at is not None
            else ""
        )
        warnings.warn(
            f"模型 {card.name} 已被标记为 sunset{when}，请考虑迁移到 active 的模型。",
            stacklevel=2,
        )
    elif card.status == "deprecated":
        warnings.warn(
            f"模型 {card.name} 已被标记为 deprecated，不建议用于新项目。",
            stacklevel=2,
        )


def _env_bool(name: str, default: bool) -> bool:
    """把环境变量解析为布尔值；无法解析时报错而非静默忽略。"""

    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"环境变量 {name} 必须是 true/false 或 1/0，当前值：{raw!r}")


def load_config() -> AppConfig:
    """加载 .env，按模型卡片校验配置，并创建配置对象。"""

    load_dotenv()

    # resolve() 将相对路径变成绝对路径，方便工具判断文件是否在项目内。
    workspace = Path(
        os.getenv("AGENT_WORKSPACE", str(Path.cwd())),
    ).expanduser().resolve()

    # agent 工作区根目录（各 agent 的 workspace 都在其下，含 skills/ 等）：
    # 非临时、明确可见，默认 workspace/agent_workspace，可用
    # AGENT_WORKSPACE_DIR 覆盖。这是用户数据，任何测试/验证都不得删除。
    agent_workspace = Path(
        os.getenv("AGENT_WORKSPACE_DIR", str(workspace / "agent_workspace")),
    ).expanduser().resolve()

    # agent 数据目录（会话/凭证/团队记录 DB）：同样是用户数据，非临时，
    # 默认 workspace/agent_data，可用 AGENT_DATA_DIR 覆盖；含明文 api_key，
    # 已 gitignore，禁止入库。
    agent_data_dir = Path(
        os.getenv("AGENT_DATA_DIR", str(workspace / "agent_data")),
    ).expanduser().resolve()

    model_name = (
        os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL_NAME).strip()
        or DEFAULT_MODEL_NAME
    )

    # 模型卡片：校验模型名与生命周期状态。
    cards = load_model_cards()
    model_card = select_model_card(model_name, cards)
    check_model_status(model_card)

    # 可选：最大输出 token 数，不能超过卡片声明的 output_size 上限。
    max_tokens_raw = os.getenv("DEEPSEEK_MAX_TOKENS", "").strip()
    max_tokens: int | None = None
    if max_tokens_raw:
        try:
            max_tokens = int(max_tokens_raw)
        except ValueError as exc:
            raise RuntimeError(
                f"环境变量 DEEPSEEK_MAX_TOKENS 必须是整数，当前值：{max_tokens_raw!r}",
            ) from exc
        if max_tokens <= 0:
            raise RuntimeError(
                f"DEEPSEEK_MAX_TOKENS 必须大于 0，当前值：{max_tokens}",
            )
        if max_tokens > model_card.output_size:
            raise RuntimeError(
                f"DEEPSEEK_MAX_TOKENS={max_tokens} 超过模型 {model_name} "
                f"卡片声明的输出上限 output_size={model_card.output_size}。",
            )

    # 可选：思考模式。只有卡片 output_types 声明了思考能力才允许开启。
    thinking = _env_bool("DEEPSEEK_THINKING", False)
    if thinking and THINKING_MIME_TYPE not in model_card.output_types:
        raise RuntimeError(
            f"模型 {model_name} 的模型卡片没有声明 {THINKING_MIME_TYPE} 输出能力，"
            "无法开启 DEEPSEEK_THINKING。",
        )

    # 可选：一轮回复内 ReAct 循环的最大迭代数（每个“思考→工具→结果”算一次）。
    # 任务越大越容易触顶：18 个待办 + 逐项工具调用会超过 AgentScope 默认的 20。
    max_iters_raw = os.getenv("AGENT_MAX_ITERS", "").strip()
    max_iters = DEFAULT_MAX_ITERS
    if max_iters_raw:
        try:
            max_iters = int(max_iters_raw)
        except ValueError as exc:
            raise RuntimeError(
                f"环境变量 AGENT_MAX_ITERS 必须是整数，当前值：{max_iters_raw!r}",
            ) from exc
        if max_iters <= 0:
            raise RuntimeError(
                f"AGENT_MAX_ITERS 必须大于 0，当前值：{max_iters}",
            )

    # 可选：YOLO 模式——工作区（项目根目录）内的文件读写（Write/Edit）
    # 自动放行；命令行（PowerShell）与工作区外敏感操作仍走审核。
    yolo = _env_bool("AGENT_YOLO", False)

    return AppConfig(
        api_key=_required_env("DEEPSEEK_API_KEY"),
        base_url=os.getenv(
            "DEEPSEEK_BASE_URL",
            "https://api.deepseek.com",
        ).strip(),
        model_name=model_name,
        agent_name=os.getenv("AGENT_NAME", "虾虾子").strip() or "虾虾子",
        system_prompt=(
            os.getenv("AGENT_SYSTEM_PROMPT", "").strip()
            or DEFAULT_SYSTEM_PROMPT
        ),
        workspace=workspace,
        agent_workspace=agent_workspace,
        agent_data_dir=agent_data_dir,
        model_card=model_card,
        max_tokens=max_tokens,
        thinking=thinking,
        # 注入给模型的时区（IANA 格式），用于运行时状态注入。
        timezone=(
            os.getenv("AGENT_TIMEZONE", "Asia/Shanghai").strip()
            or "Asia/Shanghai"
        ),
        max_iters=max_iters,
        yolo=yolo,
    )
