"""Function table and .text range of an ELF binary, via binutils.

Functions come from the symbol table (FUNC symbols with a size, C++ names
demangled); the first name seen for an address range wins. Addresses are the file's virtual addresses, so a
PIE binary starts near 0 -- the same space drcov blocks are normalised to.
"""
import subprocess
from bisect import bisect_right
from pathlib import Path


class Functions:
    """Sorted function table with fast address -> function lookup."""

    def __init__(self, funcs):
        self.funcs = sorted(funcs)                    # [(start, end, name)]
        self.starts = [f[0] for f in self.funcs]
        self.size = {n: e - s for s, e, n in self.funcs}
        self.program_bytes = sum(self.size.values())
        self.span = (max(e for _, e, _ in self.funcs) + 1) if self.funcs else 0

    def __len__(self):
        return len(self.funcs)

    def overlaps(self, a, b):
        """Functions overlapping [a, b) -> [(name, overlap bytes)]"""
        out = []
        i = max(bisect_right(self.starts, a) - 1, 0)
        while i < len(self.funcs) and self.funcs[i][0] < b:
            fs, fe, name = self.funcs[i]
            ov = min(b, fe) - max(a, fs)
            if ov > 0:
                out.append((name, ov))
            i += 1
        return out

    def covered_by_blocks(self, blocks):
        """{function: covered bytes} from a block list (overlapping blocks may double count;
        use for 'which functions' and ordering, use a Bitmap for exact byte counts)."""
        fn = {}
        for a, n in blocks:
            for name, ov in self.overlaps(a, a + n):
                fn[name] = fn.get(name, 0) + ov
        return fn


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def load_functions(binary, dedup="address", demangle=True):
    """-> Functions

    dedup="address": one function per start address, labelled with the first symbol
      seen there (aliases such as C++ C1/C2 constructors are not counted twice).
    dedup="name": the legacy pipeline's rule, one entry per symbol name (counts
      aliases twice, merges same-named local functions); for comparisons only.
    demangle: readelf -C ("ns::Class::method(args)"); C names are unchanged.
    """
    seen_name, seen_addr, out = set(), {}, []
    flag = "-sWC" if demangle else "-sW"
    for ln in _run(["readelf", flag, str(binary)]).splitlines():
        p = ln.split()
        if len(p) >= 8 and p[3] == "FUNC" and p[6] != "UND":
            try:
                a, s = int(p[1], 16), int(p[2])
            except ValueError:
                continue
            if s <= 0:
                continue
            name = " ".join(p[7:])                      # demangled names contain spaces
            if dedup == "name":
                if name in seen_name:
                    continue
                seen_name.add(name)
            else:
                if a in seen_addr:
                    continue
                seen_addr[a] = s
            out.append((a, a + s, name))
    return Functions(out)


def text_range(binary):
    """-> (start, size) of .text, or (0, 0)"""
    for ln in _run(["size", "-A", str(binary)]).splitlines():
        p = ln.split()
        if len(p) == 3 and p[0] == ".text":
            return int(p[2]), int(p[1])
    return 0, 0


def binary_name(binary):
    return Path(binary).name
