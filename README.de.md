# Atlas

<p align="center">
  <a href="README.md">简体中文</a> ·
  <a href="README.en.md">English</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <b>Deutsch</b> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.es.md">Español</a>
</p>

**Lokale und kontrollierte Dateiverwaltung und Datenträgerbereinigung für Windows: Vorschau zuerst, ausdrückliche Bestätigung, nachvollziehbare Vorgänge, Rollback soweit unterstützt und ein KI-Agent.**

Scan-Analyse, Duplikaterkennung, Bereinigung von Junk-Dateien, Verschieben zwischen Laufwerken und eine Speicherkarte helfen dabei, erst hinzusehen und dann zu handeln.

Schreibvorgänge zeigen zuerst die betroffene Dateiliste und warten auf Ihre Bestätigung. Manuelle Bereinigung verschiebt Dateien standardmäßig in die Quarantäne; eine Wiederherstellung ist möglich, wenn ursprünglicher Pfad und Dateisystemzustand es zulassen. Endgültige Agent-Bereinigung und das Leeren der Quarantäne sind nicht umkehrbar, werden gekennzeichnet und erfordern eine zusätzliche Bestätigung.
Die KI versteht Ihre Anweisung und erstellt einen Plan — die Dateien bewegt ausschließlich lokaler Code.
Abgesehen von Agent-Modellanfragen und ausdrücklich angeforderten KI-Erklärungen laufen Scan, Duplikaterkennung, Bereinigung, Migration und Sicherung lokal. Ein Konto ist nicht erforderlich.

> Es entscheidet nicht für Sie. Es legt offen, welche Dateien eine Aktion berührt.

![Atlas Startseite](docs/images/overview.png)

## Schnellstart

1. [Atlas-2.1.1-win64-portable.zip](https://github.com/whalevoyage/wenjianguanli/releases/latest) herunterladen und an einen beliebigen Ort entpacken (z. B. `D:\Atlas`)
2. `Atlas\Atlas.exe` doppelklicken
3. Der Browser öffnet sich automatisch. Es erscheint kein Konsolenfenster.

Python, Node oder andere Laufzeitumgebungen sind nicht nötig. Das Programm lauscht nur auf `127.0.0.1`, Daten liegen in `%LOCALAPPDATA%\Atlas`.
Deinstallieren heißt: diesen Ordner löschen.

**Ein zweiter Doppelklick startet keine zweite Instanz** — er zeigt nur auf die laufende.
**Ist Port 8000 belegt, wird automatisch ein freier Port gewählt**; die tatsächliche Adresse steht im Log.

## Funktionen

| Seite | Zweck |
| --- | --- |
| Start | Datenträgerkapazität, freigebbarer Platz, Aufgabenstatistik, letzte Aufgaben |
| Atlas-Agent | Natürlichsprachige Bedienung; Dateiaufgaben zuerst, allgemeine Fragen auch |
| Speicherkarte | TreeMap der Ordnerbelegung mit schrittweisem Hineinzoomen |
| Organisieren (Vorschau) | Vorschau nach Typ, Zeit, Größe oder Projekt — vollständig schreibgeschützt |
| Projekt-Radar | Erkennt Projektordner mit Typ, Größe, Dateizahl und lange ungenutzten Projekten |
| Scan-Analyse | Anzahl, Größe, Typverteilung, große und lange unberührte Dateien; Snapshots für Vorher/Nachher |
| Duplikate | Byte-genaue Duplikaterkennung; inklusive „ähnliche Bilder" (skaliert, neu komprimiert, erneut gespeichert, optional rotationstolerant) |
| Junk-Dateien | Temporärdateien, Logs, Caches, leere Ordner und 0-Byte-Dateien, dreistufige Risikoklasse |
| Volltextsuche | Lokaler FTS5-Index pro Ordner, Suche in Dateinamen und Inhalt |
| Verschieben zwischen Laufwerken | Kopieren und Element für Element prüfen; Quellen erst danach anfassen; Rollback möglich |
| Projekt-Backup | ZIP-Snapshots ohne Abhängigkeits- und Build-Ordner; Wiederherstellungsprüfung möglich |
| Aufgabencenter | Live-Fortschritt, Fehler, Abbruch und Rollback-Status |
| Papierkorb | Manuelle Bereinigung geht zuerst in die Quarantäne; Wiederherstellung, soweit Pfad und Dateisystem es erlauben |
| Protokoll | Audit-Einträge ansehen und exportieren |

## Sicherheitsmodell

- Manuelle Bereinigung zeigt zuerst eine Liste und verschiebt Dateien nach Bestätigung standardmäßig in die Quarantäne. Eine Wiederherstellung ist möglich, wenn ursprünglicher Pfad und Dateisystemzustand es zulassen. Endgültige Agent-Bereinigung und das Leeren der Quarantäne sind nicht umkehrbar und erfordern eine separate Bestätigung.
- Verschieben kopiert zuerst und prüft; die Quelle wird erst nach bestandener Prüfung angefasst.
- Lange Operationen laufen als Hintergrundaufgaben mit Fortschritt, Abbruch und Rollback.
- Projektordner, Statusordner und Quarantäne sind geschützt.
- Systemordner werden standardmäßig nicht gescannt. Im Standardmodus sind Schreibvorgänge dort gesperrt; im Modus „Vollzugriff“ kann dieser Schutz ausdrücklich gelockert werden. Atlas-eigene Daten und die Quarantäne bleiben geschützt.
- `node_modules`, `.git`, `__pycache__`, `.venv`, `dist`, `build` u. a. sind standardmäßig ausgeschlossen.
- Vor Verschieben und Backup wird der freie Platz am Ziel geprüft.
- Lauscht nur auf `127.0.0.1`, nicht im LAN erreichbar.

## Zur Dokumentation

Diese Seite ist eine **Zusammenfassung**. Details (Architektur, Konfiguration, Tests, Grenzen) finden Sie hier:

- Englische Fassung (alle 16 Kapitel): [README.en.md](README.en.md)
- Chinesische Originalfassung: [README.md](README.md)

Sicherheit und Datenschutz: [SECURITY.md](SECURITY.md) / [PRIVACY.md](PRIVACY.md) (beide auf Chinesisch).

## Lizenz

[MIT License](LICENSE). Freie Nutzung, Änderung und Weitergabe, solange Copyright- und Lizenzhinweis erhalten bleiben.
