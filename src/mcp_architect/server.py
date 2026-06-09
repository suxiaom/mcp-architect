# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastmcp",
#     "jieba",
# ]
# ///

#!/usr/bin/env python3
import json
import os
import re
import time
import uuid
import shutil
import difflib
from pathlib import Path
from fastmcp import FastMCP

# jieba 用于中文分词以改善搜索匹配。容错导入：未安装时退化为"不分词"，
# 搜索仍可用（只是模糊匹配能力下降），不会让整个 server 崩溃。
try:
    import jieba
    jieba.setLogLevel("ERROR")  # 静默 jieba 的加载日志，避免污染 MCP stdio
    _HAS_JIEBA = True
except Exception:
    _HAS_JIEBA = False

# 1. 初始化 MCP Server
mcp = FastMCP("business-index-server")

# 2. 三类索引分文件存储
# 设计：类型由"记录存在哪个文件"决定，因此记录内部不再保存 type 字段。
# 路径基于脚本自身位置（__file__）定位，不依赖进程 CWD——
# setup 会把本脚本复制进项目根目录，故索引始终与脚本同目录。
BASE_DIR = Path(__file__).parent
STORE_FILES = {
    "module": BASE_DIR / "modules.json",
    "workflow": BASE_DIR / "workflows.json",
    "decision": BASE_DIR / "decisions.json",
}

# 层面二兜底：工具返回序列化后的字符数上限。
# Claude Code 对工具返回有 token 上限（约 17 万字符量级），超限会被落盘+逼分块读。
# 这里留足安全余量设为 10 万，作为纯兜底——正常的 m+w 精简缩影远小于此，基本碰不到。
MAX_OUTPUT_CHARS = 100_000

# ============================================================
# 数据模型约定（重要，便于理解下面的代码）
# ------------------------------------------------------------
# 三种记录都有一个 "id" 字段，但它的语义是【组号】，不是行的唯一标识：
#   - 同一次"因果事件"（一次代码改动 + 对应的业务语义/决策）产生的
#     module / workflow / decision，共享【同一个 id】。
#   - 不同的因果事件，id 不同。
#   - 因此"由果找因"= 拿着 module 的 id，去 workflow/decision 文件里查同 id 的记录。
#
# module / workflow 额外有版本链字段：
#   - is_current: 是否为该 path 的当前版本（搜索只命中当前版）。
#   - prev_id:    指向同一 path 上一版本的【组号 id】；首版为 None。
#   - 回溯 = 顺着 prev_id 往回找更早版本。
#
# decision 不做版本链：它是历史事件序列，只追加、不覆盖、无 is_current/prev_id。
# ============================================================


# --- 辅助函数：读写 ---

def load_store(rec_type: str):
    """读取某一类记录的列表。文件不存在或损坏时返回空列表。"""
    path = STORE_FILES[rec_type]
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_store(rec_type: str, items: list):
    """写入某一类记录的列表。写入前自动备份旧文件。"""
    path = STORE_FILES[rec_type]
    if os.path.exists(path):
        shutil.copy(path, str(path) + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _dump_capped(data: dict) -> str:
    """
    层面二兜底：序列化 data；若超过 MAX_OUTPUT_CHARS，则按【整条记录】边界
    丢弃尾部记录（绝不切碎任何单条记录的内容），并附明确提示告知省略条数与按需查询方式。

    data 形如 {"modules": [...], "workflows": [...]}。
    正常 m+w 精简缩影远小于上限，此函数基本不触发；仅为极端大库兜底，
    且只会"少给几条 + 告诉你怎么取剩下的"，不会破坏 JSON、也不会切碎单条内容。
    """
    full = json.dumps(data, ensure_ascii=False, indent=2)
    if len(full) <= MAX_OUTPUT_CHARS:
        return full

    limit = MAX_OUTPUT_CHARS - 600  # 预留末尾提示文字的余量
    out = {k: [] for k in data}
    omitted = {k: 0 for k in data}
    stop = False
    for key, records in data.items():
        for rec in records:
            if stop:
                omitted[key] += 1
                continue
            out[key].append(rec)
            if len(json.dumps(out, ensure_ascii=False, indent=2)) > limit:
                out[key].pop()        # 回退这条，保证不超限；它及其后全部计入省略
                omitted[key] += 1
                stop = True
    out["_truncated"] = {
        "reason": f"内容超过 {MAX_OUTPUT_CHARS} 字符上限，已按整条记录边界省略尾部记录（未切碎任何单条内容）。",
        "omitted": {k: v for k, v in omitted.items() if v},
        "next": "用 search_business_index 按关键词精确查询被省略的部分；拿到 id 后用 get_related 取完整记录。",
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


def generate_group_id() -> str:
    """生成一个唯一的组号 id。纯标识，不掺业务内容。"""
    return f"g_{int(time.time())}_{uuid.uuid4().hex[:6]}"


def clean_json_string(s: str) -> str:
    """清理 Markdown 代码块标记。"""
    s = s.strip()
    s = re.sub(r"^```(json)?", "", s, flags=re.MULTILINE)
    s = re.sub(r"```$", "", s, flags=re.MULTILINE)
    return s.strip()


def _tokenize(text: str) -> list:
    """把文本切成片段，用于逐片段匹配。
    有 jieba 时用中文分词；没有时退化为：按非字母数字分隔 + 整体保留。
    """
    text = text.strip()
    if not text:
        return []
    if _HAS_JIEBA:
        # jieba 切词，过滤掉纯空白片段
        return [t for t in jieba.cut(text) if t.strip()]
    # 退化方案：按标点/空白切，至少保证英文单词能切开
    parts = [p for p in re.split(r"[\s,，。、;；:：/\\\-_()（）\[\]]+", text) if p]
    return parts or [text]


def calculate_match_score(query: str, text: str) -> float:
    """
    搜索打分（0~1）：
    1. 精确/部分包含 -> 1.0（query 作为子串原样出现在 text 中）
    2. 否则：把 text 分词，query 与每个片段分别算相似度，取最高分。
       这修复了旧实现"短 query 对整段长 text 算 ratio 被长度稀释"的失效问题。
       相似度 > 0.6 时给分（similarity * 0.8）。
    """
    if not text or not query:
        return 0.0

    q = query.lower().strip()
    t = text.lower()

    # 1. 子串包含：直接满分
    if q in t:
        return 1.0

    # 2. 分词后逐片段比，取最高
    best = 0.0
    for token in _tokenize(t):
        tok = token.lower()
        # 片段内再做一次子串判断（query 命中某个词）
        if q in tok or tok in q:
            return 1.0
        sim = difflib.SequenceMatcher(None, q, tok).ratio()
        if sim > best:
            best = sim

    if best > 0.6:
        return best * 0.8
    return 0.0


# [近场提醒] 附加在"模型即将开始干活"的读操作返回末尾。
# 目的：把"任务结束要写回索引"从遥远的系统提示搬到模型当下的上下文里，降低遗忘率。
# 仅在少数关键路径附加，避免到处都加导致脱敏。
WRITEBACK_REMINDER = (
    "\n\n———\n"
    "⚠️ 收尾铁律（客观判定，非主观）：只要本次任务【围绕某个业务/模块展开、且你为搞清它读了源码】，"
    "结束前该业务对应的每个源码 path 就【必须】在 module 层存在当前版条目；缺失则用模式 A 补建。"
    "「你读过这段代码」是事实而非判断题，不得以“只是验证一下/没产生新理解”自我豁免。\n"
    "update_business_index 是 append-only、写前自动备份的【非破坏性】操作（等同于保存文件），"
    "属任务收尾的必做步骤：请直接调用并用一句话汇报记录了什么，【绝不要反问用户是否需要更新】。"
)


def _find_current(items: list, path_key: str, path_value: str):
    """在列表中找到指定 path 的当前版本（is_current=True）。找不到返回 None。"""
    for it in items:
        if it.get(path_key) == path_value and it.get("is_current"):
            return it
    return None


# --- MCP 工具定义 ---

@mcp.tool()
def search_business_index(keyword: str) -> str:
    """
    [智能搜索 · 分层兜底] 按关键词搜索业务索引，支持中文分词与模糊匹配。
    检索策略（分两层）：
      第一层：先在 module（代码层，当前版）里搜。若命中，直接返回这些代码模块。
      第二层（兜底）：若 module 层一条都没命中，再从 workflow + decision（业务层）搜，
                      因为用户的问法常是业务语言，可能只在业务描述里有字面重叠。
                      业务层结果可能较多、较宽泛，需你自行筛选；选定后可用其 id 调 get_related 定位代码。
    命中结果都会带上 id（组号），可据此用 get_related 追溯关联。
    keyword: 搜索词，如 "Payment"、"登录"、"计费"。
    """
    def score_of(item):
        name_score = calculate_match_score(keyword, item.get("name", "")) * 2
        summary_score = calculate_match_score(keyword, item.get("summary", ""))
        id_score = calculate_match_score(keyword, item.get("id", ""))
        content_score = calculate_match_score(keyword, item.get("content", ""))
        return max(name_score, summary_score, id_score, content_score)

    def collect(items, kind, current_only):
        hits = []
        for it in items:
            if current_only and not it.get("is_current"):
                continue
            s = score_of(it)
            if s > 0.4:
                hits.append({"score": s, "kind": kind, "data": it})
        return hits

    # ---- 第一层：module ----
    module_hits = collect(load_store("module"), "module", current_only=True)
    if module_hits:
        module_hits.sort(key=lambda x: x["score"], reverse=True)
        payload = [{"kind": r["kind"], **r["data"]} for r in module_hits[:10]]
        return json.dumps(payload, ensure_ascii=False, indent=2) + WRITEBACK_REMINDER

    # ---- 第二层（兜底）：workflow + decision ----
    biz_hits = []
    biz_hits += collect(load_store("workflow"), "workflow", current_only=True)
    biz_hits += collect(load_store("decision"), "decision", current_only=False)

    if biz_hits:
        biz_hits.sort(key=lambda x: x["score"], reverse=True)
        payload = [{"kind": r["kind"], **r["data"]} for r in biz_hits[:10]]
        hint = (
            "\n\n（说明：代码层(module)未直接命中，以下是从业务层(workflow/decision)兜底找到的结果，"
            "可能较宽泛，请筛选；选定后可用其 id 调 get_related(id, \"module\") 定位对应代码。）"
        )
        return json.dumps(payload, ensure_ascii=False, indent=2) + hint + WRITEBACK_REMINDER

    # ---- 两层都无命中 ----
    return (
        f"🔍 索引中暂无与 '{keyword}' 相关的记录。\n"
        f"这【不是终点】，也【不代表该业务不存在或本工具无用】——只是这块还没人记，现在轮到你来记。\n"
        f"立即执行的下一步：阅读与本次任务相关的源码、搞清这块业务的实现，"
        f"然后【在继续手头任务之前】调用 update_business_index（模式 A）把它落账，"
        f"使后续会话可直接复用、无需重新通读代码。\n"
        f"提示：update_business_index 是 append-only、自动备份的非破坏性操作，直接写即可，无需征求用户许可。"
    )


@mcp.tool()
def get_related(id: str, type: str = "") -> str:
    """
    [横向找因] 给定一个 id（组号），取出同一次因果事件的关联记录。
    用途：当你搜到某个 module（果），想知道"为什么这么改"(decision) 或"对应什么业务语义"(workflow) 时调用。
    - type 留空：返回该组号在三类中的所有记录。
    - type 指定为 "module"/"workflow"/"decision"：只返回那一类（例如只想看决策就传 "decision"）。
    无追溯关联的需求时不要调用本工具，以免徒增上下文。
    """
    if not id:
        return "错误：必须提供 id（组号）。"

    types = [type] if type in STORE_FILES else list(STORE_FILES.keys())
    found = {}
    for t in types:
        hits = [rec for rec in load_store(t) if rec.get("id") == id]
        if hits:
            found[t] = hits

    if not found:
        return f"未找到 id 为 '{id}' 的关联记录。"
    return json.dumps(found, ensure_ascii=False, indent=2)


@mcp.tool()
def get_version_history(path: str) -> str:
    """
    [纵向回溯] 给定 module 的 path，返回该模块的所有历史版本（含当前版），从新到旧排列。
    用途：当你需要对比某个模块"改之前 vs 改之后"时调用（例如新业务有问题，想看旧实现做对照或混合开发）。
    只返回 module 本身；若要看某个旧版本对应的业务语义/决策，再用那一版的 id 调 get_related。
    无新旧对比需求时不要调用本工具。
    """
    if not path:
        return "错误：必须提供 path。"

    modules = load_store("module")
    chain = [m for m in modules if m.get("path") == path]
    if not chain:
        return f"未找到 path 为 '{path}' 的模块历史。"

    # 按时间从新到旧排序（last_updated 降序；缺失则靠后）
    chain.sort(key=lambda m: m.get("last_updated", 0), reverse=True)
    return json.dumps(chain, ensure_ascii=False, indent=2)


@mcp.tool()
def check_stale_indexes() -> str:
    """
    [新鲜度检查] 对比源文件修改时间与索引时间，找出可能过期的模块。
    只检查 module 的当前版本。
    """
    modules = load_store("module")
    current_modules = [m for m in modules if m.get("is_current")]

    if not current_modules:
        return (
            "📭 业务索引目前为空。这是新项目的【正常状态】，绝不代表本工具无效。\n"
            "你现在处于【索引建立期】：此阶段【写比读更重要】，首要任务不是检索而是记录。\n"
            "立即执行的下一步：边阅读本次任务相关的代码边理解业务，"
            "每搞清一个模块/流程/技术决策，就调用 update_business_index 将其写入索引，"
            "为后续会话积累可复用的项目记忆。\n"
            "提示：写入是 append-only、自动备份的非破坏性操作，直接做，无需向用户确认。"
        )

    stale = []
    for m in current_modules:
        path = m.get("path")
        last_updated = m.get("last_updated", 0)
        if path and os.path.exists(path):
            file_mtime = os.path.getmtime(path)
            if file_mtime > last_updated:
                lag_hours = int((file_mtime - last_updated) / 3600)
                stale.append(f"- {m.get('name')} (path: {path}): 代码已更新，索引滞后约 {lag_hours} 小时")

    if not stale:
        return "✅ 已索引的模块目前都是最新的（基于文件修改时间）。" + WRITEBACK_REMINDER

    return "以下模块的索引可能已过期，建议读取代码并调用 update_business_index：\n" + "\n".join(stale)


@mcp.tool()
def generate_architecture_diagram() -> str:
    """
    [架构图] 生成 Mermaid 依赖图，只展示当前版本的模块与工作流。
    module 间依赖通过 dependencies 字段（存的是其他 module 的 path）解析。
    """
    modules = [m for m in load_store("module") if m.get("is_current")]
    workflows = [w for w in load_store("workflow") if w.get("is_current")]

    # path -> 安全节点名 的映射（Mermaid 节点 id 不能有特殊字符，这里用序号代替）
    path_to_node = {}
    lines = ["graph TD"]
    lines.append("    classDef module fill:#e1f5fe,stroke:#01579b,stroke-width:2px;")
    lines.append("    classDef workflow fill:#fff3e0,stroke:#ff6f00,stroke-width:2px,stroke-dasharray: 5 5;")

    for idx, m in enumerate(modules):
        node = f"m{idx}"
        path_to_node[m.get("path")] = node
        safe_name = (m.get("name") or "Unknown").replace(" ", "_")
        lines.append(f'    {node}["📦 {safe_name}"]:::module')

    for idx, w in enumerate(workflows):
        node = f"w{idx}"
        safe_name = (w.get("name") or "Flow").replace(" ", "_")
        lines.append(f'    {node}(["🔄 {safe_name}"]):::workflow')
        # workflow 依赖的 module（按 path 关联）
        for dep_path in w.get("dependencies", []):
            target = path_to_node.get(dep_path)
            if target:
                lines.append(f"    {node} -.-> {target}")

    # module 之间的依赖（按 path 关联）
    for idx, m in enumerate(modules):
        src = f"m{idx}"
        for dep_path in m.get("dependencies", []):
            target = path_to_node.get(dep_path)
            if target and target != src:
                lines.append(f"    {src} --> {target}")

    return "\n".join(lines)


def _write_new_version(rec_type: str, path_key: str, new_data: dict, group_id: str, now: float):
    """
    为 module / workflow 写入一个新版本：
    - 把同 path 的旧当前版 is_current 翻为 False；
    - 新记录带 group_id、is_current=True、prev_id=旧当前版的 id。
    """
    items = load_store(rec_type)
    path_value = new_data.get(path_key)

    prev_id = None
    for it in items:
        if it.get(path_key) == path_value and it.get("is_current"):
            it["is_current"] = False
            prev_id = it.get("id")

    record = dict(new_data)
    record["id"] = group_id
    record["is_current"] = True
    record["prev_id"] = prev_id
    record["last_updated"] = now
    items.append(record)
    save_store(rec_type, items)


@mcp.tool()
def update_business_index(updates: str) -> str:
    """
    [写入索引] 记录一次业务理解或代码改动。updates 为 JSON 字符串（不要带 Markdown 标记）。
    你【不需要、也不应该】提供任何 id，id 由系统自动生成。
    本操作 append-only、写前自动备份、非破坏性，是任务收尾的常规步骤——【无需向用户确认，直接调用】。

    模式 A —— 记录一次新改动（会生成新版本，三类共享同一个新 id/组号）：
    {
      "module":   {"path": "src/calc.py", "name": "计费计算", "summary": "单价×3",
                   "key_functions": ["calc"], "dependencies": ["src/price.py"]},
      "workflow": {"summary": "业务语义：单价乘数量"},          // 可选
      "decision": {"content": "运营要求按单价×件数计费，故由累加改为乘法",
                   "file_ref": "src/calc.py"}                  // 可选
    }
    说明：module 是主体（必填 path）；workflow 跟随 module（其 path 用 module 的 path）；
    workflow/decision 可省略。三者共享本次生成的同一个 id。

    模式 B —— 给某个【已存在的版本】补充 workflow/decision（不产生新 module 版本）：
    {
      "append_to_path": "src/calc.py",                          // 补到该 path 的当前版
      "workflow": {...} 和/或 "decision": {...}
    }
    系统会找到该 path 当前版 module 的 id，把补充内容挂到同一个 id 下。
    注意：模式 B 只能补到【当前版】。若补充的内容其实是针对某次旧改动的，
    请在 decision 的 content 里明确写出"这是针对哪一次改动/哪一版的补充"，避免日后混淆。
    """
    try:
        obj = json.loads(clean_json_string(updates))
    except Exception as e:
        return f"更新失败：JSON 解析错误 - {e}"

    now = time.time()

    # ---------- 模式 B：补充到已有版本 ----------
    if "append_to_path" in obj:
        target_path = obj.get("append_to_path")
        modules = load_store("module")
        current = _find_current(modules, "path", target_path)
        if not current:
            return f"补充失败：未找到 path 为 '{target_path}' 的当前版本模块。请先用模式 A 记录该模块。"
        group_id = current.get("id")

        done = []
        if "workflow" in obj and obj["workflow"]:
            wf = load_store("workflow")
            rec = dict(obj["workflow"])
            rec["id"] = group_id
            # 补充的 workflow 也作为当前版（沿用 module 的 path 作为关联键）
            rec.setdefault("path", target_path)
            rec["is_current"] = True
            rec["prev_id"] = None
            rec["last_updated"] = now
            # 同 path 旧当前版降级
            for it in wf:
                if it.get("path") == target_path and it.get("is_current"):
                    it["is_current"] = False
                    rec["prev_id"] = it.get("id")
            wf.append(rec)
            save_store("workflow", wf)
            done.append("workflow")

        if "decision" in obj and obj["decision"]:
            ds = load_store("decision")
            rec = dict(obj["decision"])
            rec["id"] = group_id
            rec["last_updated"] = now
            ds.append(rec)
            save_store("decision", ds)
            done.append("decision")

        if not done:
            return "补充失败：模式 B 至少需要提供 workflow 或 decision。"
        return f"已补充 {', '.join(done)} 到 path '{target_path}'（id={group_id}）。"

    # ---------- 模式 A：新改动 ----------
    if "module" not in obj or not obj["module"]:
        return "更新失败：模式 A 必须包含 module（且需含 path）。"
    if not obj["module"].get("path"):
        return "更新失败：module 必须包含 path 字段。"

    group_id = generate_group_id()
    module_path = obj["module"]["path"]

    # module（必填）
    _write_new_version("module", "path", obj["module"], group_id, now)

    # workflow（可选）：path 跟随 module
    if "workflow" in obj and obj["workflow"]:
        wf_data = dict(obj["workflow"])
        wf_data.setdefault("path", module_path)
        _write_new_version("workflow", "path", wf_data, group_id, now)

    # decision（可选）：纯追加，不做版本链
    if "decision" in obj and obj["decision"]:
        ds = load_store("decision")
        rec = dict(obj["decision"])
        rec["id"] = group_id
        rec["last_updated"] = now
        ds.append(rec)
        save_store("decision", ds)

    return f"索引更新成功（id={group_id}）。已写入并备份。"


@mcp.tool()
def get_business_index() -> str:
    """
    [现状地图] 返回当前版 module 与 workflow 的精简缩影。
    内容：
      - module：id + name + summary
      - workflow：id + summary
    刻意【不含 decision、不含历史版本】，因此体积小、不会再因过大而失败。

    用途：建立项目全局现状认知——"现在有哪些模块、各自干嘛、对应什么业务语义"。
    要进一步钻取（本工具只给"果"和"业务语义"，不给"为什么"和"演进"）：
      - 想知道某处"当初为什么这么改" → 用该记录的 id 调 get_related(id, "decision")。
      - 想看某模块"改之前长什么样 / 演进过程" → 用其 path 调 get_version_history(path)。
    """
    modules = [
        {"id": m.get("id"), "name": m.get("name"), "summary": m.get("summary")}
        for m in load_store("module") if m.get("is_current")
    ]
    workflows = [
        {"id": w.get("id"), "summary": w.get("summary")}
        for w in load_store("workflow") if w.get("is_current")
    ]
    return _dump_capped({"modules": modules, "workflows": workflows})


@mcp.tool()
def validate_index() -> str:
    """
    [清理] 移除"源文件已不存在"的模块【当前版本】。历史版本予以保留（作为回溯资产）。
    """
    modules = load_store("module")
    removed = 0
    kept = []
    for m in modules:
        # 只清理"当前版且源文件已不存在"的记录；历史版无条件保留
        if m.get("is_current") and not os.path.exists(m.get("path", "")):
            removed += 1
            continue
        kept.append(m)
    save_store("module", kept)
    current_count = len([m for m in kept if m.get("is_current")])
    return f"验证完成：移除了 {removed} 个失效的当前版模块，现有有效当前版模块 {current_count} 个（历史版本已保留）。"


if __name__ == "__main__":
    mcp.run()