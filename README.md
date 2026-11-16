# Nebelkind Repo

Offizielles Kodi-Addon-Repository von Nebelkind, verwaltet von **schlimmtendo**.

## Veröffentlichung nur nach Freigabe

- **`main` ist LIVE:** Kodi bezieht die Addon-Liste ausschließlich von `main`.
- **`testing` ist die Vorbereitung:** ZIPs und Metadaten können hier getestet werden, ohne Updates an Kodi-Geräte auszuliefern.
- **Du gibst jedes Update frei:** Erst wenn du Änderungen von `testing` nach `main` übernimmst, sehen Kodi-Geräte die neue Version. Kein automatisches Deployment von `testing` nach `main`.
- Nur Accounts mit ausdrücklich erteilten GitHub-Schreibrechten können den Repo-Inhalt ändern. Öffentliche Besucher haben lediglich Lesezugriff und können Vorschläge über Forks/Pull Requests machen.

## Neues Kodi-Addon vorbereiten

1. Lege die fertige Addon-ZIP in `packages/<addon-id>/<addon-id>-<version>.zip` auf dem Branch `testing` ab. Die Versionsnummer muss mit der Version in der `addon.xml` übereinstimmen.
2. Die GitHub Action `Prepare Kodi repository` prüft die ZIP-Dateien und erstellt auf `testing` die Dateien `packages/addons.xml` und `packages/addons.xml.md5`.
3. Teste die neue Version zunächst selbst (direkt als ZIP installieren).
4. Nur nach deiner Freigabe: Pull Request `testing` → `main` zusammenführen. Damit wird die Version öffentlich für Kodi-Updates.

**Wichtig:** Eine Korrektur mit derselben Versionsnummer wird von Kodi normalerweise nicht als neueres Update erkannt. Für ein echtes Update die Addon-Version erhöhen.

## Kodi-Repo-Installer

Der Repository-Installer hat die Addon-ID `repository.nebelkind`. Seine ZIP liegt nach der Vorbereitung unter `packages/repository.nebelkind/repository.nebelkind-1.0.0.zip`.

Die veröffentlichte Addon-Liste lautet:

`https://raw.githubusercontent.com/schlimmtendo/nebelkind.repo/main/packages/addons.xml`

Eine zusätzliche Webseite bzw. GitHub Pages ist für diese direkten GitHub-Raw-URLs nicht nötig.

## Sicherheit

- Keine Passwörter, Tokens, API-Keys oder privaten Konfigurationen in dieses **öffentliche** Repository hochladen – auch nicht in `testing`.
- Unter **Settings → Collaborators** kontrollieren, dass keine unerwünschten Personen Schreibzugriff besitzen.
- GitHub-Zwei-Faktor-Authentifizierung aktivieren und den `main`-Branch gegen unbeabsichtigtes Löschen/Force-Push schützen.
- ChatGPT arbeitet, soweit verbunden und autorisiert, mit deinen GitHub-Berechtigungen; es ist kein eigenständiger GitHub-Mitarbeiter.
