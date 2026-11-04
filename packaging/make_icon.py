"""Draw the dctl app icon with pycairo (developer tool; the generated files are committed).

    python3 packaging/make_icon.py

Writes packaging/icon.png (1024 px), packaging/icons/<size>.png and packaging/dctl.ico.
"""
import io
import math
import os
import struct

import cairo

HERE = os.path.dirname(os.path.abspath(__file__))


def squircle(cr, x, y, s, n=5.0, steps=720):
    """Superellipse |x|^n + |y|^n = 1 (the soft 'app icon' shape)."""
    a = s / 2
    cx, cy = x + a, y + a
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        c, d = math.cos(t), math.sin(t)
        px = cx + a * math.copysign(abs(c) ** (2 / n), c)
        py = cy + a * math.copysign(abs(d) ** (2 / n), d)
        cr.move_to(px, py) if i == 0 else cr.line_to(px, py)
    cr.close_path()


def slab(cr, cx, cy, w, t, top, left, right):
    """One isometric slab: top rhombus centred at (cx, cy), thickness t."""
    h = w * 0.5
    T, R, B, L = (cx, cy - h / 2), (cx + w / 2, cy), (cx, cy + h / 2), (cx - w / 2, cy)
    # left face
    cr.move_to(*L); cr.line_to(*B); cr.line_to(B[0], B[1] + t); cr.line_to(L[0], L[1] + t); cr.close_path()
    cr.set_source(left); cr.fill()
    # right face
    cr.move_to(*B); cr.line_to(*R); cr.line_to(R[0], R[1] + t); cr.line_to(B[0], B[1] + t); cr.close_path()
    cr.set_source(right); cr.fill()
    # top face
    cr.move_to(*T); cr.line_to(*R); cr.line_to(*B); cr.line_to(*L); cr.close_path()
    cr.set_source(top); cr.fill()
    return T, R, B, L


def rgba(h, a=1.0):
    h = h.lstrip("#")
    return cairo.SolidPattern(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


def soft_shadow(S, pad, tile, dy, blur, alpha):
    """Blurred squircle shadow (numpy box blur x3 ~ gaussian)."""
    import numpy as np
    m = cairo.ImageSurface(cairo.FORMAT_A8, S, S)
    c = cairo.Context(m)
    c.scale(S / 1024, S / 1024)
    squircle(c, pad, pad + dy, tile)
    c.set_source_rgba(0, 0, 0, 1)
    c.fill()
    m.flush()
    st = m.get_stride()
    a = np.frombuffer(m.get_data(), np.uint8).reshape(S, st)[:, :S].astype(np.float32) / 255
    r = max(1, int(blur * S / 1024))
    for _ in range(3):
        for ax in (0, 1):
            cs = np.cumsum(np.pad(a, [(r + 1, r) if i == ax else (0, 0) for i in range(2)], mode="edge"), axis=ax)
            a = (np.take(cs, range(2 * r + 1, cs.shape[ax]), axis=ax) - np.take(cs, range(0, cs.shape[ax] - 2 * r - 1), axis=ax)) / (2 * r + 1)
    out = np.zeros((S, S, 4), np.uint8)
    out[..., 3] = np.clip(a * alpha * 255, 0, 255)
    out[..., 2] = out[..., 3] * 0.08          # premultiplied, very dark indigo
    out[..., 0] = out[..., 3] * 0.25
    return cairo.ImageSurface.create_for_data(bytearray(out.tobytes()), cairo.FORMAT_ARGB32, S, S, S * 4)


def draw(S):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
    cr = cairo.Context(surf)
    pad, tile = 80, 1024 - 160
    if S >= 64:
        cr.set_source_surface(soft_shadow(S, pad, tile, 22, 26, 0.45), 0, 0)
        cr.paint()
    cr.scale(S / 1024, S / 1024)

    # background gradient: violet -> blue -> cyan
    squircle(cr, pad, pad, tile)
    g = cairo.LinearGradient(pad, pad, pad + tile, pad + tile)
    g.add_color_stop_rgb(0.00, 0x8b / 255, 0x5c / 255, 0xf6 / 255)
    g.add_color_stop_rgb(0.48, 0x3b / 255, 0x6e / 255, 0xf0 / 255)
    g.add_color_stop_rgb(1.00, 0x06 / 255, 0xb6 / 255, 0xd4 / 255)
    cr.set_source(g)
    cr.fill_preserve()
    rg = cairo.RadialGradient(pad + tile * 0.2, pad + tile * 0.1, 0, pad + tile * 0.2, pad + tile * 0.1, tile * 0.95)
    rg.add_color_stop_rgba(0, 1, 1, 1, 0.30)
    rg.add_color_stop_rgba(0.6, 1, 1, 1, 0.04)
    rg.add_color_stop_rgba(1, 1, 1, 1, 0.0)
    cr.set_source(rg)
    cr.fill_preserve()
    cr.set_line_width(4)
    cr.set_source_rgba(1, 1, 1, 0.22)
    cr.stroke()

    # three opaque stacked slabs (bottom -> top) = a fleet of servers
    cx, w, t, gap, base = 512, 500, 58, 128, 648
    tones = [
        ("#c7d2fe", "#818cf8", "#60a5fa"),
        ("#e0e7ff", "#a5b4fc", "#7dd3fc"),
        (None, "#c4b5fd", "#a5f3fc"),
    ]
    for i, (top, left, right) in enumerate(tones):
        y = base - i * gap
        if top is None:
            tg = cairo.LinearGradient(cx - w / 2, y - w / 4, cx + w / 2, y + w / 4)
            tg.add_color_stop_rgb(0, 1, 1, 1)
            tg.add_color_stop_rgb(1, 0.91, 0.95, 1)
            top_p = tg
        else:
            top_p = rgba(top)
        T, R, B, L = slab(cr, cx, y, w, t, top_p, rgba(left), rgba(right))
        # LEDs on the front-right face, like a server rack
        for k, (pos, col) in enumerate(((0.58, (0x22, 0xe3, 0xa1)), (0.76, (0x22, 0xe3, 0xa1)))):
            lx = B[0] + (R[0] - B[0]) * pos
            ly = B[1] + (R[1] - B[1]) * pos + t * 0.5
            rr = 12
            glow = cairo.RadialGradient(lx, ly, 0, lx, ly, rr * 2.8)
            glow.add_color_stop_rgba(0, col[0] / 255, col[1] / 255, col[2] / 255, 0.55)
            glow.add_color_stop_rgba(1, col[0] / 255, col[1] / 255, col[2] / 255, 0)
            cr.set_source(glow); cr.arc(lx, ly, rr * 2.8, 0, 2 * math.pi); cr.fill()
            cr.set_source_rgb(col[0] / 255, col[1] / 255, col[2] / 255)
            cr.save(); cr.translate(lx, ly); cr.scale(1, 1.0); cr.arc(0, 0, rr, 0, 2 * math.pi); cr.restore(); cr.fill()
        # thin bright edge along the top face's front edges
        cr.set_line_width(3); cr.set_source_rgba(1, 1, 1, 0.7)
        cr.move_to(*L); cr.line_to(*B); cr.line_to(*R); cr.stroke()
    return surf


def png_bytes(size):
    buf = io.BytesIO()
    draw(size).write_to_png(buf)
    return buf.getvalue()


if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "icons"), exist_ok=True)
    draw(1024).write_to_png(os.path.join(HERE, "icon.png"))
    for s in (16, 24, 32, 48, 64, 128, 256, 512):
        draw(s).write_to_png(os.path.join(HERE, "icons", f"{s}.png"))
    sizes = [256, 128, 64, 48, 32, 24, 16]
    imgs = [png_bytes(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    off, dirs = 6 + 16 * len(sizes), b""
    for s, d in zip(sizes, imgs):
        dirs += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(d), off)
        off += len(d)
    open(os.path.join(HERE, "dctl.ico"), "wb").write(head + dirs + b"".join(imgs))
    print("wrote icon.png, icons/*.png, dctl.ico")
