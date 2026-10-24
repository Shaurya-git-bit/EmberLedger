import hashlib
import json
import pickle
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

APP = Path(__file__).parent / "app_data"
P = json.loads((APP / "params.json").read_text())
FEATURES, THRESH, DUP_THRESH, LB = P["FEATURES"], P["THRESH"], P["DUP_THRESH"], P["LB"]
LOCAL_TZ, MIN_FP_ROWS = P["LOCAL_TZ"], P["MIN_FP_ROWS"]
MERGE_GAP_SEC, MIN_EVENT_SEC = P["MERGE_GAP_SEC"], P["MIN_EVENT_SEC"]
WINDOWS, RENAME, FLICKER_STD = P["WINDOWS"], P["RENAME"], P["FLICKER_STD"]

MODEL = joblib.load(APP / "model.joblib")
with open(APP / "library.pkl", "rb") as f:
    LIBRARY = pickle.load(f)


def local_dt(sec):
    return pd.Timestamp(sec, unit="s", tz="UTC").tz_convert(LOCAL_TZ)


def load_household(path):
    """Read one SUM file, drop unusable rows, return a frame indexed by local time."""
    df = pd.read_csv(path).rename(columns=str.strip).rename(columns=RENAME)
    for col in ["ts", "temp", "amb_temp", "humidity", "error"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    keep = df["ts"].between(1e9, 4e9) & (df["error"].fillna(0) == 0) & df["temp"].notna()
    df = df[keep].sort_values("ts").drop_duplicates("ts")
    idx = pd.DatetimeIndex(pd.to_datetime(df["ts"], unit="s", utc=True))
    df.index = idx.tz_convert(LOCAL_TZ).rename("time")
    return df[["ts", "temp", "amb_temp", "humidity"]]


def build_features(df):
    """Causal features: each row uses only itself and earlier rows."""
    s = df["temp"]
    f = pd.DataFrame(index=df.index)
    f["temp"] = s
    f["temp_minus_amb"] = s - df["amb_temp"]
    f["humidity"] = df["humidity"]
    f["hour"] = df.index.hour
    dt = df["ts"].diff()
    f["dt_prev"] = dt
    f["slope"] = s.diff() / dt.where(dt > 0)
    for w in WINDOWS:
        roll = s.rolling(w)
        f[f"max_{w}"] = roll.max()
        f[f"min_{w}"] = roll.min()
        f[f"mean_{w}"] = roll.mean()
        f[f"std_{w}"] = roll.std()
        f[f"n_rows_{w}"] = roll.count()
        f[f"drop_from_max_{w}"] = f[f"max_{w}"] - s
    f["above_24h_min"] = s - s.rolling("24h").min()
    return f


def extract_events(flags, secs):
    """Group flagged rows into (start_sec, end_sec) events."""
    pos = np.flatnonzero(flags)
    if pos.size == 0:
        return []
    events, start, prev = [], pos[0], pos[0]
    for p in pos[1:]:
        if secs[p] - secs[prev] > MERGE_GAP_SEC:
            events.append((secs[start], secs[prev]))
            start = p
        prev = p
    events.append((secs[start], secs[prev]))
    return [(a, b) for a, b in events if b - a >= MIN_EVENT_SEC]


def n_rows(ts, a, b):
    return int(((ts >= a) & (ts <= b)).sum())


def row_seq(ts, temp, a, b):
    """Row-to-row temperature changes: the flicker pattern a copy would reproduce."""
    return np.diff(temp[(ts >= a) & (ts <= b)])


def best_match(q, r):
    """Highest Pearson correlation between the shorter sequence and any equal-length window of the longer."""
    short, long_ = (q, r) if len(q) <= len(r) else (r, q)
    win = sliding_window_view(long_, len(short))
    z_win = (win - win.mean(1, keepdims=True)) / (win.std(1, keepdims=True) + 1e-9)
    z_short = (short - short.mean()) / (short.std() + 1e-9)
    return float((z_win @ z_short).max() / len(short))


def is_duplicate(seq, key):
    return any(best_match(seq, r["seq"]) >= DUP_THRESH for r in LIBRARY if r["key"] != key)


def event_stats(ts, temp, a, b):
    t = temp[(ts >= a) & (ts <= b)]
    d = np.diff(t)
    return {"peak": t.max(), "dur_min": (b - a) / 60, "flicker": d.std() if len(d) > 2 else np.nan}


def plausible(st_):
    ok = st_["peak"] >= LB["peak"] and st_["dur_min"] >= LB["dur_min"]
    if not np.isnan(st_["flicker"]):
        ok = ok and st_["flicker"] >= LB["flicker"]
    return bool(ok)


def event_hash(ts, temp, a, b):
    m = (ts >= a) & (ts <= b)
    return hashlib.sha256(np.round(temp[m] * 4).astype(np.int64).tobytes()).hexdigest()


def raw_hash(day_df):
    """What the sensor signs: the day's raw timestamps and thermocouple readings."""
    return hashlib.sha256(
        day_df["ts"].to_numpy().astype(np.int64).tobytes()
        + np.round(day_df["temp"].to_numpy() * 4).astype(np.int64).tobytes()).digest()


def day_rows(df, date):
    return df[np.array(df.index.date) == date]


def analyse_file(df, hid):
    """Model, plausibility gate and duplicate check for every day of one stove file."""
    X = build_features(df)
    prob = MODEL.predict_proba(X[FEATURES])[:, 1]
    pred = (prob >= THRESH).astype(int)
    ts, temp = df["ts"].to_numpy(), df["temp"].to_numpy()
    days = {d: {"status": "No cooking detected", "events": [], "hash": None}
            for d in sorted(set(df.index.date))}
    for a, b in extract_events(pred, ts):
        date = local_dt(a).date()
        st_ = event_stats(ts, temp, a, b)
        dup = n_rows(ts, a, b) >= MIN_FP_ROWS and is_duplicate(row_seq(ts, temp, a, b), (hid, date))
        days[date]["events"].append({
            "start": local_dt(a), "end": local_dt(b),
            "peak": float(st_["peak"]), "dur_min": float(st_["dur_min"]),
            "flicker": None if np.isnan(st_["flicker"]) else float(st_["flicker"]),
            "plausible": plausible(st_), "duplicate": bool(dup),
            "hash": event_hash(ts, temp, a, b)})
    for d in days.values():
        good = [e for e in d["events"] if e["plausible"] and not e["duplicate"]]
        if good:
            d["status"], d["hash"] = "Verified", good[0]["hash"]
        elif d["events"]:
            d["status"] = ("Rejected: not realistic cooking"
                           if not any(e["plausible"] for e in d["events"])
                           else "Rejected: copied cooking trace")
    return days, X, prob


def inject_fake(full, date, seed=7):
    """Replace 12:00 onward on one day with a fake heating curve, logged like the real sensor."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp(date).tz_localize(LOCAL_TZ).timestamp() + 12 * 3600
    amb, hum = float(full["amb_temp"].median()), float(full["humidity"].median())
    t_act = np.cumsum(rng.integers(60, 62, size=45))               # one row per ~60 s while active
    temp_act = amb + (550 - amb) * (1 - np.exp(-t_act / 180)) + rng.normal(0, FLICKER_STD, t_act.size)
    t_cool = t_act[-1] + 300 * np.arange(1, 13)                    # sparse cooldown rows
    temp_cool = amb + (temp_act[-1] - amb) * np.exp(-(t_cool - t_act[-1]) / 1200)
    temp = np.round(np.maximum(np.r_[temp_act, temp_cool], amb) * 4) / 4   # thermocouple steps of 0.25 C
    ts = np.round(start + np.r_[t_act, t_cool])
    idx = pd.DatetimeIndex(pd.to_datetime(ts, unit="s", utc=True)).tz_convert(LOCAL_TZ).rename("time")
    fake = pd.DataFrame({"ts": ts, "temp": temp, "amb_temp": amb, "humidity": hum}, index=idx)
    kept = full[(full["ts"] < start) | (full["ts"] > ts[-1])]
    return pd.concat([kept, fake]).sort_index().drop_duplicates("ts")