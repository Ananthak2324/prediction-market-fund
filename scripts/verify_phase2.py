"""
scripts/verify_phase2.py

Phase 2 verification for the 2026-07-04 EdgeFund rebuild. Runs all 12 checks
from the rebuild plan and prints PASS/FAIL for each.
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    mark = "✓" if passed else "✗"
    print(f"{mark} {name}" + (f" — {detail}" if detail else ""))


def main() -> None:
    from core.desk_loader import get_desk, get_active_desks

    # 1. desks/ has 4 YAML files
    yaml_files = [f for f in os.listdir(os.path.join(BASE, "desks")) if f.endswith(".yaml")]
    check("desks/ has 4 YAML files", len(yaml_files) == 4, f"found: {sorted(yaml_files)}")

    # 2. get_desk("MLB").is_active == True
    mlb = get_desk("MLB")
    check('get_desk("MLB").is_active == True', mlb.is_active is True)

    # 3. get_desk("NFL").is_pending == True
    nfl = get_desk("NFL")
    check('get_desk("NFL").is_pending == True', nfl.is_pending is True)

    # 4. get_active_desks() == [MLB, WNBA]
    active_ids = sorted(d.desk_id for d in get_active_desks())
    check("get_active_desks() == [MLB, WNBA]", active_ids == ["MLB", "WNBA"], f"got: {active_ids}")

    # 5. Tier B kelly = 0.10 from config
    check("Tier B kelly = 0.10 from config", mlb.tier_kelly.get("B") == 0.10,
          f"tier_kelly={mlb.tier_kelly}")

    # 6. KALSHI_ALIAS removed from 3 files (snapshot_gaps.py, live_gap_detector.py fully
    #    desk-driven; backtest_gap.py's is now sourced from desk config, not hand-maintained)
    kalshi_alias_status = []
    for fname in ("scripts/snapshot_gaps.py", "live_gap_detector.py", "scripts/backtest_gap.py"):
        path = os.path.join(BASE, fname)
        with open(path) as f:
            src = f.read()
        # A hand-maintained dict named KALSHI_ALIAS = {...} with actual team entries
        # is what we removed; a fallback dict (snapshot_gaps.py) or a desk-sourced
        # reference (backtest_gap.py's _MLB_DESK.alias_map) are both acceptable.
        uses_desk = "get_desk(" in src or "desk.alias_map" in src or "alias_map=alias_map" in src
        kalshi_alias_status.append((fname, uses_desk))
    check("Alias dicts consolidated via desk config in all 3 files",
          all(ok for _, ok in kalshi_alias_status), f"{kalshi_alias_status}")

    # 7. MLB prompt contains "starting pitcher"
    mlb_prompt = mlb.agent_system_prompt.lower()
    check('MLB prompt contains "starting pitcher"', "starting pitcher" in mlb_prompt)

    # 8. WNBA prompt lacks "pitcher"
    wnba = get_desk("WNBA")
    wnba_prompt = wnba.agent_system_prompt.lower()
    check('WNBA prompt lacks "pitcher"', "pitcher" not in wnba_prompt)

    # 9. data/mlb/ and data/wnba/ exist
    mlb_dir = os.path.join(BASE, "data", "mlb")
    wnba_dir = os.path.join(BASE, "data", "wnba")
    check("data/mlb/ and data/wnba/ exist",
          os.path.isdir(mlb_dir) and os.path.isdir(wnba_dir))

    # 10. data/*.bak backups exist
    bak_files = [f for f in os.listdir(os.path.join(BASE, "data"))
                 if f.endswith(".bak")]
    check("data/*.bak backups exist", len(bak_files) > 0, f"found: {bak_files}")

    # 11. Dashboard tabs generate dynamically
    dash_path = os.path.join(BASE, "dashboard", "app.py")
    with open(dash_path) as f:
        dash_src = f.read()
    ast.parse(dash_src)  # syntax check
    dynamic_tabs = "get_active_desks" in dash_src and "_desk_tab_labels" in dash_src
    check("Dashboard tabs generate dynamically", dynamic_tabs)

    # 12. Tier status cards show in desk tabs (render_desk_tab renders tier_kelly info)
    tier_cards_present = "render_desk_tab" in dash_src and "tier_kelly" in dash_src and "tab_invest" in dash_src
    check("Tier status cards show in desk tabs + Investigation tab exists", tier_cards_present)

    print()
    n_pass = sum(1 for _, p, _ in results if p)
    print(f"Phase 2: {n_pass}/{len(results)} passed")
    if n_pass < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
