# -*- coding: utf-8 -*-
"""实时决策日志的后台聚合逻辑。

只读取 ok-script.log，不修改日志文件，也不依赖 HTTP 服务。
日志会被聚合成四段式决策卡片：识别、状态、判断、执行。
"""
import os
import re
import sys
import threading
import time

# 最多保留多少张卡片，防止长时间运行后内存无限增长
MAX_CARDS = 400

# 日志行正则：2026-11-21 09:23:28,956 INFO TaskExecutor ChaosMode:消息
LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(INFO|WARNING|ERROR)\s+(\S+?)\s+(.*)$"
)

# 只关心脚本业务日志，过滤框架噪声
BUSINESS_MODULES = ("ChaosMode", "SortieMode", "StoryMode")


def split_body(body):
    """把 body 拆成模块名和消息正文。"""
    index = body.find(":")
    if index >= 0:
        return body[:index], body[index + 1:]
    return "", body


# 完全忽略的噪声行
NOISE_RE = re.compile(
    r"^(info_set |left_click |move_relative |当前生命值:|"
    r"按钮左侧区域颜色:|read_from_json|find_one_feature|check_size|"
    r"FeatureSet|install ocr|do_init|start init|use_openvino|use_npu|"
    r"det_model_dir|rec_model_dir|openvino core)"
)

# 识别到什么页面/事件
DETECT_RE = re.compile(
    r"(检测到|识别到|进入|所处层数|页面判定|通过页面判定|"
    r"^(handle_\w+):|事件选项|三选一事件选项|候选|发现)"
)

# 读到的关键状态值
STATE_RE = re.compile(
    r"(信用点|余额|费用|血量|生命值|配置|阈值|上限|"
    r"=|\d+%|待冥想|无需冥想|已售罄|已移除|已获得|"
    r"当前\d+|剩余|相似度|特征绑定)"
)

# 判断条件结论
JUDGE_RE = re.compile(
    r"(足够|不足|满足|不满足|未命中|命中|达到|超过|未超过|"
    r"已售罄|上限|配置未开启|未开启|已用完|耗尽|"
    r"跳过|不再|兜底|随机|优先|失败|无法|未识别|未找到|"
    r"大于|小于|过高|过低|禁止|过滤)"
)

# 强决策动作：命中后切出新卡片
STRONG_ACTION_RE = re.compile(
    r"(随机选择|优先选择|改选|"
    r"点击休息|点击冥想|点击移除|点击刷新|点击购买|点击跳过|"
    r"点击[^\s]{0,8}节点|进入商店|"
    r"达到\d+张上限|不再使用信用点|不再重复点击|"
    r"跳过删除|跳过点击|跳过本次|跳过，不删|跳过刷新|不付费刷新|"
    r"自动停止任务|自动逃脱|暂停工具|重新开始|"
    r"跳过点击休息|点击该卡牌|点击目标卡牌)"
)

# 机械动作：并入当前卡片的执行段，不单独切卡
WEAK_ACTION_RE = re.compile(
    r"(页面处理「handle_\w+」触发点击事件|"
    r"点击屏幕事件|点击确认|点击确定|二次点击确认|"
    r"点击进入按钮|点击下一步|点击进入|"
    r"left_click|检测到确认操作|检测到进入按钮|检测到下一步按钮)"
)

# 完全忽略的重复机械动作
IGNORE_MECHANICAL_RE = re.compile(
    r"^(页面处理「handle_\w+」触发点击事件|点击屏幕事件，点击屏幕)$"
)

# 卡片分类规则
CATEGORY_RULES = [
    ("商店", r"商店|德朗|刷新|购买|shop"),
    ("冥想", r"冥想|meditat"),
    ("移除", r"移除|删卡|remove"),
    ("闪光", r"闪光|flash|查看原件"),
    ("装备", r"装备|equip|号位"),
    ("卡牌", r"卡牌|选牌|复制|card"),
    ("战斗", r"战斗|出牌|生命值|胜率|三选一事件|battle"),
    ("路线", r"路线|节点|小地图|层数|地图|route|进入"),
    ("事件", r"事件|命运|记忆|event"),
    ("存档", r"存档|存储数据|主战员|垃圾桶|deletecards"),
    ("结算", r"结算|奖励|探险结果|通关"),
]


def categorize(text):
    """给日志文本打分类标签。"""
    tags = []
    for name, pattern in CATEGORY_RULES:
        if re.search(pattern, text, re.I):
            tags.append(name)
    return tags[:2] or ["其他"]


class DecisionAggregator:
    """把连续日志聚合成一张张四段式决策卡片。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.buf = []
        self.weak_actions = []
        self.cards = []
        self.seq = 0
        self.last_card = None

    def _flush(self, action_time, action_msg):
        """把缓冲内容输出为一张卡片。"""
        if not self.buf and not action_msg:
            return None

        detect, state, judge = [], [], []
        for _, message in self.buf:
            if DETECT_RE.search(message):
                detect.append(message)
            if JUDGE_RE.search(message):
                judge.append(message)
            elif STATE_RE.search(message) and not DETECT_RE.search(message):
                state.append(message)

        all_text = " ".join(message for _, message in self.buf) + " " + action_msg
        if not detect and self.buf:
            detect = [self.buf[0][1]]

        actions = []
        if action_msg:
            actions.append(action_msg)
        for weak_action in self.weak_actions:
            if weak_action not in actions:
                actions.append(weak_action)

        # 动作和判断都与上一张相同则不重复出卡
        signature = (action_msg, tuple(judge[-2:]))
        if self.last_card and self.last_card.get("_sig") == signature:
            self.buf = []
            self.weak_actions = []
            return None

        self.seq += 1
        card = {
            "id": self.seq,
            "time": action_time,
            "cat": categorize(all_text),
            "detect": detect[-3:],
            "state": state[-4:],
            "judge": judge[-4:],
            "action": " → ".join(actions[:3]),
            "_sig": signature,
        }
        self.cards.append(card)
        if len(self.cards) > MAX_CARDS:
            self.cards = self.cards[-MAX_CARDS:]
        self.last_card = card
        self.buf = []
        self.weak_actions = []
        return card

    def feed(self, time_str, message):
        """喂入一条日志，返回新产生的卡片或 None。"""
        if not message or NOISE_RE.search(message):
            return None
        if IGNORE_MECHANICAL_RE.match(message):
            return None

        if STRONG_ACTION_RE.search(message):
            return self._flush(time_str, message)
        if WEAK_ACTION_RE.search(message):
            if message not in self.weak_actions:
                self.weak_actions.append(message)
            if len(self.weak_actions) > 3:
                self.weak_actions = self.weak_actions[-3:]
            return None

        self.buf.append((time_str, message))
        if len(self.buf) > 14:
            self.buf = self.buf[-14:]
        return None


def read_log_since(path, offset):
    """从 offset 开始读取新增完整行，返回 (日志行列表, 新 offset)。"""
    if not os.path.exists(path):
        return [], offset
    with open(path, "rb") as log_file:
        log_file.seek(0, os.SEEK_END)
        size = log_file.tell()
        if offset > size:
            offset = 0
        log_file.seek(offset)
        data = log_file.read()
        new_offset = log_file.tell()

    rows = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        time_str, _, thread, body = match.groups()
        module, message = split_body(body)
        if module in BUSINESS_MODULES or thread == "TaskExecutor":
            rows.append((time_str[-12:], message))
    return rows, new_offset


def get_log_path():
    """返回当前运行模式下实际使用的日志路径。"""
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.getcwd()
    return os.path.normpath(os.path.join(base_dir, "logs", "ok-script.log"))


class RealtimeLogWorker:
    """后台读取日志并维护聚合卡片。"""

    def __init__(self, log_path=None):
        self.log_path = log_path or get_log_path()
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self.lock = threading.RLock()
        self.aggregator = DecisionAggregator()
        self.offset = 0
        self.running = False
        self.error = ""
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.running = False

    def request_reload(self):
        self.reload_event.set()

    def snapshot(self):
        """给 UI 线程读取当前卡片和状态。"""
        with self.lock:
            cards = list(self.aggregator.cards)
            seq = self.aggregator.seq
            error = self.error
            running = self.running
        return cards, seq, error, os.path.exists(self.log_path), running

    def _load_history(self):
        """启动时读取日志尾部，建立上下文。"""
        if not os.path.exists(self.log_path):
            return 0
        with open(self.log_path, "rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            size = log_file.tell()
            start = max(0, size - 3 * 1024 * 1024)
            log_file.seek(start)
            data = log_file.read()

        lines = data.decode("utf-8", errors="replace").splitlines()[-4000:]
        for line in lines:
            match = LINE_RE.match(line)
            if not match:
                continue
            time_str, _, thread, body = match.groups()
            module, message = split_body(body)
            if module in BUSINESS_MODULES or thread == "TaskExecutor":
                self.aggregator.feed(time_str[-12:], message)
        return size

    def _loop(self):
        try:
            with self.lock:
                self.offset = self._load_history()
            while not self.stop_event.is_set():
                if self.reload_event.is_set():
                    self.reload_event.clear()
                    with self.lock:
                        self.aggregator.reset()
                        self.offset = self._load_history()

                rows, self.offset = read_log_since(self.log_path, self.offset)
                if rows:
                    with self.lock:
                        for time_str, message in rows:
                            self.aggregator.feed(time_str, message)
                time.sleep(0.4)
        except Exception as error:  # 不让后台线程把主程序带崩
            with self.lock:
                self.error = str(error)
                self.running = False
