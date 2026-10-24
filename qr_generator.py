"""Small dependency-free QR encoder for the WebUI login page.

The login endpoint returns a short URL.  Version 5-L is sufficient for the
current endpoint length and keeps the generated image small enough for a
WebUI API response.  The encoder emits a PNG directly with the standard
library, so the marketplace package does not need Pillow or another runtime
dependency.
"""

from __future__ import annotations

import base64
import struct
import zlib


def _gf_mul(x: int, y: int) -> int:
    result = 0
    while y:
        if y & 1:
            result ^= x
        y >>= 1
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    return result


def _rs_generator(degree: int) -> list[int]:
    # The leading 1 is kept here so the remainder loop can use g[1:].
    result = [1]
    for root_index in range(degree):
        root = 1
        for _ in range(root_index):
            root = _gf_mul(root, 2)
        next_result = [0] * (len(result) + 1)
        for index, value in enumerate(result):
            next_result[index] ^= value
            next_result[index + 1] ^= _gf_mul(value, root)
        result = next_result
    return result


def _ecc_codewords(data: list[int], degree: int) -> list[int]:
    generator = _rs_generator(degree)
    remainder = [0] * degree
    for value in data:
        factor = value ^ remainder[0]
        remainder = remainder[1:] + [0]
        for index, coefficient in enumerate(generator[1:]):
            remainder[index] ^= _gf_mul(coefficient, factor)
    return remainder


def _format_bits(value: int) -> int:
    remainder = value << 10
    while remainder.bit_length() >= 11:
        remainder ^= 0x537 << (remainder.bit_length() - 11)
    return ((value << 10) | remainder) ^ 0x5412


def make_qr_matrix(text: str) -> list[list[bool]]:
    """Return a version 5-L QR matrix for a UTF-8 URL or text value."""

    raw = str(text).encode("utf-8")
    if len(raw) > 106:
        raise ValueError("二维码内容超过版本 5-L 的容量")

    bits: list[int] = [0, 1, 0, 0]  # byte mode
    bits.extend((len(raw) >> (7 - index)) & 1 for index in range(8))
    for value in raw:
        bits.extend((value >> (7 - index)) & 1 for index in range(8))
    capacity = 108 * 8
    bits.extend([0] * min(4, capacity - len(bits)))
    bits.extend([0] * ((-len(bits)) % 8))
    data: list[int] = [
        sum(bits[offset + index] << (7 - index) for index in range(8))
        for offset in range(0, len(bits), 8)
    ]
    pad_bytes = (0xEC, 0x11)
    while len(data) < 108:
        data.append(pad_bytes[(len(data) - len(bits) // 8) % 2])
    codewords = data + _ecc_codewords(data, 26)

    size = 37
    matrix: list[list[bool | None]] = [[None] * size for _ in range(size)]

    def put(row: int, column: int, value: bool) -> None:
        if 0 <= row < size and 0 <= column < size:
            matrix[row][column] = bool(value)

    def finder(row0: int, column0: int) -> None:
        for row in range(-1, 8):
            for column in range(-1, 8):
                if row in (-1, 7) or column in (-1, 7):
                    value = False
                else:
                    value = (
                        row in (0, 6)
                        or column in (0, 6)
                        or (2 <= row <= 4 and 2 <= column <= 4)
                    )
                put(row0 + row, column0 + column, value)

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    # Version 5 has alignment centers at 6 and 30; the three corner patterns
    # already occupy the other possible combinations.
    center_row = center_column = 30
    for row in range(-2, 3):
        for column in range(-2, 3):
            if matrix[center_row + row][center_column + column] is None:
                put(
                    center_row + row,
                    center_column + column,
                    max(abs(row), abs(column)) != 1,
                )

    # Timing patterns in the open space between the finders.
    for index in range(8, size - 8):
        if matrix[6][index] is None:
            put(6, index, index % 2 == 0)
        if matrix[index][6] is None:
            put(index, 6, index % 2 == 0)

    # Reserve both format-information copies before placing codewords.
    for index in range(9):
        if matrix[index][8] is None:
            put(index, 8, False)
        if matrix[8][index] is None:
            put(8, index, False)
    for index in range(size - 8, size):
        put(8, index, False)
    for index in range(size - 7, size):
        put(index, 8, False)
    put(size - 8, 8, True)  # fixed dark module

    payload: list[int] = []
    for value in codewords:
        payload.extend((value >> (7 - index)) & 1 for index in range(8))
    bit_index = 0
    column = size - 1
    upward = True
    while column > 0:
        if column == 6:
            column -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for current_column in (column, column - 1):
                if matrix[row][current_column] is None and bit_index < len(payload):
                    bit = payload[bit_index]
                    matrix[row][current_column] = bool(
                        bit ^ ((row + current_column) % 2 == 0)
                    )  # mask 0
                    bit_index += 1
        upward = not upward
        column -= 2
    if bit_index != len(payload):
        raise RuntimeError("二维码数据区布局异常")
    for row in range(size):
        for column in range(size):
            if matrix[row][column] is None:
                matrix[row][column] = False

    fmt = _format_bits(1 << 3)  # error correction L, mask 0
    for index in range(15):
        bit = bool((fmt >> index) & 1)
        if index < 6:
            matrix[index][8] = bit
        elif index < 8:
            matrix[index + 1][8] = bit
        else:
            matrix[8][14 - index] = bit
        if index < 8:
            matrix[8][size - index - 1] = bit
        else:
            matrix[size - 15 + index][8] = bit
    matrix[size - 8][8] = True
    return [[bool(value) for value in row] for row in matrix]


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def qr_png_bytes(text: str, *, scale: int = 8, border: int = 4) -> bytes:
    matrix = make_qr_matrix(text)
    modules = len(matrix) + border * 2
    width = modules * scale
    rows: list[bytes] = []
    blank = bytes([255]) * width
    for row in range(-border, len(matrix) + border):
        line = bytearray()
        for column in range(-border, len(matrix) + border):
            pixel = 0 if 0 <= row < len(matrix) and 0 <= column < len(matrix) and matrix[row][column] else 255
            line.extend([pixel] * scale)
        encoded = bytes(line)
        for _ in range(scale):
            rows.append(b"\x00" + encoded)
    ihdr = struct.pack(">IIBBBBB", width, width, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _png_chunk(b"IEND", b"")


def qr_data_url(text: str) -> str:
    encoded = base64.b64encode(qr_png_bytes(text)).decode("ascii")
    return f"data:image/png;base64,{encoded}"
