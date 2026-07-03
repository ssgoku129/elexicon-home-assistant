#!/usr/bin/env python3
"""
Elexicon Energy (Green Button) -> Home Assistant Energy Dashboard importer.

Pipeline:
  1. Parse a Green Button ESPI XML file (hourly interval data with TOU + cost).
  2. Merge the hours into a local ledger (source of truth across rolling 30-day
     downloads), so re-imports are consistent and self-correcting.
  3. Push external long-term statistics into Home Assistant via the WebSocket
     recorder/import_statistics API:
        elexicon:grid_energy            (kWh)  total consumption
        elexicon:grid_energy_off_peak   (kWh)
        elexicon:grid_energy_mid_peak   (kWh)
        elexicon:grid_energy_on_peak    (kWh)
        elexicon:grid_cost              (CAD)  total energy cost
        elexicon:grid_cost_off_peak     (CAD)
        elexicon:grid_cost_mid_peak     (CAD)
        elexicon:grid_cost_on_peak      (CAD)

Usage:
  python elexicon_to_ha.py                 # newest matching file -> ledger -> HA
  python elexicon_to_ha.py --file foo.xml  # import a specific file
  python elexicon_to_ha.py --dry-run       # parse + show summary, don't touch HA
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))

# TOU code (from the ESPI <tou> element) -> human tier.
TOU = {1: "on_peak", 2: "mid_peak", 3: "off_peak"}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    ha = cfg.setdefault("home_assistant", {})
    if not ha.get("token"):
        tok_file = os.path.join(HERE, ".ha_token")
        if os.path.exists(tok_file):
            ha["token"] = open(tok_file, encoding="utf-8").read().strip()
    if not ha.get("token"):
        sys.exit("No HA token: set [home_assistant].token or create a .ha_token file.")
    return cfg


# --------------------------------------------------------------------------- #
# Green Button parsing
# --------------------------------------------------------------------------- #
def _tag(e) -> str:
    return e.tag.split("}")[-1]


def parse_greenbutton(path: str) -> list[dict]:
    """Return hourly records: {start_epoch:int, kwh:float, tou:int, cost:float(CAD)}."""
    root = ET.parse(path).getroot()
    out = []
    for entry in root.iter():
        if _tag(entry) != "entry":
            continue
        title = next((x.text for x in entry if _tag(x) == "title"), "") or ""
        # Only the hourly delivered-energy blocks. Skip daily "Register Reads".
        if "Energy Delivered" not in title:
            continue
        for ir in entry.iter():
            if _tag(ir) != "IntervalReading":
                continue
            d = {}
            for c in ir:
                if _tag(c) == "timePeriod":
                    for cc in c:
                        d[_tag(cc)] = cc.text
                else:
                    d[_tag(c)] = c.text
            if int(d.get("duration", 0)) != 3600:
                continue  # hourly only
            out.append(
                {
                    "start_epoch": int(d["start"]),
                    "kwh": int(d["value"]) * 1e-6,        # uom 72 Wh, mult -3 -> kWh
                    "tou": int(d.get("tou", 0)),
                    "cost": int(d.get("cost", 0)) * 1e-3,  # mills CAD -> CAD
                }
            )
    if not out:
        sys.exit(f"No hourly 'Energy Delivered' readings found in {path}.")
    return out


# --------------------------------------------------------------------------- #
# Ledger (local merged history)
# --------------------------------------------------------------------------- #
def load_ledger(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ledger(path: str, ledger: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def merge(ledger: dict, records: list[dict]) -> int:
    """Merge records into ledger keyed by epoch hour. Returns count of new/changed."""
    changed = 0
    for r in records:
        key = str(r["start_epoch"])
        val = [round(r["kwh"], 4), r["tou"], round(r["cost"], 4)]
        if ledger.get(key) != val:
            ledger[key] = val
            changed += 1
    return changed


# --------------------------------------------------------------------------- #
# Build statistic streams
# --------------------------------------------------------------------------- #
def build_streams(ledger: dict, cost_mult: float):
    """Return dict stream_key -> list of (epoch, hour_value, cumulative_sum)."""
    hours = sorted(int(k) for k in ledger)
    keys = [
        "grid_energy", "grid_energy_off_peak", "grid_energy_mid_peak", "grid_energy_on_peak",
        "grid_cost", "grid_cost_off_peak", "grid_cost_mid_peak", "grid_cost_on_peak",
    ]
    streams = {k: [] for k in keys}
    run = {k: 0.0 for k in keys}
    for ep in hours:
        kwh, tou, cost = ledger[str(ep)]
        cost = cost * cost_mult
        tier = TOU.get(tou)
        contrib = {k: 0.0 for k in keys}
        contrib["grid_energy"] = kwh
        contrib["grid_cost"] = cost
        if tier:
            contrib[f"grid_energy_{tier}"] = kwh
            contrib[f"grid_cost_{tier}"] = cost
        for k in keys:
            run[k] += contrib[k]
            streams[k].append((ep, contrib[k], round(run[k], 4)))
    return streams


META = {
    "grid_energy":          ("Elexicon Grid Energy", "kWh"),
    "grid_energy_off_peak": ("Elexicon Grid Energy Off-Peak", "kWh"),
    "grid_energy_mid_peak": ("Elexicon Grid Energy Mid-Peak", "kWh"),
    "grid_energy_on_peak":  ("Elexicon Grid Energy On-Peak", "kWh"),
    "grid_cost":            ("Elexicon Grid Cost", "CAD"),
    "grid_cost_off_peak":   ("Elexicon Grid Cost Off-Peak", "CAD"),
    "grid_cost_mid_peak":   ("Elexicon Grid Cost Mid-Peak", "CAD"),
    "grid_cost_on_peak":    ("Elexicon Grid Cost On-Peak", "CAD"),
}


# --------------------------------------------------------------------------- #
# Home Assistant import (WebSocket)
# --------------------------------------------------------------------------- #
def import_to_ha(cfg: dict, streams: dict, tz: ZoneInfo) -> None:
    import websocket  # websocket-client

    base = cfg["home_assistant"]["url"].rstrip("/")
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    prefix = cfg["importer"]["statistic_prefix"]

    ws = websocket.create_connection(ws_url, timeout=30)
    try:
        ws.recv()  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": cfg["home_assistant"]["token"]}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            sys.exit("HA WebSocket auth failed (check token).")

        msg_id = 0
        for key, rows in streams.items():
            msg_id += 1
            name, unit = META[key]
            stats = [
                {
                    "start": datetime.fromtimestamp(ep, timezone.utc)
                    .astimezone(tz)
                    .isoformat(),
                    "state": round(val, 4),
                    "sum": csum,
                }
                for ep, val, csum in rows
            ]
            ws.send(json.dumps({
                "id": msg_id,
                "type": "recorder/import_statistics",
                "metadata": {
                    "has_mean": False,
                    "has_sum": True,
                    "name": name,
                    "source": prefix,
                    "statistic_id": f"{prefix}:{key}",
                    "unit_of_measurement": unit,
                },
                "stats": stats,
            }))
            resp = json.loads(ws.recv())
            ok = resp.get("success", False)
            print(f"  {'OK ' if ok else 'ERR'} {prefix}:{key:22s} {len(stats):5d} hours"
                  + ("" if ok else f"  <- {resp.get('error')}"))
    finally:
        ws.close()


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Import Elexicon Green Button data into Home Assistant.")
    ap.add_argument("--config", default=os.path.join(HERE, "config.toml"))
    ap.add_argument("--file", help="Specific Green Button XML to import (default: newest match).")
    ap.add_argument("--dry-run", action="store_true", help="Parse + summarize only; don't touch HA.")
    ap.add_argument("--force", action="store_true", help="Push to HA even if no new/changed hours.")
    args = ap.parse_args()

    cfg = load_config(args.config)
    tz = ZoneInfo(cfg["importer"].get("timezone", "America/Toronto"))

    # Pick the input file.
    if args.file:
        path = args.file
    else:
        pattern = os.path.join(cfg["source"]["download_folder"], cfg["source"]["file_glob"])
        matches = sorted(glob.glob(pattern), key=os.path.getmtime)
        if not matches:
            sys.exit(f"No Green Button files match {pattern}")
        path = matches[-1]
    print(f"Parsing {path}")
    records = parse_greenbutton(path)
    span0 = datetime.fromtimestamp(min(r["start_epoch"] for r in records), tz)
    span1 = datetime.fromtimestamp(max(r["start_epoch"] for r in records), tz)
    print(f"  {len(records)} hourly readings, {span0:%Y-%m-%d %H:%M} -> {span1:%Y-%m-%d %H:%M} {tz.key}")

    # Merge into ledger.
    ledger_path = os.path.join(HERE, cfg["source"]["ledger"])
    ledger = load_ledger(ledger_path)
    changed = merge(ledger, records)
    print(f"  ledger: {len(ledger)} total hours ({changed} new/updated this run)")

    # Build + summarize streams.
    streams = build_streams(ledger, float(cfg["importer"].get("cost_multiplier", 1.0)))
    tot_kwh = streams["grid_energy"][-1][2] if streams["grid_energy"] else 0
    tot_cost = streams["grid_cost"][-1][2] if streams["grid_cost"] else 0
    print(f"  ledger totals: {tot_kwh:.1f} kWh, ${tot_cost:.2f}  "
          f"(off {streams['grid_energy_off_peak'][-1][2]:.0f} / "
          f"mid {streams['grid_energy_mid_peak'][-1][2]:.0f} / "
          f"on {streams['grid_energy_on_peak'][-1][2]:.0f} kWh)")

    if args.dry_run:
        print("Dry run: not writing ledger, not importing to HA.")
        return

    if changed == 0 and not args.force:
        print("No new or changed hours since last run — skipping HA import (use --force to re-push).")
        return

    save_ledger(ledger_path, ledger)
    print(f"Importing {len(streams)} statistics to {cfg['home_assistant']['url']} ...")
    import_to_ha(cfg, streams, tz)
    print("Done.")


if __name__ == "__main__":
    main()
