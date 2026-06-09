# MCP Architect 🧠

> 为 [Claude Code](https://docs.claude.com/en/docs/claude-code) 打造的长期业务记忆库 —— 并用 Hook 机制**强制** Claude 维护它。
>
> *Give Claude a hippocampus for your project — and make it actually use it.*

MCP Architect 是一个基于 **Model Context Protocol (MCP)** 的工具集。它在项目里维护一份本地的**业务逻辑索引**（记录模块在做什么、对应什么业务语义、以及当初为什么这么改），让 Claude 在不通读整个代码库、不消耗大量上下文的前提下，准确掌握项目的业务与演进。

它和"又一个语义检索工具"最大的不同在于：**它不只提供记忆，还通过 Claude Code 的 Hook 机制，让 Claude 不得不维护这份记忆**——读了源码却不沉淀，会在收尾时被拦下。

---

## 为什么需要它

Claude 每开一个新会话都是"失忆"的。结果是三个反复出现的痛点：

- **重复理解**：每次都从头通读源码去重建对项目的认知，慢且费上下文。
- **读了不沉淀**：这次费力读懂了某个模块，下次会话又得重来——理解没有被留下。
- **知识漂移**：靠模型脑补的"项目印象"会过时，也无人校验。

MCP Architect 用"一次写入、长期复用"的本地索引解决前两个；用 Hook 强制写回 + 陈旧度检测应对后两个。

---

## 核心理念：两因一果的数据模型

索引把每一次代码改动拆成"一果两因"，三者共享同一个**组号 id**，从而串成因果关系：

| 记录类型 | 角色 | 内容 |
|---|---|---|
| **module** | 果 | 一段代码的当前实现（名称、摘要、依赖等） |
| **workflow** | 因·业务语义 | 这段代码在业务上"本该表达什么" |
| **decision** | 因·技术决策 | 当初"为什么这么改" |

- **因果绑定**：拿到一个 module 的 `id`，就能用它找回促成它的 workflow / decision。
- **版本链**：module 与 workflow 每次改动生成新的当前版，旧版降级为历史版（`prev_id` 串联）；检索默认只命中当前版。
- **决策只追加**：decision 是历史事件序列，只增不改，构成项目的演进轨迹。

三类记录分别存于 `modules.json` / `workflows.json` / `decisions.json`。

---

## 核心能力

### 🧩 记忆层

- **🔍 智能检索**：关键词模糊匹配定位业务，无需全量读码（中文分词，未装 `jieba` 时自动降级仍可用）。
- **🗺️ 现状地图**：一条命令拿到"当前有哪些模块、各自干嘛"的精简全景；细节与演进按 `id` / `path` 按需钻取，**不会因索引变大而撑爆返回**。
- **⏰ 陈旧度检测**：对比源文件改动时间与索引更新时间，标出"代码改了、索引没跟上"的模块，防止 Claude 用过期知识作答。
- **📐 架构可视化**：一键生成 Mermaid 模块依赖图。
- **💾 自动备份**：每次写入索引前自动备份（`*.json.bak`）。

### 🔒 强制力层（本工具的差异点）

光把规则写进提示词，Claude 会"读到、但不一定照做"。本工具改用 Claude Code 的 Hook 做**确定性兜底**：

- **事后写回（Stop hook）**：当本轮读过源码、却没有写回索引时，Claude 想结束会被拦下，并被要求先调用 `update_business_index` 落账——**不依赖模型自觉**。为避免死循环，每轮最多强制一次。
- **事前查索引（UserPromptSubmit hook）**：本会话还没查过索引时，会在 Claude 动手前注入一句提醒，引导它"先查索引、再决定是否通读源码"（非阻断，可被合理忽略）。

这两道 Hook 由 `setup` 自动部署，无需手动配置。

---

## 环境要求

- **Claude Code**（2.x）。本工具依赖 `claude mcp add` 注册与 Claude Code 的 Hook 机制，**不适用于 Claude Desktop**。
- **Python 3.10+**。
- **推荐安装 [uv](https://github.com/astral-sh/uv)**：生成的 Server 以 PEP 723 单文件脚本运行，`uv` 会自动管理其依赖（`fastmcp`、`jieba`）。无 `uv` 时回退到系统 `python`，此时需自行确保该环境装有 `fastmcp`。

---

## 📦 安装

仓库：<https://github.com/suxiaom/mcp-architect>

**方式 1：pipx（推荐，隔离环境，不污染全局 Python）**

```bash
pipx install git+https://github.com/suxiaom/mcp-architect.git
```

**方式 2：pip**

```bash
pip install git+https://github.com/suxiaom/mcp-architect.git
```

安装后即可使用 `mcp-arch` 命令。

---

## 🛠️ 快速开始

在任何你想让 Claude "记住"的项目目录下：

**1. 确保项目已有 `CLAUDE.md`**（没有就先用 Claude Code 生成）

```bash
claude
> /init
> /exit
```

**2. 一键集成**

```bash
mcp-arch setup
```

该命令会自动完成：

1. 将 MCP Server 脚本释放为项目根目录下的 `business_index_mcp.py`；
2. 把"架构师指令"以标记块的形式注入 `CLAUDE.md`（幂等，重复执行只更新该块，**不动你的其余内容**）；
3. 通过 `claude mcp add business-index --scope local` 注册 MCP Server（按项目隔离，配置存入 `~/.claude.json`）；
4. 部署强制力 Hook（`.claude/hooks/writeback_hook.py`）并**合并**进 `.claude/settings.json`（保留你已有的其它 Hook）。

**3. 开一个新的 Claude Code 会话使其生效**

```bash
claude
```

试着问它：

```text
扫描一下项目结构，把核心模块写进业务索引。
先查业务索引，帮我理清 XXX 模块现在是怎么实现的。
画一张当前模块依赖的架构图。
检查一下索引是不是过期了？
```

---

## 🧠 工作流原理

1. **事前**：新会话开始、还没查过索引时，Claude 会被提醒先用 `check_stale_indexes` / `search_business_index` 建立上下文，而不是直接通读全量代码。
2. **认知卸载**：Claude 不再试图"背诵"整个代码库，而是把上下文窗口留给你的代码风格与当前任务。
3. **事后写回**：当它读了源码、理解了业务，收尾时会（被 Stop hook 兜底地）调用 `update_business_index` 记录变更与决策原因。
4. **长期复用**：下一个会话直接查索引复用已有理解，省去重新读码。

---

## 🔧 MCP 工具一览

| 工具 | 作用 |
|---|---|
| `search_business_index` | 关键词模糊检索当前版记录（先 module，未命中再兜底搜 workflow/decision） |
| `check_stale_indexes` | 比对源文件改动时间与索引更新时间，标出过期模块；索引为空时提示进入"建立期" |
| `get_business_index` | **现状地图**：当前版 module（id+名称+摘要）+ workflow（id+摘要）缩影，不含 decision 与历史，带返回体积上限 |
| `get_related` | 按组号 `id` **横向找因**：取同一次因果事件的 module / workflow / decision |
| `get_version_history` | 按 `path` **纵向回溯**：返回某模块的全部历史版本（新→旧） |
| `update_business_index` | 写入索引（模式 A 打包记录新改动；模式 B 补充 workflow/decision），append-only + 自动备份 |
| `generate_architecture_diagram` | 生成 Mermaid 模块依赖架构图 |
| `validate_index` | 清理"源文件已不存在"的当前版模块（历史版保留作为回溯资产） |

---

## 📂 集成后的项目结构

```text
your-project/
├── CLAUDE.md                   # 架构师指令以标记块注入；你的其余内容原样保留
├── business_index_mcp.py       # MCP Server（setup 释放）
├── modules.json                # 索引 · module（果）
├── workflows.json              # 索引 · workflow（因 · 业务语义）
├── decisions.json              # 索引 · decision（因 · 技术决策）
└── .claude/
    ├── settings.json           # 合并注入了强制力 Hook（不覆盖你已有的）
    └── hooks/
        └── writeback_hook.py   # 事前提醒 + 事后写回拦截
```

> 运行期还会产生 `*.json.bak`（写入前备份）与 `.claude/.writeback_pending` 等会话级标记文件，均可安全忽略，建议加入 `.gitignore`。

---

## ⚠️ 注意事项

- 仅适配 **Claude Code**；注册方式与 Hook 均为 Claude Code 特性。
- Hook 脚本由你机器上的 Python 执行（`setup` 会锁定一个绝对路径的解释器），若该环境被移除需重新 `setup`。
- 事前提醒是**非阻断**的引导，模型可在合理判断下忽略；真正的"读了必写回"由事后的 Stop hook 确定性保证。
- 若 Claude 把读码任务整体委派给子 Agent，子 Agent 内部的读取可能不被父层 Hook 追踪——这是已知边界。

---

## 🤝 贡献

欢迎提交 Issue 或 PR。

## 📄 License

[MIT](./LICENSE)