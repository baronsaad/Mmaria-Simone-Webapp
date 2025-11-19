#!/usr/bin/env python3
"""
MMARIA & SIMONe plot publisher (Flask, Datei + Datenbank)

Ziele:
- Design wie bestehende Seite (schwarz/weiß), rechts Plot, links Google‑Map
- Backend in Python (Flask)
- Datei + Datenbank: Archiviert wird pro Station GENAU EIN Bild pro Tag (das jeweils letzte an diesem Tag)
- Suchfeld (Land, Station, Datum) über das Archiv
- DB-Spalten: image_name (PNG), country, station, date (YYYY-MM-DD)

Ordnerstruktur (anpassbar, siehe CONFIG):
- data/incoming/<station_key>/NameDesBildes.png               # Quelle: wird alle ~5 Min. überschrieben
- data/current/<station_key>/latest.png                       # von uns gespiegelt, fürs Frontend
- data/archive/<station_key>/YYYY/MM/DD/NameDesBildes.png     # Archiv, aber genau 1 Datei/Tag+Station (jeweils letzte)
- data/old incoming/<station_key>/NameDesBildes.png           # Quelle: für das Nachreichen von PNGs.
- data/app.db                                                 # SQLite DB

Ausführung:
  $ pip install flask jinja2 click
  $ python mmaria_simone_webapp.py init-db      # einmalig
  $ python mmaria_simone_webapp.py scan         # per Cron alle 5 Min. aufrufen
  $ python mmaria_simone_webapp.py run          # Webserver starten

Cron-Beispiel (alle 5 Min.):
  */5 * * * * /usr/bin/python /path/mmaria_simone_webapp.py scan >> /var/log/scan.log 2>&1

Hinweis: Stations‑Konfiguration in STATIONS.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import shutil
import click
from flask import Flask, render_template, request, send_from_directory, url_for
from jinja2 import DictLoader, ChoiceLoader

# -----------------
# Konfiguration
# -----------------
BASE_DIR = Path(os.environ.get("APP_BASE", ".")).resolve()
DATA_DIR = (BASE_DIR / os.environ.get("DATA_DIR", "data")).resolve()
INCOMING_DIR = DATA_DIR / "incoming"
CURRENT_DIR = DATA_DIR / "current"
ARCHIVE_DIR = DATA_DIR / "archive"
OLD_INCOMING_DIR = DATA_DIR / "old incoming"
DB_PATH = DATA_DIR / "app.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
INCOMING_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
OLD_INCOMING_DIR.mkdir(parents=True, exist_ok=True)

# -----------------
# Stations (Projekt, Land, Map‑Embed, Quelle)
# station_key ist Ordnername unter incoming/old incoming/current/archive
# -----------------
STATIONS = [
    {
        "key": "mmaria_scandinavia",
        "project": "MMARIA",
        "country": "Norway",
        "station": "MMARIA Scandinavia",
        "map_embed": "https://www.google.com/maps/d/u/0/embed?mid=1A70yF5VLpIKCW6-V_TgGJFn6Qok",
        "incoming_filename": "multilink_overview_mmaria-norway.png",
    },
    {
        "key": "mmaria_germany",
        "project": "MMARIA",
        "country": "Germany",
        "station": "MMARIA Germany",
        "map_embed": "https://www.google.com/maps/d/u/0/embed?mid=1I6D20ucZeomNTYKe3iDHF3jtPXm36CRx",
        "incoming_filename": "multilink_overview_mmaria-germany.png",
    },
    {
        "key": "simone_jicamarca",
        "project": "SIMONe",
        "country": "Peru",
        "station": "SIMONe Jicamarca",
        "map_embed": "https://www.google.com/maps/d/u/0/embed?mid=1IxOgSL2Yh3NxMPDsR3nCFNTTIeFXuF56",
        "incoming_filename": "multilink_overview_simone-peru.png",
    },
    {
        "key": "simone_piura",
        "project": "SIMONe",
        "country": "Peru",
        "station": "SIMONe Piura",
        "map_embed": "https://www.google.com/maps/d/embed?mid=1ynGhjIEs0zK7nZp6xt1RJE86ALFMGguI",
        "incoming_filename": "multilink_overview_simone-peru2.png",
    },
    {
        "key": "simone_argentina",
        "project": "SIMONe",
        "country": "Argentina",
        "station": "SIMONe Argentina",
        "map_embed": "https://www.google.com/maps/d/embed?mid=1AGS55_83ywb7weAJ-O2VqRNw6F0SKne5",
        "incoming_filename": "multilink_overview_simone-argentina.png",
    },
    {
        "key": "simone_newmexico",
        "project": "SIMONe",
        "country": "USA",
        "station": "SIMONe New Mexico",
        "map_embed": "https://www.google.com/maps/d/u/0/embed?mid=1jZVpT6BahHtqejR2qziAi5xh5bWKtcs&ehbc=2E312F",
        "incoming_filename": "multilink_overview_simone-new_mexico.png",
    },
    {
        "key": "simone_haystack",
        "project": "SIMONe",
        "country": "USA",
        "station": "SIMONe Haystack",
        "map_embed": "https://www.google.com/maps/d/embed?mid=1ugVi-xvVCkU7yc3lcuOJXbyV6qK1UDY&ehbc=2E312F",
        "incoming_filename": "multilink_overview_simone-haystack.png",
    },
]

# Abbild der Seite: Reihenfolge wie oben
ROWS = STATIONS

# -----------------
# DB (SQLite)
# -----------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS archive_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_name TEXT NOT NULL,
    country TEXT NOT NULL,
    station TEXT NOT NULL,
    date TEXT NOT NULL,            -- YYYY-MM-DD (UTC)
    station_key TEXT NOT NULL,
    file_path TEXT NOT NULL,
    UNIQUE (station_key, date)
);
CREATE INDEX IF NOT EXISTS idx_archive_country ON archive_images(country);
CREATE INDEX IF NOT EXISTS idx_archive_station ON archive_images(station);
CREATE INDEX IF NOT EXISTS idx_archive_date ON archive_images(date);
"""

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)  # autocommit mode
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

# -----------------
# Dateioperationen & Archivlogik
# -----------------

def _ensure_dirs_for_station(key: str):
    (INCOMING_DIR / key).mkdir(parents=True, exist_ok=True)
    (CURRENT_DIR / key).mkdir(parents=True, exist_ok=True)
    (ARCHIVE_DIR / key).mkdir(parents=True, exist_ok=True)
    (OLD_INCOMING_DIR / key).mkdir(parents=True, exist_ok=True)

def utc_date_from_mtime(p: Path) -> str:
    ts = p.stat().st_mtime
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")

def _archive_file(src: Path, station_cfg: dict) -> dict | None:
    # Kopiert eine Datei direkt ins Archiv und pflegt den DB-Eintrag.
    key = station_cfg["key"]
    _ensure_dirs_for_station(key)
    if not src.exists():
        return None

    date_str = utc_date_from_mtime(src)
    yyyy, mm, dd = date_str.split("-")
    archive_dir = ARCHIVE_DIR / key / yyyy / mm / dd
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Im Archiv den Originalnamen beibehalten
    image_name = src.name
    archive_dst = archive_dir / image_name

    # Ins Archiv kopieren
    shutil.copy2(src, archive_dst)

    # DB upsert
    with closing(get_db()) as db:
        db.execute(
            """
            INSERT INTO archive_images(image_name, country, station, date, station_key, file_path)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(station_key, date) DO UPDATE SET
              image_name=excluded.image_name,
              country=excluded.country,
              station=excluded.station,
              file_path=excluded.file_path
            """,
            (
                image_name,
                station_cfg["country"],
                station_cfg["station"],
                date_str,
                key,
                archive_dst.relative_to(DATA_DIR).as_posix(),
            ),
        )
        db.commit()

    return {
        "key": key,
        "date": date_str,
        "archive": str(archive_dst.relative_to(DATA_DIR)),
    }

def _is_newer_than_current(src: Path, current_dst: Path) -> bool:
    # Gibt True zurueck, wenn src neuer ist als current_dst (oder current_dst nicht existiert).
    if not current_dst.exists():
        return True
    try:
        return src.stat().st_mtime > current_dst.stat().st_mtime
    except Exception:
        # Im Zweifel nicht spiegeln (konservativ), aber trotzdem archivieren
        return False

def mirror_current_and_archive(station_cfg: dict):
    # incoming -> current und danach ins Archiv + DB.
    key = station_cfg["key"]
    _ensure_dirs_for_station(key)

    src = INCOMING_DIR / key / station_cfg["incoming_filename"]
    if not src.exists():
        return None  # nichts zu tun

    current_dst = CURRENT_DIR / key / "latest.png"

    # Nur spiegeln, wenn das neue Bild wirklich neuer als current ist.
    if _is_newer_than_current(src, current_dst):
        shutil.copy2(src, current_dst)
        # Danach immer archivieren + DB upsert
        return _archive_file(src, station_cfg)
    else:
        # Nicht neuer: nur ins Archiv uebernehmen (current bleibt unveraendert)
        return _archive_file(src, station_cfg)

def archive_only_from_old_incoming(station_cfg: dict):
    """
    Archiviert PNGs aus *old incoming* direkt ins Archiv (ohne current)
    und leert den Ordner nach erfolgreicher Übernahme.
    """
    key = station_cfg["key"]
    _ensure_dirs_for_station(key)
  
    src_dir = OLD_INCOMING_DIR / key
    if not src_dir.exists():
        return None  # nichts zu tun

    # Nur PNG-Dateien verarbeiten; nach Änderungszeit sortieren
    pngs = sorted([p for p in src_dir.glob("*.png") if p.is_file()],
                  key=lambda p: p.stat().st_mtime)
    archived = []
    for src in pngs:
        try:
            info = _archive_file(src, station_cfg)
            if info:
                archived.append(info)
                # Quelle löschen, damit der Ordner leer bleibt
                src.unlink(missing_ok=True)
        except Exception as e:
            print(f"[WARN] Konnte alte Datei nicht archivieren: {src} -> {e}")
            continue
    return archived

# -----------------
# Flask App
# -----------------
app = Flask(__name__)

# Templates, um alles in einer Datei zu halten
BASE_HTML = """<!doctype html>
<html>
<head>
  <meta http-equiv="REFRESH" content="30;">  <!-- wie Original -->
  <title>Meteor radar networks - latest results</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { background:#000; color:#fff; font-family: Arial, Helvetica, sans-serif; }
    a { color: #fff; }
    table { border-collapse: collapse; margin: 0 auto; }
    td { border: 1px solid #666; vertical-align: top; }
    .center { text-align:center; }
    .hdr { text-align:center; }
    .note { color:#FF8080; animation: blink 1.5s steps(2, start) infinite; }
    @keyframes blink { to { visibility: hidden; } }
    .searchbar { max-width: 1100px; margin: 0.5rem auto 1rem auto; padding: 0.5rem; border: 1px solid #444; }
    .searchbar input, .searchbar select { background:#111; color:#fff; border:1px solid #555; padding:6px; margin-right:8px; }
    .btn { background:#111; color:#fff; border:1px solid #888; padding:6px 10px; cursor:pointer; }
    .footer { width:100%; }
  </style>
</head>
<body>
  <div class="hdr"><h1>Latest results of 
    <a href="https://www.iap-kborn.de/forschung/abteilung-radarsondierungen/instrumente/meteorradar/meteornetzwerke/">meteor radar networks</a>
  </h1></div>

  <div class="searchbar">
    <form action="{{ url_for('search') }}" method="get">
      <label>Land: 
        <select name="country">
          <option value="">– alle –</option>
          {% for c in countries %}<option value="{{c}}" {% if request.args.get('country')==c %}selected{% endif %}>{{c}}</option>{% endfor %}
        </select>
      </label>
      <label>Station: 
        <select name="station">
          <option value="">– alle –</option>
          {% for s in stations %}<option value="{{s}}" {% if request.args.get('station')==s %}selected{% endif %}>{{s}}</option>{% endfor %}
        </select>
      </label>
      <label>Datum: <input type="date" name="date" value="{{ request.args.get('date','') }}"></label>
      <button class="btn" type="submit">Suche im Archiv</button>
      {% if request.endpoint=='search' %}
      <a class="btn" href="{{ url_for('index') }}">Zurück zu Latest</a>
      {% endif %}
    </form>
  </div>

  {% block content %}{% endblock %}

  <hr>
  <table class="footer"><tr>
    <td width="33%"><i>Contact: <a href="https://www.iap-kborn.de/en/institute/staff/staff-details/28/">Ralph Latteck</a><br>
      Last change: {{ last_change }}</i></td>
    <td width="33%" class="center"><span class="note"><i>Note: All results are provided for information only and must be validated by the issuing authority before use!</i></span></td>
    <td width="33%" style="text-align:right"><a href="http://www.iap-kborn.de/"><img src="{{ url_for('static_file', path='iap-logo-sw.png') }}" height="65" alt="IAP"></a></td>
  </tr></table>
</body>
</html>"""

INDEX_HTML = """{% extends 'base.html' %}
{% block content %}
<div class="center">
<table>
  {% for row in rows %}
  <tr>
    <td class="center" style="width:260px">
      <h2><a href="#">{{ row.station.split(' ')[0] }}<br>{{ ' '.join(row.station.split(' ')[1:]) }}</a></h2>
      <p>contact:<br>
        <a href="mailto:info@example.org">info@example.org</a>
      </p>
    </td>
    <td style="width:600px"><iframe src="{{ row.map_embed }}" width="600" height="600"></iframe></td>
    <td>
      {% set current_rel = 'current/' + row.key + '/latest.png' %}
      <a href="{{ url_for('station_view', station_key=row.key) }}">
        <img src="{{ url_for('data_file', path=current_rel) }}" width="1067" height="600" alt="{{ row.station }}">
      </a>
    </td>
  </tr>
  {% endfor %}
</table>
</div>
{% endblock %}
"""

SEARCH_HTML = """{% extends 'base.html' %}
{% block content %}
  <div class="center">
    {% if results %}
    <h2>Archiv-Ergebnisse ({{ results|length }})</h2>
    <table>
      <tr><th>Datum</th><th>Land</th><th>Station</th><th>Bild</th></tr>
      {% for r in results %}
      <tr>
        <td style="padding:6px 10px">{{ r['date'] }}</td>
        <td style="padding:6px 10px">{{ r['country'] }}</td>
        <td style="padding:6px 10px">{{ r['station'] }}</td>
        <td style="padding:6px 10px"><a href="{{ url_for('data_file', path=r['file_path']) }}" target="_blank"><img src="{{ url_for('data_file', path=r['file_path']) }}" height="120"></a></td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
      <h3>Keine Treffer im Archiv für die gewählten Kriterien.</h3>
    {% endif %}
  </div>
{% endblock %}
"""

STATION_HTML = """{% extends 'base.html' %}
{% block content %}
  <div class="center">
    <table>
      <tr>
        <td class="center" style="width:260px"><h2>{{ row.station }}</h2></td>
        <td style="width:600px"><iframe src="{{ row.map_embed }}" width="600" height="600"></iframe></td>
        <td>
          <img src="{{ url_for('data_file', path='current/' + row.key + '/latest.png') }}" width="1067" height="600" alt="{{ row.station }}">
          <p style="text-align:left; padding:6px 10px">Archiv: <a href="{{ url_for('search') }}?station={{ row.station | urlencode }}">Bilder dieser Station durchsuchen</a></p>
        </td>
      </tr>
    </table>
  </div>
{% endblock %}
"""

TEMPLATES = {
    'base.html': BASE_HTML,
    'index.html': INDEX_HTML,
    'search.html': SEARCH_HTML,
    'station.html': STATION_HTML,
}

app.jinja_loader = ChoiceLoader([DictLoader(TEMPLATES), app.jinja_loader])

@app.route('/')
def index():
    countries = sorted({s["country"] for s in STATIONS})
    stations = sorted({s["station"] for s in STATIONS})
    return render_template(
        'index.html',
        rows=ROWS,
        countries=countries,
        stations=stations,
        last_change=datetime.now().strftime('%d.%m.%Y'),
    )

@app.route('/station/<station_key>')
def station_view(station_key: str):
    row = next((s for s in STATIONS if s["key"] == station_key), None)
    if not row:
        return "Unknown station", 404
    countries = sorted({s["country"] for s in STATIONS})
    stations = sorted({s["station"] for s in STATIONS})
    return render_template(
        'station.html',
        row=row,
        countries=countries,
        stations=stations,
        last_change=datetime.now().strftime('%d.%m.%Y'),
    )

@app.route('/search')
def search():
    q_country = request.args.get('country', '').strip()
    q_station = request.args.get('station', '').strip()
    q_date = request.args.get('date', '').strip()  # YYYY-MM-DD

    sql = "SELECT image_name, country, station, date, file_path FROM archive_images WHERE 1=1"
    params = []
    if q_country:
        sql += " AND country = ?"
        params.append(q_country)
    if q_station:
        sql += " AND station = ?"
        params.append(q_station)
    if q_date:
        sql += " AND date = ?"
        params.append(q_date)
    sql += " ORDER BY date DESC, station ASC"

    with closing(get_db()) as db:
        rows = db.execute(sql, params).fetchall()
        results = [dict(r) for r in rows]

    countries = sorted({s["country"] for s in STATIONS})
    stations = sorted({s["station"] for s in STATIONS})
    return render_template(
        'search.html',
        results=results,
        countries=countries,
        stations=stations,
        last_change=datetime.now().strftime('%d.%m.%Y'),
    )

# Statische Auslieferung von DATA_DIR (aktuelle & Archiv‑Bilder) und Logos
@app.route('/data/<path:path>')
def data_file(path: str):
    # Backslashes aus URLs in Forward-Slashes wandeln
    norm = path.replace('\\', '/')
    return send_from_directory(DATA_DIR, norm)

@app.route('/static/<path:path>')
def static_file(path: str):
    static_dir = BASE_DIR / 'static'
    return send_from_directory(static_dir, path)

# -----------------
# CLI‑Kommandos
# -----------------
@click.group()
def cli():
    pass

@cli.command('init-db')
def init_db_cmd():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(get_db()) as db:
        for stmt in SCHEMA_SQL.strip().split(';'):
            s = stmt.strip()
            if s:
                db.execute(s)
        db.commit()
    print(f"DB initialisiert: {DB_PATH}")

@cli.command('scan')
def scan_cmd():
    created = []
    archived_old = []

    # 1) Normale incoming-Verarbeitung (current + archive)
    for st in STATIONS:
        info = mirror_current_and_archive(st)
        if info:
            created.append(info)

    # 2) old incoming: nur Archiv, danach löschen
    for st in STATIONS:
        archived = archive_only_from_old_incoming(st)
        archived_old.extend(archived)

    print(f"Scan fertig, aktualisiert: {len(created)} Station(en), nachgereicht: {len(archived_old)} Datei(en)")
    for it in created:
        print(f"- {it['key']} @ {it['date']} -> current= current/{it['key']}/latest.png archive={it['archive']}")
    for it in archived_old:
        print(f"  + old incoming {it['key']} @ {it['date']} -> archive={it['archive']}")

@cli.command('run')
@click.option('--host', default='0.0.0.0')
@click.option('--port', default=8000, type=int)
@click.option('--debug', is_flag=True, default=False)
def run_cmd(host, port, debug):
    app.run(host=host, port=port, debug=debug)

if __name__ == '__main__':
    cli()
