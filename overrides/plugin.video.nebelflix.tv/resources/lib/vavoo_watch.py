"""Observe Vavoo's existing startup fingerprints without changing Vavoo."""
from pathlib import Path
import time
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
from .common import DATA
from . import vavoo_jobs


class Watcher:
    def __init__(self):
        self.addon=xbmcaddon.Addon('plugin.video.vavoo.tv.plus')
        self.profile=Path(xbmcvfs.translatePath(self.addon.getAddonInfo('profile')))
        self.path=DATA/'vavoo-watch.json'
        self.previous=vavoo_jobs.read(self.path).get('fingerprints')
        if self.previous is None:
            self.previous=vavoo_jobs.read(self.profile/'startup_country_snapshots.json')
            vavoo_jobs.write(self.path,{'fingerprints':self.previous})
        self.next_check=0
        self.next_worker=0

    def poll(self):
        if time.monotonic()<self.next_check:return
        self.next_check=time.monotonic()+10
        current=vavoo_jobs.read(self.profile/'startup_country_snapshots.json')
        if not current:return
        changed=[country for country,value in current.items()
                 if country in self.previous and self.previous[country]!=value]
        if changed:
            vavoo_jobs.request(DATA,list(current),current)
            self.next_worker=0
        self.previous=current
        vavoo_jobs.write(self.path,{'fingerprints':current})

    def due(self):
        if xbmc.getCondVisibility('Player.HasVideo') or xbmc.getCondVisibility('System.HasModalDialog'):return False
        home=xbmcgui.Window(10000)
        if home.getProperty('NF.StartupBlocked')=='true' or home.getProperty('NF.IntroActive')=='true':return False
        try:
            if xbmcaddon.Addon('plugin.video.nebelflix.tv').getSetting('vavoo.auto_maintenance')=='false':return False
        except RuntimeError:return False  # Kodi may be replacing/disabling this addon during an update.
        if not self.addon.getSetting('selected_countries').strip():return False
        if time.monotonic()<self.next_worker:return False
        job=vavoo_jobs.read(DATA/'nebelkind-maintenance-request.json')
        state=vavoo_jobs.read(DATA/'nebelkind-maintenance-state.json')
        return bool(job.get('id')) and not (state.get('id')==job['id'] and state.get('completed'))

    def run(self,monitor):
        from .vavoo_runtime import load
        from .common import log
        try:
            api,groups=load()
            vavoo_jobs.run_pending(DATA,api,groups,monitor)
        except Exception as exc:
            # Log code locations only: exception messages can contain signed URLs.
            import traceback
            frames=traceback.extract_tb(exc.__traceback__)
            location=' > '.join('%s:%s:%s' % (Path(f.filename).name,f.lineno,f.name) for f in frames)
            log('Vavoo-Schnittstelle nicht verfügbar: '+type(exc).__name__+' ['+location+']')
