# Kompakte Einstellungen und Aufnahmefeedback

Aktuelle Ergänzungen und Prüfliste: [Hover, Hotkeys und Untertitelwechsel](subtitleplayer-2026-10-22-hover-input.md).

Lokal auf `feature/browser-sync-native-capture`. Keine Testläufe, App-Neustarts,
Browser-/Anki-Liveaktionen oder GitHub-Uploads für diese Änderung. Nur statische
Syntax- und Codeprüfung. Persönliche Daten und bestehende Notizen wurden dabei
nicht bearbeitet.

## Änderungen und gefundene Probleme

| Bereich | Befund und Änderung |
|---|---|
| Tabwechsel | Jeder Wechsel packte alle Einstellungsgruppen erneut und setzte Scrollpositionen zurück. Unveränderte Filter werden jetzt übersprungen; Layoutmaße werden im Speicher gecacht und Aktualisierungen gebündelt. |
| Anki-Layout | Gemeinsame untere Rasterzeile und pauschal verdoppelte Eingabebreiten entfernt. Links Connection, Deck/Model, Tags; rechts Language, Browser Recording, Anki Fields. EXE-Pfad kurz neben der Startup-Checkbox. |
| General | Synchronisierung am Ende der rechten Spalte; linke Spalte beginnt wieder mit Playback/Overlay. |
| Hauptfenster | Inhaltshöhe plus acht logische Pixel; die gespeicherte Breite und Position bleiben erhalten. |
| Hover-Pause | Bisher wurden Desktop-Zustand und sofortige Mauswechsel ausgewertet, ohne den Pause-Abschluss abzuwarten. Jetzt besitzt Hover eine Pause-Bestätigung und eine Quellen-/Befehlsgeneration. Beide Recorder verwenden bei Verbindung denselben Browserweg. |
| Wortfarbe | Wartete bisher auf spätere Notizabfragen und Annotation-Aktualisierung. Jetzt operationseigene, vorläufige Farbmarkierung vor der eigentlichen Arbeit, Bestätigung nach Notizerstellung und Rücknahme bei deren Fehler. Keine vorläufigen Einträge in der Vokabeldatenbank. |
| Aufnahmehäkchen | Wartete auf vollständige Anki-Medienübernahme. Native bestätigt jetzt nach lokaler WAV-/Bildprüfung und atomarer Sicherung, während Anki unabhängig weiterarbeitet. asbplayer bestätigt beim ersten überprüften frischen Mediennachweis. |
| Screenshot | Native Einstellung `CAPTURE_SCREENSHOT_POSITION`, Standard `500 ms`; auch Brüche wie `1/2`. Zeit zählt ab gemapptem Cue-Beginn und wird auf den eingefrorenen Clip begrenzt. Bei asbplayer deaktiviert. |

Das grüne Aufnahmehäkchen bedeutet **Aufnahme erfolgreich gesichert**. Die
vollständige Karte ist erst nach gesonderter Medienprüfung fertig. Spätere
Importfehler bleiben im Log und in der Wiederherstellung erkennbar.

## Weiter benötigt

- asbplayer-Aufnahmehotkey, Fensterziel, Wartezeit nach Kartenerstellung und
  Medien-Prüfintervall: gehören weiterhin zum ursprünglichen Recorder-Adapter.
- Operation-ID, explizite Notiz-ID, eingefrorene Auswahl, Medienvergleich und
  Wiederherstellung: verhindern falsche Notizzuordnung und verlorene Aufnahmen.
- Browser-Identität, Player-Bestätigungen und Eingriffsgenerationen: gelten
  unabhängig vom ausgewählten Recorder.
- X1 fügt mit frischer Aufnahme hinzu. X2 fügt über Untertiteln ohne Aufnahme
  hinzu und schaltet außerhalb die Untertitel um. Capture-only verwendet eine
  ausdrücklich bekannte, von SubtitlePlayer in diesem Lauf angelegte Notiz.
- Medienwiederverwendung nur bei Add ohne Aufnahme; Capture verlangt frische
  Medien. Diese Pfade wurden im Code nachverfolgt, nicht live getestet.

## Ersetzt oder entfernbar

- Entfernt: ungenutzte Tastaturimporte des Overlay-Controllers sowie unbenutzte
  Hover-Timer-/synthetische Wiedergabetasten-Initialisierungen.
- Ersetzt: erneutes vollständiges Layout bei unverändertem Tabwechsel und die
  gemeinsame untere Zeile im Anki-Tab.
- Aufräumkandidaten für einen gesonderten Durchgang: verbliebene OCR-UI-Helfer,
  alte Region-Konstanten und Aufrufe inzwischen leerer Simulations-Timer-Helfer.
  Ihre Archiv-/Migrationsdaten bleiben erhalten.
- Die gezielte Unterdrückung des injizierten asbplayer-Hotkeys bleibt nötig;
  sie darf nicht zusammen mit überholten allgemeinen Tastaturwegen entfernt werden.

## asbplayer ohne Browser-Verbindung

Nur hier bleibt der ausdrücklich gewünschte alte Fenster-/Space-Weg für Hover
aktiv. Er merkt sich das konkrete Zielfenster, stellt den Fokus wieder her und
verwirft die automatische Rückkehr bei verändertem Fenstertitel. Ohne Browser-
Beobachtung kann dieser Kompatibilitätsweg den tatsächlichen Play/Pause-Zustand
nicht sicher auslesen; es bleibt der frühere gepaarte Toggle-Weg. Bei Verbindung
werden keine Space-Tasten gesendet.

## Manueller Check durch den Benutzer

1. App neu starten. Erweiterung auf **0.3.5** neu laden und Videoseite einmal
   aktualisieren; bei Bedarf das Erweiterungspopup zur Audioverbindung öffnen.
2. Hauptfensterhöhe, unveränderte Position/Breite und sichtbare Zeitbeschriftung
   prüfen. General-Sync unten rechts, Tags links und Fields rechts prüfen.
3. Tabs mehrfach wechseln; Scrollposition prüfen. Einen längeren Feldwert
   eingeben und Profil wechseln: Breiten müssen einmal passend aktualisiert
   werden. EXE-Pfad über Browse auswählen, Apply Now, erneut öffnen.
4. Hover bei spielendem und bereits pausiertem Video mit Native und asbplayer
   prüfen. Kurz hinein/hinaus bewegen; manuell pausieren, aufnehmen oder Tab
   wechseln: kein verspätetes automatisches Play.
5. Screenshot mit 0, 500 und 1500 ms testen, außerdem einen Wert jenseits des
   kurzen Clips. Audio-Padding soll den Bildzeitpunkt nicht verschieben.
6. Neues Wort hinzufügen: Farbe sofort sichtbar. Fehlgeschlagene Notizerstellung
   darf keinen neuen dauerhaften Lernstatus hinterlassen.
7. Native-Aufnahme: Häkchen nach gesicherter Aufnahme, auch wenn Anki noch
   arbeitet. Kontrollieren, dass die Zielnotiz später Audio und Bild enthält.
8. X1, X2 und Shift+V/Capture-only prüfen. Capture desselben Satzes muss frische
   Medien liefern; Add ohne Aufnahme darf dessen vorhandene Medien übernehmen.

Alle Laufzeit- und Oberflächenpunkte oben bleiben bis zu diesem manuellen Check
unbestätigt. Die statische Prüfung ersetzt keinen Funktionstest.
