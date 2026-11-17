"""更新程序本身，不碰 data/（设置和数据）、output/（导出的文件）、.venv/（运行环境）。
- 在线更新：用 git 下载的 → git pull；下载 ZIP 解压的 → 从 GitHub 下载最新 ZIP，只把程序文件覆盖过去
- 离线更新：别人发来的程序 ZIP（GitHub 上 Code → Download ZIP 下载的那个），在页面上选它，同样只覆盖程序文件
版本号：version.txt 在 GitHub 打包 ZIP 时会自动填上提交号和日期（.gitattributes 里的 export-subst）。
更新后要关掉黑色窗口、重新双击启动，新版本才生效（启动脚本会顺便装好新依赖）。
"""
import io
import os
import shutil
import subprocess
import time
import zipfile

import requests

REPO = "xx1127-create/bili-collector"
BRANCH = "main"
KEEP = {"data", "output", ".venv", ".git", "__MACOSX"}  # 这些不覆盖
CACHE_SEC = 6 * 3600  # 打开页面时的自动检查，6 小时内只真正查一次


def _parse_version(text):
    """version.txt 的内容「提交号 日期」→ {"sha", "date"}；没被填过（git 克隆的）返回 None"""
    parts = (text or "").split()
    if len(parts) == 2 and "$" not in text:
        return {"sha": parts[0], "date": parts[1]}
    return None


class Updater:
    def __init__(self, base):
        self.base = base
        self._cache = (0, None)

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
        """当前版本 {"sha", "date"}，不知道时返回 None"""
        if self.uses_git():
            sha, _, date = self._git("log", "-1", "--format=%H %cs").stdout.strip().partition(" ")
            return {"sha": sha, "date": date}
        p = os.path.join(self.base, "version.txt")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return _parse_version(f.read())
        return None

    def latest(self):
        r = requests.get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}", timeout=15,
                         headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        c = r.json()
        return {"sha": c["sha"], "date": c["commit"]["committer"]["date"][:10],
                "message": c["commit"]["message"].split("\n")[0]}

    def check(self, cached=False):
        if cached and self._cache[1] and time.time() - self._cache[0] < CACHE_SEC:
            return self._cache[1]
        cur = self.current()
        if self.uses_git():  # 本地可能有还没推送的提交，所以比较「落后几个提交」，而不是比较版本号
            f = self._git("fetch", "-q", "origin", BRANCH)
            if f.returncode != 0:
                raise RuntimeError("连不上 GitHub：" + f.stderr.strip())
            behind = int(self._git("rev-list", "--count", f"HEAD..origin/{BRANCH}").stdout.strip() or 0)
            date, _, msg = self._git("log", "-1", "--format=%cs%x09%s", f"origin/{BRANCH}").stdout.strip().partition("\t")
            res = {"current": cur, "latest": {"date": date, "message": msg}, "known": True,
                   "up_to_date": behind == 0, "behind": behind, "method": "git"}
        else:
            new = self.latest()
            res = {"current": cur, "latest": new, "known": bool(cur),
                   "up_to_date": bool(cur) and cur["sha"] == new["sha"], "method": "zip"}
        self._cache = (time.time(), res)
        return res

    # ---------- 用 ZIP 更新 ----------
    @staticmethod
    def inspect_zip(data):
        """检查是不是本程序的 ZIP。返回 (ZipFile, 程序所在的目录前缀, 版本)"""
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise RuntimeError("这不是 ZIP 文件")
        names = set(z.namelist())
        prefixes = sorted((n[:-len("app.py")] for n in names if n == "app.py" or n.endswith("/app.py")), key=len)
        prefix = next((p for p in prefixes if p + "review.py" in names and p + "categories.py" in names), None)
        if prefix is None:
            raise RuntimeError("这个 ZIP 里不是 B站视频采集器的程序（找不到 app.py）")
        ver = _parse_version(z.read(prefix + "version.txt").decode("utf-8", "replace")) \
            if prefix + "version.txt" in names else None
        return z, prefix, ver

    def apply_zip(self, data):
        """把 ZIP 里的程序文件覆盖到程序目录，返回覆盖了几个文件"""
        z, prefix, _ = self.inspect_zip(data)
        n = 0
        for info in z.infolist():
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            parts = info.filename[len(prefix):].split("/")
            if parts[0] in KEEP or ".." in parts or parts[-1] == ".DS_Store":
                continue
            dst = os.path.join(self.base, *parts)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            is_new = not os.path.exists(dst)
            with z.open(info) as src, open(dst, "wb") as out:  # 覆盖已有文件会保留它原来的权限
                shutil.copyfileobj(src, out)
            if is_new and (info.external_attr >> 16) & 0o111:
                os.chmod(dst, 0o755)
            n += 1
        self._cache = (0, None)
        return n

    def update(self):
        """在线更新。返回一句话说明结果；失败抛 RuntimeError"""
        if self.uses_git():
            r = self._git("pull", "--ff-only")
            if r.returncode != 0:
                raise RuntimeError("git pull 失败：" + (r.stderr or r.stdout).strip())
            self._cache = (0, None)
            return "已更新（git pull）" if "Already up to date" not in r.stdout else "已经是最新版本"
        new = self.latest()
        r = requests.get(f"https://codeload.github.com/{REPO}/zip/{new['sha']}", timeout=120)
        r.raise_for_status()
        n = self.apply_zip(r.content)
        return f"已更新到 {new['date']} 的版本（{n} 个程序文件）"
