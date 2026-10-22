"""themes.json の上場銘柄の株価を Yahoo Finance から取得し、data.json を書き出す。

取得に失敗した銘柄は、前回の data.json の値をそのまま残す（errors に記録）。
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def pct(now, base):
    if base is None or base == 0 or math.isnan(base):
        return None
    return round((now / base - 1) * 100, 2)


def close_on_or_before(s, when):
    """when 以前で最後の終値。履歴が when より新しくしか無ければ None。"""
    past = s[s.index <= when]
    return None if past.empty else float(past.iloc[-1])


def metrics(s):
    s = s.dropna()
    if len(s) < 2:
        return None
    last_day = s.index[-1]
    last, prev = float(s.iloc[-1]), float(s.iloc[-2])
    first_day = s.index[0]
    five_years_ago = last_day - pd.DateOffset(years=5)
    prev_year_end = s[s.index < pd.Timestamp(year=last_day.year, month=1, day=1)]
    return {
        "p": round(last, 2),
        "ch": pct(last, prev),
        "m1": pct(last, close_on_or_before(s, last_day - pd.DateOffset(months=1))),
        "ytd": pct(last, None if prev_year_end.empty else float(prev_year_end.iloc[-1])),
        "yr": pct(last, close_on_or_before(s, last_day - pd.DateOffset(years=1))),
        # 上場（または履歴の開始）が5年以内なら5年騰落は出さない
        "y5": None if first_day > five_years_ago + pd.Timedelta(days=7)
        else pct(last, close_on_or_before(s, five_years_ago)),
        "asof": last_day.strftime("%Y-%m-%d"),
    }


def main():
    themes = load_json(ROOT / "themes.json", None)
    old = load_json(ROOT / "data.json", {"quotes": {}})
    listed = {cid: c[4] for cid, c in themes["companies"].items() if c[1]}

    opts = dict(period="6y", interval="1d", auto_adjust=False, group_by="ticker", progress=False)
    hist = yf.download(list(listed.values()), threads=True, **opts)

    def frame(ticker):
        try:
            return hist[ticker]
        except KeyError:
            return pd.DataFrame(columns=["Open", "Close"])

    # 並列取得はキャッシュDBの衝突（database is locked）で落ちることがあるので、欠けた銘柄だけ1件ずつ取り直す
    retry = {}
    for ticker in listed.values():
        if frame(ticker)["Close"].dropna().size < 2:
            try:
                retry[ticker] = yf.download(ticker, threads=False, **opts)[ticker]
            except Exception:
                pass

    quotes, errors = {}, []
    for cid, ticker in listed.items():
        df = retry[ticker] if ticker in retry else frame(ticker)
        # 複数市場をまとめて取ると他市場の営業日の空行が混ざるので、取引のあった日だけ残す
        s = df[df["Open"].notna() | df["Close"].notna()]["Close"].copy()
        try:
            fi = yf.Ticker(ticker).fast_info
        except Exception:
            fi = None
        # Yahoo は直近の取引日の終値だけ空で返すことがあるので、最新値で埋める
        if fi is not None and len(s) and pd.isna(s.iloc[-1]):
            try:
                s.iloc[-1] = float(fi["last_price"])
            except Exception:
                pass
        m = metrics(s)
        if m is None:
            errors.append(ticker)
            if cid in old["quotes"]:
                quotes[cid] = old["quotes"][cid]
            continue
        try:
            m["mc"] = float(fi["market_cap"])
        except Exception:
            m["mc"] = old["quotes"].get(cid, {}).get("mc")
            errors.append(f"{ticker}(時価総額)")
        quotes[cid] = m

    asof = lambda cid: quotes.get(cid, {}).get("asof")
    data = {
        "updated": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "asof_jp": asof("toyota"),
        "asof_us": asof("aapl"),
        "quotes": quotes,
        "errors": errors,
    }
    (ROOT / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"取得 {len(quotes)}/{len(listed)} 銘柄、失敗 {len(errors)} 件: {errors}")


if __name__ == "__main__":
    main()
