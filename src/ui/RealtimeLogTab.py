# -*- coding: utf-8 -*-
"""ok-kes 主窗口内的实时决策日志标签页。

不打开浏览器，不起 HTTP 服务；后台线程 tail 日志，UI 线程用 QTimer 定时读取
聚合结果并用 QTextBrowser 渲染四段式决策卡片。
"""
from html import escape

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
)
from qfluentwidgets import BodyLabel, FluentIcon

from ok.gui.widget.CustomTab import CustomTab
from src.ui.realtime_log_core import RealtimeLogWorker


class RealtimeLogTab(CustomTab):
    """在主窗口里展示实时决策卡片。"""

    CATEGORIES = [
        "全部", "商店", "冥想", "移除", "闪光", "装备", "卡牌",
        "战斗", "路线", "事件", "存档", "结算", "其他",
    ]

    def __init__(self):
        super().__init__()
        self.logger.info("RealtimeLogTab init")
        self.icon = FluentIcon.STOP_WATCH

        self.worker = RealtimeLogWorker()
        self.cards = []
        self.visible_cards = []
        self.last_card_id = 0
        self.last_total = 0
        self.stopped = False

        self.title_label = BodyLabel("实时决策日志")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.add_widget(self.title_label)

        self.status_label = BodyLabel("正在载入历史日志...")
        self.status_label.setStyleSheet("color: #6b7280;")
        self.add_widget(self.status_label)

        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(0, 8, 0, 4)

        self.category_combo = QComboBox()
        self.category_combo.addItems(self.CATEGORIES)
        self.category_combo.setFixedWidth(100)
        filter_layout.addWidget(self.category_combo)

        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("关键词过滤，例如：信用点 / 冥想 / 移除")
        filter_layout.addWidget(self.keyword_edit, 1)

        self.pause_check = QCheckBox("暂停滚动")
        filter_layout.addWidget(self.pause_check)
        self.addLayout(filter_layout)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 8)

        self.reload_button = QPushButton("重新载入日志")
        self.reload_button.clicked.connect(self.reload_log)
        button_layout.addWidget(self.reload_button)

        self.clear_button = QPushButton("清空显示")
        self.clear_button.clicked.connect(self.clear_display)
        button_layout.addWidget(self.clear_button)

        button_layout.addStretch(1)
        self.addLayout(button_layout)

        self.card_browser = QTextBrowser()
        self.card_browser.setOpenExternalLinks(False)
        self.card_browser.setOpenLinks(False)
        self.card_browser.setContextMenuPolicy(Qt.CustomContextMenu)
        self.card_browser.customContextMenuRequested.connect(self.on_card_context_menu)
        self.card_browser.setMinimumHeight(520)
        self.card_browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.card_browser.setStyleSheet(
            "QTextBrowser { background: #f3f5f8; border: 1px solid #e0e4ea; border-radius: 6px; }"
        )
        self.add_widget(self.card_browser, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self.refresh_cards)
        self.timer.start()

        self.worker.start()

        from PySide6.QtWidgets import QApplication
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.stop_worker)

        self.render_cards()

    @property
    def name(self):
        return "实时决策日志"

    def stop_worker(self):
        if self.stopped:
            return
        self.stopped = True
        self.timer.stop()
        self.worker.stop()

    def closeEvent(self, event):
        self.stop_worker()
        super().closeEvent(event)

    def reload_log(self):
        self.cards = []
        self.last_card_id = 0
        self.last_total = 0
        self.worker.request_reload()
        self.status_label.setText("正在重新载入日志...")
        self.render_cards()

    def clear_display(self):
        self.cards = []
        self.render_cards()

    def refresh_cards(self):
        all_cards, seq, error, log_exists, running = self.worker.snapshot()

        if error:
            self.status_label.setText("日志读取失败：" + error)
            self.status_label.setStyleSheet("color: #c0392b;")
            return

        # 聚合器重载或日志轮转后 id 会回退，此时从当前快照重新开始显示
        if seq < self.last_total:
            self.cards = []
            self.last_card_id = 0

        new_cards = [card for card in all_cards if card.get("id", 0) > self.last_card_id]
        if new_cards:
            self.cards.extend(new_cards)
            self.last_card_id = new_cards[-1].get("id", self.last_card_id)
        self.cards = self.cards[-400:]

        self.last_total = seq
        latest_time = all_cards[-1].get("time", "") if all_cards else ""
        state_text = "运行中" if running else "已停止"
        log_text = "日志存在" if log_exists else "未找到日志"
        self.status_label.setText(
            "卡片 %d　最新 %s　%s　%s" % (len(self.cards), latest_time, state_text, log_text)
        )
        self.status_label.setStyleSheet("color: #6b7280;")

        self.render_cards()

    def _filtered_cards(self):
        category = self.category_combo.currentText()
        keyword = self.keyword_edit.text().strip().lower()
        result = []
        for card in self.cards:
            if category != "全部" and category not in card.get("cat", []):
                continue
            if keyword:
                searchable = " ".join(
                    [card.get("action", "")]
                    + card.get("detect", [])
                    + card.get("state", [])
                    + card.get("judge", [])
                    + card.get("cat", [])
                ).lower()
                if keyword not in searchable:
                    continue
            result.append(card)
        return result

    def render_cards(self):
        cards = self._filtered_cards()
        if not cards:
            self.card_browser.setHtml(
                '<div style="color:#6b7280;font-size:13px;padding:12px;">'
                "暂无决策卡片。启动游戏脚本并等待日志产生。"
                "</div>"
            )
            return

        parts = [
            '<div style="font-family:Microsoft YaHei, Segoe UI, sans-serif; '
            'font-size:12px; color:#1f2937;">'
        ]
        self.visible_cards = cards[-120:]
        for card in self.visible_cards:
            card_id = int(card.get("id", 0) or 0)
            # 整张卡片包一层锚点，右键时用光标所在的锚点定位到具体卡片，
            # 避免日志滚动刷新后按屏幕坐标选到别人的内容。
            parts.append(
                '<a href="card:%d" style="text-decoration:none;color:inherit;">' % card_id
            )
            tags = " ".join(
                '<span style="display:inline-block;background:#e8f0fe;color:#1d4ed8;'
                'border-radius:8px;padding:1px 6px;margin-right:4px;">%s</span>'
                % escape(tag)
                for tag in card.get("cat", [])
            )
            parts.append(
                '<div style="background:#ffffff;border:1px solid #e4e9f0;'
                'border-left:4px solid #2f80ed;border-radius:6px;'
                'padding:8px 10px;margin:0 0 8px 0;">'
            )
            parts.append(
                '<div style="font-size:11px;color:#6b7280;margin-bottom:6px;">'
                "%s　%s</div>" % (escape(str(card.get("time", ""))), tags)
            )
            parts.append(self._card_row("识别", card.get("detect", []), "#0ea5e9"))
            parts.append(self._card_row("状态", card.get("state", []), "#8b5cf6"))
            parts.append(self._card_row("判断", card.get("judge", []), "#f59e0b"))
            parts.append(self._card_row("执行", [card.get("action", "")], "#22c55e"))
            parts.append("</div></a>")
        parts.append("</div>")

        self.card_browser.setHtml("".join(parts))
        if not self.pause_check.isChecked():
            cursor = self.card_browser.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.card_browser.setTextCursor(cursor)

    @staticmethod
    def _card_text(card):
        """把一张卡片转成便于粘贴的纯文本。"""
        head = str(card.get("time", ""))
        tags = card.get("cat", [])
        if tags:
            head += "  [" + " / ".join(str(tag) for tag in tags) + "]"
        lines = [head]
        for title, key in (
            ("识别", "detect"),
            ("状态", "state"),
            ("判断", "judge"),
            ("执行", "action"),
        ):
            values = card.get(key, [])
            if isinstance(values, str):
                values = [values]
            text = " / ".join(str(value) for value in values if value) if values else "无"
            lines.append("%s：%s" % (title, text))
        return "\n".join(lines)

    def _copy_text(self, text):
        QApplication.clipboard().setText(text)
        self.status_label.setText("已复制 %d 字到剪贴板" % len(text))
        self.status_label.setStyleSheet("color: #15803d;")

    def _card_at_click(self, pos):
        """按右键位置找到对应卡片；找不到锚点时返回 (None, 光标)。"""
        cursor = self.card_browser.cursorForPosition(pos)
        href = cursor.charFormat().anchorHref()
        # 嵌套 span 可能覆盖字符格式，向前回看一小段找卡片锚点
        if not href:
            probe = QTextCursor(self.card_browser.document())
            for offset in range(1, 120):
                index = cursor.position() - offset
                if index <= 0:
                    break
                probe.setPosition(index)
                found = probe.charFormat().anchorHref()
                if found:
                    href = found
                    break
        if not href or not href.startswith("card:"):
            return None, cursor
        try:
            card_id = int(href.split(":", 1)[1])
        except ValueError:
            return None, cursor
        card = next(
            (item for item in self.visible_cards if int(item.get("id", 0) or 0) == card_id),
            None,
        )
        return card, cursor

    def _select_card_text(self, cursor, href):
        """选中锚点覆盖的整张卡片，便于用户继续手动复制局部内容。"""
        document = self.card_browser.document()
        probe = QTextCursor(document)
        start = cursor.position()
        end = start
        while start > 0:
            probe.setPosition(start - 1)
            probe.setPosition(start, QTextCursor.KeepAnchor)
            if probe.charFormat().anchorHref() != href:
                break
            start -= 1
        while end < document.characterCount() - 1:
            probe.setPosition(end)
            probe.setPosition(end + 1, QTextCursor.KeepAnchor)
            if probe.charFormat().anchorHref() != href:
                break
            end += 1
        if end > start:
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            self.card_browser.setTextCursor(cursor)

    @staticmethod
    def _as_point(pos):
        return pos if isinstance(pos, QPoint) else QPoint(int(pos[0]), int(pos[1]))

    def on_card_context_menu(self, pos):
        """右键某条日志：整卡选中，并可直接复制这条或全部可见日志。"""
        pos = self._as_point(pos)
        card, cursor = self._card_at_click(pos)
        menu = QMenu(self.card_browser)
        if card is not None:
            href = "card:%s" % card.get("id", 0)
            self._select_card_text(cursor, href)
            copy_one = menu.addAction("复制这条日志")
            copy_one.triggered.connect(lambda: self._copy_text(self._card_text(card)))
            menu.addSeparator()
        copy_all = menu.addAction("复制当前全部日志")
        copy_all.triggered.connect(
            lambda: self._copy_text(
                "\n\n".join(self._card_text(item) for item in self.visible_cards)
            )
        )
        menu.exec(self.card_browser.mapToGlobal(pos))

    @staticmethod
    def _card_row(title, values, color):
        if not values:
            content = '<span style="color:#9ca3af;">无</span>'
        else:
            content = "<br>".join(escape(str(value)) for value in values if value)
            if not content:
                content = '<span style="color:#9ca3af;">无</span>'
        return (
            '<table style="width:100%;border-collapse:collapse;margin:2px 0;">'
            "<tr>"
            f'<td style="width:42px;vertical-align:top;color:{color};font-weight:600;">{escape(title)}</td>'
            f'<td style="vertical-align:top;line-height:1.45;">{content}</td>'
            "</tr></table>"
        )
