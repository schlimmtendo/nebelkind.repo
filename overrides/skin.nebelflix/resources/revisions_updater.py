"""Nebelkind releases, gated by the controller's completed-widget session.

Only the explicitly managed add-ons are installed. Packages are staged and
validated before an installed directory is moved; user data is never touched.
"""
import io
from bounded_workers import SearchExecutor
import json
from pathlib import Path, PurePosixPath
import re
import os
import shutil
import tempfile
import time
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile

import xbmc
import xbmcgui
import xbmcvfs

BASE = 'https://raw.githubusercontent.com/schlimmtendo/nebelkind.repo/main/packages/'
MANAGED = ('plugin.video.nebelflix.hybrid',
           'plugin.video.nebelflix.tv', 'plugin.video.nebelkindmusic', 'skin.nebelflix')
TITLE = 'Revisions Updater'
MAX_ZIP = 150 * 1024 * 1024
MAX_UNPACKED = 600 * 1024 * 1024


def version(value):
    """Repo releases use numeric dotted versions; refuse ambiguous ordering."""
    if not re.fullmatch(r'\d+(?:\.\d+)*', value or ''):
        raise ValueError('Nicht unterstützte Versionsnummer: ' + str(value))
    parts = [int(x) for x in value.split('.')]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def rpc(method, params=None):
    result = json.loads(xbmc.executeJSONRPC(json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})))
    if 'error' in result:
        raise RuntimeError(str(result['error']))
    return result.get('result', {})


def installed(addon_id):
    try:
        return rpc('Addons.GetAddonDetails', {'addonid': addon_id,
                    'properties': ['version', 'enabled', 'name']})['addon']
    except (RuntimeError, KeyError):
        return None


def fetch(url, limit, monitor, progress=None):
    request = Request(url, headers={'User-Agent': 'Nebelkind-Revisions-Updater/1'})
    with urlopen(request, timeout=20) as response:
        if not response.geturl().startswith(BASE):
            raise ValueError('Unerwartetes Download-Ziel')
        total = int(response.headers.get('Content-Length') or 0)
        if total > limit:
            raise ValueError('Download ist zu groß')
        data = bytearray()
        while True:
            if monitor.abortRequested() or (progress and progress.iscanceled()):
                raise InterruptedError('Aktualisierung abgebrochen')
            chunk = response.read(128 * 1024)
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('Download ist zu groß')


def catalog(data):
    root = ET.fromstring(data)
    if root.tag != 'addons':
        raise ValueError('Ungültiger Repository-Katalog')
    result = []
    seen = set()
    for node in root.findall('addon'):
        addon_id = node.get('id')
        if addon_id not in MANAGED:
            continue
        if addon_id in seen:
            raise ValueError('Doppelter Katalogeintrag: ' + addon_id)
        seen.add(addon_id)
        version(node.get('version'))
        result.append(node)
    return sorted(result, key=lambda item: MANAGED.index(item.get('id')))


def unpack(data, destination, addon_id, expected):
    """Reject foreign roots, traversal, links and mismatched package metadata."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 30000 or sum(x.file_size for x in entries) > MAX_UNPACKED:
            raise ValueError('Entpacktes Paket ist zu groß')
        seen = set()
        for entry in entries:
            name = entry.orig_filename
            parts = PurePosixPath(name).parts
            if (not parts or parts[0] != addon_id or '\\' in name or ':' in name
                    or any(x in ('.', '..') for x in name.split('/'))
                    or ((entry.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError('Unsicherer Paketpfad: ' + name)
            key = name.rstrip('/').casefold()
            if key in seen:
                raise ValueError('Doppelter Paketpfad: ' + name)
            seen.add(key)
        manifest = ET.fromstring(archive.read(addon_id + '/addon.xml'))
        if manifest.get('id') != addon_id or manifest.get('version') != expected:
            raise ValueError('Paket und Katalog stimmen nicht überein')
        archive.extractall(destination)
    return manifest


def wait_version(addon_id, expected, monitor):
    deadline = time.monotonic() + 25
    while not monitor.abortRequested() and time.monotonic() < deadline:
        info = installed(addon_id)
        if info and info.get('version') == expected:
            return
        monitor.waitForAbort(0.25)
    raise RuntimeError('Kodi hat die neue Version nicht übernommen: ' + addon_id)


def dependencies(manifest):
    for node in manifest.findall('./requires/import'):
        if node.get('optional') == 'true':
            continue
        addon_id = node.get('addon')
        if addon_id.startswith('xbmc.'):
            # Kodi's virtual API add-ons are omitted by Addons.GetAddonDetails.
            import xbmcaddon
            info = {'version': xbmcaddon.Addon(addon_id).getAddonInfo('version'),
                    'enabled': True}
        else:
            info = installed(addon_id)
        if not info or (node.get('version') and
                        version(info['version']) < version(node.get('version'))):
            raise RuntimeError('Abhängigkeit fehlt oder ist zu alt: ' + addon_id +
                               '. Bitte zuerst über Kodis Addon-Verwaltung installieren.')
        if not info.get('enabled'):
            raise RuntimeError('Abhängigkeit ist deaktiviert: ' + addon_id)


def activate_skin(staged, target, expected, monitor, backup):
    """Patch the active skin without renaming Kodi's open directory/fonts.

    Replace changed files only, manifest last. Keep per-file rollback copies;
    never enter the profile, remove its cache, or disable the active skin.
    """
    changed = []
    files = sorted((p for p in staged.rglob('*') if p.is_file()),
                   key=lambda p: (p.name == 'addon.xml', p.relative_to(staged).as_posix()))
    try:
        for source in files:
            if monitor.abortRequested():
                raise InterruptedError('Kodi wird beendet')
            relative = source.relative_to(staged)
            dest = target / relative
            if any(p.is_symlink() for p in (dest, *dest.parents) if p != target.parent):
                raise ValueError('Verknüpfter Skin-Pfad ist nicht erlaubt')
            data = source.read_bytes()
            if dest.exists() and dest.read_bytes() == data:
                continue  # In particular, leave Kodi's loaded fonts/textures alone.
            existed = dest.exists()
            saved = backup / relative
            if existed:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, saved)
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.revision-', dir=str(dest.parent))
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                os.replace(temporary, dest)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            changed.append((dest, saved, existed))
        xbmc.executebuiltin('UpdateLocalAddons', True)
        wait_version('skin.nebelflix', expected, monitor)
    except Exception:
        for dest, saved, existed in reversed(changed):
            if existed:
                shutil.copy2(saved, dest)
            else:
                dest.unlink(missing_ok=True)
        xbmc.executebuiltin('UpdateLocalAddons', True)
        raise


def activate(staged, addon_id, expected, monitor, backup_root):
    """Disable services through Kodi, replace atomically, rescan, verify, enable.

    Keep the previous directory in profile-local backups, including on success.
    No direct writes to Kodi's database or addon_data are necessary.
    """
    addons = Path(xbmcvfs.translatePath('special://home/addons/')).resolve()
    target = addons / addon_id
    old = installed(addon_id)
    enabled = old is None or old.get('enabled', False)
    is_skin = addon_id == xbmc.getSkinDir()
    backup = backup_root / addon_id
    if is_skin:
        return activate_skin(staged, target, expected, monitor, backup)
    moved = False
    replaced = False
    try:
        if old and enabled and not is_skin:
            rpc('Addons.SetAddonEnabled', {'addonid': addon_id, 'enabled': False})
            if installed(addon_id).get('enabled'):
                raise RuntimeError('Addon konnte nicht angehalten werden: ' + addon_id)
            if monitor.waitForAbort(1):
                raise InterruptedError('Kodi wird beendet')
        if target.is_symlink() or (target.exists() and target.resolve().parent != addons):
            raise ValueError('Addon-Ziel liegt außerhalb des Addon-Ordners')
        if target.exists():
            target.rename(backup)
            moved = True
        staged.rename(target)
        replaced = True
        xbmc.executebuiltin('UpdateLocalAddons', True)
        wait_version(addon_id, expected, monitor)
        if enabled and not is_skin:
            rpc('Addons.SetAddonEnabled', {'addonid': addon_id, 'enabled': True})
            if not installed(addon_id).get('enabled'):
                raise RuntimeError('Addon konnte nicht aktiviert werden: ' + addon_id)
    except Exception:
        if replaced:
            # Keep the failed candidate for diagnosis; restore the old directory.
            target.rename(backup_root / (addon_id + '.failed'))
        if moved:
            backup.rename(target)
        xbmc.executebuiltin('UpdateLocalAddons', True)
        if old:
            wait_version(addon_id, old['version'], xbmc.Monitor())
            if enabled and not is_skin:
                rpc('Addons.SetAddonEnabled', {'addonid': addon_id, 'enabled': True})
        raise


def request_repository_updates(window, monitor):
    """Ask Kodi to resolve/install repository updates, including dependencies.

    Kodi owns platform selection, dependency ordering and installed origins.
    This starts asynchronous work; it never claims packages were installed.
    """
    if monitor.abortRequested():return False
    try:
        last=float(window.getProperty('NF.Revisions.RepoCheck') or 0)
    except ValueError:last=0
    if time.time()-last<3600:return False
    try:
        setting='general.addonupdates'
        current=rpc('Settings.GetSettingValue',{'setting':setting}).get('value')
        if current!=0:
            if rpc('Settings.SetSettingValue',{'setting':setting,'value':0}) is not True:
                raise RuntimeError('Automatische Kodi-Updates konnten nicht aktiviert werden')
        xbmc.executebuiltin('UpdateAddonRepos',False)
        window.setProperty('NF.Revisions.RepoCheck',str(time.time()))
        xbmc.log('[Revisions Updater] Kodi-Repo-Prüfung und automatische Addon-Updates angefordert.',xbmc.LOGINFO)
        return True
    except Exception:
        xbmc.log('[Revisions Updater] Kodi-Repo-Prüfung konnte nicht gestartet werden.',xbmc.LOGERROR)
        return False


class Updater:
    def __init__(self, window, monitor, blocked):
        self.window = window
        self.monitor = monitor
        # Window properties survive skin reloads, but vanish on a real Kodi exit.
        if blocked:
            window.setProperty('NF.Revisions.Deferred', 'true')
        self.blocked = blocked or bool(window.getProperty('NF.Revisions.Deferred'))
        self.next_check = 0
        self.catalog_job = None
        self.catalog_data = None
        self.catalog_pool = SearchExecutor(max_workers=1)

    def due(self):
        if self.blocked or self.window.getProperty('NF.Revisions.Running'):
            return False
        try:
            last = float(self.window.getProperty('NF.Revisions.LastCheck') or 0)
        except ValueError:
            last = 0
        return time.time() - last >= 3600 and time.monotonic() >= self.next_check

    def prepare(self):
        """Catalog I/O must never hold the UI/controller or claim installation."""
        if self.catalog_data is not None:return True
        if self.catalog_job is None:
            self.catalog_job=self.catalog_pool.submit(fetch,BASE+'addons.xml',2*1024*1024,self.monitor)
            return False
        if not self.catalog_job.done():return False
        try:self.catalog_data=self.catalog_job.result()
        except Exception as exc:
            self.next_check=time.monotonic()+300
            xbmc.log('[Revisions Updater] Katalogpruefung: '+str(exc),xbmc.LOGWARNING)
        finally:self.catalog_job=None
        return self.catalog_data is not None

    def run(self):
        self.next_check = time.monotonic() + 300
        progress = None
        updated = []
        failure = ''
        try:
            data=self.catalog_data
            self.catalog_data=None
            nodes = catalog(data if data is not None else fetch(BASE + 'addons.xml', 2 * 1024 * 1024, self.monitor))
            pending = []
            for node in nodes:
                info = installed(node.get('id'))
                if info is None or version(info['version']) < version(node.get('version')):
                    pending.append(node)
            self.window.setProperty('NF.Revisions.LastCheck', str(time.time()))
            if not pending:
                return False
            self.window.setProperty('NF.Revisions.Running','true')
            progress = xbmcgui.DialogProgress()
            progress.create(TITLE, 'Neue Revisionen werden vorbereitet …')
            work_root = Path(xbmcvfs.translatePath('special://home/addons/'))
            # Same filesystem as the destination, so replacement is a rename.
            with tempfile.TemporaryDirectory(prefix='.nebelkind-stage-', dir=str(work_root)) as work:
                stage = Path(work)
                for index, node in enumerate(pending):
                    try:
                        addon_id, revision = node.get('id'), node.get('version')
                        label = re.sub(r'\[.*?\]', '', node.get('name', addon_id))
                        progress.update(int(index * 100 / len(pending)),
                                        '%s → %s\nPaket wird geladen und geprüft (%d/%d)' %
                                        (label, revision, index + 1, len(pending)))
                        data = fetch(BASE + '%s/%s-%s.zip' % (addon_id, addon_id, revision),
                                     MAX_ZIP, self.monitor, progress)
                        manifest = unpack(data, stage, addon_id, revision)
                        # Check actual package dependencies, not only the catalog.
                        dependencies(manifest)
                        if progress.iscanceled() or self.monitor.abortRequested():
                            raise InterruptedError('Aktualisierung abgebrochen')
                        backups = Path(xbmcvfs.translatePath(
                            'special://profile/addon_data/skin.nebelflix/revision-backups/'))
                        backup_root = backups / ('%s-%s' % (time.strftime('%Y%m%d-%H%M%S'), index))
                        backup_root.mkdir(parents=True, exist_ok=False)
                        progress.update(int(index * 100 / len(pending)),
                                        '%s → %s\nWird installiert …' % (label, revision))
                        activate(stage / addon_id, addon_id, revision, self.monitor, backup_root)
                        updated.append('%s: %s' % (label, revision))
                        progress.update(int((index + 1) * 100 / len(pending)),
                                        '%s\nErfolgreich aktualisiert ✓' % updated[-1])
                        self.monitor.waitForAbort(0.6)
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        if self.monitor.abortRequested() or progress.iscanceled():
                            raise InterruptedError('Aktualisierung abgebrochen')
                        detail=node.get('id')+': '+str(exc)
                        failure += ('\n' if failure else '')+detail
                        xbmc.log('[Revisions Updater] Übersprungen: '+detail,xbmc.LOGERROR)
                        self.window.setProperty('NF.Revisions.LastCheck',str(time.time()-3300))
                        continue

        except Exception as exc:
            failure = str(exc)
            xbmc.log('[Revisions Updater] ' + failure, xbmc.LOGERROR)
            # Retry a failed catalog/update after five minutes, even after reload.
            self.window.setProperty('NF.Revisions.LastCheck', str(time.time() - 3300))
        finally:
            if progress:
                progress.close()
            self.window.clearProperty('NF.Revisions.Running')
            request_repository_updates(self.window,self.monitor)
        if self.monitor.abortRequested():
            return False
        if updated or failure:
            message = '\n'.join(updated)
            if failure:
                message += '\n\nNicht abgeschlossen: ' + failure
                if updated:message += '\n\nErfolgreiche Updates bleiben installiert. Der Skin wird neu geladen.'
            elif updated:
                message += '\n\nAlle Revisionen sind installiert. Der Skin wird neu geladen.'
            xbmcgui.Dialog().ok(TITLE, message.strip())
        return bool(updated)
