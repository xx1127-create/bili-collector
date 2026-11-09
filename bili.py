"""B站接口客户端：视频搜索、UP主视频列表、粉丝数。

不需要登录，使用网页端公开接口。请求之间有间隔；如果 B 站提示请求太频繁（-412 / -352），
就暂停几分钟并放慢速度后再继续。
"""
import hashlib
import html
import random
import re
import threading
import time
import urllib.parse
import warnings

warnings.filterwarnings("ignore")
import requests  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
             33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
             61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
             36, 20, 34, 44, 52]

# 访问 UP 主空间接口时需要带上的浏览器指纹参数（固定值即可）
DM_PARAMS = {
    "platform": "web",
    "web_location": 1550101,
    "dm_img_list": "[]",
    "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
    "dm_cover_img_str": "QU5HTEUgKEFwcGxlLCBBTkdMRSBNZXRhbCBSZW5kZXJlcjogQXBwbGUgTTEsIFVuc3BlY2lmaWVkIFZlcnNpb24pR29vZ2xlIEluYy4gKEFwcGxlKQ",
    "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
}

RISK_CODES = {-412, -352, -509, -799}


class BiliError(Exception):
    pass


def clean_title(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_cookie(raw):
    """接受整段 Cookie（k=v; k2=v2），或只粘贴 SESSDATA 的值。"""
    raw = (raw or "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:].strip()
    if not raw:
        return {}
    if "=" not in raw:
        return {"SESSDATA": raw}
    out = {}
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k and v:
            out[k.strip()] = v.strip()
    return out


def fmt_date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""


class QRLogin:
    """B站官方网页扫码登录：生成二维码 → 用户用手机 App 扫码确认 → 拿到登录 Cookie。"""
    STATES = {86101: "waiting", 86090: "scanned", 86038: "expired", 0: "success"}

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        d = self.s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                       timeout=10).json()["data"]
        self.url, self.key = d["url"], d["qrcode_key"]

    def poll(self):
        """返回 (状态, cookie 字符串)。状态：waiting / scanned / expired / success / error"""
        d = self.s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                       params={"qrcode_key": self.key}, timeout=10).json().get("data") or {}
        state = self.STATES.get(d.get("code"), "error")
        if state != "success":
            return state, ""
        self.s.get("https://www.bilibili.com/", timeout=10)  # 顺便拿到 buvid3 等基础 cookie
        jar = {c.name: c.value for c in self.s.cookies if c.domain.endswith("bilibili.com")}
        return state, "; ".join(f"{k}={v}" for k, v in jar.items())


class BiliClient:
    def __init__(self, interval=3.0, log=print, cooldown=180, stop_event=None, cookie=""):
        self.interval = interval
        self.stop_event = stop_event or threading.Event()
        self.cooldown = cooldown  # 被限流后的暂停秒数（逐次加长）
        self.log = log
        self.cookie = cookie
        self._last = 0.0
        self._mixin_key = None
        self._key_time = 0
        self._new_session()

    # ---------- 会话与签名 ----------
    def _new_session(self):
        s = requests.Session()
        s.headers.update({"User-Agent": UA,
                          "Referer": "https://www.bilibili.com/",
                          "Origin": "https://www.bilibili.com"})
        for k, v in parse_cookie(self.cookie).items():
            s.cookies.set(k, v, domain=".bilibili.com")
        try:
            s.get("https://www.bilibili.com/", timeout=10)
            if "buvid3" not in s.cookies:
                d = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=10).json()["data"]
                s.cookies.set("buvid3", d["b_3"], domain=".bilibili.com")
                s.cookies.set("buvid4", d["b_4"], domain=".bilibili.com")
        except Exception as e:  # 拿不到也先继续，请求失败时会重试
            self.log(f"初始化会话失败：{e}")
        self.s = s
        self._mixin_key = None

    def whoami(self):
        """返回 (是否登录, 用户名)"""
        d = self.s.get("https://api.bilibili.com/x/web-interface/nav", timeout=10).json().get("data") or {}
        return bool(d.get("isLogin")), d.get("uname", "")

    def _get_mixin_key(self):
        if self._mixin_key and time.time() - self._key_time < 3600:
            return self._mixin_key
        img = self.s.get("https://api.bilibili.com/x/web-interface/nav", timeout=10).json()["data"]["wbi_img"]
        ik = img["img_url"].rsplit("/", 1)[1].split(".")[0]
        sk = img["sub_url"].rsplit("/", 1)[1].split(".")[0]
        self._mixin_key = "".join((ik + sk)[i] for i in MIXIN_TAB)[:32]
        self._key_time = time.time()
        return self._mixin_key

    def _sign(self, params):
        p = dict(params)
        p["wts"] = int(time.time())
        p = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in sorted(p.items())}
        p["w_rid"] = hashlib.md5((urllib.parse.urlencode(p) + self._get_mixin_key()).encode()).hexdigest()
        return p

    def _throttle(self):
        wait = self.interval * random.uniform(0.8, 1.4) - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _get(self, url, params, wbi=True, retries=3):
        for attempt in range(retries):
            self._throttle()
            try:
                r = self.s.get(url, params=self._sign(params) if wbi else params, timeout=15)
                if r.status_code == 412:
                    code, msg = -412, "HTTP 412"
                else:
                    j = r.json()
                    code, msg = j.get("code"), j.get("message")
                    data = j.get("data") or {}
                    if code == 0 and isinstance(data, dict) and data.get("v_voucher") \
                            and not data.get("result") and not data.get("list"):
                        # 被风控时 B站仍返回 code 0，但只给一个验证凭证、没有数据
                        code, msg = -352, "风控验证（返回了空结果）"
                    elif code == 0:
                        return data
            except Exception as e:
                code, msg = None, str(e)
            if code is not None and code not in RISK_CODES:
                raise BiliError(f"{code} {msg}")
            if code in RISK_CODES:
                # 被 B 站限流：老老实实暂停一段时间再试，之后的请求也整体放慢
                pause = self.cooldown * (attempt + 1)
                self.interval = min(self.interval * 1.5, 10)
                tip = "" if self.cookie else "（建议先扫码登录）"
                self.log(f"  B站限流了（{code} {msg}），暂停 {pause // 60} 分钟后继续，"
                         f"请求间隔调为 {self.interval:.1f} 秒{tip}…")
            else:
                pause = 10 * (attempt + 1)
                self.log(f"  网络错误（{msg}），{pause} 秒后重试…")
            if self.stop_event.wait(pause):
                raise BiliError("已停止")
        raise BiliError(f"多次重试失败：{code} {msg}")

    # ---------- 业务接口 ----------
    def search_videos(self, keyword, page=1, order="totalrank", duration=0,
                      pubtime_begin=None, pubtime_end=None):
        """返回 (视频列表, 总页数)。order: totalrank/click/pubdate/dm/stow"""
        params = {"search_type": "video", "keyword": keyword, "page": page,
                  "order": order, "duration": duration}
        if pubtime_begin:
            params["pubtime_begin_s"] = int(pubtime_begin)
            params["pubtime_end_s"] = int(pubtime_end or time.time())
        d = self._get("https://api.bilibili.com/x/web-interface/wbi/search/type", params)
        out = []
        for v in d.get("result") or []:
            if v.get("type") != "video" or not v.get("bvid"):
                continue
            out.append({
                "bvid": v["bvid"],
                "title": clean_title(v.get("title")),
                "url": f"https://www.bilibili.com/video/{v['bvid']}",
                "mid": v.get("mid"),
                "author": v.get("author", ""),
                "play": v.get("play") if isinstance(v.get("play"), int) else 0,
                "pubdate": fmt_date(v.get("pubdate")),
                "duration": v.get("duration", ""),
                "typename": v.get("typename", ""),
                "tags": v.get("tag", ""),
                "description": clean_title(v.get("description"))[:300],
            })
        return out, d.get("numPages") or 0

    def channel_videos(self, mid, page=1, ps=50):
        """返回 (视频列表, 视频总数)"""
        params = {"mid": mid, "ps": ps, "pn": page, "order": "pubdate", **DM_PARAMS}
        d = self._get("https://api.bilibili.com/x/space/wbi/arc/search", params)
        total = (d.get("page") or {}).get("count", 0)
        out = []
        for v in ((d.get("list") or {}).get("vlist")) or []:
            out.append({
                "bvid": v["bvid"],
                "title": clean_title(v.get("title")),
                "url": f"https://www.bilibili.com/video/{v['bvid']}",
                "mid": mid,
                "author": v.get("author", ""),
                "play": v.get("play") if isinstance(v.get("play"), int) else 0,
                "pubdate": fmt_date(v.get("created")),
                "duration": v.get("length", ""),
                "typename": "",
                "tags": "",
                "description": (v.get("description") or "")[:300],
            })
        return out, total

    def follower_count(self, mid):
        try:
            d = self._get("https://api.bilibili.com/x/relation/stat", {"vmid": mid}, wbi=False, retries=2)
            return d.get("follower")
        except BiliError:
            return None
