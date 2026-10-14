#!/usr/bin/env python3
"""
iGPSport -> Garmin Connect Sync (gehaertete Variante).

Unterschiede zum Original (hmqgg/iGPSport2Garmin):
  * Garmin-Login ueber python-garminconnect statt des eingestellten "garth".
  * Kein Garmin-Passwort in GitHub: Das Script nutzt nur Tokens, die einmalig
    lokal mit garmin_login.py erzeugt werden (2FA bleibt aktiv).
  * Zustandslos: kein last_sync_date.json, keine Commits, kein Push.
    Duplikate werden ueber Zeitueberschneidung + Garmins eigene 409-Pruefung
    vermieden.
  * Timeouts ueberall, Groessenlimit und FIT-Pruefung beim Download.
  * Region muss explizit gesetzt sein (kein stiller Fallback auf China).
  * Bei Fehlern Exit-Code != 0, damit GitHub dich per Mail benachrichtigt.

Umgebungsvariablen:
  IGPSPORT_USERNAME, IGPSPORT_PASSWORD   Pflicht
  IGPSPORT_REGION                        Pflicht: "global" oder "cn"
  GARMIN_TOKENS                          Token-JSON aus garmin_login.py (Pflicht
                                         beim ersten Lauf, danach Fallback)
  GARMIN_TOKEN_DIR                       Ordner fuer aufgefrischte Tokens
                                         (Standard: garmin_tokens)
  LOOKBACK_DAYS                          Wie viele Tage zurueck pruefen (Std: 7)
  LOCAL_TZ                               Zeitzone fuer iGPSport-Zeiten ohne
                                         Zonenangabe (Std: Europe/Berlin)
  DRY_RUN                                "1" = nur anzeigen, nichts hochladen
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("igpsport-garmin")


class _GitHubAnnotations(logging.Handler):
    """Warnungen/Fehler zusaetzlich als GitHub-Annotation ausgeben,
    damit sie direkt auf der Uebersichtsseite des Laufs stehen."""

    def emit(self, record: logging.LogRecord) -> None:
        level = "error" if record.levelno >= logging.ERROR else "warning"
        msg = self.format(record).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level}::{msg}", flush=True)


if os.environ.get("GITHUB_ACTIONS") == "true":
    _h = _GitHubAnnotations(level=logging.WARNING)
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)


def notice(msg: str, *args) -> None:
    """Info-Meldung, die auf GitHub zusaetzlich als Hinweis erscheint."""
    log.info(msg, *args)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::notice::{msg % args if args else msg}", flush=True)
# Bibliotheken leise halten, damit nichts Sensibles im Log landet.
for noisy in ("garminconnect", "urllib3", "requests", "curl_cffi"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

HTTP_TIMEOUT = 30            # Sekunden pro Request
MAX_FIT_BYTES = 50 * 1024**2  # 50 MB
MAX_IGPSPORT_PAGES = 5
OVERLAP_BUFFER = timedelta(minutes=5)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


class SyncError(Exception):
    """Fehler, der den Lauf abbrechen soll."""


# --------------------------------------------------------------------------
# Zeit-Hilfsfunktionen
# --------------------------------------------------------------------------

_DOTTED_DATE = re.compile(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(.*)$")


def parse_time(value, local_tz: ZoneInfo) -> datetime:
    """Wandelt iGPSport-/Garmin-Zeitangaben robust in UTC-datetime um."""
    if value is None or value == "":
        raise ValueError("leere Zeitangabe")

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e11:  # Millisekunden
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    text = str(value).strip()
    if text.isdigit():
        return parse_time(int(text), local_tz)

    m = _DOTTED_DATE.match(text)
    if m:
        y, mo, d, rest = m.groups()
        text = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}{rest}"
    text = text.replace("Z", "+00:00")

    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(timezone.utc)


def overlaps(a_start: datetime, a_secs: float, b_start: datetime, b_secs: float) -> bool:
    a_end = a_start + timedelta(seconds=max(a_secs, 0))
    b_end = b_start + timedelta(seconds=max(b_secs, 0))
    return a_start - OVERLAP_BUFFER <= b_end and b_start - OVERLAP_BUFFER <= a_end


# --------------------------------------------------------------------------
# iGPSport
# --------------------------------------------------------------------------

class IGPSportClient:
    REGIONS = {
        # (API-Basis, Web-App-Herkunft, App-Version wie von der Web-App gesendet)
        "cn": ("https://prod.zh.igpsport.com/service", "https://app.igpsport.cn", "8.07.08"),
        "global": ("https://prod.en.igpsport.com/service", "https://app.igpsport.com", "8.07.06"),
    }

    def __init__(self, username: str, password: str, region: str):
        if region not in self.REGIONS:
            raise SyncError(
                f"IGPSPORT_REGION muss 'global' oder 'cn' sein, nicht {region!r}"
            )
        self.base_url, origin, app_version = self.REGIONS[region]
        self.region = region
        if region == "cn":
            log.warning("Region 'cn': Der China-Server verlangt inzwischen signierte "
                        "Anfragen, das unterstuetzt dieses Script nicht.")
        self._username = username
        self._password = password
        self.session = requests.Session()
        self.session.headers.update({
            "user-agent": USER_AGENT,
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "origin": origin,
            "referer": origin + "/",
            "qiwu-app-version": app_version,
            "timezone": "Asia/Shanghai",  # sendet die Web-App unabhaengig vom Standort
        })

    def _get_json(self, method: str, path: str, **kwargs) -> dict:
        resp = self.session.request(method, self.base_url + path, timeout=HTTP_TIMEOUT, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0 or "data" not in data:
            raise SyncError(f"iGPSport-Fehler bei {path.split('?')[0]}: {data.get('message', 'unbekannt')}")
        return data["data"]

    def login(self) -> None:
        data = self._get_json(
            "POST", "/auth/account/login",
            json={"username": self._username, "password": self._password, "appId": "igpsport-web"},
        )
        self._password = None  # nicht laenger als noetig im Speicher halten
        token = (data or {}).get("access_token")
        if not token:
            raise SyncError("iGPSport-Login: kein Token erhalten")
        self.session.headers["authorization"] = f"Bearer {token}"
        log.info("iGPSport-Login erfolgreich")

    def list_activities(self, page: int, size: int = 20) -> list[dict]:
        data = self._get_json(
            "GET", "/web-gateway/web-analyze/activity/queryMyActivity",
            params={"pageNo": page, "pageSize": size, "reqType": 0, "sort": 1},
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("rows") or data.get("list") or []
        return []  # iGPSport liefert "data": null, wenn es keine Aktivitaeten gibt

    def activity_detail(self, ride_id) -> dict:
        data = self._get_json(
            "GET", f"/web-gateway/web-analyze/activity/queryActivityDetail/{int(ride_id)}"
        )
        if not isinstance(data, dict):
            raise SyncError(f"Aktivitaet {ride_id}: keine Details erhalten")
        return data

    @staticmethod
    def download_fit(url: str) -> bytes:
        """FIT-Datei laden. Bewusst ohne iGPSport-Token (fremder Host)."""
        if urlparse(url).scheme != "https":
            raise SyncError("FIT-URL ist kein https-Link, wird uebersprungen")
        with requests.get(url, timeout=HTTP_TIMEOUT, stream=True,
                          headers={"user-agent": USER_AGENT}) as resp:
            resp.raise_for_status()
            chunks, total = [], 0
            for chunk in resp.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_FIT_BYTES:
                    raise SyncError("FIT-Datei groesser als 50 MB, wird uebersprungen")
                chunks.append(chunk)
        data = b"".join(chunks)
        # FIT-Header: Byte 8-11 = ".FIT"
        if len(data) < 14 or data[8:12] != b".FIT":
            raise SyncError("Download ist keine gueltige FIT-Datei")
        return data


# --------------------------------------------------------------------------
# Garmin
# --------------------------------------------------------------------------

def garmin_login(token_dir: Path) -> Garmin:
    """Login nur per Token, nie per Passwort."""
    token_file = token_dir / "garmin_tokens.json"

    if token_file.is_file():
        try:
            g = Garmin()
            g.login(str(token_dir))
            log.info("Garmin: Tokens aus dem Cache geladen")
            return g
        except Exception as e:  # noqa: BLE001
            log.warning("Garmin: Cache-Tokens ungueltig (%s), nutze GARMIN_TOKENS", type(e).__name__)

    seed = os.environ.get("GARMIN_TOKENS", "").strip()
    if not seed:
        raise SyncError("GARMIN_TOKENS fehlt. Bitte garmin_login.py lokal ausfuehren.")
    try:
        g = Garmin()
        g.login(seed)
    except Exception as e:  # noqa: BLE001
        raise SyncError(
            "Garmin-Login mit GARMIN_TOKENS fehlgeschlagen "
            f"({type(e).__name__}). Tokens neu erzeugen: garmin_login.py"
        ) from None
    log.info("Garmin: Tokens aus GARMIN_TOKENS geladen")
    return g


def save_garmin_tokens(g: Garmin, token_dir: Path) -> None:
    """Aufgefrischte Tokens fuer den naechsten Lauf ablegen (landet im Cache)."""
    try:
        g.client.dump(str(token_dir))
    except Exception as e:  # noqa: BLE001
        log.warning("Garmin-Tokens konnten nicht gespeichert werden: %s", type(e).__name__)


def garmin_intervals(g: Garmin, since: datetime) -> list[tuple[datetime, float]]:
    start = (since - timedelta(days=1)).date().isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    activities = g.get_activities_by_date(start, end)
    result = []
    for a in activities:
        try:
            st = parse_time(a["startTimeGMT"], ZoneInfo("UTC"))
            result.append((st, float(a.get("duration") or 0)))
        except Exception:  # noqa: BLE001
            log.warning("Garmin-Aktivitaet ohne lesbare Startzeit ignoriert")
    return result


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    ride_id: int
    start: datetime
    duration: float
    fit_url: str


def collect_candidates(ig: IGPSportClient, since: datetime, local_tz: ZoneInfo) -> list[Candidate]:
    out: list[Candidate] = []
    newest: datetime | None = None
    for page in range(1, MAX_IGPSPORT_PAGES + 1):
        rows = ig.list_activities(page)
        if not rows:
            if page == 1:
                log.warning(
                    "iGPSport meldet fuer dieses Konto ueberhaupt keine Aktivitaeten "
                    "(Region '%s'). Stimmt die Region zum Konto?", ig.region,
                )
            break
        reached_old = False
        for row in rows:
            ride_id = row.get("rideId")
            try:
                day = parse_time(row.get("startTime"), local_tz)
            except Exception:  # noqa: BLE001
                log.warning("Aktivitaet %s: Datum nicht lesbar, uebersprungen", ride_id)
                continue
            if newest is None or day > newest:
                newest = day
            # Listen-Datum hat oft nur Tagesgenauigkeit -> 1 Tag Puffer
            if day < since - timedelta(days=1):
                reached_old = True
                continue
            try:
                detail = ig.activity_detail(ride_id)
                start = parse_time(detail.get("startTime"), local_tz)
                duration = float(detail.get("totalTime") or 0)
                fit_url = detail.get("fitUrl") or row.get("fitOssPath")
            except Exception as e:  # noqa: BLE001
                log.warning("Aktivitaet %s: Details nicht lesbar (%s)", ride_id, type(e).__name__)
                continue
            if start < since:
                continue
            if not fit_url:
                log.warning("Aktivitaet %s: keine FIT-Datei vorhanden", ride_id)
                continue
            out.append(Candidate(int(ride_id), start, duration, str(fit_url)))
        if reached_old:
            break
    if newest is not None:
        notice("Neueste Aktivitaet bei iGPSport: %s", newest.astimezone(local_tz).strftime("%d.%m.%Y"))
    return out


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SyncError(f"Umgebungsvariable {name} fehlt")
    return value


def main() -> int:
    region = env_required("IGPSPORT_REGION").lower()
    local_tz = ZoneInfo(os.environ.get("LOCAL_TZ", "Europe/Berlin"))
    lookback = int(os.environ.get("LOOKBACK_DAYS", "7"))
    dry_run = os.environ.get("DRY_RUN", "") == "1"
    token_dir = Path(os.environ.get("GARMIN_TOKEN_DIR", "garmin_tokens"))
    since = datetime.now(timezone.utc) - timedelta(days=lookback)

    ig = IGPSportClient(env_required("IGPSPORT_USERNAME"), env_required("IGPSPORT_PASSWORD"), region)
    ig.login()

    candidates = collect_candidates(ig, since, local_tz)
    notice("%d iGPSport-Aktivitaet(en) der letzten %d Tage gefunden", len(candidates), lookback)
    if not candidates:
        return 0

    g = garmin_login(token_dir)
    failures = 0
    try:
        existing = garmin_intervals(g, since)  # Fehler hier -> Abbruch statt Blindupload
        todo = [
            c for c in candidates
            if not any(overlaps(c.start, c.duration, s, d) for s, d in existing)
        ]
        notice("%d davon fehlen noch bei Garmin", len(todo))

        for c in todo:
            label = f"Aktivitaet {c.ride_id} ({c.start.astimezone(local_tz):%Y-%m-%d %H:%M})"
            if dry_run:
                notice("[Testlauf] wuerde hochladen: %s", label)
                continue
            try:
                fit = ig.download_fit(c.fit_url)
            except Exception as e:  # noqa: BLE001
                log.error("%s: Download fehlgeschlagen (%s)", label, e if isinstance(e, SyncError) else type(e).__name__)
                failures += 1
                continue

            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"igpsport_{c.ride_id}.fit"
                path.write_bytes(fit)
                try:
                    g.upload_activity(str(path))
                    notice("%s: hochgeladen", label)
                except GarminConnectTooManyRequestsError:
                    log.error("Garmin-Ratelimit erreicht, Rest beim naechsten Lauf")
                    failures += 1
                    break
                except GarminConnectConnectionError as e:
                    if "409" in str(e):
                        log.info("%s: existiert bereits bei Garmin", label)
                    else:
                        log.error("%s: Upload fehlgeschlagen (%s)", label, str(e)[:120])
                        failures += 1
            time.sleep(2)
    finally:
        save_garmin_tokens(g, token_dir)

    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncError as e:
        log.error("%s", e)
        sys.exit(1)
    except GarminConnectAuthenticationError:
        log.error("Garmin-Anmeldung abgelehnt. Tokens neu erzeugen: garmin_login.py")
        sys.exit(1)
    except requests.RequestException as e:
        log.error("Netzwerkfehler: %s", type(e).__name__)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        import traceback
        where = traceback.extract_tb(e.__traceback__)
        frame = next((f for f in reversed(where) if f.filename.endswith("sync.py")), where[-1])
        log.error(
            "Unerwarteter Fehler: %s: %s (sync.py, Zeile %s, in %s)",
            type(e).__name__, str(e)[:200], frame.lineno, frame.name,
        )
        sys.exit(1)
