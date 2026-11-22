# Nebelkind Repo

Kodi-Repository fuer Nebelkind Hybrid (2026.11.22.1), NebelFlix TV (0.2.5),
Nebelkind Musik (5.1.2) und Nebelflix Skin (0.5.81).

Der Skin ist wieder im Kodi-Katalog und im Revisions Updater enthalten.
Skin-Updates ersetzen ausschliesslich die ausgelieferten Dateien unter
addons/skin.nebelflix. Einstellungen, Widgets, bereits geladene Inhalte,
Favoriten und Verlauf unter userdata bleiben erhalten. Eine abgeschlossene
Widget-Vorbereitung wird durch eine normale Skin-Revision nicht zurueckgesetzt.
Kodi laedt ein vollstaendiges Addon-ZIP; eine Neueinrichtung ist nicht erforderlich.

0.5.74: getrennte Hub-Zustaende, begrenzte Widget-Starts, kleinere Kachelbilder,
Katalog-Metadaten mit kurzem Abruf, Ladezeiten-Diagnose und korrigiertes
Settings-Layout: Ladezeiten & Diagnose steht rechts neben Hybrid einstellen.

Installation: repository.nebelkind installieren, dann unter Look and Feel / Skin
Nebelflix waehlen. Fuer Filme und Serien wird Hybrid zur Laufzeit benoetigt;
die Addons bleiben ohne zyklische Skin-Abhaengigkeiten installierbar.

Veröffentlichungen nur nach ausdrücklicher Freigabe des Inhabers.
Persönliche Kodi-Profile und Zugangsdaten werden nicht veröffentlicht.

## Revision vom 22.11.2026

TV ist der Widget- und Senderadapter fuer Vavoo TV Plus 2.0.11. Der EPG-Einstieg oeffnet Vavoos `favorites_epg`; der alte eigene Guide wurde entfernt. Vorhandene Start-Fingerabdruecke werden gelesen. Erst eine echte Aenderung gegenueber der Vergleichsbasis plant die automatische Favoritenpflege (quick, danach defects). Ein Worker, begrenzte Wiederholungen und Pause waehrend Wiedergabe/Modalfenstern vermeiden parallele Reparaturschleifen. Vavoos Programmdateien werden nicht gepatcht; seine installierte Synchronisationslogik verarbeitet die Favoritendaten mit Sicherungen.

Hybrid beendet fehlgeschlagene Wiedergabe-Waechter spaetestens nach 30 Sekunden und startet dann keine Folgenvorbereitung. Manuelle Quellenwahl probiert nur die ausgewaehlte Quelle. Autoplay hat hoechstens drei zeitlich begrenzte Kandidaten. Laufende Drittanbieter-Netzwerkanfragen koennen nicht gewaltsam beendet werden; verspaetete Ergebnisse nach Abbruch werden verworfen.

Skin: Spotlight ist pro Nutzer abschaltbar, Widget-Fokus bleibt bei Cache-Aktualisierungen und beim Schliessen von Details erhalten. Auf Fire TV weniger eager Hub-Arbeit und kleinere Kachelbilder. Erstvorbereitung kuendigt einen Neustart an; Windows/Linux automatisch, Android beendet Kodi zum manuellen Wiederoeffnen.

Musik: 240 Playlists und einheitliche Genre-Icons. Oeffentliche Titellisten vermeiden die im Log fehlgeschlagene Playlist-Data-API-Abfrage; die Video-Wiedergabe erfolgt weiter ueber das YouTube-Addon. Bis zu 100 Titel je Queue, vollstaendige Playlist im Kontextmenue.

Gezielte lokale Tests, Paketintegritaet und Repository-Konsistenz wurden geprueft. Keine Garantie fuer die Beseitigung aller Fire-TV-Freezes oder die Erreichbarkeit jeder externen Quelle; Praxistests auf dem Geraet bleiben erforderlich.
