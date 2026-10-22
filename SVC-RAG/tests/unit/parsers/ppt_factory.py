"""Сборка минимального .ppt (OLE Compound File + записи PowerPoint 97-2003) вручную.

Бинарных файлов в репозитории нет: структура собирается кодом и покрывает ровно то, что читает ppt_binary
(Current User, PowerPoint Document, Pictures, каталог persist, списки слайдов/заметок, фигуры Escher, BStore).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

FREE, END, FATSECT, DIFSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SS, MINI = 512, 64

# ───────────────────────────── OLE ─────────────────────────────


def build_ole(streams: Dict[str, bytes], *, cutoff: int = 4096, difat_sector: bool = False,
              extra_storage: bool = False) -> bytes:
    sectors: List[bytes] = []
    fat: List[int] = []

    def alloc(blob: bytes, pad: bytes = b"\0") -> int:
        if not blob:
            return END
        n = (len(blob) + SS - 1) // SS
        blob = blob + pad * (n * SS - len(blob))
        start = len(sectors)
        for i in range(n):
            sectors.append(blob[i * SS:(i + 1) * SS])
            fat.append(start + i + 1 if i < n - 1 else END)
        return start

    entries: Dict[str, Tuple[int, int]] = {}
    mini = b""
    minifat: List[int] = []
    for name, data in streams.items():
        if len(data) >= cutoff:
            entries[name] = (alloc(data), len(data))
        else:
            n = max(1, (len(data) + MINI - 1) // MINI)
            start = len(mini) // MINI
            mini += data + b"\0" * (n * MINI - len(data))
            minifat.extend(start + i + 1 if i < n - 1 else END for i in range(n))
            entries[name] = (start if data else END, len(data))
    root_start = alloc(mini) if mini else END
    mf_blob = b"".join(struct.pack("<I", x) for x in minifat)
    first_minifat = alloc(mf_blob, b"\xff") if minifat else END
    n_minifat = (len(mf_blob) + SS - 1) // SS

    def dirent(name: str, kind: int, start: int, size: int) -> bytes:
        nm = name.encode("utf-16-le")
        return (nm.ljust(64, b"\0") + struct.pack("<H", len(nm) + 2 if name else 0) + bytes([kind, 1])
                + struct.pack("<III", FREE, FREE, FREE) + b"\0" * 16 + b"\0" * 4 + b"\0" * 16
                + struct.pack("<IQ", start, size))

    dirents = [dirent("Root Entry", 5, root_start, len(mini))]
    if extra_storage:
        dirents.append(dirent("Storage", 1, END, 0))
    dirents += [dirent(n, 2, s, z) for n, (s, z) in entries.items()]
    while len(dirents) % 4:
        dirents.append(b"\0" * 128)
    first_dir = alloc(b"".join(dirents))

    extra = 1 if difat_sector else 0
    n_fat = 1
    while n_fat * 128 < len(sectors) + n_fat + extra:
        n_fat += 1
    fat_start = len(sectors)
    fat.extend([FATSECT] * n_fat)
    if difat_sector:
        fat.append(DIFSECT)
    fat.extend([FREE] * (n_fat * 128 - len(fat)))
    fat_sids = list(range(fat_start, fat_start + n_fat))
    for i in range(n_fat):
        sectors.append(b"".join(struct.pack("<I", x) for x in fat[i * 128:(i + 1) * 128]))
    header_difat = [FREE] * 109
    first_difat, n_difat = END, 0
    if difat_sector:
        first_difat, n_difat = len(sectors), 1
        vals = fat_sids + [FREE] * (127 - len(fat_sids)) + [END]
        sectors.append(b"".join(struct.pack("<I", x) for x in vals))
    else:
        for i, sid in enumerate(fat_sids):
            header_difat[i] = sid

    h = bytearray(512)
    h[0:8] = OLE_MAGIC
    struct.pack_into("<HHHHH", h, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<II", h, 0x2C, n_fat, first_dir)
    struct.pack_into("<III", h, 0x38, cutoff, first_minifat, n_minifat)
    struct.pack_into("<II", h, 0x44, first_difat, n_difat)
    struct.pack_into("<109I", h, 0x4C, *header_difat)
    return bytes(h) + b"".join(sectors)


# ───────────────────────────── записи PowerPoint ─────────────────────────────

RT_DOCUMENT, RT_SLIDE, RT_NOTES = 0x03E8, 0x03EE, 0x03F0
RT_SLIDE_PERSIST, RT_SLIDE_ATOM = 0x03F3, 0x03EF
RT_PPDRAWING_GROUP, RT_PPDRAWING = 0x040B, 0x040C
RT_OUTLINE_REF, RT_TEXT_CHARS, RT_TEXT_BYTES = 0x0F9E, 0x0FA0, 0x0FA8
RT_SLIDE_LIST, RT_USER_EDIT, RT_CURRENT_USER, RT_PERSIST_DIR = 0x0FF0, 0x0FF5, 0x0FF6, 0x1772
RT_BSTORE, RT_FBSE, RT_SPGR, RT_SP, RT_FOPT, RT_CLIENT_TEXTBOX = 0xF001, 0xF007, 0xF003, 0xF004, 0xF00B, 0xF00D


def rec(rtype: int, data: bytes = b"", inst: int = 0, ver: int = 0) -> bytes:
    return struct.pack("<HHI", (inst << 4) | ver, rtype, len(data)) + data


def container(rtype: int, *children: bytes, inst: int = 0) -> bytes:
    return rec(rtype, b"".join(children), inst, 0xF)


def text_chars(s: str) -> bytes:
    return rec(RT_TEXT_CHARS, s.encode("utf-16-le"))


def text_bytes(s: str) -> bytes:
    return rec(RT_TEXT_BYTES, s.encode("latin-1"))


def outline_ref(k: int) -> bytes:
    return rec(RT_OUTLINE_REF, struct.pack("<i", k))


def textbox(*atoms: bytes) -> bytes:
    return container(RT_CLIENT_TEXTBOX, *atoms)


def fopt(pib: int) -> bytes:
    return rec(RT_FOPT, struct.pack("<HI", 0x4104, pib), inst=1)


def shape(*children: bytes) -> bytes:
    """SpContainer: дочерние записи (textbox, fopt, вложенная группа)."""
    return container(RT_SP, rec(0xF00A, b"\0" * 8, inst=0x4B2), *children)


def group(*shapes: bytes) -> bytes:
    return container(RT_SPGR, *shapes)


def blip(rtype: int, inst: int, body: bytes) -> bytes:
    two = {0xF01D: (0x46B, 0x6E3), 0xF02A: (0x6E3,), 0xF01E: (0x6E1,), 0xF01F: (0x7A9,), 0xF029: (0x6E5,)}.get(rtype, ())
    uid = b"\x11" * 16 + (b"\x22" * 16 if inst in two else b"")
    return rec(rtype, uid + b"\xff" + body, inst=inst, ver=0)


def pictures_stream(*blips: bytes) -> Tuple[bytes, List[int]]:
    out, offsets = b"", []
    for b in blips:
        offsets.append(len(out))
        out += b
    return out, offsets


def fbse(offset: int) -> bytes:
    return rec(RT_FBSE, b"\0" * 28 + struct.pack("<I", offset) + b"\0" * 4)


@dataclass
class Slide:
    shapes: Sequence[bytes] = ()
    outline: Sequence[bytes] = ()  # атомы текста в списке слайдов (TextChars/TextBytes)
    notes_id: int = 0
    with_drawing: bool = True
    wrong_type: bool = False


@dataclass
class Note:
    own_id: int
    listed: Sequence[bytes] = ()
    page_boxes: Sequence[bytes] = ()  # ClientTextbox на странице заметок
    wrong_type: bool = False


@dataclass
class PptParts:
    doc: bytes
    current_user: bytes
    pictures: bytes
    slide_offsets: List[int] = field(default_factory=list)


def _slide_container(s: Slide) -> bytes:
    atom = rec(RT_SLIDE_ATOM, b"\0" * 16 + struct.pack("<I", s.notes_id) + b"\0" * 4)
    children = [atom]
    if s.with_drawing:
        children.append(container(RT_PPDRAWING, container(0xF002, container(RT_SPGR, *s.shapes))))
    return rec(0x03EC if s.wrong_type else RT_SLIDE, b"".join(children), ver=0xF)


def _note_container(n: Note) -> bytes:
    inner = container(0xF002, container(RT_SPGR, *[container(RT_SP, box) for box in n.page_boxes]))
    return rec(RT_SLIDE if n.wrong_type else RT_NOTES, container(RT_PPDRAWING, inner), ver=0xF)


def build_parts(
    slides: Sequence[Slide] = (),
    notes: Sequence[Note] = (),
    bstore_children: Optional[Sequence[bytes]] = None,
    pictures: bytes = b"",
    doc_pid_type_ok: bool = True,
    newer_slides: Optional[Dict[int, Slide]] = None,
    extra_doc_children: Sequence[bytes] = (),
    drop_slide_from_directory: Optional[int] = None,
) -> PptParts:
    doc = bytearray()
    directory: Dict[int, int] = {}

    def put(pid: int, blob: bytes) -> int:
        directory[pid] = len(doc)
        doc.extend(blob)
        return directory[pid]

    slide_pids = [2 + i for i in range(len(slides))]
    note_pids = [100 + i for i in range(len(notes))]
    for pid, s in zip(slide_pids, slides):
        put(pid, _slide_container(s))
    for pid, n in zip(note_pids, notes):
        put(pid, _note_container(n))

    def persist_atoms(pid: int, s_id: int, texts: Sequence[bytes]) -> bytes:
        return rec(RT_SLIDE_PERSIST, struct.pack("<IIIII", pid, 0, 0, s_id, 0)) + b"".join(texts)

    slide_list = container(RT_SLIDE_LIST, *[persist_atoms(pid, 256 + i, s.outline) for i, (pid, s) in enumerate(zip(slide_pids, slides))], inst=0)
    note_list = container(RT_SLIDE_LIST, *[persist_atoms(pid, n.own_id, n.listed) for pid, n in zip(note_pids, notes)], inst=2)
    children = [slide_list, note_list]
    if bstore_children is not None:
        children.append(container(RT_PPDRAWING_GROUP, rec(0xF006, b"\0" * 4), container(0xF000, container(RT_BSTORE, *bstore_children))))
    children.extend(extra_doc_children)
    put(1, container(RT_DOCUMENT, *children) if doc_pid_type_ok else rec(0x03E9, b"", ver=0xF))

    def persist_dir(dir_: Dict[int, int]) -> bytes:
        pids = sorted(dir_)
        data, i = b"", 0
        while i < len(pids):
            j = i
            while j + 1 < len(pids) and pids[j + 1] == pids[j] + 1:
                j += 1
            cnt = j - i + 1
            data += struct.pack("<I", pids[i] | (cnt << 20)) + b"".join(struct.pack("<I", dir_[p]) for p in pids[i:j + 1])
            i = j + 1
        return rec(RT_PERSIST_DIR, data)

    def user_edit(last_edit: int, pdir_off: int) -> bytes:
        return rec(RT_USER_EDIT, b"\0" * 8 + struct.pack("<III", last_edit, pdir_off, 1) + b"\0" * 8)

    base_dir = {p: o for p, o in directory.items() if p != drop_slide_from_directory}
    pdir_off = len(doc)
    doc.extend(persist_dir(base_dir))
    edit_off = len(doc)
    doc.extend(user_edit(0, pdir_off))
    last_edit_off = edit_off

    if newer_slides:
        newer_dir = {}
        for idx, s in newer_slides.items():
            newer_dir[slide_pids[idx]] = len(doc)
            doc.extend(_slide_container(s))
        pdir2 = len(doc)
        doc.extend(persist_dir(newer_dir))
        last_edit_off = len(doc)
        doc.extend(user_edit(edit_off, pdir2))

    current_user = rec(RT_CURRENT_USER, b"\0" * 8 + struct.pack("<I", last_edit_off) + b"\0" * 8)
    return PptParts(bytes(doc), current_user, pictures, [directory[p] for p in slide_pids])


def build_ppt(*args, ole_kw: Optional[dict] = None, **kwargs) -> bytes:
    parts = build_parts(*args, **kwargs)
    streams = {"Current User": parts.current_user, "PowerPoint Document": parts.doc}
    if parts.pictures:
        streams["Pictures"] = parts.pictures
    return build_ole(streams, **(ole_kw or {}))
