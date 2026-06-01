import os
import sys
import json
import shutil
import click
import platform
import subprocess
from pathlib import Path

# 获取包内资源路径
BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "templates"


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
        click.echo("请先在当前目录运行 Claude Code 并执行初始化命令：")
        click.secho("  $ claude", fg="green")
        click.secho("  > /init", fg="green")
        click.echo("生成项目基础上下文后，再运行此 setup 命令。")
        return

    click.echo("✅ 检测到 CLAUDE.md，准备集成...")

    # --- 步骤 2: 释放 Server 脚本 ---
    server_src = BASE_DIR / "server.py"
    server_dst = current_dir / "business_index_mcp.py"

    # 只复制文件，依赖由 uv run (PEP723) 自动管理，或假设环境已装 fastmcp
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
        new_content = original_content + "\n\n" + "-" * 20 + "\n\n" + architect_prompt
        with open(project_claude_md, "w", encoding="utf-8") as f:
            f.write(new_content)
        click.secho("✅ 已将架构师指令追加到 CLAUDE.md 末尾", fg="green")

    # --- 步骤 4: 通过 Claude Code 官方命令注册 MCP (scope=local) ---
    register_mcp_server(server_dst)

    click.echo("\n🎉 集成完成！请重新打开一个 Claude Code 会话使其生效。")


def register_mcp_server(server_dst: Path):
    """
    使用 `claude mcp add ... --scope local` 注册 MCP 服务器。

    设计要点：
    - scope=local：配置存入 ~/.claude.json，按项目隔离，仅当前用户可见，不污染项目目录。
    - 幂等：先静默 remove 同名条目（忽略失败），再 add，避免重复 setup 报"已存在"。
    - 运行器：优先 uv run（自动管理 PEP723 单脚本依赖），无 uv 则回退 python。
    - 失败兜底：任何异常都打印可手动执行的命令，不让流程卡死。
    """
    script_abs_path = str(server_dst.absolute())

    # 决定运行器
    if shutil.which("uv"):
        runner = ["uv", "run", script_abs_path]
        runner_display = f"uv run {script_abs_path}"
    else:
        runner = ["python", script_abs_path]
        runner_display = f"python {script_abs_path}"

    manual_cmd = f"claude mcp add business-index --scope local -- {runner_display}"

    # 解析 claude 可执行文件的完整路径
    claude_path = shutil.which("claude")
    if not claude_path:
        click.secho("⚠️  未在 PATH 中找到 claude 命令，无法自动注册。", fg="yellow")
        click.echo("请在本项目目录下手动运行：")
        click.secho(f"  {manual_cmd}", fg="green")
        return

    remove_cmd = [claude_path, "mcp", "remove", "business-index", "--scope", "local"]
    add_cmd = [claude_path, "mcp", "add", "business-index", "--scope", "local", "--"] + runner

    is_windows = platform.system() == "Windows"

    try:
        # 幂等：先尝试移除（忽略其成败）
        _run_claude(remove_cmd, is_windows)
        # 再添加
        result = _run_claude(add_cmd, is_windows)

        if result.returncode == 0:
            click.secho("✅ 已注册 MCP 服务器: business-index (scope=local)", fg="green")
            click.echo(f"   运行器: {runner[0]}")
        else:
            click.secho("❌ 自动注册失败。", fg="red")
            click.echo((result.stderr or result.stdout or "").strip())
            click.echo("请在本项目目录下手动运行：")
            click.secho(f"  {manual_cmd}", fg="green")
    except Exception as e:
        click.secho(f"❌ 自动注册异常: {e}", fg="red")
        click.echo("请在本项目目录下手动运行：")
        click.secho(f"  {manual_cmd}", fg="green")


def _run_claude(cmd_list, is_windows: bool):
    """
    跨平台执行 claude 命令。
    Windows 上 claude 多为 .cmd 包装脚本，直接传列表可能报 WinError 193，
    故用 list2cmdline 拼成字符串并通过 shell=True (cmd.exe) 执行；
    其他平台直接传列表、shell=False。
    """
    if is_windows:
        return subprocess.run(
            subprocess.list2cmdline(cmd_list),
            capture_output=True, text=True, shell=True
        )
    return subprocess.run(cmd_list, capture_output=True, text=True)


if __name__ == "__main__":
    cli()