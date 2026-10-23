#!/usr/bin/env python3
"""
Interface Gráfica Nativa (GTK 4 / Libadwaita / Cairo) para o Driver Ugee M708 no Linux
Baseada na arquitetura e recursos da interface oficial ugeeTablet.exe:
  1. Área de Trabalho (Rotação 0/90/180/270, Recorte interativo nos 4 cantos/bordas da Mesa e da Tela, Modo de Precisão)
  2. Caneta e Pressão (Editor interativo de curva Bézier P0/P1X/P1Y/P2, Botões da Haste, Canvas de Teste com Pressão Real)
  3. Teclas de Atalho K1-K8 (Indicadores ao vivo, Predefinições, Atalhos customizados, Painel Flutuante funcional via IPC)
  4. Ferramenta de Diagnóstico em Tempo Real e Importação/Exportação de Perfis
"""

import os
import sys
import json
import math
import socket
import subprocess

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, GLib, Gio, GdkPixbuf

CONFIG_DIR = os.path.expanduser("~/.config/ugee-tablet")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

ASSET_DIRS = [
    "/usr/local/share/ugee-m708/assets",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"),
]

# Coordenadas originais do <PictureFiles> do Ugee_Tablet.xml para as imagens M708_K0..K3 (300x300)
PICTURE_RECTS = {
    0:   (77,  88, 175, 110),
    90:  (92,  65, 110, 175),
    180: (48,  88, 175, 110),
    270: (92,  60, 110, 175),
}

PRESET_SHORTCUTS = [
    ("Pincel (B)",              "shortcut",         "KEY_B"),
    ("Borracha (E)",            "shortcut",         "KEY_E"),
    ("Conta-gotas (Alt)",       "shortcut",         "KEY_LEFTALT"),
    ("Mão / Pan (Espaço)",      "shortcut",         "KEY_SPACE"),
    ("Desfazer (Ctrl+Z)",       "shortcut",         "KEY_LEFTCTRL+KEY_Z"),
    ("Refazer (Ctrl+Shift+Z)",  "shortcut",         "KEY_LEFTCTRL+KEY_LEFTSHIFT+KEY_Z"),
    ("Salvar (Ctrl+S)",         "shortcut",         "KEY_LEFTCTRL+KEY_S"),
    ("Copiar (Ctrl+C)",         "shortcut",         "KEY_LEFTCTRL+KEY_C"),
    ("Colar (Ctrl+V)",          "shortcut",         "KEY_LEFTCTRL+KEY_V"),
    ("Zoom + (Ctrl++)",         "shortcut",         "KEY_LEFTCTRL+KEY_EQUAL"),
    ("Zoom - (Ctrl+-)",         "shortcut",         "KEY_LEFTCTRL+KEY_MINUS"),
    ("Aumentar Pincel (])",     "shortcut",         "KEY_RIGHTBRACE"),
    ("Diminuir Pincel ([)",     "shortcut",         "KEY_LEFTBRACE"),
    ("Rolagem Acima (Scroll+)", "shortcut",         "SCROLL_UP"),
    ("Rolagem Abaixo (Scroll-)","shortcut",         "SCROLL_DOWN"),
    ("Alternar Modo Precisão",  "precision_toggle", ""),
    ("Alternar Caneta/Borracha","eraser_toggle",    ""),
    ("Abrir Painel Ugee",       "open_gui",         "ugee_m708_gui.py"),
    ("Botão Nativo Wayland",    "tablet_button",    ""),
    ("Atalho Personalizado...", "custom_shortcut",  ""),
    ("Executar Aplicativo...",  "run_cmd",          ""),
    ("Sem Função (Desativado)", "disabled",         ""),
]

PEN_BTN_OPTIONS = [
    ("Clique Direito (Stylus 1)",           "BTN_STYLUS"),
    ("Clique de Rolagem / Meio (Stylus 2)", "BTN_STYLUS2"),
    ("Modo Borracha (Eraser)",              "ERASER"),
    ("Desfazer (Ctrl+Z)",                   "KEY_LEFTCTRL+KEY_Z"),
    ("Mão / Arrastar (Espaço)",             "KEY_SPACE"),
    ("Pincel (B)",                          "KEY_B"),
    ("Sem Função (Desativado)",             "DISABLED"),
]


def find_asset(filename):
    for d in ASSET_DIRS:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return None


def get_socket_path():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if os.path.isdir(runtime_dir):
        return os.path.join(runtime_dir, "ugee-m708.sock")
    return f"/tmp/ugee-m708-{os.getuid()}.sock"


def get_default_config():
    return {
        "workarea": {
            "rotation": 0,
            "keep_aspect_ratio": False,
            "precision_mode": False,
            "tablet_area": {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0},
            "screen_area": {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0},
        },
        "pen": {
            "pressure_enabled": True,
            "tilt_enabled": True,
            "pressure_curve": {"p0": 0.0, "p1x": 0.5, "p1y": 0.5, "p2": 1.0},
            "btn1_action": "BTN_STYLUS",
            "btn2_action": "BTN_STYLUS2",
        },
        "express_keys": {
            "enabled": True,
            "osd_notifications": True,
            "keys": [
                {"id": "K1", "label": "Pincel (B)",          "type": "shortcut", "value": "KEY_B"},
                {"id": "K2", "label": "Borracha (E)",        "type": "shortcut", "value": "KEY_E"},
                {"id": "K3", "label": "Conta-gotas (Alt)",   "type": "shortcut", "value": "KEY_LEFTALT"},
                {"id": "K4", "label": "Mão / Pan (Espaço)",  "type": "shortcut", "value": "KEY_SPACE"},
                {"id": "K5", "label": "Desfazer (Ctrl+Z)",   "type": "shortcut", "value": "KEY_LEFTCTRL+KEY_Z"},
                {"id": "K6", "label": "Zoom + (Ctrl++)",     "type": "shortcut", "value": "KEY_LEFTCTRL+KEY_EQUAL"},
                {"id": "K7", "label": "Zoom - (Ctrl+-)",     "type": "shortcut", "value": "KEY_LEFTCTRL+KEY_MINUS"},
                {"id": "K8", "label": "Abrir Painel Ugee",   "type": "open_gui", "value": "ugee_m708_gui.py"},
            ],
        },
    }


def load_config():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    default = get_default_config()
    if not os.path.exists(CONFIG_PATH):
        save_config(default)
        return default
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for sec in ("workarea", "pen", "express_keys"):
            if sec in data and isinstance(data[sec], dict):
                for k, v in data[sec].items():
                    if isinstance(v, dict) and isinstance(default[sec].get(k), dict):
                        default[sec][k].update(v)
                    else:
                        default[sec][k] = v
        return default
    except Exception:
        return default


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


class TabletAreaWidget(Gtk.DrawingArea):
    """
    Widget interativo para ajuste visual da Área Ativa sobre a imagem da Ugee M708.
    Suporta redimensionamento pelos 4 cantos (tl, tr, bl, br), pelas 4 bordas (l, r, t, b),
    movimentação central e visualização em tempo real do ponto onde a caneta está na mesa.
    """
    def __init__(self, config, on_change_cb):
        super().__init__()
        self.config = config
        self.on_change_cb = on_change_cb
        self.live_pen = None  # (norm_x, norm_y, inside_area)
        self.set_content_width(330)
        self.set_content_height(310)
        self.set_draw_func(self.on_draw)

        self.pixbufs = {}
        for idx, angle in enumerate((0, 90, 180, 270)):
            p = find_asset(f"M708_K{idx}.png")
            if p:
                try:
                    self.pixbufs[angle] = GdkPixbuf.Pixbuf.new_from_file_at_scale(p, 300, 300, True)
                except Exception:
                    pass

        self.drag_mode = None
        self.drag_start = (0, 0)
        self.orig_area = {}

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    def get_base_rect(self, width, height):
        rot = int(self.config["workarea"].get("rotation", 0)) % 360
        bx, by, bw, bh = PICTURE_RECTS.get(rot, PICTURE_RECTS[0])
        ox = (width - 300) / 2.0
        oy = (height - 300) / 2.0
        return ox + bx, oy + by, bw, bh

    def on_draw(self, area, cr, width, height):
        rot = int(self.config["workarea"].get("rotation", 0)) % 360
        ox = (width - 300) / 2.0
        oy = (height - 300) / 2.0

        # Fundo suave
        cr.set_source_rgba(0.14, 0.16, 0.20, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        pb = self.pixbufs.get(rot)
        if pb:
            Gdk.cairo_set_source_pixbuf(cr, pb, ox, oy)
            cr.paint()
        else:
            cr.set_source_rgba(0.25, 0.28, 0.34, 1.0)
            cr.rectangle(ox + 30, oy + 60, 240, 180)
            cr.fill()

        bx, by, bw, bh = self.get_base_rect(width, height)
        # Borda limite da área física total
        cr.set_source_rgba(0.7, 0.75, 0.85, 0.45)
        cr.set_line_width(1.2)
        cr.rectangle(bx, by, bw, bh)
        cr.stroke()

        # Retângulo ativo configurado
        ta = self.config["workarea"]["tablet_area"]
        rx = bx + ta["x_min"] * bw
        ry = by + ta["y_min"] * bh
        rw = (ta["x_max"] - ta["x_min"]) * bw
        rh = (ta["y_max"] - ta["y_min"]) * bh

        cr.set_source_rgba(0.01, 0.40, 1.0, 0.28)
        cr.rectangle(rx, ry, rw, rh)
        cr.fill()

        cr.set_source_rgba(0.10, 0.55, 1.0, 0.95)
        cr.set_line_width(2.0)
        cr.rectangle(rx, ry, rw, rh)
        cr.stroke()

        # Alças nos 4 cantos + 4 meios de borda
        handles = [
            (rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh),
            (rx + rw * 0.5, ry), (rx + rw * 0.5, ry + rh),
            (rx, ry + rh * 0.5), (rx + rw, ry + rh * 0.5),
        ]
        for hx, hy in handles:
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            cr.arc(hx, hy, 4.5, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(0.01, 0.40, 1.0, 1.0)
            cr.set_line_width(1.8)
            cr.arc(hx, hy, 4.5, 0, 2 * math.pi)
            cr.stroke()

        # Ponto ao vivo da caneta sobre a superfície da mesa
        if self.live_pen is not None:
            pnx, pny, inside = self.live_pen
            px = bx + pnx * bw
            py = by + pny * bh
            if inside:
                cr.set_source_rgba(0.15, 0.95, 0.45, 0.95)
            else:
                cr.set_source_rgba(1.0, 0.35, 0.25, 0.95)
            cr.arc(px, py, 5.5, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
            cr.set_line_width(1.5)
            cr.arc(px, py, 5.5, 0, 2 * math.pi)
            cr.stroke()

        # Texto de dimensões
        pct_w = int(round((ta["x_max"] - ta["x_min"]) * 100))
        pct_h = int(round((ta["y_max"] - ta["y_min"]) * 100))
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(11)
        cr.move_to(bx, by - 8)
        cr.show_text(f"Mesa Ativa: {pct_w}% × {pct_h}% ({rot}°)")

    def on_drag_begin(self, gesture, start_x, start_y):
        w = self.get_width()
        h = self.get_height()
        bx, by, bw, bh = self.get_base_rect(w, h)
        ta = self.config["workarea"]["tablet_area"]
        rx = bx + ta["x_min"] * bw
        ry = by + ta["y_min"] * bh
        rw = (ta["x_max"] - ta["x_min"]) * bw
        rh = (ta["y_max"] - ta["y_min"]) * bh

        self.drag_start = (start_x, start_y)
        self.orig_area = dict(ta)

        tol = 14.0
        # 4 Cantos
        if math.hypot(start_x - rx, start_y - ry) < tol:
            self.drag_mode = "tl"
        elif math.hypot(start_x - (rx + rw), start_y - ry) < tol:
            self.drag_mode = "tr"
        elif math.hypot(start_x - rx, start_y - (ry + rh)) < tol:
            self.drag_mode = "bl"
        elif math.hypot(start_x - (rx + rw), start_y - (ry + rh)) < tol:
            self.drag_mode = "br"
        # 4 Bordas
        elif abs(start_x - rx) < tol and (ry <= start_y <= ry + rh):
            self.drag_mode = "l"
        elif abs(start_x - (rx + rw)) < tol and (ry <= start_y <= ry + rh):
            self.drag_mode = "r"
        elif abs(start_y - ry) < tol and (rx <= start_x <= rx + rw):
            self.drag_mode = "t"
        elif abs(start_y - (ry + rh)) < tol and (rx <= start_x <= rx + rw):
            self.drag_mode = "b"
        # Interior (Mover)
        elif rx <= start_x <= rx + rw and ry <= start_y <= ry + rh:
            self.drag_mode = "move"
        else:
            self.drag_mode = None

    def on_drag_update(self, gesture, offset_x, offset_y):
        if not self.drag_mode:
            return
        w = self.get_width()
        h = self.get_height()
        _, _, bw, bh = self.get_base_rect(w, h)
        dx = offset_x / max(float(bw), 1.0)
        dy = offset_y / max(float(bh), 1.0)
        ta = self.config["workarea"]["tablet_area"]
        min_span = 0.10

        if "l" in self.drag_mode:
            ta["x_min"] = round(max(0.0, min(ta["x_max"] - min_span, self.orig_area["x_min"] + dx)), 3)
        if "r" in self.drag_mode:
            ta["x_max"] = round(min(1.0, max(ta["x_min"] + min_span, self.orig_area["x_max"] + dx)), 3)
        if "t" in self.drag_mode:
            ta["y_min"] = round(max(0.0, min(ta["y_max"] - min_span, self.orig_area["y_min"] + dy)), 3)
        if "b" in self.drag_mode:
            ta["y_max"] = round(min(1.0, max(ta["y_min"] + min_span, self.orig_area["y_max"] + dy)), 3)
        if self.drag_mode == "move":
            span_x = self.orig_area["x_max"] - self.orig_area["x_min"]
            span_y = self.orig_area["y_max"] - self.orig_area["y_min"]
            nx = min(max(self.orig_area["x_min"] + dx, 0.0), 1.0 - span_x)
            ny = min(max(self.orig_area["y_min"] + dy, 0.0), 1.0 - span_y)
            ta["x_min"] = round(nx, 3)
            ta["y_min"] = round(ny, 3)
            ta["x_max"] = round(nx + span_x, 3)
            ta["y_max"] = round(ny + span_y, 3)

        self.queue_draw()
        self.on_change_cb(live_only=False)

    def on_drag_end(self, gesture, offset_x, offset_y):
        if self.drag_mode:
            self.drag_mode = None
            self.on_change_cb(live_only=False)


class ScreenAreaWidget(Gtk.DrawingArea):
    """Widget interativo para definir qual região do Monitor (Tela) a mesa percorre."""
    def __init__(self, config, on_change_cb):
        super().__init__()
        self.config = config
        self.on_change_cb = on_change_cb
        self.set_content_width(280)
        self.set_content_height(165)
        self.set_draw_func(self.on_draw)

        self.drag_mode = None
        self.orig_area = {}

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    def get_monitor_rect(self, width, height):
        mw = min(width - 28, (height - 32) * (16.0 / 9.0))
        mh = mw * (9.0 / 16.0)
        mx = (width - mw) / 2.0
        my = (height - mh) / 2.0 + 6
        return mx, my, mw, mh

    def on_draw(self, area, cr, width, height):
        cr.set_source_rgba(0.14, 0.16, 0.20, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        mx, my, mw, mh = self.get_monitor_rect(width, height)

        # Moldura do monitor 16:9
        cr.set_source_rgba(0.20, 0.23, 0.29, 1.0)
        cr.rectangle(mx, my, mw, mh)
        cr.fill()
        cr.set_source_rgba(0.65, 0.70, 0.80, 0.8)
        cr.set_line_width(2.0)
        cr.rectangle(mx, my, mw, mh)
        cr.stroke()

        sa = self.config["workarea"]["screen_area"]
        rx = mx + sa["x_min"] * mw
        ry = my + sa["y_min"] * mh
        rw = (sa["x_max"] - sa["x_min"]) * mw
        rh = (sa["y_max"] - sa["y_min"]) * mh

        cr.set_source_rgba(0.15, 0.75, 0.45, 0.28)
        cr.rectangle(rx, ry, rw, rh)
        cr.fill()

        cr.set_source_rgba(0.20, 0.85, 0.50, 0.95)
        cr.set_line_width(2.0)
        cr.rectangle(rx, ry, rw, rh)
        cr.stroke()

        for hx, hy in [(rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh)]:
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            cr.arc(hx, hy, 4.0, 0, 2 * math.pi)
            cr.fill()

        pct_w = int(round((sa["x_max"] - sa["x_min"]) * 100))
        pct_h = int(round((sa["y_max"] - sa["y_min"]) * 100))
        cr.set_source_rgba(0.95, 0.97, 1.0, 0.95)
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(11)
        cr.move_to(mx, my - 6)
        cr.show_text(f"Região da Tela: {pct_w}% × {pct_h}%")

    def on_drag_begin(self, gesture, start_x, start_y):
        mx, my, mw, mh = self.get_monitor_rect(self.get_width(), self.get_height())
        sa = self.config["workarea"]["screen_area"]
        rx = mx + sa["x_min"] * mw
        ry = my + sa["y_min"] * mh
        rw = (sa["x_max"] - sa["x_min"]) * mw
        rh = (sa["y_max"] - sa["y_min"]) * mh
        self.orig_area = dict(sa)
        tol = 13.0
        if math.hypot(start_x - rx, start_y - ry) < tol:
            self.drag_mode = "tl"
        elif math.hypot(start_x - (rx + rw), start_y - ry) < tol:
            self.drag_mode = "tr"
        elif math.hypot(start_x - rx, start_y - (ry + rh)) < tol:
            self.drag_mode = "bl"
        elif math.hypot(start_x - (rx + rw), start_y - (ry + rh)) < tol:
            self.drag_mode = "br"
        elif rx <= start_x <= rx + rw and ry <= start_y <= ry + rh:
            self.drag_mode = "move"
        else:
            self.drag_mode = None

    def on_drag_update(self, gesture, offset_x, offset_y):
        if not self.drag_mode:
            return
        _, _, mw, mh = self.get_monitor_rect(self.get_width(), self.get_height())
        dx = offset_x / max(float(mw), 1.0)
        dy = offset_y / max(float(mh), 1.0)
        sa = self.config["workarea"]["screen_area"]
        min_span = 0.10
        if "l" in self.drag_mode:
            sa["x_min"] = round(max(0.0, min(sa["x_max"] - min_span, self.orig_area["x_min"] + dx)), 3)
        if "r" in self.drag_mode:
            sa["x_max"] = round(min(1.0, max(sa["x_min"] + min_span, self.orig_area["x_max"] + dx)), 3)
        if "t" in self.drag_mode:
            sa["y_min"] = round(max(0.0, min(sa["y_max"] - min_span, self.orig_area["y_min"] + dy)), 3)
        if "b" in self.drag_mode:
            sa["y_max"] = round(min(1.0, max(sa["y_min"] + min_span, self.orig_area["y_max"] + dy)), 3)
        if self.drag_mode == "move":
            span_x = self.orig_area["x_max"] - self.orig_area["x_min"]
            span_y = self.orig_area["y_max"] - self.orig_area["y_min"]
            nx = min(max(self.orig_area["x_min"] + dx, 0.0), 1.0 - span_x)
            ny = min(max(self.orig_area["y_min"] + dy, 0.0), 1.0 - span_y)
            sa["x_min"] = round(nx, 3)
            sa["y_min"] = round(ny, 3)
            sa["x_max"] = round(nx + span_x, 3)
            sa["y_max"] = round(ny + span_y, 3)
        self.queue_draw()
        self.on_change_cb(live_only=False)

    def on_drag_end(self, gesture, offset_x, offset_y):
        if self.drag_mode:
            self.drag_mode = None
            self.on_change_cb(live_only=False)


class PressureCurveWidget(Gtk.DrawingArea):
    """Gráfico interativo da Curva de Pressão Bézier (QPressAdjustWedget portado para Cairo)."""
    def __init__(self, config, on_change_cb):
        super().__init__()
        self.config = config
        self.on_change_cb = on_change_cb
        self.current_in = 0.0
        self.current_out = 0.0
        self.dragging = False

        self.set_content_width(250)
        self.set_content_height(250)
        self.set_draw_func(self.on_draw)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    def eval_curve(self, t_in):
        if not self.config["pen"].get("pressure_enabled", True):
            return 1.0 if t_in > 0 else 0.0
        c = self.config["pen"]["pressure_curve"]
        p0 = float(c.get("p0", 0.0))
        p1x = float(c.get("p1x", 0.5))
        p1y = float(c.get("p1y", 0.5))
        p2 = float(c.get("p2", 1.0))

        if t_in <= p0:
            return 0.0
        u = min(max((t_in - p0) / max(1.0 - p0, 1e-5), 0.0), 1.0)
        a = 1.0 - 2.0 * p1x
        b = 2.0 * p1x
        if abs(a) < 1e-6:
            s = u / max(b, 1e-5)
        else:
            disc = max(b * b + 4.0 * a * u, 0.0)
            s = (-b + math.sqrt(disc)) / (2.0 * a)
        s = min(max(s, 0.0), 1.0)
        return min(max((2.0 * (1.0 - s) * s * p1y + s * s) * p2, 0.0), 1.0)

    def on_draw(self, area, cr, width, height):
        pad = 28.0
        gw = width - 2 * pad
        gh = height - 2 * pad

        # Fundo do gráfico
        cr.set_source_rgba(0.13, 0.15, 0.19, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # Grade
        cr.set_source_rgba(0.28, 0.32, 0.38, 0.6)
        cr.set_line_width(1.0)
        for i in range(5):
            x = pad + (gw * i) / 4.0
            y = pad + (gh * i) / 4.0
            cr.move_to(x, pad)
            cr.line_to(x, pad + gh)
            cr.move_to(pad, y)
            cr.line_to(pad + gw, y)
        cr.stroke()

        # Diagonal de referência
        cr.set_source_rgba(0.5, 0.55, 0.65, 0.35)
        cr.move_to(pad, pad + gh)
        cr.line_to(pad + gw, pad)
        cr.stroke()

        # Indicador de pressão em tempo real
        if self.current_in > 0.002:
            ix = pad + self.current_in * gw
            iy = pad + (1.0 - self.current_out) * gh
            cr.set_source_rgba(0.15, 0.80, 0.45, 0.25)
            cr.rectangle(pad, iy, ix - pad, pad + gh - iy)
            cr.fill()
            cr.set_source_rgba(0.20, 0.90, 0.50, 0.95)
            cr.arc(ix, iy, 5.0, 0, 2 * math.pi)
            cr.fill()

        # Desenha a curva Bézier
        cr.set_source_rgba(0.10, 0.55, 1.0, 1.0)
        cr.set_line_width(2.6)
        steps = 60
        for i in range(steps + 1):
            tx = i / float(steps)
            ty = self.eval_curve(tx)
            px = pad + tx * gw
            py = pad + (1.0 - ty) * gh
            if i == 0:
                cr.move_to(px, py)
            else:
                cr.line_to(px, py)
        cr.stroke()

        # Ponto de controle (P1X, P1Y)
        c = self.config["pen"]["pressure_curve"]
        p0 = float(c.get("p0", 0.0))
        p1x = float(c.get("p1x", 0.5))
        p1y = float(c.get("p1y", 0.5))
        p2 = float(c.get("p2", 1.0))

        cx = pad + (p0 + p1x * (1.0 - p0)) * gw
        cy = pad + (1.0 - p1y * p2) * gh

        # Linhas guia até o ponto de controle
        cr.set_source_rgba(1.0, 0.65, 0.15, 0.5)
        cr.set_line_width(1.2)
        cr.move_to(pad + p0 * gw, pad + gh)
        cr.line_to(cx, cy)
        cr.line_to(pad + gw, pad + (1.0 - p2) * gh)
        cr.stroke()

        # Bolinha arrastável do ponto de controle
        cr.set_source_rgba(1.0, 0.60, 0.10, 1.0)
        cr.arc(cx, cy, 6.5, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        cr.set_line_width(1.8)
        cr.arc(cx, cy, 6.5, 0, 2 * math.pi)
        cr.stroke()

        # Legendas dos eixos
        cr.set_source_rgba(0.80, 0.84, 0.90, 0.9)
        cr.set_font_size(10)
        cr.move_to(pad, height - 8)
        cr.show_text(f"Entrada (P0={int(p0*100)}%)  P1=({int(p1x*100)}%, {int(p1y*100)}%)")
        cr.move_to(pad + 4, pad - 8)
        cr.show_text(f"Saída (Máx={int(p2*100)}%)")

    def update_point_from_xy(self, x, y):
        pad = 28.0
        gw = max(1.0, self.get_width() - 2 * pad)
        gh = max(1.0, self.get_height() - 2 * pad)
        c = self.config["pen"]["pressure_curve"]
        p0 = float(c.get("p0", 0.0))
        p2 = max(0.1, float(c.get("p2", 1.0)))

        norm_x = (x - pad) / gw
        norm_y = 1.0 - (y - pad) / gh

        p1x = (norm_x - p0) / max(1.0 - p0, 0.1)
        p1y = norm_y / p2
        c["p1x"] = round(min(max(p1x, 0.05), 0.95), 3)
        c["p1y"] = round(min(max(p1y, 0.05), 0.95), 3)
        self.queue_draw()

    def on_drag_begin(self, gesture, start_x, start_y):
        self.dragging = True
        self.update_point_from_xy(start_x, start_y)
        self.on_change_cb()

    def on_drag_update(self, gesture, offset_x, offset_y):
        if not self.dragging:
            return
        ok, sx, sy = gesture.get_start_point()
        if ok:
            self.update_point_from_xy(sx + offset_x, sy + offset_y)
            self.on_change_cb()

    def on_drag_end(self, gesture, offset_x, offset_y):
        if self.dragging:
            self.dragging = False
            self.on_change_cb()


class PenDrawTestWidget(Gtk.DrawingArea):
    """
    Área de Teste de Traço e Pressão (CPenDrawWidget do driver original).
    Combina leitura direta de pressão da telemetria IPC + Gtk.GestureStylus do Wayland,
    sem valores fixos artificiais, variando a espessura de 0.6 px até 24.0 px.
    """
    def __init__(self, get_pressure_state_cb):
        super().__init__()
        self.get_pressure_state_cb = get_pressure_state_cb
        self.strokes = []
        self.current_stroke = []
        self.last_raw_ratio = 0.0
        self.last_out_ratio = 0.0
        self.set_content_width(290)
        self.set_content_height(245)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        # Gesto nativo de Stylus (Wayland / libinput)
        stylus = Gtk.GestureStylus.new()
        stylus.connect("down", self.on_stylus_down)
        stylus.connect("motion", self.on_stylus_motion)
        stylus.connect("up", self.on_stylus_up)
        self.add_controller(stylus)

        # Fallback para arraste caso o compositor entregue como ponteiro
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    def clear(self):
        self.strokes.clear()
        self.current_stroke.clear()
        self.queue_draw()

    def _resolve_pressure(self, stylus_gesture=None):
        raw_r, out_r, in_prox = self.get_pressure_state_cb()
        if in_prox or raw_r > 0.0 or out_r > 0.0:
            self.last_raw_ratio = raw_r
            self.last_out_ratio = out_r
            return out_r

        if stylus_gesture is not None:
            ok, axis_p = stylus_gesture.get_axis(Gdk.AxisUse.PRESSURE)
            if ok and axis_p is not None and axis_p > 0.0:
                self.last_raw_ratio = axis_p
                self.last_out_ratio = axis_p
                return axis_p

        return out_r

    def on_stylus_down(self, gesture, x, y):
        p = self._resolve_pressure(gesture)
        if p > 0.001:
            self.current_stroke = [(x, y, p)]
        else:
            self.current_stroke = []
        self.queue_draw()

    def on_stylus_motion(self, gesture, x, y):
        p = self._resolve_pressure(gesture)
        if p > 0.001:
            self.current_stroke.append((x, y, p))
            self.queue_draw()

    def on_stylus_up(self, gesture, x, y):
        if self.current_stroke:
            self.strokes.append(self.current_stroke)
            self.current_stroke = []
            self.queue_draw()

    def on_drag_begin(self, gesture, x, y):
        if self.current_stroke:
            return
        p = self._resolve_pressure(None)
        if p > 0.001:
            self.current_stroke = [(x, y, p)]
            self.queue_draw()

    def on_drag_update(self, gesture, ox, oy):
        ok, sx, sy = gesture.get_start_point()
        if ok:
            p = self._resolve_pressure(None)
            if p > 0.001:
                self.current_stroke.append((sx + ox, sy + oy, p))
                self.queue_draw()

    def on_drag_end(self, gesture, ox, oy):
        if self.current_stroke:
            self.strokes.append(self.current_stroke)
            self.current_stroke = []
            self.queue_draw()

    def on_draw(self, area, cr, width, height):
        cr.set_source_rgba(0.97, 0.98, 0.99, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        cr.set_source_rgba(0.75, 0.78, 0.82, 1.0)
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()

        if not self.strokes and not self.current_stroke:
            cr.set_source_rgba(0.45, 0.50, 0.60, 0.85)
            cr.set_font_size(12)
            cr.move_to(20, height / 2.0)
            cr.show_text("Risque aqui com a caneta variando a força")

        cr.set_line_cap(1)  # ROUND
        cr.set_line_join(1)
        all_strokes = self.strokes + ([self.current_stroke] if self.current_stroke else [])
        for stroke in all_strokes:
            if len(stroke) == 1:
                x0, y0, p0 = stroke[0]
                r = max(0.4, p0 * 12.0)
                cr.set_source_rgba(0.02, 0.28, 0.85, min(1.0, 0.35 + p0 * 0.65))
                cr.arc(x0, y0, r, 0, 2 * math.pi)
                cr.fill()
            for i in range(1, len(stroke)):
                x0, y0, p0 = stroke[i - 1]
                x1, y1, p1 = stroke[i]
                p_avg = (p0 + p1) * 0.5
                # Espessura varia de 0.6 px (leve) a 24.0 px (pressão máxima)
                w = 0.6 + (p_avg ** 1.15) * 23.4
                alpha = min(1.0, 0.30 + p_avg * 0.70)
                cr.set_source_rgba(0.02, 0.28, 0.85, alpha)
                cr.set_line_width(w)
                cr.move_to(x0, y0)
                cr.line_to(x1, y1)
                cr.stroke()

        # HUD superior mostrando espessura e resposta da curva em tempo real
        raw_r, out_r, _ = self.get_pressure_state_cb()
        cur_w = 0.6 + (out_r ** 1.15) * 23.4 if out_r > 0 else 0.0
        cr.set_source_rgba(0.12, 0.16, 0.22, 0.85)
        cr.rectangle(6, 6, width - 12, 22)
        cr.fill()
        cr.set_source_rgba(0.95, 0.97, 1.0, 1.0)
        cr.select_font_face("Monospace", 0, 0)
        cr.set_font_size(10.5)
        cr.move_to(12, 21)
        cr.show_text(
            f"Força: {int(round(raw_r*100)):3d}% -> Curva: {int(round(out_r*100)):3d}% (Traço: {cur_w:4.1f}px)"
        )


class FloatingShortcutPadWindow(Gtk.Window):
    """Painel de Teclas de Atalho Flutuante na Tela (virtualKeyWindow do driver original)."""
    def __init__(self, parent, config, trigger_action_cb):
        super().__init__(title="Atalhos Ugee M708", transient_for=parent)
        self.set_default_size(270, 150)
        self.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)

        lbl = Gtk.Label(label="<b>Teclas Flutuantes M708</b>", use_markup=True)
        box.append(lbl)

        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        grid.set_row_homogeneous(True)
        grid.set_column_homogeneous(True)

        keys = config["express_keys"]["keys"]
        for i in range(8):
            k_info = keys[i] if i < len(keys) else {"label": f"K{i+1}"}
            btn = Gtk.Button(label=f"K{i+1}: {k_info.get('label', '')[:14]}")
            btn.connect("clicked", lambda _b, idx=i: trigger_action_cb(idx))
            grid.attach(btn, i % 2, i // 2, 1, 1)

        box.append(grid)
        self.set_child(box)


class UgeeM708Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Configurações Ugee M708")
        self.set_default_size(960, 660)
        self.set_icon_name("ugee-m708")

        self.config = load_config()
        self.updating_ui = False
        self.telemetry = {
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
        self.ipc_sock = None
        self.ipc_buf = ""
        self.floating_pad = None

        self.build_ui()
        self.connect_ipc()
        # Polling de 15ms (~66 Hz) para resposta imediata de pressão e coordenadas
        GLib.timeout_add(15, self.poll_ipc)

    def build_ui(self):
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        self.status_badge = Gtk.Label(label="● Verificando...")
        self.status_badge.set_margin_start(10)
        header.pack_start(self.status_badge)

        reset_btn = Gtk.Button(label="Restaurar Padrões")
        reset_btn.connect("clicked", self.on_reset_defaults)
        header.pack_end(reset_btn)

        root_box.append(header)

        self.stack.add_titled_with_icon(
            self.build_workarea_tab(), "workarea", "Área de Trabalho", "video-display-symbolic"
        )
        self.stack.add_titled_with_icon(
            self.build_pen_tab(), "pen", "Caneta e Pressão", "document-edit-symbolic"
        )
        self.stack.add_titled_with_icon(
            self.build_express_keys_tab(), "keys", "Teclas de Atalho (K1–K8)", "input-keyboard-symbolic"
        )
        self.stack.add_titled_with_icon(
            self.build_diagnostics_tab(), "diag", "Diagnóstico e Perfis", "utilities-system-monitor-symbolic"
        )

        root_box.append(self.stack)
        self.set_content(root_box)

    # =========================================================================
    # ABA 1: ÁREA DE TRABALHO
    # =========================================================================
    def build_workarea_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(18)
        box.set_margin_end(18)

        # Coluna Esquerda: Visualização interativa da M708
        left_card = Gtk.Frame(label="1. Área Ativa na Mesa Ugee M708 (Arraste cantos/bordas)")
        left_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left_vbox.set_margin_top(8)
        left_vbox.set_margin_bottom(8)
        left_vbox.set_margin_start(10)
        left_vbox.set_margin_end(10)

        self.tablet_area_widget = TabletAreaWidget(self.config, self.on_area_widget_changed)
        left_vbox.append(self.tablet_area_widget)

        # Sliders numéricos para Largura % e Altura % da mesa
        size_grid = Gtk.Grid(row_spacing=4, column_spacing=8)
        size_grid.attach(Gtk.Label(label="Largura Mesa (%):", xalign=0), 0, 0, 1, 1)
        self.scale_tab_w = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 15, 100, 1)
        self.scale_tab_w.set_hexpand(True)
        ta = self.config["workarea"]["tablet_area"]
        self.scale_tab_w.set_value(round((ta["x_max"] - ta["x_min"]) * 100))
        self.scale_tab_w.connect("value-changed", self.on_tablet_size_sliders_changed)
        size_grid.attach(self.scale_tab_w, 1, 0, 1, 1)

        size_grid.attach(Gtk.Label(label="Altura Mesa (%):", xalign=0), 0, 1, 1, 1)
        self.scale_tab_h = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 15, 100, 1)
        self.scale_tab_h.set_hexpand(True)
        self.scale_tab_h.set_value(round((ta["y_max"] - ta["y_min"]) * 100))
        self.scale_tab_h.connect("value-changed", self.on_tablet_size_sliders_changed)
        size_grid.attach(self.scale_tab_h, 1, 1, 1, 1)
        left_vbox.append(size_grid)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for lbl, cb in [
            ("Mesa 100%", self.on_full_tablet_area),
            ("Proporção 16:9", self.on_aspect_ratio_area),
            ("Centralizar 50%", self.on_half_tablet_area),
        ]:
            b = Gtk.Button(label=lbl)
            b.set_hexpand(True)
            b.connect("clicked", cb)
            btn_row.append(b)
        left_vbox.append(btn_row)

        left_card.set_child(left_vbox)
        box.append(left_card)

        # Coluna Direita: Área da Tela + Orientação + Modo de Precisão
        right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right_vbox.set_hexpand(True)

        screen_card = Gtk.Frame(label="2. Região de Atuação na Tela / Monitor (Arraste ou escolha preset)")
        screen_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        screen_box.set_margin_top(8)
        screen_box.set_margin_bottom(8)
        screen_box.set_margin_start(10)
        screen_box.set_margin_end(10)

        self.screen_area_widget = ScreenAreaWidget(self.config, self.on_area_widget_changed)
        screen_box.append(self.screen_area_widget)

        s_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for lbl, rect in [
            ("Tela Inteira (100%)", (0.0, 0.0, 1.0, 1.0)),
            ("Metade Esquerda", (0.0, 0.0, 0.5, 1.0)),
            ("Metade Direita", (0.5, 0.0, 1.0, 1.0)),
            ("Centro (60%)", (0.2, 0.2, 0.8, 0.8)),
        ]:
            sb = Gtk.Button(label=lbl)
            sb.set_hexpand(True)
            sb.connect("clicked", self.on_screen_preset, rect)
            s_btn_row.append(sb)
        screen_box.append(s_btn_row)
        screen_card.set_child(screen_box)
        right_vbox.append(screen_card)

        rot_group = Adw.PreferencesGroup(title="Orientação, Modo Canhoto e Precisão")
        rot_row = Adw.ActionRow(title="Ângulo de Rotação da Mesa")
        self.rot_combo = Gtk.DropDown.new_from_strings([
            "0° — Padrão (Destro, botões à esquerda)",
            "90° — Vertical (Botões no topo)",
            "180° — Modo Canhoto (Botões à direita)",
            "270° — Vertical Invertido (Botões na base)",
        ])
        rot_map = {0: 0, 90: 1, 180: 2, 270: 3}
        cur_rot = int(self.config["workarea"].get("rotation", 0))
        self.rot_combo.set_selected(rot_map.get(cur_rot, 0))
        self.rot_combo.set_valign(Gtk.Align.CENTER)
        self.rot_combo.connect("notify::selected", self.on_rotation_changed)
        rot_row.add_suffix(self.rot_combo)
        rot_group.add(rot_row)

        prec_row = Adw.ActionRow(
            title="Modo de Precisão (JingXiMode)",
            subtitle="Concentra toda a mesa na região central da tela para traços detalhados."
        )
        self.prec_switch = Gtk.Switch()
        self.prec_switch.set_active(bool(self.config["workarea"].get("precision_mode", False)))
        self.prec_switch.set_valign(Gtk.Align.CENTER)
        self.prec_switch.connect("notify::active", self.on_precision_toggled)
        prec_row.add_suffix(self.prec_switch)
        rot_group.add(prec_row)

        gnome_row = Adw.ActionRow(
            title="Vincular a um Monitor Específico (GNOME Wayland)",
            subtitle="Abre o painel Wacom do GNOME caso você use múltiplos monitores."
        )
        btn_gnome = Gtk.Button(label="Painel GNOME")
        btn_gnome.set_valign(Gtk.Align.CENTER)
        btn_gnome.connect("clicked", lambda _b: subprocess.Popen(["gnome-control-center", "wacom"]))
        gnome_row.add_suffix(btn_gnome)
        rot_group.add(gnome_row)

        right_vbox.append(rot_group)
        box.append(right_vbox)
        return box

    # =========================================================================
    # ABA 2: CANETA E PRESSÃO
    # =========================================================================
    def build_pen_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        # 1. Painel Esquerdo: Ilustração da Caneta e Botões
        pen_frame = Gtk.Frame(label="Botões da Caneta")
        pen_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        pen_vbox.set_margin_top(10)
        pen_vbox.set_margin_bottom(10)
        pen_vbox.set_margin_start(12)
        pen_vbox.set_margin_end(12)

        pen_img_path = find_asset("pen.png")
        if pen_img_path:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(pen_img_path, 65, 175, True)
                pic = Gtk.Picture.new_for_pixbuf(pb)
                pic.set_size_request(65, 175)
                pen_vbox.append(pic)
            except Exception:
                pass

        lbl_b2 = Gtk.Label(label="<b>Botão Superior (UpKey):</b>", use_markup=True, xalign=0)
        pen_vbox.append(lbl_b2)
        self.combo_pen_b2 = Gtk.DropDown.new_from_strings([x[0] for x in PEN_BTN_OPTIONS])
        b2_cur = self.config["pen"].get("btn2_action", "BTN_STYLUS2")
        self.combo_pen_b2.set_selected(next((i for i, v in enumerate(PEN_BTN_OPTIONS) if v[1] == b2_cur), 1))
        self.combo_pen_b2.connect("notify::selected", self.on_pen_buttons_changed)
        pen_vbox.append(self.combo_pen_b2)

        lbl_b1 = Gtk.Label(label="<b>Botão Inferior (DownKey):</b>", use_markup=True, xalign=0)
        pen_vbox.append(lbl_b1)
        self.combo_pen_b1 = Gtk.DropDown.new_from_strings([x[0] for x in PEN_BTN_OPTIONS])
        b1_cur = self.config["pen"].get("btn1_action", "BTN_STYLUS")
        self.combo_pen_b1.set_selected(next((i for i, v in enumerate(PEN_BTN_OPTIONS) if v[1] == b1_cur), 0))
        self.combo_pen_b1.connect("notify::selected", self.on_pen_buttons_changed)
        pen_vbox.append(self.combo_pen_b1)

        self.chk_pressure = Gtk.CheckButton(label="Sensibilidade à Pressão Ativa")
        self.chk_pressure.set_active(bool(self.config["pen"].get("pressure_enabled", True)))
        self.chk_pressure.connect("toggled", self.on_pen_toggles_changed)
        pen_vbox.append(self.chk_pressure)

        self.chk_tilt = Gtk.CheckButton(label="Inclinação (Tilt X/Y) Ativa")
        self.chk_tilt.set_active(bool(self.config["pen"].get("tilt_enabled", True)))
        self.chk_tilt.connect("toggled", self.on_pen_toggles_changed)
        pen_vbox.append(self.chk_tilt)

        pen_frame.set_child(pen_vbox)
        box.append(pen_frame)

        # 2. Painel Central: Curva Bézier (P0, P1X, P1Y, P2)
        curve_frame = Gtk.Frame(label="Curva de Sensibilidade à Pressão")
        curve_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        curve_vbox.set_margin_top(10)
        curve_vbox.set_margin_bottom(10)
        curve_vbox.set_margin_start(12)
        curve_vbox.set_margin_end(12)

        self.curve_widget = PressureCurveWidget(self.config, self.save_and_notify)
        curve_vbox.append(self.curve_widget)

        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for name, p1x, p1y in [
            ("Muito Macia", 0.15, 0.85),
            ("Macia", 0.30, 0.70),
            ("Linear", 0.50, 0.50),
            ("Firme", 0.70, 0.30),
            ("Muito Firme", 0.85, 0.15),
        ]:
            b = Gtk.Button(label=name)
            b.set_hexpand(True)
            b.connect("clicked", self.on_curve_preset, p1x, p1y)
            preset_row.append(b)
        curve_vbox.append(preset_row)

        p0_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        p0_box.append(Gtk.Label(label="P0 (Força Mín):", xalign=0))
        self.scale_p0 = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 0.35, 0.01)
        self.scale_p0.set_hexpand(True)
        self.scale_p0.set_value(float(self.config["pen"]["pressure_curve"].get("p0", 0.0)))
        self.scale_p0.connect("value-changed", self.on_p0_p2_changed)
        p0_box.append(self.scale_p0)
        curve_vbox.append(p0_box)

        p2_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        p2_box.append(Gtk.Label(label="P2 (Teto Máx):", xalign=0))
        self.scale_p2 = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.50, 1.0, 0.01)
        self.scale_p2.set_hexpand(True)
        self.scale_p2.set_value(float(self.config["pen"]["pressure_curve"].get("p2", 1.0)))
        self.scale_p2.connect("value-changed", self.on_p0_p2_changed)
        p2_box.append(self.scale_p2)
        curve_vbox.append(p2_box)

        curve_frame.set_child(curve_vbox)
        box.append(curve_frame)

        # 3. Painel Direito: Medidor ao Vivo + Quadro de Teste de Desenho
        test_frame = Gtk.Frame(label="Teste de Pressão em Tempo Real")
        test_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        test_vbox.set_margin_top(10)
        test_vbox.set_margin_bottom(10)
        test_vbox.set_margin_start(12)
        test_vbox.set_margin_end(12)
        test_vbox.set_hexpand(True)

        self.lbl_live_pressure = Gtk.Label(
            label="<tt>Bruto:    0 (  0%) | Curva:    0/8191 (  0%)</tt>",
            use_markup=True,
            xalign=0,
        )
        # Trava a largura do label para impedir que o container redimensione ao mudar os dígitos
        self.lbl_live_pressure.set_width_chars(44)
        self.lbl_live_pressure.set_max_width_chars(44)
        self.lbl_live_pressure.set_ellipsize(3)  # Pango.EllipsizeMode.END
        test_vbox.append(self.lbl_live_pressure)

        self.bar_pressure = Gtk.ProgressBar()
        self.bar_pressure.set_fraction(0.0)
        test_vbox.append(self.bar_pressure)

        self.draw_test = PenDrawTestWidget(self.get_live_pressure_state)
        test_vbox.append(self.draw_test)

        btn_clear = Gtk.Button(label="Limpar Quadro de Teste")
        btn_clear.connect("clicked", lambda _b: self.draw_test.clear())
        test_vbox.append(btn_clear)

        test_frame.set_child(test_vbox)
        box.append(test_frame)
        return box

    # =========================================================================
    # ABA 3: TECLAS DE ATALHO (EXPRESS KEYS K1–K8)
    # =========================================================================
    def build_express_keys_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(14)
        vbox.set_margin_bottom(14)
        vbox.set_margin_start(18)
        vbox.set_margin_end(18)

        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.chk_ek_enabled = Gtk.CheckButton(label="Ativar Teclas de Atalho Físicas (K1–K8)")
        self.chk_ek_enabled.set_active(bool(self.config["express_keys"].get("enabled", True)))
        self.chk_ek_enabled.connect("toggled", self.on_ek_header_toggled)
        top_bar.append(self.chk_ek_enabled)

        self.chk_osd = Gtk.CheckButton(label="Exibir Notificação na Tela (OSD) ao Pressionar")
        self.chk_osd.set_active(bool(self.config["express_keys"].get("osd_notifications", True)))
        self.chk_osd.connect("toggled", self.on_ek_header_toggled)
        top_bar.append(self.chk_osd)

        btn_float = Gtk.Button(label="Abrir Painel de Atalhos Flutuante")
        btn_float.connect("clicked", self.on_open_floating_pad)
        top_bar.append(btn_float)
        vbox.append(top_bar)

        leds_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        leds_box.append(Gtk.Label(label="<b>Status Físico ao Vivo:</b>", use_markup=True))
        self.key_leds = []
        for i in range(8):
            pill = Gtk.Label(label=f" K{i+1} ")
            pill.set_opacity(0.35)
            leds_box.append(pill)
            self.key_leds.append(pill)
        vbox.append(leds_box)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.key_rows = []
        keys_cfg = self.config["express_keys"]["keys"]
        preset_names = [p[0] for p in PRESET_SHORTCUTS]

        for i in range(8):
            k_data = keys_cfg[i] if i < len(keys_cfg) else {
                "id": f"K{i+1}", "label": f"K{i+1}", "type": "shortcut", "value": ""
            }
            row_frame = Gtk.Frame()
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_top(6)
            row.set_margin_bottom(6)
            row.set_margin_start(10)
            row.set_margin_end(10)

            badge = Gtk.Label(label=f"<b>Tecla K{i+1}</b>", use_markup=True)
            badge.set_size_request(75, -1)
            row.append(badge)

            combo = Gtk.DropDown.new_from_strings(preset_names)
            sel_idx = self._find_preset_index(k_data)
            combo.set_selected(sel_idx)
            row.append(combo)

            entry_val = Gtk.Entry()
            entry_val.set_placeholder_text("Ex: KEY_LEFTCTRL+KEY_Z ou comando")
            entry_val.set_text(k_data.get("value", ""))
            entry_val.set_hexpand(True)
            row.append(entry_val)

            entry_lbl = Gtk.Entry()
            entry_lbl.set_placeholder_text("Nome no OSD")
            entry_lbl.set_text(k_data.get("label", f"K{i+1}"))
            entry_lbl.set_size_request(160, -1)
            row.append(entry_lbl)

            combo.connect("notify::selected", self.on_key_preset_selected, i, entry_val, entry_lbl)
            entry_val.connect("changed", self.on_key_entry_changed, i, entry_val, entry_lbl)
            entry_lbl.connect("changed", self.on_key_entry_changed, i, entry_val, entry_lbl)

            row_frame.set_child(row)
            list_box.append(row_frame)
            self.key_rows.append((combo, entry_val, entry_lbl))

        scrolled.set_child(list_box)
        vbox.append(scrolled)
        return vbox

    def _find_preset_index(self, k_data):
        custom_idx = next((i for i, p in enumerate(PRESET_SHORTCUTS) if p[1] == "custom_shortcut"), 0)
        for p_idx, (_p_label, p_type, p_val) in enumerate(PRESET_SHORTCUTS):
            if p_type == "custom_shortcut":
                continue
            if k_data.get("type") == p_type and (p_val == k_data.get("value") or not p_val):
                return p_idx
        return custom_idx

    # =========================================================================
    # ABA 4: DIAGNÓSTICO E PERFIS
    # =========================================================================
    def build_diagnostics_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_margin_top(18)
        vbox.set_margin_bottom(18)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)

        diag_group = Adw.PreferencesGroup(
            title="Diagnóstico de Hardware e Telemetria em Tempo Real",
            description="Leituras diretas recebidas do daemon ugee_m708_driver.py via socket IPC."
        )

        self.row_dev_status = Adw.ActionRow(title="Dispositivo / Porta HID", subtitle="Aguardando...")
        diag_group.add(self.row_dev_status)

        self.row_coords = Adw.ActionRow(title="Coordenadas (X, Y)", subtitle="X: 0  |  Y: 0")
        diag_group.add(self.row_coords)

        self.row_press = Adw.ActionRow(
            title="Sensor de Pressão e Inclinação",
            subtitle="Bruta: 0  |  Processada: 0  |  Tilt X: 0°  |  Tilt Y: 0°"
        )
        diag_group.add(self.row_press)

        self.row_btns = Adw.ActionRow(
            title="Estado das Ferramentas e Botões",
            subtitle="Ferramenta: Caneta  |  Stylus: 0/0  |  Express Keys Mask: 0b00000000"
        )
        diag_group.add(self.row_btns)
        vbox.append(diag_group)

        profile_group = Adw.PreferencesGroup(
            title="Gerenciamento de Configuração e Serviço",
            description=f"Arquivo de configuração atual: {CONFIG_PATH}"
        )
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_margin_top(8)

        btn_export = Gtk.Button(label="Exportar Perfil (.json)")
        btn_export.connect("clicked", self.on_export_profile)
        btn_box.append(btn_export)

        btn_import = Gtk.Button(label="Importar Perfil (.json)")
        btn_import.connect("clicked", self.on_import_profile)
        btn_box.append(btn_import)

        btn_restart = Gtk.Button(label="Reiniciar Serviço Systemd")
        btn_restart.connect("clicked", self.on_restart_service)
        btn_box.append(btn_restart)

        vbox.append(profile_group)
        vbox.append(btn_box)
        return vbox

    # =========================================================================
    # CALLBACKS E SINCRONIZAÇÃO IPC
    # =========================================================================
    def connect_ipc(self):
        if self.ipc_sock is not None:
            return
        sp = get_socket_path()
        if not os.path.exists(sp):
            return
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.setblocking(False)
            s.connect(sp)
            self.ipc_sock = s
            self.send_ipc({"cmd": "get_state"})
        except Exception:
            self.ipc_sock = None

    def send_ipc(self, obj):
        if self.ipc_sock is None:
            self.connect_ipc()
        if self.ipc_sock is None:
            return
        try:
            self.ipc_sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        except Exception:
            try:
                self.ipc_sock.close()
            except Exception:
                pass
            self.ipc_sock = None

    def poll_ipc(self):
        if self.ipc_sock is None:
            self.connect_ipc()
            if self.ipc_sock is None:
                self.status_badge.set_markup("<span foreground='#e05555'>● Serviço Offline</span>")
                return True

        updated = False
        try:
            while True:
                chunk = self.ipc_sock.recv(8192)
                if not chunk:
                    self.ipc_sock.close()
                    self.ipc_sock = None
                    break
                self.ipc_buf += chunk.decode("utf-8", errors="ignore")
                while "\n" in self.ipc_buf:
                    line, self.ipc_buf = self.ipc_buf.split("\n", 1)
                    if line.strip():
                        msg = json.loads(line)
                        if msg.get("event") == "telemetry":
                            self.telemetry.update(msg)
                            updated = True
        except BlockingIOError:
            pass
        except Exception:
            if self.ipc_sock:
                try:
                    self.ipc_sock.close()
                except Exception:
                    pass
                self.ipc_sock = None

        if updated:
            self.update_telemetry_ui()
        return True

    def update_telemetry_ui(self):
        t = self.telemetry
        if t.get("connected"):
            pid = t.get("pid", 0)
            hidraw = t.get("hidraw", "")
            self.status_badge.set_markup(
                f"<span foreground='#35d073'>● Ugee M708 Conectada (0x28BD:0x{pid:04X})</span>"
            )
            self.row_dev_status.set_subtitle(f"Conectada em {hidraw} — VID 0x28BD / PID 0x{pid:04X}")
        else:
            self.status_badge.set_markup("<span foreground='#f0a030'>● Mesa Desconectada</span>")
            self.row_dev_status.set_subtitle("Mesa desconectada (aguardando conexão USB)")

        max_p = max(1, int(t.get("max_pressure", 8191)))
        raw_p = int(t.get("raw_pressure", 0))
        raw_ratio = min(max(raw_p / float(max_p), 0.0), 1.0)
        # Avalia localmente a curva atual da GUI sobre a pressão bruta para feedback instantâneo
        out_ratio = self.curve_widget.eval_curve(raw_ratio) if raw_p > 0 else 0.0
        cur_p = int(round(out_ratio * max_p))
        raw_pct = int(round(raw_ratio * 100))
        pct = int(round(out_ratio * 100))

        self.lbl_live_pressure.set_markup(
            f"<tt>Bruto: {raw_p:4d} ({raw_pct:3d}%) | Curva: {cur_p:4d}/{max_p:4d} ({pct:3d}%)</tt>"
        )
        self.bar_pressure.set_fraction(out_ratio)

        self.curve_widget.current_in = raw_ratio
        self.curve_widget.current_out = out_ratio
        self.curve_widget.queue_draw()
        self.draw_test.queue_draw()

        # Atualiza ponto verde ao vivo da caneta na pré-visualização da mesa (Aba 1)
        if t.get("in_proximity"):
            self.tablet_area_widget.live_pen = (
                float(t.get("norm_x", 0.0)),
                float(t.get("norm_y", 0.0)),
                bool(t.get("inside_area", True)),
            )
        else:
            self.tablet_area_widget.live_pen = None
        self.tablet_area_widget.queue_draw()

        self.row_coords.set_subtitle(
            f"X: {int(t.get('x', 0)):5d} / {int(t.get('max_x', 50800)):5d}   |   "
            f"Y: {int(t.get('y', 0)):5d} / {int(t.get('max_y', 30480)):5d}"
            f"   |   Dentro da Área Ativa: {'Sim' if t.get('inside_area', True) else 'Não'}"
        )
        self.row_press.set_subtitle(
            f"Sensor Bruto: {raw_p:4d}   |   Saída Curva: {cur_p:4d}   |   "
            f"Tilt X: {int(t.get('tilt_x', 0)):+3d}°   |   Tilt Y: {int(t.get('tilt_y', 0)):+3d}°"
        )
        tool_str = "Borracha (Eraser)" if t.get("eraser") else "Caneta (Pen)"
        bmask = int(t.get("buttons", 0))
        self.row_btns.set_subtitle(
            f"Ferramenta: {tool_str}   |   Botões Caneta: {t.get('pen_btn1', 0)}/{t.get('pen_btn2', 0)}   |   Teclas K1–K8: 0b{bmask:08b}"
        )

        for i, pill in enumerate(self.key_leds):
            if bmask & (1 << i):
                pill.set_markup(f"<span background='#0243FF' foreground='#FFFFFF'><b> K{i+1} </b></span>")
                pill.set_opacity(1.0)
            else:
                pill.set_text(f" K{i+1} ")
                pill.set_opacity(0.35)

    def get_live_pressure_state(self):
        max_p = max(1, int(self.telemetry.get("max_pressure", 8191)))
        raw_p = int(self.telemetry.get("raw_pressure", 0))
        raw_ratio = min(max(raw_p / float(max_p), 0.0), 1.0)
        out_ratio = self.curve_widget.eval_curve(raw_ratio) if raw_p > 0 else 0.0
        in_prox = bool(self.telemetry.get("in_proximity", False))
        return raw_ratio, out_ratio, in_prox

    def save_and_notify(self):
        if self.updating_ui:
            return
        save_config(self.config)
        self.send_ipc({"cmd": "reload_config"})

    def on_area_widget_changed(self, live_only=False):
        self.updating_ui = True
        ta = self.config["workarea"]["tablet_area"]
        self.scale_tab_w.set_value(round((ta["x_max"] - ta["x_min"]) * 100))
        self.scale_tab_h.set_value(round((ta["y_max"] - ta["y_min"]) * 100))
        self.updating_ui = False
        if not live_only:
            self.save_and_notify()

    def on_tablet_size_sliders_changed(self, _scale):
        if self.updating_ui:
            return
        w_ratio = min(max(self.scale_tab_w.get_value() / 100.0, 0.15), 1.0)
        h_ratio = min(max(self.scale_tab_h.get_value() / 100.0, 0.15), 1.0)
        ta = self.config["workarea"]["tablet_area"]
        cx = (ta["x_min"] + ta["x_max"]) * 0.5
        cy = (ta["y_min"] + ta["y_max"]) * 0.5
        xmin = min(max(cx - w_ratio * 0.5, 0.0), 1.0 - w_ratio)
        ymin = min(max(cy - h_ratio * 0.5, 0.0), 1.0 - h_ratio)
        ta["x_min"] = round(xmin, 3)
        ta["y_min"] = round(ymin, 3)
        ta["x_max"] = round(xmin + w_ratio, 3)
        ta["y_max"] = round(ymin + h_ratio, 3)
        self.tablet_area_widget.queue_draw()
        self.save_and_notify()

    def on_rotation_changed(self, combo, _pspec):
        if self.updating_ui:
            return
        angles = [0, 90, 180, 270]
        idx = combo.get_selected()
        if 0 <= idx < len(angles):
            self.config["workarea"]["rotation"] = angles[idx]
            self.tablet_area_widget.queue_draw()
            self.save_and_notify()

    def on_precision_toggled(self, sw, _pspec):
        if self.updating_ui:
            return
        self.config["workarea"]["precision_mode"] = sw.get_active()
        self.save_and_notify()

    def on_full_tablet_area(self, _btn):
        self.config["workarea"]["tablet_area"] = {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0}
        self.tablet_area_widget.queue_draw()
        self.on_area_widget_changed()

    def on_aspect_ratio_area(self, _btn):
        self.config["workarea"]["tablet_area"] = {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 0.9375}
        self.tablet_area_widget.queue_draw()
        self.on_area_widget_changed()

    def on_half_tablet_area(self, _btn):
        self.config["workarea"]["tablet_area"] = {"x_min": 0.25, "y_min": 0.25, "x_max": 0.75, "y_max": 0.75}
        self.tablet_area_widget.queue_draw()
        self.on_area_widget_changed()

    def on_screen_preset(self, _btn, rect):
        x0, y0, x1, y1 = rect
        self.config["workarea"]["screen_area"] = {"x_min": x0, "y_min": y0, "x_max": x1, "y_max": y1}
        self.screen_area_widget.queue_draw()
        self.save_and_notify()

    def on_pen_buttons_changed(self, _combo, _pspec):
        if self.updating_ui:
            return
        i1 = self.combo_pen_b1.get_selected()
        i2 = self.combo_pen_b2.get_selected()
        if 0 <= i1 < len(PEN_BTN_OPTIONS):
            self.config["pen"]["btn1_action"] = PEN_BTN_OPTIONS[i1][1]
        if 0 <= i2 < len(PEN_BTN_OPTIONS):
            self.config["pen"]["btn2_action"] = PEN_BTN_OPTIONS[i2][1]
        self.save_and_notify()

    def on_pen_toggles_changed(self, _chk):
        if self.updating_ui:
            return
        self.config["pen"]["pressure_enabled"] = self.chk_pressure.get_active()
        self.config["pen"]["tilt_enabled"] = self.chk_tilt.get_active()
        self.curve_widget.queue_draw()
        self.save_and_notify()

    def on_curve_preset(self, _btn, p1x, p1y):
        c = self.config["pen"]["pressure_curve"]
        c["p0"] = 0.0
        c["p1x"] = p1x
        c["p1y"] = p1y
        c["p2"] = 1.0
        self.updating_ui = True
        self.scale_p0.set_value(0.0)
        self.scale_p2.set_value(1.0)
        self.updating_ui = False
        self.curve_widget.queue_draw()
        self.save_and_notify()

    def on_p0_p2_changed(self, _scale):
        if self.updating_ui:
            return
        c = self.config["pen"]["pressure_curve"]
        c["p0"] = round(self.scale_p0.get_value(), 3)
        c["p2"] = round(self.scale_p2.get_value(), 3)
        self.curve_widget.queue_draw()
        self.save_and_notify()

    def on_ek_header_toggled(self, _chk):
        if self.updating_ui:
            return
        self.config["express_keys"]["enabled"] = self.chk_ek_enabled.get_active()
        self.config["express_keys"]["osd_notifications"] = self.chk_osd.get_active()
        self.save_and_notify()

    def on_key_preset_selected(self, combo, _pspec, idx, entry_val, entry_lbl):
        if self.updating_ui:
            return
        sel = combo.get_selected()
        if 0 <= sel < len(PRESET_SHORTCUTS):
            p_label, p_type, p_val = PRESET_SHORTCUTS[sel]
            k_obj = self.config["express_keys"]["keys"][idx]
            k_obj["type"] = "shortcut" if p_type == "custom_shortcut" else p_type
            if p_type not in ("custom_shortcut", "run_cmd"):
                k_obj["value"] = p_val
                k_obj["label"] = p_label
                self.updating_ui = True
                entry_val.set_text(p_val)
                entry_lbl.set_text(p_label)
                self.updating_ui = False
            self.save_and_notify()

    def on_key_entry_changed(self, _entry, idx, entry_val, entry_lbl):
        if self.updating_ui:
            return
        k_obj = self.config["express_keys"]["keys"][idx]
        k_obj["value"] = entry_val.get_text().strip()
        k_obj["label"] = entry_lbl.get_text().strip() or f"K{idx+1}"
        self.save_and_notify()

    def on_open_floating_pad(self, _btn):
        if self.floating_pad is not None:
            self.floating_pad.close()
        self.floating_pad = FloatingShortcutPadWindow(self, self.config, self.trigger_floating_key)
        self.floating_pad.present()

    def trigger_floating_key(self, idx):
        # Envia o comando via IPC para o daemon disparar o atalho real no uinput
        self.send_ipc({"cmd": "trigger_key", "index": idx})
        keys = self.config["express_keys"]["keys"]
        if idx < len(keys) and keys[idx].get("type") == "precision_toggle":
            self.prec_switch.set_active(not self.prec_switch.get_active())

    def refresh_all_widgets_from_config(self):
        self.updating_ui = True
        rot_map = {0: 0, 90: 1, 180: 2, 270: 3}
        self.rot_combo.set_selected(rot_map.get(int(self.config["workarea"].get("rotation", 0)), 0))
        self.prec_switch.set_active(bool(self.config["workarea"].get("precision_mode", False)))

        ta = self.config["workarea"]["tablet_area"]
        self.scale_tab_w.set_value(round((ta["x_max"] - ta["x_min"]) * 100))
        self.scale_tab_h.set_value(round((ta["y_max"] - ta["y_min"]) * 100))

        self.tablet_area_widget.config = self.config
        self.tablet_area_widget.queue_draw()
        self.screen_area_widget.config = self.config
        self.screen_area_widget.queue_draw()

        b1_cur = self.config["pen"].get("btn1_action", "BTN_STYLUS")
        b2_cur = self.config["pen"].get("btn2_action", "BTN_STYLUS2")
        self.combo_pen_b1.set_selected(next((i for i, v in enumerate(PEN_BTN_OPTIONS) if v[1] == b1_cur), 0))
        self.combo_pen_b2.set_selected(next((i for i, v in enumerate(PEN_BTN_OPTIONS) if v[1] == b2_cur), 1))
        self.chk_pressure.set_active(bool(self.config["pen"].get("pressure_enabled", True)))
        self.chk_tilt.set_active(bool(self.config["pen"].get("tilt_enabled", True)))

        c = self.config["pen"]["pressure_curve"]
        self.scale_p0.set_value(float(c.get("p0", 0.0)))
        self.scale_p2.set_value(float(c.get("p2", 1.0)))
        self.curve_widget.config = self.config
        self.curve_widget.queue_draw()

        self.chk_ek_enabled.set_active(bool(self.config["express_keys"].get("enabled", True)))
        self.chk_osd.set_active(bool(self.config["express_keys"].get("osd_notifications", True)))
        keys_cfg = self.config["express_keys"]["keys"]
        for i, (combo, entry_val, entry_lbl) in enumerate(self.key_rows):
            if i < len(keys_cfg):
                k_data = keys_cfg[i]
                combo.set_selected(self._find_preset_index(k_data))
                entry_val.set_text(k_data.get("value", ""))
                entry_lbl.set_text(k_data.get("label", f"K{i+1}"))
        self.updating_ui = False

    def on_reset_defaults(self, _btn):
        self.config = get_default_config()
        save_config(self.config)
        self.refresh_all_widgets_from_config()
        self.send_ipc({"cmd": "reload_config"})

    def on_export_profile(self, _btn):
        export_path = os.path.expanduser("~/ugee_m708_profile_backup.json")
        try:
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            self.row_dev_status.set_subtitle(f"Perfil exportado com sucesso para {export_path}")
        except Exception as ex:
            self.row_dev_status.set_subtitle(f"Erro ao exportar: {ex}")

    def on_import_profile(self, _btn):
        import_path = os.path.expanduser("~/ugee_m708_profile_backup.json")
        if not os.path.exists(import_path):
            self.row_dev_status.set_subtitle(f"Arquivo {import_path} não encontrado.")
            return
        try:
            with open(import_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            save_config(data)
            self.config = load_config()
            self.refresh_all_widgets_from_config()
            self.send_ipc({"cmd": "reload_config"})
            self.row_dev_status.set_subtitle(f"Perfil importado com sucesso de {import_path}")
        except Exception as ex:
            self.row_dev_status.set_subtitle(f"Erro ao importar: {ex}")

    def on_restart_service(self, _btn):
        subprocess.Popen(["systemctl", "--user", "restart", "ugee-m708.service"])


class UgeeM708App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.ugee.m708.settings", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = UgeeM708Window(self)
        win.present()


def main():
    app = UgeeM708App()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
