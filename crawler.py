"""抓取任务：① 按关键词搜索视频  ② 展开搜到的 UP 主（频道），列出其全部视频。在后台线程运行。

可以限时采集（每次采几分钟可调，默认 10 分钟；之后休息一段时间才能再采，最短 3 分钟），进度随时记在断点文件里，
停下后可以从断掉的地方继续。"""
import collections
import json
import math
import os
import threading
import time

from bili import BiliClient, BiliError

DEFAULT_RUN_MIN = 10  # 限时采集每次默认 10 分钟
MAX_CHANNEL_VIDEOS = 5000  # 视频太多的频道只取最新的 5000 个，取到就算抓全
MIN_REST_MIN = 3   # 限时采集结束后至少休息 3 分钟
MINUTE = 60        # 一分钟的秒数（测试时可以调小）


def hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


class Job:
    def __init__(self, store, categories, reviewer=None, state_path=None, cookie_fn=lambda: ""):
        self.store = store
        self.categories = categories
        self.reviewer = reviewer
        self.state_path = state_path
        self.cookie_fn = cookie_fn
        self.thread = None
        self.stop_flag = threading.Event()
        self.logs = collections.deque(maxlen=800)
        self.state = {"running": False, "phase": "空闲", "progress": ""}
        self.cp = self._load_cp()
        self.time_up = False
        self.user_stopped = False
        self.auto_timer = None
        if self.cp.get("auto_at") and self.resumable:  # app 重启后，恢复之前安排好的自动继续
            self._arm_auto(self.cp["auto_at"])

    def log(self, msg):
        self.logs.append(f"{time.strftime('%H:%M:%S')}  {msg}")

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    # ---------- 断点 ----------
    def _load_cp(self):
        if self.state_path and os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        return {}

    def _save_cp(self):
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cp, f, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    @property
    def resumable(self):
        return bool(self.cp.get("opts")) and not self.cp.get("finished")

    def checkpoint_info(self):
        cp = self.cp
        desc = ""
        if self.resumable:
            n_kw = len(self.categories.keyword_map(cp["opts"].get("node_ids", [])))
            if cp.get("phase") == "展开":
                ch = cp.get("channel")
                desc = "关键词已全部搜完，正在展开频道" + (f"（停在「{ch['name']}」第 {ch['page']} 页）" if ch else "")
            else:
                s = cp.get("search")
                desc = f"搜索中：已搜完 {len(cp.get('done_keywords', []))}/{n_kw} 个关键词" + \
                       (f"，停在「{s['kw']}」第 {s['page']} 页" if s else "")
        return {"resumable": self.resumable, "resume_desc": desc,
                "next_allowed": cp.get("next_allowed", 0),
                "auto_at": cp.get("auto_at") if self.auto_timer else None,
                "started_at": cp.get("started_at")}

    # ---------- 开始 / 继续 / 停止 ----------
    def start(self, opts, resume=False):
        if self.running:
            raise RuntimeError("已有任务在运行")
        wait = self.cp.get("next_allowed", 0) - time.time()
        if wait > 0:
            raise RuntimeError(f"限时采集后正在休息，{hm(self.cp['next_allowed'])} 以后才能再次采集"
                               f"（还要等 {math.ceil(wait / 60)} 分钟）")
        limit = {k: opts[k] for k in ("time_limit_on", "time_limit", "rest_min", "auto_continue") if k in opts}
        if resume:
            if not self.resumable:
                raise RuntimeError("没有可以继续的采集")
            self.cp["opts"].update(limit)
            o = self.cp["opts"]
        else:
            o = {k: v for k, v in opts.items() if k != "cookie"}
            self.cp = {"opts": o, "phase": "搜索", "done_keywords": [], "search": None, "channel": None,
                       "finished": False, "started_at": time.time()}
        kmap = self.categories.keyword_map(o.get("node_ids", []))
        if not kmap:
            raise ValueError("请至少勾选一个类别" if not resume else "上次勾选的类别已经不在类别树里了")
        self._cancel_auto()
        self.cp.pop("next_allowed", None)
        self._save_cp()
        self.stop_flag.clear()
        self.time_up = self.user_stopped = False
        if resume:
            self.log("———— 继续上次的采集 ————")
        else:
            self.logs.clear()
        self.thread = threading.Thread(target=self._run, args=(kmap, o, resume), daemon=True)
        self.thread.start()

    def stop(self):
        if not self.running:
            if self._cancel_auto():
                self.log("已取消自动继续。进度还在，可以随时点「继续上次采集」")
                self._save_cp()
            return
        self.user_stopped = True
        self.stop_flag.set()
        self.log("收到停止指令，当前请求完成后停止…")

    def forget(self):
        """丢掉断点（休息时间照样保留）"""
        self._cancel_auto()
        self.cp = {k: v for k, v in self.cp.items() if k == "next_allowed"}
        self._save_cp()

    def _time_is_up(self, minutes):
        self.time_up = True
        self.log(f"⏰ 限时 {minutes} 分钟到了，正在停止…")
        self.stop_flag.set()

    def _arm_auto(self, at):
        self._cancel_auto()
        self.cp["auto_at"] = at
        self.auto_timer = threading.Timer(max(0, at - time.time()), self._auto_resume)
        self.auto_timer.daemon = True
        self.auto_timer.start()

    def _cancel_auto(self):
        had = self.auto_timer is not None
        if self.auto_timer:
            self.auto_timer.cancel()
        self.auto_timer = None
        self.cp.pop("auto_at", None)
        return had

    def _auto_resume(self):
        self.auto_timer = None
        self.cp.pop("auto_at", None)
        try:
            self.start({}, resume=True)
        except Exception as e:
            self.log(f"自动继续失败：{e}")

    # ------------------------------------------------------------
    def _run(self, kmap, o, resume):
        st = self.state
        cp = self.cp
        st.update(running=True, phase="搜索", progress="")
        limit_min = max(1, int(o.get("time_limit") or DEFAULT_RUN_MIN)) if o.get("time_limit_on") else 0
        rest_min = max(MIN_REST_MIN, int(o.get("rest_min") or MIN_REST_MIN))
        timer = None
        if limit_min:
            timer = threading.Timer(limit_min * MINUTE, self._time_is_up, args=(limit_min,))
            timer.daemon = True
            timer.start()
            self.log(f"限时采集：这次最多 {limit_min} 分钟，到时自动停止并记下进度；之后休息 {rest_min} 分钟才能再采")
        cookie = self.cookie_fn()
        client = BiliClient(interval=float(o.get("interval", 3)), log=self.log,
                            stop_event=self.stop_flag, cookie=cookie)
        if cookie:
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
            done_kw = set(cp["done_keywords"])
            if cp["phase"] == "展开":
                self.log("关键词上次已经全部搜完，直接继续展开频道")
            else:
                if done_kw or cp.get("search"):
                    s = cp.get("search")
                    self.log(f"继续搜索：已搜完 {len(done_kw)}/{len(kmap)} 个关键词" +
                             (f"，从「{s['kw']}」第 {s['page']} 页接着搜" if s else ""))
                else:
                    self.log(f"开始：{len(kmap)} 个关键词，每个最多 {pages} 页（每页 20 条）")
                for i, (kw, paths) in enumerate(kmap.items(), 1):
                    if self.stop_flag.is_set():
                        break
                    if kw in done_kw:
                        continue
                    s = cp.get("search")
                    first = s["page"] if s and s["kw"] == kw else 1
                    got, page = 0, first
                    for page in range(first, pages + 1):
                        if self.stop_flag.is_set():
                            break
                        st["progress"] = f"关键词 {i}/{len(kmap)}「{kw}」第 {page} 页"
                        try:
                            vids, num_pages = client.search_videos(
                                kw, page, order=o.get("order", "totalrank"),
                                duration=int(o.get("duration", 0)), pubtime_begin=begin)
                        except BiliError as e:
                            if not self.stop_flag.is_set():
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
                                self.store.ensure_channel(v["mid"], v["author"])
                        got += len(vids)
                        cp["search"] = {"kw": kw, "page": page + 1}
                        self._save_cp()
                        if page >= num_pages:
                            break
                    if self.stop_flag.is_set():
                        if got:
                            self.log(f"「{kw}」→ 已取 {got} 个视频，停在第 {cp['search']['page']} 页")
                        break
                    cp["done_keywords"].append(kw)
                    cp["search"] = None
                    self._save_cp()
                    self.log(f"[{len(cp['done_keywords'])}/{len(kmap)}]「{kw}」→ {got} 个视频（类别：{'；'.join(paths)}）")

                if not self.stop_flag.is_set():
                    cp["phase"] = "展开"
                    self._save_cp()
                    self.log(f"搜索阶段完成" + (f"，这次搜到 {len(found)} 个 UP 主" if found else ""))

            if not self.stop_flag.is_set():
                if o.get("expand_channels", True):
                    self._expand(client, found, names, sorted({p for ps in kmap.values() for p in ps}), o)
                if not self.stop_flag.is_set():
                    cp["finished"] = True
                    self._save_cp()
        except Exception as e:  # 防止线程静默崩溃
            self.log(f"任务出错：{e!r}")
        finally:
            if timer:
                timer.cancel()
            if self.reviewer:
                try:
                    s = self.reviewer.run()
                    self.log(f"自动筛选（只筛已抓全的 {s['已抓全']} 个频道）：入选 {s['通过']}，筛掉 {s['淘汰']}，"
                             f"待定 {s['待定']}（手动改判过的 {s['手动']} 个保持不变）；"
                             f"没抓全的 {s['未抓全']} 个不参加筛选，下次采集会继续展开")
                except Exception as e:
                    self.log(f"自动筛选失败：{e!r}")
            st["progress"] = ""
            if limit_min:
                cp["next_allowed"] = time.time() + rest_min * MINUTE
            if cp.get("finished"):
                self.log("这次采集全部完成")
            else:
                self.log("进度已记下，可以点「继续上次采集」从断掉的地方接着抓")
                if limit_min and o.get("auto_continue") and not self.user_stopped:
                    self._arm_auto(cp["next_allowed"])
                    self.log(f"休息 {rest_min} 分钟，{hm(cp['next_allowed'])} 自动继续")
            if limit_min and not self.auto_timer:
                self.log(f"休息 {rest_min} 分钟，{hm(cp['next_allowed'])} 以后才能再次采集")
            self._save_cp()
            st.update(running=False, phase="完成" if cp.get("finished") else
                      ("限时到，休息中" if self.time_up else "已停止"))
            self.log("任务结束")

    def _channel_counts(self):
        r = self.store.q("SELECT COUNT(*) n, SUM(status = '已完成') done FROM channels")[0]
        return r["n"], r["done"] or 0

    def _pending_in(self, paths):
        """以前搜到、属于这次所选类别、但还没抓全的频道：{mid: 被搜到次数}"""
        conds = " OR ".join(["h.category = ? OR h.category LIKE ?"] * len(paths))
        args = [x for p in paths for x in (p, p + " > %")]
        rows = self.store.q(f"""SELECT v.mid, c.name, COUNT(DISTINCT h.bvid) n
                                FROM hits h JOIN videos v ON v.bvid = h.bvid JOIN channels c ON c.mid = v.mid
                                WHERE ({conds}) AND c.status IS NOT '已完成' GROUP BY v.mid""", args)
        return {r["mid"]: (r["n"], r["name"]) for r in rows}

    def _expand(self, client, found, names, paths, o):
        st = self.state
        cp = self.cp
        st["phase"] = "展开频道"
        max_ch = int(o.get("max_channels", 0))
        max_v = min(int(o.get("max_videos") or MAX_CHANNEL_VIDEOS), MAX_CHANNEL_VIDEOS)
        skip_done = o.get("skip_done", True)

        # 本次搜到的频道 + 以前搜到但还没抓全的频道，被搜到次数多的优先
        pending = self._pending_in(paths)
        older = [m for m in pending if m not in found]
        for m in older:
            names.setdefault(m, pending[m][1])
        counts = collections.Counter(found)
        for m, (n, _) in pending.items():
            counts[m] = max(counts[m], n)
        mids = [m for m, _ in counts.most_common()]
        if found and older:
            self.log(f"另有 {len(older)} 个以前搜到、还没抓全的频道，这次一起展开")
        if skip_done:
            before = len(mids)
            mids = [m for m in mids if self.store.channel_status(m) != "已完成"]
            if before - len(mids):
                self.log(f"跳过 {before - len(mids)} 个之前已抓全的频道")
        if self.reviewer:
            # 有筛选结论（入选、筛掉，或手动改判过）的频道一律不再抓取
            recorded = self.reviewer.recorded()
            skipped = collections.Counter(recorded[m] for m in mids if m in recorded)
            if skipped:
                mids = [m for m in mids if m not in recorded]
                self.log(f"跳过 {sum(skipped.values())} 个已有筛选记录的频道（入选 {skipped['通过']}，"
                         f"筛掉 {skipped['淘汰']}），不再抓取")
        # 上次抓到一半的频道排在最前面，从停下的那一页接着抓
        ch_cp = cp.get("channel")
        if ch_cp and ch_cp["mid"] in mids:
            mids.remove(ch_cp["mid"])
            mids.insert(0, ch_cp["mid"])
        if max_ch > 0 and len(mids) > max_ch:
            self.log(f"按设置，这次只展开前 {max_ch} 个，其余 {len(mids) - max_ch} 个留到下次")
            mids = mids[:max_ch]
        total_ch, done_ch = self._channel_counts()
        self.log(f"频道情况：共 {total_ch} 个，已抓全 {done_ch} 个，没抓全 {total_ch - done_ch} 个")
        self.log(f"开始展开 {len(mids)} 个频道，每个频道视频多于 {max_v} 个的只取最新的 {max_v} 个（取到就算抓全）")

        result = collections.Counter()
        for i, mid in enumerate(mids, 1):
            if self.stop_flag.is_set():
                break
            name = names.get(mid, str(mid))
            tag = f"[{i}/{len(mids)}]「{name}」"
            st["progress"] = f"频道 {i}/{len(mids)}「{name}」"
            fetched, total, page, err = 0, 0, 1, None
            if ch_cp and ch_cp["mid"] == mid:
                fetched, page = ch_cp["fetched"], ch_cp["page"]
                self.log(f"{tag} 接着上次抓，从第 {page} 页开始（已取 {fetched} 个）…")
            else:
                self.log(f"{tag} 正在抓…")
            fans = client.follower_count(mid)
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
                cp["channel"] = {"mid": mid, "name": name, "page": page, "fetched": fetched}
                self._save_cp()
                self.log(f"{tag} 已取 {fetched}/{total}…")
            if self.stop_flag.is_set():
                status = "未完成"
                self.log(f"{tag} 停在第 {page} 页，已取 {fetched}/{total}，下次从这里接着抓")
            elif err:
                status = "失败"
                self.log(f"{tag} 失败（{err}），已取 {fetched} 个，下次会重抓")
            else:
                status = "已完成"
                if fetched < total and fetched >= max_v:
                    self.log(f"{tag} 共 {total} 个视频，按上限只取了最新的 {fetched} 个，算抓全")
            if status != "未完成":
                cp["channel"] = None
                self._save_cp()
            self.store.update_channel(mid, fans=fans, total_videos=total,
                                      fetched_videos=fetched, status=status)
            result[status] += 1
            if status == "已完成":
                total_ch, done_ch = self._channel_counts()
                self.log(f"{tag} ✓ 抓全：粉丝 {fans if fans is not None else '?'}，视频 {fetched} 个"
                         f"｜本次已抓全 {result['已完成']}，还剩 {len(mids) - i} 个｜累计抓全 {done_ch}/{total_ch}")

        total_ch, done_ch = self._channel_counts()
        bad = {k: v for k, v in result.items() if k != "已完成"}
        self.log(f"展开结束：本次抓全 {result['已完成']} 个" +
                 "".join(f"，{k} {v} 个" for k, v in bad.items()) +
                 (f"，还有 {len(mids) - sum(result.values())} 个没轮到" if sum(result.values()) < len(mids) else "") +
                 f"。全部 {total_ch} 个频道中已抓全 {done_ch} 个，没抓全 {total_ch - done_ch} 个")
