"""Draw the dctl icon (no image libraries needed): writes icon.png (512px) and dctl.ico."""
import os, struct, zlib

HERE = os.path.dirname(os.path.abspath(__file__))


def render(n, ss=4):
    N = n * ss
    px = bytearray(N * N * 4)
    r = 0.22 * N                                     # corner radius of the tile
    bars = [(0.27, 0.37), (0.45, 0.55), (0.63, 0.73)]  # three "server" bars (y from..to)
    for y in range(N):
        for x in range(N):
            # rounded square mask
            cx = min(max(x, r), N - 1 - r); cy = min(max(y, r), N - 1 - r)
            if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                continue
            t = (x + y) / (2 * N)                    # diagonal gradient violet -> cyan
            c = (int(124 + (0 - 124) * t), int(92 + (200 - 92) * t), int(255 + (240 - 255) * t))
            fy, fx = y / N, x / N
            for (a, b) in bars:
                if a <= fy <= b and 0.22 <= fx <= 0.78:
                    rr = (b - a) / 2 * N; bx = min(max(x, 0.22 * N + rr), 0.78 * N - rr); by = (a + b) / 2 * N
                    if (x - bx) ** 2 + (y - by) ** 2 <= rr * rr:
                        c = (255, 255, 255)
                        if 0.68 <= fx <= 0.72 and abs(fy - (a + b) / 2) < 0.02:  # status LED
                            c = (34, 227, 161)
            i = (y * N + x) * 4
            px[i:i + 4] = bytes((*c, 255))
    out = bytearray()                                # downsample (anti-alias)
    for y in range(n):
        out.append(0)
        for x in range(n):
            acc = [0, 0, 0, 0]
            for dy in range(ss):
                for dx in range(ss):
                    i = ((y * ss + dy) * N + x * ss + dx) * 4
                    a = px[i + 3]
                    for k in range(3):
                        acc[k] += px[i + k] * a
                    acc[3] += a
            a = acc[3]
            out += bytes([acc[k] // a if a else 0 for k in range(3)] + [a // (ss * ss)])
    return out


def png(n):
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(render(n)), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    open(os.path.join(HERE, "icon.png"), "wb").write(png(512))
    sizes = [256, 64, 48, 32, 16]
    imgs = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes)); off = 6 + 16 * len(sizes); dirs = b""
    for s, d in zip(sizes, imgs):
        dirs += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(d), off); off += len(d)
    open(os.path.join(HERE, "dctl.ico"), "wb").write(head + dirs + b"".join(imgs))
    print("wrote icon.png and dctl.ico")
