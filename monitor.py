#!/usr/bin/env python3
"""Startplatzbörsen-Watcher: schickt eine ntfy-Push-Nachricht aufs Handy.

Konfiguration über Umgebungsvariablen (siehe README.md):
  WATCH_URL      Eine oder mehrere URLs (durch Leerzeichen, Komma oder Zeilenumbruch getrennt)
  WATCH_MODE     offers | change | appears | disappears   (Standard: offers)
                   offers      meldet nur, wenn Angebote da sind: bei HTML-Tabellen (z. B. RaceResult)
                               jede Datenzeile, sonst Treffer von OFFER_REGEX; nur bei neuem Stand
                   change      meldet jede Änderung des sichtbaren Seitentexts
                   appears     meldet, wenn WATCH_TEXT auftaucht
                   disappears  meldet, wenn WATCH_TEXT verschwindet
  WATCH_TEXT     Suchtext für appears/disappears
  OFFER_REGEX    optionale Regex für den Modus offers ohne Tabelle (Standard: €|EUR|kaufen)
  WATCH_IGNORE   optionale Regex, deren Treffer vor dem Vergleich entfernt werden
  NOTIFY_NOTE    optionaler Zusatzhinweis in der Nachricht (z. B. Ummeldegebühr)
  CLICK_URL      Link, der beim Tippen auf die Nachricht geöffnet wird (Standard: erste WATCH_URL)
  NTFY_TOPIC     Dein geheimes ntfy-Topic (Pflicht)
  NTFY_SERVER    Standard: https://ntfy.sh
  STATE_FILE     Standard: state.json
  ERR_ALERT_AFTER  Fehlversuche in Folge bis zur Warnung (Standard 12)

Test der Benachrichtigung:  python monitor.py --test
"""
import hashlib
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

URLS = [u for u in re.split(r"[\s,]+", os.environ.get("WATCH_URL", "").strip()) if u]
MODE = os.environ.get("WATCH_MODE", "offers").strip().lower() or "offers"
TEXT = os.environ.get("WATCH_TEXT", "").strip()
OFFER_REGEX = os.environ.get("OFFER_REGEX", "").strip() or r"€|\beur\b|\bkaufen\b"
IGNORE = os.environ.get("WATCH_IGNORE", "").strip()
NOTE = os.environ.get("NOTIFY_NOTE", "").strip()
CLICK_URL = os.environ.get("CLICK_URL", "").strip()
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
ERR_ALERT_AFTER = int(os.environ.get("ERR_ALERT_AFTER", "12") or "12")
USER_AGENT = "Mozilla/5.0 (compatible; startplatz-watch/1.1; privater Hobby-Monitor)"
MIN_TEXT_LEN = 50  # gilt nur für Seiten-Modi, nicht für offers (leere Liste ist dort normal)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def visible_text(raw_html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|noscript|head|title)[^>]*>.*?</\1>", " ", raw_html)
    cleaned = re.sub(r"(?s)<!--.*?-->", " ", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def flatten_json(data, prefix: str = "") -> list:
    parts = []
    if isinstance(data, dict):
        for key, value in data.items():
            parts += flatten_json(value, f"{key}: " if not prefix else f"{prefix}{key}: ")
    elif isinstance(data, list):
        for value in data:
            parts += flatten_json(value, prefix)
    elif data is not None and str(data).strip() != "":
        parts.append(f"{prefix}{data}".strip())
    return parts


def read_response(response):
    """Gibt (raw, text) zurück: Rohtext der Antwort und bereinigter sichtbarer Text."""
    ctype = response.headers.get("Content-Type", "").lower()
    if "charset" not in ctype:
        response.encoding = "utf-8"  # requests rät sonst ISO-8859-1 und verstümmelt €, ö usw.
    raw = response.text
    if "json" in ctype or raw.lstrip()[:1] in ("{", "["):
        try:
            parts = flatten_json(response.json())
            text = " | ".join(visible_text(p) if "<" in p else p for p in parts)
        except ValueError:
            text = visible_text(raw)
    else:
        text = visible_text(raw)
    if IGNORE:
        text = re.sub(IGNORE, " ", text)
    return raw, re.sub(r"\s+", " ", text).strip()


def fetch(url: str):
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            response.raise_for_status()
            raw, text = read_response(response)
            if MODE != "offers" and len(text) < MIN_TEXT_LEN:
                raise RuntimeError("Seite liefert kaum Text (evtl. JavaScript, Login oder Bot-Sperre)")
            return raw, text
        except Exception as exc:  # noqa: BLE001 - bewusst breit, Fehler wird gemeldet
            last_error = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last_error))


def table_rows(raw_html: str):
    """Liest HTML-Tabellen: gibt (Kopfzeile, Datenzeilen) zurück. Zeile = {"cells": [...], "links": [...]}."""
    header, rows = [], []
    for tr in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", raw_html):
        cells = re.findall(r"(?is)<t([hd])\b[^>]*>(.*?)</t\1>", tr)
        if not cells:
            continue
        texts = [visible_text(c[1]) for c in cells]
        joined = " ".join(texts)
        if any(kind.lower() == "h" for kind, _ in cells) or re.search(r"startnummer|einstellungsdatum", joined, re.I):
            header = header or texts
            continue
        # echte Datenzeile: mehrere Zellen und irgendwo eine Ziffer ("Keine Einträge" fällt raus)
        if len(texts) >= 2 and any(ch.isdigit() for t in texts for ch in t):
            links = re.findall(r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"']", tr)
            rows.append({"cells": texts, "links": links})
    return header, rows


def describe_row(header: list, row: dict) -> str:
    """Kurzbeschreibung einer Zeile. Den Vornamen des Verkäufers lassen wir bewusst weg."""
    cells = row["cells"]

    def pick(*names):
        for i, label in enumerate(header):
            if any(n in label.lower() for n in names) and i < len(cells):
                return cells[i]
        return ""

    parts = []
    bib = pick("startnummer")
    date = pick("einstellungsdatum", "datum")
    if bib:
        parts.append(f"Startnummer {bib}")
    if date:
        parts.append(f"eingestellt {date}")
    return ", ".join(parts) if parts else " | ".join(c for c in cells if c)


def notify(title: str, message: str, click: str = "") -> None:
    headers = {"Title": title, "Priority": "high", "Tags": "bell"}  # Title bewusst ASCII
    if click:
        headers["Click"] = click
    response = requests.post(f"{SERVER}/{TOPIC}", data=message.encode("utf-8"), headers=headers, timeout=15)
    response.raise_for_status()


def summarize_offer(text: str) -> str:
    events = []
    for match in re.finditer(r"velodom\s*(\d+)", text, re.I):
        label = f"Velodom {match.group(1)}"
        if label not in events:
            events.append(label)
    prices = []
    for match in re.finditer(r"\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?\s?€|€\s?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?", text):
        price = match.group(0).strip()
        if price not in prices:
            prices.append(price)
    headline = " | ".join(filter(None, [", ".join(events), ", ".join(prices)]))
    excerpt = text if len(text) <= 500 else text[:500] + " ..."
    return (headline + "\n" if headline else "") + excerpt


def check_one(url: str, sub: dict) -> None:
    """Prüft eine URL und passt den Teilzustand `sub` an. Wirft RuntimeError bei Abruffehlern."""
    raw, text = fetch(url)
    lowered = text.lower()
    click = CLICK_URL or url

    if "lade …" in lowered or "lade ..." in lowered:
        print(f"Hinweis: {url} enthält nur den Platzhalter 'Lade …'. Die Angebote kommen vermutlich "
              f"per JavaScript aus einer anderen URL (API). Diese URL stattdessen eintragen.")

    if MODE == "offers":
        header, rows = table_rows(raw) if re.search(r"(?i)<table", raw) else ([], [])
        if rows:  # Tabelle mit Datenzeilen = Angebote vorhanden
            digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()
            has_offers = True
        else:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            has_offers = False if re.search(r"(?i)<table", raw) else bool(re.search(OFFER_REGEX, text, re.I))
        if has_offers and digest != sub.get("notified"):
            if rows:
                lines = []
                first_link = ""
                for row in rows[:5]:
                    link = urljoin(url, row["links"][0]) if row["links"] else ""
                    first_link = first_link or link
                    lines.append("• " + describe_row(header, row) + (f"\n  Kaufen: {link}" if link else ""))
                more = f"\n(+{len(rows) - 5} weitere)" if len(rows) > 5 else ""
                message = (f"Startplatz verfügbar ({len(rows)} Angebot{'e' if len(rows) != 1 else ''}):\n"
                           + "\n".join(lines) + more
                           + "\nDistanz und Preis zeigt das Formular hinter dem Link.")
                click = CLICK_URL or first_link or url
            else:
                message = "Startplatz verfügbar:\n" + summarize_offer(text)
            if NOTE:
                message += "\n" + NOTE
            notify("Startplatz frei", message, click)
            sub["notified"] = digest
            print("Angebot erkannt, Benachrichtigung gesendet.")
        elif has_offers:
            print("Angebot weiterhin vorhanden (bereits gemeldet).")
        else:
            sub["notified"] = None
            print("Keine Angebote.")
    elif MODE == "change":
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        previous = sub.get("hash")
        if previous and previous != digest:
            notify("Startplatz-Alarm", "Die Seite hat sich geändert. Jetzt prüfen!", click)
            print("Änderung erkannt, Benachrichtigung gesendet.")
        else:
            print("Keine Änderung." if previous else "Erster Lauf, Zustand gespeichert.")
        sub["hash"] = digest
    elif MODE == "appears":
        present = TEXT.lower() in lowered
        if present and not sub.get("present"):
            notify("Startplatz-Alarm", f"Text gefunden: {TEXT}", click)
            print("Text aufgetaucht, Benachrichtigung gesendet.")
        else:
            print("Text vorhanden (bereits gemeldet)." if present else "Text nicht vorhanden.")
        sub["present"] = present
    else:  # disappears
        gone = TEXT.lower() not in lowered
        if gone and not sub.get("gone"):
            notify("Startplatz-Alarm", f"Der Text ist verschwunden: {TEXT}. Vermutlich sind Plätze da.", click)
            print("Text verschwunden, Benachrichtigung gesendet.")
        else:
            print("Text weiterhin weg (bereits gemeldet)." if gone else "Text weiterhin vorhanden.")
        sub["gone"] = gone


def main() -> int:
    if not TOPIC:
        print("NTFY_TOPIC fehlt.")
        return 2
    if "--test" in sys.argv:
        notify("Startplatz-Monitor", "Testnachricht: Benachrichtigung funktioniert.", CLICK_URL or (URLS[0] if URLS else ""))
        print("Testnachricht gesendet.")
        return 0
    if not URLS:
        print("WATCH_URL fehlt.")
        return 2
    if MODE not in {"offers", "change", "appears", "disappears"}:
        print("WATCH_MODE muss offers, change, appears oder disappears sein.")
        return 2
    if MODE in {"appears", "disappears"} and not TEXT:
        print("Für appears/disappears wird WATCH_TEXT benötigt.")
        return 2

    state = load_state()
    for url in URLS:
        sub = state.setdefault("u:" + url, {})
        try:
            check_one(url, sub)
            sub["errors"] = 0
        except RuntimeError as exc:
            errors = int(sub.get("errors", 0)) + 1
            sub["errors"] = errors
            print(f"Abruf fehlgeschlagen ({errors}x in Folge): {url}: {exc}")
            if errors == ERR_ALERT_AFTER:
                notify("Startplatz-Monitor Fehler",
                       f"{url} seit {errors} Prüfungen nicht abrufbar: {exc}", CLICK_URL or url)
    save_state(state)
    return 0  # kein Workflow-Fehler, damit GitHub keine Mail-Flut schickt


if __name__ == "__main__":
    sys.exit(main())
