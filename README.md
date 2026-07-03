# Elexicon Energy → Home Assistant (Green Button)

Pull your **Elexicon Energy** electricity usage — split by **Time-of-Use tier
(on-peak / mid-peak / off-peak)** with cost — into the **Home Assistant Energy
Dashboard**, with full history.

Built for **[Elexicon Energy](https://elexiconenergy.com) customers** — the utility
serving **Pickering, Ajax, Whitby, Clarington, Brooklin, Belleville and Gravenhurst**
(Durham Region & area, Ontario). If your account lives at `myelexicon.elexiconenergy.com`,
this is for you.

> **Not affiliated with, endorsed by, or supported by Elexicon Energy.** It uses
> your own account's **Green Button** data export (an open standard). Use at your
> own risk.

---

## Why this exists

Home Assistant has no way to hand-enter historical energy usage, and Elexicon's
`my.elexicon` portal is hostile to scraping (Cloudflare bot protection, invisible
reCAPTCHA, and email 2FA). But Elexicon is **Green Button certified**, and their
Green Button connector (`greenbuttonconnector.com`) exposes standardized **ESPI
XML** — hourly interval data already tagged with the TOU period *and* cost. This
tool parses that file and pushes it into Home Assistant as long-term statistics.

## What you get

Eight external statistics in Home Assistant:

| Statistic id | Unit | Meaning |
|---|---|---|
| `elexicon:grid_energy` | kWh | total consumption (use this in the Energy Dashboard) |
| `elexicon:grid_energy_off_peak` / `_mid_peak` / `_on_peak` | kWh | usage per TOU tier |
| `elexicon:grid_cost` | CAD | total energy cost |
| `elexicon:grid_cost_off_peak` / `_mid_peak` / `_on_peak` | CAD | cost per TOU tier |

Plus a ready-made Time-of-Use dashboard (`dashboard.yaml`).

## How it works

```
Green Button XML  ──►  elexicon_to_ha.py  ──►  Home Assistant
(hourly ESPI data)     parse → merge into        external statistics
                       data/ledger.json          (elexicon:grid_*)
```

Two ways to get the file:

- **Semi-auto (reliable):** you download the file from Elexicon yourself, the
  importer loads it. Nothing to break.
- **Full-auto (optional):** a Playwright job logs into the Green Button connector
  and downloads it for you on a schedule. The connector isn't behind Cloudflare, so
  this runs **headless** — it only needs a one-time human CAPTCHA solve to seed the
  session.

---

## Requirements

- A running **Home Assistant** instance you can reach on your network.
- **Python 3.11+** (needs `tomllib` + `zoneinfo`).
- An **Elexicon Energy** account with online access (for the Green Button export).
- For full-auto only: **Playwright** + Chromium (`pip install playwright && playwright install chromium`).

## Setup

1. **Home Assistant token** — in HA: Profile → Security → **Long-Lived Access
   Tokens** → create one, and save it to a file named `.ha_token` in this folder.

2. **Config** — copy the template and fill it in:
   ```
   copy config.example.toml config.toml     # Windows
   cp   config.example.toml config.toml     # macOS/Linux
   ```
   Set your `account_number` (10 digits, from your bill), `postal_code`, timezone,
   HA `url`, and the download folder path.

3. **Install dependencies:**
   ```
   python -m pip install -r requirements.txt
   ```

### Get your data (semi-auto)

Log into [my.elexicon](https://myelexicon.elexiconenergy.com) → **Usage → Usage
Overview** → the green **Download My Data** (Green Button) button → keep *Electric
Usage* checked, pick a time span (a longer span backfills history) → **Download My
Data**. Then:

```
python elexicon_to_ha.py            # imports the newest matching file
python elexicon_to_ha.py --dry-run  # parse + summarize only, don't touch HA
```

### Home Assistant dashboard

- **Energy Dashboard:** Settings → Dashboards → Energy → *Add Consumption* → pick
  **`elexicon:grid_energy`**.
- **TOU view:** add the cards from `dashboard.yaml` (instructions at the top of that
  file) to see usage and cost split by peak tier.

---

## Full-auto downloader (optional)

`elexicon_download.py` drives the Green Button connector directly and downloads for
you. It authenticates via the connector's **one-time access** form (account number +
postal code + a CAPTCHA); once you solve the CAPTCHA the session cookie is saved to
`connector_session.json` and reused **headlessly** until it expires.

```
pip install playwright
playwright install chromium
```

Seed the session once (opens a real browser; account + postal are pre-filled — just
solve the CAPTCHA). Re-run whenever a download says the session expired:
```
python elexicon_download.py --login      # or double-click seed.bat on Windows
```

Unattended download + import (headless, safe for a scheduler):
```
python elexicon_download.py --import      # or run_autodownload.bat
```

**Tip — keep everything in one folder:** create a venv and put Chromium inside the
project so nothing touches your system Python:
```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
set PLAYWRIGHT_BROWSERS_PATH=%CD%\pw-browsers
.venv\Scripts\python -m playwright install chromium
```
The included `run_autodownload.bat` / `seed.bat` auto-detect `.venv` and set
`PLAYWRIGHT_BROWSERS_PATH` for you.

### Scheduling (Windows Task Scheduler)

```
schtasks /Create /TN "Elexicon HA AutoDownload" /TR "\"%CD%\run_autodownload.bat\"" ^
         /SC DAILY /ST 06:05 /F
```
(Use `run_import.bat` instead if you download files yourself.)

---

## Notes & limitations

- **Update cadence:** Elexicon publishes interval data roughly **once a day**, about
  a day behind real time (standard for Ontario smart-meter/MDM data). Running more
  than once or twice a day gains nothing — the importer skips when there's no new
  data.
- **Cost is the commodity charge** at OEB TOU rates (encoded in the file). It
  excludes delivery, regulatory charges, HST and the Ontario Electricity Rebate. Use
  `cost_multiplier` in config to approximate your all-in bill.
- **Connector session** expires periodically; re-seed with `--login` (needs a
  desktop for the CAPTCHA). This is the only recurring manual step for full-auto.
- **The importer is idempotent** — it merges into a local ledger, so re-imports and
  overlapping downloads never double-count.

## Privacy & security

- Your usage data, HA token, connector session, and account details stay **local**.
  `.gitignore` keeps `config.toml`, `.ha_token`, `connector_session.json`, the
  `data/` ledger and all downloaded files out of the repo.
- Never commit your `config.toml` or `.ha_token`. Prefer a Long-Lived Access Token
  over your HA password.

## License

MIT — see [LICENSE](LICENSE).
