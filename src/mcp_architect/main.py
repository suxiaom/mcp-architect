import os
import sys
import re
import json
import shutil
import click
import platform
import subprocess
from pathlib import Path

# 获取包内资源路径
BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "templates"

# CLAUDE.md 注入区块的边界标记。
# 架构师指令被包在这一对标记之间，使得后续 setup 能"只替换我们这段、不动用户内容"。
MARKER_BEGIN = "<!-- BEGIN MCP-ARCHITECT (自动生成，请勿手动编辑此区块) -->"
MARKER_END = "<!-- END MCP-ARCHITECT -->"


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

    # 用标记把架构师指令包起来，便于后续 setup 精确替换
    wrapped_prompt = f"{MARKER_BEGIN}\n{architect_prompt}\n{MARKER_END}"

    if MARKER_BEGIN in original_content and MARKER_END in original_content:
        # 已有标记区块 -> 只替换标记之间的内容，用户其余内容原样保留
        pattern = re.compile(
            re.escape(MARKER_BEGIN) + r".*?" + re.escape(MARKER_END),
            re.DOTALL,
        )
        new_content = pattern.sub(wrapped_prompt, original_content)
        with open(project_claude_md, "w", encoding="utf-8") as f:
            f.write(new_content)
        click.secho("✅ 已更新 CLAUDE.md 中的架构师指令区块（用户其余内容未改动）", fg="green")
    elif "business_index_mcp" in original_content:
        # 检测到【旧版无标记】的架构师指令：无法自动定位边界，避免误删用户内容，
        # 这里采取保守策略——追加一段带标记的新版，并提醒用户手动删除旧的那段（仅此一次）。
        new_content = original_content + "\n\n" + wrapped_prompt
        with open(project_claude_md, "w", encoding="utf-8") as f:
            f.write(new_content)
        click.secho("⚠️  检测到旧版（无标记）架构师指令。", fg="yellow")
        click.echo("   已追加一段带标记的新版指令到文件末尾。")
        click.echo("   请手动删除文件中【旧的那段】架构师指令（无标记的部分），以免新旧并存。")
        click.echo("   此后再次 setup 将自动更新带标记的区块，无需再手动处理。")
    else:
        # 全新：直接追加带标记的区块
        new_content = original_content + "\n\n" + wrapped_prompt
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