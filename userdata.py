"""你的设置（类别结构、筛选规则、词库）都放在 data/ 文件夹里，和程序代码分开。
更新程序（git pull、下载新版、页面上的「更新程序」）都不会碰 data/，所以设置和抓到的数据不会丢。

- 每次保存前，把旧版本自动备份到 data/backups/，每种保留最近 BACKUP_KEEP 份
- 可以把全部设置导出成一个文件，换电脑或重装后再导入
- 旧版本把 categories.json、filter_rules.json 放在程序目录里，启动时自动搬进 data/
"""
import glob
import json
import os
import shutil
import time

BACKUP_KEEP = 50


def prepare(path, legacy=None, default=None):
    """确保 data/ 里有这个设置文件：没有的话，先搬旧位置的文件，再用 defaults/ 里的默认值。返回做了什么。"""
    if os.path.exists(path):
        return ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if legacy and os.path.exists(legacy):
        shutil.move(legacy, path)
        return f"已把 {os.path.basename(legacy)} 从程序目录搬到 data 文件夹"
    if default and os.path.exists(default):
        shutil.copy2(default, path)
    return ""


def backup_dir(path):
    return os.path.join(os.path.dirname(path), "backups")


def backup(path, kind):
    """把现有文件复制一份到 data/backups/，文件名带时间；超过 BACKUP_KEEP 份删掉最旧的"""
    if not os.path.exists(path):
        return None
    d = backup_dir(path)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, f"{kind}_{time.strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copy2(path, dst)
    for old in sorted(glob.glob(os.path.join(d, f"{kind}_*.json")))[:-BACKUP_KEEP]:
        os.remove(old)
    return dst


def save_json(path, obj, kind):
    """保存设置文件：内容有变化时先备份旧的，再整体替换（写一半断电也不会损坏）"""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return
        backup(path, kind)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def bundle(categories, reviewer):
    """全部设置打包成一个文件的内容"""
    return {"app": "B站视频采集器", "kind": "settings", "version": 1,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "categories": categories.tree, "filter_rules": reviewer.rules()}


def import_bundle(data, categories, reviewer):
    """导入设置。认得三种文件：导出的设置文件、旧版的 categories.json、旧版的 filter_rules.json。
    导入前的设置会自动备份。返回导入了什么。"""
    tree = rules = None
    if isinstance(data, list):
        tree = data
    elif isinstance(data, dict) and data.get("kind") == "settings":
        tree, rules = data.get("categories"), data.get("filter_rules")
    elif isinstance(data, dict) and ("custom_rules" in data or "related_words" in data or "word_lists" in data):
        rules = data
    else:
        raise ValueError("认不出这个文件：请选择从这里导出的设置文件，或旧版的 categories.json / filter_rules.json")
    done = []
    if tree is not None:
        categories.set_tree(tree)
        done.append(f"类别结构（{len(tree)} 个主类别）")
    if rules is not None:
        r = reviewer.normalize(rules)
        reviewer.save_rules({**reviewer.rules(), **r})
        done.append(f"筛选规则和词库（{len(r.get('word_lists', []))} 个词表，{len(r.get('custom_rules', []))} 条自定义规则）")
    return done
