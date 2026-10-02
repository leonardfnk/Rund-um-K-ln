# Startplatzbörsen-Monitor (GitHub Actions + ntfy)

Prüft alle ca. 5 Minuten eine oder mehrere URLs und schickt dir eine Push-Nachricht aufs Handy.
Im Modus `offers` meldet er **nur, wenn ein Angebot auftaucht**, und schreibt hinein, **was** angeboten
wird (z. B. Velodom 60/120) und **für wie viel** (Preise aus der Antwort). Er bucht nichts.

## Für Rund um Köln (Velodom 60/120)

Die Börse unter https://www.rundumkoeln.de/startplatzboerse/ lädt ihre Liste per JavaScript aus einer
RaceResult-Abfrage. Die Daten-URL (aus der Chrome-Konsole ausgelesen) lautet:

`https://api.raceresult.com/405972/NK8LI7I5V6KLE5TBZO0RKFJV03RNNVSX`

Sie liefert eine HTML-Tabelle mit den Spalten *Startnummer, Vorname, Link, Einstellungsdatum*
(Stand beim Test: keine Datenzeilen, also keine Angebote). Der Monitor meldet, sobald eine Datenzeile
auftaucht. Die Nachricht enthält Startnummer, Einstellungsdatum und den **Kaufen-Link**. Das Antippen
der Nachricht öffnet direkt diesen Link.

**Was die Liste nicht enthält:** Distanz (Velodom 60/120) und Preis. Beides siehst du erst im
Anmeldeformular hinter dem Link. Der Monitor folgt dem Link bewusst nicht selbst, damit er nichts
auslöst oder reserviert. Den Vornamen des Verkäufers übernimmt er nicht in die Nachricht.

Repository-Variablen für diesen Fall:

| Name | Wert |
|---|---|
| `WATCH_MODE` | `offers` |
| `WATCH_URL` | `https://api.raceresult.com/405972/NK8LI7I5V6KLE5TBZO0RKFJV03RNNVSX` |
| `NOTIFY_NOTE` | `Käufer zahlen die aktuelle Preiskategorie plus 25 € Ummeldegebühr. Börse bis 15.4.2027.` |
| `CLICK_URL` | **leer lassen**, dann öffnet die Nachricht direkt den Kaufen-Link |

Nach der Börsen-Deadline (15. April 2027 laut Veranstalter) kannst du den Workflow deaktivieren.
Laut Reglement ist ein Platz für alle sichtbar und nicht reservierbar, du musst nach dem Alarm
also schnell sein. Die URL steht im öffentlichen Workflow-Log, ist aber auf der Seite ohnehin für
jeden sichtbar.

## Einrichtung (ca. 10 Minuten)

1. **ntfy-App** auf dem Handy installieren (iOS/Android) und ein **geheimes Topic** abonnieren,
   z. B. `startplatz-` plus 12 zufällige Zeichen. Wer den Namen kennt, kann mitlesen, also nicht teilen.
2. **Neues GitHub-Repo** anlegen und diese Dateien hochladen
   (`monitor.py`, `.github/workflows/monitor.yml`, `README.md`).
3. Im Repo: *Settings → Secrets and variables → Actions*
   - Tab **Secrets**: `NTFY_TOPIC` = dein Topic
   - Tab **Variables**: siehe Tabelle oben
4. Tab **Actions**: Workflow *startplatz-monitor* öffnen → *Run workflow* → Haken bei **test** →
   du solltest sofort eine Testnachricht bekommen.
5. Danach einmal ohne Haken starten und im Lauf-Log prüfen, dass "Keine Angebote." erscheint.
   Erscheint der Hinweis "Platzhalter 'Lade …'", ist die URL noch die Seiten-URL statt der API-URL.

## Modi

| WATCH_MODE | Meldet, wenn ... | Weitere Variablen |
|---|---|---|
| `offers` (Standard) | eine HTML-Tabelle Datenzeilen enthält, sonst: ein Treffer von `OFFER_REGEX` (Standard `€|EUR|kaufen`) vorliegt, jeweils nur bei neuem Stand | optional `OFFER_REGEX`, `NOTIFY_NOTE`, `CLICK_URL` |
| `change` | sich der sichtbare Text ändert | optional `WATCH_IGNORE` |
| `appears` | der Text auftaucht | `WATCH_TEXT` |
| `disappears` | der Text verschwindet | `WATCH_TEXT` |

Im Modus `offers` kommt pro neuem Angebotsstand genau eine Nachricht. Verschwindet das Angebot
(jemand war schneller) und kommt später wieder, meldet er erneut.

## Grenzen, die du kennen solltest

- **Nicht sekundengenau:** GitHub-Zeitpläne laufen frühestens alle 5 Minuten und werden bei hoher
  Last oft um mehrere Minuten verzögert, selten ausgelassen.
- **60-Tage-Regel:** In öffentlichen Repos werden geplante Workflows nach 60 Tagen ohne Aktivität
  deaktiviert. Alle paar Wochen einen Commit machen (z. B. die README anfassen).
- **Kosten:** In einem **öffentlichen** Repo laufen Standard-Runner nach meinem Kenntnisstand
  kostenlos. In einem privaten Repo zählt jede Minute gegen dein Kontingent/Guthaben (ca. 8.600 Läufe
  pro Monat). Prüf das vorher in deinen GitHub-Billing-Einstellungen. Das Topic liegt als Secret im
  Repo, nie im Code. Die Workflow-Logs sind in öffentlichen Repos sichtbar, sie enthalten keine Secrets.
- **Tabellenformat:** Gelesen habe ich nur die leere Tabelle (Kopfzeile). Wie eine gefüllte Zeile
  aussieht, ist getestet, aber nicht an einem echten Angebot gesehen. Erscheint die erste echte
  Nachricht seltsam, schau dir die Antwort der URL im Browser an und gib mir Bescheid.
- **Fairness:** Prüf die AGB des Veranstalters (https://www.rundumkoeln.de/agb/). Alle 5 Minuten
  eine Abfrage ist sehr leicht, aber nicht jeder Betreiber erlaubt automatische Abrufe.
