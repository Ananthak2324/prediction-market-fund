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
import sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
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
    Aggregate rows from ALL snapshot files taken today (ET date).
    For each unique event_ticker, keep only the most recent reading.
    Returns (deduplicated_rows, latest_snap_time_label).
    """
    files = sorted(glob.glob(_p("data", "snapshots", "????-??-??_????.json")))
    if not files:
        return [], ""

    # Cutoff = midnight today ET (converted to UTC)
    today_midnight_et = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = today_midnight_et.astimezone(timezone.utc)

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


@st.cache_data(ttl=60)
def load_gap_curves_db(sport_filter: str = "all", hours_window: int = 48) -> pd.DataFrame:
    """Load gap_curves.db rows from the past `hours_window` hours."""
    import sqlite3 as _sqlite3
    from datetime import timedelta
    db_path = _p("data", "gap_curves.db")
    if not os.path.exists(db_path):
        return pd.DataFrame()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_window)).strftime("%Y-%m-%dT%H:%M:%SZ")
        q      = "SELECT * FROM gap_curves WHERE snapshot_utc >= ?"
        params: list = [cutoff]
        if sport_filter and sport_filter.upper() != "ALL":
            q += " AND sport = ?"
            params.append(sport_filter.upper())
        q += " ORDER BY market_ticker, snapshot_utc"
        conn = _sqlite3.connect(db_path)
        df   = pd.read_sql_query(q, conn, params=params)
        conn.close()
        if df.empty:
            return df
        df["hours_to_game"]    = df["seconds_to_close"]   / 3600
        df["hours_since_open"] = df["seconds_since_open"] / 3600
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def fetch_today_schedule(series_ticker: str = "KXMLBGAME") -> list[dict]:
    """
    Fetch today's games from Kalshi for a given series (display-only, no Pinnacle needed).
    Returns [{event_ticker, label, start_et, start_utc}] sorted by start time.
    Cached 5 min. Returns [] on any error.
    """
    import requests
    from core.utils import ticker_to_utc

    KALSHI_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
    today_et    = datetime.now(ET).date()

    try:
        resp = requests.get(
            f"{KALSHI_BASE}/events",
            params={"series_ticker": series_ticker, "status": "open", "limit": 200},
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception:
        return []

    games = []
    seen: set[str] = set()
    for ev in events:
        et_ticker = ev.get("event_ticker", "")
        if not et_ticker or et_ticker in seen:
            continue
        start_utc = ticker_to_utc(et_ticker)
        if start_utc is None:
            continue
        if start_utc.astimezone(ET).date() != today_et:
            continue
        seen.add(et_ticker)
        start_et = start_utc.astimezone(ET)
        title    = ev.get("title", et_ticker)
        games.append({
            "event_ticker": et_ticker,
            "label":        title,
            "start_et":     start_et,
            "start_utc":    start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    return sorted(games, key=lambda g: g["start_et"])


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
tab_mlb, tab_wnba, tab_curves, tab_log, tab_perf, tab_sandbox, tab_sys = st.tabs([
    "⚾ MLB", "🏀 WNBA", "📉 Gap Curves", "📋 Trade Log", "📈 Performance", "💰 Sandbox", "⚙️ System"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB — MLB LIVE GAP SCANNER
# ══════════════════════════════════════════════════════════════════════════════
with tab_mlb:
    now_utc       = datetime.now(timezone.utc)
    today_et_date = now_utc.astimezone(ET).date()

    # ── Build gap records from all open markets in latest snapshots ───────────
    # No date restriction — biggest gaps often appear when Kalshi markets open
    # days before game time. Show all open markets sorted by gap size.
    records = []
    snapped_tickers: set[str] = set()
    mlb_rows = [r for r in all_recent_rows if r.get("sport", "MLB") == "MLB"]
    for row in mlb_rows:
        gap     = row.get("gap", 0) or 0
        abs_gap = row.get("abs_gap", abs(gap))
        signal  = "BUY_YES" if gap <= 0 else "BUY_NO"
        tier    = 1 if abs_gap >= 0.10 else 2

        try:
            game_dt = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
            hours   = round((game_dt - now_utc).total_seconds() / 3600, 1)
        except Exception:
            hours = None

        # Drop only games that ended >4h ago (Kalshi settled)
        if hours is not None and hours < -4:
            continue

        if game_dt.astimezone(ET).date() == today_et_date:
            snapped_tickers.add(row.get("event_ticker", ""))

        if abs_gap >= 0.05 and tier == 1:
            action = "TRADE ✓"
        elif abs_gap >= 0.03:
            action = "WATCH"
        else:
            action = "—"

        try:
            game_date_label = game_dt.astimezone(ET).strftime("%b %-d")
        except Exception:
            game_date_label = "—"

        records.append({
            "Game":          row.get("game", ""),
            "Date":          game_date_label,
            "Game Time":     fmt_game_time(row.get("start_utc", "")),
            "Kalshi":        fmt_pct(row.get("k_prob")),
            "Pinnacle":      fmt_pct(row.get("v_prob")),
            "Gap":           f"{gap * 100:+.1f}%",
            "Signal":        signal,
            "Tier":          tier,
            "Hours":         round(hours, 1) if hours is not None else "—",
            "Action":        action,
            "Snapped":       fmt_snap_time(row.get("_snap_time", "")),
            "_abs_gap":      abs_gap,
        })

    # ── Today's full schedule (games not yet snapped) ─────────────────────────
    schedule = fetch_today_schedule()
    unsnapped = [g for g in schedule if g["event_ticker"] not in snapped_tickers]

    if unsnapped:
        st.markdown("**Today's Schedule** — awaiting snapshot (fires ~2h before first pitch)")
        sched_rows = []
        for g in unsnapped:
            hrs_away = round((g["start_et"].astimezone(timezone.utc) - now_utc).total_seconds() / 3600, 1)
            sched_rows.append({
                "Game":         g["label"],
                "Start (CT)":   g["start_et"].strftime("%-I:%M %p"),
                "Hours Away":   hrs_away,
                "Status":       "Pending snapshot",
            })
        st.dataframe(pd.DataFrame(sched_rows), use_container_width=True, hide_index=True)
        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

    # ── Gap table (all open markets) ──────────────────────────────────────────
    if records:
        st.markdown("**Open Market Gap Scanner** — Kalshi vs Pinnacle · all dates · sorted by gap")
        df_snap = (
            pd.DataFrame(records)
            .sort_values("_abs_gap", ascending=False)
            .drop(columns=["_abs_gap"])
            .reset_index(drop=True)
        )
        st.dataframe(df_snap, use_container_width=True, hide_index=True)

        n_games  = len(records)
        t1_ct    = sum(1 for r in records if r["Tier"] == 1)
        gap3_ct  = sum(1 for r in records if r["Action"] in ("TRADE ✓", "WATCH"))
        st.markdown(
            f"<div style='font-size:11px;color:#6B7280;margin-top:6px'>"
            f"{n_games} sides tracked &nbsp;·&nbsp; {t1_ct} Tier 1 signals &nbsp;·&nbsp; "
            f"{gap3_ct} show |gap| ≥ 3% &nbsp;·&nbsp; "
            f"Latest snap: <span style='color:#9CA3AF;font-family:monospace'>{fmt_snap_time(latest_snap_label)}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    elif not unsnapped:
        st.info("No open markets found in latest snapshots.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB — WNBA LIVE GAP SCANNER
# ══════════════════════════════════════════════════════════════════════════════
with tab_wnba:
    now_utc_w       = datetime.now(timezone.utc)
    today_et_date_w = now_utc_w.astimezone(ET).date()

    wnba_rows = [r for r in all_recent_rows if r.get("sport") == "WNBA"]
    records_w = []
    snapped_tickers_w: set[str] = set()

    for row in wnba_rows:
        gap     = row.get("gap", 0) or 0
        abs_gap = row.get("abs_gap", abs(gap))
        signal  = "BUY_YES" if gap <= 0 else "BUY_NO"
        tier    = 1 if abs_gap >= 0.10 else 2

        try:
            game_dt_w = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
            hours_w   = round((game_dt_w - now_utc_w).total_seconds() / 3600, 1)
        except Exception:
            game_dt_w = None
            hours_w   = None

        if hours_w is not None and hours_w < -4:
            continue

        if game_dt_w is not None and game_dt_w.astimezone(ET).date() == today_et_date_w:
            snapped_tickers_w.add(row.get("event_ticker", ""))

        if abs_gap >= 0.05 and tier == 1:
            action = "TRADE ✓"
        elif abs_gap >= 0.03:
            action = "WATCH"
        else:
            action = "—"

        try:
            game_date_label_w = game_dt_w.astimezone(ET).strftime("%b %-d") if game_dt_w else "—"
        except Exception:
            game_date_label_w = "—"

        records_w.append({
            "Game":     row.get("game", ""),
            "Date":     game_date_label_w,
            "Game Time": fmt_game_time(row.get("start_utc", "")),
            "Kalshi":   fmt_pct(row.get("k_prob")),
            "Pinnacle": fmt_pct(row.get("v_prob")),
            "Gap":      f"{gap * 100:+.1f}%",
            "Signal":   signal,
            "Tier":     tier,
            "Hours":    round(hours_w, 1) if hours_w is not None else "—",
            "Action":   action,
            "Snapped":  fmt_snap_time(row.get("_snap_time", "")),
            "_abs_gap": abs_gap,
        })

    schedule_w = fetch_today_schedule("KXWNBAGAME")
    unsnapped_w = [g for g in schedule_w if g["event_ticker"] not in snapped_tickers_w]

    if unsnapped_w:
        st.markdown("**Today's Schedule** — awaiting snapshot (fires ~2h before tip-off)")
        sched_rows_w = []
        for g in unsnapped_w:
            hrs_away = round((g["start_et"].astimezone(timezone.utc) - now_utc_w).total_seconds() / 3600, 1)
            sched_rows_w.append({
                "Game":       g["label"],
                "Start (CT)": g["start_et"].strftime("%-I:%M %p"),
                "Hours Away": hrs_away,
                "Status":     "Pending snapshot",
            })
        st.dataframe(pd.DataFrame(sched_rows_w), use_container_width=True, hide_index=True)
        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

    if records_w:
        st.markdown("**Open Market Gap Scanner** — Kalshi vs Pinnacle · all dates · sorted by gap")
        df_snap_w = (
            pd.DataFrame(records_w)
            .sort_values("_abs_gap", ascending=False)
            .drop(columns=["_abs_gap"])
            .reset_index(drop=True)
        )
        st.dataframe(df_snap_w, use_container_width=True, hide_index=True)

        n_games_w = len(records_w)
        t1_ct_w   = sum(1 for r in records_w if r["Tier"] == 1)
        gap3_ct_w = sum(1 for r in records_w if r["Action"] in ("TRADE ✓", "WATCH"))
        st.markdown(
            f"<div style='font-size:11px;color:#6B7280;margin-top:6px'>"
            f"{n_games_w} sides tracked &nbsp;·&nbsp; {t1_ct_w} Tier 1 signals &nbsp;·&nbsp; "
            f"{gap3_ct_w} show |gap| ≥ 3% &nbsp;·&nbsp; "
            f"Latest snap: <span style='color:#9CA3AF;font-family:monospace'>{fmt_snap_time(latest_snap_label)}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    elif not unsnapped_w:
        st.info("No open WNBA markets found in latest snapshots.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB — GAP CURVES
# ══════════════════════════════════════════════════════════════════════════════
with tab_curves:
    gc_ctrl1, gc_ctrl2, _ = st.columns([2, 2, 6])
    with gc_ctrl1:
        gc_sport = st.selectbox("Sport", ["All", "MLB", "WNBA"], key="gc_sport")
    with gc_ctrl2:
        gc_window_label = st.selectbox("Window", ["Last 24h", "Last 48h", "Last 7 days"], key="gc_window")

    _window_map = {"Last 24h": 24, "Last 48h": 48, "Last 7 days": 168}
    gc_df = load_gap_curves_db(gc_sport.lower(), _window_map[gc_window_label])

    if gc_df.empty:
        st.info("No gap curve data yet — the tracker polls every 5 min and writes to data/gap_curves.db. Check back after the next sync.")
    else:
        # Drop thinly-sampled markets (< 2 snapshots)
        _counts = gc_df.groupby("market_ticker")["id"].count()
        gc_df   = gc_df[gc_df["market_ticker"].isin(_counts[_counts >= 2].index)].copy()

        if gc_df.empty:
            st.info("Markets found but each has fewer than 2 snapshots — check back after the next poll cycle.")
        else:
            chart_col, table_col = st.columns([3, 2])

            with chart_col:
                st.markdown("**Gap Trajectory** — |Kalshi − Pinnacle| vs hours to game start")

                # One color per event (home + away sides share same color, away = dashed)
                _events = gc_df["event_ticker"].unique()
                _palette = [
                    "#00C896", "#3B82F6", "#F59E0B", "#A78BFA", "#EC4899",
                    "#10B981", "#60A5FA", "#FBBF24", "#C084FC", "#F472B6",
                    "#34D399", "#93C5FD", "#FCD34D", "#D8B4FE", "#FB923C",
                ]
                _ev_color = {ev: _palette[i % len(_palette)] for i, ev in enumerate(_events)}

                fig_gc = go.Figure()
                fig_gc.add_hline(
                    y=5.0, line_dash="dash", line_color="#065F46", line_width=1,
                    annotation_text="5% threshold",
                    annotation_font_color="#065F46", annotation_font_size=9,
                )
                fig_gc.add_vline(x=0, line_dash="dot", line_color="#7F1D1D", line_width=1)

                _seen_events: set = set()
                for _, _grp in gc_df.groupby("market_ticker"):
                    _grp  = _grp.sort_values("snapshot_utc")
                    _ev   = _grp["event_ticker"].iloc[0]
                    _col  = _ev_color[_ev]
                    _team = _grp["team"].iloc[0]
                    _game = _grp["game"].iloc[0]
                    _dash = "dash" if _grp["side"].iloc[0] == "AWAY" else "solid"
                    _show = _ev not in _seen_events
                    _seen_events.add(_ev)

                    fig_gc.add_trace(go.Scatter(
                        x=_grp["hours_to_game"],
                        y=_grp["abs_gap"] * 100,
                        mode="lines+markers",
                        line=dict(color=_col, width=2, dash=_dash),
                        marker=dict(size=4),
                        name=_game if _show else None,
                        legendgroup=_ev,
                        showlegend=_show,
                        hovertemplate=(
                            f"<b>{_game}</b><br>"
                            f"{_team}<br>"
                            "|Gap|: %{y:.1f}%<br>"
                            "Hours to game: %{x:.1f}h<extra></extra>"
                        ),
                    ))

                fig_gc.update_layout(
                    xaxis=dict(
                        title="Hours to Game  (right = game time, 0 = first pitch/tip-off)",
                        autorange="reversed",
                        gridcolor="#1F2937",
                        zerolinecolor="#7F1D1D",
                    ),
                    yaxis=dict(
                        title="|Gap| (%)",
                        ticksuffix="%",
                        gridcolor="#1F2937",
                        zerolinecolor="#1F2937",
                    ),
                    legend=dict(
                        font=dict(size=9, color="#9CA3AF"),
                        bgcolor="#0D1117",
                        bordercolor="#1F2937",
                        title_text="— solid = HOME  · · dashed = AWAY",
                        title_font=dict(size=8, color="#6B7280"),
                    ),
                )
                st.plotly_chart(dark_plotly(fig_gc, height=360), use_container_width=True)

            with table_col:
                st.markdown("**Latest Snapshot** — sorted by gap")

                # One row per market_ticker — the most recent reading
                _latest = (
                    gc_df.sort_values("snapshot_utc")
                    .groupby("market_ticker")
                    .last()
                    .reset_index()
                    .sort_values("abs_gap", ascending=False)
                )

                def _gc_action(g: float) -> str:
                    return "TRADE ✓" if g >= 0.05 else ("WATCH" if g >= 0.03 else "—")

                _snap_rows = []
                for _, _r in _latest.iterrows():
                    _h = _r.get("hours_to_game")
                    _snap_rows.append({
                        "Game":   _r["game"],
                        "Team":   _r["team"],
                        "Gap":    f"{_r['abs_gap'] * 100:.1f}%",
                        "Hours":  f"{_h:.1f}h" if pd.notna(_h) else "—",
                        "Action": _gc_action(_r["abs_gap"]),
                    })
                st.dataframe(pd.DataFrame(_snap_rows), use_container_width=True, hide_index=True)

                _n_markets = gc_df["market_ticker"].nunique()
                _n_games   = gc_df["event_ticker"].nunique()
                _n_rows    = len(gc_df)
                st.markdown(
                    f"<div style='font-size:11px;color:#6B7280;margin-top:6px'>"
                    f"{_n_games} games &nbsp;·&nbsp; {_n_markets} market sides &nbsp;·&nbsp; "
                    f"{_n_rows} snapshots &nbsp;·&nbsp; avg {_n_rows / _n_markets:.1f}/side"
                    f"</div>",
                    unsafe_allow_html=True,
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB — PAPER TRADE LOG
# ══════════════════════════════════════════════════════════════════════════════
with tab_log:
    if trades_df.empty:
        st.info("No trades logged yet. Snapshot script is running.")
    else:
        df = trades_df.copy()

        # Build game_date_et for filtering (raw date string in ET)
        def _game_date_et(utc_str):
            try:
                dt = datetime.fromisoformat(str(utc_str).replace("Z", "+00:00"))
                return dt.astimezone(ET).strftime("%Y-%m-%d")
            except Exception:
                return None

        if "start_utc" in df.columns:
            df["_game_date"] = df["start_utc"].apply(_game_date_et)
        else:
            df["_game_date"] = None

        # Sorted unique game dates for the dropdown (display as "Jun 22")
        raw_dates = sorted(df["_game_date"].dropna().unique().tolist())
        date_labels = {d: datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d") for d in raw_dates}

        fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])
        with fc1:
            sports    = ["All"] + sorted(df["sport"].dropna().unique().tolist())
            sel_sport = st.selectbox("Sport", sports, key="t2_sport")
        with fc2:
            sel_outcome = st.selectbox("Outcome", ["All", "WIN", "LOSS", "OPEN", "SKIPPED"], key="t2_outcome")
        with fc3:
            sel_tier = st.selectbox("Tier", ["All", "Tier 1", "Tier 2"], key="t2_tier")
        with fc4:
            date_options = ["All"] + [date_labels[d] for d in raw_dates]
            sel_date_label = st.selectbox("Game Date", date_options, key="t2_game_date")

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
        if sel_date_label != "All":
            # Map label back to raw date
            sel_raw_date = next((d for d, lbl in date_labels.items() if lbl == sel_date_label), None)
            if sel_raw_date:
                df = df[df["_game_date"] == sel_raw_date]

        df = df.sort_values("snapshot_time", ascending=False)

        def _short_id(tid):
            return str(tid)[-8:] if pd.notna(tid) else "—"

        agent_col      = df["agent_verdict"]      if "agent_verdict"      in df.columns else pd.Series(["—"] * len(df), index=df.index)
        confidence_col = df["agent_confidence"]   if "agent_confidence"   in df.columns else pd.Series(["—"] * len(df), index=df.index)

        def _fmt_game_date(utc_str):
            try:
                dt = datetime.fromisoformat(str(utc_str).replace("Z", "+00:00"))
                return dt.astimezone(ET).strftime("%b %-d")
            except Exception:
                return "—"

        display = pd.DataFrame({
            "ID":         df["trade_id"].apply(_short_id),
            "Game Date":  df["start_utc"].apply(_fmt_game_date) if "start_utc" in df.columns else "—",
            "Game Start": df["start_utc"].apply(fmt_game_time) if "start_utc" in df.columns else "—",
            "Captured":   df["snapshot_time"].apply(fmt_snap_time),
            "Game":       df["game"],
            "Signal":     df.apply(
                              lambda r: f"YES · {r['team']}" if r.get("signal") == "BUY_YES"
                                        else (f"NO · {r['team']}" if r.get("signal") == "BUY_NO" else r.get("signal", "—")),
                              axis=1,
                          ),
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
# TAB — PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════
with tab_perf:
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
# TAB — SANDBOX PORTFOLIO
# ══════════════════════════════════════════════════════════════════════════════
with tab_sandbox:
    import sqlite3 as _sqlite3

    _DB = _p("data", "paper_trades.db")

    def _sb_load() -> tuple[dict, list[dict], list[dict], list[dict]]:
        """Returns (config, open_trades, closed_trades, bankroll_history)."""
        if not os.path.exists(_DB):
            return {}, [], [], []
        try:
            conn = _sqlite3.connect(_DB)
            conn.row_factory = _sqlite3.Row
            cfg = conn.execute("SELECT * FROM sandbox_config WHERE id=1").fetchone()
            config = dict(cfg) if cfg else {}
            open_t  = [dict(r) for r in conn.execute(
                "SELECT * FROM sandbox_trades WHERE status='OPEN' ORDER BY created_at DESC"
            ).fetchall()]
            closed_t = [dict(r) for r in conn.execute(
                "SELECT * FROM sandbox_trades WHERE status='CLOSED' ORDER BY exit_time DESC"
            ).fetchall()]
            history = [dict(r) for r in conn.execute(
                "SELECT * FROM sandbox_bankroll_history ORDER BY timestamp ASC"
            ).fetchall()]
            conn.close()
            return config, open_t, closed_t, history
        except Exception:
            return {}, [], [], []

    @st.cache_data(ttl=60)
    def _fetch_live_prices(tickers_signal: list[tuple[str, str]]) -> dict[str, float]:
        """Fetch current contract prices for open positions (60s cache)."""
        KALSHI = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
        prices = {}
        import requests as _req
        for ticker, signal in tickers_signal:
            try:
                r = _req.get(f"{KALSHI}/markets/{ticker}", timeout=8)
                if r.status_code != 200:
                    continue
                m   = r.json().get("market", {})
                bid = float(m.get("yes_bid_dollars") or 0)
                ask = float(m.get("yes_ask_dollars") or 1)
                if bid == 0 and ask >= 0.99:
                    continue
                yes_mid = (bid + ask) / 2.0
                prices[ticker] = yes_mid if signal == "BUY_YES" else round(1.0 - yes_mid, 4)
            except Exception:
                pass
        return prices

    sb_config, sb_open, sb_closed, sb_history = _sb_load()

    if not sb_config:
        st.info("Sandbox not initialized yet. Run `python scripts/backfill_sandbox.py` to set up.")
    else:
        bankroll_start   = sb_config.get("bankroll_start", 1000.0)
        total_pnl        = sum(t.get("pnl_dollars") or 0 for t in sb_closed)
        total_bankroll   = bankroll_start + total_pnl
        capital_deployed = sum(t.get("actual_cost") or 0 for t in sb_open)
        available_cash   = total_bankroll - capital_deployed
        total_return_pct = (total_bankroll - bankroll_start) / bankroll_start if bankroll_start else 0
        pnl_sign         = "+" if total_pnl >= 0 else ""

        # ── ROW 1: Metric cards ───────────────────────────────────────────────
        sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
        deployed_pct = capital_deployed / total_bankroll if total_bankroll else 0
        _sb_cards = [
            (sc1, "Starting Bankroll", f"<span class='gray'>${bankroll_start:,.2f}</span>", "&nbsp;"),
            (sc2, "Current Bankroll",
             f"<span class='{'teal' if total_bankroll >= bankroll_start else 'red'}'>${total_bankroll:,.2f}</span>",
             "&nbsp;"),
            (sc3, "Total Return",
             f"<span class='{'teal' if total_pnl >= 0 else 'red'}'>{pnl_sign}${total_pnl:.2f}</span>",
             f"{pnl_sign}{total_return_pct * 100:.2f}%"),
            (sc4, "Available Cash", f"<span class='teal'>${available_cash:,.2f}</span>", "&nbsp;"),
            (sc5, "Capital Deployed",
             f"<span class='{'yellow' if deployed_pct > 0.30 else 'teal'}'>${capital_deployed:.2f}</span>",
             f"{deployed_pct * 100:.1f}% of bankroll"),
            (sc6, "Open Positions", f"<span class='teal'>{len(sb_open)}</span>", "&nbsp;"),
        ]
        for col, label, val, sub in _sb_cards:
            with col:
                st.markdown(f"""
                <div class="mcard">
                  <div class="mlabel">{label}</div>
                  <div class="mval">{val}</div>
                  <div class="msub">{sub}</div>
                </div>""", unsafe_allow_html=True)

        # ── ROW 2: Risk metric cards (Sharpe, Max Drawdown) ───────────────────
        import statistics as _stats_sb

        _sb_sharpe    = None
        _sb_max_dd    = None
        _closed_rets  = [t.get("pnl_pct") for t in sb_closed if t.get("pnl_pct") is not None]
        if len(_closed_rets) >= 2:
            _mean_r = sum(_closed_rets) / len(_closed_rets)
            _std_r  = _stats_sb.stdev(_closed_rets)
            _sb_sharpe = round(_mean_r / _std_r, 3) if _std_r > 0 else None

        if sb_history:
            _bks  = [r.get("bankroll") for r in sb_history if r.get("bankroll") is not None]
            if _bks:
                _peak, _max_dd_val = _bks[0], 0.0
                for _b in _bks:
                    _peak = max(_peak, _b)
                    _dd   = (_peak - _b) / _peak if _peak > 0 else 0.0
                    _max_dd_val = max(_max_dd_val, _dd)
                _sb_max_dd = round(_max_dd_val, 4)

        _n_closed       = len(sb_closed)
        _need_more      = _n_closed < 5
        _sharpe_val_str = (
            "<span class='gray'>N/A (&lt;5 trades)</span>" if _need_more
            else (
                f"<span class='{'teal' if _sb_sharpe and _sb_sharpe > 0 else 'red'}'>{_sb_sharpe:.3f}</span>"
                if _sb_sharpe is not None else "<span class='gray'>—</span>"
            )
        )
        _dd_val_str = (
            "<span class='gray'>N/A (&lt;5 trades)</span>" if _need_more
            else (
                f"<span class='{'teal' if _sb_max_dd is not None and _sb_max_dd < 0.15 else 'red'}'>"
                f"{_sb_max_dd * 100:.1f}%</span>"
                if _sb_max_dd is not None else "<span class='gray'>—</span>"
            )
        )
        _r2c1, _r2c2, _ = st.columns([1, 1, 4])
        _r2_cards = [
            (_r2c1, "Sharpe Ratio",   _sharpe_val_str, f"{_n_closed} closed trade(s)"),
            (_r2c2, "Max Drawdown",   _dd_val_str,     "peak-to-trough"),
        ]
        for col, label, val, sub in _r2_cards:
            with col:
                st.markdown(f"""
                <div class="mcard">
                  <div class="mlabel">{label}</div>
                  <div class="mval">{val}</div>
                  <div class="msub">{sub}</div>
                </div>""", unsafe_allow_html=True)

        st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

        # ── ROW 2: Charts ─────────────────────────────────────────────────────
        ch1, ch2 = st.columns(2)

        with ch1:
            st.markdown("**Bankroll Over Time**")
            if sb_history:
                hist_df = pd.DataFrame(sb_history)
                hist_df["timestamp"] = pd.to_datetime(hist_df["timestamp"])
                hist_df = hist_df[hist_df["event_type"] == "TRADE_CLOSE"].copy()

                if hist_df.empty:
                    # Show just the starting point
                    hist_df = pd.DataFrame([{
                        "timestamp": pd.Timestamp(sb_config.get("start_date", "2026-06-25")),
                        "bankroll":  bankroll_start,
                    }])

                # Insert starting point
                start_row = pd.DataFrame([{
                    "timestamp": hist_df["timestamp"].min() - pd.Timedelta(minutes=1),
                    "bankroll":  bankroll_start,
                }])
                hist_df = pd.concat([start_row, hist_df[["timestamp","bankroll"]]], ignore_index=True)

                peak = hist_df["bankroll"].max()

                fig_bk = go.Figure()
                fig_bk.add_hline(y=bankroll_start, line_dash="dash",
                                  line_color="#374151", line_width=1,
                                  annotation_text="Start $1,000",
                                  annotation_font_color="#6B7280", annotation_font_size=9)
                fig_bk.add_trace(go.Scatter(
                    x=hist_df["timestamp"], y=hist_df["bankroll"],
                    mode="lines+markers",
                    line=dict(color="#00C896", width=2),
                    marker=dict(size=5),
                    fill="tozeroy",
                    fillcolor="rgba(0,200,150,0.08)",
                    name="Bankroll",
                ))
                fig_bk.add_annotation(
                    x=hist_df["timestamp"].iloc[-1], y=hist_df["bankroll"].iloc[-1],
                    text=f"Now ${total_bankroll:,.2f}",
                    showarrow=True, arrowcolor="#00C896",
                    font=dict(color="#00C896", size=10),
                )
                if peak > bankroll_start:
                    peak_row = hist_df.loc[hist_df["bankroll"].idxmax()]
                    fig_bk.add_annotation(
                        x=peak_row["timestamp"], y=peak,
                        text=f"Peak ${peak:,.2f}",
                        showarrow=False,
                        font=dict(color="#F59E0B", size=9),
                        yshift=12,
                    )
                fig_bk.update_layout(showlegend=False, yaxis=dict(tickprefix="$"))
                st.plotly_chart(dark_plotly(fig_bk), use_container_width=True)
            else:
                st.info("No closed positions yet — chart will appear after first exit.")

        with ch2:
            st.markdown("**P&L per Trade**")
            if sb_closed:
                pnl_df = pd.DataFrame(sb_closed)
                pnl_df = pnl_df.sort_values("exit_time")
                pnl_df["label"] = pnl_df["game"].apply(lambda g: g.split(" @ ")[-1][:12])
                bar_colors = ["#00C896" if v >= 0 else "#EF4444" for v in pnl_df["pnl_dollars"]]
                fig_pnl = go.Figure(go.Bar(
                    x=pnl_df["label"],
                    y=pnl_df["pnl_dollars"],
                    marker_color=bar_colors,
                    text=[f"${v:+.2f}" for v in pnl_df["pnl_dollars"]],
                    textposition="outside",
                    textfont=dict(size=10, color="#E5E7EB"),
                    customdata=pnl_df[["game","exit_type","shares","entry_price","exit_price"]].values,
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        "Exit: %{customdata[1]}<br>"
                        "Shares: %{customdata[2]}<br>"
                        "Entry: $%{customdata[3]:.3f}  Exit: $%{customdata[4]:.3f}<br>"
                        "P&L: $%{y:+.2f}<extra></extra>"
                    ),
                ))
                fig_pnl.add_hline(y=0, line_color="#374151", line_width=1)
                fig_pnl.update_layout(showlegend=False, yaxis=dict(tickprefix="$"))
                st.plotly_chart(dark_plotly(fig_pnl), use_container_width=True)
            else:
                st.info("No closed positions yet.")

        # ── ROW 3: Open Positions ─────────────────────────────────────────────
        st.markdown("**Open Positions**")
        if not sb_open:
            st.info("No open positions.")
        else:
            ticker_signal_pairs = [(t["kalshi_ticker"], t["signal"]) for t in sb_open]
            live_prices = _fetch_live_prices(ticker_signal_pairs)

            open_rows = []
            for t in sb_open:
                cp   = live_prices.get(t["kalshi_ticker"])
                ep   = t["entry_price"] or 0
                pp   = t["pinnacle_prob"] or 0
                unr  = round((cp - ep) * (t["shares"] or 0), 2) if cp else None
                unr_pct = round((cp - ep) / ep * 100, 1) if cp and ep else None

                if cp is not None:
                    triggers = []
                    pnl_pct_f = (cp - ep) / ep if ep else 0
                    if cp >= pp:
                        triggers.append("FAIR_VALUE")
                    elif pnl_pct_f <= -0.40:
                        triggers.append("STOP_LOSS")
                    elif pnl_pct_f >= 0.80:
                        triggers.append("PROFIT_TARGET")
                    next_trigger = triggers[0] if triggers else "HOLD"
                else:
                    next_trigger = "—"

                open_rows.append({
                    "Game":             t["game"],
                    "Game Date":        (datetime.fromisoformat(t["start_utc"].replace("Z","+00:00"))
                                         .astimezone(ET).strftime("%b %-d")
                                         if t.get("start_utc") else "—"),
                    "Game Start":       fmt_game_time(t["start_utc"]) if t.get("start_utc") else "—",
                    "Signal":           t["signal"],
                    "Shares":           t["shares"],
                    "Entry":            f"${ep:.3f}",
                    "Current":          f"${cp:.3f}" if cp else "—",
                    "Fair Value":       f"${pp:.3f}",
                    "Unreal P&L ($)":   f"${unr:+.2f}" if unr is not None else "—",
                    "P&L %":            f"{unr_pct:+.1f}%" if unr_pct is not None else "—",
                    "Next Trigger":     next_trigger,
                    "Cost":             f"${t['actual_cost']:.2f}",
                })
            st.dataframe(pd.DataFrame(open_rows), use_container_width=True, hide_index=True)

        # ── ROW 4: Closed Positions ───────────────────────────────────────────
        st.markdown("**Closed Positions**")
        if not sb_closed:
            st.info("No closed positions yet.")
        else:
            closed_rows = []
            for t in sb_closed:
                closed_rows.append({
                    "Game":         t["game"],
                    "Entry Date":   t["entry_date"] or "—",
                    "Exit":         str(t.get("exit_time") or "")[:16].replace("T", " "),
                    "Shares":       t["shares"],
                    "Entry $":      f"${(t['entry_price'] or 0):.3f}",
                    "Exit $":       f"${(t['exit_price'] or 0):.3f}",
                    "Exit Type":    t.get("exit_type") or "—",
                    "P&L $":        f"${(t['pnl_dollars'] or 0):+.2f}",
                    "P&L %":        f"{(t['pnl_pct'] or 0) * 100:+.1f}%",
                    "Kelly Used":   f"{(t['position_fraction'] or 0) * 100:.2f}%",
                })
            closed_df = pd.DataFrame(closed_rows)
            st.dataframe(closed_df, use_container_width=True, hide_index=True)

            # Footer totals
            total_closed_pnl = sum(t.get("pnl_dollars") or 0 for t in sb_closed)
            avg_ret = sum(t.get("pnl_pct") or 0 for t in sb_closed) / len(sb_closed)
            best    = max(sb_closed, key=lambda t: t.get("pnl_dollars") or 0)
            worst   = min(sb_closed, key=lambda t: t.get("pnl_dollars") or 0)
            st.markdown(
                f"<div style='font-size:11px;color:#6B7280;margin-top:4px'>"
                f"Total P&L: <b style='color:{'#10B981' if total_closed_pnl>=0 else '#EF4444'}'>"
                f"${total_closed_pnl:+.2f}</b> &nbsp;·&nbsp; "
                f"Avg return: <b style='color:#9CA3AF'>{avg_ret*100:+.1f}%</b> &nbsp;·&nbsp; "
                f"Best: <b style='color:#10B981'>${(best.get('pnl_dollars') or 0):+.2f}</b> &nbsp;·&nbsp; "
                f"Worst: <b style='color:#EF4444'>${(worst.get('pnl_dollars') or 0):+.2f}</b>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # ── ROW 5: Exit Rule Performance ──────────────────────────────────────
        st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
        st.markdown("**Exit Rule Performance**")
        exit_rules = ["FAIR_VALUE", "STOP_LOSS", "PROFIT_TARGET", "NEAR_RESOLUTION", "RESOLUTION"]
        exit_rows = []
        for rule in exit_rules:
            trades_for_rule = [t for t in sb_closed if t.get("exit_type") == rule]
            n = len(trades_for_rule)
            if n == 0:
                exit_rows.append({"Rule": rule, "Count": 0, "Avg Return": "—", "Total P&L": "—"})
                continue
            avg_r   = sum(t.get("pnl_pct") or 0 for t in trades_for_rule) / n
            tot_pnl = sum(t.get("pnl_dollars") or 0 for t in trades_for_rule)
            exit_rows.append({
                "Rule":       rule,
                "Count":      n,
                "Avg Return": f"{avg_r * 100:+.1f}%",
                "Total P&L":  f"${tot_pnl:+.2f}",
            })
        st.dataframe(pd.DataFrame(exit_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB — SYSTEM STATUS
# ══════════════════════════════════════════════════════════════════════════════
with tab_sys:
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
    f"<span>MLB + WNBA 2026 &nbsp;·&nbsp; Benchmark: Pinnacle &nbsp;·&nbsp; Threshold: |gap| ≥ 5%"
    f"&nbsp;&nbsp;|&nbsp;&nbsp;Last refresh: {datetime.now(ET).strftime('%-I:%M:%S %p CT')}</span>"
    f"</div>",
    unsafe_allow_html=True,
)
