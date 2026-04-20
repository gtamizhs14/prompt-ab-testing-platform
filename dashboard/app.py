"""
Streamlit dashboard for the Prompt A/B Testing Platform.

Run with:  streamlit run dashboard/app.py
Assumes the FastAPI server is running at http://localhost:8000
"""

import streamlit as st
import requests
import pandas as pd

import os
API = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Prompt A/B Platform", layout="wide")
st.title("Prompt A/B Testing Platform")

# ── Sidebar: experiment picker ────────────────────────────────────────────────

st.sidebar.header("Select Experiment")

try:
    experiments = requests.get(f"{API}/experiments", timeout=5).json()
except Exception:
    st.error("Cannot reach API at http://localhost:8000 — is the server running?")
    st.stop()

if not experiments:
    st.info("No experiments yet. Create one via POST /experiments.")
    st.stop()

exp_options = {f"[{e['id']}] {e['name']} ({e['status']})": e for e in experiments}
selected_label = st.sidebar.selectbox("Experiment", list(exp_options.keys()))
exp = exp_options[selected_label]
experiment_id = exp["id"]

st.sidebar.markdown(f"""
| Field | Value |
|---|---|
| Status | `{exp['status']}` |
| Primary metric | `{exp.get('primary_metric', '—')}` |
| Owner | `{exp.get('owner') or '—'}` |
| Winner | `{exp.get('winner') or '—'}` |
| Promoted | `{'yes' if exp.get('winner_promoted') else 'no'}` |
""")

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_results, tab_timeseries, tab_compare, tab_manage = st.tabs(
    ["Live Results", "Time Series", "Compare Versions", "Manage"]
)

# ── Tab 1: Live Results ───────────────────────────────────────────────────────

with tab_results:
    st.subheader("Live Metrics & Statistical Significance")

    try:
        results = requests.get(f"{API}/experiments/{experiment_id}/results", timeout=10).json()
    except Exception as e:
        st.error(f"Failed to load results: {e}")
        results = None

    if results:
        verdict = results.get("verdict", "—")
        winner = results.get("winner")
        sig = results.get("significance") or {}

        col1, col2, col3 = st.columns(3)
        col1.metric("Verdict", verdict)
        col2.metric("Winner", winner or "—")
        p_val = sig.get("p_value")
        col3.metric("p-value", f"{p_val:.4f}" if p_val is not None else "—")

        # Variant metrics table
        vm = results.get("variant_metrics", {})
        if vm:
            rows = []
            for vname, metrics in vm.items():
                rows.append({
                    "Variant": vname,
                    "Samples": metrics.get("sample_count", 0),
                    "Latency mean (ms)": metrics.get("latency_ms", {}).get("mean", "—"),
                    "Quality score mean": metrics.get("quality_score", {}).get("mean", "—"),
                    "Error rate": metrics.get("error_rate", "—"),
                    "Input tokens": metrics.get("input_tokens", {}).get("mean", "—"),
                    "Output tokens": metrics.get("output_tokens", {}).get("mean", "—"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

        # Confidence interval
        ci = results.get("confidence_interval")
        if ci:
            st.markdown(
                f"**95% CI on difference:** [{ci.get('lower'):.2f}, {ci.get('upper'):.2f}] "
                f"(point estimate: {ci.get('difference'):.2f})"
            )

        # MDE
        mde = results.get("mde")
        if mde:
            st.markdown(f"**Minimum Detectable Effect:** {mde:.2f}")

        # Sample size progress
        ssp = results.get("sample_size_progress")
        if ssp:
            remaining = ssp.get("samples_remaining", 0)
            st.markdown(f"**Samples remaining to reach 30 per variant:** {remaining}")
            prog_cols = st.columns(len(ssp) - 1 if "samples_remaining" in ssp else len(ssp))
            i = 0
            for vname, info in ssp.items():
                if vname == "samples_remaining":
                    continue
                have = info.get("have", 0)
                need = info.get("need", 30)
                prog_cols[i].progress(
                    min(have / need, 1.0),
                    text=f"{vname}: {have}/{need}",
                )
                i += 1

        # Promote winner button
        if winner and not exp.get("winner_promoted"):
            st.divider()
            st.markdown(f"**Winner declared: `{winner}`** — promote to active version?")
            if st.button("Promote Winner", type="primary"):
                resp = requests.post(f"{API}/experiments/{experiment_id}/promote-winner", timeout=10)
                if resp.status_code == 200:
                    st.success(resp.json().get("message", "Promoted!"))
                    st.rerun()
                else:
                    st.error(resp.json().get("detail", "Failed"))

# ── Tab 2: Time Series ────────────────────────────────────────────────────────

with tab_timeseries:
    st.subheader("Metric Over Time")

    metric = st.selectbox(
        "Metric",
        ["latency_ms", "quality_score", "input_tokens", "output_tokens"],
        key="ts_metric",
    )

    try:
        ts_data = requests.get(
            f"{API}/experiments/{experiment_id}/timeseries",
            params={"metric": metric},
            timeout=10,
        ).json()
    except Exception as e:
        st.error(f"Failed to load time series: {e}")
        ts_data = None

    if ts_data and ts_data.get("series"):
        series = ts_data["series"]
        all_rows = []
        for variant_name, points in series.items():
            for pt in points:
                all_rows.append({"timestamp": pt["timestamp"], "value": pt["value"], "variant": variant_name})

        if all_rows:
            df = pd.DataFrame(all_rows)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values("timestamp")

            # Pivot so each variant is a column
            pivoted = df.pivot_table(index="timestamp", columns="variant", values="value", aggfunc="mean")
            st.line_chart(pivoted)
        else:
            st.info("No data points yet.")
    else:
        st.info("No time series data yet — run some completions first.")

# ── Tab 3: Compare Versions ───────────────────────────────────────────────────

with tab_compare:
    st.subheader("Side-by-Side Version Comparison")
    st.caption("Runs the same input through two prompt versions and shows outputs.")

    prompt_id = exp.get("prompt_id")
    if not prompt_id:
        st.warning("No prompt_id on this experiment.")
    else:
        try:
            versions = requests.get(f"{API}/prompts/{prompt_id}/versions", timeout=5).json()
        except Exception:
            versions = []

        if len(versions) < 2:
            st.info("Need at least 2 prompt versions to compare.")
        else:
            ver_options = {f"v{v['version']} (id={v['id']}): {v['system_prompt'][:60]}…": v["id"] for v in versions}
            col_a, col_b = st.columns(2)
            with col_a:
                va_label = st.selectbox("Version A", list(ver_options.keys()), key="va")
                va_id = ver_options[va_label]
            with col_b:
                vb_label = st.selectbox("Version B", list(ver_options.keys()), index=1, key="vb")
                vb_id = ver_options[vb_label]

            variables_raw = st.text_area(
                "Variables (JSON, optional)",
                value="{}",
                height=80,
            )

            if st.button("Run Comparison"):
                import json
                try:
                    variables = json.loads(variables_raw)
                except json.JSONDecodeError:
                    st.error("Variables must be valid JSON.")
                    st.stop()

                with st.spinner("Calling LLM for both versions…"):
                    resp = requests.post(
                        f"{API}/prompts/{prompt_id}/compare",
                        json={"version_a_id": va_id, "version_b_id": vb_id, "variables": variables},
                        timeout=60,
                    )

                if resp.status_code == 200:
                    data = resp.json()
                    col_ra, col_rb = st.columns(2)
                    with col_ra:
                        va_res = data["version_a"]
                        st.markdown(f"**Version A** (v{va_res['version_number']})")
                        st.text_area("Response A", value=va_res["response"], height=200, key="ra")
                        st.caption(f"Latency: {va_res['latency_ms']}ms | Tokens in/out: {va_res['input_tokens']}/{va_res['output_tokens']}")
                    with col_rb:
                        vb_res = data["version_b"]
                        st.markdown(f"**Version B** (v{vb_res['version_number']})")
                        st.text_area("Response B", value=vb_res["response"], height=200, key="rb")
                        st.caption(f"Latency: {vb_res['latency_ms']}ms | Tokens in/out: {vb_res['input_tokens']}/{vb_res['output_tokens']}")
                else:
                    st.error(resp.json().get("detail", "Comparison failed"))

# ── Tab 4: Manage ─────────────────────────────────────────────────────────────

with tab_manage:
    st.subheader("Experiment Controls")

    status = exp["status"]

    col_start, col_stop = st.columns(2)

    with col_start:
        if status == "draft":
            if st.button("Start Experiment", type="primary"):
                resp = requests.put(f"{API}/experiments/{experiment_id}/start", timeout=10)
                if resp.status_code == 200:
                    st.success("Experiment started!")
                    st.rerun()
                else:
                    st.error(resp.json().get("detail", "Failed to start"))
        else:
            st.button("Start Experiment", disabled=True)

    with col_stop:
        if status == "running":
            if st.button("Stop Experiment", type="secondary"):
                resp = requests.put(f"{API}/experiments/{experiment_id}/stop", timeout=10)
                if resp.status_code == 200:
                    st.success("Experiment stopped.")
                    st.rerun()
                else:
                    st.error(resp.json().get("detail", "Failed to stop"))
        else:
            st.button("Stop Experiment", disabled=True)

    st.divider()
    st.subheader("Create New Experiment")

    with st.form("create_experiment"):
        new_name = st.text_input("Name")
        new_prompt_id = st.number_input("Prompt ID", min_value=1, step=1)
        new_metric = st.selectbox("Primary Metric", ["latency_ms", "quality_score"])
        new_sample_size = st.number_input("Target Sample Size (per variant)", min_value=0, step=10, value=100)
        new_owner = st.text_input("Owner (email or name)")
        submitted = st.form_submit_button("Create")

        if submitted:
            payload = {
                "name": new_name,
                "prompt_id": int(new_prompt_id),
                "primary_metric": new_metric,
                "sample_size": int(new_sample_size) if new_sample_size else None,
                "owner": new_owner or None,
            }
            resp = requests.post(f"{API}/experiments", json=payload, timeout=10)
            if resp.status_code == 200:
                st.success(f"Created experiment id={resp.json()['id']}")
                st.rerun()
            else:
                st.error(resp.json().get("detail", "Failed to create"))
