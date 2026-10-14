#!/usr/bin/env python3
"""
Einmaliger Garmin-Login auf DEINEM eigenen Rechner.

Fragt E-Mail, Passwort und ggf. den 2FA-Code ab und speichert danach nur die
Garmin-Tokens in "garmin_tokens_secret.json". Diesen Inhalt hinterlegst du in
GitHub als Secret GARMIN_TOKENS. Dein Garmin-Passwort landet nie bei GitHub.

Voraussetzung:  pip install garminconnect==0.3.17
Aufruf:         python garmin_login.py
"""

import getpass
import os
import sys
from pathlib import Path

try:
    from garminconnect import Garmin
except ImportError:
    sys.exit("Bitte zuerst installieren:  pip install garminconnect==0.3.17")

OUT = Path("garmin_tokens_secret.json")


def main() -> None:
    email = input("Garmin E-Mail: ").strip()
    password = getpass.getpass("Garmin Passwort (wird nicht angezeigt): ")

    client = Garmin(
        email,
        password,
        prompt_mfa=lambda: input("2FA-Code aus App/E-Mail: ").strip(),
    )
    client.login()  # kein Tokenstore -> nur im Speicher
    del password

    name = client.get_full_name() or "unbekannt"
    tokens = client.client.dumps()

    # Datei nur fuer dich lesbar anlegen
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tokens)

    print(f"\nLogin erfolgreich als: {name}")
    print(f"Tokens gespeichert in: {OUT.resolve()}")
    print(
        "\nNaechster Schritt: Inhalt dieser Datei als GitHub-Secret GARMIN_TOKENS\n"
        "hinterlegen und die Datei danach LOESCHEN.\n"
        "Behandle sie wie ein Passwort: Wer sie hat, kommt in dein Garmin-Konto."
    )


if __name__ == "__main__":
    main()
