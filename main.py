#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import pwd
import queue
import socket
import struct
import sys
import threading
import time
import traceback
from collections import defaultdict
from typing import Optional, Tuple

import psutil
import snappy
from scapy.all import IP, Raw, conf, get_if_list, sniff

import net_pb2 as OverField_pb2
from gui import ChatOverlay, SetupDialog
from msg_id import MsgId
from prompt_templates import DEFAULT_AI_PROMPT_TEMPLATE
from PyQt5.QtWidgets import QApplication, QDialog
from TranslatorManager import TranslatorManager, TranslatorsMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GAME_PORT_MIN = 11001
GAME_PORT_MAX = 11003
MAX_HEADER_LEN = 20 * 1024

id_to_name = {
    value: name
    for name, value in vars(MsgId).items()
    if not name.startswith("__") and isinstance(value, int)
}
_proto_class_cache: dict[int, type | None] = {}

flow_buffers: defaultdict[tuple, bytearray] = defaultdict(bytearray)

CONFIG_FILE = "configData.json"
_active_config_path: str | None = None


def _config_candidates() -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        if not path:
            return
        path = os.path.abspath(path)
        if path in seen:
            return
        seen.add(path)
        paths.append(path)

    add(os.environ.get("OVERFIELD_CONFIG", "").strip())
    root = os.environ.get("OVERFIELD_ROOT", "").strip()
    if root:
        add(os.path.join(root, CONFIG_FILE))
    add(os.path.join(os.getcwd(), CONFIG_FILE))
    add(os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILE))
    add(os.path.join(os.getcwd(), "dist", CONFIG_FILE))
    return paths


def _config_file() -> str:
    global _active_config_path
    if _active_config_path:
        return _active_config_path
    candidates = _config_candidates()
    return candidates[0] if candidates else CONFIG_FILE


DEFAULT_CONFIG = {
    "skipSetupOnStartup": False,
    "iface": "",
    "translatorMode": TranslatorsMode.GoogleTranslate.name,
    "targetLang": "en",
    "sourceLang": "auto",
    "libreTranslateURL": "https://libretranslate.com/",
    "apiKey": "",
    "aiIP": "127.0.0.1",
    "aiPort": "",
    "model": "qwen2:1.5b",
    "modelPrompt": DEFAULT_AI_PROMPT_TEMPLATE,
    "windowX": None,
    "windowY": None,
    "windowWidth": 400,
    "windowHeight": 400,
    "windowCollapsed": False,
}

config_data = dict(DEFAULT_CONFIG)
shutting_down = False
stop_evt = threading.Event()
message_queue: queue.Queue = queue.Queue()
trans_manager: Optional[TranslatorManager] = None
overlay: Optional[ChatOverlay] = None


def _restore_config_owner() -> None:
    if os.geteuid() != 0:
        return
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user or sudo_user == "root":
        return
    path = _config_file()
    try:
        entry = pwd.getpwnam(sudo_user)
        os.chown(path, entry.pw_uid, entry.pw_gid)
    except (KeyError, OSError) as exc:
        logger.warning("Could not set config owner to %s: %s", sudo_user, exc)


def write_config():
    path = _config_file()
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(config_data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
        _restore_config_owner()
        logger.info("Saved config to %s", os.path.abspath(path))
    except OSError as exc:
        logger.error("Failed to save %s: %s", path, exc)
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def load_config():
    global config_data, _active_config_path

    loaded: dict | None = None
    loaded_from: str | None = None

    for path in _config_candidates():
        logger.info(
            "Trying config (euid=%s, path=%s, exists=%s)",
            os.geteuid(),
            path,
            os.path.isfile(path),
        )
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to read %s: %s", path, exc)
            continue

        if not isinstance(data, dict):
            logger.error("Config file is not a JSON object: %s", path)
            continue

        loaded = data
        loaded_from = path
        break

    if loaded is None:
        _active_config_path = _config_candidates()[0]
        logger.warning(
            "No readable config found; creating defaults at %s",
            _active_config_path,
        )
        write_config()
        return

    _active_config_path = loaded_from
    config_data = dict(DEFAULT_CONFIG)
    config_data.update(loaded)
    if "skipSetupOnStartup" not in loaded and loaded.get("setupComplete"):
        config_data["skipSetupOnStartup"] = True

    logger.info(
        "Loaded config from %s (iface=%s, mode=%s, model=%s)",
        loaded_from,
        config_data.get("iface"),
        config_data.get("translatorMode"),
        config_data.get("model"),
    )


def needs_setup() -> bool:
    return not config_data.get("skipSetupOnStartup", False)


def run_setup_wizard(app: QApplication) -> bool:
    global config_data
    logger.info(
        "Opening setup dialog with iface=%s mode=%s aiIP=%s model=%s",
        config_data.get("iface"),
        config_data.get("translatorMode"),
        config_data.get("aiIP"),
        config_data.get("model"),
    )
    dialog = SetupDialog(dict(config_data))
    if dialog.exec_() != QDialog.Accepted:
        return False
    config_data.update(dialog.get_config())
    write_config()
    return True


def save_window_geometry(x, y, width, height, collapsed):
    config_data["windowX"] = x
    config_data["windowY"] = y
    config_data["windowWidth"] = width
    config_data["windowHeight"] = height
    config_data["windowCollapsed"] = collapsed
    write_config()


def request_shutdown():
    global shutting_down
    shutting_down = True
    stop_evt.set()


def list_interfaces():
    interfaces = get_if_list()
    print("\nAvailable Interfaces:\n")

    for idx, iface in enumerate(interfaces):
        ip_addr = "No IPv4"
        try:
            for addr in psutil.net_if_addrs().get(iface, []):
                if addr.family == socket.AF_INET:
                    ip_addr = addr.address
                    break
        except OSError:
            pass
        print(f"[{idx}] {iface:<20} {ip_addr}")

    return interfaces


def choose_interface():
    interfaces = list_interfaces()
    while not shutting_down:
        try:
            selection = input("\nSelect interface number: ").strip()
            idx = int(selection)
            if 0 <= idx < len(interfaces):
                chosen = interfaces[idx]
                print(f"\nUsing interface: {chosen}")
                return chosen
            print("Invalid selection.")
        except KeyboardInterrupt:
            raise
        except ValueError:
            print("Please enter a valid number.")
    return None


def normalize_flow_key(src_ip, dst_ip, sport, dport):
    return tuple(sorted([(src_ip, sport), (dst_ip, dport)]))


def get_proto_class(msg_id: int):
    if msg_id not in _proto_class_cache:
        proto_name = id_to_name.get(msg_id)
        _proto_class_cache[msg_id] = (
            getattr(OverField_pb2, proto_name, None) if proto_name else None
        )
    return _proto_class_cache[msg_id]


def process_flow_buffer(flow_key):
    buf = flow_buffers[flow_key]

    while not shutting_down:
        if len(buf) < 2:
            break

        try:
            header_len = struct.unpack(">H", buf[0:2])[0]
        except struct.error:
            del buf[:2]
            continue

        if header_len > MAX_HEADER_LEN:
            del buf[:2]
            continue

        if len(buf) < 2 + header_len:
            break

        header_data = bytes(buf[2 : 2 + header_len])
        packet_head = OverField_pb2.PacketHead()

        try:
            packet_head.ParseFromString(header_data)
        except Exception:
            del buf[:2]
            continue

        body_len = packet_head.body_len
        total_needed = 2 + header_len + body_len
        if len(buf) < total_needed:
            break

        body_data = bytes(buf[2 + header_len : total_needed])
        del buf[:total_needed]

        if packet_head.flag == 1:
            try:
                body_data = snappy.uncompress(body_data)
            except Exception:
                logger.debug("Snappy decompression failed")
                continue

        msg_id = packet_head.msg_id
        proto_cls = get_proto_class(msg_id)
        if proto_cls is None:
            continue

        try:
            message = proto_cls()
            message.ParseFromString(body_data)
            txt = getattr(message.msg, "text", "")
            name = getattr(message.msg, "name", "")
            if txt:
                print(f"{name} >> {txt}")
                message_queue.put((name, txt))
        except Exception:
            logger.debug("Failed to parse protobuf body", exc_info=True)


def pkt_callback(
    pkt,
    ip_filter: Optional[str],
    port_range: Optional[Tuple[int, int]],
    stop_event: Optional[threading.Event] = None,
):
    if stop_event is not None and stop_event.is_set():
        return False

    if not pkt.haslayer(Raw):
        return

    ip_layer = pkt.getlayer(IP)
    if ip_layer is None:
        return

    src_ip = ip_layer.src
    dst_ip = ip_layer.dst
    sport = getattr(pkt.payload, "sport", None)
    dport = getattr(pkt.payload, "dport", None)

    if ip_filter is not None and src_ip != ip_filter and dst_ip != ip_filter:
        return

    if port_range is not None:
        pmin, pmax = port_range
        if not (
            (sport is not None and pmin <= sport <= pmax)
            or (dport is not None and pmin <= dport <= pmax)
        ):
            return

    payload = bytes(pkt[Raw].load)
    if not payload:
        return

    flow_key = normalize_flow_key(src_ip, dst_ip, sport, dport)
    flow_buffers[flow_key].extend(payload)
    process_flow_buffer(flow_key)


def start_sniffer(
    iface: str,
    ip: Optional[str],
    port_range: Optional[Tuple[int, int]],
    stop_event: threading.Event,
    bpf: Optional[str] = None,
    promisc: bool = False,
):
    if ip is None:
        if port_range is not None:
            pmin, pmax = port_range
            bpf_filter = f"tcp and portrange {pmin}-{pmax}"
        else:
            bpf_filter = "tcp"
    elif port_range is not None:
        pmin, pmax = port_range
        bpf_filter = f"tcp and host {ip} and portrange {pmin}-{pmax}"
    else:
        bpf_filter = f"tcp and host {ip}"

    if bpf:
        bpf_filter = f"({bpf_filter}) and ({bpf})"

    logger.info("BPF Filter: %s", bpf_filter)
    conf.sniff_promisc = bool(promisc)

    try:
        sniff(
            iface=iface,
            filter=bpf_filter,
            prn=lambda pkt: pkt_callback(pkt, ip, port_range, stop_event),
            store=False,
            stop_filter=lambda _: stop_event.is_set(),
        )
    except PermissionError:
        logger.error("Permission denied. Run with sudo.")
        request_shutdown()
    except Exception:
        traceback.print_exc()
        logger.error("Packet sniffing failed. Ensure libpcap is installed.")
        request_shutdown()


def worker(overlay_widget: ChatOverlay, manager: TranslatorManager):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        while not shutting_down:
            try:
                name, text = message_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                translated = loop.run_until_complete(manager.translateText(text))
                if not translated:
                    translated = text
                overlay_widget.add_message(f"{name} >> {translated}")
            except Exception:
                logger.exception("Worker failed to translate message")
                overlay_widget.add_message(f"{name} >> {text}")
            finally:
                message_queue.task_done()
    finally:
        loop.run_until_complete(manager.close())
        loop.close()


def start_gui(app: QApplication):
    global overlay, trans_manager

    overlay = ChatOverlay(
        on_close=request_shutdown,
        window_config=config_data,
        on_geometry_changed=save_window_geometry,
    )
    overlay.show()

    trans_manager = TranslatorManager(config_data)
    overlay.add_message(
        f"Program started ({config_data.get('translatorMode', 'unknown')})..."
    )

    threading.Thread(
        target=worker,
        daemon=True,
        args=(overlay, trans_manager),
        name="translate-worker",
    ).start()

    app.exec_()
    request_shutdown()


def main():
    load_config()
    app = QApplication(sys.argv)

    if needs_setup() and not run_setup_wizard(app):
        print("Setup cancelled.")
        return

    iface = config_data.get("iface")
    if not iface:
        iface = choose_interface()
        if iface is None:
            return
        config_data["iface"] = iface
        write_config()

    logger.info("Listening on %s (ports %s-%s)", iface, GAME_PORT_MIN, GAME_PORT_MAX)

    threading.Thread(
        target=start_sniffer,
        args=(iface, None, (GAME_PORT_MIN, GAME_PORT_MAX), stop_evt),
        kwargs={"bpf": None, "promisc": False},
        name="sniffer",
        daemon=True,
    ).start()

    try:
        start_gui(app)
    except KeyboardInterrupt:
        logger.info("Stopping...")
        request_shutdown()

    stop_evt.set()
    print("Exited cleanly.")


if __name__ == "__main__":
    main()
