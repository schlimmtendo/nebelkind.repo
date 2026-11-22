"""Change-triggered maintenance using Vavoo's own quick/defects workflows."""
import hashlib
import json
import os
from pathlib import Path
import time
import xbmc
import xbmcgui


def read(path):
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):return {}


def write(path,data):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data),encoding='utf-8')
    temporary.replace(path)


def request(profile,countries,snapshots):
    """Persist before consuming so an abort never loses a detected change."""
    payload={'countries':list(countries),'fingerprints':{c:snapshots[c] for c in countries if c in snapshots}}
    payload['id']=hashlib.sha256(json.dumps(payload,sort_keys=True).encode('utf-8')).hexdigest()
    write(Path(profile)/'nebelkind-maintenance-request.json',payload)
    return payload


def run_pending(profile,api,groups,monitor):
    from . import vavoo_sync as repair_engine
    root=Path(profile)
    job=read(root/'nebelkind-maintenance-request.json')
    state_path=root/'nebelkind-maintenance-state.json'
    state=read(state_path)
    if not job.get('id') or not job.get('countries'):return False
    if state.get('id')==job['id'] and state.get('completed'):return False
    lock=root/'nebelkind-maintenance.lock'
    # A lock from a previous Kodi process must not prevent a later retry.
    try:
        if lock.exists() and int(lock.read_text(encoding='ascii'))!=os.getpid():lock.unlink()
        fd=os.open(str(lock),os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    except (OSError,ValueError):return False
    try:
        with os.fdopen(fd,'w') as stream:stream.write(str(os.getpid()))
        state=read(state_path)
        if state.get('id')==job['id'] and state.get('completed'):return False
        if not groups.load_groups():return False
        codes=api.parse_country_codes(__import__('xbmcaddon').Addon('plugin.video.vavoo.tv.plus').getSetting('selected_countries'))
        countries=[country for country in api.get_available_countries() if api.country_to_code(country).upper() in {str(c).upper() for c in codes}]
        if not countries:return False
        attempt=0
        while attempt<3:
            while xbmc.getCondVisibility('Player.HasVideo') or xbmc.getCondVisibility('System.HasModalDialog'):
                if monitor.waitForAbort(10):return False
            if monitor.abortRequested():return False
            state=dict(id=job['id'],completed=False,attempt=attempt+1,at=time.time())
            write(state_path,state)
            try:
                for mode in ('quick','defects'):
                    xbmc.log('[Vavoo/Nebelkind] Automatische Favoritenpflege: '+mode,xbmc.LOGINFO)
                    ok=repair_engine.sync_favorite_groups('',False,mode,
                        addon_name='Vavoo TV Plus',api_module=api,group_favs=groups,
                        get_active_countries=lambda available:countries,
                        append_internal_log=lambda message:xbmc.log('[Vavoo/Nebelkind] '+message,xbmc.LOGINFO),
                        background=True,monitor=monitor)
                    if not ok:break
                if ok:
                    state.update(completed=True,finished_at=time.time())
                    write(state_path,state)
                    xbmcgui.Window(10000).setProperty('VavooPlus.FavoritesRevision',job['id'])
                    xbmcgui.Dialog().notification('Vavoo TV Plus','Sender aktualisiert und defekte Favoriten geprüft.',time=4500,sound=False)
                    return True
            except Exception as error:
                xbmc.log('[Vavoo/Nebelkind] Favoritenpflege zurückgestellt: '+type(error).__name__,xbmc.LOGWARNING)
            if xbmc.getCondVisibility('Player.HasVideo') or xbmc.getCondVisibility('System.HasModalDialog'):
                if monitor.waitForAbort(10):return False
                continue
            attempt+=1
            if monitor.waitForAbort(30):return False
        xbmcgui.Dialog().notification('Vavoo TV Plus','Automatische Favoritenpflege nicht vollständig. Vorhandene Sender bleiben erhalten.',time=6000,sound=False)
        return False
    finally:
        lock.unlink(missing_ok=True)
