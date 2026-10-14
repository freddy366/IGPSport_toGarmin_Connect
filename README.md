# iGPSport → Garmin Sync (abgesicherte Version)

Lädt deine Fahrten automatisch alle 3 Stunden von iGPSport zu Garmin Connect hoch.
Läuft komplett bei GitHub, dein PC muss nicht an sein.

> **Dies ist eine Vorlage.** Nicht forken und nicht hier Secrets eintragen,
> sondern über **„Use this template“** ein eigenes, **privates** Repo erstellen (siehe Schritt 1).

**Voraussetzungen:** GitHub-Account · iGPSport-Konto auf dem **internationalen** Server
([app.igpsport.com](https://app.igpsport.com), dort müssen deine Fahrten sichtbar sein) ·
Garmin-Connect-Konto · Python 3.12+ auf deinem Rechner (nur für den einmaligen Garmin-Login).

## Was anders ist als beim Original

| | Original | Diese Version |
|---|---|---|
| Garmin-Login | `garth` (eingestellt, neue Logins gehen nicht mehr) | `garminconnect` 0.3.17 |
| Garmin-Passwort in GitHub | ja | **nein**, nur Tokens |
| Garmin-2FA | muss aus sein | **darf an bleiben** |
| Repo | öffentlicher Fork | **privates** Repo |
| Fremde Push-Action auf `@master` | ja | entfernt |
| Commits bei jedem Sync | ja (öffentlich sichtbar) | keine |
| Versionen fest + Hash-geprüft | nein | ja |
| Rechte des Workflows | Standard | nur Lesen |
| Ohne Region-Angabe | Login geht an China-Server | Abbruch mit Fehlermeldung |

## Einrichtung

### 1. Eigenes privates Repository aus der Vorlage erstellen
1. Oben auf dieser Seite **Use this template → Create a new repository**
2. Name frei wählen, **Private** auswählen, **Create repository**

Wichtig: **Private**. Sonst wären die Protokolle deiner Läufe (mit Fahrtzeiten) öffentlich,
und über fremde Pull Requests könnte jemand an deine Garmin-Tokens im Cache kommen.

### 2. Garmin-Tokens erzeugen (einmalig, auf deinem PC)
Du brauchst Python 3.12 oder neuer ([python.org](https://www.python.org/downloads/)).
Lade dein Repo als ZIP herunter (**Code → Download ZIP**), entpacke es und öffne
im entpackten Ordner ein Terminal.

**Windows** (bei der Python-Installation „Add python.exe to PATH“ anhaken):
```
pip install garminconnect==0.3.17
python garmin_login.py
```

**Mac:**
```
python3 -m pip install garminconnect==0.3.17
python3 garmin_login.py
```
Tipp Mac: `pbcopy < garmin_tokens_secret.json` kopiert den Inhalt direkt in die Zwischenablage.

Das Script fragt E-Mail, Passwort und ggf. den 2FA-Code ab und legt die Datei
`garmin_tokens_secret.json` an. Dein Passwort verlässt dabei deinen PC nur Richtung Garmin.

### 3. Secrets hinterlegen
Im Repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Wert |
|---|---|
| `IGPSPORT_USERNAME` | dein iGPSport-Benutzername / E-Mail |
| `IGPSPORT_PASSWORD` | dein iGPSport-Passwort |
| `IGPSPORT_REGION` | `global` |
| `GARMIN_TOKENS` | kompletter Inhalt von `garmin_tokens_secret.json` |

**Danach `garmin_tokens_secret.json` auf deinem PC löschen.**

### 4. Testlauf
1. Tab **Actions → iGPSport to Garmin Sync → Run workflow**
2. Haken bei **Nur anzeigen, nichts hochladen** setzen → **Run workflow**
3. Im Log siehst du, welche Fahrten hochgeladen würden.
4. Passt alles: noch einmal ohne Haken starten. Ab dann läuft es automatisch.

## Gut zu wissen

- **Benachrichtigung bei Fehlern:** Schlägt ein Lauf fehl, schickt GitHub dir standardmäßig eine E-Mail.
- **Wenn Garmin den Login ablehnt** (Meldung „Tokens neu erzeugen“): Schritt 2 wiederholen und das
  Secret `GARMIN_TOKENS` überschreiben.
- **Doppelte Fahrten:** Fahrten, die sich zeitlich mit einer vorhandenen Garmin-Aktivität
  überschneiden (±5 Min.), werden übersprungen. Exakte Duplikate lehnt Garmin zusätzlich selbst ab.
- **Zeitraum:** Bei jedem Lauf werden die letzten 7 Tage geprüft (`LOOKBACK_DAYS` im Workflow).
  Für eine einmalige Nachholaktion kannst du den Wert vorübergehend erhöhen.
- **Kosten:** Private Repos haben im kostenlosen GitHub-Tarif 2.000 Actions-Minuten pro Monat.
  8 Läufe am Tag brauchen grob 250–500 Minuten.
- **Wenn die Tokens mal in falsche Hände geraten:** In den Sicherheitseinstellungen deines
  Garmin-Kontos das Passwort ändern und alle Sitzungen abmelden. Lokales Löschen allein
  macht ausgestellte Tokens nicht ungültig.
- **Nie** Pull Requests von Fremden annehmen und das Repo **privat** lassen.

## Hinweise

- Inoffizielle Schnittstellen von iGPSport und Garmin. Wenn einer der Dienste etwas ändert,
  kann der Sync brechen. Nutzung auf eigenes Risiko, siehe [LICENSE](LICENSE).
- Für die eigene Sicherheit: in den GitHub-Einstellungen unter *Emails* den Haken
  **„Keep my email addresses private“** setzen, damit Commits über die Weboberfläche
  nicht deine private Adresse enthalten.

## Danke

- Idee und ursprünglicher Ablauf: [hmqgg/iGPSport2Garmin](https://github.com/hmqgg/iGPSport2Garmin)
- Garmin-Login: [cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect) (MIT)
- Aktuelle iGPSport-Anfrageparameter: [dengxuhui/igpsport-mcp](https://github.com/dengxuhui/igpsport-mcp) (MIT)
