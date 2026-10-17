# mailabruf

Lädt alle Nachrichten aus allen Ordnern eines oder mehrerer IMAP-Postfächer (z. B. Strato) herunter und speichert sie lokal als `.eml`-Dateien. Anhänge bleiben unverändert in der Datei. Bereits geladene Nachrichten merkt sich das Programm lokal und lädt sie nicht erneut.

Zu jeder Mail legt das Programm zusätzlich eine PDF-Datei mit gleichem Namen an (siehe [PDF-Erzeugung](#pdf-erzeugung)). Außerdem schreibt es pro Postfach eine `index.html` mit allen Mails (siehe [Index](#index-indexhtml)). Diese Datei wird bei jedem Abruf aktualisiert.

- Auf dem Server wird nur gelesen (`EXAMINE` und `BODY.PEEK`). Der Status „gelesen“ ändert sich dadurch nicht.
- Duplikate erkennt das Programm über die IMAP-UID (pro Ordner und `UIDVALIDITY`) und zusätzlich über den `Message-ID`-Header pro Konto. Liegt dieselbe Mail in mehreren Ordnern, wird sie nur einmal gespeichert.
- Es gibt externe Abhängigkeiten, siehe [Voraussetzungen und Abhängigkeiten](#voraussetzungen-und-abhängigkeiten).

## Voraussetzungen und Abhängigkeiten

Abruf und Ablage der Mails (IMAP, `.eml`, SQLite, Konfiguration) kommen allein mit der Python-Standardbibliothek aus. Für die **PDF-Erzeugung** kommen externe Abhängigkeiten dazu. Ohne sie startet das Programm nicht, auch nicht für den reinen Abruf.

| Art | Abhängigkeit | Wofür |
|---|---|---|
| Laufzeit | Python ≥ 3.11 | das Programm selbst (`tomllib`) |
| Python-Paket | [WeasyPrint](https://weasyprint.org/) ≥ 70 | HTML → PDF |
| Python-Pakete (von WeasyPrint) | `pydyf`, `tinycss2`, `cssselect2`, `tinyhtml5`, `fonttools`, `pillow`, `pyphen`, `cffi` | PDF-Ausgabe, CSS, HTML-Parser, Schriften, Bilder |
| Systembibliotheken | Pango (≥ 1.44) mit HarfBuzz, Fontconfig | Textsatz und Schriften; WeasyPrint lädt sie über `cffi` |
| Schriften | beliebige installierte Systemschriften | Darstellung in den PDFs |
| nur Entwicklung | `pytest`, `pypdf` | Tests; `pypdf` liest den Text der erzeugten PDFs aus |

Die Python-Pakete installiert `uv` automatisch: `uv run` nimmt die festen Versionen aus `uv.lock`, `uv tool install .` die jeweils passenden aktuellen. Die Systembibliotheken sind auf einem Fedora-Desktop normalerweise schon vorhanden. Fehlen sie, zum Beispiel auf einem Server ohne grafische Oberfläche, installierst du sie so:

```sh
sudo dnf install pango                      # Fedora
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core   # Debian/Ubuntu
```

Die index.html hat **keine** externen Abhängigkeiten. Sie ist eine einzelne Datei mit eingebettetem CSS und JavaScript und funktioniert ohne Internet in jedem aktuellen Browser.

Netzzugriff hat das Programm nur auf den IMAP-Server. Beim Erzeugen der PDFs lädt es nichts aus dem Internet.

## Installation

```sh
uv tool install .            # installiert ~/.local/bin/mailabruf
# Aktualisieren nach Änderungen:
uv tool install --reinstall .
```

## Konfiguration

Die Konfiguration liegt in `~/.config/mailabruf/`. Mit `--config-dir` lässt sich ein anderer Ordner angeben.

```
~/.config/mailabruf/
├── config.toml           # optional, globale Vorgaben
└── accounts/
    ├── privat.toml       # ein Postfach = eine Datei; Dateiname = Kontoname
    └── firma.toml
```

```sh
mkdir -p ~/.config/mailabruf/accounts
cp contrib/example-account.toml ~/.config/mailabruf/accounts/privat.toml
chmod 600 ~/.config/mailabruf/accounts/privat.toml
$EDITOR ~/.config/mailabruf/accounts/privat.toml
```

Vorlagen: [`contrib/example-account.toml`](contrib/example-account.toml) und [`contrib/config.toml`](contrib/config.toml). Das Passwort steht im Klartext in der Kontodatei. Ist die Datei für andere Benutzer lesbar, gibt das Programm eine Warnung aus.

## Benutzung

```sh
mailabruf check              # Konfiguration prüfen, Login testen
mailabruf folders privat     # Ordner auf dem Server anzeigen
mailabruf fetch --dry-run    # zählen, was neu wäre
mailabruf fetch              # alle aktiven Konten abrufen
mailabruf fetch firma        # nur ein Konto
mailabruf status             # was lokal schon gespeichert ist
mailabruf render             # offline: fehlende/fehlgeschlagene PDFs erzeugen, index.html neu schreiben
mailabruf render --force     # alle PDFs neu erzeugen
mailabruf -v fetch           # jede gespeicherte Datei ausgeben
mailabruf -q fetch           # nur Fehler ausgeben (für cron)
```

Ergebnisse gehen auf stdout, Warnungen und Fehler auf stderr.

Exit-Codes:

| Code | Bedeutung |
|---|---|
| 0 | alles in Ordnung |
| 1 | einzelne Nachrichten, Ordner oder Konten sind fehlgeschlagen |
| 2 | Konfigurations- oder Aufruffehler |

### Ablage

```
<target_dir>/index.html
<target_dir>/<Ordner>/<Unterordner>/<JJJJ-MM-TT_HHMMSS>_<UID>.eml
<target_dir>/<Ordner>/<Unterordner>/<JJJJ-MM-TT_HHMMSS>_<UID>.pdf
```

Das Datum ist das Empfangsdatum auf dem Server (`INTERNALDATE`) in lokaler Zeit. Alle Dateien erhalten die Rechte `0600`.

### PDF-Erzeugung

Zu jeder gespeicherten `.eml`-Datei legt das Programm eine PDF mit gleichem Namen in denselben Ordner, z. B. `INBOX/2026-07-30_142413_1900.eml` → `INBOX/2026-07-30_142413_1900.pdf`. Erzeugt werden die PDFs mit [WeasyPrint](https://weasyprint.org/) (siehe [Abhängigkeiten](#voraussetzungen-und-abhängigkeiten)).

**Wann:** Nach dem Abruf eines Kontos, sobald die Verbindung zum Server beendet ist. Dabei entstehen die PDFs für alle Mails, die noch keine haben. Das gilt für die gerade geladenen Mails ebenso wie für ältere, deren PDF fehlt, etwa nach dem Umstieg von einer älteren Version oder wenn eine PDF gelöscht wurde. `mailabruf fetch --dry-run` erzeugt keine PDFs.

**Inhalt:**

- Oben auf der ersten Seite steht ein grauer Kopfblock mit Absender (From), Empfänger (To), Cc, Datum, Betreff und den Namen und Größen der Anhänge.
- Darunter folgt der Text der Mail. Bei HTML-Mails wird die HTML-Fassung mit ihrem Layout genommen, sonst der reine Text in Festbreitenschrift.
- Format A4 mit Seitenzahl „Seite / Gesamt“ unten rechts.

**Externe Inhalte werden blockiert:** In die PDF kommen nur Bilder, die in der Mail selbst stecken (`cid:`-Teile und `data:`-URIs). Bilder aus dem Internet und Verweise auf lokale Dateien (`file://`) lädt das Programm nicht. Das hat drei Gründe: Absender erfahren nichts vom Öffnen (Tracking-Pixel), die Erzeugung funktioniert offline, und keine Mail kann lokale Dateien in die PDF einschleusen. Externe Logos fehlen deshalb in der PDF, stattdessen erscheint gegebenenfalls ihr Ersatztext.

**Grenzen:**

- Die Anhänge stehen in der PDF nur als Name. Ihr Inhalt steckt allein in der `.eml`-Datei.
- Sehr breite HTML-Mails können am rechten Rand abgeschnitten sein. Bilder werden auf Seitenbreite verkleinert.
- Die PDF ist eine Lesekopie. Das unveränderte Original vom Server bleibt die `.eml`-Datei.

**Dauer:** etwa 4 PDFs pro Sekunde. Ein erster Lauf über ein bestehendes Archiv mit rund 1.350 Mails dauerte etwa 6,5 Minuten und brauchte bis zu 550 MB Arbeitsspeicher. Danach kommen nur noch die neuen Mails dazu. Bei mehr als 50 ausstehenden PDFs zeigt das Programm alle 50 Stück den Fortschritt an.

**Fehler:** Scheitert eine PDF, gibt das Programm eine Warnung mit dem Grund aus, und der Lauf endet mit Exit-Code 1. Beim normalen Abruf versucht es dieselbe Mail nicht noch einmal, damit nicht jeder Lauf dieselbe Warnung bringt. Einen neuen Versuch startet:

```sh
mailabruf render             # fehlende und fehlgeschlagene PDFs erzeugen, index.html neu schreiben
mailabruf render --force     # alle PDFs neu erzeugen (z. B. nach einem Update)
```

`render` arbeitet ohne Verbindung zum Server. Ob eine PDF erzeugt wurde oder fehlgeschlagen ist (mit Fehlertext), steht in der Datenbank in den Spalten `pdf_status` und `pdf_error`.

### Index (index.html)

Im Zielordner jedes Kontos liegt eine `index.html`, z. B. `~/Mail-Archiv/privat/index.html`. Öffnen mit:

```sh
xdg-open ~/Mail-Archiv/<konto>/index.html
```

**Inhalt:** Eine Tabelle mit allen gespeicherten Mails des Kontos:

| Spalte | Inhalt |
|---|---|
| Date | Empfangsdatum (lokale Zeit) |
| Folder | Mailordner, Unterordner mit `/`, z. B. `Archiv/2025` |
| Subject | Betreff |
| From | Absender |
| To | Empfänger aus An und Cc |
| Files | Links **EML** und **PDF** |

Neueste Mails stehen oben. Der PDF-Link fehlt, wenn es zu der Mail keine PDF gibt. Mails, deren `.eml`-Datei gelöscht wurde, tauchen nicht auf. Als Duplikat übersprungene Mails erscheinen ebenfalls nicht, weil sie keine eigene Datei haben.

**Bedienung:**

- Ein Klick auf eine Spaltenüberschrift sortiert die Tabelle, ein zweiter Klick dreht die Reihenfolge um.
- Das Suchfeld durchsucht alle Spalten, ohne auf Groß- und Kleinschreibung zu achten.
- Das Auswahlfeld zeigt nur die Mails eines Ordners.
- Die Anzeige „x of y messages“ zeigt, wie viele Mails gerade sichtbar sind.
- Funktioniert offline, im hellen und dunklen Modus und auch in schmalen Fenstern.

**Aktualisierung:** Das Programm schreibt die Datei nach jedem `mailabruf fetch` komplett neu, auch wenn keine neuen Mails da sind. Dasselbe passiert bei `mailabruf render`. Die Daten kommen aus der Datenbank, nicht aus den Dateien. Betreff, Absender und Empfänger werden beim Speichern einer Mail einmal ausgelesen. Für Mails aus älteren Versionen werden sie beim ersten Lauf nachgetragen.

**Links:** Alle Links sind relativ zum Zielordner. Du kannst das komplette Archiv also verschieben, kopieren oder auf einen USB-Stick legen, und die Links funktionieren weiter. Ob ein Klick auf **EML** die Mail im Mailprogramm öffnet oder sie herunterlädt, hängt vom Browser ab. **PDF** öffnet in einem neuen Tab.

**Datenschutz:** Die Datei enthält Betreff, Absender und Empfänger aller Mails. Wie alle Dateien des Archivs ist sie deshalb nur für den eigenen Benutzer lesbar (`0600`).

Die Beschriftungen von Index und PDF-Kopf sind wie die übrige Programmausgabe englisch.

### Wie erkennt mailabruf bereits abgerufene Mails?

Pro Konto gibt es eine SQLite-Datenbank `~/.local/state/mailabruf/<konto>.sqlite3`. Der Ordner lässt sich in `config.toml` mit `[defaults] state_dir` ändern. In der Tabelle `messages` steht pro Mail eine Zeile. Erkannt wird eine Mail am Schlüssel **Ordner + UIDVALIDITY + UID**:

| Spalte | Inhalt |
|---|---|
| `folder` | IMAP-Ordnername, z. B. `Ablage` |
| `uidvalidity` | „Generationsnummer“ des Ordners auf dem Server |
| `uid` | Nummer der Mail im Ordner, vom Server vergeben, steigt immer an |
| `message_id` | `Message-ID`-Header der Mail |
| `status` | `saved` (gespeichert) oder `duplicate` (übersprungen) |
| `path` | Pfad zur `.eml`-Datei |
| weitere | Abrufzeit, Metadaten für den Index, PDF-Status |

Die Tabelle `folders` merkt sich die zuletzt gesehene UIDVALIDITY je Ordner.

**Ablauf je Ordner:**

1. Der Ordner wird nur lesend geöffnet (`EXAMINE`), der Server meldet dabei die UIDVALIDITY.
2. `UID SEARCH ALL` liefert alle UIDs. Davon zieht das Programm die UIDs ab, die für diesen Ordner und diese UIDVALIDITY schon in der Datenbank stehen. Übrig bleiben die neuen Mails.
3. Für die neuen Mails holt es nur die Kopfzeilen `Message-ID` und `Date`.
4. Steht die Message-ID schon in der Datenbank, egal in welchem Ordner, wird die Mail als `duplicate` eingetragen und nicht geladen. Sonst lädt das Programm die Mail und schreibt die `.eml`-Datei. **Erst danach** trägt es sie als `saved` ein. Bricht ein Lauf ab, wird die Mail beim nächsten Mal einfach erneut geladen.

**Warum UIDVALIDITY?** Ein IMAP-Server darf die UIDs eines Ordners neu vergeben, etwa nach einer Migration oder wenn ein Ordner neu angelegt wird. Er zeigt das durch eine neue UIDVALIDITY an. mailabruf gibt dann eine Warnung aus und prüft den Ordner neu. Dabei verhindert der Message-ID-Abgleich, dass Mails doppelt gespeichert werden.

**Praktische Folgen:**

- **Mail auf dem Server in einen anderen Ordner verschoben:** Sie bekommt dort eine neue UID, wird aber über die Message-ID als Duplikat erkannt und nicht erneut gespeichert.
- **Mail ohne `Message-ID`:** Sie wird immer gespeichert, weil es nichts gibt, womit sie sich vergleichen ließe.
- **Eine `.eml`-Datei gelöscht:** Sie wird nicht neu geladen, denn die Datenbank führt sie weiter als abgerufen. Im Index fehlt sie dann.
- **Datenbank gelöscht:** Beim nächsten Lauf lädt das Programm alles erneut herunter. Die vorhandenen Dateien bleiben liegen, die neuen kommen mit Zusatz `-1` im Namen dazu.
- **Datenbank einer älteren Version:** Sie wird beim ersten Start automatisch erweitert. PDFs und Index-Daten für vorhandene Mails werden nachträglich erzeugt.

Selbst hineinschauen:

```sh
sqlite3 ~/.local/state/mailabruf/<konto>.sqlite3 \
  "select folder, status, count(*) from messages group by folder, status"
```

## Automatisierung

**systemd (Benutzer-Timer, stündlich):**

```sh
mkdir -p ~/.config/systemd/user
cp contrib/mailabruf.service contrib/mailabruf.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mailabruf.timer
journalctl --user -u mailabruf.service     # Ausgaben ansehen
```

Damit der Timer auch ohne angemeldete Sitzung läuft, einmalig `loginctl enable-linger $USER` ausführen.

**cron:**

```
0 * * * * $HOME/.local/bin/mailabruf -q fetch
```

Mehrere Läufe gleichzeitig sind unkritisch: Pro Konto setzt das Programm eine Sperre. Ein zweiter Lauf überspringt das Konto mit einer Warnung.

## Entwicklung

```sh
uv run pytest
uv run mailabruf --help
```

## Lizenz

Copyright (C) 2026 Matthias Hotzel

Dieses Programm ist freie Software. Du kannst es unter den Bedingungen der GNU General Public License, wie von der Free Software Foundation veröffentlicht, weitergeben und/oder verändern, entweder gemäß Version 2 der Lizenz oder (nach deiner Wahl) jeder späteren Version (`GPL-2.0-or-later`).

Die Veröffentlichung erfolgt in der Hoffnung, dass das Programm nützlich ist, aber **ohne jede Gewährleistung**, sogar ohne die implizite Gewährleistung der Marktreife oder der Eignung für einen bestimmten Zweck. Details stehen in der GNU General Public License in der Datei [`LICENSE`](LICENSE).
