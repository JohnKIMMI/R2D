"""Address bitmaps: one byte per address of the binary's function span.

Painting a block is a slice assignment, so painting many inputs into the same
bitmap is a union, and overlapping blocks are counted once. Counting uses a big
integer AND + bit_count, which is a few milliseconds even for a 12 MB span, so
no numpy is needed anywhere.

File format: the raw bytes (one byte per address, 0/1). Length == span.
"""
MARK = b"\x01"


class Bitmap:
    __slots__ = ("buf", "_int")

    def __init__(self, span_or_bytes):
        if isinstance(span_or_bytes, int):
            self.buf = bytearray(span_or_bytes)
        else:
            self.buf = bytearray(span_or_bytes)
        self._int = None

    # -- building ---------------------------------------------------------
    def paint(self, blocks):
        """blocks: iterable of (addr, size); anything beyond the span is clipped."""
        n = len(self.buf)
        buf = self.buf
        for a, sz in blocks:
            if a >= n:
                continue
            b = min(a + sz, n)
            buf[a:b] = MARK * (b - a)
        self._int = None
        return self

    def paint_ranges(self, ranges):
        n = len(self.buf)
        for a, b in ranges:
            if a < n:
                b = min(b, n)
                self.buf[a:b] = MARK * (b - a)
        self._int = None
        return self

    def union(self, other):
        """self |= other (same span)."""
        a, b = self.as_int(), other.as_int()
        self.buf = bytearray((a | b).to_bytes(len(self.buf), "little"))
        self._int = None
        return self

    def clear(self):
        self.buf = bytearray(len(self.buf))
        self._int = None

    # -- counting ---------------------------------------------------------
    def as_int(self):
        if self._int is None:
            self._int = int.from_bytes(self.buf, "little")
        return self._int

    def count(self):
        return self.buf.count(1)

    def count_and(self, other):
        """|self & other|"""
        return (self.as_int() & other.as_int()).bit_count()

    def count_and_not(self, other):
        """|self & ~other|"""
        return (self.as_int() & ~other.as_int()).bit_count()

    def any_in(self, a, b):
        return 1 in self.buf[a:b]

    def count_in(self, a, b):
        return self.buf[a:b].count(1)

    def functions_hit(self, funcs):
        """{name: covered bytes} over a Functions table (exact, no double counting)."""
        out = {}
        buf = self.buf
        for s, e, n in funcs.funcs:
            c = buf[s:e].count(1)
            if c:
                out[n] = c
        return out

    # -- persistence --------------------------------------------------------
    def save(self, path):
        with open(path, "wb") as fh:
            fh.write(self.buf)

    @classmethod
    def load(cls, path, span=None):
        with open(path, "rb") as fh:
            data = fh.read()
        if span is not None and len(data) != span:
            raise ValueError(f"{path}: length {len(data)} != span {span}")
        return cls(data)

    @classmethod
    def from_functions(cls, funcs):
        """Mask of bytes that belong to some function."""
        return cls(funcs.span).paint_ranges((s, e) for s, e, _ in funcs.funcs)
