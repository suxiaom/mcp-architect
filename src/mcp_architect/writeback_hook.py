#!/usr/bin/env python3
"""
business-index 索引强制 hook（Claude Code）。

把两件本该做、但模型常常不做的事，从【模型自觉】变成【机制托底】：
  1) 事前：开始通读源码前，先查业务索引（避免绕过已有记忆、重复读代码、理解不一致）。
  2) 事后：读了源码理解了业务，结束前必须写回索引。

实现：用两个会话级标记文件记录状态，分别在「用户提交消息时」「工具调用后」
      「模型想结束时」「会话开始时」由对应 hook 触发对应子命令。
      不依赖模型主动调 MCP 工具、也不依赖它记得规则。

子命令（由 .claude/settings.json 的 hook 配置调用，约定见 main.py）：
  precheck  UserPromptSubmit 时：本会话还没查过索引 -> 往 stdout 注入"先查索引"提醒（非阻断）
  seen      PostToolUse(任一索引读/写工具) 后：置"已查过索引"标记，停止 precheck 提醒
  mark      PostToolUse(Read) 后：若读的是源码文件，记入"待写回"标记
  clear     PostToolUse(update_business_index) 后：写回已发生，清空"待写回"标记
  gate      Stop 时：仍有"待写回" -> exit 2 阻断结束，把写回指令喂回模型
  reset     SessionStart 时：清掉上次会话残留的两个标记

退出码约定（Claude Code）：
  exit 0 = 放行；对 UserPromptSubmit/SessionStart，exit 0 的 stdout 会被注入上下文。
  exit 2 = 阻断（Stop 时即"强制继续"），reason 走 stderr。
所有子命令从 stdin 读取 Claude Code 传入的 JSON。脚本只用标准库，任何 python3 均可运行。
"""
import sys
import json
from pathlib import Path

# 本脚本部署在 <项目>/.claude/hooks/writeback_hook.py
# 标记文件放在 <项目>/.claude/ 下（用 __file__ 定位，不依赖运行时 CWD）
HOOK_DIR = Path(__file__).resolve().parent              # .../.claude/hooks
CLAUDE_DIR = HOOK_DIR.parent                            # .../.claude
MARKER = CLAUDE_DIR / ".writeback_pending"             # 待写回的源码读取
INDEX_MARKER = CLAUDE_DIR / ".index_consulted"         # 本会话是否已查过索引

# 视为"源码"的扩展名：读它们才算产生业务理解、才触发写回义务。
# 索引文件 modules/workflows/decisions.json 是 .json，天然不在此列。
CODE_EXTS = {
    ".py", ".java", ".js", ".jsx", ".ts", ".tsx", ".vue",
    ".go", ".rs", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".cs", ".rb", ".php", ".kt", ".scala", ".sql", ".sh",
}
# 显式排除：组件自身的文件，读它们不该触发写回义务。
EXCLUDE_NAMES = {"business_index_mcp.py", "writeback_hook.py"}

# precheck 注入给模型的提醒（事前查索引）。刻意短，且给无关任务留出豁免口径。
INDEX_REMINDER = (
    "【索引提示】本会话尚未查询业务索引。若本次任务涉及理解某个已有业务/模块，"
    "请先调用 search_business_index 或 check_stale_indexes 查看有无现成的业务记忆，"
    "再决定是否通读源码——避免重复读代码、避免得出与已有索引不一致的理解。"
    "与代码理解无关的任务（查报错、改配置等）可忽略本提示。"
)


def read_stdin_json():
    """容错读取 stdin 的 JSON；读不到或解析失败返回空 dict。"""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def emit(stream, text: str):
    """
    按 UTF-8 写指定流（stdout / stderr）。
    Windows 上 Python 默认用本地代码页(cp936/GBK)输出，而 Claude Code 按 UTF-8
    读取，导致中文乱码。这里直接往二进制缓冲写 UTF-8 字节，绕过文本层编码；
    缓冲不可用时退而重配流编码再输出。
    """
    data = text.encode("utf-8", errors="replace")
    try:
        stream.buffer.write(data)
        stream.buffer.flush()
    except (AttributeError, ValueError):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print(text, file=stream)


def is_source_read(path_str: str) -> bool:
    """判断这次读取的文件是否算"理解了业务的源码"。"""
    if not path_str:
        return False
    p = Path(path_str)
    if ".claude" in p.parts:          # 组件自身目录（hook 脚本、标记、settings）一律不计
        return False
    if p.name in EXCLUDE_NAMES:       # 组件自身脚本不计
        return False
    return p.suffix.lower() in CODE_EXTS


def cmd_precheck(data):
    """UserPromptSubmit：本会话还没查过索引 -> 往 stdout 注入提醒（exit 0，非阻断）。"""
    if not INDEX_MARKER.exists():
        emit(sys.stdout, INDEX_REMINDER)
    sys.exit(0)


def cmd_seen(data):
    """PostToolUse(任一索引读/写工具)：置"已查过索引"标记，停止 precheck 提醒。"""
    try:
        INDEX_MARKER.parent.mkdir(parents=True, exist_ok=True)
        INDEX_MARKER.write_text("1", encoding="utf-8")
    except Exception:
        pass
    sys.exit(0)


def cmd_mark(data):
    """PostToolUse(Read)：源码读取 -> 记入"待写回"标记（去重累加）。"""
    tool_input = data.get("tool_input") or {}
    fp = tool_input.get("file_path") or tool_input.get("path") or ""
    if is_source_read(fp):
        try:
            MARKER.parent.mkdir(parents=True, exist_ok=True)
            existing = set()
            if MARKER.exists():
                existing = {
                    ln for ln in MARKER.read_text(encoding="utf-8").splitlines()
                    if ln.strip()
                }
            existing.add(Path(fp).as_posix())
            MARKER.write_text("\n".join(sorted(existing)), encoding="utf-8")
        except Exception:
            pass
    sys.exit(0)


def cmd_clear(data):
    """PostToolUse(update_business_index)：写回已发生 -> 清空"待写回"标记。"""
    try:
        if MARKER.exists():
            MARKER.unlink()
    except Exception:
        pass
    sys.exit(0)


def cmd_reset(data):
    """SessionStart：清掉上次会话残留的两个标记。"""
    for m in (MARKER, INDEX_MARKER):
        try:
            if m.exists():
                m.unlink()
        except Exception:
            pass
    sys.exit(0)


def cmd_gate(data):
    """Stop：仍有"待写回" -> 阻断本轮结束，把写回指令喂回模型。"""
    # 防死循环：已处于"被上一次 block 强制继续"的状态则直接放行，
    # 保证最多只强制一次（足够把模型拉回来，且不会 8 连击触发上限强停）。
    if data.get("stop_hook_active"):
        sys.exit(0)

    if not MARKER.exists():
        sys.exit(0)
    try:
        files = [ln for ln in MARKER.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        files = []
    if not files:
        sys.exit(0)

    shown = "\n".join(f"  - {f}" for f in files[:12])
    more = "" if len(files) <= 12 else f"\n  …等共 {len(files)} 个文件"
    msg = (
        "⛔ 本轮你读取了以下源码文件，但尚未调用 update_business_index 写回索引：\n"
        f"{shown}{more}\n"
        "按铁律一，任务结束前你对这些业务的理解必须在索引中落账。\n"
        "请现在调用 update_business_index（模式 A）记录你理解到的业务/改动——"
        "它是 append-only、自动备份的非破坏性操作，直接写即可，无需向用户确认。\n"
        "（写回后本提示会自动消失。）"
    )
    emit(sys.stderr, msg + "\n")
    sys.exit(2)


DISPATCH = {
    "precheck": cmd_precheck,
    "seen": cmd_seen,
    "mark": cmd_mark,
    "clear": cmd_clear,
    "reset": cmd_reset,
    "gate": cmd_gate,
}


def main():
    sub = sys.argv[1] if len(sys.argv) > 1 else ""
    data = read_stdin_json()
    handler = DISPATCH.get(sub)
    if handler:
        handler(data)
    else:
        sys.exit(0)  # 未知子命令：不干预，安全放行


if __name__ == "__main__":
    main()