"""dashboard/app.py — EdgeFund trading terminal dashboard."""

import glob
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ─── paths ────────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET   = ZoneInfo("America/Chicago")


def _p(*parts: str) -> str:
    return os.path.join(BASE, *parts)


# ─── page setup ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="EdgeFund",
    page_icon="△",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st_autorefresh(interval=60000, key="dashboard_refresh")

# ─── global CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container {
    padding: 1rem 1.5rem 0 1.5rem !important;
    max-width: 100% !important;
  }
  header { visibility: hidden; }
  footer { visibility: hidden; }
  section[data-testid="stSidebar"] { display: none; }

  /* ── Header ── */
  .ef-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: 10px 0 14px;
    border-bottom: 1px solid #1F2937;
    margin-bottom: 14px;
  }
  .ef-logo { font-size: 26px; font-weight: 800; color: #00C896; letter-spacing: -0.5px; }
  .ef-sub  { font-size: 12px; color: #6B7280; margin-top: 2px; }
  .ef-clock { font-size: 13px; color: #9CA3AF; text-align: right; font-family: monospace; }
  .ef-live {
    display: inline-flex; align-items: center;
    background: #022C22; border: 1px solid #065F46; border-radius: 4px;
    padding: 3px 10px; font-size: 11px; color: #10B981; font-weight: 600;
    margin-top: 6px;
  }
  .ef-pulse {
    display: inline-block; width: 7px; height: 7px;
    background: #10B981; border-radius: 50%; margin-right: 7px;
    animation: pulse 1.8s infinite;
  }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }

  /* ── Metric cards ── */
  .mcard {
    background: #0D1117;
    border: 1px solid #1F2937;
    border-radius: 6px;
    padding: 14px 10px;
    text-align: center;
  }
  .mlabel { font-size: 10px; color: #6B7280; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 5px; }
  .mval   { font-size: 22px; font-weight: 700; font-family: monospace; }
  .msub   { font-size: 10px; color: #9CA3AF; margin-top: 3px; }
  .teal   { color: #00C896; }
  .red    { color: #EF4444; }
  .yellow { color: #F59E0B; }
  .gray   { color: #9CA3AF; }

  /* ── Status cards ── */
  .scard  {
    background: #0D1117;
    border: 1px solid #1F2937;
    border-radius: 6px;
    padding: 16px;
  }
  .sctitle { font-size: 10px; color: #6B7280; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 12px; font-weight: 600; }
  .scrow   { display: flex; justify-content: space-between; font-size: 12px; padding: 5px 0; border-bottom: 1px solid #0F1923; }
  .scrow:last-child { border-bottom: none; }
  .sckey  { color: #6B7280; }
  .scval  { color: #E5E7EB; font-family: monospace; }

  /* ── Insight callouts ── */
  .insight-teal   { background: #022C22; border: 1px solid #065F46; border-radius: 6px; padding: 16px; margin-top: 14px; font-size: 13px; color: #A7F3D0; }
  .insight-yellow { background: #1C1207; border: 1px solid #78350F; border-radius: 6px; padding: 16px; margin-top: 14px; font-size: 13px; color: #FDE68A; }
  .insight-gray   { background: #111827; border: 1px solid #374151; border-radius: 6px; padding: 16px; margin-top: 14px; font-size: 13px; color: #9CA3AF; }

  /* ── Live conditions gate ── */
  .gate-item { display: flex; align-items: center; padding: 7px 0; font-size: 13px; border-bottom: 1px solid #0F1923; }
  .gate-item:last-child { border-bottom: none; }
  .gate-icon { width: 22px; font-size: 13px; font-weight: 700; margin-right: 10px; }

  /* ── Footer ── */
  .ef-footer {
    border-top: 1px solid #1F2937;
    padding: 10px 0;
    margin-top: 24px;
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: #4B5563;
  }
</style>
""", unsafe_allow_html=True)


# ─── data loaders ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_trades() -> pd.DataFrame:
    fp = _p("data", "paper_trades.json")
    if not os.path.exists(fp):
        return pd.DataFrame()
    try:
        with open(fp) as f:
            data = json.load(f)
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30)
def load_summary() -> dict:
    fp = _p("data", "performance_summary.json")
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp) as f:
            return json.load(f)
    except Exception:
        return {}


@st.cache_data(ttl=30)
def load_cost_log() -> pd.DataFrame:
    fp = _p("data", "agent_cost_log.csv")
    if not os.path.exists(fp):
        return pd.DataFrame()
    try:
        return pd.read_csv(fp)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30)
def load_latest_snapshot() -> tuple[dict | None, str | None]:
    """Load the single latest snapshot file (used for metadata/timestamp)."""
    files = sorted(glob.glob(_p("data", "snapshots", "????-??-??_????.json")))
    if not files:
        return None, None
    try:
        with open(files[-1]) as f:
            return json.load(f), files[-1]
    except Exception:
        return None, None


@st.cache_data(ttl=30)
def load_all_recent_rows() -> tuple[list[dict], str]:
    """
    Aggregate rows from ALL snapshot files in the last 24 hours.
    For each unique event_ticker, keep only the most recent reading.
    Returns (deduplicated_rows, latest_snap_time_label).
    """
    files = sorted(glob.glob(_p("data", "snapshots", "????-??-??_????.json")))
    if not files:
        return [], ""

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = []
    for fp in files:
        name = os.path.basename(fp).replace(".json", "")
        try:
            dt = datetime.strptime(name, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                recent.append((dt, fp, name))
        except Exception:
            continue

    if not recent:
        recent = [(None, files[-1], os.path.basename(files[-1]).replace(".json", ""))]

    # Load and merge rows, keeping latest reading per event_ticker
    best: dict[str, tuple[datetime, dict]] = {}
    for snap_dt, fp, snap_name in sorted(recent):
        try:
            with open(fp) as f:
                snap = json.load(f)
            for row in snap.get("rows", []):
                key = row.get("event_ticker") or row.get("game", "")
                if key not in best or (snap_dt and snap_dt > best[key][0]):
                    best[key] = (snap_dt, {**row, "_snap_time": snap_name})
        except Exception:
            continue

    rows     = [v for _, v in best.values()]
    last_snap = recent[-1][2] if recent else ""
    return rows, last_snap


# ─── helpers ─────────────────────────────────────────────────────────────────
def fmt_pct(v, decimals: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v) * 100:.{decimals}f}%"


def fmt_game_time(utc_str: str) -> str:
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        et = dt.astimezone(ET)
        return et.strftime("%b %-d %-I:%M %p CT")
    except Exception:
        return utc_str or "—"


def now_et() -> str:
    return datetime.now(ET).strftime("%a %b %-d  %-I:%M:%S %p CT")


def fmt_snap_time(snap_time: str) -> str:
    """Convert UTC snapshot filename timestamp (e.g. '2026-06-23_2338') to CT display."""
    try:
        dt_utc = datetime.strptime(snap_time, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
        dt_et  = dt_utc.astimezone(ET)
        return dt_et.strftime("%b %-d %-I:%M %p CT")
    except Exception:
        return snap_time


def dark_plotly(fig: go.Figure, height: int = 280) -> go.Figure:
    fig.update_layout(
        paper_bgcolor="#080C10",
        plot_bgcolor="#0D1117",
        font=dict(color="#9CA3AF", family="monospace", size=11),
        height=height,
        margin=dict(l=44, r=16, t=28, b=36),
        xaxis=dict(gridcolor="#1F2937", zerolinecolor="#1F2937"),
        yaxis=dict(gridcolor="#1F2937", zerolinecolor="#1F2937"),
    )
    return fig


def scrow(key: str, val: str, val_style: str = "") -> str:
    style = f" style='{val_style}'" if val_style else ""
    return f"<div class='scrow'><span class='sckey'>{key}</span><span class='scval'{style}>{val}</span></div>"


# ─── load data ────────────────────────────────────────────────────────────────
trades_df          = load_trades()
summary            = load_summary()
cost_df            = load_cost_log()
snap, snap_path    = load_latest_snapshot()
all_recent_rows, latest_snap_label = load_all_recent_rows()
today_str          = datetime.now(ET).strftime("%Y-%m-%d")
all_snap_files     = sorted(glob.glob(_p("data", "snapshots", "????-??-??_????.json")))

# ─── HEADER ───────────────────────────────────────────────────────────────────
if not trades_df.empty and "snapshot_time" in trades_df.columns:
    try:
        first_snap = trades_df["snapshot_time"].min()
        first_dt   = datetime.strptime(str(first_snap)[:16], "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
        days_running = (datetime.now(timezone.utc) - first_dt).days
    except Exception:
        days_running = 0
else:
    days_running = 0

st.markdown(f"""
<div class="ef-header">
  <div>
    <div class="ef-logo">△ EdgeFund</div>
    <div class="ef-sub">Prediction Market Alpha System</div>
  </div>
  <div style="text-align:right">
    <div class="ef-clock">{now_et()}</div>
    <div>
      <span class="ef-live"><span class="ef-pulse"></span>PAPER TRADING — LIVE</span>
      &nbsp;&nbsp;
      <span style="font-size:11px;color:#6B7280;font-family:monospace">Day {days_running}</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ─── METRIC CARDS ─────────────────────────────────────────────────────────────
wr1     = summary.get("win_rate_tier1")
total   = summary.get("total_logged", 0)
res_ct  = summary.get("total_resolved", 0)
pval    = summary.get("p_value")
avg_gap = trades_df["abs_gap"].mean() if not trades_df.empty and "abs_gap" in trades_df.columns else None

if not cost_df.empty and "timestamp" in cost_df.columns:
    cost_today = cost_df[cost_df["timestamp"].str[:10] == today_str]["estimated_cost_usd"].sum()
else:
    cost_today = 0.0

wr_cls = "teal" if wr1 and wr1 > 0.58 else ("red" if wr1 and wr1 < 0.50 else "yellow")
pv_cls = "teal" if pval and pval < 0.10 else ("yellow" if pval and pval < 0.20 else "red")
pv_sub = ("Significant ✓"        if pval and pval < 0.05 else
          ("Approaching sig."     if pval and pval < 0.10 else
           ("Approaching sig."    if pval and pval < 0.20 else "Need more data")))

mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
_cards = [
    (mc1, "Win Rate (T1)",  f"<span class='{wr_cls}'>{fmt_pct(wr1)}</span>",           "tier 1 only"),
    (mc2, "Trades Logged",  f"<span class='teal'>{total}</span>",                      "&nbsp;"),
    (mc3, "Resolved",       f"<span class='teal'>{res_ct}</span>",                     f"of {total} total"),
    (mc4, "Avg Gap",        f"<span class='teal'>{fmt_pct(avg_gap)}</span>",           "all trades"),
    (mc5, "P-Value",        f"<span class='{pv_cls}'>{f'{pval:.3f}' if pval else '—'}</span>", pv_sub),
    (mc6, "API Cost Today", f"<span class='{'teal' if cost_today < 0.50 else 'yellow'}'>${cost_today:.3f}</span>", "agent calls"),
]
for col, label, val, sub in _cards:
    with col:
        st.markdown(f"""
        <div class="mcard">
          <div class="mlabel">{label}</div>
          <div class="mval">{val}</div>
          <div class="msub">{sub}</div>
        </div>""", unsafe_allow_html=True)

st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

# ─── TABS ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📡 Live Gaps", "📋 Trade Log", "📈 Performance", "⚙️ System"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — LIVE GAP SCANNER
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    if not all_recent_rows:
        st.info("No snapshots yet. Scheduler fires 2h before first pitch.")
    else:
        now_utc = datetime.now(timezone.utc)

        records = []
        for row in all_recent_rows:
            gap     = row.get("gap", 0) or 0
            abs_gap = row.get("abs_gap", abs(gap))
            signal  = "BUY_YES" if gap <= 0 else "BUY_NO"
            tier    = 1 if abs_gap >= 0.10 else 2

            try:
                game_dt = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
                hours   = round((game_dt - now_utc).total_seconds() / 3600, 1)
            except Exception:
                hours = None

            # Skip games that have already started
            if hours is not None and hours < 0:
                continue

            if abs_gap >= 0.05 and tier == 1:
                action = "TRADE ✓"
            elif abs_gap >= 0.03:
                action = "WATCH"
            else:
                action = "—"

            snap_label = fmt_snap_time(row.get("_snap_time", ""))

            records.append({
                "Game":          row.get("game", ""),
                "Game Time":     fmt_game_time(row.get("start_utc", "")),
                "Kalshi":        fmt_pct(row.get("k_prob")),
                "Pinnacle":      fmt_pct(row.get("v_prob")),
                "Gap":           f"{gap * 100:+.1f}%",
                "Signal":        signal,
                "Tier":          tier,
                "Hours to Game": round(hours, 1) if hours is not None else "—",
                "Action":        action,
                "Snapped":       snap_label,
                "_abs_gap":      abs_gap,
            })

        if not records:
            st.info("No upcoming games in today's snapshots.")
        else:
            df_snap = (
                pd.DataFrame(records)
                .sort_values("_abs_gap", ascending=False)
                .drop(columns=["_abs_gap"])
                .reset_index(drop=True)
            )

            st.dataframe(df_snap, use_container_width=True, hide_index=True)

            n_games  = len(records)
            t1_ct    = sum(1 for r in records if r["Tier"] == 1)
            # parse Gap string back to float for the 3% filter
            def _gap_val(r):
                try:
                    return abs(float(r["Gap"].rstrip("%").replace("+", ""))) / 100
                except Exception:
                    return 0.0
            gap3_pct = sum(1 for r in records if _gap_val(r) >= 0.03) / n_games * 100 if n_games else 0

            st.markdown(
                f"<div style='font-size:11px;color:#6B7280;margin-top:6px'>"
                f"{n_games} sides tracked &nbsp;·&nbsp; {t1_ct} Tier 1 signals &nbsp;·&nbsp; "
                f"{gap3_pct:.0f}% show |gap| ≥ 3% &nbsp;·&nbsp; "
                f"Latest snap: <span style='color:#9CA3AF;font-family:monospace'>{fmt_snap_time(latest_snap_label)}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — PAPER TRADE LOG
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    if trades_df.empty:
        st.info("No trades logged yet. Snapshot script is running.")
    else:
        df = trades_df.copy()

        fc1, fc2, fc3 = st.columns([2, 2, 2])
        with fc1:
            sports    = ["All"] + sorted(df["sport"].dropna().unique().tolist())
            sel_sport = st.selectbox("Sport", sports, key="t2_sport")
        with fc2:
            sel_outcome = st.selectbox("Outcome", ["All", "WIN", "LOSS", "OPEN", "SKIPPED"], key="t2_outcome")
        with fc3:
            sel_tier = st.selectbox("Tier", ["All", "Tier 1", "Tier 2"], key="t2_tier")

        if sel_sport != "All":
            df = df[df["sport"] == sel_sport]
        if sel_outcome == "OPEN":
            df = df[df["outcome"].isna() | (df["outcome"] == "")]
        elif sel_outcome in ("WIN", "LOSS", "SKIPPED"):
            df = df[df["outcome"] == sel_outcome]
        if "abs_gap" in df.columns:
            if sel_tier == "Tier 1":
                df = df[df["abs_gap"] >= 0.10]
            elif sel_tier == "Tier 2":
                df = df[df["abs_gap"] < 0.10]

        df = df.sort_values("snapshot_time", ascending=False)

        def _short_id(tid):
            return str(tid)[-8:] if pd.notna(tid) else "—"

        def _fmt_date(s):
            try:
                return datetime.strptime(str(s)[:10], "%Y-%m-%d").strftime("%b %-d")
            except Exception:
                return str(s)[:10] if s else "—"

        agent_col      = df["agent_verdict"]      if "agent_verdict"      in df.columns else pd.Series(["—"] * len(df), index=df.index)
        confidence_col = df["agent_confidence"]   if "agent_confidence"   in df.columns else pd.Series(["—"] * len(df), index=df.index)

        def _fmt_game_date(utc_str):
            try:
                dt = datetime.fromisoformat(str(utc_str).replace("Z", "+00:00"))
                return dt.astimezone(ET).strftime("%b %-d")
            except Exception:
                return "—"

        def _fmt_snap_date(snap_str):
            try:
                dt = datetime.strptime(str(snap_str), "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
                return dt.astimezone(ET).strftime("%b %-d")
            except Exception:
                return _fmt_date(str(snap_str)[:10])

        display = pd.DataFrame({
            "ID":         df["trade_id"].apply(_short_id),
            "Game Date":  df["start_utc"].apply(_fmt_game_date) if "start_utc" in df.columns else "—",
            "Snap Date":  df["snapshot_time"].apply(_fmt_snap_date),
            "Game":       df["game"],
            "Signal":     df["signal"],
            "Gap":        df["abs_gap"].apply(lambda v: fmt_pct(v) if pd.notna(v) else "—"),
            "Tier":       df["abs_gap"].apply(lambda v: 1 if pd.notna(v) and v >= 0.10 else 2) if "abs_gap" in df.columns else "—",
            "Kalshi":     df["k_prob"].apply(lambda v: fmt_pct(v) if pd.notna(v) else "—"),
            "Pinnacle":   df["v_prob"].apply(lambda v: fmt_pct(v) if pd.notna(v) else "—"),
            "Agent":      agent_col.fillna("—"),
            "Confidence": confidence_col.fillna("—"),
            "Outcome":    df["outcome"].fillna("OPEN"),
            "Hours":      df["hours_before_game"].apply(lambda v: f"{v:.1f}" if pd.notna(v) else "—"),
        })

        st.dataframe(display, use_container_width=True, hide_index=True)

        wins_  = (df["outcome"] == "WIN").sum()
        loss_  = (df["outcome"] == "LOSS").sum()
        open_  = df["outcome"].isna().sum()
        skip_  = (df["outcome"] == "SKIPPED").sum()

        st.markdown(
            f"<div style='font-size:11px;color:#6B7280;margin-top:6px'>"
            f"Total shown: <b style='color:#9CA3AF'>{len(df)}</b> &nbsp;·&nbsp; "
            f"Wins: <b style='color:#10B981'>{wins_}</b> &nbsp;·&nbsp; "
            f"Losses: <b style='color:#EF4444'>{loss_}</b> &nbsp;·&nbsp; "
            f"Open: <b style='color:#9CA3AF'>{open_}</b> &nbsp;·&nbsp; "
            f"Skipped: <b style='color:#4B5563'>{skip_}</b>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Show note if no trades have agent reasoning yet
        if not trades_df.empty:
            agent_covered = trades_df["agent_verdict"].notna().sum() if "agent_verdict" in trades_df.columns else 0
            if agent_covered == 0:
                st.markdown(
                    "<div style='font-size:10px;color:#4B5563;margin-top:4px;font-style:italic'>"
                    "ℹ️ Agent reasoning active from Jun 24 forward — dashes above are expected for earlier trades."
                    "</div>",
                    unsafe_allow_html=True,
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    r1c1, r1c2 = st.columns(2)

    # ── Win Rate Over Time ────────────────────────────────────────────────────
    with r1c1:
        st.markdown("**Win Rate Over Time** (7-day rolling)")
        if not trades_df.empty and "outcome" in trades_df.columns:
            df_t = trades_df.copy()
            df_t["date"]     = df_t["snapshot_time"].apply(lambda s: str(s)[:10])
            df_t             = df_t.sort_values("date")
            df_t["won"]      = (df_t["outcome"] == "WIN").astype(int)
            df_t["resolved"] = df_t["outcome"].isin(["WIN", "LOSS"]).astype(int)

            daily = df_t.groupby("date").agg(
                wins=("won", "sum"), resolved=("resolved", "sum")
            ).reset_index()
            daily["roll_w"] = daily["wins"].rolling(7, min_periods=1).sum()
            daily["roll_r"] = daily["resolved"].rolling(7, min_periods=1).sum()
            daily["wr"]     = daily.apply(
                lambda r: r["roll_w"] / r["roll_r"] * 100 if r["roll_r"] > 0 else None, axis=1
            )

            # x-axis: clean dates with a 14-day minimum window
            from datetime import timedelta
            x_min = daily["date"].min()
            x_max_floor = daily["date"].max()
            x_min_dt    = datetime.strptime(x_min, "%Y-%m-%d")
            x_max_dt    = datetime.strptime(x_max_floor, "%Y-%m-%d")
            x_end       = max(x_max_dt, x_min_dt + timedelta(days=14)).strftime("%Y-%m-%d")

            fig = go.Figure()
            fig.add_hline(y=50, line_dash="dash", line_color="#374151", line_width=1)
            fig.add_hline(
                y=58, line_dash="dash", line_color="#00C896", line_width=1,
                annotation_text="58% min", annotation_font_color="#00C896", annotation_font_size=9,
            )
            fig.add_trace(go.Scatter(
                x=daily["date"], y=daily["wr"],
                mode="lines+markers",
                line=dict(color="#00C896", width=2),
                marker=dict(size=5),
                connectgaps=False,
                name="7-day WR",
            ))
            fig.update_layout(
                showlegend=False,
                yaxis=dict(range=[0, 105], ticksuffix="%"),
                xaxis=dict(
                    range=[x_min, x_end],
                    tickformat="%b %-d",
                    dtick="D1",
                    tickangle=-45,
                ),
            )
            st.plotly_chart(dark_plotly(fig), use_container_width=True)
        else:
            st.info("No resolved trades yet.")

    # ── Win Rate by Gap Bucket ────────────────────────────────────────────────
    with r1c2:
        st.markdown("**Win Rate by Gap Bucket** ← primary signal chart")
        bucket_data = summary.get("by_gap_bucket", {})
        _order = [("5–7%", "5_7"), ("7–10%", "7_10"), ("10–15%", "10_15"), ("15%+", "15_plus")]
        labels, wr_vals, n_vals, bar_colors = [], [], [], []
        for label, key in _order:
            bkt = bucket_data.get(key)
            if bkt:
                wr = bkt.get("win_rate", 0)
                n  = bkt.get("trades",   0)
                labels.append(label)
                wr_vals.append(wr * 100)
                n_vals.append(n)
                bar_colors.append("#10B981" if wr >= 0.60 else ("#F59E0B" if wr >= 0.50 else "#EF4444"))

        if labels:
            MIN_BAR = 2.0  # minimum visible height for 0% bars
            display_vals = [max(v, MIN_BAR) for v in wr_vals]

            fig2 = go.Figure()
            fig2.add_hline(y=50, line_dash="dash", line_color="#374151", line_width=1)
            fig2.add_trace(go.Bar(
                x=labels, y=display_vals,
                marker_color=bar_colors,
                text=[f"{wr:.0f}%<br>n={n}" for wr, n in zip(wr_vals, n_vals)],
                textposition="outside",
                textfont=dict(size=11, color="#E5E7EB"),
            ))
            fig2.update_layout(showlegend=False, yaxis=dict(range=[0, 120], ticksuffix="%"))
            st.plotly_chart(dark_plotly(fig2), use_container_width=True)
        else:
            st.info("No gap-bucket data yet.")

    r2c1, r2c2 = st.columns(2)

    # ── Outcomes Donut ────────────────────────────────────────────────────────
    with r2c1:
        st.markdown("**Trade Outcomes**")
        if not trades_df.empty:
            _wins = int((trades_df["outcome"] == "WIN").sum())
            _loss = int((trades_df["outcome"] == "LOSS").sum())
            _open = int(trades_df["outcome"].isna().sum())
            _skip = int((trades_df["outcome"] == "SKIPPED").sum())

            fig3 = go.Figure(go.Pie(
                labels=["WIN", "LOSS", "OPEN", "SKIPPED"],
                values=[_wins, _loss, _open, _skip],
                hole=0.55,
                marker=dict(colors=["#10B981", "#EF4444", "#6B7280", "#374151"]),
                textfont=dict(size=11, color="#E5E7EB"),
                textinfo="label+percent",
            ))
            fig3.update_layout(
                showlegend=False,
                annotations=[dict(
                    text=f"<b>{len(trades_df)}</b><br>trades",
                    x=0.5, y=0.5, font_size=13, font_color="#E5E7EB", showarrow=False,
                )],
            )
            st.plotly_chart(dark_plotly(fig3), use_container_width=True)
        else:
            st.info("No trades yet.")

    # ── Agent Stats ───────────────────────────────────────────────────────────
    with r2c2:
        st.markdown("**Agent Performance Stats**")
        agent = summary.get("agent_stats", {})

        def _fmt_a(v, pct: bool = False) -> str:
            if v is None:
                return "<span class='gray'>—</span>"
            if pct:
                return f"<span class='teal'>{v * 100:.1f}%</span>"
            return f"<span class='teal'>{v}</span>"

        agent_rows = [
            ("Total evaluated",            _fmt_a(agent.get("total_evaluated"))),
            ("Trade recommendations",       _fmt_a(agent.get("trade_recommendations"))),
            ("Skip recommendations",        _fmt_a(agent.get("skip_recommendations"))),
            ("Skip rate",                   _fmt_a(agent.get("skip_rate"),                   pct=True)),
            ("Win rate (agent-approved)",   _fmt_a(agent.get("win_rate_after_agent"),         pct=True)),
            ("Win rate (unvetted)",         _fmt_a(agent.get("win_rate_without_agent"),       pct=True)),
            ("High confidence win rate",    _fmt_a(agent.get("high_confidence_win_rate"),     pct=True)),
            ("Medium confidence win rate",  _fmt_a(agent.get("medium_confidence_win_rate"),   pct=True)),
            ("News found rate",             _fmt_a(agent.get("news_found_rate"),              pct=True)),
        ]
        rows_html = "".join(
            f"<div class='scrow'><span class='sckey'>{k}</span><span class='scval'>{v}</span></div>"
            for k, v in agent_rows
        )
        st.markdown(f"<div class='scard'>{rows_html}</div>", unsafe_allow_html=True)

    # ── Insight callout ───────────────────────────────────────────────────────
    wr_overall = summary.get("win_rate_overall", wr1)
    if pval is not None and pval < 0.05:
        st.markdown(
            f"<div class='insight-teal'>✓ <b>Edge Confirmed</b> — "
            f"{fmt_pct(wr_overall)} win rate on {res_ct} resolved trades (p={pval:.3f})</div>",
            unsafe_allow_html=True,
        )
    elif pval is not None and pval < 0.10:
        n_more = max(0, 30 - res_ct)
        st.markdown(
            f"<div class='insight-yellow'>→ <b>Approaching Significance</b> — "
            f"{fmt_pct(wr_overall)} on {res_ct} trades (p={pval:.3f}). "
            f"Need ~{n_more} more resolved trades.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div class='insight-gray'>◷ <b>Building Evidence</b> — "
            f"{res_ct} resolved trades so far. Need 30+ for significance.</div>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SYSTEM STATUS
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    # Precompute values used across multiple cards
    today_snaps    = [f for f in all_snap_files if os.path.basename(f).startswith(today_str)]
    n_snap_days    = len(set(os.path.basename(f)[:10] for f in all_snap_files))
    cost_total     = cost_df["estimated_cost_usd"].sum()  if not cost_df.empty else 0.0
    cost_mean      = cost_df["estimated_cost_usd"].mean() if not cost_df.empty else 0.0

    if not trades_df.empty:
        open_trades    = trades_df[trades_df["outcome"].isna()]
        today_trades   = trades_df[trades_df["snapshot_time"].apply(lambda s: str(s)[:10]) == today_str]
        oldest_open    = str(open_trades["snapshot_time"].min())[:10] if not open_trades.empty else "—"
    else:
        open_trades    = pd.DataFrame()
        today_trades   = pd.DataFrame()
        oldest_open    = "—"

    resolved_today = pd.DataFrame()
    if not trades_df.empty and "resolved_at" in trades_df.columns:
        resolved_today = trades_df[
            trades_df["resolved_at"].apply(lambda s: str(s)[:10] == today_str if pd.notna(s) else False)
        ]

    sc1, sc2, sc3 = st.columns(3)

    # ── Snapshot Pipeline ─────────────────────────────────────────────────────
    with sc1:
        if all_snap_files:
            last_name = os.path.basename(all_snap_files[-1]).replace(".json", "")
            try:
                last_dt      = datetime.strptime(last_name, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
                age_min      = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
                last_name_et = fmt_snap_time(last_name)

                # Check scheduler_log.txt — if it logged activity in last 20 min,
                # pipeline is alive even when no games are in the current window
                sched_log = _p("data", "snapshots", "scheduler_log.txt")
                sched_alive = False
                next_window_note = ""
                if os.path.exists(sched_log):
                    sched_age = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(sched_log)) / 60
                    if sched_age < 20:
                        sched_alive = True
                    # Try to extract "next window opens in X min" from last lines
                    try:
                        with open(sched_log) as f:
                            last_lines = f.read().rsplit("\n", 6)
                        for line in last_lines:
                            if "Next window opens in" in line:
                                next_window_note = line.split("] ")[-1].strip()
                    except Exception:
                        pass

                if age_min < 120:
                    pipe_st  = "🟢 RUNNING"
                    pipe_col = "color:#10B981"
                elif sched_alive:
                    pipe_st  = "🟡 BETWEEN WINDOWS"
                    pipe_col = "color:#F59E0B"
                else:
                    pipe_st  = "🔴 STALE"
                    pipe_col = "color:#EF4444"
            except Exception:
                last_name = last_name_et = "—"; pipe_st = "🟡 UNKNOWN"; pipe_col = "color:#F59E0B"
                next_window_note = ""

            today_game_rows = 0
            for sf in today_snaps:
                try:
                    with open(sf) as f:
                        s = json.load(f)
                    today_game_rows += len(s.get("rows", []))
                except Exception:
                    pass

            if "timing_suspect" in today_trades.columns:
                today_clean   = int((~today_trades["timing_suspect"]).sum())
                today_suspect = int(today_trades["timing_suspect"].sum())
            else:
                today_clean = today_suspect = 0
        else:
            last_name = last_name_et = "—"; pipe_st = "🔴 NO DATA"; pipe_col = "color:#EF4444"
            today_game_rows = today_clean = today_suspect = 0
            next_window_note = ""

        next_row = scrow("Next window", f"<span style='color:#6B7280'>{next_window_note}</span>") if next_window_note else ""

        st.markdown(
            f"<div class='scard'>"
            f"<div class='sctitle'>Snapshot Pipeline</div>"
            + scrow("Status",              f"<span style='{pipe_col}'>{pipe_st}</span>")
            + scrow("Last run",            last_name_et)
            + next_row
            + scrow("Games captured today",str(today_game_rows))
            + scrow("Clean trades today",  f"<span style='color:#10B981'>{today_clean}</span>")
            + scrow("Timing-suspect today",f"<span style='color:#F59E0B'>{today_suspect}</span>")
            + "</div>",
            unsafe_allow_html=True,
        )

    # ── Research Agent ────────────────────────────────────────────────────────
    with sc2:
        if not trades_df.empty and "agent_verdict" in trades_df.columns:
            with_agent    = int(trades_df["agent_verdict"].notna().sum())
            without_agent = int(trades_df["agent_verdict"].isna().sum())
            agent_st      = "🟢 ACTIVE" if with_agent > 0 else "🟡 WIRED — awaiting new trades"
            agent_col_    = "color:#10B981" if with_agent > 0 else "color:#F59E0B"
        else:
            with_agent    = 0
            without_agent = len(trades_df) if not trades_df.empty else 0
            agent_st      = "🟡 WIRED — awaiting new trades"
            agent_col_    = "color:#F59E0B"

        st.markdown(
            f"<div class='scard'>"
            f"<div class='sctitle'>Research Agent</div>"
            + scrow("Status",           f"<span style='{agent_col_}'>{agent_st}</span>")
            + scrow("Trades with agent",str(with_agent))
            + scrow("Trades without",   str(without_agent))
            + scrow("Today's API cost", f"${cost_today:.3f}")
            + scrow("Total API cost",   f"${cost_total:.3f}")
            + scrow("Avg cost / trade", f"${cost_mean:.4f}" if cost_mean else "—")
            + "</div>",
            unsafe_allow_html=True,
        )

    # ── Outcome Updater ───────────────────────────────────────────────────────
    with sc3:
        outcomes_log = _p("data", "snapshots", "outcomes_out.log")
        if os.path.exists(outcomes_log):
            mtime       = os.path.getmtime(outcomes_log)
            last_run_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            last_run_s  = last_run_dt.astimezone(ET).strftime("%b %-d %-I:%M %p CT")
            age_h       = (datetime.now(timezone.utc) - last_run_dt).total_seconds() / 3600
            upd_st      = "🟢 CURRENT" if age_h < 2 else ("🟡 RECENT" if age_h < 12 else "🔴 STALE")
            upd_col     = "color:#10B981" if age_h < 2 else ("color:#F59E0B" if age_h < 12 else "color:#EF4444")
        else:
            last_run_s = "—"; upd_st = "🟡 UNKNOWN"; upd_col = "color:#F59E0B"

        st.markdown(
            f"<div class='scard'>"
            f"<div class='sctitle'>Outcome Updater</div>"
            + scrow("Status",           f"<span style='{upd_col}'>{upd_st}</span>")
            + scrow("Last run",         last_run_s)
            + scrow("Resolved today",   str(len(resolved_today)))
            + scrow("Currently open",   str(len(open_trades)))
            + scrow("Oldest open trade",oldest_open)
            + "</div>",
            unsafe_allow_html=True,
        )

    # ── Live Conditions Gate ──────────────────────────────────────────────────
    st.markdown("<div style='margin-top:22px'></div>", unsafe_allow_html=True)
    st.markdown("**LIVE CONDITIONS GATE**")

    clean_res = summary.get("clean_trades", {}).get("resolved", 0)
    wr_t1_val = summary.get("win_rate_tier1") or 0

    agent_all_covered = (
        not trades_df.empty
        and "agent_verdict" in trades_df.columns
        and trades_df["agent_verdict"].notna().all()
    )

    conditions = [
        ("14+ days of clean snapshot data",        n_snap_days >= 14),
        ("30+ resolved Tier 1 trades",             clean_res >= 30),
        ("Win rate ≥ 58% on Tier 1",               wr_t1_val >= 0.58),
        ("P-value < 0.10",                         pval is not None and pval < 0.10),
        ("Agent attached to all trades",           agent_all_covered),
        ("Position manager tested (5+ cycles)",    False),
        ("Live trade log separate from paper log", False),
        ("LIVE_MODE gate confirmed working",       False),
        ("Starting bankroll confirmed disposable", False),
    ]

    gate_rows = "".join(
        f"<div class='gate-item'>"
        f"<span class='gate-icon' style='color:{'#10B981' if ok else '#EF4444'}'>{'✓' if ok else '✗'}</span>"
        f"<span style='color:#E5E7EB'>{label}</span></div>"
        for label, ok in conditions
    )
    st.markdown(f"<div class='scard' style='margin-top:8px'>{gate_rows}</div>", unsafe_allow_html=True)

    n_fail   = sum(1 for _, ok in conditions if not ok)
    all_pass = n_fail == 0
    if all_pass:
        st.markdown(
            "<div style='margin-top:10px;padding:12px 16px;background:#022C22;"
            "border:1px solid #065F46;border-radius:6px;font-size:13px;color:#10B981'>"
            "🟢 ALL CONDITIONS MET — Live trading authorized</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div style='margin-top:10px;padding:12px 16px;background:#1A0A0A;"
            f"border:1px solid #7F1D1D;border-radius:6px;font-size:13px;color:#EF4444'>"
            f"🔴 {n_fail} condition{'s' if n_fail != 1 else ''} pending — Continue paper trading</div>",
            unsafe_allow_html=True,
        )


# ─── FOOTER ───────────────────────────────────────────────────────────────────
st.markdown(
    f"<div class='ef-footer'>"
    f"<span>EdgeFund &nbsp;·&nbsp; Paper Trading Mode &nbsp;·&nbsp; Not financial advice</span>"
    f"<span>MLB 2026 &nbsp;·&nbsp; Benchmark: Pinnacle &nbsp;·&nbsp; Threshold: |gap| ≥ 5%"
    f"&nbsp;&nbsp;|&nbsp;&nbsp;Last refresh: {datetime.now(ET).strftime('%-I:%M:%S %p CT')}</span>"
    f"</div>",
    unsafe_allow_html=True,
)
