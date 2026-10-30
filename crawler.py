"""抓取任务：① 按关键词搜索视频  ② 展开搜到的 UP 主（频道），列出其全部视频。在后台线程运行。"""
import collections
import math
import threading
import time

from bili import BiliClient, BiliError


class Job:
    def __init__(self, store, categories, exporter):
        self.store = store
        self.categories = categories
        self.exporter = exporter
        self.thread = None
        self.stop_flag = threading.Event()
        self.logs = collections.deque(maxlen=800)
        self.state = {"running": False, "phase": "空闲", "progress": "", "last_export": None}

    def log(self, msg):
        self.logs.append(f"{time.strftime('%H:%M:%S')}  {msg}")

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, opts):
        if self.running:
            raise RuntimeError("已有任务在运行")
        kmap = self.categories.keyword_map(opts.get("node_ids", []))
        if not kmap:
            raise ValueError("请至少勾选一个类别")
        self.stop_flag.clear()
        self.logs.clear()
        self.thread = threading.Thread(target=self._run, args=(kmap, opts), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        self.log("收到停止指令，当前请求完成后停止…")

    # ------------------------------------------------------------
    def _run(self, kmap, o):
        st = self.state
        st.update(running=True, phase="搜索", progress="")
        client = BiliClient(interval=float(o.get("interval", 3)), log=self.log,
                            stop_event=self.stop_flag, cookie=o.get("cookie", ""))
        if o.get("cookie"):
            try:
                ok, uname = client.whoami()
                self.log(f"已登录 B站：{uname}" if ok else "Cookie 已失效，按未登录状态抓取（频道展开会很慢）")
            except Exception as e:
                self.log(f"检测登录状态失败：{e}")
        else:
            self.log("未登录：频道展开会频繁被 B站限流，速度很慢")
        pages = max(1, min(50, int(o.get("pages", 2))))
        days = int(o.get("days", 0))
        begin = time.time() - days * 86400 if days > 0 else None
        found = collections.Counter()  # 本次任务中每个 UP 主被搜到的次数
        names = {}
        try:
            self.log(f"开始：{len(kmap)} 个关键词，每个最多 {pages} 页（每页 20 条）")
            for i, (kw, paths) in enumerate(kmap.items(), 1):
                if self.stop_flag.is_set():
                    break
                got = 0
                for page in range(1, pages + 1):
                    if self.stop_flag.is_set():
                        break
                    st["progress"] = f"关键词 {i}/{len(kmap)}「{kw}」第 {page} 页"
                    try:
                        vids, num_pages = client.search_videos(
                            kw, page, order=o.get("order", "totalrank"),
                            duration=int(o.get("duration", 0)), pubtime_begin=begin)
                    except BiliError as e:
                        self.log(f"「{kw}」第 {page} 页失败：{e}")
                        break
                    if not vids:
                        break
                    self.store.save_videos(vids)
                    for p in paths:
                        self.store.save_hits([v["bvid"] for v in vids], p, kw)
                    for v in vids:
                        if v["mid"]:
                            found[v["mid"]] += 1
                            names[v["mid"]] = v["author"]
                    got += len(vids)
                    if page >= num_pages:
                        break
                self.log(f"「{kw}」→ {got} 个视频（类别：{'；'.join(paths)}）")

            for mid, name in names.items():
                self.store.ensure_channel(mid, name)
            self.log(f"搜索阶段完成，共涉及 {len(found)} 个 UP 主")

            if o.get("expand_channels", True) and not self.stop_flag.is_set():
                self._expand(client, found, names, o)
        except Exception as e:  # 防止线程静默崩溃
            self.log(f"任务出错：{e!r}")
        finally:
            st.update(phase="导出", progress="")
            try:
                st["last_export"] = self.exporter()
                self.log(f"已导出：{', '.join(st['last_export'])}")
            except Exception as e:
                self.log(f"导出失败：{e!r}")
            st.update(running=False, phase="已停止" if self.stop_flag.is_set() else "完成")
            self.log("任务结束")

    def _expand(self, client, found, names, o):
        st = self.state
        st["phase"] = "展开频道"
        max_ch = int(o.get("max_channels", 0))
        max_v = int(o.get("max_videos", 0))
        skip_done = o.get("skip_done", True)
        mids = [m for m, _ in found.most_common()]  # 被搜到次数多的频道优先
        if skip_done:
            before = len(mids)
            mids = [m for m in mids if self.store.channel_status(m) != "已完成"]
            if before - len(mids):
                self.log(f"跳过 {before - len(mids)} 个之前已展开过的频道")
        if max_ch > 0:
            mids = mids[:max_ch]
        self.log(f"开始展开 {len(mids)} 个频道" + (f"，每个最多取 {max_v} 个视频" if max_v else "，取全部视频"))

        for i, mid in enumerate(mids, 1):
            if self.stop_flag.is_set():
                break
            name = names.get(mid, str(mid))
            st["progress"] = f"频道 {i}/{len(mids)}「{name}」"
            fans = client.follower_count(mid)
            fetched, total, page, err = 0, 0, 1, None
            while not self.stop_flag.is_set():
                try:
                    vids, total = client.channel_videos(mid, page)
                except BiliError as e:
                    err = e
                    break
                if max_v:
                    vids = vids[:max_v - fetched]
                if not vids:
                    break
                self.store.save_videos(vids)
                fetched += len(vids)
                st["progress"] = f"频道 {i}/{len(mids)}「{name}」{fetched}/{total}"
                if (max_v and fetched >= max_v) or page >= math.ceil(total / 50):
                    break
                page += 1
            if self.stop_flag.is_set():
                status = "未完成"
            elif err:
                status = "失败"
                self.log(f"[{i}/{len(mids)}] {name}：失败（{err}），已取 {fetched} 个，下次运行会重抓")
            else:
                status = "已完成"
                self.log(f"[{i}/{len(mids)}] {name}：粉丝 {fans if fans is not None else '?'}，"
                         f"视频 {total} 个，已取 {fetched} 个")
            self.store.update_channel(mid, fans=fans, total_videos=total,
                                      fetched_videos=fetched, status=status)
