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
from fastmcp import FastMCP

# 1. 初始化 MCP Server
mcp = FastMCP("business-index-server")

# 2. 定义索引文件路径
INDEX_FILE = "business_index.json"
BACKUP_FILE = "business_index.json.bak"

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
    # ratio() 返回 0-1 之间的相似度
    similarity = difflib.SequenceMatcher(None, query, text).ratio()
    if similarity > 0.6: # 设定一个门槛
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
    
    # 统一处理 modules, workflows, decisions
    # 给不同字段设置权重: Name > Summary > ID
    
    def process_items(items, item_type):
        for item in items:
            score = 0
            # 名称匹配 (权重 x 2)
            name_score = calculate_match_score(keyword, item.get('name', '')) * 2
            # 摘要匹配
            summary_score = calculate_match_score(keyword, item.get('summary', ''))
            # ID 匹配
            id_score = calculate_match_score(keyword, item.get('id', ''))
            # 内容(决策)匹配
            content_score = calculate_match_score(keyword, item.get('content', ''))

            score = max(name_score, summary_score, id_score, content_score)
            
            if score > 0.4: # 过滤低相关度
                results.append({
                    "score": score,
                    "type": item_type,
                    "data": item
                })

    process_items(data.get("modules", []), "module")
    process_items(data.get("workflows", []), "workflow")
    process_items(data.get("decisions", []), "decision")
    
    # 按相关度降序排列
    results.sort(key=lambda x: x["score"], reverse=True)
    
    # 移除 score 字段，只返回数据，且限制返回数量防止 Token 爆炸
    top_results = [r["data"] for r in results[:10]] # 只返回前10个最相关的
    
    if not top_results:
        return f"在索引中未找到与 '{keyword}' 相关的记录 (已启用模糊匹配)。"
        
    return json.dumps(top_results, ensure_ascii=False, indent=2)

@mcp.tool()
def check_stale_indexes() -> str:
    """
    [增强功能 2] 检查索引新鲜度。
    对比文件系统的修改时间与索引的 'last_updated' 时间。
    """
    data = load_index()
    stale_items = []
    current_time = time.time()
    
    for m in data.get("modules", []):
        path = m.get("path")
        last_updated = m.get("last_updated", 0) # 默认为0，表示很老
        
        if path and os.path.exists(path):
            file_mtime = os.path.getmtime(path)
            # 如果文件修改时间 比 索引时间 晚了超过 24小时 (86400秒)
            # 或者 last_updated 根本不存在
            if file_mtime > last_updated:
                # 计算滞后时间
                lag_hours = int((file_mtime - last_updated) / 3600)
                stale_items.append(f"- {m.get('name')} (ID: {m.get('id')}): 代码已更新，索引滞后约 {lag_hours} 小时")
    
    if not stale_items:
        return "所有模块索引目前都是最新的（基于文件修改时间）。"
        
    return "以下模块的索引可能已过期，建议读取代码并调用 update_business_index：\n" + "\n".join(stale_items)

@mcp.tool()
def generate_architecture_diagram() -> str:
    """
    [增强功能 3] 生成 Mermaid 格式的架构图代码。
    展示模块依赖和工作流关系。
    """
    data = load_index()
    mermaid = ["graph TD"]
    
    # 样式定义
    mermaid.append("    classDef module fill:#e1f5fe,stroke:#01579b,stroke-width:2px;")
    mermaid.append("    classDef workflow fill:#fff3e0,stroke:#ff6f00,stroke-width:2px,stroke-dasharray: 5 5;")
    
    # 1. 绘制模块
    for m in data.get("modules", []):
        safe_name = m.get('name', 'Unknown').replace(" ", "_")
        mermaid.append(f'    {m["id"]}["📦 {safe_name}"]:::module')
        
        # 模块间的引用（如果有 explicit dependencies 字段，这里假设通过 workflows 关联，暂时只画节点）
        
    # 2. 绘制工作流及其依赖
    for w in data.get("workflows", []):
        safe_flow_name = w.get('name', 'Flow').replace(" ", "_")
        flow_id = w.get("id", "flow_unk")
        mermaid.append(f'    {flow_id}(["🔄 {safe_flow_name}"]):::workflow')
        
        # 绘制 流程 -> 依赖模块
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
        
        if not u_data: return "错误: 数据为空"

        # [自动注入时间戳]
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
        return f"索引更新成功并已备份。"
        
    except Exception as e:
        return f"更新失败: {str(e)}"

# 保留原有的 validate_index 和 get_business_index (此处省略以节省篇幅，实际部署时请保留)
# ... (insert validate_index and get_business_index here if needed, or use previous version)

# 为了完整性，这里补充上 validate_index 和 get_business_index
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