# 🌫️ Nebelkind Repo

**Die offizielle Kodi-Repository für meinen NebelFlix-Skin und die zugehörigen Nebelkind-Addons.** Keine fremden Builds, keine BuildGuard-Tools und keine TitanX-Lab-Sammelrepo.

## Enthaltene Addons

| Addon | Aktuelle Ausgangsversion |
| --- | --- |
| `skin.nebelflix` – NebelFlix Skin | 0.5.70 |
| `plugin.video.nebelflix.hybrid` – Filme & Serien | 2026.11.15.1 |
| `plugin.video.nebelflix.tv` – Live-TV/Programmführer | 0.2.0 |
| `plugin.video.nebelkindmusic` – Nebelkind Musik | 5.0.0 |

Die ZIP-Dateien werden unverändert aus den bereitgestellten Addon-Versionen übernommen. Externe Kodi-Abhängigkeiten müssen bei Bedarf separat installiert werden (z. B. Vavoo TV Plus für NebelFlix TV oder Multitrailer für den Skin).

## 🌐 Website und Download

Nach Aktivierung von GitHub Pages: **https://schlimmtendo.github.io/nebelkind.repo/**

Dort wird die installierbare `repository.nebelkind-1.0.0.zip` direkt heruntergeladen. Kodi kann danach die veröffentlichten Nebelkind-Addons aus dem Repository installieren und Updates beziehen.

## 🔒 NebelFlix Skin als Voraussetzung

Der Skin `skin.nebelflix` lässt sich eigenständig installieren. **Hybrid, Live-TV und Musik** enthalten dagegen eine Pflicht-Abhängigkeit auf `skin.nebelflix >= 0.5.70`. Der Skin muss installiert, aber nicht aktiv sein.

**Wichtige Kodi-Einschränkung:** Kodi darf eine fehlende Skin-Abhängigkeit beim Installieren eines der drei Addons automatisch mitinstallieren. Die Addons sind dann nur *zusammen mit installiertem Skin* ausführbar – das ist keine Zugangskontrolle, kein DRM und keine unsichtbare Repository-Liste. Öffentliche ZIP-Dateien können außerdem kopiert werden.

Die GitHub-Veröffentlichungsautomatik ergänzt bzw. prüft diese Abhängigkeit bei jedem Paket und vermeidet den früheren Abhängigkeits-Zirkel (Skin → Hybrid → Skin). Im Skin ist Hybrid nur noch eine optionale Abhängigkeit.

### Vier Addons mit einer einzigen ZIP hochladen

Die vorbereitete Datei **`nebelkind-paket.zip`** enthält genau vier einzelne Kodi-Addon-ZIPs. Diese Datei gehört **unentpackt in das Hauptverzeichnis des Branches `main`**: auf GitHub **Add file → Upload files → Commit changes**.

GitHub Actions führt dann automatisch die vier Schritte aus:

1. Vier Addon-ZIPs prüfen und nach `packages/<addon-id>/` kopieren.
2. Fehlende Skin-Abhängigkeit ergänzen und einen Pflicht-Abhängigkeits-Zirkel ausschließen.
3. `packages/addons.xml` und `addons.xml.md5` aktualisieren.
4. Die verarbeitete `nebelkind-paket.zip` aus dem Git-Stand entfernen.

Die Versionen der Addons bleiben beim Import unverändert. **Unveränderte Versionsnummern lösen in Kodi kein automatisches Update aus**; das ist für manuelle Testinstallationen vorgesehen. Für reguläre Updates die Versionsnummer im jeweiligen `addon.xml` erhöhen.

## So veröffentliche ich Updates – auch Teststände

1. **Jede fertige Test- oder Update-ZIP wird öffentlich in `main` abgelegt** unter `packages/<addon-id>/<addon-id>-<version>.zip`. Sie muss intern eine `addon.xml` mit exakt gleicher ID und Versionsnummer enthalten.
2. GitHub Actions prüft die ZIP und erstellt automatisch `packages/addons.xml` und `packages/addons.xml.md5` neu. Der Repo-Installer wird ebenfalls erzeugt.
3. **Teststand ohne neue Versionsnummer:** Ein neuer Benutzer kann diese Fassung installieren. Bereits installierte Kodi-Geräte erhalten **kein automatisches Update** bei unveränderter Versionsnummer; zum Testen dort die ZIP manuell installieren (ggf. Kodi-Paketcache beachten).
4. **Freigabe als reguläres Update:** Ich erhöhe die Versionsnummer in `addon.xml` und veröffentliche die passend benannte ZIP. Dann bietet Kodi das Update an (automatisch nur, wenn auf dem Gerät so eingestellt).

`testing` ist eine optionale, nicht von Kodi verwendete Branch-Kopie. **Veröffentlicht wird aus `main`**, auch bei Testständen.

## Dateistruktur

```text
index.html
assets/nebelkind-logo.svg
repository.nebelkind/addon.xml
packages/
  addons.xml                 # automatisch erstellt
  addons.xml.md5             # automatisch erstellt
  repository.nebelkind/repository.nebelkind-1.0.0.zip
  skin.nebelflix/skin.nebelflix-0.5.70.zip
  plugin.video.nebelflix.hybrid/plugin.video.nebelflix.hybrid-2026.11.15.1.zip
  plugin.video.nebelflix.tv/plugin.video.nebelflix.tv-0.2.0.zip
  plugin.video.nebelkindmusic/plugin.video.nebelkindmusic-5.0.0.zip
```

## Zugriff & Sicherheit

Nur `schlimmtendo` und von ihm ausdrücklich autorisierte GitHub-Verbindungen dürfen Dateien ändern. Öffentliche Leser dürfen herunterladen und Forks erstellen; Pull Requests haben **keine direkten Schreibrechte**.

**Keine Zugangsdaten, API-Tokens, privaten URLs oder persönlichen Konfigurationen in Addon-ZIPs committen.** In GitHub `Settings → Collaborators`, Zwei-Faktor-Authentifizierung und den Schutz des `main`-Branches kontrollieren.

> GitHub Pages: `Settings → Pages → Deploy from a branch → main → /(root) → Save`.