"""
LakerSinceBirth — Stat Pull Script
Pulls team, player, lineup, and clutch data for the LA Lakers via nba_api
and writes a JSON snapshot shaped for the Tier 2 Prep Dashboard.

Fields NOT covered by nba_api (left blank / manual in the dashboard):
  - vs .500+ teams record (needs per-game opponent-record filtering, not a
    direct endpoint — skip or build later with leaguegamefinder if you want it)
  - Front Office: cap space, luxury tax, contract details, draft assets
    (nba_api has no salary/cap data — use Spotrac)
  - Rotation minutes notes / Q4 lineup notes (qualitative — you write these)

Usage:
    python3 laker_stat_pull.py --season 2026-27 --season-type "Regular Season"
    python3 laker_stat_pull.py --season 2026-27 --season-type "Playoffs"

Requires: nba_api, requests  (pip install nba_api requests --break-system-packages)
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from nba_api.stats.endpoints import (
    leaguedashteamstats,
    leaguedashplayerstats,
    leaguedashlineups,
    teamplayeronoffdetails,
    leaguedashplayerclutch,
)

LAKERS_ID = 1610612747
LAKERS_ABBR = "LAL"

# NBA.com stats endpoints are rate-limit sensitive. Keep a small delay between calls.
PAUSE = 0.6


def call(endpoint_cls, **kwargs):
    """Wrapper with a small delay + basic retry, since stats.nba.com is flaky."""
    for attempt in range(3):
        try:
            time.sleep(PAUSE)
            ep = endpoint_cls(**kwargs)
            return ep.get_data_frames()
        except Exception as e:
            print(f"  [warn] {endpoint_cls.__name__} attempt {attempt+1} failed: {e}", file=sys.stderr)
            time.sleep(2)
    raise RuntimeError(f"{endpoint_cls.__name__} failed after 3 attempts")


def pct(x):
    """NBA.com returns most percentages as 0-1 floats; convert to 0-100, 1 decimal."""
    try:
        return round(float(x) * 100, 1)
    except (TypeError, ValueError):
        return None


def rnd(x, d=1):
    try:
        return round(float(x), d)
    except (TypeError, ValueError):
        return None


def get_team_section(season, season_type):
    print("Pulling team advanced stats...")
    adv = call(
        leaguedashteamstats.LeagueDashTeamStats,
        season=season,
        season_type_all_star=season_type,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
    )[0]
    lak_adv = adv[adv["TEAM_ID"] == LAKERS_ID].iloc[0]

    print("Pulling team base stats (overall + home + road + last 10)...")
    base_overall = call(
        leaguedashteamstats.LeagueDashTeamStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Base", per_mode_detailed="Totals",
    )[0]
    lak_base = base_overall[base_overall["TEAM_ID"] == LAKERS_ID].iloc[0]

    base_home = call(
        leaguedashteamstats.LeagueDashTeamStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Base", per_mode_detailed="Totals",
        location_nullable="Home",
    )[0]
    lak_home = base_home[base_home["TEAM_ID"] == LAKERS_ID].iloc[0]

    base_road = call(
        leaguedashteamstats.LeagueDashTeamStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Base", per_mode_detailed="Totals",
        location_nullable="Road",
    )[0]
    lak_road = base_road[base_road["TEAM_ID"] == LAKERS_ID].iloc[0]

    base_last10 = call(
        leaguedashteamstats.LeagueDashTeamStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Base", per_mode_detailed="Totals",
        last_n_games=10,
    )[0]
    lak_last10 = base_last10[base_last10["TEAM_ID"] == LAKERS_ID].iloc[0]

    print("Pulling bench points (team + league average)...")
    bench_all = call(
        leaguedashteamstats.LeagueDashTeamStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Base", per_mode_detailed="PerGame",
        starter_bench_nullable="Bench",
    )[0]
    lak_bench_pts = bench_all[bench_all["TEAM_ID"] == LAKERS_ID].iloc[0]["PTS"]
    league_avg_bench_pts = bench_all["PTS"].mean()

    print("Pulling opponent points off turnovers (Misc)...")
    misc = call(
        leaguedashteamstats.LeagueDashTeamStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Misc", per_mode_detailed="PerGame",
    )[0]
    lak_misc = misc[misc["TEAM_ID"] == LAKERS_ID].iloc[0]

    return {
        "recordOverall": f"{int(lak_base['W'])}-{int(lak_base['L'])}",
        "last10": f"{int(lak_last10['W'])}-{int(lak_last10['L'])}",
        "homeRoad": f"{int(lak_home['W'])}-{int(lak_home['L'])} / {int(lak_road['W'])}-{int(lak_road['L'])}",
        "vs500": "",  # not directly available — fill manually
        "oRtg": rnd(lak_adv["OFF_RATING"]),
        "dRtg": rnd(lak_adv["DEF_RATING"]),
        "netRtg": rnd(lak_adv["NET_RATING"]),
        "pace": rnd(lak_adv["PACE"]),
        "benchPts": rnd(lak_bench_pts),
        "leagueAvgBench": rnd(league_avg_bench_pts),
        "tovPct": pct(lak_adv["TM_TOV_PCT"]),
        "oppPtsOffTov": rnd(lak_misc.get("OPP_PTS_OFF_TOV")),
        "drebPct": pct(lak_adv["DREB_PCT"]),
    }


def get_rotation_section(season, season_type):
    print("Pulling top 5-man lineup by minutes...")
    lineups = call(
        leaguedashlineups.LeagueDashLineups,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Advanced", per_mode_detailed="PerGame",
        group_quantity=5, team_id_nullable=LAKERS_ID,
    )[0]
    if lineups.empty:
        return {"topLineup": "", "topLineupNet": None}
    top = lineups.sort_values("MIN", ascending=False).iloc[0]
    return {
        "topLineup": top["GROUP_NAME"].replace(" - ", ", "),
        "topLineupNet": rnd(top["NET_RATING"]),
    }


def get_players_section(season, season_type, top_n=8):
    print("Pulling player base + advanced stats...")
    base = call(
        leaguedashplayerstats.LeagueDashPlayerStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Base", per_mode_detailed="PerGame",
        team_id_nullable=LAKERS_ID,
    )[0]
    adv = call(
        leaguedashplayerstats.LeagueDashPlayerStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Advanced", per_mode_detailed="PerGame",
        team_id_nullable=LAKERS_ID,
    )[0]

    print("Pulling rolling last-10-game plus/minus...")
    last10 = call(
        leaguedashplayerstats.LeagueDashPlayerStats,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Base", per_mode_detailed="PerGame",
        team_id_nullable=LAKERS_ID, last_n_games=10, plus_minus="Y",
    )[0]

    print("Pulling clutch stats...")
    clutch = call(
        leaguedashplayerclutch.LeagueDashPlayerClutch,
        season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Base", per_mode_detailed="PerGame",
        team_id_nullable=LAKERS_ID,
    )[0]

    print("Pulling on/off net ratings...")
    onoff = call(
        teamplayeronoffdetails.TeamPlayerOnOffDetails,
        team_id=LAKERS_ID, season=season, season_type_all_star=season_type,
        measure_type_detailed_defense="Advanced", per_mode_detailed="PerGame",
    )
    # data_frames: [0]=Overall, [1]=PlayersOnCourt, [2]=PlayersOffCourt
    on_df, off_df = onoff[1], onoff[2]

    top_players = base.sort_values("MIN", ascending=False).head(top_n)

    players = []
    for _, row in top_players.iterrows():
        pid, name = row["PLAYER_ID"], row["PLAYER_NAME"]
        adv_row = adv[adv["PLAYER_ID"] == pid]
        l10_row = last10[last10["PLAYER_ID"] == pid]
        clutch_row = clutch[clutch["PLAYER_ID"] == pid]
        on_row = on_df[on_df["VS_PLAYER_ID"] == pid] if "VS_PLAYER_ID" in on_df.columns else on_df[on_df["GROUP_VALUE"] == name]
        off_row = off_df[off_df["VS_PLAYER_ID"] == pid] if "VS_PLAYER_ID" in off_df.columns else off_df[off_df["GROUP_VALUE"] == name]

        on_net = rnd(on_row.iloc[0]["NET_RATING"]) if not on_row.empty else None
        off_net = rnd(off_row.iloc[0]["NET_RATING"]) if not off_row.empty else None
        on_off_diff = round(on_net - off_net, 1) if (on_net is not None and off_net is not None) else None

        players.append({
            "name": name,
            "pts": rnd(row["PTS"]),
            "reb": rnd(row["REB"]),
            "ast": rnd(row["AST"]),
            "usg": pct(adv_row.iloc[0]["USG_PCT"]) if not adv_row.empty else None,
            "fgPct": pct(row["FG_PCT"]),
            "threePct": pct(row["FG3_PCT"]),
            "ftPct": pct(row["FT_PCT"]),
            "efgPct": pct(adv_row.iloc[0]["EFG_PCT"]) if not adv_row.empty else None,
            "plusMinus": rnd(row["PLUS_MINUS"]) if "PLUS_MINUS" in row else None,
            "plusMinus10": rnd(l10_row.iloc[0]["PLUS_MINUS"]) if not l10_row.empty else None,
            "clutchFgPct": pct(clutch_row.iloc[0]["FG_PCT"]) if not clutch_row.empty else None,
            "onOffNet": on_off_diff,
        })

    return players


def main():
    parser = argparse.ArgumentParser(description="Pull Lakers stats for the Tier 2 Prep Dashboard")
    parser.add_argument("--season", default="2026-27", help="e.g. 2026-27")
    parser.add_argument("--season-type", default="Regular Season", choices=["Regular Season", "Playoffs"])
    parser.add_argument("--out", default="data/latest.json", help="output JSON path")
    args = parser.parse_args()

    print(f"=== LakerSinceBirth stat pull: {args.season} {args.season_type} ===")

    snapshot = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "season": "playoffs" if args.season_type == "Playoffs" else "regular",
        "seasonLabel": args.season,
        "team": get_team_section(args.season, args.season_type),
        "rotation": get_rotation_section(args.season, args.season_type),
        "players": get_players_section(args.season, args.season_type),
    }

    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"\nSaved snapshot to {args.out}")
    print(json.dumps(snapshot, indent=2)[:800] + "...")


if __name__ == "__main__":
    main()
