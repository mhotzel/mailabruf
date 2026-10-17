# mailabruf

Lädt alle Nachrichten aus allen Ordnern eines oder mehrerer IMAP-Postfächer (z. B. Strato) herunter und speichert sie lokal als `.eml`-Dateien. Anhänge bleiben unverändert in der Datei. Bereits geladene Nachrichten merkt sich das Programm lokal und lädt sie nicht erneut.

Zu jeder Mail legt das Programm zusätzlich eine PDF-Datei mit gleichem Namen an. Außerdem schreibt es pro Postfach eine `index.html` mit allen Mails. Diese Datei wird bei jedem Abruf aktualisiert.

- Auf dem Server wird nur gelesen (`EXAMINE` und `BODY.PEEK`). Der Status „gelesen“ ändert sich dadurch nicht.
- Duplikate erkennt das Programm über die IMAP-UID (pro Ordner und `UIDVALIDITY`) und zusätzlich über den `Message-ID`-Header pro Konto. Liegt dieselbe Mail in mehreren Ordnern, wird sie nur einmal gespeichert.
- Benötigt Python ≥ 3.11. Einzige externe Abhängigkeit ist [WeasyPrint](https://weasyprint.org/) für die PDFs. Es braucht die Systembibliothek Pango, die unter Fedora normalerweise schon installiert ist (`dnf install pango`).

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

**PDF:** Oben auf der ersten Seite stehen Absender, Empfänger, Cc, Datum, Betreff und die Namen der Anhänge, darunter folgt der Text der Mail. Bei HTML-Mails wird die HTML-Fassung genommen, sonst der reine Text. Bilder kommen nur in die PDF, wenn sie in der Mail selbst stecken. Externe Bilder und Links auf lokale Dateien werden blockiert. Das hat drei Gründe: Absender erfahren nichts vom Öffnen, es funktioniert offline, und keine Mail kann lokale Dateien in die PDF einschleusen. Die Anhänge selbst stecken nur in der `.eml`-Datei.

Scheitert eine PDF, gibt das Programm eine Warnung aus und versucht es beim normalen Abruf nicht erneut. Einen neuen Versuch startet `mailabruf render`.

**index.html:** Die Datei ist in sich geschlossen, braucht also keine externen Abhängigkeiten und funktioniert offline. Sie listet alle Mails mit Datum, Ordner, Betreff, Absender und Empfängern (An und Cc) und verlinkt jeweils die EML- und die PDF-Datei. Ein Klick auf eine Spaltenüberschrift sortiert die Tabelle, das Suchfeld durchsucht alle Spalten, und ein Auswahlfeld zeigt nur einen Ordner. Öffnen mit z. B. `xdg-open ~/Mail-Archiv/<konto>/index.html`.

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
