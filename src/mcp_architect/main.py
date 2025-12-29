import os
import sys
import json
import shutil
import click
import platform
from pathlib import Path

# 获取包内资源路径
BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "templates"

def get_claude_config_path():
    """根据操作系统获取 Claude Desktop/CLI 配置文件路径"""
    system = platform.system()
    if system == "Darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        return Path(os.getenv("APPDATA")) / "Claude" / "claude_desktop_config.json"
    else:  # Linux (假设)
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"

@click.group()
def cli():
    """MCP Architect 管理工具"""
    pass

@cli.command()
def setup():
    """将架构师记忆库集成到当前项目 (自动配置 MCP + 更新 Prompt)"""
    current_dir = Path.cwd()
    project_claude_md = current_dir / "CLAUDE.md"
    
    # --- 步骤 1: 检查 CLAUDE.md 是否存在 ---
    if not project_claude_md.exists():
        click.secho("❌ 错误: 未找到 CLAUDE.md 文件。", fg="red")
        click.echo("请先在当前目录运行 Claude CLI 并执行初始化命令：")
        click.secho("  $ claude", fg="green")
        click.secho("  > /init", fg="green")
        click.echo("生成项目基础上下文后，再运行此 setup 命令。")
        return

    click.echo("✅ 检测到 CLAUDE.md，准备集成...")

    # --- 步骤 2: 释放 Server 脚本 ---
    server_src = BASE_DIR / "server.py"
    server_dst = current_dir / "business_index_mcp.py"
    
    # 简单的依赖检查：确保脚本里引用的 fastmcp 可用
    # 这里我们只复制文件，假设用户会在环境中安装依赖，或者使用 uv run
    shutil.copy(server_src, server_dst)
    click.secho(f"✅ 已创建工具脚本: {server_dst.name}", fg="green")

    # --- 步骤 3: 追加 Prompt 到 CLAUDE.md ---
    prompt_src = TEMPLATE_DIR / "CLAUDE.md"
    
    with open(prompt_src, "r", encoding="utf-8") as f:
        architect_prompt = f.read()
    
    with open(project_claude_md, "r", encoding="utf-8") as f:
        original_content = f.read()
    
    # 简单的去重检查，防止重复追加
    if "business_index_mcp" in original_content:
        click.echo("⚠️  CLAUDE.md 似乎已经包含了架构师指令，跳过追加。")
    else:
        # 添加分隔符和新内容
        new_content = original_content + "\n\n" + "-" * 20 + "\n\n" + architect_prompt
        with open(project_claude_md, "w", encoding="utf-8") as f:
            f.write(new_content)
        click.secho("✅ 已将架构师指令追加到 CLAUDE.md 末尾", fg="green")

    # --- 步骤 4: 自动配置 MCP (写入 claude_desktop_config.json) ---
    config_path = get_claude_config_path()
    
    # 确保配置目录存在
    if not config_path.parent.exists():
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            click.secho("⚠️ 无法创建配置目录，跳过自动配置 MCP。", fg="yellow")
            return

    # 读取现有配置
    config_data = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except json.JSONDecodeError:
            click.secho("⚠️ 配置文件损坏，将创建新配置。", fg="yellow")

    # 准备 MCP 配置条目
    # 推荐使用 'uv' 运行，因为它能自动管理单脚本依赖，极其稳定
    # 如果用户没有 uv，回退到 python (假设已安装依赖)
    
    script_abs_path = str(server_dst.absolute())
    
    mcp_entry = {
        "command": "python", # 默认尝试 python
        "args": [script_abs_path]
    }
    
    # 检测是否存在 uv
    if shutil.which("uv"):
        mcp_entry = {
            "command": "uv",
            "args": ["run", script_abs_path]
        }
    
    # 更新配置
    if "mcpServers" not in config_data:
        config_data["mcpServers"] = {}
    
    config_data["mcpServers"]["business-index"] = mcp_entry
    
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        click.secho(f"✅ 已更新 Claude 配置: {config_path}", fg="green")
        click.echo(f"   已注册 Server: business-index (使用 {mcp_entry['command']})")
    except Exception as e:
        click.secho(f"❌ 自动配置失败: {e}", fg="red")
        click.echo("请手动将以下内容添加到您的配置文件中：")
        click.echo(json.dumps({"mcpServers": {"business-index": mcp_entry}}, indent=2))

    click.echo("\n🎉 集成完成！请重启 Claude Desktop 或重新运行 Claude CLI 生效。")

if __name__ == "__main__":
    cli()