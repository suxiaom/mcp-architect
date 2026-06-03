#!/usr/bin/env python3
"""
business-index 写回强制 hook（Claude Code）。

作用：把"读了源码就必须写回索引"从【模型自觉】变成【确定性拦截】。
做法：用一个会话级标记文件记录"本会话读过、但还没写回的源码"，
      在模型想结束本轮时（Stop hook）检查标记——还在就拦下、把指令喂回去。
      它不依赖模型主动调 MCP 读工具、也不依赖它记得规则。

子命令（由 .claude/settings.json 的 hook 配置调用，约定见 main.py）：
  mark   PostToolUse(Read) 后：若读的是源码文件，记入待写回标记
  clear  PostToolUse(update_business_index) 后：写回已发生，清空标记
  gate   Stop 时：标记仍在 -> exit 2 阻断结束，把行动指令喂回模型
  reset  SessionStart 时：清掉上次会话残留的标记

所有子命令从 stdin 读取 Claude Code 传入的 JSON。
标记文件固定为 <项目>/.claude/.writeback_pending，路径由本脚本自身位置推出，
不依赖运行时 CWD。脚本只用标准库，任何 python3 均可运行。

退出码约定（Claude Code）：
  exit 0 = 放行；exit 2 = 阻断（Stop 时即"强制继续"），reason 走 stderr。
"""
import sys
import json
from pathlib import Path

# 本脚本部署在 <项目>/.claude/hooks/writeback_hook.py
# 标记文件放在 <项目>/.claude/.writeback_pending（用 __file__ 定位，不依赖 CWD）
HOOK_DIR = Path(__file__).resolve().parent          # .../.claude/hooks
MARKER = HOOK_DIR.parent / ".writeback_pending"      # .../.claude/.writeback_pending

# 视为"源码"的扩展名：读它们才算产生业务理解、才触发写回义务。
# 索引文件 modules/workflows/decisions.json 是 .json，天然不在此列。
CODE_EXTS = {
    ".py", ".java", ".js", ".jsx", ".ts", ".tsx", ".vue",
    ".go", ".rs", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".cs", ".rb", ".php", ".kt", ".scala", ".sql", ".sh",
}

# 显式排除：组件自身的文件，读它们不该触发写回义务。
EXCLUDE_NAMES = {"business_index_mcp.py", "writeback_hook.py"}


def read_stdin_json():
    """容错读取 stdin 的 JSON；读不到或解析失败返回空 dict。"""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


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


def cmd_mark(data):
    """PostToolUse(Read)：源码读取 -> 记入标记（去重累加）。"""
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
    """PostToolUse(update_business_index) 或 SessionStart：清空标记。"""
    try:
        if MARKER.exists():
            MARKER.unlink()
    except Exception:
        pass
    sys.exit(0)


def cmd_gate(data):
    """Stop：标记仍在 -> 阻断本轮结束，把写回指令喂回模型。"""
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
    print(msg, file=sys.stderr)
    sys.exit(2)


def main():
    sub = sys.argv[1] if len(sys.argv) > 1 else ""
    data = read_stdin_json()
    if sub == "mark":
        cmd_mark(data)
    elif sub == "clear" or sub == "reset":
        cmd_clear(data)
    elif sub == "gate":
        cmd_gate(data)
    else:
        # 未知子命令：不干预，安全放行
        sys.exit(0)


if __name__ == "__main__":
    main()
