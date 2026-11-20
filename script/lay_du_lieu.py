"""
lay_du_lieu.py — Lấy dữ liệu tất cả mã HOSE, HNX, UPCoM thành tệp CSV
để nạp vào trang "Phân Tích Mã".

Cài đặt (một lần):   pip install -U vnstock pandas
Chạy:                python lay_du_lieu.py
Chạy thử 30 mã:      python lay_du_lieu.py --limit 30
Chỉ sàn HOSE:        python lay_du_lieu.py --exchange HOSE

Kết quả: tệp du_lieu_<ngày>.csv cùng thư mục. Nếu bị ngắt giữa chừng,
chạy lại sẽ tiếp tục từ mã chưa lấy (đọc tệp tạm .partial.csv).

Lưu ý:
- Script dùng thư viện mã nguồn mở vnstock (nguồn VCI). Tên hàm/cột của
  vnstock có thể đổi giữa các phiên bản; script đã viết để dò tên cột linh
  hoạt, nhưng nếu lỗi, hãy cập nhật vnstock (pip install -U vnstock).
- Nguồn dữ liệu giới hạn số yêu cầu mỗi phút, nên toàn bộ ~1.600 mã có thể
  mất vài giờ. Có thể tăng --sleep nếu bị chặn.
- Tuân thủ điều khoản sử dụng của nguồn dữ liệu; chỉ dùng cho mục đích cá nhân.
"""
import argparse, os, sys, time, unicodedata
from datetime import date, timedelta

import pandas as pd

try:
    from vnstock import Listing, Vnstock
except ImportError:
    sys.exit("Chưa cài vnstock. Chạy: pip install -U vnstock pandas")

COLS = ["ticker", "name", "exchange", "sector", "price", "mcap", "pe", "pb", "roe", "roa",
        "rev_g", "np_g", "gross_m", "net_m", "de", "cfo_np", "div_y",
        "chg_1m", "chg_1y", "vol_ratio", "foreign"]

# Từ khóa để dò cột trong bảng chỉ số tài chính của vnstock (không dấu, chữ thường)
KEYS = {
    "pe": [["p/e"]], "pb": [["p/b"]],
    "roe": [["roe"]], "roa": [["roa"]],
    "gross_m": [["bien loi nhuan gop"], ["gross", "margin"]],
    "net_m": [["bien loi nhuan rong"], ["net", "margin"]],
    "de": [["no/vcsh"], ["debt", "equity"], ["no", "von chu so huu"]],
    "div_y": [["ty suat co tuc"], ["dividend"]],
    "mcap": [["von hoa"], ["market", "capital"]],
    "eps": [["eps"]],
}


def plain(s):
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    return s.lower().replace("đ", "d").strip()


def flat_cols(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" | ".join(str(x) for x in c if str(x) != "nan") for c in df.columns]
    return df


def find(row, keys):
    for group in keys:
        for col in row.index:
            p = plain(col)
            if all(k in p for k in group):
                try:
                    v = float(row[col])
                    if pd.notna(v):
                        return v
                except (TypeError, ValueError):
                    pass
    return None


def pct(v):
    """vnstock thường trả tỷ lệ dạng 0,15 = 15%: đổi sang %."""
    if v is None:
        return None
    return v * 100 if abs(v) <= 3 else v


def growth(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / abs(prev) * 100


def latest_two(df, keys):
    """Lấy giá trị năm gần nhất và năm trước cho một chỉ tiêu."""
    if df is None or df.empty:
        return None, None
    df = flat_cols(df.copy())
    ycol = next((c for c in df.columns if "nam" in plain(c) or "year" in plain(c)), None)
    if ycol:
        df = df.sort_values(ycol, ascending=False)
    vals = [find(r, keys) for _, r in df.head(2).iterrows()]
    vals += [None] * (2 - len(vals))
    return vals[0], vals[1]


def one(sym, meta, sleep):
    out = {"ticker": sym, **meta}
    st = Vnstock().stock(symbol=sym, source="VCI")

    # 1) Chỉ số tài chính năm
    try:
        r = flat_cols(st.finance.ratio(period="year", lang="vi", dropna=True))
        ycol = next((c for c in r.columns if "nam" in plain(c) or "year" in plain(c)), None)
        if ycol:
            r = r.sort_values(ycol, ascending=False)
        row = r.iloc[0]
        for k in ["pe", "pb", "mcap"]:
            out[k] = find(row, KEYS[k])
        for k in ["roe", "roa", "gross_m", "net_m", "div_y"]:
            out[k] = pct(find(row, KEYS[k]))
        out["de"] = find(row, KEYS["de"])
        if out.get("mcap") and out["mcap"] > 1e6:  # đồng → tỷ đồng
            out["mcap"] = out["mcap"] / 1e9
    except Exception as e:
        print(f"  {sym}: không lấy được chỉ số ({e.__class__.__name__})")
    time.sleep(sleep)

    # 2) Kết quả kinh doanh → tăng trưởng doanh thu, lợi nhuận
    np_cur = None
    try:
        inc = st.finance.income_statement(period="year", lang="vi", dropna=True)
        rv, rv0 = latest_two(inc, [["doanh thu thuan"], ["doanh thu"], ["revenue"]])
        np_cur, np0 = latest_two(inc, [["loi nhuan sau thue cua co dong cua cong ty me"],
                                        ["loi nhuan sau thue"], ["net profit"]])
        out["rev_g"], out["np_g"] = growth(rv, rv0), growth(np_cur, np0)
    except Exception as e:
        print(f"  {sym}: không lấy được KQKD ({e.__class__.__name__})")
    time.sleep(sleep)

    # 3) Lưu chuyển tiền tệ → dòng tiền KD / lợi nhuận
    try:
        cf = st.finance.cash_flow(period="year", lang="vi", dropna=True)
        cfo, _ = latest_two(cf, [["luu chuyen tien thuan tu hoat dong kinh doanh"],
                                 ["luu chuyen tien", "kinh doanh"], ["operating"]])
        if cfo is not None and np_cur:
            out["cfo_np"] = cfo / np_cur
    except Exception as e:
        print(f"  {sym}: không lấy được LCTT ({e.__class__.__name__})")
    time.sleep(sleep)

    # 4) Giá: giá đóng cửa, biến động 1 tháng / 1 năm, khối lượng so TB20
    try:
        h = st.quote.history(start=(date.today() - timedelta(days=380)).isoformat(),
                             end=date.today().isoformat(), interval="1D")
        if h is not None and len(h) > 25:
            c, v = h["close"].astype(float), h["volume"].astype(float)
            out["price"] = c.iloc[-1]
            out["chg_1m"] = (c.iloc[-1] / c.iloc[-22] - 1) * 100
            out["chg_1y"] = (c.iloc[-1] / c.iloc[0] - 1) * 100
            avg20 = v.iloc[-21:-1].mean()
            out["vol_ratio"] = v.iloc[-1] / avg20 if avg20 else None
    except Exception as e:
        print(f"  {sym}: không lấy được giá ({e.__class__.__name__})")
    time.sleep(sleep)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="", help="HOSE / HNX / UPCOM (bỏ trống = tất cả)")
    ap.add_argument("--limit", type=int, default=0, help="Chỉ lấy N mã đầu (để chạy thử)")
    ap.add_argument("--sleep", type=float, default=1.2, help="Số giây nghỉ giữa các yêu cầu")
    a = ap.parse_args()

    ls = Listing(source="VCI")
    ex = flat_cols(ls.symbols_by_exchange())
    ex = ex[ex.get("type", pd.Series(["STOCK"] * len(ex))).astype(str).str.upper().isin(["STOCK", "CP"])] if "type" in ex else ex
    excol = next((c for c in ex.columns if plain(c) in ("exchange", "board", "san")), None)
    if a.exchange and excol:
        ex = ex[ex[excol].astype(str).str.upper().str.replace("HSX", "HOSE") == a.exchange.upper()]
    try:
        ind = flat_cols(ls.symbols_by_industries())
        ind = ind.set_index("symbol")
    except Exception:
        ind = pd.DataFrame()

    syms = [s for s in ex["symbol"].astype(str) if len(s) == 3]
    if a.limit:
        syms = syms[: a.limit]

    part = "du_lieu.partial.csv"
    done = pd.read_csv(part) if os.path.exists(part) else pd.DataFrame(columns=COLS)
    have = set(done["ticker"].astype(str))
    rows = done.to_dict("records")
    todo = [s for s in syms if s not in have]
    print(f"Tổng {len(syms)} mã, đã có {len(have & set(syms))}, cần lấy {len(todo)}.")

    exmap = dict(zip(ex["symbol"].astype(str), ex[excol].astype(str))) if excol else {}
    for i, s in enumerate(todo, 1):
        meta = {"exchange": exmap.get(s, "").replace("HSX", "HOSE")}
        if not ind.empty and s in ind.index:
            r = ind.loc[s]
            r = r.iloc[0] if isinstance(r, pd.DataFrame) else r
            meta["name"] = r.get("organ_name", "")
            meta["sector"] = r.get("icb_name2", "") or r.get("icb_name3", "")
        print(f"[{i}/{len(todo)}] {s}")
        rows.append(one(s, meta, a.sleep))
        if i % 10 == 0:
            pd.DataFrame(rows).reindex(columns=COLS).to_csv(part, index=False)

    df = pd.DataFrame(rows).reindex(columns=COLS)
    out = f"du_lieu_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False, float_format="%.4f", encoding="utf-8-sig")
    if os.path.exists(part):
        os.remove(part)
    print(f"Xong: {out} ({len(df)} mã). Mở trang Phân Tích Mã → Nạp dữ liệu → chọn tệp này.")


if __name__ == "__main__":
    main()
