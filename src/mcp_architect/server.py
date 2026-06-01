# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastmcp",
# ]
# ///

#!/usr/bin/env python3
import json
import os
import re
import time
import shutil
import difflib
from pathlib import Path
from fastmcp import FastMCP

# 1. 初始化 MCP Server
mcp = FastMCP("business-index-server")

# 2. 定义索引文件路径
# [改动] 基于脚本自身位置定位，而非进程 CWD。
# setup 会把本脚本复制进项目根目录，因此索引始终与脚本同目录，不受启动目录影响。
BASE_DIR = Path(__file__).parent
INDEX_FILE = BASE_DIR / "business_index.json"
BACKUP_FILE = BASE_DIR / "business_index.json.bak"

# --- 辅助函数 ---

def load_index():
    if not os.path.exists(INDEX_FILE):
        return {
            "project_meta": {"name": "Project Memory", "description": "Auto-generated business index"},
            "modules": [],
            "workflows": [],
            "decisions": []
        }
    with open(INDEX_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"modules": [], "workflows": [], "decisions": []}

def save_index(data):
    # [增强功能 1] 自动备份：每次写入前备份旧文件
    if os.path.exists(INDEX_FILE):
        shutil.copy(INDEX_FILE, BACKUP_FILE)

    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clean_json_string(s: str) -> str:
    """清理 Markdown 标记"""
    s = s.strip()
    s = re.sub(r"^```(json)?", "", s, flags=re.MULTILINE)
    s = re.sub(r"```$", "", s, flags=re.MULTILINE)
    return s.strip()

def calculate_match_score(query: str, text: str) -> float:
    """
    [增强功能 4] 搜索核心算法
    1. 包含匹配 (权重高)
    2. 模糊匹配 (difflib, 处理拼写错误或相似词)
    """
    if not text:
        return 0.0

    query = query.lower()
    text = text.lower()

    # 1. 精确/部分包含 (权重 1.0)
    if query in text:
        return 1.0

    # 2. 模糊匹配 (权重 0.8) - 处理 "paymnt" vs "payment"
    similarity = difflib.SequenceMatcher(None, query, text).ratio()
    if similarity > 0.6:
        return similarity * 0.8

    return 0.0

# --- MCP 工具定义 ---

@mcp.tool()
def search_business_index(keyword: str) -> str:
    """
    [智能搜索] 根据关键词搜索索引。支持模糊匹配。
    会优先匹配名称，其次是摘要。
    keyword: 搜索词，如 "Payment", "登录", "UserSchema"
    """
    data = load_index()
    results = []

    def process_items(items, item_type):
        for item in items:
            name_score = calculate_match_score(keyword, item.get('name', '')) * 2
            summary_score = calculate_match_score(keyword, item.get('summary', ''))
            id_score = calculate_match_score(keyword, item.get('id', ''))
            content_score = calculate_match_score(keyword, item.get('content', ''))

            score = max(name_score, summary_score, id_score, content_score)

            if score > 0.4:
                results.append({
                    "score": score,
                    "type": item_type,
                    "data": item
                })

    process_items(data.get("modules", []), "module")
    process_items(data.get("workflows", []), "workflow")
    process_items(data.get("decisions", []), "decision")

    results.sort(key=lambda x: x["score"], reverse=True)
    top_results = [r["data"] for r in results[:10]]

    if not top_results:
        # [改动] 空结果不再是死胡同，而是一条明确的行动指令。
        return (
            f"🔍 索引中暂无与 '{keyword}' 相关的记录。\n"
            f"这通常意味着该业务区域【尚未被索引】，而不是它不存在。\n"
            f"正确做法：直接阅读相关源码理解其逻辑，理解后调用 update_business_index 把它记录下来，"
            f"以便后续会话可以直接复用，而不必重新读代码。"
        )

    return json.dumps(top_results, ensure_ascii=False, indent=2)

@mcp.tool()
def check_stale_indexes() -> str:
    """
    [增强功能 2] 检查索引新鲜度。
    对比文件系统的修改时间与索引的 'last_updated' 时间。
    """
    data = load_index()
    modules = data.get("modules", [])

    # [改动] 优先区分"索引为空"与"有索引且都最新"，避免空索引时误报"一切最新"。
    if not modules:
        return (
            "📭 业务索引目前为空。这是新项目的正常状态，并不代表本工具无效。\n"
            "你现在处于【索引建立期】：此阶段的首要任务不是检索，而是边理解代码边记录。\n"
            "请在读懂相关模块 / 流程 / 技术决策后，调用 update_business_index 将其写入索引，"
            "为后续会话积累可复用的项目记忆。"
        )

    stale_items = []
    for m in modules:
        path = m.get("path")
        last_updated = m.get("last_updated", 0)

        if path and os.path.exists(path):
            file_mtime = os.path.getmtime(path)
            if file_mtime > last_updated:
                lag_hours = int((file_mtime - last_updated) / 3600)
                stale_items.append(
                    f"- {m.get('name')} (ID: {m.get('id')}): 代码已更新，索引滞后约 {lag_hours} 小时"
                )

    if not stale_items:
        return "✅ 已索引的模块目前都是最新的（基于文件修改时间）。"

    return "以下模块的索引可能已过期，建议读取代码并调用 update_business_index：\n" + "\n".join(stale_items)

@mcp.tool()
def generate_architecture_diagram() -> str:
    """
    [增强功能 3] 生成 Mermaid 格式的架构图代码。
    展示模块依赖和工作流关系。
    """
    data = load_index()
    mermaid = ["graph TD"]

    mermaid.append("    classDef module fill:#e1f5fe,stroke:#01579b,stroke-width:2px;")
    mermaid.append("    classDef workflow fill:#fff3e0,stroke:#ff6f00,stroke-width:2px,stroke-dasharray: 5 5;")

    for m in data.get("modules", []):
        safe_name = m.get('name', 'Unknown').replace(" ", "_")
        mermaid.append(f'    {m["id"]}["📦 {safe_name}"]:::module')

    for w in data.get("workflows", []):
        safe_flow_name = w.get('name', 'Flow').replace(" ", "_")
        flow_id = w.get("id", "flow_unk")
        mermaid.append(f'    {flow_id}(["🔄 {safe_flow_name}"]):::workflow')

        for dep_id in w.get("dependencies", []):
            mermaid.append(f'    {flow_id} -.-> {dep_id}')

    return "\n".join(mermaid)

@mcp.tool()
def update_business_index(updates: str) -> str:
    """
    更新业务索引。
    updates: JSON 字符串。会自动记录更新时间戳。
    """
    try:
        clean_updates = clean_json_string(updates)
        update_obj = json.loads(clean_updates)

        data = load_index()
        u_type = update_obj.get("type")
        u_data = update_obj.get("data")

        if not u_data:
            return "错误: 数据为空"

        u_data["last_updated"] = time.time()

        if u_type == "module":
            data["modules"] = [
                m for m in data["modules"]
                if m.get("id") != u_data.get("id") and m.get("name") != u_data.get("name")
            ]
            data["modules"].append(u_data)

        elif u_type == "workflow":
            data["workflows"] = [
                w for w in data["workflows"]
                if w.get("id") != u_data.get("id") and w.get("name") != u_data.get("name")
            ]
            data["workflows"].append(u_data)

        elif u_type == "decision":
            if "id" in u_data:
                data["decisions"] = [d for d in data["decisions"] if d.get("id") != u_data["id"]]
            data["decisions"].append(u_data)

        save_index(data)
        return "索引更新成功并已备份。"

    except Exception as e:
        return f"更新失败: {str(e)}"

@mcp.tool()
def get_business_index() -> str:
    data = load_index()
    return json.dumps(data, ensure_ascii=False, indent=2)

@mcp.tool()
def validate_index() -> str:
    data = load_index()
    valid_modules = [m for m in data.get("modules", []) if os.path.exists(m.get("path", ""))]
    data["modules"] = valid_modules
    save_index(data)
    return f"验证完成，现有有效模块 {len(valid_modules)} 个。"

if __name__ == "__main__":
    mcp.run()