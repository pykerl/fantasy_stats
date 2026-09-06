"""League scoring.

Every source that exposes raw stat projections is re-scored under the league's
own rules, so a 0.5-PPR league does not silently inherit a site's full-PPR
point totals. Sources that only publish a point total fall back to that total.

Canonical stat keys follow Sleeper's naming, which the other adapters map into.
"""
from __future__ import annotations

import logging
import re
from typing import Mapping

log = logging.getLogger(__name__)

# Standard full-PPR fallback, used when the Yahoo league is unavailable.
DEFAULT_SCORING: dict[str, float] = {
    "pass_yd": 0.04,
    "pass_td": 4.0,
    "pass_int": -1.0,
    "pass_2pt": 2.0,
    "rush_yd": 0.1,
    "rush_td": 6.0,
    "rush_2pt": 2.0,
    "rec": 1.0,
    "rec_yd": 0.1,
    "rec_td": 6.0,
    "rec_2pt": 2.0,
    "fum_lost": -2.0,
    "xpm": 1.0,
    "fgm_0_19": 3.0,
    "fgm_20_29": 3.0,
    "fgm_30_39": 3.0,
    "fgm_0_39": 3.0,
    "fgm_40_49": 4.0,
    "fgm_50p": 5.0,
}

# Sources bucket made field goals differently: Sleeper/Yahoo split 0-19/20-29/30-39,
# ESPN reports a single 0-39 bucket. `fgm_0_39` is derived from the league's
# sub-bucket values so either shape scores correctly; a source must never
# provide both shapes or the kick would be counted twice.
_FG_SUB_BUCKETS = ("fgm_0_19", "fgm_20_29", "fgm_30_39")

# Yahoo publishes stat categories with human names; we key off the name rather
# than the numeric stat_id because Yahoo's ids differ between offense/kicker/DEF
# blocks and have moved historically.
_YAHOO_NAME_MAP: list[tuple[str, str]] = [
    (r"^passing yards$", "pass_yd"),
    (r"^passing touchdowns$", "pass_td"),
    (r"^interceptions$", "pass_int"),
    (r"^passing attempts$", "pass_att"),
    (r"^completions$", "pass_cmp"),
    (r"^incomplete passes$", "pass_inc"),
    (r"^rushing yards$", "rush_yd"),
    (r"^rushing touchdowns$", "rush_td"),
    (r"^rushing attempts$", "rush_att"),
    (r"^receptions$", "rec"),
    (r"^(reception|receiving) yards$", "rec_yd"),
    (r"^(reception|receiving) touchdowns$", "rec_td"),
    (r"^targets$", "rec_tgt"),
    (r"^return yards$", "ret_yd"),
    (r"^return touchdowns$", "ret_td"),
    (r"^2-?point conversions$", "two_pt"),
    (r"^fumbles lost$", "fum_lost"),
    (r"^fumbles$", "fum"),
    (r"^offensive fumble return td$", "fum_ret_td"),
    (r"^point after attempt made$", "xpm"),
    (r"^point after attempt missed$", "xpm_miss"),
    (r"^field goals 0-19 yards$", "fgm_0_19"),
    (r"^field goals 20-29 yards$", "fgm_20_29"),
    (r"^field goals 30-39 yards$", "fgm_30_39"),
    (r"^field goals 40-49 yards$", "fgm_40_49"),
    (r"^field goals 50\+ yards$", "fgm_50p"),
    (r"^points allowed$", "def_pa"),
    (r"^sacks?$", "def_sack"),
    (r"^fumble recovery$", "def_fum_rec"),
    (r"^touchdowns?$", "def_td"),
    (r"^safeties$", "def_safe"),
    (r"^block(ed)? kick$", "def_blk"),
]


def map_yahoo_stat_name(name: str) -> str | None:
    """Map a Yahoo stat category display name to a canonical stat key."""
    key = name.strip().lower()
    for pattern, canonical in _YAHOO_NAME_MAP:
        if re.match(pattern, key):
            return canonical
    return None


def scoring_from_yahoo(stat_categories: Mapping, stat_modifiers: Mapping) -> dict[str, float]:
    """Build a canonical scoring dict from Yahoo's categories + modifiers.

    `stat_categories` maps stat_id -> {"name": ...}; `stat_modifiers` maps
    stat_id -> points-per-unit. Unmapped categories are logged and dropped
    rather than silently mis-scored.
    """
    scoring: dict[str, float] = {}
    unmapped: list[str] = []
    for stat_id, value in stat_modifiers.items():
        meta = stat_categories.get(stat_id) or stat_categories.get(str(stat_id)) or {}
        name = meta.get("name") or meta.get("display_name") or ""
        canonical = map_yahoo_stat_name(name)
        if canonical is None:
            if name:
                unmapped.append(f"{name} (id {stat_id})")
            continue
        try:
            scoring[canonical] = float(value)
        except (TypeError, ValueError):
            continue
    if unmapped:
        log.warning("Yahoo stat categories with no canonical mapping: %s", ", ".join(unmapped))
    if not scoring:
        log.warning("no Yahoo scoring recovered; falling back to standard PPR")
        return dict(DEFAULT_SCORING)
    # Yahoo expresses 2-point conversions as one category; split it across the
    # three canonical keys so every source's raw stats can score it.
    if "two_pt" in scoring:
        two = scoring.pop("two_pt")
        scoring.setdefault("pass_2pt", two)
        scoring.setdefault("rush_2pt", two)
        scoring.setdefault("rec_2pt", two)
    return derive_fg_buckets(scoring)


def derive_fg_buckets(scoring: dict[str, float]) -> dict[str, float]:
    """Add the combined 0-39 FG bucket from whichever sub-buckets the league sets."""
    values = [scoring[k] for k in _FG_SUB_BUCKETS if k in scoring]
    if values and "fgm_0_39" not in scoring:
        scoring["fgm_0_39"] = sum(values) / len(values)
    return scoring


def score_stats(stats: Mapping[str, float], scoring: Mapping[str, float]) -> float:
    """Dot-product a raw stat line with the league's scoring rules."""
    total = 0.0
    for key, per_unit in scoring.items():
        value = stats.get(key)
        if value:
            total += float(value) * float(per_unit)
    return round(total, 3)


def ppr_value(scoring: Mapping[str, float]) -> float:
    return float(scoring.get("rec", 0.0))
