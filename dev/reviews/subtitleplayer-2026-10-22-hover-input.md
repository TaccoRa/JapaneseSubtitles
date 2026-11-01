# Hover, Hotkeys und ruhige Untertitelwechsel

Lokaler Stand auf `feature/browser-sync-native-capture`, Erweiterung **0.3.5**.
Keine automatischen Tests, Browser-/Anki-Liveaktionen, App-Neustarts oder Uploads
durchgeführt. Nur Code- und Syntaxprüfung. Alle folgenden Laufzeitpunkte sind
noch durch den Benutzer zu prüfen.

## Änderungen

- Advanced Settings speichert beim Schließen über Close, X oder Zahnrad.
  Ungültige Werte halten das Fenster offen; das erste betreffende Feld wird
  ausgewählt. Unveränderte Werte werden nicht erneut angewandt oder geschrieben.
  Der asbplayer-Unterdialog übernimmt seine Änderungen weiterhin nur mit OK.
- Hover pausiert beim Eintritt, unabhängig vom Recorder. Nur Austritte werden
  kurz zusammengefasst. Eine veraltete Clock wird mit PlaybackProbe aktualisiert.
  Nur die eigene, bestätigte Hover-Pause darf wieder fortgesetzt werden.
- Die eingefrorene Aufnahmezeile bleibt beim Zurückspulen und Start-Padding
  sichtbar. Ab ihrem gemappten Beginn läuft die Anzeige normal weiter, auch
  während des End-Paddings. Clock und Slider zeigen immer die Videoposition.
  Abbruch, Fehler, manuelles Spulen und Quellenwechsel lösen die operationseigene
  Sperre; alte Ergebnisse können die Sperre einer neuen Aufnahme nicht lösen.
- Set to, Offset einschließlich grauem ↻-Button und Skip teilen gleich große
  Eingabebereiche. Offset ändert weiterhin nur die Feinkorrektur, nicht das Video.
- `CAPTURE_SCREENSHOT_POSITION` akzeptiert `500`, `500 ms`, `1/2` oder `1/3`.
  Brüche beziehen sich auf die gemappte Untertiteldauer ohne Padding. Position
  und konkreter Bildzeitpunkt werden mit der Aufnahme eingefroren und auf den
  Clip begrenzt. Alte Millisekundenwerte werden übernommen und archiviert.
  Bei asbplayer bleibt das Feld deaktiviert.
- Nur ein akzeptierter Capture per Maushotkey bewegt die Maus einmal an den
  unteren Rand ihres aktuellen Monitors. Auswahl vorher einfrieren; X-Position
  erhalten. Add, Stimme und abgelehnte Aufnahme bewegen die Maus nicht.
- Physische Hotkeys gelten nur für SubtitlePlayer oder den aktiven verbundenen
  Videotab im fokussierten Chrome-Fenster. Eingabefelder, Adressleiste, andere
  Tabs und Programme bleiben ausgenommen. Wartende Aktionen werden erneut
  geprüft; Modifier und Tastenfreigaben werden auch außerhalb verarbeitet.
  Die Sprachsteuerung behält ihren eigenen Weg.
- Die nächsten drei Cues und der aktuelle Cue werden von einem einzelnen
  Hintergrund-Worker für Ruby vorbereitet. Nicht vorbereitete Cues werden erst
  mit fertigen Lesungen eingeblendet; nach großen Sprüngen kann daher kurz eine
  leere Anzeige entstehen. Geometrie und fertiger Text werden gemeinsam ohne
  Zwischenzeichnung des alten Inhalts aktualisiert.

## Bestandsliste

- **Weiter benötigt:** Browserbindung und Bestätigungen, asbplayer-Adapter,
  eingefrorene Auswahl/Notiz-ID, frischer Medienvergleich und Wiederherstellung.
  X1 bleibt Capture, X2 Add über Untertiteln beziehungsweise Sichtbarkeit
  außerhalb. Capture-only benötigt eine bekannte SubtitlePlayer-Zielnotiz.
- **Ersetzt/entfernbar:** vorläufige Ruby-Anzeige mit späterer Größenkorrektur,
  unbeschränkte globale Hotkeys und mehrfacher Tab-Layoutaufbau. Verbliebene
  OCR-/Simulations-Helfer sind weiterhin getrennte Aufräumkandidaten; der
  asbplayer-Fensterweg wird bei fehlender Browserverbindung noch gebraucht.
- **Konkrete Codefehler bearbeitet:** verzögerte Ruby-Ergänzung und Zeichnung
  beim Resize, fehlender sofortiger Hover-Eintritt, verlorene Hover-Anforderung
  bei alter Clock, alte Seek-Vorschau nach Fokuswechsel und fehlendes Speichern
  beim Schließen. Eine Laufzeitbestätigung steht noch aus.
- **Live-Prüfung offen:** beide Recorder, Hover, X1/X2, Capture-only,
  benutzerdefinierte Shortcuts, Wiederverwendung nur bei Add, frische Capture-
  Medien und Wiedergabe nach Aufnahmen. Bestehende Notizen und SRTs unverändert.

## Kurze manuelle Prüfliste

1. App neu starten, Erweiterung auf **0.3.5** neu laden und Videotab aktualisieren.
   Falls nötig Popup einmal zur Wiederherstellung der Tab-Audioverbindung öffnen.
2. Hauptfensterhöhe, gleiche Timing-Feldgrößen und grauen ↻-Button prüfen.
   In Advanced einen Wert ändern und über X schließen; erneut öffnen.
   Mit ungültigem Screenshotwert muss das Fenster offen bleiben.
3. Hover mit Native und asbplayer: spielend, bereits pausiert, schnelle
   Mauswechsel sowie manuelle Pause/Aufnahme/Quellenwechsel. Kein spätes Play.
4. Hotkeys in SubtitlePlayer und Videotab prüfen; danach in VS Code,
   anderem Chrome-Tab, Adressleiste und Eingabefeld tippen. Keine App-Aktion.
   Eine gehaltene Sprungtaste beim Fokuswechsel loslassen: kein Nachholsprung.
5. Screenshot mit `500 ms`, `1/2`, `1/3` und deutlichem Audio-Padding prüfen.
   X1 muss nach akzeptierter Aufnahme die Maus am selben Monitor nach unten
   bewegen. X2/Add und Sprach-Capture dürfen sie nicht bewegen.
6. Deutliches Start- und End-Padding: kein vorheriger Satz während des Starts;
   ab Cue-Beginn normale Anzeige. Abbruch und direkt folgende Aufnahme prüfen.
7. Mehrere Cue-Wechsel mit Ruby sowie einen weiten Seek prüfen: kein sichtbares
   Nachrücken der fertigen Lesungen. Sofortige Wortfarbe und frühes Aufnahme-
   häkchen prüfen; danach getrennt Audio/Bild in der Zielnotiz kontrollieren.
