# Maintenance Guide — Israel Lotto Archive

## Starting the website

```bash
cd C:\Users\user\Desktop\lottery
python app.py
```

Open your browser at http://127.0.0.1:5000

The server runs in debug mode. Press Ctrl+C to stop it.

---

## Adding a new lottery draw

### Option A — Add the next missing draw automatically

```bash
python update.py --latest
```

This finds the highest ID already in the database, increments it by 1, fetches that draw
from pais.co.il, validates that it is in the modern format (numbers 1–37, strong 1–7),
saves it to the database, and regenerates the PNG graphs.

### Option B — Add (or re-scrape) a specific draw by ID

```bash
python update.py 3910
```

### Option C — Update several draws at once

```bash
python update.py 3910 3911 3912
```

### Skip graph regeneration (faster, useful when adding many draws in a row)

```bash
python update.py 3910 3911 --no-stats
python update.py --latest --no-stats
```

After a batch update without stats, regenerate the graphs once at the end:

```bash
python stats.py
```

---

## Regenerating the PNG graphs only

```bash
python stats.py
```

This reads `lottery.db` and writes two files to the same folder:
- `stats_main_numbers.png`
- `stats_strong_number.png`

---

## Database location

`lottery.db` — the live database used by the website.

`with old result/lottery.db` — backup of the original full archive (all eras, 2,870 draws).

Do not delete the backup. If the main database becomes corrupted, copy the backup and
re-run `update.py --latest` to catch up.

---

## Full re-scrape from scratch (rare)

Only needed if the database is lost or badly corrupted.

```bash
python scraper.py
```

The scraper is resumable. If it is interrupted, re-run the same command and it will
continue from where it stopped (already-scraped IDs and known-missing IDs are skipped).

**Warning:** A full scrape takes a long time (1,672+ HTTP requests with rate limiting).

---

## File overview

| File | Purpose |
|------|---------|
| `app.py` | Flask web server — run this to start the site |
| `scraper.py` | Full archive scraper (run once, or to back-fill) |
| `update.py` | Add / refresh individual draws and regenerate graphs |
| `stats.py` | Generate the two PNG frequency graphs |
| `lottery.db` | Live SQLite database |
| `requirements.txt` | Python package dependencies |
| `templates/` | Jinja2 HTML templates for the website |
| `stats_main_numbers.png` | Main-number frequency chart (served by website) |
| `stats_strong_number.png` | Strong-number frequency chart (served by website) |
| `with old result/` | Backup databases from older lottery eras |

---

## Python dependencies

```bash
pip install -r requirements.txt
pip install flask
```
