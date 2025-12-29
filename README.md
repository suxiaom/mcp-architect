MCP Architect 🧠
为 Claude 打造的长期业务记忆与架构师助手。
Give Claude a "Hippocampus" for your project.
MCP Architect 是一个基于 Model Context Protocol (MCP) 的工具集。它通过维护一个本地的业务逻辑索引 (Business Index)，让 Claude (Claude Code / Claude Desktop) 能够在不消耗大量 Context Window 的情况下，准确掌握项目的业务逻辑、模块依赖和技术决策历史。
✨ 核心功能
🔍 智能索引与检索：支持模糊匹配搜索，不再需要全量读取代码即可定位业务逻辑。
⏰ 陈旧度检测 (Anti-Hallucination)：自动对比代码修改时间与索引更新时间，防止 Claude 使用过期的知识回答问题。
🗺️ 架构可视化：一键生成 Mermaid 架构图，理清复杂模块依赖。
💾 自动备份与容灾：每次更新索引自动备份，数据更安全。
🚀 一键集成：通过 CLI 自动配置 Claude 桌面端/终端，无需手动修改 JSON 配置文件。
📦 安装
方式 1：使用 pipx (推荐)
这会将工具安装在隔离环境中，不会污染你的全局 Python 环境。
code
Bash
# 替换为你的 GitHub 仓库地址
pipx install git+https://github.com/your-username/mcp-architect.git
方式 2：使用 pip
code
Bash
pip install git+https://github.com/your-username/mcp-architect.git
依赖提示：本工具生成的 Server 脚本推荐使用 uv 运行以获得最佳体验（已内置支持），但也支持标准 python 环境。
🛠️ 快速开始
在任何你想要 Claude "记住" 的项目中：
1. 初始化 Claude 上下文 (如果尚未初始化)
进入你的项目目录，确保已经有了 CLAUDE.md（如果还没有，运行 Claude CLI 生成）：
code
Bash
claude
> /init
> /exit
2. 一键集成架构师能力
运行以下命令，工具会自动注入提示词并修改配置文件：
code
Bash
mcp-arch setup
命令执行后会自动完成：

将 business_index_mcp.py 释放到当前目录。

将架构师思维模型追加到 CLAUDE.md 中。

自动修改 claude_desktop_config.json 注册 MCP Server。
3. 开始使用
重启 Claude Desktop 或重新运行 claude 终端：
code
Bash
claude
试试问它：
"扫描一下现在的项目结构，初始化业务索引。"
"画一张当前模块的依赖关系图。"
"检查一下索引是不是过期了？"
🧠 工作流原理
任务开始前：Claude 会自动调用 check_stale_indexes 检查索引是否落后于代码，然后使用 search_business_index 检索相关知识。
认知卸载：Claude 不再尝试“背诵”整个代码库，而是将注意力集中在你的代码风格 (Style) 和当前任务上。
动态更新：当你修改了核心逻辑，Claude 会调用 update_business_index 记录变更和决策原因（Decision Log）。
📂 项目结构
code
Text
.
├── CLAUDE.md               # (自动追加) 包含架构师角色的 System Prompt
├── business_index_mcp.py   # (自动生成) 本地运行的 MCP Server
└── business_index.json     # (自动生成) 存储业务逻辑、流程和决策的数据库
🤝 贡献
欢迎提交 Issue 或 PR！
📄 License
MIT