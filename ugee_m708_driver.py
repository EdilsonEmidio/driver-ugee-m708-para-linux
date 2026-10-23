#!/usr/bin/env python3
"""
Driver User-Space Completo para Ugee M708 no Debian 13 (GNOME / Wayland)
Com suporte a configuração dinâmica via GUI (IPC Unix Socket + JSON Config),
Curva de Pressão Bézier (P0, P1X, P1Y, P2), Rotação (0/90/180/270),
Recorte de Área Ativa da Mesa e da Tela, Modo de Precisão, Atalhos K1-K8
e Telemetria em Tempo Real de Baixa Latência.
"""

import os
import re
import sys
import glob
import json
import math
import time
import socket
import struct
import select
import subprocess

# Garante flush imediato no journalctl do systemd
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

try:
    from evdev import UInput, AbsInfo, ecodes as e
except ImportError:
    print("ERRO: O módulo 'evdev' não está instalado.", file=sys.stderr)
    print("No Debian/Ubuntu, instale com: sudo apt install python3-evdev", file=sys.stderr)
    sys.exit(1)

VID = 0x28BD
PIDS = {0x0907, 0x0924, 0x0927}

CONFIG_DIR = os.path.expanduser("~/.config/ugee-tablet")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def get_socket_path():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if os.path.isdir(runtime_dir):
        return os.path.join(runtime_dir, "ugee-m708.sock")
    return f"/tmp/ugee-m708-{os.getuid()}.sock"


DEFAULT_CONFIG = {
    "workarea": {
        "rotation": 0,              # 0, 90, 180 (Canhoto), 270
        "keep_aspect_ratio": False,
        "precision_mode": False,
        "tablet_area": {
            "x_min": 0.0,
            "y_min": 0.0,
            "x_max": 1.0,
            "y_max": 1.0
        },
        "screen_area": {
            "x_min": 0.0,
            "y_min": 0.0,
            "x_max": 1.0,
            "y_max": 1.0
        }
    },
    "pen": {
        "pressure_enabled": True,
        "tilt_enabled": True,
        "pressure_curve": {
            "p0": 0.0,    # Zona morta inicial (0.0 .. 0.35)
            "p1x": 0.5,   # Ponto de controle Bézier X (0.05 .. 0.95)
            "p1y": 0.5,   # Ponto de controle Bézier Y (0.05 .. 0.95)
            "p2": 1.0     # Teto de saturação (0.5 .. 1.0)
        },
        "btn1_action": "BTN_STYLUS",   # Botão Inferior: Clique Direito / Stylus 1
        "btn2_action": "BTN_STYLUS2"   # Botão Superior: Clique do Meio / Stylus 2
    },
    "express_keys": {
        "enabled": True,
        "osd_notifications": True,
        "keys": [
            {"id": "K1", "label": "Pincel (B)",          "type": "shortcut",   "value": "KEY_B"},
            {"id": "K2", "label": "Borracha (E)",        "type": "shortcut",   "value": "KEY_E"},
            {"id": "K3", "label": "Conta-gotas (Alt)",   "type": "shortcut",   "value": "KEY_LEFTALT"},
            {"id": "K4", "label": "Mão / Pan (Espaço)",  "type": "shortcut",   "value": "KEY_SPACE"},
            {"id": "K5", "label": "Desfazer (Ctrl+Z)",   "type": "shortcut",   "value": "KEY_LEFTCTRL+KEY_Z"},
            {"id": "K6", "label": "Zoom + (Ctrl++)",     "type": "shortcut",   "value": "KEY_LEFTCTRL+KEY_EQUAL"},
            {"id": "K7", "label": "Zoom - (Ctrl+-)",     "type": "shortcut",   "value": "KEY_LEFTCTRL+KEY_MINUS"},
            {"id": "K8", "label": "Abrir Painel Ugee",   "type": "open_gui",   "value": "ugee_m708_gui.py"}
        ]
    }
}


def load_config():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        except Exception as ex:
            print(f"Aviso ao criar config padrão: {ex}")
        return json.loads(json.dumps(DEFAULT_CONFIG))

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        for section in ("workarea", "pen", "express_keys"):
            if section in user_cfg and isinstance(user_cfg[section], dict):
                for k, v in user_cfg[section].items():
                    if isinstance(v, dict) and isinstance(merged[section].get(k), dict):
                        merged[section][k].update(v)
                    else:
                        merged[section][k] = v
        return merged
    except Exception as ex:
        print(f"Erro ao ler {CONFIG_PATH}, usando padrão: {ex}")
        return json.loads(json.dumps(DEFAULT_CONFIG))


def eval_bezier_pressure(raw_p, max_p, curve_cfg):
    """
    Calcula a pressão de saída usando a curva Bézier quadrática (P0, P1X, P1Y, P2)
    idêntica ao QPressAdjustWedget do driver original.
    """
    if raw_p <= 0:
        return 0
    t_in = min(max(raw_p / float(max_p), 0.0), 1.0)
    p0 = float(curve_cfg.get("p0", 0.0))
    p1x = float(curve_cfg.get("p1x", 0.5))
    p1y = float(curve_cfg.get("p1y", 0.5))
    p2 = float(curve_cfg.get("p2", 1.0))

    if t_in <= p0:
        return 0
    u = (t_in - p0) / max(1.0 - p0, 1e-5)
    u = min(max(u, 0.0), 1.0)

    # Resolve B_x(s) = 2*(1-s)*s*p1x + s^2 = u para encontrar o parâmetro 's'
    a = 1.0 - 2.0 * p1x
    b = 2.0 * p1x
    c = -u
    if abs(a) < 1e-6:
        s = u / max(b, 1e-5)
    else:
        disc = max(b * b - 4.0 * a * c, 0.0)
        s = (-b + math.sqrt(disc)) / (2.0 * a)
    s = min(max(s, 0.0), 1.0)

    # Avalia B_y(s) = (2*(1-s)*s*p1y + s^2) * p2
    y_norm = (2.0 * (1.0 - s) * s * p1y + s * s) * p2
    y_norm = min(max(y_norm, 0.0), 1.0)
    return int(round(y_norm * max_p))


def transform_coordinates(raw_x, raw_y, max_x, max_y, workarea_cfg):
    """
    Aplica rotação (0/90/180/270), recorte de área ativa da mesa (tablet_area),
    recorte de área da tela (screen_area) e modo de precisão.
    Retorna: (out_x, out_y, rot_nx, rot_ny, inside_active_area)
    """
    rx_norm = min(max(raw_x / float(max_x), 0.0), 1.0)
    ry_norm = min(max(raw_y / float(max_y), 0.0), 1.0)

    # 1. Aplica rotação primeiro para alinhar com a orientação visual da mesa
    rot = int(workarea_cfg.get("rotation", 0)) % 360
    if rot == 180:
        ux = 1.0 - rx_norm
        uy = 1.0 - ry_norm
    elif rot == 90:
        ux = 1.0 - ry_norm
        uy = rx_norm
    elif rot == 270:
        ux = ry_norm
        uy = 1.0 - rx_norm
    else:
        ux = rx_norm
        uy = ry_norm

    # 2. Recorte da área ativa da mesa (tablet_area)
    ta = workarea_cfg.get("tablet_area", {})
    xmin = float(ta.get("x_min", 0.0))
    ymin = float(ta.get("y_min", 0.0))
    xmax = float(ta.get("x_max", 1.0))
    ymax = float(ta.get("y_max", 1.0))

    if xmax - xmin < 0.05:
        xmax = min(1.0, xmin + 0.05)
    if ymax - ymin < 0.05:
        ymax = min(1.0, ymin + 0.05)

    # Tolera 1% nas bordas para facilitar tocar os cantos da tela
    inside_area = (xmin - 0.01 <= ux <= xmax + 0.01) and (ymin - 0.01 <= uy <= ymax + 0.01)

    nx = (ux - xmin) / (xmax - xmin)
    ny = (uy - ymin) / (ymax - ymin)
    nx = min(max(nx, 0.0), 1.0)
    ny = min(max(ny, 0.0), 1.0)

    # 3. Mapeamento na área da tela ou Modo de Precisão
    if workarea_cfg.get("precision_mode", False):
        nx = 0.25 + nx * 0.50
        ny = 0.25 + ny * 0.50
    else:
        sa = workarea_cfg.get("screen_area", {})
        s_xmin = float(sa.get("x_min", 0.0))
        s_ymin = float(sa.get("y_min", 0.0))
        s_xmax = float(sa.get("x_max", 1.0))
        s_ymax = float(sa.get("y_max", 1.0))
        nx = s_xmin + nx * max(0.05, s_xmax - s_xmin)
        ny = s_ymin + ny * max(0.05, s_ymax - s_ymin)

    out_x = int(round(min(max(nx, 0.0), 1.0) * max_x))
    out_y = int(round(min(max(ny, 0.0), 1.0) * max_y))
    return out_x, out_y, ux, uy, inside_area


def find_m708_interface2():
    """
    Localiza especificamente o /dev/hidraw correspondente à Interface 2 (MI_02).
    Compatível com formatos de uevent de 4 ou 8 dígitos hexadecimais
    (ex: HID_ID=0003:000028BD:00000924 e MODALIAS=hid:b0003g0001v000028BDp00000924).
    """
    for sys_path in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
        try:
            uevent_path = os.path.join(sys_path, 'device', 'uevent')
            if not os.path.exists(uevent_path):
                continue
            with open(uevent_path, 'r') as f:
                uevent = f.read().upper()

            matched_pid = None
            for pid in PIDS:
                if (
                    re.search(rf"28BD:0*{pid:04X}\b", uevent)
                    or re.search(rf"V0*28BDP0*{pid:04X}\b", uevent)
                ):
                    matched_pid = pid
                    break

            if not matched_pid:
                continue

            # Verifica bInterfaceNumber subindo 1 ou 2 níveis a partir de device
            for rel in ('..', '../..'):
                b_iface_file = os.path.realpath(os.path.join(sys_path, 'device', rel, 'bInterfaceNumber'))
                if os.path.exists(b_iface_file):
                    with open(b_iface_file, 'r') as bf:
                        if bf.read().strip() == "02":
                            return f"/dev/{os.path.basename(sys_path)}", matched_pid

            real_dev = os.path.realpath(os.path.join(sys_path, 'device'))
            if "/INPUT2" in uevent or ":1.2/" in real_dev:
                return f"/dev/{os.path.basename(sys_path)}", matched_pid
        except Exception:
            continue
    return None, None


def get_shortcut_keycodes():
    """Retorna lista de keycodes suportados para o dispositivo virtual de atalhos."""
    codes = [
        e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE,
        e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT,
        e.KEY_LEFTALT, e.KEY_RIGHTALT, e.KEY_LEFTMETA,
        e.KEY_SPACE, e.KEY_TAB, e.KEY_ENTER, e.KEY_ESC, e.KEY_BACKSPACE, e.KEY_DELETE,
        e.KEY_EQUAL, e.KEY_MINUS, e.KEY_LEFTBRACE, e.KEY_RIGHTBRACE,
        e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT,
        e.KEY_PAGEUP, e.KEY_PAGEDOWN, e.KEY_HOME, e.KEY_END,
        e.KEY_MUTE, e.KEY_VOLUMEUP, e.KEY_VOLUMEDOWN, e.KEY_PLAYPAUSE,
        e.KEY_NEXTSONG, e.KEY_PREVIOUSSONG,
    ]
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        kc = getattr(e, f"KEY_{ch}", None)
        if kc is not None:
            codes.append(kc)
    for fn in range(1, 13):
        kc = getattr(e, f"KEY_F{fn}", None)
        if kc is not None:
            codes.append(kc)
    return list(set(codes))


def parse_combo_to_keycodes(combo_str):
    """Converte string como 'KEY_LEFTCTRL+KEY_Z' em lista de códigos evdev."""
    result = []
    if not combo_str:
        return result
    aliases = {
        "CTRL": "KEY_LEFTCTRL",
        "CONTROL": "KEY_LEFTCTRL",
        "SHIFT": "KEY_LEFTSHIFT",
        "ALT": "KEY_LEFTALT",
        "SUPER": "KEY_LEFTMETA",
        "META": "KEY_LEFTMETA",
        "WIN": "KEY_LEFTMETA",
        "PLUS": "KEY_EQUAL",
        "+": "KEY_EQUAL",
        "-": "KEY_MINUS",
        "[": "KEY_LEFTBRACE",
        "]": "KEY_RIGHTBRACE",
    }
    for part in combo_str.split("+"):
        part = part.strip().upper()
        if not part:
            continue
        if part in aliases:
            part = aliases[part]
        elif not part.startswith("KEY_") and not part.startswith("BTN_"):
            part = "KEY_" + part
        code = getattr(e, part, None)
        if code is not None:
            result.append(code)
    return result


def create_uinput_devices(pid, max_x=50800, max_y=30480, max_pressure=8191):
    # 1. Caneta (com INPUT_PROP_POINTER para mapeamento correto de mesa no Wayland)
    pen_caps = {
        e.EV_KEY: [
            e.BTN_TOUCH,
            e.BTN_TOOL_PEN,
            e.BTN_TOOL_RUBBER,
            e.BTN_STYLUS,
            e.BTN_STYLUS2,
        ],
        e.EV_ABS: [
            (e.ABS_X, AbsInfo(value=0, min=0, max=max_x, fuzz=0, flat=0, resolution=200)),
            (e.ABS_Y, AbsInfo(value=0, min=0, max=max_y, fuzz=0, flat=0, resolution=200)),
            (e.ABS_PRESSURE, AbsInfo(value=0, min=0, max=max_pressure, fuzz=0, flat=0, resolution=0)),
            (e.ABS_TILT_X, AbsInfo(value=0, min=-60, max=60, fuzz=0, flat=0, resolution=0)),
            (e.ABS_TILT_Y, AbsInfo(value=0, min=-60, max=60, fuzz=0, flat=0, resolution=0)),
        ],
    }
    pen = UInput(
        pen_caps,
        name="UGEE M708 Pen",
        bustype=e.BUS_USB,
        vendor=VID,
        product=pid,
        version=1,
        input_props=[e.INPUT_PROP_POINTER],
    )

    # 2. Pad Nativo (Express Keys) - Inclui ABS_MISC exigido pelo libinput
    pad_caps = {
        e.EV_KEY: [e.BTN_0, e.BTN_1, e.BTN_2, e.BTN_3, e.BTN_4, e.BTN_5, e.BTN_6, e.BTN_7],
        e.EV_ABS: [
            (e.ABS_MISC, AbsInfo(value=0, min=0, max=0, fuzz=0, flat=0, resolution=0)),
        ],
    }
    pad = UInput(
        pad_caps,
        name="UGEE M708 Pad",
        bustype=e.BUS_USB,
        vendor=VID,
        product=pid,
        version=1,
    )

    # 3. Teclado/Mouse Virtual para Atalhos Customizados (K1-K8 / Macros)
    kbd_caps = {
        e.EV_KEY: get_shortcut_keycodes(),
        e.EV_REL: [e.REL_WHEEL],
    }
    kbd = UInput(
        kbd_caps,
        name="UGEE M708 Shortcuts",
        bustype=e.BUS_USB,
        vendor=VID,
        product=pid,
        version=1,
    )
    return pen, pad, kbd


class IPCServer:
    """Servidor Unix Domain Socket não-bloqueante para comunicação em tempo real com a GUI."""
    def __init__(self):
        self.sock_path = get_socket_path()
        if os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(self.sock_path)
        os.chmod(self.sock_path, 0o600)
        self.server.listen(5)
        self.server.setblocking(False)
        self.clients = []
        self.last_broadcast = 0.0

    def fileno(self):
        return self.server.fileno()

    def get_select_fds(self):
        return [self.server.fileno()] + [c.fileno() for c in self.clients]

    def process_events(self, current_state, on_reload_cb, on_trigger_key_cb=None):
        # Aceita novas conexões da GUI
        try:
            while True:
                conn, _ = self.server.accept()
                conn.setblocking(False)
                self.clients.append(conn)
                self.send_to_client(conn, {"event": "telemetry", **current_state})
        except BlockingIOError:
            pass

        # Lê comandos dos clientes conectados
        for conn in list(self.clients):
            try:
                data = conn.recv(4096)
                if not data:
                    self.clients.remove(conn)
                    conn.close()
                    continue
                for line in data.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    cmd = msg.get("cmd")
                    if cmd == "reload_config":
                        on_reload_cb()
                        self.send_to_client(conn, {"event": "ack", "cmd": "reload_config"})
                    elif cmd == "get_state":
                        self.send_to_client(conn, {"event": "telemetry", **current_state})
                    elif cmd == "trigger_key" and on_trigger_key_cb is not None:
                        idx = int(msg.get("index", 0))
                        on_trigger_key_cb(idx)
            except BlockingIOError:
                pass
            except Exception:
                if conn in self.clients:
                    self.clients.remove(conn)
                try:
                    conn.close()
                except Exception:
                    pass

    def send_to_client(self, conn, payload):
        try:
            raw = (json.dumps(payload) + "\n").encode("utf-8")
            conn.sendall(raw)
        except Exception:
            if conn in self.clients:
                self.clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass

    def broadcast_telemetry(self, state, force=False):
        if not self.clients:
            return
        now = time.monotonic()
        # ~100 FPS durante uso da caneta para desenho fluido no quadro de teste
        if not force and (now - self.last_broadcast) < 0.010:
            return
        self.last_broadcast = now
        for conn in list(self.clients):
            self.send_to_client(conn, {"event": "telemetry", **state})

    def close(self):
        for c in self.clients:
            try:
                c.close()
            except Exception:
                pass
        try:
            self.server.close()
        except Exception:
            pass
        if os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except Exception:
                pass


def send_osd_notification(title, body):
    try:
        subprocess.Popen(
            [
                "notify-send",
                "-a", "Ugee M708",
                "-i", "input-tablet",
                "-t", "1200",
                "-h", "string:x-canonical-private-synchronous:ugee-m708",
                title,
                body,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def launch_gui():
    gui_paths = [
        "/usr/local/bin/ugee_m708_gui.py",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ugee_m708_gui.py"),
    ]
    for gp in gui_paths:
        if os.path.exists(gp):
            try:
                subprocess.Popen([sys.executable, gp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass


def main():
    print("Iniciando daemon do driver Ugee M708 para Linux (Wayland/GNOME)...")
    config = load_config()
    ipc = IPCServer()

    state = {
        "connected": False,
        "pid": 0,
        "hidraw": "",
        "max_x": 50800,
        "max_y": 30480,
        "max_pressure": 8191,
        "x": 0,
        "y": 0,
        "norm_x": 0.0,
        "norm_y": 0.0,
        "inside_area": True,
        "raw_pressure": 0,
        "pressure": 0,
        "tilt_x": 0,
        "tilt_y": 0,
        "buttons": 0,
        "pen_btn1": 0,
        "pen_btn2": 0,
        "eraser": False,
        "in_proximity": False,
    }

    def reload_cfg():
        nonlocal config
        config = load_config()
        # Se houver pressão bruta presente, recalcula imediatamente para refletir na GUI
        if state["raw_pressure"] > 0:
            pen_cfg = config.get("pen", {})
            if pen_cfg.get("pressure_enabled", True):
                state["pressure"] = eval_bezier_pressure(
                    state["raw_pressure"], state["max_pressure"], pen_cfg.get("pressure_curve", {})
                )
            else:
                state["pressure"] = state["max_pressure"]
            ipc.broadcast_telemetry(state, force=True)
        print("Configuração recarregada via IPC.")

    try:
        while True:
            hidraw_path, pid = find_m708_interface2()
            if not hidraw_path:
                state["connected"] = False
                state["pid"] = 0
                state["hidraw"] = ""
                ipc.process_events(state, reload_cfg)
                ipc.broadcast_telemetry(state, force=True)
                rlist, _, _ = select.select(ipc.get_select_fds(), [], [], 1.0)
                if rlist:
                    ipc.process_events(state, reload_cfg)
                continue

            max_pressure = 16383 if pid == 0x0927 else 8191
            max_x, max_y = 50800, 30480

            fd = None
            pen = None
            pad = None
            kbd = None
            try:
                try:
                    fd = os.open(hidraw_path, os.O_RDWR | os.O_NONBLOCK)
                except OSError as ex:
                    state["connected"] = False
                    state["pid"] = 0
                    state["hidraw"] = ""
                    ipc.broadcast_telemetry(state, force=True)
                    print(f"Aviso: Não foi possível abrir {hidraw_path} ({ex}). Verifique as permissões udev.")
                    time.sleep(1.5)
                    continue

                try:
                    os.write(fd, bytes([0x02, 0xB0, 0x04] + [0x00] * 7))
                except OSError:
                    try:
                        os.write(fd, bytes([0x02, 0xB0, 0x04] + [0x00] * 17))
                    except OSError as ex:
                        print(f"Aviso ao enviar init report: {ex}")

                state.update({
                    "connected": True,
                    "pid": pid,
                    "hidraw": hidraw_path,
                    "max_x": max_x,
                    "max_y": max_y,
                    "max_pressure": max_pressure,
                })
                print(f"Conectado com sucesso à Ugee M708 (PID 0x{pid:04X}) em {hidraw_path}")
                ipc.broadcast_telemetry(state, force=True)

                pen, pad, kbd = create_uinput_devices(pid, max_x=max_x, max_y=max_y, max_pressure=max_pressure)

                in_proximity = False
                current_tool = e.BTN_TOOL_PEN
                prev_btn_mask = 0
                prev_pen_b1 = 0
                prev_pen_b2 = 0
                active_shortcuts = {}  # bit_idx -> list of pressed keycodes
                active_pen_shortcuts = {}
                eraser_toggle = False

                def execute_express_key(idx, is_pressed):
                    nonlocal eraser_toggle
                    ek_cfg = config.get("express_keys", {})
                    if not ek_cfg.get("enabled", True):
                        return
                    keys_list = ek_cfg.get("keys", [])
                    osd_on = ek_cfg.get("osd_notifications", True)
                    k_item = keys_list[idx] if idx < len(keys_list) else {"type": "tablet_button", "label": f"K{idx+1}"}
                    k_type = k_item.get("type", "shortcut")
                    k_val = k_item.get("value", "")
                    k_label = k_item.get("label", f"K{idx+1}")

                    if k_type == "disabled":
                        return
                    elif k_type == "tablet_button":
                        pad.write(e.EV_KEY, e.BTN_0 + idx, 1 if is_pressed else 0)
                        pad.write(e.EV_ABS, e.ABS_MISC, 15 if is_pressed else 0)
                        pad.syn()
                    elif k_type == "shortcut":
                        if k_val in ("SCROLL_UP", "SCROLL_DOWN"):
                            if is_pressed:
                                kbd.write(e.EV_REL, e.REL_WHEEL, 1 if k_val == "SCROLL_UP" else -1)
                                kbd.syn()
                                if osd_on:
                                    send_osd_notification(f"Tecla K{idx+1}", k_label)
                            return
                        if is_pressed:
                            codes = parse_combo_to_keycodes(k_val)
                            active_shortcuts[idx] = codes
                            for c in codes:
                                kbd.write(e.EV_KEY, c, 1)
                            kbd.syn()
                            if osd_on:
                                send_osd_notification(f"Tecla K{idx+1}", k_label)
                        else:
                            codes = active_shortcuts.pop(idx, [])
                            for c in reversed(codes):
                                kbd.write(e.EV_KEY, c, 0)
                            kbd.syn()
                    elif k_type == "precision_toggle" and is_pressed:
                        wa = config.setdefault("workarea", {})
                        wa["precision_mode"] = not wa.get("precision_mode", False)
                        mode_txt = "Ativado" if wa["precision_mode"] else "Desativado"
                        if osd_on:
                            send_osd_notification("Modo de Precisão", f"Modo de precisão: {mode_txt}")
                    elif k_type == "eraser_toggle" and is_pressed:
                        eraser_toggle = not eraser_toggle
                        mode_txt = "Borracha" if eraser_toggle else "Caneta"
                        if osd_on:
                            send_osd_notification("Alternar Ferramenta", f"Modo: {mode_txt}")
                    elif k_type == "open_gui" and is_pressed:
                        launch_gui()
                    elif k_type == "run_cmd" and is_pressed and k_val:
                        try:
                            subprocess.Popen(k_val, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            if osd_on:
                                send_osd_notification(f"Tecla K{idx+1}", k_label)
                        except Exception:
                            pass

                def trigger_virtual_key(idx):
                    execute_express_key(idx, True)
                    time.sleep(0.02)
                    execute_express_key(idx, False)

                while True:
                    watch_fds = [fd] + ipc.get_select_fds()
                    rlist, _, _ = select.select(watch_fds, [], [], 0.2)
                    if any(f != fd for f in rlist):
                        ipc.process_events(state, reload_cfg, trigger_virtual_key)

                    if fd not in rlist:
                        continue

                    report = os.read(fd, 64)
                    if not report or len(report) < 10:
                        continue

                    if report[0] != 0x02:
                        continue

                    status = report[1]
                    if status in (0xB4, 0xF2, 0xF8):
                        continue

                    # 1. Caneta fora de alcance (0xC0)
                    if status == 0xC0:
                        if in_proximity:
                            pen.write(e.EV_KEY, e.BTN_TOUCH, 0)
                            pen.write(e.EV_KEY, e.BTN_STYLUS, 0)
                            pen.write(e.EV_KEY, e.BTN_STYLUS2, 0)
                            pen.write(e.EV_KEY, current_tool, 0)
                            pen.write(e.EV_ABS, e.ABS_PRESSURE, 0)
                            pen.syn()
                            in_proximity = False
                        for p_codes in active_pen_shortcuts.values():
                            for c in reversed(p_codes):
                                kbd.write(e.EV_KEY, c, 0)
                            kbd.syn()
                        active_pen_shortcuts.clear()
                        prev_pen_b1 = 0
                        prev_pen_b2 = 0
                        state.update({
                            "raw_pressure": 0,
                            "pressure": 0,
                            "pen_btn1": 0,
                            "pen_btn2": 0,
                            "in_proximity": False,
                        })
                        ipc.broadcast_telemetry(state, force=True)
                        continue

                    # 2. Botões da mesa (Express Keys: status == 0xF0)
                    if status == 0xF0:
                        btn_mask = report[2]
                        state["buttons"] = btn_mask
                        ipc.broadcast_telemetry(state, force=True)

                        for i in range(8):
                            was_pressed = bool(prev_btn_mask & (1 << i))
                            is_pressed = bool(btn_mask & (1 << i))
                            if was_pressed != is_pressed:
                                execute_express_key(i, is_pressed)

                        prev_btn_mask = btn_mask
                        continue

                    # 3. Relatório da Caneta (nibble superior 0xA0)
                    if (status & 0xF0) == 0xA0:
                        raw_x = report[2] | (report[3] << 8)
                        raw_y = report[4] | (report[5] << 8)
                        raw_pressure = report[6] | (report[7] << 8)
                        tilt_x, tilt_y = struct.unpack('bb', report[8:10])

                        if len(report) >= 12:
                            raw_x |= (report[10] << 16)
                            raw_y |= (report[11] << 16)

                        if len(report) >= 12 and max_pressure == 8191:
                            raw_pressure = min(raw_pressure // 2, 8191)

                        # Aplica transformações de Área de Trabalho e Rotação
                        wa_cfg = config.get("workarea", {})
                        x, y, rot_nx, rot_ny, inside_area = transform_coordinates(
                            raw_x, raw_y, max_x, max_y, wa_cfg
                        )

                        # Aplica Curva de Pressão Bézier
                        pen_cfg = config.get("pen", {})
                        tip_bit = bool(status & 0x01)
                        if not pen_cfg.get("pressure_enabled", True):
                            pressure = max_pressure if (tip_bit or raw_pressure > 0) else 0
                            tip_down = tip_bit or raw_pressure > 0
                        else:
                            pressure = eval_bezier_pressure(
                                raw_pressure, max_pressure, pen_cfg.get("pressure_curve", {})
                            )
                            tip_down = pressure > 0

                        if not pen_cfg.get("tilt_enabled", True):
                            tilt_x, tilt_y = 0, 0

                        b1_raw = 1 if (status & 0x02) else 0
                        b2_raw = 1 if (status & 0x04) else 0
                        b1_act = pen_cfg.get("btn1_action", "BTN_STYLUS")
                        b2_act = pen_cfg.get("btn2_action", "BTN_STYLUS2")

                        is_eraser = bool(status & 0x08) or eraser_toggle
                        if (b1_raw and b1_act == "ERASER") or (b2_raw and b2_act == "ERASER"):
                            is_eraser = True

                        new_tool = e.BTN_TOOL_RUBBER if is_eraser else e.BTN_TOOL_PEN

                        if not in_proximity:
                            current_tool = new_tool
                            pen.write(e.EV_KEY, current_tool, 1)
                            in_proximity = True
                        elif new_tool != current_tool:
                            pen.write(e.EV_KEY, current_tool, 0)
                            current_tool = new_tool
                            pen.write(e.EV_KEY, current_tool, 1)

                        # Trata ações especiais nos botões da caneta (ex: CTRL+Z, SPACE, etc.)
                        for btn_idx, b_raw, b_prev, b_act in (
                            (1, b1_raw, prev_pen_b1, b1_act),
                            (2, b2_raw, prev_pen_b2, b2_act),
                        ):
                            if b_raw != b_prev and b_act not in ("BTN_STYLUS", "BTN_STYLUS2", "ERASER", "DISABLED"):
                                if b_raw:
                                    codes = parse_combo_to_keycodes(b_act)
                                    active_pen_shortcuts[btn_idx] = codes
                                    for c in codes:
                                        kbd.write(e.EV_KEY, c, 1)
                                    kbd.syn()
                                else:
                                    codes = active_pen_shortcuts.pop(btn_idx, [])
                                    for c in reversed(codes):
                                        kbd.write(e.EV_KEY, c, 0)
                                    kbd.syn()
                        prev_pen_b1 = b1_raw
                        prev_pen_b2 = b2_raw

                        out_stylus1 = 0
                        out_stylus2 = 0
                        if b1_act == "BTN_STYLUS":
                            out_stylus1 |= b1_raw
                        elif b1_act == "BTN_STYLUS2":
                            out_stylus2 |= b1_raw

                        if b2_act == "BTN_STYLUS":
                            out_stylus1 |= b2_raw
                        elif b2_act == "BTN_STYLUS2":
                            out_stylus2 |= b2_raw

                        # Se a caneta estiver dentro da área ativa da mesa configurada, atualiza X/Y e toque
                        if inside_area:
                            pen.write(e.EV_ABS, e.ABS_X, x)
                            pen.write(e.EV_ABS, e.ABS_Y, y)
                            pen.write(e.EV_ABS, e.ABS_PRESSURE, pressure)
                            pen.write(e.EV_ABS, e.ABS_TILT_X, tilt_x)
                            pen.write(e.EV_ABS, e.ABS_TILT_Y, tilt_y)
                            pen.write(e.EV_KEY, e.BTN_TOUCH, 1 if tip_down else 0)
                            pen.write(e.EV_KEY, e.BTN_STYLUS, out_stylus1)
                            pen.write(e.EV_KEY, e.BTN_STYLUS2, out_stylus2)
                            pen.syn()
                        else:
                            # Fora da área recortada da mesa: libera o toque para não riscar fora da área ativa
                            pen.write(e.EV_ABS, e.ABS_PRESSURE, 0)
                            pen.write(e.EV_KEY, e.BTN_TOUCH, 0)
                            pen.syn()

                        state.update({
                            "x": x,
                            "y": y,
                            "norm_x": round(rot_nx, 4),
                            "norm_y": round(rot_ny, 4),
                            "inside_area": inside_area,
                            "raw_pressure": raw_pressure,
                            "pressure": pressure,
                            "tilt_x": tilt_x,
                            "tilt_y": tilt_y,
                            "pen_btn1": b1_raw,
                            "pen_btn2": b2_raw,
                            "eraser": is_eraser,
                            "in_proximity": True,
                        })
                        ipc.broadcast_telemetry(state)

            except OSError as ex:
                print(f"Dispositivo desconectado ({ex}). Aguardando reconexão...")
                time.sleep(1.0)
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
                if pen is not None:
                    pen.close()
                if pad is not None:
                    pad.close()
                if kbd is not None:
                    kbd.close()
    finally:
        ipc.close()


if __name__ == '__main__':
    main()
