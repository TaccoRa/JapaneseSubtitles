# Hover-Pause beim Austritt über ein Tooltip

Untertitelfenster und sichtbare angehängte Hover-Tooltips gelten als gemeinsamer
Hover-Bereich. Der Renderer meldet Tooltip-Eintritt, Austritt, Ausblenden und
Zerstören an den bestehenden Pause-Besitzer. Ein auf die Hover-Zeit begrenzter
75-ms-Pointercheck deckt fehlende Leave-Ereignisse ab; Austritte bleiben 40 ms
zusammengefasst. Versteckte Fenster werden nicht anhand alter Geometrien als
Hover-Ziele behandelt.

Eine veraltete Browser-Clock beim Austritt verwirft die Pause-Zuständigkeit
nicht sofort: PlaybackProbe und kurze Wiederholungen aktualisieren sie maximal
zwei Sekunden lang. Identität, Eingriffsgeneration und Aufnahmezustand werden
vor Play weiterhin geprüft. Manuelle Eingriffe, Quellenwechsel und Aufnahmen
dürfen kein verspätetes Fortsetzen verursachen. Der getrennte asbplayer-Weg
ohne Verbindung verwendet dieselbe Hover-Bereichserkennung.

Nur statische Prüfung, keine automatischen Tests oder Liveaktionen. Lokal;
App-Neustart erforderlich, keine Erweiterungs-Neuladung für diese Änderung.

Manuell prüfen:

1. Spielendes Video: Untertitel → Wörterbuch-Tooltip → außerhalb. Im Tooltip
   bleibt es pausiert, nach Verlassen läuft es weiter.
2. Tooltip → Untertitel → Tooltip rasch wechseln; kein Zwischen-Play.
3. Zuvor pausiertes Video sowie manuelle Pause, Capture und Quellenwechsel:
   kein unerwünschtes automatisches Play beim Verlassen.
