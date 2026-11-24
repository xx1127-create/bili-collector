"""把数据库导出成 Excel（给人看）和 JSON（给 Claude 读，按频道分组）。"""
import json
import os
import time

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill

HEADER_FILL = PatternFill("solid", fgColor="FB7299")
HEADER_FONT = Font(bold=True, color="FFFFFF")
LINK_FONT = Font(color="0563C1", underline="single")


def _clean(v):
    """去掉 Excel 不允许的隐藏控制字符（有的 B站标题里带着），否则整个文件写不出来"""
    return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v


def _sheet(wb, title, headers, rows, widths, link_cols=()):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for r in rows:
        ws.append([_clean(v) for v in r])
    for col in link_cols:
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            c = row[0]
            if c.value:
                c.hyperlink, c.font = c.value, LINK_FONT
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    ws.freeze_panes = "A2"


def export_all(store, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    hits = store.q("""
        SELECT h.category, h.keyword, v.title, v.url, v.author, v.mid, v.play, v.pubdate,
               v.duration, v.typename, v.tags, v.bvid
        FROM hits h JOIN videos v ON v.bvid = h.bvid
        ORDER BY h.category, h.keyword, v.play DESC""")
    channels = store.q("""
        SELECT c.*, COUNT(DISTINCT h.bvid) AS hit_count,
               GROUP_CONCAT(DISTINCT h.category) AS cats
        FROM channels c
        LEFT JOIN videos v ON v.mid = c.mid
        LEFT JOIN hits h ON h.bvid = v.bvid
        GROUP BY c.mid ORDER BY hit_count DESC, c.fans DESC""")
    vids = store.q("""SELECT v.mid, v.author, v.title, v.url, v.play, v.pubdate, v.duration, v.bvid
                      FROM videos v JOIN channels c ON c.mid = v.mid
                      ORDER BY v.mid, v.pubdate DESC""")
    names = {c["mid"]: c["name"] for c in channels}

    wb = Workbook()
    wb.remove(wb.active)
    _sheet(wb, "搜索结果",
           ["类别", "关键词", "视频标题", "视频链接", "UP主", "UP主主页", "播放量", "发布日期", "时长", "分区", "标签", "BV号"],
           [[h["category"], h["keyword"], h["title"], h["url"], h["author"],
             f"https://space.bilibili.com/{h['mid']}", h["play"], h["pubdate"], h["duration"],
             h["typename"], h["tags"], h["bvid"]] for h in hits],
           [24, 14, 60, 42, 18, 34, 12, 12, 10, 12, 40, 14], link_cols=(4, 6))
    _sheet(wb, "频道",
           ["UP主", "UP主主页", "粉丝数", "视频总数", "已取视频数", "搜索命中视频数", "命中类别", "展开状态", "抓取时间"],
           [[c["name"], c["url"], c["fans"], c["total_videos"], c["fetched_videos"], c["hit_count"],
             (c["cats"] or "").replace(",", "；"), c["status"], c["crawled_at"]] for c in channels],
           [20, 34, 12, 10, 10, 14, 50, 10, 20], link_cols=(2,))
    _sheet(wb, "频道视频",
           ["UP主", "UP主主页", "视频标题", "视频链接", "播放量", "发布日期", "时长", "BV号"],
           [[names.get(v["mid"]) or v["author"], f"https://space.bilibili.com/{v['mid']}", v["title"],
             v["url"], v["play"], v["pubdate"], v["duration"], v["bvid"]] for v in vids],
           [20, 34, 60, 42, 12, 12, 10, 14], link_cols=(2, 4))
    xlsx = os.path.join(out_dir, "B站数据.xlsx")
    wb.save(xlsx)

    by_mid = {}
    for v in vids:
        by_mid.setdefault(v["mid"], []).append(
            {"标题": v["title"], "链接": v["url"], "播放": v["play"], "发布": v["pubdate"]})
    data = {
        "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "统计": store.stats(),
        "频道": [{
            "UP主": c["name"], "主页": c["url"], "粉丝数": c["fans"], "视频总数": c["total_videos"],
            "展开状态": c["status"], "命中类别": (c["cats"] or "").split(",") if c["cats"] else [],
            "搜索命中视频数": c["hit_count"], "视频": by_mid.get(c["mid"], []),
        } for c in channels],
        "搜索结果": [{
            "类别": h["category"], "关键词": h["keyword"], "标题": h["title"], "链接": h["url"],
            "UP主": h["author"], "UP主主页": f"https://space.bilibili.com/{h['mid']}",
            "播放": h["play"], "发布": h["pubdate"], "分区": h["typename"], "标签": h["tags"],
        } for h in hits],
    }
    js = os.path.join(out_dir, "B站数据.json")
    with open(js, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return [xlsx, js]


# ---------------- 筛选结果导出 ----------------
AI_PROMPT = """你是 B 站频道审核员。下面是按规则初筛后「入选」的频道，每个频道附有已抓到的视频标题。
请逐个复核，判断是否保留，标准如下：
1. 频道内容要和它所属的类别相关：跟类别无关的视频不能超过三分之一。
2. 科技类频道：讲中国自主研制品牌（华为、小米、比亚迪等国产品牌）的视频不能超过三分之一。
3. 考古类频道：讲鉴宝、古玩鉴定、收藏估价的视频不能超过三分之一。
4. 如果频道内容很杂（例如生活、美食、旅游混在一起），标注「雜」。

请输出一张表格，每个频道一行，列为：
| 原频道名 | 新名字 | 频道主页 | 结论（保留 / 剔除） | 理由（一句话） | Tags（一级类别，内容杂则写「雜」） |
"""


def _rule_words(r, c):
    """规则用到的词：所选词表（连同里面的词）和另加的词"""
    lists = {wl["id"]: wl for wl in r.get("word_lists", [])}
    parts = [f"【{lists[i]['name']}】词表（{lists[i]['words']}）" for i in c.get("lists", []) if i in lists]
    if c.get("words"):
        parts.append(f"「{c['words']}」")
    return " 或 ".join(parts)


def rule_summary(r):
    """把启用的规则写成一句句人话"""
    out = []
    if r["unrelated_on"]:
        out.append(f"无关视频 ≤ {r['unrelated_max']}%")
    if r["domestic_on"]:
        out.append(f"{r['domestic_scope']}类国产品牌视频 ≤ {r['domestic_max']}%")
    if r["jianbao_on"]:
        out.append(f"{r['jianbao_scope']}类鉴宝视频 ≤ {r['jianbao_max']}%")
    if r["fans_on"]:
        out.append(f"粉丝 ≤ {r['fans_max']}")
    fields = {"fans": "粉丝数", "total_videos": "视频总数", "days_since_update": "停更天数", "avg_play": "平均播放量",
              "low_res": "低于720P的视频占比(%)"}
    for c in r["custom_rules"]:
        if not c["enabled"]:
            continue
        scope = f"{c['scope']}类频道" if c["scope"] else "所有频道"
        op = {">": "超过", "<": "低于"}[c["op"]]
        if c["type"] == "title_ratio":
            out.append(f"{c['label']}：{scope}标题含 {_rule_words(r, c)} 里的词的视频{op} {c['value']}% 则筛掉")
        elif c["type"] == "name_contains":
            out.append(f"{c['label']}：{scope}频道名含 {_rule_words(r, c)} 里的词则筛掉")
        else:
            out.append(f"{c['label']}：{scope}{fields[c['field']]}{op} {c['value']:g} 则筛掉")
    return out or ["（没有启用任何规则）"]


def _free_name(out_dir, stem, ext):
    """同一天导出多次时不覆盖：B站_20261111.xlsx、B站_20261111_2.xlsx …"""
    name, i = f"{stem}{ext}", 1
    while os.path.exists(os.path.join(out_dir, name)):
        i += 1
        name = f"{stem}_{i}{ext}"
    return os.path.join(out_dir, name)


def export_review(reviewer, store, out_dir, template_path, max_titles=300, include_exported=False):
    """导出两个文件：给 AI 复核的 Markdown，和按标准模版填好的 Excel。只包含「入选」的频道；
    默认跳过以前导出过的，导出后给这些频道记上导出时间和文件名。"""
    os.makedirs(out_dir, exist_ok=True)
    day = time.strftime("%Y%m%d")
    rows = reviewer.list(decision="通过", new_only=not include_exported)
    skipped = 0 if include_exported else reviewer.summary()["通过"] - len(rows)
    if not rows:
        return {"files": [], "count": 0, "skipped": skipped}
    r = reviewer.rules()

    # 1) 给 AI 审查
    lines = [f"# B站频道复核清单（{time.strftime('%Y-%m-%d %H:%M')}）", "", AI_PROMPT,
             "初筛时启用的规则：" + "；".join(rule_summary(r)) + "。",
             f"共 {len(rows)} 个频道。", ""]
    for i, c in enumerate(rows, 1):
        vids = store.q("SELECT title FROM videos WHERE mid = ? ORDER BY pubdate DESC", (c["mid"],))
        cats = store.q("""SELECT DISTINCT h.category FROM hits h JOIN videos v ON v.bvid = h.bvid
                          WHERE v.mid = ?""", (c["mid"],))
        stats = [f"无关 {c['unrelated_ratio']}%"] if c["unrelated_ratio"] is not None else []
        if c["domestic_ratio"] is not None:
            stats.append(f"国产品牌 {c['domestic_ratio']}%")
        if c["jianbao_ratio"] is not None:
            stats.append(f"鉴宝 {c['jianbao_ratio']}%")
        stats += [f"{k} {v}" for k, v in c["custom_stats"].items()]
        lines += [f"## {i}. {c['name']}（新名字：{c['channel_name'] or c['name']}）",
                  f"- 主页：{c['url']}",
                  f"- 粉丝：{c['fans']}　B站上视频总数：{c['total_videos']}　已抓到：{len(vids)}",
                  f"- 命中类别：{'；'.join(x['category'] for x in cats)}",
                  f"- 规则统计：{'，'.join(stats)}" + ("（手动改判为入选）" if c["manual_decision"] else "")]
        if c["note"]:
            lines.append(f"- 备注：{c['note']}")
        shown = vids[:max_titles]
        lines.append(f"- 视频标题（共 {len(vids)} 个" + (f"，以下是最新的 {max_titles} 个" if len(vids) > max_titles else "") + "）：")
        lines += [f"  - {v['title']}" for v in shown]
        lines.append("")
    md = _free_name(out_dir, f"给AI审查_{day}", ".md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 2) 标准格式（按模版填写，第 3 行开始是数据）
    wb = load_workbook(template_path)
    ws = wb["B站汇总"] if "B站汇总" in wb.sheetnames else wb.active
    for row in ws.iter_rows(min_row=3, max_row=max(ws.max_row, 3)):
        for cell in row:
            cell.value = None
    for i, c in enumerate(rows, 3):
        ws.cell(i, 1, c["url"])
        ws.cell(i, 2, c["total_videos"])
        ws.cell(i, 3, "Channel")
        ws.cell(i, 4, None)  # PublishDate 留空
        ws.cell(i, 5, _clean(c["channel_name"] or c["name"]))
        ws.cell(i, 6, c["top_category"])
    xlsx = _free_name(out_dir, f"B站_{day}", ".xlsx")
    wb.save(xlsx)
    reviewer.mark_exported([c["mid"] for c in rows], os.path.basename(xlsx))
    return {"files": [md, xlsx], "count": len(rows), "skipped": skipped}
