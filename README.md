# AgentScope 2.0 AI Agent 服务（虾虾子）

这是一个基于 AgentScope 2.0 的 AI Agent 程序：**程序即服务**——启动后提供
agent service 后端（官方 `create_app`）与 Swagger 交互界面，并使用 DeepSeek
模型。服务端自带原生 team/subagent 能力：虾虾子可以派生只读 worker 并行
分析任务。

## 工作流程

```text
python main.py
    ↓
预置：DeepSeek 凭证 + 虾虾子 agent + 默认会话（幂等）
    ↓
uvicorn 启动 agent service（http://127.0.0.1:8000）
    ↓
主人通过 Swagger(/docs) 或 REST(POST /chat/) 发消息
    ↓
虾虾子按 ReAct 循环调用工具：
  workspace 工具（Read / Glob / Grep / Bash / Write / Edit）
  team/subagent 工具（TeamCreate / AgentCreate / TeamSay / TeamDelete）
    ↓
模型直接回答，或派生 worker 并行干活后整合汇报
```

## 项目结构

```text
Agent_POC/
├─ main.py              # 程序入口：启动 agent service（唯一入口）
├─ config.py            # 读取 .env、加载模型卡片并校验配置
├─ service_factory.py   # create_app 装配 + 虾虾子/凭证/会话预置
├─ subagent_templates.py # 原生子代理模板（只读 worker）
├─ web_ui/             # 官方 Web UI 前端工程（React+Vite，构建后挂载到 /）
├─ model_cards/         # 模型卡片注册表（YAML 声明模型能力与上限）
├─ .env                 # 本地真实配置，不会提交到 Git
├─ .env.example         # 可以公开的配置模板
└─ requirements.txt     # Python 依赖
```

建议按 `main.py → config.py → service_factory.py → subagent_templates.py` 的顺序阅读。

## 1. 安装依赖

需要 Python 3.11 或更高版本。

```powershell
python -m pip install -r requirements.txt
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
程序通过 `DeepSeekChatModel.list_models(custom_yaml_dir=...)` 加载这些卡片，
并据此校验配置：

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

启动后：

- Web UI（官方聊天前端）：http://127.0.0.1:8000/
- API 文档（Swagger UI）：http://127.0.0.1:8000/docs
- 启动时自动预置 DeepSeek 凭证 + 虾虾子 agent + 默认会话（幂等），
  无需手工建 agent——服务端口的虾虾子与配置完全来自同一份 `config.py`。

### 构建 Web UI（首次启动前执行一次）

Web UI 是 AgentScope 官方前端工程（`web_ui/`，React + Vite）。首次使用
需要构建（生成 `dist/` 后服务自动挂载到根路径；未构建时服务仍以纯 API
可用）：

```powershell
# 需要 Node.js 20.19+（vite 8 要求；官网安装 https://nodejs.org/ 推荐 22 LTS）
cd web_ui/frontend
npm install
npm run build
```

> 官方工程是 pnpm monorepo（frontend + backend），backend 是官方开发用
> 的占位服务，本项目不需要；只用 frontend 时 npm 与 pnpm 均可构建。

构建产物在 `web_ui/frontend/dist/`（已 gitignore，不入库）。启动后在
浏览器打开 http://127.0.0.1:8000/ ，首次进入会要求设置服务器地址
（填 `http://127.0.0.1:8000`）和用户名——页面右上角设置完成即可聊天。

已知限制：前端页面路由（如 `/schedule`、`/credential`）与后端 API 端点
同名，浏览器直接刷新这些地址会命中 API 返回 JSON。请从首页 `/` 进入，
用页面内导航即可。

## 4. 与虾虾子对话（REST）

用 `POST /chat/` 发消息（`x-user-id: local-user`）：

```text
POST http://127.0.0.1:8000/chat/
{
  "agent_id": "xiashazi",
  "session_id": "session-xiashazi",
  "input": [{"name": "user", "role": "user",
             "content": [{"type": "text", "text": "你好，虾虾子"}]}]
}
```

返回 `{"status": "started", "session_id": "..."}`（异步执行）。查询结果：

- `GET /sessions/session-xiashazi/status?agent_id=xiashazi`：`running` / `idle`
- `GET /sessions/session-xiashazi/messages?agent_id=xiashazi`：会话消息

也可以直接打开 `/docs`，在 Swagger 里点 `POST /chat/` 试玩。

## 5. team/subagent 能力

虾虾子自带原生团队工具（AgentScope 官方 `create_app` 按会话团队角色自动
装配）：

| 工具 | 作用 |
| --- | --- |
| `TeamCreate` | 创建团队（目标、子代理类型） |
| `AgentCreate` | 按 `subagent_type` 派生 worker 成员 |
| `TeamSay` | 成员向 leader 汇报 |
| `TeamDelete` | 解散团队（`created` 成员连 agent+session 级联删除） |

worker 为**只读调查员**（`subagent_templates.py`）：allow `Read/Glob/Grep`，
deny `Bash/PowerShell/Write/Edit`。服务形态没有人工确认界面，deny 而非
ask——worker 想写文件/执行命令会被直接拒绝，不会卡住等待确认。

示例任务（虾虾子会派生多个 worker 并行分析后整合汇报）：

```text
用原生团队工具分析 model_cards/ 目录下有哪些模型卡片，并总结它们的用途。
```

worker 的派生/汇报由 AgentScope 原生机制调度：父 agent 的 `AgentCreate`
只是"下订单"，worker 由消息总线触发、在独立会话中执行，`TeamSay` 回传结果。

## 6. 安全

- worker 只读（见上节），无法写文件或执行命令。
- 服务数据落在 `config.workspace/agentscope_app.db`（SQLite，含明文
  api_key），已被 `.gitignore` 忽略，**禁止入库**。
- 凭证（DeepSeek api_key）通过 `service_factory.provision` 写入存储，
  代码内不打印密钥。
- 虾虾子（leader）调用写工具（`Bash / PowerShell / Write / Edit`）时会
  触发确认事件——服务形态的**异步确认机制**：主人通过 `POST /chat/`
  把 `UserConfirmResult` 作为 `input` 回传即可完成确认（官方设计，无
  人工界面时不会自动放行）。worker 则直接 deny，绝不等确认。

## 配置项

| 环境变量 | 必填 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | 是 | 无 | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | DeepSeek API 接口地址 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-flash` | 模型名称，必须存在于 `model_cards/` 的卡片中 |
| `DEEPSEEK_MAX_TOKENS` | 否 | 无 | 最大输出 token 数，不得超过卡片 `output_size` |
| `DEEPSEEK_THINKING` | 否 | `false` | 思考模式；仅卡片声明 `application/x-thinking` 的模型可开启 |
| `AGENT_MAX_ITERS` | 否 | `60` | 一轮回复内 ReAct 循环最大迭代数；大型多步骤任务建议 ≥ 60 |
| `AGENT_NAME` | 否 | `虾虾子` | Agent 显示名称 |
| `AGENT_SYSTEM_PROMPT` | 否 | `config.py` 中的默认人格 | 身份和行为设定 |
| `AGENT_TIMEZONE` | 否 | `Asia/Shanghai` | 注入给模型的时区（IANA 格式） |
| `AGENT_WORKSPACE` | 否 | 启动程序时的当前目录 | 工具默认工作目录 |

会话历史保存在 `agentscope_app.db` 中，重启服务后仍在；运行中的状态由
`InMemoryMessageBus` 承载，不跨进程。
