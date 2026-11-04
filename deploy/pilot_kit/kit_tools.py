"""Helpers for the CTDMS pilot kit (the build and the check after copying). Standard library only, so it runs on the
kit's own Python.

    python kit_tools.py runtime  KIT_DIR [--copy]   # which Microsoft runtime DLLs do the compiled parts need? (--copy: add
                                                    # the missing ones from this PC's System32 into KIT_DIR\\python)
    python kit_tools.py manifest KIT_DIR            # write MANIFEST.txt: size of every file, SHA-256 of the program files
    python kit_tools.py verify   [KIT_DIR]          # compare a copied kit with its MANIFEST.txt (CHECK_COPY.bat)
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import sys
from pathlib import Path

# Microsoft Visual C++ runtime files. Windows itself does not ship these, so a clean PC may lack them.
VC_FAMILY = ("msvcp140", "vcruntime140", "concrt140", "vcomp140", "vccorlib140", "mfc140")
HASHED_TOP = ("app", "migrations", "docs")  # these folders (and the launchers) also get a SHA-256 in the manifest


def imported_dlls(path: Path) -> list[str]:
    """Names of the DLLs a Windows .dll / .pyd imports (PE import table). [] when it is not a readable PE file."""
    try:
        data = path.read_bytes()
        if data[:2] != b"MZ":
            return []
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return []
        n_sections, = struct.unpack_from("<H", data, pe + 6)
        opt_size, = struct.unpack_from("<H", data, pe + 20)
        opt = pe + 24
        magic, = struct.unpack_from("<H", data, opt)
        dd = opt + (96 if magic == 0x10B else 112)
        import_rva, = struct.unpack_from("<I", data, dd + 8)  # data directory 1 = import table
        if not import_rva:
            return []
        sections = []
        sec = opt + opt_size
        for i in range(n_sections):
            vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, sec + 40 * i + 8)
            sections.append((va, max(vsize, rawsize), rawptr))

        def offset(rva: int) -> int:
            for va, size, rawptr in sections:
                if va <= rva < va + size:
                    return rva - va + rawptr
            raise ValueError("rva outside sections")

        names = []
        desc = offset(import_rva)
        while True:
            fields = struct.unpack_from("<IIIII", data, desc)
            if not fields[3]:
                break
            start = offset(fields[3])
            names.append(data[start:data.index(b"\0", start)].decode("ascii", "replace"))
            desc += 20
        return names
    except (OSError, ValueError, struct.error):
        return []


def runtime(kit: Path, copy: bool) -> int:
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    python_dir = kit / "python"
    checked = 0
    needs: dict[str, list[str]] = {}
    for p in kit.rglob("*"):
        if p.suffix.lower() not in (".dll", ".pyd") or not p.is_file():
            continue
        checked += 1
        for name in imported_dlls(p):
            if not name.lower().startswith(VC_FAMILY):
                continue
            # a .pyd finds its DLLs next to itself and next to python.exe
            if (p.parent / name).exists() or (python_dir / name).exists():
                continue
            needs.setdefault(name.lower(), []).append(p.name)
    print(f"Compiled parts checked: {checked}")
    carried = sorted(f.name for f in python_dir.glob("*.dll") if f.name.lower().startswith(VC_FAMILY))
    print("Microsoft runtime files the kit carries: " + (", ".join(carried) or "none"))
    if not needs:
        print("Nothing is missing: every Microsoft runtime file that is needed is inside the kit.")
        return 0
    status = 0
    for name, who in sorted(needs.items()):
        print(f"MISSING from the kit: {name}  (needed by {', '.join(sorted(set(who))[:4])})")
        if copy:
            src = system32 / name
            if src.exists():
                shutil.copy2(src, python_dir / name)
                print(f"  -> copied {name} from {system32} into the kit")
            else:
                print(f"  -> {name} is not in {system32} either - the kit cannot run on a PC without it")
                status = 1
        else:
            status = 1
    return status


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _tracked(kit: Path):
    for p in sorted(kit.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(kit).as_posix()
        if rel.split("/")[0] == "data" or rel in (".env", "MANIFEST.txt") or "__pycache__" in rel or p.suffix == ".pyc":
            continue  # these change while CTDMS runs
        yield p, rel


def manifest(kit: Path) -> int:
    lines = []
    for p, rel in _tracked(kit):
        top = rel.split("/")[0]
        digest = _sha256(p) if top in HASHED_TOP or "/" not in rel else "-"
        lines.append(f"{p.stat().st_size}\t{digest}\t{rel}")
    (kit / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"MANIFEST.txt written: {len(lines)} files")
    return 0


def verify(kit: Path) -> int:
    mf = kit / "MANIFEST.txt"
    if not mf.exists():
        print("MANIFEST.txt is missing - copy the whole CTDMS folder again.")
        return 1
    problems: list[str] = []
    total = 0
    for line in mf.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        size, digest, rel = line.split("\t", 2)
        total += 1
        p = kit / rel
        if not p.is_file():
            problems.append(f"missing  {rel}")
        elif p.stat().st_size != int(size):
            problems.append(f"damaged  {rel} (size differs)")
        elif digest != "-" and _sha256(p) != digest:
            problems.append(f"changed  {rel}")
    print(f"{total} files checked.")
    if problems:
        for pr in problems[:25]:
            print("  " + pr)
        if len(problems) > 25:
            print(f"  ... and {len(problems) - 25} more")
        print("COPY IS NOT COMPLETE - delete C:\\CTDMS and copy the whole folder from the USB drive again.")
        return 1
    print("COPY IS COMPLETE - every file is there and unchanged.")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("runtime", "manifest", "verify"):
        print(__doc__)
        return 2
    cmd = argv[0]
    kit = Path(argv[1]).resolve() if len(argv) > 1 and not argv[1].startswith("--") else Path(__file__).resolve().parent
    if cmd == "runtime":
        return runtime(kit, "--copy" in argv)
    if cmd == "manifest":
        return manifest(kit)
    return verify(kit)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
