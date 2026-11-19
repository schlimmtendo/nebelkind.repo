# 🌫️ Nebelkind Repo

Offizielles Kodi-Repository für **Nebelkind Hybrid**, **NebelFlix TV** und **Nebelkind Musik**.

**Der NebelFlix-Skin (`skin.nebelflix`) wird hier nicht mehr angeboten.** Er ist aus der aktuellen Kodi-Addon-Liste und den Downloads entfernt. Bereits lokal installierte Skins werden dadurch nicht deinstalliert.

## Verbleibende Add-ons

| Add-on | Version |
| --- | --- |
| `plugin.video.nebelflix.hybrid` – Nebelkind Hybrid | 2026.11.19.1 |
| `plugin.video.nebelflix.tv` – NebelFlix TV | 0.2.4 |
| `plugin.video.nebelkindmusic` – Nebelkind Musik | 5.0.1 |

Der Repository-Installer `repository.nebelkind` bleibt erhalten. Diese drei Add-ons haben keine Pflicht-Abhängigkeit vom Skin. Externe Kodi-Abhängigkeiten können weiterhin erforderlich sein.

## Installation

- Downloadseite: https://schlimmtendo.github.io/
- Repo-Installer: `packages/repository.nebelkind/repository.nebelkind-1.0.0.zip`
- Kodi-Katalog: `packages/addons.xml` und `packages/addons.xml.md5`
- Versionsübersicht: `packages/release.json`

Bei Veröffentlichungen werden nur die drei unterstützten Add-ons indexiert. Paketimport und ZIP-Build akzeptieren keine neuen Skin-Pakete mehr.

**Veröffentlichungen nur nach ausdrücklicher Freigabe des Repository-Inhabers.** Änderungen können zuerst lokal oder in einem Testbranch vorbereitet werden. Nur `main` veröffentlicht die Live-Repository-Daten.

Persönliche Kodi-Einstellungen, Zugangsdaten und private Dateien gehören nicht in dieses öffentliche Repo. Das frühere Skin-Paket bleibt in älteren Git-Commits der Versionshistorie auffindbar, wird aber vom aktuellen Repository nicht mehr ausgeliefert.


## Revision vom 19.11.2026

Hybrid: Quellenlayout, Abbruch und begrenzte Worker. TV: Favoritenansicht, Kontextmenü, Guide-Rückkehr und kleinere Senderseiten. Musik: unveränderte Funktionen mit höherer Wartungsversion für den Revisions-Updater-Test. Der Skin bleibt separat.
