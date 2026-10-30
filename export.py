"""把数据库导出成 Excel（给人看）和 JSON（给 Claude 读，按频道分组）。"""
import json
import os
import time

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

HEADER_FILL = PatternFill("solid", fgColor="FB7299")
HEADER_FONT = Font(bold=True, color="FFFFFF")
LINK_FONT = Font(color="0563C1", underline="single")


def _sheet(wb, title, headers, rows, widths, link_cols=()):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for r in rows:
        ws.append(r)
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
