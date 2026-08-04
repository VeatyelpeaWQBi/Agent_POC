# AgentScope 2.0 工具调用聊天 Agent

这是一个适合 Python 新手阅读的命令行 Agent 示例。它使用 DeepSeek 模型，
支持多轮聊天、连续工具调用，以及敏感工具人工审核。

## 工作流程

```text
主人输入问题
    ↓
模型决定直接回答，或者调用工具
    ↓
只读工具（Read / Glob / Grep）────────→ 自动执行
敏感工具（PowerShell / Write / Edit）─→ 终端显示参数，等待 y/n 审核
    ↓
工具结果交还给模型
    ↓
模型可以继续调用工具，或生成最终回答
```

工具循环由 AgentScope 2.0 内部的 ReAct 机制负责。`approval.py` 负责接收
“需要主人确认”的事件，并在确认后让同一轮回复继续运行。

## 项目结构

```text
Agent_POC/
├─ main.py            # 程序入口和聊天循环
├─ config.py          # 读取 .env、加载模型卡片并校验配置
├─ agent_factory.py   # 创建模型、工具箱和权限规则
├─ approval.py        # 敏感工具审核及暂停/恢复循环
├─ model_cards/       # 模型卡片注册表（YAML 声明模型能力与上限）
├─ .env               # 本地真实配置，不会提交到 Git
├─ .env.example       # 可以公开的配置模板
└─ requirements.txt   # Python 依赖
```

建议按 `main.py → config.py → agent_factory.py → approval.py` 的顺序阅读。

## 1. 安装依赖

需要 Python 3.11 或更高版本。

```powershell
python -m pip install -r requirements.txt
```

如果 Windows 中使用 Python Launcher：

```powershell
py -m pip install -r requirements.txt
```

## 2. 配置 DeepSeek

第一次使用时复制模板：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中填写真实 API Key：

```dotenv
DEEPSEEK_API_KEY=sk-xxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

`.env` 已加入 `.gitignore`。不要在聊天、截图或代码仓库中公开真实 Key。

### 模型卡片：大模型配置由卡片驱动

每个可用模型在 `model_cards/` 目录下有一张 YAML 模型卡片，声明它的
能力、上限与参数覆盖（格式见
[AgentScope 文档](https://docs.agentscope.io/versions/2.0.6dev/zh/building-blocks/model/overview)）。
程序启动时通过 `DeepSeekChatModel.list_models(custom_yaml_dir=...)` 加载这些
卡片，并据此校验配置：

| 卡片字段 | 作用 |
| --- | --- |
| `status` | `active` / `deprecated` / `sunset`；非 active 只告警不阻止 |
| `context_size` | 上下文窗口大小，传给模型用于上下文压缩 |
| `output_size` | 最大输出 token 上限，`DEEPSEEK_MAX_TOKENS` 不能超过它 |
| `output_types` | 是否包含 `application/x-thinking` 决定能否开启思考模式 |

可选的思考模式与输出上限配置：

```dotenv
# 思考模式只对卡片声明了 application/x-thinking 的模型生效
DEEPSEEK_THINKING=true
DEEPSEEK_MAX_TOKENS=8192
```

想使用一个卡片里没有的模型，往 `model_cards/` 添加一张 YAML 卡片即可，
无需改代码。

人格也可以通过 `.env` 修改：

```dotenv
AGENT_NAME=虾虾子
AGENT_SYSTEM_PROMPT=你的名字是虾虾子，你始终称呼用户为主人……
```

如果没有填写 `AGENT_SYSTEM_PROMPT`，程序会使用 `config.py` 中完整的默认人格。

## 3. 启动

```powershell
python main.py
```

或者：

```powershell
py main.py
```

输入 `/exit`、`/quit` 或 `退出` 可以结束聊天。

## 4. 测试工具调用

先测试只读工具：

```text
请查看当前目录有哪些 Python 文件，并总结它们的用途。
```

`Glob`、`Read` 或 `Grep` 会自动执行，不弹出确认。

再测试敏感工具：

```text
请创建 hello.txt，内容是“你好，主人”。
```

执行 `Write` 前会出现类似提示：

```text
⚠ 检测到需要人工审核的工具调用
工具：Write
参数：...
是否允许执行？[y=允许 / n=拒绝]：
```

- 输入 `y`：只批准当前这次调用；
- 输入 `n`：拒绝调用，工具不会执行；
- 拒绝后，模型会收到拒绝结果，并可以解释影响或提出替代方案。

## 5. 安全策略在哪里修改

权限规则位于 `agent_factory.py`：

```python
READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
SENSITIVE_TOOLS = ("PowerShell", "Write", "Edit")
```

示例故意将所有命令行调用都视为敏感操作，因为即使看似普通的命令也可能
包含删除文件、安装软件或访问网络等行为。不要为了省去确认而把
`SENSITIVE_TOOLS` 加入自动放行列表。

AgentScope 的 `Write` 和 `Edit` 还内置了敏感文件保护，例如 `.env`、`.git`
和 SSH 配置等路径会受到额外检查。

## 配置项

| 环境变量 | 必填 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | 是 | 无 | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | DeepSeek API 接口地址 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-flash` | 模型名称，必须存在于 `model_cards/` 的卡片中 |
| `DEEPSEEK_MAX_TOKENS` | 否 | 无 | 最大输出 token 数，不得超过卡片 `output_size` |
| `DEEPSEEK_THINKING` | 否 | `false` | 思考模式；仅卡片声明 `application/x-thinking` 的模型可开启 |
| `AGENT_NAME` | 否 | `虾虾子` | Agent 显示名称 |
| `AGENT_SYSTEM_PROMPT` | 否 | `config.py` 中的默认人格 | 身份和行为设定 |
| `AGENT_WORKSPACE` | 否 | 启动程序时的当前目录 | 工具默认工作目录 |

本示例会在程序运行期间保留多轮对话上下文；退出后不会持久化聊天记录。
# Agent_POC
