import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
import streamlit as st
from eth_account import Account
from eth_utils import keccak

import pipeline as pl
from chain import Chain, day_number

st.set_page_config(page_title="Proof-of-Use", layout="wide")
STOVES = ["SUM_18", "SUM_21"]


@st.cache_resource
def get_chain():
    return Chain(pl.APP / "ProofOfUse.sol", STOVES, pl.P["sensor_keys"], pl.P["baseline_by_stove"])


@st.cache_resource
def get_data():
    return {s: pl.load_household(pl.APP / f"{s}.csv") for s in STOVES}


@st.cache_resource
def get_explainer():
    return shap.TreeExplainer(pl.MODEL)


@st.cache_resource
def base_analysis(hid):
    return pl.analyse_file(get_data()[hid], hid)


DFS = get_data()
BASE = {s: base_analysis(s) for s in STOVES}
verified_now = {(s, str(d)) for s in STOVES for d, v in BASE[s][0].items() if v["status"] == "Verified"}
expected = {(r["stove_id"], r["date"])
            for r in json.loads((pl.APP / "verified_days.json").read_text())}

with st.sidebar:
    st.header("Proof-of-Use")
    if verified_now == expected:
        st.success(f"Pipeline reproduces the notebook: {len(expected)} verified stove-days")
    else:
        st.warning(f"Differs from notebook. Only here: {sorted(verified_now - expected)} | "
                   f"only in notebook: {sorted(expected - verified_now)}")
    if st.button("Reset chain"):
        get_chain.clear()
        st.session_state.pop("attack_results", None)
        st.rerun()

CH = get_chain()
issued = {(e["stove"], e["date"]) for e in CH.log}
short = lambda h: h[:10] + "…"


def plot_day(df, prob, date, events):
    day = np.array(df.index.date) == date
    fig, ax = plt.subplots(figsize=(11, 3.2))
    ax.plot(df.index[day], df["temp"].to_numpy()[day], color="black", lw=0.9, marker="o", ms=2)
    for e in events:
        ax.axvspan(e["start"], e["end"], color="orange", alpha=0.35)
    ax.set_ylabel("Thermocouple (C)")
    ax2 = ax.twinx()
    ax2.plot(df.index[day], prob[day], color="tab:blue", lw=0.8, alpha=0.7)
    ax2.axhline(pl.THRESH, color="tab:blue", ls="--", lw=0.6)
    ax2.set_ylim(0, 1)
    ax2.set_ylabel("Cooking probability")
    st.pyplot(fig)
    plt.close(fig)


st.title("Proof-of-Use")
st.caption("Cookstove usage signed by the stove's sensor, checked by AI, and enforced by a smart contract.")
tab_contract, tab_verify, tab_tamper, tab_pay = st.tabs(
    ["Smart contract", "Check a day", "Fraud attempt", "Payments and impact"])

# ---------------------------------------------------------------- Contract
with tab_contract:
    st.graphviz_chart("""
    digraph {
      rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#f2f2f2", fontname="Helvetica"];
      Sensor [label="Stove sensor\\nseals each day's data"];
      ML [label="AI + sanity check\\nproposes a cooking day"];
      Contract [label="Smart contract\\nchecks the seal · links every day\\none credit per day · holds payment\\nwaiting period · deposit penalty", fillcolor="#ffd966"];
      Pay [label="Household payment"];
      Sensor -> ML [label="data"]; Sensor -> Contract [label="seal"];
      ML -> Contract [label="cooking proof"]; Contract -> Pay;
    }""")

    cols = st.columns(len(STOVES) + 1)
    cols[0].metric("Payment pool (ETH)", CH.pool())
    for col, s in zip(cols[1:], STOVES):
        col.metric(f"{s}: credits / deposit (ETH)", f"{sum(e['stove'] == s for e in CH.log)} / {CH.stake(s)}")
    st.code("\n".join(f"{s} latest record: {CH.head(s).hex()}" for s in STOVES))
    st.code(f"contract address: {CH.c.address}\nlatest block: {CH.w3.eth.block_number}")

    st.markdown("**Rules the contract enforces**")
    st.dataframe(pd.DataFrame([
        ("Data must be sealed by the stove's own sensor", "Fake sensor, data edited after sealing", "bad sensor signature"),
        ("Every day links to the day before", "Backfilling or rewriting history", "chain broken"),
        ("One credit per stove-day", "Claiming a day twice", "day already issued"),
        ("Each cooking session counts once", "Reusing an old cooking record on a new day", "duplicate event"),
        ("At most 2 sessions per day", "Padding a day with extra sessions", "event cap"),
    ], columns=["Rule", "Fraud it stops", "Message from the contract"]), hide_index=True)

    if st.button("Issue all verified days", type="primary"):
        for s in STOVES:
            for d, v in BASE[s][0].items():
                if v["status"] == "Verified" and (s, d) not in issued:
                    CH.attempt(s, day_number(d), [bytes.fromhex(v["hash"])],
                               pl.raw_hash(pl.day_rows(DFS[s], d)))
        issued = {(e["stove"], e["date"]) for e in CH.log}

    st.markdown("**On-chain record**")
    if CH.log:
        st.dataframe(pd.DataFrame([{
            "credit #": e["claim_id"], "stove": e["stove"], "day": str(e["date"]),
            "cooking proof": short(e["event_hash"]), "sensor data fingerprint": short(e["raw_hash"]),
            "previous record": short(e["prev_head"]), "new record": short(e["new_head"]),
            "transaction ID": short(e["tx"])}
            for e in CH.log]), hide_index=True)
    else:
        st.info("No days issued yet.")

# ---------------------------------------------------------------- Verify a day
with tab_verify:
    c1, c2 = st.columns(2)
    hid = c1.selectbox("Stove", STOVES, key="v_stove")
    days, X, prob = BASE[hid]
    date = c2.selectbox("Day", list(days), key="v_date",
                        format_func=lambda d: f"{d}  ·  {days[d]['status']}")
    verdict = days[date]
    day_mask = np.array(DFS[hid].index.date) == date

    {"Verified": st.success, "No cooking detected": st.info}.get(verdict["status"], st.error)(verdict["status"])
    m = st.columns(4)
    m[0].metric("Rows logged", int(day_mask.sum()))
    m[1].metric("Peak temperature (C)", round(float(DFS[hid]["temp"].to_numpy()[day_mask].max()), 1))
    m[2].metric("Cooking confidence", round(float(prob[day_mask].max()), 3))
    m[3].metric("Cooking sessions found", len(verdict["events"]))

    plot_day(DFS[hid], prob, date, verdict["events"])
    if verdict["events"]:
        st.dataframe(pd.DataFrame([{
            "start": e["start"].strftime("%H:%M"), "end": e["end"].strftime("%H:%M"),
            "peak (C)": round(e["peak"], 1), "duration (min)": round(e["dur_min"], 1),
            "heat fluctuation": None if e["flicker"] is None else round(e["flicker"], 2),
            "realistic": e["plausible"], "copied": e["duplicate"]} for e in verdict["events"]]),
            hide_index=True)
    st.caption(f"Sanity check (based on real cooking sessions): peak ≥ {pl.LB['peak']:.0f} C, "
               f"duration ≥ {pl.LB['dur_min']:.1f} min, heat fluctuation ≥ {pl.LB['flicker']:.2f}.")

    with st.expander("Why did the AI flag this day? (SHAP)"):
        i = int(np.argmax(np.where(day_mask, prob, -1)))
        exp = get_explainer()(X.iloc[[i]])
        if exp.values.ndim == 3:
            exp = exp[:, :, 1]
        shap.plots.waterfall(exp[0], max_display=10, show=False)
        st.pyplot(plt.gcf())
        plt.close("all")
        st.caption("Row with the highest cooking probability on this day.")

    st.markdown("**Issue the credit**")
    if verdict["status"] != "Verified":
        st.button("Submit to chain", disabled=True)
    elif (hid, date) in issued:
        st.info("This day is already issued on-chain.")
    elif st.button("Submit to chain", type="primary"):
        ok, res = CH.attempt(hid, day_number(date), [bytes.fromhex(verdict["hash"])],
                             pl.raw_hash(DFS[hid][day_mask]))
        if ok:
            e = CH.log[res]
            st.success(f"Credit issued: claim #{res}")
            st.code(f"sensor seal:       {e['signature']}\nprevious record:   {e['prev_head']}\n"
                    f"new record:        {e['new_head']}")
        else:
            st.error(f"Contract rejected the submission: {res}")

# ---------------------------------------------------------------- Corrupt official
with tab_tamper:
    st.subheader("A corrupt official tampers with the data")
    st.caption("The official controls the oracle that submits days, but not the sensor's signing key.")
    c1, c2 = st.columns(2)
    t_hid = c1.selectbox("Stove", STOVES, key="t_stove")
    t_days = BASE[t_hid][0]
    empty = [d for d, v in t_days.items() if v["status"] == "No cooking detected"]
    t_date = c2.selectbox("Day to fake", empty or list(t_days), key="t_date")

    key = (t_hid, t_date)
    if st.session_state.get("t_key") != key:
        st.session_state.update(t_key=key, t_df=DFS[t_hid].copy(), t_ver=0)
    full = st.session_state.t_df
    mask = np.array(full.index.date) == t_date

    b1, b2 = st.columns(2)
    if b1.button("Inject fake cooking trace"):
        st.session_state.t_df = pl.inject_fake(full, t_date)
        st.session_state.t_ver += 1
        st.rerun()
    if b2.button("Reset data"):
        st.session_state.t_df = DFS[t_hid].copy()
        st.session_state.t_ver += 1
        st.rerun()

    day = full[mask]
    table = pd.DataFrame({"time": day.index.strftime("%H:%M:%S"),
                          "temp": day["temp"].to_numpy(), "ts": day["ts"].to_numpy()})
    edited = st.data_editor(table, key=f"t_ed_{st.session_state.t_ver}", hide_index=True,
                            disabled=["time", "ts"], column_config={"ts": None}, height=240)
    full.loc[mask, "temp"] = edited["temp"].to_numpy()

    raw_orig = pl.raw_hash(pl.day_rows(DFS[t_hid], t_date))
    raw_new = pl.raw_hash(full[mask])
    st.code(f"data fingerprint the sensor sealed: {raw_orig.hex()}\n"
            f"data fingerprint being submitted:   {raw_new.hex()}")

    if st.button("Run the AI check on this data"):
        res_days, _, res_prob = pl.analyse_file(full, t_hid)
        st.session_state.t_result = {"raw": raw_new.hex(), "verdict": res_days[t_date], "prob": res_prob}
    res = st.session_state.get("t_result")
    if res and res["raw"] == raw_new.hex():
        v = res["verdict"]
        if v["status"] == "Verified":
            st.warning("The AI is fooled by this fake. Only the sensor's seal can stop it.")
        else:
            st.info(f"AI verdict: {v['status']}")
        plot_day(full, res["prob"], t_date, v["events"])

    if raw_new == raw_orig:
        st.info("Edit or inject data first.")
    elif st.button("Submit to the chain as the official", type="primary"):
        day_n = day_number(t_date)
        honest_sig = CH.sign(t_hid, day_n, raw_orig, CH.head(t_hid))     # the sensor only signed the real data
        v = res["verdict"] if res and res["raw"] == raw_new.hex() else None
        ev = bytes.fromhex(v["hash"]) if v and v["hash"] else keccak(b"forged" + raw_new)
        ok, msg = CH.attempt(t_hid, day_n, [ev], raw_new, sig=honest_sig)
        if ok:
            st.error("NOT BLOCKED")
        else:
            st.error(f"Contract rejected the submission: {msg}")
            st.caption("The edited data no longer matches what the sensor sealed.")

    st.divider()
    st.subheader("Fraud tests")
    if not CH.log:
        st.info("Issue at least one day (Smart contract tab) so there is history to attack.")
    else:
        r = CH.log[-1]
        s0, d0 = r["stove"], r["day"]
        fresh = lambda n: keccak(text=f"fake{n}")
        ATTACKS = [
            ("Claim the same day twice", "Submit a second credit for a day already issued.", "day already issued",
             lambda: CH.attempt(s0, d0, [fresh(1)], fresh("r1"))),
            ("Reuse an old cooking record", "Reuse a real cooking trace to claim a new day.", "duplicate event",
             lambda: CH.attempt(s0, d0 + 100, [bytes.fromhex(r["event_hash"])], fresh("r2"))),
            ("Insert a day into old history", "Insert a day using an outdated chain head.", "chain broken",
             lambda: CH.attempt(s0, d0 + 101, [fresh(2)], fresh("r3"), prev=CH.baseline[s0])),
            ("Claim 3 events in one day", "Submit more events than the daily limit.", "event cap",
             lambda: CH.attempt(s0, d0 + 102, [fresh(3), fresh(4), fresh(5)], fresh("r4"))),
            ("Fake sensor seal", "Sign the day with a key that is not the stove's sensor.", "bad sensor signature",
             lambda: CH.attempt(s0, d0 + 103, [fresh(6)], fresh("r5"), key=Account.create())),
            ("Data changed after sealing", "Change the readings after the sensor signed them.", "bad sensor signature",
             lambda: CH.attempt(s0, d0 + 104, [fresh(7)], fresh("r6-edited"),
                                sig=CH.sign(s0, d0 + 104, fresh("r6"), CH.head(s0)))),
        ]
        results = st.session_state.setdefault("attack_results", {})

        def run(name, expected_reason, fn):
            ok, msg = fn()
            results[name] = ((not ok) and expected_reason in str(msg), msg)

        if st.button("Run all 6 fraud tests", type="primary"):
            for name, _, exp_reason, fn in ATTACKS:
                run(name, exp_reason, fn)
        for i, (name, what, exp_reason, fn) in enumerate(ATTACKS):
            c = st.columns([4, 1])
            c[0].markdown(f"**{name}**  \n{what}")
            if c[1].button("Run", key=f"atk{i}"):
                run(name, exp_reason, fn)
            if name in results:
                blocked, msg = results[name]
                (st.success if blocked else st.error)(f"{'Blocked' if blocked else 'NOT BLOCKED'}: {msg}")
        st.metric("Fraud attempts blocked", f"{sum(b for b, _ in results.values())}/{len(ATTACKS)}")

# ---------------------------------------------------------------- Payments and impact
with tab_pay:
    st.subheader("Payment")
    if st.button("Skip the 4-day waiting period and pay households"):
        if CH.log:
            CH.release_all()
        else:
            st.info("Issue days first.")
    bal = CH.balances()
    st.dataframe(pd.DataFrame({"stove": list(bal), "household balance (ETH)": list(bal.values())}),
                 hide_index=True)

    open_claims = [e["claim_id"] for e in CH.log if e["claim_id"] not in CH.done]
    if open_claims:
        cid = st.selectbox("Open claim (still in the waiting period)", open_claims)
        if st.button("Auditor challenges this claim"):
            try:
                s, before, after = CH.challenge(cid)
                st.warning(f"Claim {cid} cancelled. {s} deposit forfeited: {before} to {after} ETH.")
            except Exception as e:
                st.error(str(e)[:160])

    st.divider()
    st.subheader("Climate impact")
    HH, P_MSL, DISC, NCV, EF_CO2, EF_NON, FNRB = 4, 0.50, 0.95, 0.0156, 112, 9.46, 0.33

    def baseline_kg(fnrb):
        """SMEC V4.0 Option B baseline for one household-day, usage rate 1."""
        return HH * P_MSL * DISC / 365 * NCV * (EF_CO2 * fnrb + EF_NON) * 1000

    logged = sum(len(pd.date_range(df.index.min().normalize(), df.index.max().normalize()))
                 for df in DFS.values())
    n_ver = len(verified_now)
    kg = baseline_kg(FNRB)
    k = st.columns(5)
    k[0].metric("Logged stove-days", logged)
    k[1].metric("Verified stove-days", n_ver)
    k[2].metric("kg CO2e per household-day", f"{kg:.2f}")
    k[3].metric("Covered by verified days (kg)", f"{kg * n_ver:.1f}")
    k[4].metric("If every logged day were credited (kg)", f"{kg * logged:.1f}",
                f"+{logged / max(n_ver, 1) - 1:.0%}", delta_color="inverse")
    st.caption("Illustrative: baseline emissions covered by verified days under Gold Standard SMEC V4.0 "
               "Option B defaults (Rwanda fNRB 0.33). Not net credits.")

    st.divider()
    st.subheader("Supporting evidence: AI and sensor-fraud layer")
    S = json.loads((pl.APP / "summary.json").read_text())
    A = json.loads((pl.APP / "attacks.json").read_text())
    st.dataframe(pd.DataFrame(S["heldout"]).T.rename(columns={"pr_auc": "held-out PR-AUC", "f1": "held-out F1"}))
    u = S["usage_days"]
    st.write(f"Usage days on held-out households: {u['true']} true, {u['predicted']} predicted, "
             f"{u['caught']} caught. Labels are rule-based, so scores measure agreement with the rule.")
    sens = pd.DataFrame(A["sensor"])
    sens["miss rate"] = (sens["got_through"] / sens["tried"]).round(2)
    st.dataframe(sens.rename(columns={"got_through": "verified (missed)"}), hide_index=True)