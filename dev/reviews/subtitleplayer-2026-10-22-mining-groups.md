# Aufnahmegruppen, Hotkeys und Wortmarkierung

Lokal auf `feature/browser-sync-native-capture`, Erweiterung **0.3.6**.
Nur statische Code-/Syntaxprüfung; keine automatischen Tests, App-Neustarts,
Browser-/Anki-Liveaktionen oder Uploads. Laufzeitverhalten ist unbestätigt.

## Verhalten

- Offset übernimmt die Rahmenattribute von Set to. ↻ bleibt grau innerhalb der
  gemeinsamen Außenbreite; Fokusdarstellung wird am inneren Eingabefeld verfolgt.
- Maus-Hotkeys speichern Bildschirmposition und Videoidentität beim Tastendruck.
  Die UI bestimmt daraus die Wortauswahl. Ein zulässiger kurzer Fokusübergang
  zwischen verbundenem Chrome und App darf die Aktion 250 ms lang erhalten.
  Andere Programme, Tabs und editierbare Felder bleiben ausgeschlossen.
- X1/Capture und X2/Add funktionieren auch über dem geöffneten Auswahl-Popup.
  Dort wird dessen markierter Text verwendet. X2 außerhalb von Untertitel und
  Popup behält die bisherige Sichtbarkeitsaktion.
- X1 und Popup-Capture per konfigurierter Taste (z. B. `n`) bewegen die Maus nach
  akzeptierter Aktion an den unteren Rand desselben Monitors. Stimme, Add und
  abgelehnte Captures bewegen sie nicht.
- Capture-only erhält **z zusätzlich zu Shift+V**. Die einmalige Migration
  ergänzt auch bestehende Profilbindungen und archiviert deren vorherigen Wert;
  die separate Deaktivierungsoption bleibt erhalten.
- Während eines Clips werden zusätzliche Wörter desselben Satzes als eigene
  Kartenaufträge angenommen. Add und weiteres Capture teilen diesen Clip.
  Add eines anderen Satzes wartet auf das Ende und verwendet dessen eigene
  Satzmedien. Capture für einen anderen Satz und andere Wiedergabe-/Shortcut-
  Aktionen werden verworfen. Das gilt auch für Sprachaktionen und Hover.
- Esc beziehungsweise Abbrechen/Abort/Cancel/Stop brechen den aktiven Vorgang
  ab. Bestehende Weckwort- und Aktivierungseinstellungen bleiben erhalten;
  Stop listening bleibt eine andere Aktion.
- Native bereitet Inhalte im Hintergrund vor, erstellt Notizen aber erst nach
  gesichertem Audio und Bild. Abbruch während des Clips verwirft zugehörige
  Aufträge und deren vorläufige Farben. Bereits anderweitig erfolgreiche
  Wortmarkierungen werden nicht zurückgesetzt.
- Kartenaufträge werden in Annahmereihenfolge an die vorhandene Anki-Warteschlange
  übergeben. Medien haben explizite Notiz- und Vorgangs-IDs. Zusätzliche Karten
  verwenden dieselben frischen Dateien, keine ältere Satzaufnahme. Während
  Encoding/Anki-Nacharbeit kann die nächste Aufnahme beginnen.
- Der alte asbplayer-Weg erstellt weiterhin zuerst seine eindeutige Zielnotiz.
  Zusätzliche Karten warten auf verifizierte frische Medien. Solange asbplayer
  aufnimmt, werden keine weiteren Gruppenkarten angelegt, die sein Ziel der
  zuletzt erstellten Karte verändern könnten. Abbruch löscht ausschließlich die
  neu angelegte Vorgangsnotiz; bestehende Capture-only-Ziele werden nie gelöscht.
  Löschfehler werden mit Notiz-ID gemeldet. Eine bereits eigenständig laufende
  externe asbplayer-Aufnahme besitzt über diesen alten Hotkey-Adapter keinen
  bestätigten Stop-Befehl; bereits von asbplayer geschriebene Änderungen an
  bestehenden Zielnotizen werden nicht blind rückgängig gemacht.
- Native Screenshots blenden erkannte native Controls sowie Controls von
  Netflix, Video.js, Plyr, JW, Artplayer, DPlayer, Vidstack, Shaka und Fluidplayer
  für den Screenshot aus. Caption-Container bleiben erhalten. Attribute und
  Styles werden pro Vorgang wiederhergestellt, auch bei überlappender Nacharbeit
  und Fehlern. Unbekannte Controls und nicht bestätigte Frame-Neuzeichnungen
  bleiben ausdrücklich als mögliche Einschränkung im Log; kein Abschneiden
  des Bilds und keine Pause. asbplayer behält seine eigene Bildaufnahme.
- Mehrkanjige lexikalische Wörter erben keine Markierung einzelner Ruby-Kanji.
  Ganze Wörter/zulässige Grundformen und eigenständige Einzelkanji bleiben
  markierbar. Einzelkanji-Informationen bleiben im Lookup verfügbar.
- AnkiConnect-Dateiübertragungen bekommen mindestens 30 s Antwortzeit statt
  4 s. Verbindungsaufbau bleibt begrenzt, Notizerstellung wird weiterhin nicht
  blind wiederholt. Ein unresponsive Anki kann auch dieses Limit überschreiten.

## Manuelle Prüfung durch den Benutzer

1. App neu starten, Erweiterung auf **0.3.6** neu laden, Videoseite aktualisieren;
   falls nötig Popup einmal für die Tab-Audioverbindung öffnen.
2. Offset-Rahmen und Fokus mit Set to/Skip vergleichen. X1 und X2 beim ersten
   Klick über einem Wort prüfen, während Chrome beziehungsweise App fokussiert
   ist; anschließend auch eine markierte Auswahl im Popup prüfen.
3. Ein Wort capturen, ein weiteres desselben Satzes während des Clips per Add
   hinzufügen, danach per Capture ein drittes: ein Clip, getrennte Karten mit
   denselben frischen Medien. Add per Stimme und indexiertes Add mitprüfen.
4. Währenddessen Pause/Skip/andere Hotkeys drücken: kein Unterbrechen und kein
   Nachholen. Add eines anderen Satzes muss warten und andere Medien verwenden.
5. Esc und Sprachabbruch separat prüfen: bei Native keine erstellten Karten,
   vorläufige Farben zurückgesetzt. Bei asbplayer nur die neue Vorgangsnotiz
   entfernt; Capture-only-Ziel bleibt erhalten. Erst isolierte Testnotizen nutzen.
6. Direkt nach Clipende erneut capturen, während Anki noch arbeitet. Späte
   Rückmeldungen dürfen weder spulen noch Wiedergabe oder neue Auswahl ändern.
7. `n` im Popup: Capture plus Mausbewegung. `z` und Shift+V: Capture-only für
   eine bekannte Zielnotiz. In VS Code/Adressleiste/Eingabefeldern keine Aktion.
8. Website-Untertitel und vorab sichtbare Videocontrols einschalten, nativen
   Screenshot prüfen; danach müssen die Controls normal funktionieren. Bei
   unbekanntem Player sichtbare Restcontrols und Loghinweise gemeinsam prüfen.
9. Nur 流 und 行 als Einzelkanji bekannt: 流行 darf dadurch nicht markiert sein.
   Nach Hinzufügen von 流行 muss das Wort markiert werden. Einzeln auftretende
   bekannte Kanji und flektierte bekannte Wörter mitprüfen.
10. Die gemeldete verzögerte Anki-Dateiübertragung erneut beobachten: bei weiterem
    Timeout den neuen Logabschnitt aufbewahren; das Zeitlimit allein erklärt
    nicht, warum Anki zeitweise keine Antwort liefert.
