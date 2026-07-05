"""
core/desk_loader.py

Desk configuration layer (2026-07-04 rebuild, Phase 2). Each market (MLB,
WNBA, NFL, ...) is a "desk" defined by desks/base.yaml deep-merged with
desks/<desk_id>.yaml. This replaces the scattered hardcoded sport-specific
constants catalogued in desk_parameter_audit.md.
"""
import os
from pathlib import Path

import yaml

DESKS_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "desks"


def deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, val in override.items():
        if (key in result and
                isinstance(result[key], dict) and
                isinstance(val, dict)):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class DeskConfig:

    def __init__(self, desk_id: str):
        base_path = DESKS_DIR / "base.yaml"
        desk_path = DESKS_DIR / f"{desk_id.lower()}.yaml"

        with open(base_path) as f:
            base = yaml.safe_load(f)
        with open(desk_path) as f:
            desk = yaml.safe_load(f)

        self._config = deep_merge(base, desk)
        self.desk_id = desk_id.upper()

    # ── status ──────────────────────────────────────────────────────────────
    @property
    def is_active(self) -> bool:
        return self._config.get("desk_status") == "ACTIVE"

    @property
    def is_pending(self) -> bool:
        return self._config.get("desk_status") == "PENDING"

    # ── market / odds ───────────────────────────────────────────────────────
    @property
    def series_ticker(self) -> str:
        return self._config["market"]["series_ticker"]

    @property
    def sport_display_key(self) -> str:
        return self._config["market"].get("sport_display_key", self.desk_id)

    @property
    def icon(self) -> str:
        return self._config.get("icon", "")

    @property
    def sport_key(self) -> str:
        return self._config["odds_api"]["sport_key"]

    @property
    def primary_book(self) -> str:
        return self._config["odds_api"]["primary_book"]

    @property
    def books(self) -> list:
        return self._config["odds_api"]["books"]

    # ── thresholds / tiers ──────────────────────────────────────────────────
    @property
    def gap_min(self) -> float:
        return self._config["thresholds"]["gap_min"]

    @property
    def tier_a(self) -> tuple:
        return tuple(self._config["thresholds"]["tier_a"])

    @property
    def tier_b(self) -> tuple:
        return tuple(self._config["thresholds"]["tier_b"])

    @property
    def tier_c(self) -> tuple:
        return tuple(self._config["thresholds"]["tier_c"])

    def gap_tier(self, abs_gap: float) -> str:
        if abs_gap >= self.tier_c[0]:
            return "C"
        if abs_gap >= self.tier_b[0]:
            return "B"
        return "A"

    # ── teams ───────────────────────────────────────────────────────────────
    @property
    def alias_map(self) -> dict:
        return self._config.get("teams", {}).get("alias_map", {})

    @property
    def abbreviation_map(self) -> dict:
        return self._config.get("teams", {}).get("abbreviation_map", {})

    # ── data paths ──────────────────────────────────────────────────────────
    @property
    def paper_trades_path(self) -> str:
        return self._config["data_paths"]["paper_trades"]

    @property
    def skipped_trades_path(self) -> str:
        return self._config["data_paths"]["skipped_trades"]

    @property
    def shadow_trades_path(self) -> str:
        return self._config["data_paths"]["shadow_trades"]

    @property
    def monitor_cache_path(self) -> str:
        return self._config["data_paths"]["monitor_cache"]

    @property
    def snapshots_dir(self) -> str:
        return self._config["data_paths"]["snapshots_dir"]

    @property
    def performance_summary_path(self) -> str:
        return self._config["data_paths"]["performance_summary"]

    @property
    def agent_cost_log_path(self) -> str:
        return self._config["data_paths"]["agent_cost_log"]

    @property
    def funnel_log_path(self) -> str:
        return self._config["data_paths"]["funnel_log"]

    # ── schedule ────────────────────────────────────────────────────────────
    @property
    def cooldown_hours(self) -> float:
        return self._config["schedule"]["cooldown_hours"]

    # ── risk ────────────────────────────────────────────────────────────────
    @property
    def max_concurrent_positions(self) -> int:
        return self._config["risk"]["max_concurrent_positions"]

    @property
    def max_drawdown_pause_pct(self) -> float:
        return self._config["risk"]["max_drawdown_pause_pct"]

    @property
    def starting_bankroll(self) -> float:
        return self._config["risk"]["starting_bankroll"]

    # ── signal gates ────────────────────────────────────────────────────────
    @property
    def active_tiers(self) -> list:
        gates = self._config["signal_gates"]["tiers"]
        return [
            tier for tier, cfg in gates.items()
            if cfg["status"] in ("ACTIVE_FULL", "ACTIVE_REDUCED")
        ]

    @property
    def tier_kelly(self) -> dict:
        gates = self._config["signal_gates"]["tiers"]
        return {tier: cfg["kelly_multiplier"] for tier, cfg in gates.items()}

    @property
    def active_signals(self) -> list:
        return self._config["signal_gates"]["active_signals"]

    # ── agent ───────────────────────────────────────────────────────────────
    @property
    def agent_system_prompt(self) -> str:
        return _build_system_prompt(
            self._config["agent"]["prompt"],
            self._config["thresholds"],
        )

    @property
    def search_query_template(self) -> str:
        return self._config["agent"]["prompt"]["search_query_template"]

    @property
    def chronic_keywords(self) -> list:
        return self._config["agent"].get("chronic_keywords", [])

    @property
    def new_scratch_keywords(self) -> list:
        return self._config["agent"].get("new_scratch_keywords", [])

    # ── generic escape hatch ────────────────────────────────────────────────
    def get(self, key_path: str, default=None):
        keys = key_path.split(".")
        val = self._config
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
                if val is None:
                    return default
            else:
                return default
        return val


def _build_system_prompt(prompt_config: dict, thresholds: dict) -> str:
    disqualifiers = "\n".join(f"  - {d}" for d in prompt_config["disqualifiers"])
    not_disqualifiers = "\n".join(f"  - {d}" for d in prompt_config.get("not_disqualifiers", []))
    behavioral = "\n".join(f"  - {b}" for b in prompt_config.get("behavioral_patterns", []))

    return f"""
You are the EdgeFund Research Analyst for {prompt_config['market_description']} markets.

YOUR PRIMARY JOB IS NOT TO FILTER TRADES.
Your primary job is to EXPLAIN why a gap exists and provide behavioral context
for the trade. Issue SKIP only in rare specific circumstances.

THE PINNACLE STABILITY RULE:
If Pinnacle's line has been stable for 3 hours, any injury or news you find is
already priced in. A stable Pinnacle line overrides most signals.

WHEN TO ISSUE SKIP — only these situations:
{disqualifiers}

DO NOT SKIP FOR THESE — already priced by Pinnacle:
{not_disqualifiers}

BEHAVIORAL PATTERNS TO IDENTIFY:
{behavioral}

SEARCH: {prompt_config['search_query_template']}

{prompt_config.get('search_context', '')}

Respond ONLY with valid JSON. No preamble. No backticks.
"""


def get_active_desks() -> list:
    configs = []
    for yaml_file in sorted(DESKS_DIR.glob("*.yaml")):
        if yaml_file.stem == "base":
            continue
        desk_id = yaml_file.stem.upper()
        try:
            config = DeskConfig(desk_id)
            if config.is_active:
                configs.append(config)
        except Exception as e:
            print(f"Error loading desk {desk_id}: {e}")
    return configs


def get_all_desks() -> list:
    """Like get_active_desks() but includes PENDING desks too."""
    configs = []
    for yaml_file in sorted(DESKS_DIR.glob("*.yaml")):
        if yaml_file.stem == "base":
            continue
        desk_id = yaml_file.stem.upper()
        try:
            configs.append(DeskConfig(desk_id))
        except Exception as e:
            print(f"Error loading desk {desk_id}: {e}")
    return configs


def get_desk(desk_id: str) -> DeskConfig:
    return DeskConfig(desk_id)
