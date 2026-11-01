# Wortanalyse an vorhandenen SRT-Dateien

Die Prüfung hat Fehler in der Anwendung der Wortanalyse gefunden und behoben.
Besonders betroffen waren abweichende Suchbegriffe zwischen Hover und Karte,
überbreite Zusammensetzungen, echte Kana, die als eingebettete Lesung entfernt
wurden, sowie Zeit- und Datumslesungen.

## Umfang und Aussagekraft

- Alle 284 SRT-Dateien wurden eingelesen: insgesamt 99.663 Untertitelblöcke,
  darunter 2.681 Blöcke mit geklammerten Quelllesungen, 4.691 mit ASS-Tags und
  33.466 mit mehreren Zeilen.
- Vollständig durch die Analyse liefen 20 japanische SRT-Dateien aus Dr. Stone,
  Elfen Lied, Farming Life in Another World, Hunter x Hunter, One Piece,
  Record of Ragnarok, Parasyte und dem Film Terraformars. Die Auswahl umfasst
  frühe, mittlere und späte vorhandene Dateien sowie zwei One-Piece-Ordner.
- Zusätzlich wurde eine italienische Harry-Potter-SRT als Sprachkontrolle
  eingelesen und von der japanischen Analyse ausgeschlossen.
- Diese 21 Dateien enthalten 8.893 Blöcke; davon wurden 7.248 bereinigte,
  japanische Blöcke tatsächlich analysiert. Die Analyse erzeugte etwa 45.000
  Token- und überlappende Wortkandidaten, keine ebenso große Zahl unabhängiger Wörter.
- Verwendet wurden die echten lokalen Analyseengines, die normale
  SRT-Bereinigung, Cue-Snapshots und eine lesende Momentaufnahme der vorhandenen
  Wortdatenbank. Positions- und Konsistenzprüfungen liefen automatisch;
  ausgewählte auffällige Beispiele wurden zusätzlich inhaltlich geprüft.
  Das ist keine manuelle sprachliche Abnahme jedes Untertitels.

## Behobene Fehler und konkrete Beispiele

| Fundstelle | Vorher | Korrigiertes Verhalten |
| --- | --- | --- |
| Dr. Stone S01E01, Block 71 | `生き延びて` wurde zu `生延びて` | Echte Okurigana bleiben erhalten. |
| Dr. Stone S01E12, Block 145 | `聞きやがれ` verlor das zweite `き` | Die lexikalische Wortgrenze schützt die Verbendung. |
| One Piece S19E440, Block 176 | `何か食べて` wurde zu `何食べて` | Mehrdeutige Kana-Partikeln werden nicht als Lesung gelöscht. |
| Hunter x Hunter S05E101, Block 332 | `ゲーム名なんて` verlor das `な` | Grammatische Kana-Gruppen bleiben erhalten; die drei Hunter-Dateien wurden danach vollständig erneut geprüft. |
| Dr. Stone, mehrere Folgen | `説明`, `蒸留`, `中和` hatten auf der Karte eine andere Form als im Popup | Die Kontextform wie `説明する` wird einmal im Tokenizer bestimmt und von allen Verbrauchern verwendet. |
| Dr. Stone und One Piece | Partikeln wurden zu `にする`, `とする` oder `でする` | Die zusätzliche, ungeprüfte Suru-Erweiterung bei der Kartenerstellung entfällt. |
| Mehrere Dateien | Mengen oder Operatoren konnten Teil eines vermeintlichen Wortes werden | Grenzen wie `一度` + `失敗` und `硝酸` + `プラス` + `エタノール` bleiben getrennt. Suffixe beginnen keine selbstständigen Zusammensetzungen. |
| Hunter x Hunter S05E125, Block 95 | `10分` erhielt `じゅうふん` | `じゅっぷん`; weitere Lautwechsel und zusammengesetzte Zahlen wurden abgesichert. |
| One Piece S19E440, Block 164 | `８時間` wurde in `８時` und `間` aufgeteilt | Die vollständige Einheit erhält `はちじかん`. |
| Record of Ragnarok S02E01, Block 16 | Bei `４回戦` konnte `戦` die Einzelzeichenlesung erhalten | Die Einheit erhält `よんかいせん`. |
| Dr. Stone S01E01, Block 159 | Die Datumsteile `10月５日` wurden einzeln und falsch gelesen | Die Datumseinheit erhält `じゅうがついつか`; kalendarisches `1日` wird im Monatskontext als `ついたち` gelesen. |
| Farming Life, alle 11 vorhandenen Folgen | Stehengebliebene, teils doppelt maskierte `\N`-Sequenzen erzeugten Wortkandidaten wie `Nお昼` | Zeilen-/Abstandsescapes werden vor der Analyse aufgelöst. |

Die Ausrichtung generierter Ruby-Segmente übernimmt jetzt die Originalzeichen
und deren Abstände. Künstliche Trennzeichen des Backends verschieben dadurch
keine Quellpositionen mehr. Bekannte Bestandteile dürfen weiterhin kein größeres,
unbekanntes Wort einfärben. Umgekehrt darf eine reine Ruby-Anzeigegruppe mehrere
eigenständige Wörter nicht zu einem Datenbankwort erklären. Das wurde zusätzlich
an `三人` + `全員` sowie an der Kana-Zähleinheit `いくつ` geprüft.

Die Kontextauswertung berücksichtigt die UniDic-Kategorie `サ変可能`; die
Einordnung von Kurzsegmenten als mögliche Verbstämme ist in der
[UniDic-Dokumentation des NINJAL](https://clrd.ninjal.ac.jp/unidic/glossary.html)
beschrieben. Die korrigierte Lesung von `年間` ist im
[NINJAL-Wörterbuch](https://mmsrv.ninjal.ac.jp/kamus/data/item2358.html) belegt.
Die Datumsformen wurden mit den
[Irodori-Lernmaterialien der Japan Foundation](https://www.irodori.jpf.go.jp/assets/data/starter/pdf/X_L12.pdf)
abgeglichen.

## Ergebnisse und verbleibende Grenzen

In der anfänglichen, gleichmäßig verteilten Stichprobe von 1.887 japanischen
Blöcken gab es 151 Abweichungen zwischen dem gemeinsamen Suchbegriff und der
Kartenform. Nach der Korrektur gab es dort keine mehr. Auch der vollständige
Durchlauf zeigte keine Abweichungen der Tokenpositionen, keine Textänderungen
durch Annotationen und keine abweichenden Kartenformen für Zusammensetzungen.

Der vollständige Lauf markierte 373 sichtbare Textänderungen zur Nachsicht:
372 liegen in den beiden Elfen-Lied-Dateien mit unmarkierten, in den Fließtext
eingestreuten Lesungen. Die zusätzliche Hunter-Änderung war ein echter Fehler
und wurde anschließend korrigiert. Die erneute Vollprüfung der drei
Hunter-Dateien (811 japanische Blöcke) ist ohne Textänderung oder
Suchbegriffsabweichung durchgelaufen.

Die Elfen-Lied-Quellen enthalten außerdem beschädigte Schreibungen wie
`非常事態警警鈴` und `２メートル以上離てれは`. Solche Quellfehler und mehrdeutige
unmarkierte Lesungen lassen sich ohne verlässliche Referenz nicht sicher
rekonstruieren. Lange Fachbegriffe, Namen und kontextabhängige Lesungen bleiben
sprachlich mehrdeutig; eine ausdrückliche vollständige Markierung bleibt daher
maßgeblich für den Kartentext.

389 relevante Tests bestehen. Sie decken echte Analyseengines, Wortauswahl,
Popup-/Kartenfelder, Statusmarkierungen, Ruby-Anzeige, SRT-Aufbereitung,
Aufnahmebereiche und Wortdatenbank-/Anki-Synchronisierung ab. Vier ältere
SRT-Testaufbauten wurden an den vorhandenen Ladeablauf angepasst, unter anderem
mit echten temporären Dateien statt nicht vorhandener Dummy-Pfade. Zwei
bestehende `utcnow()`-Deprecation-Warnungen bleiben. Compile- und
Whitespace-Prüfungen bestehen. Es wurden keine echten Anki-Karten angelegt.

## Reproduzierbare Prüfungen

Werkzeug: [audit_word_analysis.py](../audit_word_analysis.py).

```powershell
& '.\.venv\Scripts\python.exe' dev/audit_word_analysis.py --output dev/reviews/word-analysis-audit.json
```

Maschinenberichte:

- [Bestandsaufnahme](word-analysis-corpus-inventory.json)
- [Anfängliche Stichprobe](word-analysis-2026-10-30-before.json)
- [Vergleichsstichprobe](word-analysis-2026-10-30-after.json)
- [Vollständiger Durchlauf](word-analysis-2026-10-30-full.json)
- [Hunter-Nachprüfung nach der letzten Kana-Korrektur](word-analysis-2026-10-30-hunter-recheck.json)

Zum Aktivieren der Änderungen die Desktop-App neu starten.
