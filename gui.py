import socket
from collections import deque

import psutil
from scapy.all import get_if_list

from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
    QPushButton,
    QHBoxLayout,
    QApplication,
    QDialog,
    QFormLayout,
    QComboBox,
    QLineEdit,
    QDialogButtonBox,
    QGroupBox,
    QStackedWidget,
    QCheckBox,
    QTextEdit,
)

from PyQt5.QtCore import (
    Qt,
    QObject,
    pyqtSignal,
    pyqtSlot,
    QTimer,
    QMutex,
    QMutexLocker,
    QRect,
    QSize,
)

from TranslatorManager import TranslatorsMode
from HailoAPI import ping_server, list_models
from prompt_templates import (
    DEFAULT_AI_PROMPT_TEMPLATE,
    DEFAULT_OLLAMA_MODEL,
    PROMPT_PLACEHOLDER_GUIDE,
)

# Overlay chat UI. Resize from edges; auto-scroll on new messages.

_RESIZE_MARGIN = 8
_MIN_WIDTH = 220
_MIN_HEIGHT = 120
_COLLAPSED_HEIGHT = 46
_SCALE_STEP = 40

_TOOLBAR_BTN_STYLE = """
    QPushButton {
        background: transparent;
        color: rgba(255,255,255,160);
        border: none;
        font-size: 14px;
    }
    QPushButton:hover {
        color: rgba(255,255,255,230);
    }
"""

_CLOSE_BTN_STYLE = """
    QPushButton {
        background: transparent;
        color: rgba(255,255,255,160);
        border: none;
        font-size: 16px;
    }
    QPushButton:hover {
        color: rgba(255,80,80,220);
    }
"""

_OUTLINE_STYLE = """
    QFrame#overlayFrame {
        background: rgba(18, 18, 22, 185);
        border: 2px solid rgba(120, 180, 255, 210);
        border-radius: 10px;
    }
"""


def list_network_interfaces():
    interfaces = []
    for iface in get_if_list():
        ip_addr = "No IPv4"
        try:
            for addr in psutil.net_if_addrs().get(iface, []):
                if addr.family == socket.AF_INET:
                    ip_addr = addr.address
                    break
        except Exception:
            pass
        interfaces.append((iface, ip_addr))
    return interfaces


class MessageBus(QObject):
    new_message = pyqtSignal(str)
    clear_messages = pyqtSignal()


class SetupDialog(QDialog):
    MODE_LABELS = {
        TranslatorsMode.GoogleTranslate.name: "Google Translate (free)",
        TranslatorsMode.LibreTranslate.name: "LibreTranslate",
        TranslatorsMode.HailoAITranslate.name: "Hailo AI server",
        TranslatorsMode.OllamaAITranslate.name: "Ollama server",
    }

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Overfield Translator Configuration")
        self.setMinimumWidth(460)
        self._config = dict(config)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Configure how chat messages are translated and which "
            "network adapter to listen on."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()

        self.iface_combo = QComboBox()
        for iface, ip in list_network_interfaces():
            self.iface_combo.addItem(f"{iface}  ({ip})", iface)
        form.addRow("Network interface", self.iface_combo)

        self.mode_combo = QComboBox()
        for mode_name in TranslatorsMode.__members__:
            self.mode_combo.addItem(self.MODE_LABELS[mode_name], mode_name)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_pages)
        form.addRow("Translation mode", self.mode_combo)

        self.source_lang = QLineEdit()
        form.addRow("Source language", self.source_lang)

        self.target_lang = QLineEdit()
        form.addRow("Target language", self.target_lang)

        layout.addLayout(form)

        self.mode_stack = QStackedWidget()

        google_page = QWidget()
        google_layout = QVBoxLayout(google_page)
        google_layout.addWidget(
            QLabel("Uses Google Translate. No extra settings required.")
        )
        google_layout.addStretch()
        self.mode_stack.addWidget(google_page)

        libre_page = QWidget()
        libre_form = QFormLayout(libre_page)
        self.libre_url = QLineEdit()
        self.libre_api_key = QLineEdit()
        self.libre_api_key.setEchoMode(QLineEdit.Password)
        libre_form.addRow("Server URL", self.libre_url)
        libre_form.addRow("API key (optional)", self.libre_api_key)
        self.mode_stack.addWidget(libre_page)

        hailo_page = self._build_ai_page(default_port="8000")
        self.hailo_ip = hailo_page.ip_field
        self.hailo_port = hailo_page.port_field
        self.hailo_model = hailo_page.model_combo
        self.hailo_status = hailo_page.status_label
        self.hailo_test_btn = hailo_page.test_btn
        self.mode_stack.addWidget(hailo_page.widget)

        ollama_page = self._build_ai_page(default_port="11434")
        self.ollama_ip = ollama_page.ip_field
        self.ollama_port = ollama_page.port_field
        self.ollama_model = ollama_page.model_combo
        self.ollama_status = ollama_page.status_label
        self.ollama_test_btn = ollama_page.test_btn
        self.mode_stack.addWidget(ollama_page.widget)

        mode_group = QGroupBox("Mode options")
        mode_group_layout = QVBoxLayout(mode_group)
        mode_group_layout.addWidget(self.mode_stack)
        layout.addWidget(mode_group)

        self.ai_prompt_group = QGroupBox("AI translation prompt template")
        ai_prompt_layout = QVBoxLayout(self.ai_prompt_group)

        guide_label = QLabel(PROMPT_PLACEHOLDER_GUIDE)
        guide_label.setWordWrap(True)
        guide_label.setStyleSheet(
            "color: #aaaaaa; font-size: 11px; padding: 4px; "
            "background: rgba(255,255,255,20); border-radius: 4px;"
        )
        ai_prompt_layout.addWidget(guide_label)

        self.ai_prompt = QTextEdit()
        self.ai_prompt.setPlaceholderText(
            "Prompt template sent to the AI translator. Use placeholders from the guide above."
        )
        self.ai_prompt.setMinimumHeight(140)
        ai_prompt_layout.addWidget(self.ai_prompt)
        layout.addWidget(self.ai_prompt_group)

        self.skip_next_time = QCheckBox("Skip this step next time")
        layout.addWidget(self.skip_next_time)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_saved_config()
        QTimer.singleShot(0, self._refresh_saved_ai_connection)

    def _apply_saved_config(self) -> None:
        cfg = self._config

        saved_iface = cfg.get("iface", "")
        if saved_iface:
            idx = self.iface_combo.findData(saved_iface)
            if idx < 0:
                self.iface_combo.insertItem(0, f"{saved_iface}  (saved)", saved_iface)
                idx = 0
            self.iface_combo.setCurrentIndex(idx)

        saved_mode = cfg.get("translatorMode", TranslatorsMode.GoogleTranslate.name)
        mode_idx = self.mode_combo.findData(saved_mode)
        if mode_idx >= 0:
            self.mode_combo.setCurrentIndex(mode_idx)

        self.source_lang.setText(cfg.get("sourceLang", "auto"))
        self.target_lang.setText(cfg.get("targetLang", "en"))

        self.libre_url.setText(cfg.get("libreTranslateURL", "https://libretranslate.com/"))
        self.libre_api_key.setText(cfg.get("apiKey", ""))

        ai_ip = cfg.get("aiIP", "127.0.0.1")
        ai_port = str(cfg.get("aiPort") or "")
        saved_model = cfg.get("model") or DEFAULT_OLLAMA_MODEL

        self.hailo_ip.setText(ai_ip)
        self.hailo_port.setText(ai_port or "8000")
        self._set_model_combo(self.hailo_model, saved_model)

        self.ollama_ip.setText(ai_ip)
        self.ollama_port.setText(ai_port or "11434")
        self._set_model_combo(self.ollama_model, saved_model)

        self.ai_prompt.setPlainText(cfg.get("modelPrompt", DEFAULT_AI_PROMPT_TEMPLATE))
        self.skip_next_time.setChecked(bool(cfg.get("skipSetupOnStartup")))

        self._update_mode_pages()

    def _set_model_combo(self, combo: QComboBox, model: str) -> None:
        model = (model or "").strip()
        if not model:
            return
        idx = combo.findText(model)
        if idx < 0:
            combo.insertItem(0, model)
            idx = 0
        combo.setCurrentIndex(idx)

    def _refresh_saved_ai_connection(self) -> None:
        mode = self._config.get("translatorMode")
        if mode == TranslatorsMode.HailoAITranslate.name:
            self._test_ai_connection(
                self.hailo_ip,
                self.hailo_port,
                self.hailo_model,
                self.hailo_status,
                self.hailo_test_btn,
            )
        elif mode == TranslatorsMode.OllamaAITranslate.name:
            self._test_ai_connection(
                self.ollama_ip,
                self.ollama_port,
                self.ollama_model,
                self.ollama_status,
                self.ollama_test_btn,
            )

    def _build_ai_page(self, default_port):
        class AiPage:
            pass

        page = AiPage()
        page.widget = QWidget()
        form = QFormLayout(page.widget)

        page.ip_field = QLineEdit()
        page.port_field = QLineEdit(str(default_port))
        page.model_combo = QComboBox()
        page.model_combo.setEditable(True)

        page.status_label = QLabel("Not tested")
        page.status_label.setWordWrap(True)
        page.status_label.setStyleSheet("color: gray;")

        page.test_btn = QPushButton("Test connection && refresh models")
        page.test_btn.clicked.connect(
            lambda: self._test_ai_connection(
                page.ip_field,
                page.port_field,
                page.model_combo,
                page.status_label,
                page.test_btn,
            )
        )

        test_row = QHBoxLayout()
        test_row.addWidget(page.test_btn)
        test_row.addWidget(page.status_label, stretch=1)

        form.addRow("Server IP", page.ip_field)
        form.addRow("Port", page.port_field)
        form.addRow("Model", page.model_combo)
        form.addRow("", test_row)

        return page

    def _test_ai_connection(self, ip_field, port_field, model_combo, status_label, test_btn):
        ip = ip_field.text().strip()
        port_text = port_field.text().strip()
        if not ip or not port_text:
            status_label.setText("Enter server IP and port first")
            status_label.setStyleSheet("color: #cc6666;")
            return

        try:
            port = int(port_text)
        except ValueError:
            status_label.setText("Port must be a number")
            status_label.setStyleSheet("color: #cc6666;")
            return

        test_btn.setEnabled(False)
        status_label.setText("Testing...")
        status_label.setStyleSheet("color: gray;")
        QApplication.processEvents()

        ok, message = ping_server(ip, port)
        if ok:
            models = list_models(ip, port)
            saved = model_combo.currentText().strip()
            model_combo.clear()
            if models:
                model_combo.addItems(models)
                if saved in models:
                    model_combo.setCurrentText(saved)
                elif saved:
                    model_combo.setCurrentText(saved)
                else:
                    model_combo.setCurrentIndex(0)
            elif saved:
                model_combo.addItem(saved)
                model_combo.setCurrentText(saved)
            status_label.setText(message)
            status_label.setStyleSheet("color: #66cc66;")
        else:
            status_label.setText(message)
            status_label.setStyleSheet("color: #cc6666;")

        test_btn.setEnabled(True)

    def _update_mode_pages(self):
        mode = self.mode_combo.currentData()
        page_map = {
            TranslatorsMode.GoogleTranslate.name: 0,
            TranslatorsMode.LibreTranslate.name: 1,
            TranslatorsMode.HailoAITranslate.name: 2,
            TranslatorsMode.OllamaAITranslate.name: 3,
        }
        self.mode_stack.setCurrentIndex(page_map.get(mode, 0))
        is_ai = mode in (
            TranslatorsMode.HailoAITranslate.name,
            TranslatorsMode.OllamaAITranslate.name,
        )
        self.ai_prompt_group.setVisible(is_ai)

    def get_config(self):
        mode = self.mode_combo.currentData()
        config = dict(self._config)
        config["iface"] = self.iface_combo.currentData()
        config["translatorMode"] = mode
        config["sourceLang"] = self.source_lang.text().strip() or "auto"
        config["targetLang"] = self.target_lang.text().strip() or "en"
        config["skipSetupOnStartup"] = self.skip_next_time.isChecked()

        if mode == TranslatorsMode.LibreTranslate.name:
            config["libreTranslateURL"] = self.libre_url.text().strip()
            config["apiKey"] = self.libre_api_key.text().strip()
        elif mode == TranslatorsMode.HailoAITranslate.name:
            config["aiIP"] = self.hailo_ip.text().strip()
            config["aiPort"] = self.hailo_port.text().strip()
            config["model"] = self.hailo_model.currentText().strip()
            config["modelPrompt"] = self.ai_prompt.toPlainText().strip()
        elif mode == TranslatorsMode.OllamaAITranslate.name:
            config["aiIP"] = self.ollama_ip.text().strip()
            config["aiPort"] = self.ollama_port.text().strip()
            config["model"] = self.ollama_model.currentText().strip()
            config["modelPrompt"] = self.ai_prompt.toPlainText().strip()

        return config


class ChatOverlay(QWidget):
    def __init__(
        self,
        parent=None,
        max_messages=100,
        on_close=None,
        window_config=None,
        on_geometry_changed=None,
    ):
        super().__init__(parent)

        self.max_messages = max_messages
        self.messages = deque(maxlen=max_messages)
        self.messages_mutex = QMutex()
        self.on_close = on_close
        self._on_geometry_changed = on_geometry_changed
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(250)
        self._geometry_save_timer.timeout.connect(self._save_geometry)
        self.drag_pos = None
        self._resizing = False
        self._resize_dir = ()
        self._press_pos = None
        self._press_geom = None
        self._collapsed = False
        self._expanded_size = QSize(400, 400)
        self._updating_geometry = False

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.resize(400, 400)
        self.setMinimumSize(_MIN_WIDTH, _MIN_HEIGHT)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.outer_frame = QFrame()
        self.outer_frame.setObjectName("overlayFrame")
        self.outer_frame.setStyleSheet(_OUTLINE_STYLE)
        self.outer_frame.setMouseTracking(True)

        self.layout = QVBoxLayout(self.outer_frame)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(6)

        self.top_bar_widget = QFrame()
        self.top_bar_widget.setStyleSheet("background: transparent; border: none;")
        self.top_bar_widget.setMouseTracking(True)
        top_bar = QHBoxLayout(self.top_bar_widget)
        top_bar.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel("Overfield Chat")
        self.title_label.setStyleSheet("color: rgba(255,255,255,180); font-size: 11px;")
        top_bar.addWidget(self.title_label)
        top_bar.addStretch()

        self.scale_down_btn = QPushButton("−")
        self.scale_down_btn.setFixedSize(22, 22)
        self.scale_down_btn.setToolTip("Smaller")
        self.scale_down_btn.clicked.connect(lambda: self._scale_window(-_SCALE_STEP))
        self.scale_down_btn.setStyleSheet(_TOOLBAR_BTN_STYLE)

        self.scale_up_btn = QPushButton("+")
        self.scale_up_btn.setFixedSize(22, 22)
        self.scale_up_btn.setToolTip("Larger")
        self.scale_up_btn.clicked.connect(lambda: self._scale_window(_SCALE_STEP))
        self.scale_up_btn.setStyleSheet(_TOOLBAR_BTN_STYLE)

        self.collapse_btn = QPushButton("▼")
        self.collapse_btn.setFixedSize(22, 22)
        self.collapse_btn.setToolTip("Collapse")
        self.collapse_btn.clicked.connect(self.toggle_collapse)
        self.collapse_btn.setStyleSheet(_TOOLBAR_BTN_STYLE)

        self.clear_btn = QPushButton("↺")
        self.clear_btn.setFixedSize(22, 22)
        self.clear_btn.setToolTip("Clear")
        self.clear_btn.clicked.connect(self.clear_messages)
        self.clear_btn.setStyleSheet(_TOOLBAR_BTN_STYLE)

        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.clicked.connect(self.closeProgram)
        self.close_btn.setStyleSheet(_CLOSE_BTN_STYLE)

        top_bar.addWidget(self.scale_down_btn)
        top_bar.addWidget(self.scale_up_btn)
        top_bar.addWidget(self.collapse_btn)
        top_bar.addWidget(self.clear_btn)
        top_bar.addWidget(self.close_btn)
        self.layout.addWidget(self.top_bar_widget)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.scroll.setMouseTracking(True)
        self.scroll.viewport().setMouseTracking(True)

        self.container = QFrame()
        self.container.setStyleSheet("background: transparent; border: none;")
        self.container.setMouseTracking(True)

        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(0, 0, 0, 0)
        self.vbox.setSpacing(6)
        self.vbox.addStretch()

        self.scroll.setWidget(self.container)
        self.layout.addWidget(self.scroll)

        root_layout.addWidget(self.outer_frame)

        self.bus = MessageBus()
        self.bus.new_message.connect(self._add_message_ui, Qt.QueuedConnection)
        self.bus.clear_messages.connect(self._clear_messages_ui, Qt.QueuedConnection)

        self._restore_geometry(window_config or {})

    def _restore_geometry(self, cfg):
        width = int(cfg.get("windowWidth") or 400)
        height = int(cfg.get("windowHeight") or 400)
        width = max(_MIN_WIDTH, width)
        height = max(_MIN_HEIGHT, height)
        self._expanded_size = QSize(width, height)

        if cfg.get("windowCollapsed"):
            self.resize(width, _COLLAPSED_HEIGHT)
            QTimer.singleShot(0, self._collapse)
        else:
            self.resize(width, height)

        x, y = cfg.get("windowX"), cfg.get("windowY")
        if x is not None and y is not None:
            self.move(int(x), int(y))
            QTimer.singleShot(0, self._clamp_to_screen)

    def _clamp_to_screen(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        frame = self.frameGeometry()
        x = max(available.left(), min(frame.x(), available.right() - frame.width()))
        y = max(available.top(), min(frame.y(), available.bottom() - frame.height()))
        if x != frame.x() or y != frame.y():
            self.move(x, y)

    def _remember_expanded_size(self):
        if self.height() > _COLLAPSED_HEIGHT:
            self._expanded_size = QSize(self.width(), self.height())

    def _schedule_save_geometry(self):
        self._geometry_save_timer.start()

    def _save_geometry(self):
        if not callable(self._on_geometry_changed):
            return

        if self._collapsed:
            width = self._expanded_size.width()
            height = self._expanded_size.height()
        else:
            self._remember_expanded_size()
            width = self._expanded_size.width()
            height = self._expanded_size.height()

        self._on_geometry_changed(
            self.x(),
            self.y(),
            width,
            height,
            self._collapsed,
        )

    def moveEvent(self, event):
        super().moveEvent(event)
        self._schedule_save_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (
            not self._updating_geometry
            and not self._collapsed
            and self.height() > _COLLAPSED_HEIGHT
        ):
            self._remember_expanded_size()
        self._schedule_save_geometry()

    def toggle_collapse(self):
        if self._collapsed:
            self._expand()
        else:
            self._collapse()

    def _collapse(self):
        if self._collapsed:
            return

        self._remember_expanded_size()

        self.scroll.hide()
        self.scale_down_btn.hide()
        self.scale_up_btn.hide()
        self.clear_btn.hide()
        self.setMinimumSize(_MIN_WIDTH, _COLLAPSED_HEIGHT)

        self._updating_geometry = True
        self.resize(self.width(), _COLLAPSED_HEIGHT)
        self._updating_geometry = False

        self._collapsed = True
        self.collapse_btn.setText("▲")
        self.collapse_btn.setToolTip("Expand")
        self._save_geometry()

    def _expand(self):
        if not self._collapsed:
            return

        target_w = max(_MIN_WIDTH, self._expanded_size.width())
        target_h = max(_MIN_HEIGHT, self._expanded_size.height())

        self.scroll.show()
        self.scale_down_btn.show()
        self.scale_up_btn.show()
        self.clear_btn.show()
        self.setMinimumSize(_MIN_WIDTH, _MIN_HEIGHT)

        self._updating_geometry = True
        self.resize(target_w, target_h)
        self._updating_geometry = False

        self._collapsed = False
        self.collapse_btn.setText("▼")
        self.collapse_btn.setToolTip("Collapse")
        self._schedule_scroll_bottom()
        self._save_geometry()

    def _scale_window(self, delta: int):
        if self._collapsed:
            return

        width = max(_MIN_WIDTH, self.width() + delta)
        height = max(_MIN_HEIGHT, self.height() + delta)
        self._updating_geometry = True
        self.resize(width, height)
        self._updating_geometry = False
        self._remember_expanded_size()

    def closeProgram(self):
        self._save_geometry()
        if callable(self.on_close):
            self.on_close()
        self.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def clear_messages(self):
        self.bus.clear_messages.emit()

    def add_message(self, text: str):
        self.bus.new_message.emit(text)

    @pyqtSlot()
    def _clear_messages_ui(self):
        with QMutexLocker(self.messages_mutex):
            self.messages.clear()

        while self._message_widget_count() > 0:
            item = self.vbox.itemAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is None:
                self.vbox.removeItem(item)
                continue
            self.vbox.removeWidget(widget)
            widget.deleteLater()

    @pyqtSlot(str)
    def _add_message_ui(self, text: str):
        with QMutexLocker(self.messages_mutex):
            self.messages.append(text)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(
            """
            color: white;
            background: rgba(30, 30, 30, 170);
            padding: 6px;
            border-radius: 6px;
            """
        )
        label.setMouseTracking(True)

        self.vbox.insertWidget(self.vbox.count() - 1, label)
        self._trim_old_widgets()
        self._schedule_scroll_bottom()

    def _schedule_scroll_bottom(self):
        QTimer.singleShot(0, self._scroll_bottom)
        QTimer.singleShot(50, self._scroll_bottom)

    def _scroll_bottom(self):
        self.container.adjustSize()

        last_widget = None
        for i in range(self.vbox.count() - 1, -1, -1):
            widget = self.vbox.itemAt(i).widget()
            if widget is not None:
                last_widget = widget
                break

        if last_widget is not None:
            self.scroll.ensureWidgetVisible(last_widget, 0, 0)

        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _trim_old_widgets(self):
        while self._message_widget_count() > self.max_messages:
            item = self.vbox.itemAt(0)
            if item is None:
                return
            widget = item.widget()
            if widget is None:
                self.vbox.removeItem(item)
                continue
            self.vbox.removeWidget(widget)
            widget.deleteLater()

    def _message_widget_count(self):
        count = 0
        for i in range(self.vbox.count()):
            if self.vbox.itemAt(i).widget():
                count += 1
        return count

    def _get_edges(self, p):
        x, y, w, h = p.x(), p.y(), self.width(), self.height()
        dirs = ()
        if x <= _RESIZE_MARGIN:
            dirs += ("left",)
        if x >= w - _RESIZE_MARGIN:
            dirs += ("right",)
        if y <= _RESIZE_MARGIN:
            dirs += ("top",)
        if y >= h - _RESIZE_MARGIN:
            dirs += ("bottom",)
        return dirs

    def _update_cursor(self, p):
        if self._collapsed:
            self.setCursor(Qt.ArrowCursor)
            return

        d = self._get_edges(p)
        if ("left" in d and "top" in d) or ("right" in d and "bottom" in d):
            self.setCursor(Qt.SizeFDiagCursor)
        elif ("right" in d and "top" in d) or ("left" in d and "bottom" in d):
            self.setCursor(Qt.SizeBDiagCursor)
        elif "left" in d or "right" in d:
            self.setCursor(Qt.SizeHorCursor)
        elif "top" in d or "bottom" in d:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return

        if self._collapsed:
            self.drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            return

        dirs = self._get_edges(e.pos())
        if dirs:
            self._resizing = True
            self._resize_dir = dirs
            self._press_pos = e.globalPos()
            self._press_geom = self.geometry()
            return

        self.drag_pos = e.globalPos() - self.frameGeometry().topLeft()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            local = self.top_bar_widget.mapFromGlobal(e.globalPos())
            if self.top_bar_widget.rect().contains(local):
                self.toggle_collapse()
                return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if self._collapsed:
            if self.drag_pos is not None and e.buttons() & Qt.LeftButton:
                self.move(e.globalPos() - self.drag_pos)
            return

        if self._resizing and self._press_pos is not None:
            dx = e.globalPos().x() - self._press_pos.x()
            dy = e.globalPos().y() - self._press_pos.y()
            g = QRect(self._press_geom)

            if "left" in self._resize_dir:
                g.setLeft(min(g.right() - self.minimumWidth() + 1, g.left() + dx))
            if "right" in self._resize_dir:
                g.setRight(max(g.left() + self.minimumWidth() - 1, g.right() + dx))
            if "top" in self._resize_dir:
                g.setTop(min(g.bottom() - self.minimumHeight() + 1, g.top() + dy))
            if "bottom" in self._resize_dir:
                g.setBottom(max(g.top() + self.minimumHeight() - 1, g.bottom() + dy))

            self.setGeometry(g)
            return

        if self.drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self.drag_pos)
            return

        self._update_cursor(e.pos())

    def mouseReleaseEvent(self, e):
        if self._resizing and not self._collapsed:
            self._remember_expanded_size()
            self._save_geometry()

        self.drag_pos = None
        self._resizing = False
        self._resize_dir = ()
        self._press_pos = None
        self._press_geom = None
        self.setCursor(Qt.ArrowCursor)
