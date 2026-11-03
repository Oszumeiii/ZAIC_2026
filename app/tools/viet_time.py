"""
viet_time.py — bộ giải thời gian tham chiếu cho DataMate (Zalo AI Challenge 2026, Track 2)

Hai nửa độc lập:
  A. resolve(question, as_of)     : câu hỏi tiếng Việt -> TimeSpec (các khoảng ngày [start, end] GỒM CẢ HAI ĐẦU)
  B. parse_cell_date(value, ...)  : ô dữ liệu bẩn (ISO / dd/mm/yyyy / serial Excel / '1 thg 6 2025' / dd/mm) -> date

Nguyên tắc: LLM (nếu dùng) chỉ trích *cụm từ* / điền khuyết; MỌI phép cộng trừ ngày làm bằng code ở đây.
"""
from __future__ import annotations
import re, unicodedata, calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

# ───────────────────────── helpers ngày ─────────────────────────
def last_day(y, m): return calendar.monthrange(y, m)[1]
def month_span(y, m): return (date(y, m, 1), date(y, m, last_day(y, m)))
def add_months(y, m, k):
    t = y * 12 + (m - 1) + k
    return t // 12, t % 12 + 1
def clamp_day(y, m, d): return date(y, m, min(d, last_day(y, m)))
def week_monday(d): return d - timedelta(days=d.weekday())          # tuần bắt đầu THỨ HAI
def quarter_span(y, q): return (date(y, 3 * q - 2, 1), month_span(y, 3 * q)[1])
def shift_year(d, k):                                               # 29/02 -> 28/02 nếu năm đích không nhuận
    return clamp_day(d.year + k, d.month, d.day)

# ───────────────────────── chuẩn hoá chữ ─────────────────────────
def strip_acc(s):
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

# Từ vựng thời gian dùng để nhận ra chữ Telex gõ lỗi (nawm, thangs, naof, heets, cungf, hown, ddown...)
LEX = set("""nam thang tuan quy hom nay qua roi truoc ngay het tinh den ky cung dau ke tu gan nhat lien bua
tiep nhau ket thuc so voi tang giam nao kia toi hon kem tong cong doanh thu mau bao nhieu may""".split())

def _telex_base(t):
    if len(t) > 2 and t[-1] in "sfrxjz": t = t[:-1]                  # phím dấu thanh ở cuối âm tiết
    t = t.replace("dd", "d")
    for a, b in (("aa", "a"), ("ee", "e"), ("oo", "o"), ("aw", "a"), ("ow", "o"), ("uw", "u")): t = t.replace(a, b)
    return t

def fix_telex_tokens(s):
    """Chỉ sửa token ASCII mà giải mã Telex RA MỘT TỪ KHOÁ THỜI GIAN; token khác (tên hàng: 'pudding', 'power') giữ nguyên."""
    def f(m):
        w = m.group(0)
        if w in LEX: return w
        b = _telex_base(w)
        return b if b in LEX else w
    return re.sub(r"[a-z]+", f, s)

ABBR = [(r"\bhnay\b", "hom nay"), (r"\bhqua\b", "hom qua"), (r"\bbua nay\b", "hom nay"), (r"\bbua qua\b", "hom qua"),
        (r"\bth (truoc|trc|roi)\b", r"thang \1"), (r"\bthg\b", "thang"), (r"\btrc\b", "truoc"), (r"\bhom bua\b", "hom qua"),
        (r"\bngay do\b", "ngay do"), (r"\bhom truoc\b", "hom qua")]

def normalize(q):
    s = unicodedata.normalize("NFC", q.lower())
    s = strip_acc(s)
    s = fix_telex_tokens(s)
    for p, r in ABBR: s = re.sub(p, r, s)
    return re.sub(r"\s+", " ", s).strip()

# số chữ: "tháng tư", "tháng mười một", "ba tháng liền nhau"
_WORD = {"mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9, "muoi": 10}
def month_word(tok_accented):
    """Chỉ dùng trên bản CÒN DẤU: 'tháng tư' (=4) khác 'từ tháng' (=from); 'tháng năm' (=5) khác 'năm 2025' (=year)."""
    m = re.search(r"tháng (một|hai|ba|bốn|tư|năm|sáu|bảy|tám|chín|mười một|mười hai|mười)(?![\wà-ỹ])(?!\s*(?:ngoái|nay|trước|qua|rồi)\b)", tok_accented)
    if not m: return None
    w = strip_acc(m.group(1)); return {"muoi mot": 11, "muoi hai": 12}.get(w, _WORD.get(w.split()[0] if w != "muoi" else "muoi"))

# ───────────────────────── cấu trúc kết quả ─────────────────────────
@dataclass
class Period:
    start: date
    end: date                         # gồm cả end
    label: str = ""
    role: str = "main"                # main | baseline
    def days(self): return (self.end - self.start).days + 1
    def __iter__(self): return iter((self.start, self.end))

@dataclass
class TimeSpec:
    periods: list = field(default_factory=list)
    stock: bool = False               # "đến hết ngày X / tính đến hôm nay": trạng thái còn lại, cộng dồn TỪ ĐẦU DỮ LIỆU (không phải 1 kỳ)
    stock_date: Optional[date] = None
    shift: Optional[str] = None       # 'sang' (06:00–13:59:59) | 'chieu' (14:00–21:59:59)
    needs_data: list = field(default_factory=list)   # ví dụ 'price_raise_date': phải tra bảng giá
    flags: list = field(default_factory=list)        # mơ hồ / suy luận -> nên hạ độ tin cậy, cân nhắc abstain
    series: bool = False              # nhiều tháng, trả list theo thứ tự thời gian
    def main(self): return [p for p in self.periods if p.role == "main"]
    def baseline(self): return next((p for p in self.periods if p.role == "baseline"), None)

# ───────────────────────── Tết / âm lịch ─────────────────────────
def tet_range(cal, Y):
    """cal: DataFrame calendar.csv (date, lunar_day, lunar_month, lunar_year, lunar_leap).
    23 tháng Chạp (năm âm Y-1) -> HẾT mùng 10 tháng Giêng (năm âm Y). Bỏ tháng nhuận."""
    nl = ~cal["lunar_leap"].astype(bool)
    a = cal[(cal.lunar_year == Y - 1) & (cal.lunar_month == 12) & (cal.lunar_day == 23) & nl].date
    b = cal[(cal.lunar_year == Y) & (cal.lunar_month == 1) & (cal.lunar_day == 10) & nl].date
    if not len(a) or not len(b): return None
    return a.iloc[0].date(), b.iloc[0].date()

def before_tet(cal, Y):
    t = tet_range(cal, Y)
    if not t: return None
    n = (t[1] - t[0]).days + 1
    return t[0] - timedelta(days=n), t[0] - timedelta(days=1)

# ───────────────────────── A. giải câu hỏi ─────────────────────────
_D = r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(20\d\d)"
def _mk(y, m, d):
    try: return date(y, m, d)
    except ValueError: return None

def resolve(question, as_of, cal=None):
    """as_of: date. Trả TimeSpec. Không bao giờ đoán thầm: điều gì suy luận / mơ hồ thì ghi vào .flags."""
    raw = unicodedata.normalize("NFC", question.lower())
    t = normalize(question)
    a = as_of
    sp = TimeSpec()
    used = []                                   # đã tiêu thụ đoạn nào, tránh bắt trùng

    def take(rx, s=None):
        s = t if s is None else s
        ms = []
        for m in re.finditer(rx, s):
            if any(not (m.end() <= u0 or m.start() >= u1) for u0, u1 in used): continue
            used.append((m.start(), m.end())); ms.append(m)
        return ms
    def add(p0, p1, label="", role="main"): sp.periods.append(Period(p0, p1, label, role))

    # 0. ca sáng / ca chiều (lọc theo GIỜ tạo đơn, không phải khoảng ngày)
    if re.search(r"\bca sang\b", t): sp.shift = "sang"
    if re.search(r"\bca chieu\b", t): sp.shift = "chieu"

    # 1. so sánh: tách riêng để biết cụm nào là "kỳ so"
    cmp_prev_month = take(r"(?:so (?:voi|vs|sanh voi) (?:cung ky )?|cung ky )(?:thang|th) (?:truoc|lien truoc|truoc do|roi|ngoai)(?: do)?|(?:thang) (?:lien truoc|truoc do)")
    cmp_last_year = take(r"(so (voi|vs) )?cung ky (nam|thang nam) (truoc|ngoai|qua)|cung thang nam (truoc|ngoai)|so (voi|vs) (thang|nam) (nay )?(nam )?(truoc|ngoai) ")
    cmp_prev_q = take(r"so (voi|vs) quy (truoc|roi)")
    cmp_prev_w = take(r"so (voi|vs) tuan (truoc|roi)")
    cmp_tet = take(r"(?:so (?:voi|vs) )?truoc tet(?: nam)?(?= (?:20\d\d|nay)|\b)")
    if cmp_tet and cal is not None:                                   # giữ lại năm của Tết đang hỏi
        _ym = re.search(r"truoc tet(?: nam)? (20\d\d|nay)", t); _Y = a.year if (not _ym or _ym[1] == "nay") else int(_ym[1])
        _tr = tet_range(cal, _Y)
        if _tr:
            add(_tr[0], _tr[1], f"Tết {_Y}")
            _n = (_tr[1] - _tr[0]).days + 1
            add(_tr[0] - timedelta(_n), _tr[0] - timedelta(1), "ngay trước Tết (cùng số ngày)", "baseline")
        cmp_tet = []
    cmp_tet_ly = take(r"(dip |mua )?tet nam (truoc|ngoai)")

    # 2. khoảng ngày tường minh
    for m in take(r"(\d+) ngay ke tu ngay " + _D):                      # N ngày kể từ D : [D, D+N-1]
        n = int(m[1]); d = _mk(int(m[4]), int(m[3]), int(m[2]))
        if d: add(d, d + timedelta(n - 1), f"{n} ngày kể từ {d}")
    for m in take(r"(\d+) ngay (?:ngay )?(?:truoc|lien truoc|truoc do) (?:ngay )?(?:do|" + _D + ")"):   # N ngày ngay trước D : [D-N, D-1]
        n = int(m[1]); mm = re.search(_D, m[0]); d0 = None
        if mm: d0 = _mk(int(mm[3]), int(mm[2]), int(mm[1]))
        else:                                                           # "ngày đó" -> ngày kể từ ở trên
            ref = [p for p in sp.periods if "kể từ" in p.label]
            d0 = ref[0].start if ref else None
        if d0: add(d0 - timedelta(n), d0 - timedelta(1), f"{n} ngày ngay trước {d0}", "baseline")
        else: sp.needs_data.append("price_raise_date")
    for m in take(r"(\d+) ngay ke tu ngay do|(\d+) ngay ke tu ngay tang gia"):
        sp.needs_data.append("price_raise_date")
    for m in take(r"tu (?:ngay )?" + _D + r" den (?:het )?(?:ngay )?" + _D):   # từ X đến (hết) Y: gồm cả X và Y
        d1 = _mk(int(m[3]), int(m[2]), int(m[1])); d2 = _mk(int(m[6]), int(m[5]), int(m[4]))
        if d1 and d2: add(d1, d2, f"{d1}→{d2}")
    for m in take(r"(?:den het|tinh den(?: het)?|cuoi ngay|het) (?:ngay )?" + _D):   # tính đến hết ngày X  -> STOCK
        d = _mk(int(m[3]), int(m[2]), int(m[1]))
        if d: sp.stock = True; sp.stock_date = d; add(d, d, f"đến hết {d}")
    for m in take(r"(?:ngay )?" + _D):                                  # ngày X (đơn lẻ)
        d = _mk(int(m[3]), int(m[2]), int(m[1]))
        if d: add(d, d, f"ngày {d}")

    # 3. chuỗi tháng
    for m in take(r"(\d+) thang lien (?:nhau|tiep) ket thuc (?:o|tai|vao) thang (\d{1,2}) ?(?:/|nam) ?(20\d\d)"):
        n, M, Y = int(m[1]), int(m[2]), int(m[3]); sp.series = True
        for k in range(-(n - 1), 1):
            y, mo = add_months(Y, M, k); add(*month_span(y, mo), f"{mo}/{y}")
    for m in take(r"(\d+) thang lien (?:nhau|tiep) tu thang (\d{1,2}) ?(?:/|nam) ?(20\d\d)"):
        n, M, Y = int(m[1]), int(m[2]), int(m[3]); sp.series = True
        for k in range(n):
            y, mo = add_months(Y, M, k); add(*month_span(y, mo), f"{mo}/{y}")
    for m in take(r"tu thang (\d{1,2}) ?(?:/|nam) ?(20\d\d) (?:qua|den|toi) (?:thang )?(\d{1,2}) ?(?:/|nam) ?(20\d\d)"):
        M1, Y1, M2, Y2 = map(int, m.groups()); sp.series = True
        y, mo = Y1, M1
        while (y, mo) <= (Y2, M2): add(*month_span(y, mo), f"{mo}/{y}"); y, mo = add_months(y, mo, 1)
        if "doanh thu" in t and re.search(r"tong|tat ca|cong lai", t): sp.series = False

    # 4. tháng M/Y, M năm Y, M/Y trần
    for m in take(r"thang (\d{1,2}) ?(?:/|nam|-) ?(20\d\d)|(?<![\d/])(\d{1,2}) ?/ ?(20\d\d)(?![\d/])"):
        M = int(m[1] or m[3]); Y = int(m[2] or m[4])
        if 1 <= M <= 12: add(*month_span(Y, M), f"{M}/{Y}")
    for m in take(r"quy (\d|i{1,3}|iv)\s*(?:/|nam|-)?\s*(20\d\d)|\bq([1-4])\s*[/ -]?\s*(20\d\d)"):
        q = m[1] or m[3]; q = {"i": 1, "ii": 2, "iii": 3, "iv": 4}.get(q) or int(q); Y = int(m[2] or m[4])
        add(*quarter_span(Y, q), f"Q{q}/{Y}")
    # tháng/quý KHÔNG năm -> năm nay nếu đầu kỳ không sau hôm nay, ngược lại năm trước (rulebook §2)
    for m in take(r"thang (\d{1,2})(?![\d/])(?! ?(?:/|nam|-) ?\d)"):
        M = int(m[1])
        if 1 <= M <= 12:
            Y = a.year if date(a.year, M, 1) <= a else a.year - 1
            add(*month_span(Y, M), f"{M}/{Y} (không ghi năm)"); sp.flags.append("month_no_year")
    for m in take(r"quy (\d)(?![\d/])(?! ?(?:/|nam) ?\d)"):
        q = int(m[1])
        if 1 <= q <= 4:
            Y = a.year if date(a.year, 3 * q - 2, 1) <= a else a.year - 1
            add(*quarter_span(Y, q), f"Q{q}/{Y} (không ghi năm)"); sp.flags.append("quarter_no_year")
    # 5. tương đối
    if take(r"tu dau (thang|nam|tuan) (nay )?den (het )?(hom nay|bua nay)|tu dau (thang|nam|tuan) (nay )?(toi|den) nay|tu dau (thang|nam|tuan) nay"):
        unit = re.search(r"tu dau (thang|nam|tuan)", t)[1]
        s = {"thang": a.replace(day=1), "nam": date(a.year, 1, 1), "tuan": week_monday(a)}[unit]
        add(s, a, f"đầu {unit} → hôm nay")
    for m in take(r"tu (?:ngay )?1 ?/ ?1 (?:den (?:het )?(?:hom nay|nay)|nam nay)?"): add(date(a.year, 1, 1), a, "1/1 → hôm nay"); sp.flags.append("day_month_no_year")
    if take(r"\bhom nay\b|\bngay nay\b"): add(a, a, "hôm nay")
    if take(r"hom qua"): add(a - timedelta(1), a - timedelta(1), "hôm qua")
    if take(r"hom kia"): add(a - timedelta(2), a - timedelta(2), "hôm kia")
    if take(r"ngay mai"): add(a + timedelta(1), a + timedelta(1), "ngày mai"); sp.flags.append("future")
    if take(r"tuan nay"): add(week_monday(a), a, "tuần này (T2→hôm nay)")
    if take(r"tuan (roi|truoc|qua|vua roi)"):
        m0 = week_monday(a); add(m0 - timedelta(7), m0 - timedelta(1), "tuần trước (T2→CN)")
    if take(r"thang nay"): add(a.replace(day=1), a, "tháng này (1→hôm nay)")
    if not cmp_prev_month and take(r"thang (roi|truoc|qua|vua roi)"):
        y, mo = add_months(a.year, a.month, -1); add(*month_span(y, mo), "tháng trước")
    if take(r"quy nay"): q = (a.month - 1) // 3 + 1; add(quarter_span(a.year, q)[0], a, "quý này (đầu quý→hôm nay)")
    if take(r"quy (roi|truoc|qua)"):
        q = (a.month - 1) // 3 + 1; y, q = (a.year, q - 1) if q > 1 else (a.year - 1, 4); add(*quarter_span(y, q), "quý trước")
    if take(r"nam nay") and not any("đầu năm" in p.label or "1/1" in p.label for p in sp.periods): add(date(a.year, 1, 1), a, "năm nay (1/1→hôm nay)")
    if take(r"nam (ngoai|truoc|qua)") and not cmp_last_year: add(date(a.year - 1, 1, 1), date(a.year - 1, 12, 31), "năm ngoái")
    for m in take(r"nam (20\d\d)"): add(date(int(m[1]), 1, 1), date(int(m[1]), 12, 31), f"năm {m[1]}")

    # 6. N ngày/tuần/tháng gần nhất  |  N ngày tới
    for m in take(r"(\d+) (ngay|tuan|thang) (gan nhat|qua|vua roi|gan day|vua qua|truoc)"):
        n, u = int(m[1]), m[2]
        if u == "ngay": add(a - timedelta(n - 1), a, f"{n} ngày gần nhất (gồm hôm nay)")
        elif u == "tuan": add(a - timedelta(7 * n - 1), a, f"{n} tuần gần nhất (=7n ngày)"); sp.flags.append("assume_7n_days")
        else:
            y, mo = add_months(a.year, a.month, -n); add(clamp_day(y, mo, a.day) + timedelta(1), a, f"{n} tháng gần nhất"); sp.flags.append("rolling_months_ambiguous")
        if m[3] in ("qua", "vua qua", "truoc"): sp.flags.append("'N ngày qua' coi như 'gần nhất'")
    for m in take(r"(\d+) ngay (toi|nua|sap toi)"):
        n = int(m[1]); add(a, a + timedelta(n), f"{n} ngày tới = [D, D+N]")

    # 7. thứ trong tuần
    wd = {"hai": 0, "ba": 1, "tu": 2, "nam": 3, "sau": 4, "bay": 5, "2": 0, "3": 1, "4": 2, "5": 3, "6": 4, "7": 5}
    for m in take(r"(?:thu (hai|ba|tu|nam|sau|bay|[2-7])|(chu nhat|\bcn\b)) (tuan nay|tuan (?:roi|truoc))"):
        idx = 6 if m[2] else wd[m[1]]; base = week_monday(a) - (timedelta(7) if "nay" not in m[3] else timedelta(0))
        add(base + timedelta(idx), base + timedelta(idx), f"{m[0]}")
    if take(r"(?:thu (?:hai|ba|tu|nam|sau|bay|[2-7])|chu nhat) (?:vua roi|gan nhat)"): sp.flags.append("weekday_last_ambiguous")

    # 8. Tết
    if cal is not None:
        for m in take(r"(?:dip |mua )?tet(?: nam)? (20\d\d|nay)") + take(r"(?:dip |mua )?tet\b"):
            ys = m[1] if m.lastindex else "nay"; Y = a.year if ys in ("nay", None) else int(ys)
            tr = tet_range(cal, Y)
            if tr: add(tr[0], tr[1], f"Tết {Y}")
        if cmp_tet_ly:
            tr = tet_range(cal, a.year - 1); tr and add(tr[0], tr[1], f"Tết {a.year-1}", "baseline")
        if cmp_tet:
            mains = [p for p in sp.periods if p.label.startswith("Tết")]
            if mains:
                n = mains[0].days(); add(mains[0].start - timedelta(n), mains[0].start - timedelta(1), "ngay trước Tết (cùng số ngày)", "baseline")
        tet_years = {
            int(m[1])
            for p in sp.periods
            if p.label.startswith("Tết")
            for m in [re.search(r"\b(20\d\d)\b", p.label)]
            if m
        }
        if tet_years:                                                     # năm trong "Tết năm YYYY" không phải kỳ năm
            def is_tet_year_period(period):
                if period.label == "năm nay":
                    return a.year in tet_years
                match = re.fullmatch(r"năm (20\d\d)", period.label)
                return bool(match and int(match[1]) in tet_years)

            sp.periods = [p for p in sp.periods if not is_tet_year_period(p)]
    elif re.search(r"\btet\b", t): sp.flags.append("need_calendar_for_tet")
    if re.search(r"\bram\b|mung \d|am lich|giao thua|\bxuan\b", t): sp.flags.append("lunar_expression: tra calendar.csv (bỏ tháng nhuận trừ khi nói rõ)")
    if re.search(r"dau thang|cuoi thang|giua thang|nua (dau|cuoi)|cuoi tuan|dau tuan|dau quy|cuoi quy|cuoi nam|dau nam(?! nay)", t) and not any("đầu" in p.label for p in sp.periods):
        sp.flags.append("vague_part_of_period (rulebook không định nghĩa -> abstain nếu không chắc)")

    if not any("không ghi năm" in p.label or "/" in p.label for p in sp.periods):
        mw = month_word(raw)                                           # 'tháng tư', 'tháng mười hai'
        if mw:
            my = re.search(r"tháng [\wàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹ ]+? (?:năm )?(20\d\d)", raw)
            Y = int(my[1]) if my else (a.year if date(a.year, mw, 1) <= a else a.year - 1)
            sp.periods = [p for p in sp.periods if not (p.label == f"năm {Y}")]
            add(*month_span(Y, mw), f"{mw}/{Y} (tháng bằng chữ)"); sp.flags.append("month_word")

    # 9. kỳ so sánh (baseline)
    mains = [p for p in sp.periods if p.role == "main" and not p.label.startswith("Tết")] or [p for p in sp.periods if p.role == "main"]
    if mains and not sp.baseline():
        p = mains[0]
        if cmp_last_year:
            add(shift_year(p.start, -1) if p.days() < 28 or p.start.day != 1 else date(p.start.year - 1, p.start.month, 1),
                shift_year(p.end, -1) if not (p.end.day == last_day(p.end.year, p.end.month) and p.start.day == 1) else month_span(p.end.year - 1, p.end.month)[1],
                "cùng kỳ năm trước", "baseline")
        elif cmp_prev_month:
            full = p.start.day == 1 and p.end.day == last_day(p.end.year, p.end.month)
            y, mo = add_months(p.start.year, p.start.month, -1)
            s, e = month_span(y, mo)
            if full: add(s, e, "tháng liền trước (cả tháng)", "baseline")
            else: add(s, min(clamp_day(y, mo, p.end.day), e), "cùng kỳ tháng trước (cùng số ngày, cắt ở cuối tháng)", "baseline")
        elif cmp_prev_q:
            q = (p.start.month - 1) // 3 + 1; y, q = (p.start.year, q - 1) if q > 1 else (p.start.year - 1, 4); add(*quarter_span(y, q), "quý trước", "baseline")
        elif cmp_prev_w:
            m0 = p.start - timedelta(7); add(m0, m0 + timedelta(p.days() - 1), "tuần trước (cùng số ngày)", "baseline"); sp.flags.append("baseline_by_analogy")
    seen, out = set(), []
    for p in sp.periods:
        k = (p.start, p.end, p.role)
        if k not in seen: seen.add(k); out.append(p)
    sp.periods = out
    if any("kể từ" in p.label for p in sp.periods):
        for p in sp.periods:
            if p.label.startswith("ngày ") and p.days() == 1: p.role = "anchor"
    # 10. kẹp theo as_of: dữ liệu sau hôm nay coi như chưa xảy ra (trừ trạng thái huỷ/trả của đơn)
    for p in sp.periods:
        if p.role == "main" and not sp.stock and p.start <= a < p.end and not p.label.startswith("Tết"):
            sp.flags.append(f"period_extends_past_as_of:{p.label}")
    return sp

# ───────────────────────── phạm vi dữ liệu ─────────────────────────
def availability(p, data_start, data_end, as_of):
    """full | partial | outside | future.  'outside' => unanswerable (không phải 0).  partial => tính trên phần có."""
    hi = min(data_end, as_of) if as_of else data_end
    if p.end < data_start or p.start > hi: return "future" if p.start > as_of else "outside"
    return "full" if p.start >= data_start and p.end <= hi else "partial"

def effective(p, data_start, data_end, as_of):
    return max(p.start, data_start), min(p.end, data_end, as_of)

# ───────────────────────── B. ô ngày trong dữ liệu ─────────────────────────
_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$")
_DMY = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})(?:[ T,]+\d{1,2}[:h]\d{2}(?::\d{2})?)?$")
_THG = re.compile(r"^(?:th[uứ]\s*\w+,?\s*)?(\d{1,2})\s*(?:thg|th[áa]ng|tháng)\s*(\d{1,2}),?\s*(\d{4})$", re.I)
_DM = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})$")
_YMD8 = re.compile(r"^(20\d\d)(\d{2})(\d{2})$")
_SER = re.compile(r"^\d{5}(?:\.\d+)?$")

def excel_serial(x, base1904=False):
    return (datetime(1904, 1, 1) if base1904 else datetime(1899, 12, 30)) + timedelta(days=float(x))

def parse_cell_date(v, sheet_year=None, dayfirst=True):
    """Trả date hoặc None. KHÔNG bao giờ để pandas đoán cả cột: gọi từng ô.
    - ISO (kể cả có giờ) không bao giờ bị đảo ngày/tháng.
    - dd/mm/yyyy: mặc định dayfirst (kiểu VN); nếu phần đầu > 12 thì chắc chắn là ngày; nếu phần giữa > 12 thì là mm/dd.
    - 'dd/mm' không năm: lấy năm của SHEET chứa dòng đó (rulebook §8).
    - serial Excel: phần thập phân là giờ -> bỏ; chỉ nhận 30000..60000 (1982..2064).
    - số nguyên 10 chữ số / 13 chữ số: epoch giây / mili-giây, đổi sang UTC+7 (phòng thủ; chưa thấy trong train)."""
    if v is None: return None
    if isinstance(v, datetime): return v.date()
    if isinstance(v, date): return v
    s = str(v).strip().replace("\xa0", " ")
    if s in ("", "nan", "NaT", "None", "-"): return None
    try:
        m = _ISO.match(s)
        if m: return _mk(int(m[1]), int(m[2]), int(m[3]))
        m = _THG.match(s)
        if m: return _mk(int(m[3]), int(m[2]), int(m[1]))
        m = _DMY.match(s)
        if m:
            a_, b_, y = int(m[1]), int(m[2]), int(m[3]); y = y + 2000 if y < 100 else y
            if a_ > 12 and b_ <= 12: return _mk(y, b_, a_)
            if b_ > 12 and a_ <= 12: return _mk(y, a_, b_)
            return _mk(y, b_, a_) if dayfirst else _mk(y, a_, b_)
        m = _DM.match(s)
        if m and sheet_year: return _mk(int(sheet_year), int(m[2]), int(m[1]))
        m = _YMD8.match(s)
        if m: return _mk(int(m[1]), int(m[2]), int(m[3]))
        if _SER.match(s) and 30000 <= float(s) <= 60000: return excel_serial(s).date()
        if re.fullmatch(r"\d{10}|\d{13}", s):
            x = int(s) / (1000 if len(s) == 13 else 1); return (datetime(1970, 1, 1) + timedelta(seconds=x, hours=7)).date()
    except (ValueError, OverflowError):
        return None
    return None

def parse_time_of_day(v):
    """-> số giây từ 00:00 (hoặc None). Nhận 'HH:MM[:SS]', ISO có giờ, phần thập phân serial Excel."""
    s = str(v).strip()
    m = re.search(r"(\d{1,2})[:h](\d{2})(?::(\d{2}))?", s)
    if m: return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3] or 0)
    if _SER.match(s) and "." in s: return round((float(s) % 1) * 86400)
    return None

def in_shift(sec, shift):
    """ca sáng 06:00–13:59(:59), ca chiều 14:00–21:59(:59); 22:00–05:59 không thuộc ca nào."""
    if sec is None: return False
    return (6 * 3600 <= sec < 14 * 3600) if shift == "sang" else (14 * 3600 <= sec < 22 * 3600)
