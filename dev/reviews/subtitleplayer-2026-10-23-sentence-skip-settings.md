# Ganze Sätze, Segment-Skip und schließbare Einstellungen

Lokale Änderungen; keine automatischen Tests, Browser-/Anki-Liveaktionen oder
Uploads. Nur statische Code- und Syntaxprüfung. App-Neustart nötig; keine
Änderung an der Erweiterung und keine Neuladung dafür erforderlich.

## Satzbereich

- Beim Einfrieren einer Anki-Auswahl wird ein separater Satzkontext bestimmt.
  Der ursprüngliche CueSnapshot und dessen Auswahlspannen bleiben für Lookup,
  Wortformen, Markierung und Aufnahme-Startanzeige erhalten.
- Ganze SRT-Einträge werden bis zum nächsten bestätigten Satzende verbunden.
  Rückwärts wird nach dem vorherigen Satzende gesucht. `. ? ! 。？！．` zählen,
  auch vor schließenden Anführungszeichen/Klammern. Auslassungspunkte und
  gebräuchliche lateinische Abkürzungen sind keine verlässliche Grenze.
- Mehrere Sätze innerhalb eines Eintrags bleiben wie gewünscht zusammen.
  Ohne sichere Grenzen bleibt der ausgewählte Eintrag erhalten. Zur Vermeidung
  unkontrollierter Episodenaufnahmen gelten maximal 12 Einträge, 30 Sekunden
  Gesamtspanne und 3 Sekunden Lücke zwischen benachbarten Einträgen.
- Satztext, vorhandene Ruby-Lesungen, Satzübersetzung, exakter Medienvergleich
  und Gruppen-Zuordnung verwenden denselben eingefrorenen Kontext. Zeilenumbrüche
  bleiben erhalten. Keine SRT-Datei oder Originalzeit wird verändert.
- Native nimmt vom gemappten Beginn des ersten bis zum Ende des letzten
  beteiligten Eintrags auf, zuzüglich des vorhandenen Audio-Paddings. Alle
  beteiligten Einträge müssen sicher ausgerichtet sein. Rückwärts laufende
  Timing-Sprünge werden für einen zusammenhängenden Clip abgelehnt.
- Screenshot-Millisekunden und -Brüche beziehen sich auf den ganzen Satzbereich
  ohne Audio-Padding. Capture-only behält den Kontext seiner ursprünglichen Karte.
- Der alte asbplayer-Hotkeyweg behält seine extern bestimmten Clipgrenzen;
  dessen Audio wird nicht als vollständiger korrigierter App-Satz ausgegeben.
  Kartentexte erhalten auch dort den vollständigen Satzkontext.
- Das temporäre Wörterbuch-Definitionsfeld ist pro Vorbereitungsthread isoliert,
  damit parallel vorbereitete Karten keine Definitionen anderer Wörter übernehmen.
  Bestehende Anki-Notizen werden nicht nachträglich geändert.

## Einstellungen und Skip

- Ursache des Schließproblems: gespeicherte Pause-Aliase enthielten `stop`, das
  nun gleichzeitig als Aufnahmeabbruch ergänzt wurde. Bei aktiviertem Abbruch
  gehören `stop/stopp` eindeutig dazu; Pause behält seine anderen Aliase.
  Weckwörter und `stop listening` bleiben unverändert.
- Unveränderte bereits geladene Alias-Konflikte werden im Voice-Tab angezeigt,
  blockieren aber nicht mehr das Speichern anderer Einstellungen oder Schließen.
  Neue ungültige Alias-Änderungen bleiben blockiert. Fehler beim Schließen sind
  im Fenster und Log sichtbar; Voice-Fehler wählen den Voice-Tab aus.
- Unter **Advanced → General → Playback / Overlay → Back/forward buttons**
  gibt es `Skip seconds` (Standard) und `Next / previous subtitle start`.
  Neuer Profilwert: `SKIP_NAVIGATION_MODE = time | segments`.
- Segmentmodus betrifft die normalen Vor/Zurück-Buttons und ihre Hotkeys.
  Zurück springt bei laufendem Segment zum Beginn des tatsächlich vorherigen
  Segments; in einer Lücke zum letzten vorhergehenden Segment. Vor springt zum
  nächsten Beginn. Am Dateirand entsteht kein ungültiger Seek.
- Wiederholte Aktionen berücksichtigen das bereits angeforderte Seek-Ziel,
  statt vor der Browserbestätigung denselben Sprung mehrfach zu senden.
  Explizite Sprach-Zeitsprünge verwenden weiterhin den Skip-Sekundenwert.

## Manuell prüfen

1. Satz über zwei/drei Einträge, letztes Ende mit Punkt: Wort im mittleren
   Eintrag capturen; kompletter Satz in JA-/Übersetzungsfeldern und im Audio.
   Add eines weiteren Wortes aus diesem Satz teilt den frischen Clip.
2. Mehrere Sätze in einem Eintrag bleiben gemeinsam. Datei ohne zuverlässige
   Satzzeichen sowie lange Lücken erweitern die Aufnahme nicht unkontrolliert.
3. Satzbereich mit Timing-Wechsel, manueller Feinkorrektur, Padding und
   Screenshot `1/2` prüfen. Unzuverlässig gemappte Teile müssen Aufnahme ablehnen.
4. Advanced öffnen, ohne Änderungen schließen; General-Einstellung ändern,
   schließen und erneut öffnen. Neue absichtliche Alias-Kollision wird weiterhin
   angezeigt und verhindert das Speichern bis zur Korrektur.
5. Beide Skip-Modi mit Buttons und gehaltenen Hotkeys prüfen: schnell mehrfach,
   pausiert, mitten im Segment, in Lücken und an Dateirändern. Keine Doppel-Seeks.
