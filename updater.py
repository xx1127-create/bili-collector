"""从 GitHub 更新程序本身，不碰 data/（设置和数据）、output/（导出的文件）、.venv/（运行环境）。
- 用 git 下载的：git pull
- 下载 ZIP 解压的：下载最新 ZIP，只把程序文件覆盖过去；当前版本记在 version.json
更新后要关掉黑色窗口、重新双击启动，新版本才生效（启动脚本会顺便装好新依赖）。
"""
import io
import json
import os
import shutil
import subprocess
import zipfile

import requests

REPO = "xx1127-create/bili-collector"
BRANCH = "main"
KEEP = {"data", "output", ".venv", ".git", "version.json"}  # 这些不覆盖


class Updater:
    def __init__(self, base):
        self.base = base
        self.version_file = os.path.join(base, "version.json")

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.base, *args], capture_output=True, text=True, timeout=120)

    def uses_git(self):
        if not os.path.isdir(os.path.join(self.base, ".git")) or not shutil.which("git"):
            return False
        try:
            return self._git("rev-parse", "HEAD").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def current(self):
        if self.uses_git():
            return self._git("rev-parse", "HEAD").stdout.strip()
        if os.path.exists(self.version_file):
            with open(self.version_file, encoding="utf-8") as f:
                return json.load(f).get("sha", "")
        return ""

    def latest(self):
        r = requests.get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}", timeout=15,
                         headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        c = r.json()
        return {"sha": c["sha"], "date": c["commit"]["committer"]["date"][:10],
                "message": c["commit"]["message"].split("\n")[0]}

    def check(self):
        if self.uses_git():  # 本地可能有还没推送的提交，所以比较「落后几个提交」，而不是比较版本号
            f = self._git("fetch", "-q", "origin", BRANCH)
            if f.returncode != 0:
                raise RuntimeError("连不上 GitHub：" + f.stderr.strip())
            behind = int(self._git("rev-list", "--count", f"HEAD..origin/{BRANCH}").stdout.strip() or 0)
            date, _, msg = self._git("log", "-1", "--format=%cs%x09%s", f"origin/{BRANCH}").stdout.strip().partition("\t")
            return {"current": self.current()[:7], "latest": {"date": date, "message": msg}, "known": True,
                    "up_to_date": behind == 0, "behind": behind, "method": "git"}
        cur, new = self.current(), self.latest()
        return {"current": cur[:7], "latest": new, "known": bool(cur),
                "up_to_date": cur == new["sha"], "method": "zip"}

    def update(self):
        """返回一句话说明结果；失败抛 RuntimeError"""
        if self.uses_git():
            r = self._git("pull", "--ff-only")
            if r.returncode != 0:
                raise RuntimeError("git pull 失败：" + (r.stderr or r.stdout).strip())
            return "已更新（git pull）" if "Already up to date" not in r.stdout else "已经是最新版本"
        new = self.latest()
        r = requests.get(f"https://codeload.github.com/{REPO}/zip/{new['sha']}", timeout=120)
        r.raise_for_status()
        n = 0
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for info in z.infolist():
                rel = info.filename.split("/", 1)[1] if "/" in info.filename else ""
                if not rel or info.is_dir() or rel.split("/")[0] in KEEP or ".." in rel.split("/"):
                    continue
                dst = os.path.join(self.base, *rel.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                is_new = not os.path.exists(dst)
                with z.open(info) as src, open(dst, "wb") as out:  # 覆盖已有文件会保留它原来的权限
                    shutil.copyfileobj(src, out)
                mode = info.external_attr >> 16
                if is_new and mode & 0o111:
                    os.chmod(dst, 0o755)
                n += 1
        with open(self.version_file, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False)
        return f"已更新到 {new['date']} 的版本（{n} 个程序文件）"
