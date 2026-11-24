"""Tiny dependency-free QR code generator (byte mode, error correction M, versions 1-6).

Enough for a website address of up to 106 characters. It exists so the project needs no
extra package just to show a QR code. Usage:

    from backend.qr import make_svg
    svg_text = make_svg("http://192.168.1.10:5000/")
"""

# (error-correction codewords per block, [(block count, data codewords per block), ...])
_EC_M = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
MAX_BYTES = 106


def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 2)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(data_bytes, version):
    ec_len, groups = _EC_M[version]
    capacity = sum(n * d for n, d in groups)
    bits = "0100" + format(len(data_bytes), "08b") + "".join(format(b, "08b") for b in data_bytes)
    bits += "0" * min(4, capacity * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    cw = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    while len(cw) < capacity:
        cw.append(pad[(len(cw) - len(bits) // 8) % 2])
    blocks, pos = [], 0
    for n, d in groups:
        for _ in range(n):
            blocks.append(cw[pos:pos + d])
            pos += d
    divisor = _rs_divisor(ec_len)
    ecs = [_rs_remainder(b, divisor) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_len):
        for e in ecs:
            out.append(e[i])
    return out


def _matrix(data_cw, version):
    size = 17 + 4 * version
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    pos = _ALIGN[version]
    for i, cx in enumerate(pos):
        for j, cy in enumerate(pos):
            if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        data = (0 << 3) | mask  # error correction level M has indicator bits 00
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        gb = lambda i: (bits >> i) & 1 == 1
        for i in range(0, 6):
            setf(8, i, gb(i))
        setf(8, 7, gb(6))
        setf(8, 8, gb(7))
        setf(7, 8, gb(8))
        for i in range(9, 15):
            setf(14 - i, 8, gb(i))
        for i in range(0, 8):
            setf(size - 1 - i, 8, gb(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, gb(i))
        setf(8, size - 8, True)

    draw_format(0)
    i = 0
    total = len(data_cw) * 8
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not fn[y][x] and i < total:
                    mod[y][x] = (data_cw[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2

    masks = [
        lambda x, y: (x + y) % 2 == 0,
        lambda x, y: y % 2 == 0,
        lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0,
        lambda x, y: (x // 3 + y // 2) % 2 == 0,
        lambda x, y: x * y % 2 + x * y % 3 == 0,
        lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
    ]
    best, best_mask, best_score = None, 0, None
    for m, fnc in enumerate(masks):
        trial = [row[:] for row in mod]
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and fnc(x, y):
                    trial[y][x] = not trial[y][x]
        score = _penalty(trial)
        if best_score is None or score < best_score:
            best, best_mask, best_score = trial, m, score
    mod = best
    draw_format(best_mask)
    return mod


def _penalty(m):
    size = len(m)
    score = 0
    lines = [row for row in m] + [[m[y][x] for y in range(size)] for x in range(size)]
    for line in lines:
        run = 1
        for i in range(1, size + 1):
            if i < size and line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        s = "".join("1" if v else "0" for v in line)
        for pat in ("10111010000", "00001011101"):
            start = 0
            while True:
                k = s.find(pat, start)
                if k < 0:
                    break
                score += 40
                start = k + 1
    for y in range(size - 1):
        for x in range(size - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(v for row in m for v in row)
    total = size * size
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += max(k, 0) * 10
    return score


def make_matrix(text):
    data = text.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("Text too long for the built-in QR generator (max %d bytes)." % MAX_BYTES)
    for version in range(1, 7):
        ec_len, groups = _EC_M[version]
        if len(data) + 2 <= sum(n * d for n, d in groups):
            return _matrix(_codewords(data, version), version)
    raise ValueError("Text too long")


def make_svg(text, scale=8, border=4, dark="#0B3B36", light="#FFFFFF"):
    m = make_matrix(text)
    n = len(m)
    dim = (n + 2 * border) * scale
    parts = []
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                parts.append("M%d,%dh%dv%dh-%dz" % ((x + border) * scale, (y + border) * scale, scale, scale, scale))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
        'role="img" aria-label="QR code for the website address">'
        '<rect width="100%%" height="100%%" fill="%s"/><path d="%s" fill="%s"/></svg>'
        % (dim, dim, dim, dim, light, "".join(parts), dark)
    )
