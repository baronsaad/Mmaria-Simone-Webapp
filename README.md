# Mmaria-Simone-Webapp
Webpublikation und Archivierung von Radarforschungsdatensätzen innerhalb eines verteilten Forschungsdatenworkflows


MMARIA & SIMONe – Webapp (Quickstart)

Voraussetzungen:
- Python 3.10+ (empfohlen 3.12/3.13)
- PowerShell
- (Optional) VS Code mit Python-Extension

Schritte:
1) Virtuelle Umgebung (empfohlen)
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
   Falls geblockt:  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

2) Pakete installieren
    python -m pip install --upgrade pip
    python -m pip install flask jinja2 click

3) Datenbank initialisieren
    python mmaria_simone_webapp.py init-db

4) Eingangs-Bilder ablegen
   Legen Sie die jeweils überschriebenen PNGs in data\incoming\<station_key>\
   mit exakt diesen Namen ab (siehe PUT_FILES_HERE.txt in jedem Ordner).

5) Einmaligen Scan ausführen
    python mmaria_simone_webapp.py scan

6) Webserver starten (Entwicklung)
    python mmaria_simone_webapp.py run
   Dann im Browser: http://localhost:8000

7) (Optional) Geplanter Task alle 5 Minuten (als Administrator ausführen)
    schtasks /Create /SC MINUTE /MO 5 /TN "MMARIA_SIMONe_Scan" /TR "\"%CD%\\.venv\\Scripts\\python.exe\" \"%CD%\\mmaria_simone_webapp.py\" scan" /F

Anpassungen:
- Stationen & Dateinamen ändern: im Python-File STATIONS-Liste bearbeiten.
- Datenverzeichnis anpassen: Umgebungsvariablen APP_BASE und DATA_DIR setzen.

Troubleshooting:
- Wenn keine Bilder angezeigt werden: Prüfen, ob 'scan' lief und Dateien in data\\current\\<station>\\latest.png existieren.
- Archiv zeigt nichts: Die DB speichert pro Station nur das zuletzt am Tag eingetroffene Bild (nach Dateizeitstempel in UTC).
