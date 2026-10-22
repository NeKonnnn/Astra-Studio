"""Текст и картинки .ppt (PowerPoint 97-2003) без внешних утилит.

Тот же файл лежит в бэкенде (backend/utils/ppt_binary.py) и в SVC-RAG
(app/services/ppt_binary.py) - держать одинаковыми.

Зачем свой разбор: catppt берёт текст только из списка слайдов
(SlideListWithText). PowerPoint 2007+ при сохранении в .ppt кладёт туда
только текст плейсхолдеров, а текст обычных надписей, таблиц и групп - в
фигуры слайда. catppt его не видит, и на таких файлах отдаёт пусто.

Как читаем:
  1. .ppt - составной файл OLE: потоки «Current User», «PowerPoint
     Document», «Pictures». Читаем их по FAT / MiniFAT.
  2. «Current User» → последний UserEditAtom → каталог persist-объектов
     (с учётом более ранних правок файла).
  3. DocumentContainer → список слайдов в порядке показа и их текст,
     заметки, хранилище картинок (BStore).
  4. Каждый слайд: фигуры по порядку (группы раскрываем). Текст фигуры -
     её TextChars/TextBytes или ссылка на текст плейсхолдера в списке
     слайдов. Картинка - индекс в BStore → байты из «Pictures».

collect_ppt() отдаёт то же, что pptx_xml.collect_pptx(): элементы по
слайдам, картинки для OCR, заметки - собирать текст тем же pptx_text().
"""

import struct
from typing import Dict, List, Optional, Tuple

# ── OLE (Compound File Binary) ───────────────────────────────────────────

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_FREE, _END = 0xFFFFFFFF, 0xFFFFFFFE


class PptError(Exception):
    """Файл не разобран: не OLE, нет нужного потока, битая структура."""


class _Ole:
    def __init__(self, data: bytes):
        if data[:8] != _OLE_MAGIC:
            raise PptError("не OLE-файл (нет сигнатуры D0CF11E0)")
        self.data = data
        self.ssz = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_ssz = 1 << struct.unpack_from("<H", data, 0x20)[0]
        (n_fat, first_dir) = struct.unpack_from("<II", data, 0x2C)
        (self.mini_cutoff, first_minifat, n_minifat) = struct.unpack_from(
            "<III", data, 0x38
        )
        (first_difat, n_difat) = struct.unpack_from("<II", data, 0x44)
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sid, per = first_difat, self.ssz // 4 - 1
        for _ in range(n_difat):
            if sid in (_FREE, _END):
                break
            vals = struct.unpack_from(f"<{per + 1}I", data, self._off(sid))
            difat.extend(vals[:per])
            sid = vals[per]
        fat_sids = [s for s in difat if s not in (_FREE, _END)][:n_fat]
        n = self.ssz // 4
        self.fat: List[int] = []
        for s in fat_sids:
            self.fat.extend(struct.unpack_from(f"<{n}I", data, self._off(s)))
        self.minifat: List[int] = []
        if n_minifat:
            raw = self._chain(first_minifat)
            self.minifat = list(struct.unpack_from(f"<{len(raw) // 4}I", raw))
        self.entries: Dict[str, Tuple[int, int]] = {}
        d = self._chain(first_dir)
        root_start = None
        for i in range(len(d) // 128):
            e = d[i * 128 : (i + 1) * 128]
            name_len = struct.unpack_from("<H", e, 64)[0]
            kind = e[66]
            start, size = struct.unpack_from("<II", e, 116)
            name = e[: max(0, name_len - 2)].decode("utf-16-le", errors="replace")
            if kind == 5:
                root_start = (start, size)
            elif kind == 2:
                self.entries[name.lower()] = (start, size)
        self.ministream = b""
        if root_start and root_start[0] not in (_FREE, _END):
            self.ministream = self._chain(root_start[0])[: root_start[1]]

    def _off(self, sid: int) -> int:
        return (sid + 1) * self.ssz

    def _chain(self, sid: int) -> bytes:
        out, seen = [], set()
        while sid not in (_FREE, _END) and sid < len(self.fat) and sid not in seen:
            seen.add(sid)
            out.append(self.data[self._off(sid) : self._off(sid) + self.ssz])
            sid = self.fat[sid]
        return b"".join(out)

    def stream(self, name: str) -> Optional[bytes]:
        ent = self.entries.get(name.lower())
        if ent is None:
            return None
        start, size = ent
        if size < self.mini_cutoff:
            out, sid, seen = [], start, set()
            while sid not in (_FREE, _END) and sid < len(self.minifat):
                if sid in seen:
                    break
                seen.add(sid)
                o = sid * self.mini_ssz
                out.append(self.ministream[o : o + self.mini_ssz])
                sid = self.minifat[sid]
            return b"".join(out)[:size]
        return self._chain(start)[:size]


# ── Записи PowerPoint ────────────────────────────────────────────────────

_RT_DOCUMENT = 0x03E8
_RT_SLIDE = 0x03EE
_RT_NOTES = 0x03F0
_RT_SLIDE_PERSIST = 0x03F3
_RT_PPDRAWING_GROUP = 0x040B
_RT_PPDRAWING = 0x040C
_RT_OUTLINE_TEXT_REF = 0x0F9E
_RT_TEXT_HEADER = 0x0F9F
_RT_TEXT_CHARS = 0x0FA0
_RT_TEXT_BYTES = 0x0FA8
_RT_SLIDE_LIST = 0x0FF0
_RT_USER_EDIT = 0x0FF5
_RT_CURRENT_USER = 0x0FF6
_RT_PERSIST_DIR = 0x1772
_RT_NOTES_ATOM = 0x03F1
_RT_SLIDE_ATOM = 0x03EF
# OfficeArt
_RT_BSTORE = 0xF001
_RT_FBSE = 0xF007
_RT_SPGR = 0xF003
_RT_SP = 0xF004
_RT_FOPT = 0xF00B
_RT_CLIENT_TEXTBOX = 0xF00D
_PROP_PIB = 0x0104  # индекс картинки в BStore (1-based)

# Тип блипа → (расширение, признак «две UID» по инстансу).
_BLIP = {
    0xF01D: (".jpg", (0x46B, 0x6E3)),
    0xF02A: (".jpg", (0x6E3,)),
    0xF01E: (".png", (0x6E1,)),
    0xF01F: (".bmp", (0x7A9,)),
    0xF029: (".tif", (0x6E5,)),
}

# Тип текста в TextHeaderAtom: 0 заголовок, 1 текст, 4 прочее и т.д. -
# в итог идут все, кроме заметок мастера.


def _records(buf: bytes, start: int = 0, end: Optional[int] = None):
    """(тип, инстанс, версия, начало данных, длина) записей подряд."""
    pos, end = start, len(buf) if end is None else min(end, len(buf))
    while pos + 8 <= end:
        ver_inst, rtype, rlen = struct.unpack_from("<HHI", buf, pos)
        data_at = pos + 8
        if data_at + rlen > end:
            rlen = max(0, end - data_at)
        yield rtype, ver_inst >> 4, ver_inst & 0xF, data_at, rlen
        pos = data_at + rlen


def _find(buf: bytes, start: int, end: int, rtype: int):
    for t, inst, ver, at, ln in _records(buf, start, end):
        if t == rtype:
            return t, inst, ver, at, ln
    return None


def _text_of_atom(buf: bytes, rtype: int, at: int, ln: int) -> str:
    raw = buf[at : at + ln]
    if rtype == _RT_TEXT_CHARS:
        s = raw.decode("utf-16-le", errors="replace")
    else:
        # TextBytesAtom - младшие байты UTF-16, старшие нули: это latin-1.
        s = raw.decode("latin-1")
    # \r - конец абзаца, \x0b - мягкий перенос.
    s = s.replace("\r", "\n").replace("\x0b", "\n").strip()
    # Поле (номер слайда, дата) хранится как «*» - это не текст.
    return "" if not s.strip("*").strip() else s


def _persist_directory(doc: bytes, cur_user: bytes) -> Tuple[Dict[int, int], int, int]:
    """persistId → смещение в «PowerPoint Document», persistId документа и
    сколько правок (сохранений поверх) в файле."""
    rec = _find(cur_user, 0, len(cur_user), _RT_CURRENT_USER)
    if rec is None:
        raise PptError("в «Current User» нет CurrentUserAtom")
    offset_edit = struct.unpack_from("<I", cur_user, rec[3] + 8)[0]
    directory: Dict[int, int] = {}
    doc_ref: Optional[int] = None
    seen = set()
    while offset_edit and offset_edit not in seen and offset_edit + 8 <= len(doc):
        seen.add(offset_edit)
        ver_inst, rtype, rlen = struct.unpack_from("<HHI", doc, offset_edit)
        if rtype != _RT_USER_EDIT:
            break
        at = offset_edit + 8
        last_edit, persist_dir, ref = struct.unpack_from("<III", doc, at + 8)
        if doc_ref is None:
            doc_ref = ref
        hdr = struct.unpack_from("<HHI", doc, persist_dir)
        pos, pend = persist_dir + 8, persist_dir + 8 + hdr[2]
        while pos + 4 <= pend:
            word = struct.unpack_from("<I", doc, pos)[0]
            pid, cnt = word & 0xFFFFF, word >> 20
            pos += 4
            for k in range(cnt):
                if pos + 4 > pend:
                    break
                # Более поздняя правка уже записана - её не перетираем.
                directory.setdefault(pid + k, struct.unpack_from("<I", doc, pos)[0])
                pos += 4
        offset_edit = last_edit
    if doc_ref is None or doc_ref not in directory:
        raise PptError("не найден DocumentContainer в каталоге persist")
    return directory, doc_ref, len(seen)


def _bstore_offsets(doc: bytes, at: int, ln: int) -> List[int]:
    """Смещения картинок в «Pictures» по порядку BStore (индекс pib - 1)."""
    out: List[int] = []
    grp = _find(doc, at, at + ln, _RT_PPDRAWING_GROUP)
    if not grp:
        return out
    stack = [(grp[3], grp[3] + grp[4])]
    while stack:
        s, e = stack.pop()
        for t, inst, ver, a, n in _records(doc, s, e):
            if t == _RT_BSTORE:
                for t2, _i, _v, a2, n2 in _records(doc, a, a + n):
                    if t2 == _RT_FBSE and n2 >= 36:
                        out.append(struct.unpack_from("<I", doc, a2 + 28)[0])
                    else:
                        out.append(-1)
                return out
            if ver == 0xF:
                stack.append((a, a + n))
    return out


def _blip(pictures: bytes, offset: int) -> Optional[Tuple[str, bytes]]:
    if offset < 0 or offset + 8 > len(pictures):
        return None
    ver_inst, rtype, rlen = struct.unpack_from("<HHI", pictures, offset)
    kind = _BLIP.get(rtype)
    if not kind:
        return None  # EMF, WMF, PICT - векторные, OCR их не прочитает
    ext, two_uid = kind
    skip = 16 + (16 if (ver_inst >> 4) in two_uid else 0) + 1
    body = pictures[offset + 8 + skip : offset + 8 + rlen]
    if ext == ".bmp":
        body = _dib_to_bmp(body)
    return (ext, body) if body else None


def _dib_to_bmp(dib: bytes) -> bytes:
    """DIB из блипа → BMP-файл (дописать BITMAPFILEHEADER)."""
    if len(dib) < 40:
        return b""
    hsize, _w, _h, _pl, bits, comp = struct.unpack_from("<IiiHHI", dib, 0)
    colors = struct.unpack_from("<I", dib, 32)[0] if hsize >= 36 else 0
    if not colors and bits <= 8:
        colors = 1 << bits
    extra = 12 if (comp == 3 and hsize == 40) else 0
    off = 14 + hsize + extra + colors * 4
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, off) + dib


SlideItems = List[Tuple[str, str]]


def collect_ppt(
    data: bytes, max_images: int
) -> Tuple[List[SlideItems], List[Tuple[str, bytes]], List[str], Dict[str, int]]:
    """(элементы по слайдам, картинки для OCR, заметки, счётчики для лога)."""
    ole = _Ole(data)
    doc = ole.stream("PowerPoint Document")
    cur = ole.stream("Current User")
    if not doc:
        raise PptError("нет потока «PowerPoint Document»")
    if not cur:
        raise PptError("нет потока «Current User»")
    pictures = ole.stream("Pictures") or b""
    directory, doc_ref, n_edits = _persist_directory(doc, cur)
    d_at = directory[doc_ref]
    _v, rtype, d_len = struct.unpack_from("<HHI", doc, d_at)
    if rtype != _RT_DOCUMENT:
        raise PptError("persist документа указывает не на DocumentContainer")
    d_start, d_end = d_at + 8, d_at + 8 + d_len

    # Списки слайдов (inst 0) и заметок (inst 2). Запись списка: persistId
    # контейнера (+0) и id слайда / заметок (+12) - по нему SlideAtom
    # ссылается на свои заметки. Следом - текст плейсхолдеров.
    slides: List[Tuple[int, List[str]]] = []
    notes_by_id: Dict[int, Tuple[int, List[str]]] = {}
    for t, inst, ver, at, ln in _records(doc, d_start, d_end):
        if t != _RT_SLIDE_LIST or inst not in (0, 2):
            continue
        cur_list: Optional[List[str]] = None
        for t2, _i2, _v2, a2, n2 in _records(doc, at, at + ln):
            if t2 == _RT_SLIDE_PERSIST and n2 >= 16:
                pid, _flags, _n, own_id = struct.unpack_from("<IIII", doc, a2)
                cur_list = []
                if inst == 0:
                    slides.append((pid, cur_list))
                else:
                    notes_by_id[own_id] = (pid, cur_list)
            elif t2 in (_RT_TEXT_CHARS, _RT_TEXT_BYTES) and cur_list is not None:
                cur_list.append(_text_of_atom(doc, t2, a2, n2))
    bstore = _bstore_offsets(doc, d_start, d_end - d_start)
    stats = {
        "слайдов": len(slides),
        "текстов": 0,
        "картинок": 0,
        "растровых": 0,
        "сохранений": n_edits,
        "в_bstore": len(bstore),
    }

    per_slide: List[SlideItems] = []
    images: List[Tuple[str, bytes]] = []
    notes: List[str] = []
    for si, (pid, outline) in enumerate(slides, 1):
        items: SlideItems = []
        used: set = set()
        s_at = directory.get(pid)
        notes_ref = None
        if s_at is not None and s_at + 8 <= len(doc):
            _v, st, sl = struct.unpack_from("<HHI", doc, s_at)
            if st == _RT_SLIDE:
                s0, s1 = s_at + 8, s_at + 8 + sl
                atom = _find(doc, s0, s1, _RT_SLIDE_ATOM)
                if atom and atom[4] >= 16:
                    notes_ref = struct.unpack_from("<I", doc, atom[3] + 16)[0]
                drawing = _find(doc, s0, s1, _RT_PPDRAWING)
                if drawing:
                    _walk_shapes(
                        doc,
                        drawing[3],
                        drawing[3] + drawing[4],
                        outline,
                        used,
                        items,
                        images,
                        bstore,
                        pictures,
                        max_images,
                        si,
                        stats,
                    )
        # Текст плейсхолдеров, на который фигуры не сослались, - тоже в слайд.
        for k, txt in enumerate(outline):
            if k not in used and txt:
                items.append(("text", txt))
                stats["текстов"] += 1
        per_slide.append(items)
        notes.append(_notes_text(doc, directory, notes_ref, notes_by_id))
    return per_slide, images, notes, stats


def _walk_shapes(
    doc,
    start,
    end,
    outline,
    used,
    items,
    images,
    bstore,
    pictures,
    max_images,
    si,
    stats,
) -> None:
    for t, inst, ver, at, ln in _records(doc, start, end):
        if t == _RT_SP:
            _shape(
                doc,
                at,
                at + ln,
                outline,
                used,
                items,
                images,
                bstore,
                pictures,
                max_images,
                si,
                stats,
            )
        elif ver == 0xF:
            _walk_shapes(
                doc,
                at,
                at + ln,
                outline,
                used,
                items,
                images,
                bstore,
                pictures,
                max_images,
                si,
                stats,
            )


def _shape(
    doc,
    start,
    end,
    outline,
    used,
    items,
    images,
    bstore,
    pictures,
    max_images,
    si,
    stats,
) -> None:
    for t, inst, ver, at, ln in _records(doc, start, end):
        if t == _RT_SPGR:
            _walk_shapes(
                doc,
                at,
                at + ln,
                outline,
                used,
                items,
                images,
                bstore,
                pictures,
                max_images,
                si,
                stats,
            )
        elif t == _RT_CLIENT_TEXTBOX:
            for t2, _i, _v, a2, n2 in _records(doc, at, at + ln):
                if t2 in (_RT_TEXT_CHARS, _RT_TEXT_BYTES):
                    txt = _text_of_atom(doc, t2, a2, n2)
                    if txt:
                        items.append(("text", txt))
                        stats["текстов"] += 1
                elif t2 == _RT_OUTLINE_TEXT_REF and n2 >= 4:
                    k = struct.unpack_from("<i", doc, a2)[0]
                    if 0 <= k < len(outline) and k not in used:
                        used.add(k)
                        if outline[k]:
                            items.append(("text", outline[k]))
                            stats["текстов"] += 1
        elif t == _RT_FOPT:
            pib = _pib(doc, at, ln, inst)
            if pib and 0 < pib <= len(bstore):
                stats["картинок"] += 1
                blip = _blip(pictures, bstore[pib - 1])
                if blip and len(images) < max_images:
                    stats["растровых"] += 1
                    name = f"slide{si}_img{len(images) + 1}{blip[0]}"
                    images.append((name, blip[1]))
                    items.append(("img", name))


def _pib(doc: bytes, at: int, ln: int, n_props: int) -> Optional[int]:
    for k in range(n_props):
        p = at + k * 6
        if p + 6 > at + ln:
            break
        opid, val = struct.unpack_from("<HI", doc, p)
        if opid & 0x3FFF == _PROP_PIB:
            return val
    return None


def _notes_text(doc, directory, notes_ref, notes_by_id) -> str:
    """Заметки слайда: текст из списка заметок и из фигур страницы заметок."""
    if not notes_ref or notes_ref not in notes_by_id:
        return ""
    pid, listed = notes_by_id[notes_ref]
    parts = [t for t in listed if t]
    n_at = directory.get(pid)
    if n_at is not None and n_at + 8 <= len(doc):
        _v, nt, nl = struct.unpack_from("<HHI", doc, n_at)
        if nt == _RT_NOTES:
            stack = [(n_at + 8, n_at + 8 + nl)]
            while stack:
                s, e = stack.pop()
                for t, inst, ver, a, n in _records(doc, s, e):
                    if t == _RT_CLIENT_TEXTBOX:
                        for t2, _i, _v2, a2, n2 in _records(doc, a, a + n):
                            if t2 in (_RT_TEXT_CHARS, _RT_TEXT_BYTES):
                                txt = _text_of_atom(doc, t2, a2, n2)
                                if txt and txt not in parts:
                                    parts.append(txt)
                    elif ver == 0xF:
                        stack.append((a, a + n))
    return "\n".join(parts)
