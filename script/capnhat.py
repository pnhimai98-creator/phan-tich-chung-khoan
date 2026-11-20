"""
capnhat.py — Cập nhật dữ liệu thị trường cho trang Phân Tích Mã.
Chạy tự động mỗi ngày bằng GitHub Actions (.github/workflows/cap-nhat-du-lieu.yml),
hoặc chạy tay:  pip install -U vnstock pandas  &&  python script/capnhat.py --so-ma 20

Mỗi lần chạy:
  1. Lấy danh sách mã HOSE/HNX/UPCoM và ngành (vnstock, nguồn VCI).
  2. Lấy giá & khối lượng hôm nay của TẤT CẢ mã (bảng giá, gọi theo lô 50 mã).
  3. Cập nhật báo cáo tài chính + lịch sử giá 1 năm cho N mã cũ nhất (xoay vòng),
     vì BCTC chỉ đổi theo quý — sau khoảng 1 tuần là phủ hết thị trường.
  4. Tính lại P/E, P/B, vốn hóa theo giá mới nhất; biến động giá 1 tháng, 1 năm;
     khối lượng so với trung bình 20 phiên.
  5. Ghi data/du_lieu.json (trang web đọc tệp này) và data/du_lieu.csv.

Tệp lưu trữ: data/fundamentals.csv (chỉ số tài chính), data/price_history.csv (giá).
"""
import argparse, json, math, os, sys, time, unicodedata
from datetime import datetime, timedelta, timezone

import pandas as pd

VN = timezone(timedelta(hours=7))
DATA = "data"
F_FUND = os.path.join(DATA, "fundamentals.csv")
F_HIST = os.path.join(DATA, "price_history.csv")
F_JSON = os.path.join(DATA, "du_lieu.json")
F_CSV = os.path.join(DATA, "du_lieu.csv")
OUT_COLS = ["ticker", "name", "exchange", "sector", "price", "mcap", "pe", "pb", "roe", "roa",
            "rev_g", "np_g", "gross_m", "net_m", "de", "cfo_np", "div_y",
            "chg_1m", "chg_1y", "vol_ratio", "foreign"]
SLEEP = 3.2
STATS = {"loi": 0}


def log(*a):
    print(*a, flush=True)


def plain(s):
    s = unicodedata.normalize("NFD", str(s)).replace("đ", "d").replace("Đ", "D")
    return s.encode("ascii", "ignore").decode().lower().strip()


def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def flat(df):
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" | ".join(str(x) for x in c if str(x) not in ("nan", "")) for c in df.columns]
    return df


def col(df, *groups, exact=False):
    """Tìm tên cột chứa đủ các từ khóa (không dấu). exact: so khớp đoạn cuối sau '|'."""
    for g in groups:
        for c in df.columns:
            p = plain(c)
            last = p.split("|")[-1].strip()
            if exact and last in g:
                return c
            if not exact and all(k in p for k in g):
                return c
    return None


def find(row, groups, exclude=("truoc thay doi",)):
    for g in groups:
        for c in row.index:
            p = plain(c)
            if all(k in p for k in g) and not any(x in p for x in exclude):
                v = num(row[c])
                if v is not None:
                    return v
    return None


def pct(v):
    # Nguồn VCI trả tỷ lệ dạng thập phân (0,15 = 15%)
    if v is None:
        return None
    return v * 100 if abs(v) <= 1.5 else v


def norm_price(v):
    v = num(v)
    if v is None or v <= 0:
        return None
    return v / 1000 if v >= 1000 else v  # đồng → nghìn đồng


def safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except KeyboardInterrupt:
        raise
    except BaseException as e:  # vnstock có thể gọi sys.exit khi vượt giới hạn truy vấn
        STATS["loi"] += 1
        log(f"    ! {getattr(fn, '__name__', 'call')}: {type(e).__name__}: {str(e)[:200]}")
        return None


def sort_latest(df):
    df = flat(df)
    yc = col(df, ("nam", "year", "yearreport", "nam bao cao"), exact=True)
    qc = col(df, ("ky", "quarter", "lengthreport", "ky bao cao"), exact=True)
    by = [c for c in (yc, qc) if c]
    return df.sort_values(by, ascending=False) if by else df


def growth(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / abs(prev) * 100


# ---------------------------------------------------------------- danh sách mã
def get_listing():
    from vnstock import Listing
    ls = Listing(source="VCI")
    out = {}
    ex = safe(ls.symbols_by_exchange)
    if ex is not None and len(ex):
        ex = flat(ex)
        sc = col(ex, ("symbol", "ticker"), exact=True)
        ec = col(ex, ("exchange", "board", "comgroupcode"), exact=True)
        tc = col(ex, ("type",), exact=True)
        nc = col(ex, ("organ_name", "organ_short_name"), exact=True)
        for _, r in ex.iterrows():
            s = str(r[sc]).strip().upper()
            if len(s) != 3 or not s.isalnum():
                continue
            if tc and str(r[tc]).upper() not in ("STOCK", "CP", "CO PHIEU"):
                continue
            e = str(r[ec]).upper().replace("HSX", "HOSE") if ec else ""
            if ec and e not in ("HOSE", "HNX", "UPCOM"):
                continue
            out[s] = {"ticker": s, "exchange": e, "name": str(r[nc]) if nc and pd.notna(r[nc]) else "", "sector": ""}
    time.sleep(SLEEP)
    ind = safe(ls.symbols_by_industries)
    if ind is not None and len(ind):
        ind = flat(ind)
        sc = col(ind, ("symbol", "ticker"), exact=True)
        n2 = col(ind, ("icb_name2",), exact=True)
        n3 = col(ind, ("icb_name3",), exact=True)
        nc = col(ind, ("organ_name",), exact=True)
        for _, r in ind.iterrows():
            s = str(r[sc]).strip().upper()
            if s not in out:
                continue
            sec = r[n2] if n2 and pd.notna(r[n2]) else (r[n3] if n3 and pd.notna(r[n3]) else "")
            out[s]["sector"] = str(sec)
            if nc and not out[s]["name"] and pd.notna(r[nc]):
                out[s]["name"] = str(r[nc])
    time.sleep(SLEEP)
    return out


# ---------------------------------------------------------------- giá hôm nay
def board_prices(tickers):
    from vnstock import Trading
    tr = Trading(source="VCI")
    out = {}
    for i in range(0, len(tickers), 50):
        chunk = tickers[i:i + 50]
        df = safe(tr.price_board, chunk)
        time.sleep(SLEEP)
        if df is None or not len(df):
            continue
        df = flat(df)
        sc = col(df, ("symbol",), exact=True) or col(df, ("symbol",))
        pc = col(df, ("match_price",), exact=True) or col(df, ("close_price",), exact=True) or col(df, ("match_price",))
        rc = col(df, ("ref_price",), exact=True) or col(df, ("ref_price",))
        vc = col(df, ("accumulated_volume",), exact=True) or col(df, ("total_volume",), exact=True) or col(df, ("accumulated_volume",))
        if not sc:
            continue
        for _, r in df.iterrows():
            p = norm_price(r[pc]) if pc else None
            if p is None and rc:
                p = norm_price(r[rc])
            if p:
                out[str(r[sc]).strip().upper()] = (p, num(r[vc]) if vc else None)
        log(f"  bảng giá: {min(i + 50, len(tickers))}/{len(tickers)}")
    return out


# ---------------------------------------------------------------- tài chính từng mã
def fundamentals(sym, days=400):
    from vnstock import Vnstock
    st = safe(lambda: Vnstock().stock(symbol=sym, source="VCI"))
    if st is None:
        return None, None
    f = {"ticker": sym}

    r = safe(st.finance.ratio, period="year", lang="vi", dropna=True)
    time.sleep(SLEEP)
    if r is not None and len(r):
        row = sort_latest(r).iloc[0]
        f["eps"] = find(row, [["eps"]])
        f["bvps"] = find(row, [["bvps"], ["gia tri so sach"]])
        f["pe_ref"] = find(row, [["p/e"]])
        f["pb_ref"] = find(row, [["p/b"]])
        m = find(row, [["von hoa"], ["market", "cap"]])
        f["mcap_ref"] = m / 1e9 if m and m > 1e7 else m
        f["shares"] = find(row, [["so cp luu hanh"], ["outstanding", "share"]])
        if f["shares"] and f["shares"] > 1e5:  # cổ phiếu → triệu cổ phiếu
            f["shares"] = f["shares"] / 1e6
        f["roe"] = pct(find(row, [["roe"]]))
        f["roa"] = pct(find(row, [["roa"]]))
        f["gross_m"] = pct(find(row, [["bien loi nhuan gop"], ["gross", "margin"]]))
        f["net_m"] = pct(find(row, [["bien loi nhuan rong"], ["net", "margin"]]))
        f["de"] = find(row, [["no/vcsh"], ["debt", "equity"]])
        f["div_y"] = pct(find(row, [["ty suat co tuc"], ["dividend", "yield"]]))

    np_cur = None
    inc = safe(st.finance.income_statement, period="year", lang="vi", dropna=True)
    time.sleep(SLEEP)
    if inc is not None and len(inc) >= 2:
        inc = sort_latest(inc)
        a, b = inc.iloc[0], inc.iloc[1]
        rev_g = [["doanh thu thuan"], ["doanh thu"], ["thu nhap lai thuan"], ["tong thu nhap hoat dong"], ["revenue"]]
        np_g = [["loi nhuan sau thue cua co dong cua cong ty me"], ["co dong cua cong ty me"],
                ["loi nhuan sau thue"], ["net profit"]]
        f["rev_g"] = growth(find(a, rev_g), find(b, rev_g))
        np_cur = find(a, np_g)
        f["np_g"] = growth(np_cur, find(b, np_g))

    cf = safe(st.finance.cash_flow, period="year", lang="vi", dropna=True)
    time.sleep(SLEEP)
    if cf is not None and len(cf) and np_cur:
        row = sort_latest(cf).iloc[0]
        cfo = find(row, [["luu chuyen tien te rong tu cac hoat dong sxkd"],
                         ["luu chuyen tien thuan tu hoat dong kinh doanh"],
                         ["luu chuyen", "sxkd"], ["luu chuyen", "kinh doanh"], ["operating"]])
        if cfo is not None:
            ratio = cfo / np_cur
            f["cfo_np"] = ratio if abs(ratio) <= 20 else None  # loại bỏ khi lệch đơn vị

    hist = None
    end = datetime.now(VN).date()
    h = safe(st.quote.history, start=(end - timedelta(days=days)).isoformat(), end=end.isoformat(), interval="1D")
    time.sleep(SLEEP)
    if h is not None and len(h):
        h = flat(h)
        tc = col(h, ("time", "date", "trading_date"), exact=True)
        if tc and "close" in h and "volume" in h:
            hist = pd.DataFrame({"date": pd.to_datetime(h[tc]).dt.strftime("%Y-%m-%d"),
                                 "ticker": sym,
                                 "close": h["close"].map(norm_price),
                                 "volume": pd.to_numeric(h["volume"], errors="coerce")}).dropna(subset=["close"])
    return f, hist


# ---------------------------------------------------------------- tổng hợp
def clean(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = num(v)
        return None if v is None else round(v, 4)
    if v is None or (isinstance(v, float)):
        return None
    return str(v)


def txt(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip() and v.lower() != "nan":
            return v.strip()
    return ""


def build(listing, fund, hist):
    fund = {r["ticker"]: r for r in fund.to_dict("records")} if len(fund) else {}
    groups = {t: g.sort_values("date") for t, g in hist.groupby("ticker")} if len(hist) else {}
    tickers = sorted(set(listing) | set(fund) | set(groups))
    one_year_ago = (datetime.now(VN).date() - timedelta(days=365)).isoformat()
    rows = []
    for t in tickers:
        m, f, g = listing.get(t, {}), fund.get(t, {}), groups.get(t)
        o = {"ticker": t, "name": txt(m.get("name"), f.get("name")), "exchange": txt(m.get("exchange"), f.get("exchange")),
             "sector": txt(m.get("sector"), f.get("sector")) or "Khác"}
        price = None
        if g is not None and len(g):
            c, v = g["close"].astype(float).tolist(), g["volume"].astype(float).tolist()
            price = c[-1]
            if len(c) >= 22 and c[-22]:
                o["chg_1m"] = (c[-1] / c[-22] - 1) * 100
            old = g[g["date"] >= one_year_ago]
            if len(old) and len(g) > 200 and float(old["close"].iloc[0]):
                o["chg_1y"] = (c[-1] / float(old["close"].iloc[0]) - 1) * 100
            if len(v) >= 21:
                avg = sum(x for x in v[-21:-1] if x == x) / 20
                o["vol_ratio"] = v[-1] / avg if avg else None
        o["price"] = price
        ref = num(f.get("ref_price"))
        scale = price / ref if price and ref else None
        eps, bvps = num(f.get("eps")), num(f.get("bvps"))
        pe = price * 1000 / eps if price and eps else None
        if pe is None or abs(pe) > 1000 or abs(pe) < 0.3:
            pe = num(f.get("pe_ref")) * scale if num(f.get("pe_ref")) and scale else num(f.get("pe_ref"))
        o["pe"] = pe
        pb = price * 1000 / bvps if price and bvps and bvps > 0 else None
        if pb is None or pb > 100 or pb < 0.02:
            pb = num(f.get("pb_ref")) * scale if num(f.get("pb_ref")) and scale else num(f.get("pb_ref"))
        o["pb"] = pb
        sh = num(f.get("shares"))
        o["mcap"] = sh * price if sh and price else (num(f.get("mcap_ref")) * scale if num(f.get("mcap_ref")) and scale else num(f.get("mcap_ref")))
        for k in ["roe", "roa", "rev_g", "np_g", "gross_m", "net_m", "de", "cfo_np", "div_y"]:
            o[k] = num(f.get(k))
        if o["price"] is None and all(o.get(k) is None for k in ["pe", "roe", "np_g"]):
            continue
        rows.append({k: clean(o.get(k)) for k in OUT_COLS})
    return rows


def main():
    global SLEEP
    ap = argparse.ArgumentParser()
    ap.add_argument("--so-ma", type=int, default=250, help="Số mã cập nhật BCTC trong lần chạy này")
    ap.add_argument("--phut-toi-da", type=float, default=300, help="Giới hạn thời gian cho phần BCTC (phút)")
    ap.add_argument("--nghi", type=float, default=3.2, help="Số giây nghỉ giữa các truy vấn")
    a = ap.parse_args()
    SLEEP = a.nghi
    t0 = time.time()
    os.makedirs(DATA, exist_ok=True)
    today = datetime.now(VN).date().isoformat()

    try:
        import vnstock
        log(f"vnstock {getattr(vnstock, '__version__', '?')}")
    except ImportError:
        sys.exit("Chưa cài vnstock: pip install -U vnstock pandas")

    fund = pd.read_csv(F_FUND, dtype={"ticker": str}) if os.path.exists(F_FUND) else pd.DataFrame(columns=["ticker", "updated"])
    hist = pd.read_csv(F_HIST, dtype={"ticker": str}) if os.path.exists(F_HIST) else pd.DataFrame(columns=["date", "ticker", "close", "volume"])

    log("1) Danh sách mã…")
    listing = get_listing()
    if not listing and len(fund):
        listing = {r["ticker"]: {k: r.get(k, "") for k in ("ticker", "name", "exchange", "sector")} for r in fund.to_dict("records")}
    tickers = sorted(listing)
    log(f"   {len(tickers)} mã")
    if not tickers:
        sys.exit("Không lấy được danh sách mã. Xem lỗi phía trên.")

    log("2) Giá hôm nay…")
    bp = board_prices(tickers)
    new = [{"date": today, "ticker": t, "close": p, "volume": v} for t, (p, v) in bp.items() if v and v > 0]
    log(f"   {len(bp)} mã có giá, {len(new)} mã có giao dịch hôm nay")
    if new:
        hist = pd.concat([hist, pd.DataFrame(new)], ignore_index=True)

    log(f"3) Báo cáo tài chính (tối đa {a.so_ma} mã)…")
    done = dict(zip(fund["ticker"].astype(str), fund["updated"].astype(str))) if len(fund) else {}
    todo = sorted(tickers, key=lambda t: done.get(t, ""))[: a.so_ma]
    recs = {r["ticker"]: r for r in fund.to_dict("records")} if len(fund) else {}
    parts = []
    for i, s in enumerate(todo, 1):
        if (time.time() - t0) / 60 > a.phut_toi_da:
            log("   Hết thời gian cho phép, dừng — lần chạy sau sẽ tiếp tục.")
            break
        log(f"   [{i}/{len(todo)}] {s}")
        f, h = fundamentals(s)
        if h is not None and len(h):
            parts.append(h)
        if f and len(f) > 1:
            f.update({k: listing[s].get(k, "") for k in ("name", "exchange", "sector")})
            f["updated"] = today
            f["ref_price"] = float(h["close"].iloc[-1]) if h is not None and len(h) else (bp.get(s, (None,))[0])
            recs[s] = f
        if i % 25 == 0:
            pd.DataFrame(list(recs.values())).to_csv(F_FUND, index=False)

    if parts:
        hist = pd.concat([hist] + parts, ignore_index=True)
    if len(hist):
        hist = hist.drop_duplicates(["date", "ticker"], keep="last")
        cutoff = (datetime.now(VN).date() - timedelta(days=380)).isoformat()
        hist = hist[hist["date"] >= cutoff].sort_values(["ticker", "date"])
        hist.to_csv(F_HIST, index=False, float_format="%.4f")
    fund = pd.DataFrame(list(recs.values()))
    if len(fund):
        fund.to_csv(F_FUND, index=False)

    log("4) Tổng hợp…")
    rows = build(listing, fund, hist)
    meta = {"updated": today, "generated_at": datetime.now(VN).isoformat(timespec="minutes"),
            "source": f"vnstock (VCI) · cập nhật tự động {today}",
            "coverage": {"tong_ma": len(tickers), "co_bctc": int(len(fund)), "co_gia": len(set(hist["ticker"])) if len(hist) else 0},
            "rows": rows}
    with open(F_JSON, "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, separators=(",", ":"))
    pd.DataFrame(rows, columns=OUT_COLS).to_csv(F_CSV, index=False, encoding="utf-8-sig")
    log(f"Xong: {len(rows)} mã · BCTC {len(fund)}/{len(tickers)} · lỗi {STATS['loi']} · {(time.time() - t0) / 60:.0f} phút")
    if not rows:
        sys.exit("Không tạo được dòng dữ liệu nào.")


if __name__ == "__main__":
    main()
