"""Low-impact background maintenance. Starts only after user first opens TV."""
import time
import threading
import xbmc
from resources.lib.common import settings,log


def _detect_external_vavoo(win, player):
    """Recognize direct Vavoo-live playback, without modifying Vavoo Plus.

    Only known Vavoo live URLs and an exact known channel name qualify.
    Ordinary film/series playback remains untouched.
    """
    from resources.lib import channels
    from resources.lib.common import canonical, load_json
    if not player.isPlayingVideo():
        return False
    try:
        playing=str(player.getPlayingFile() or '')
    except Exception:
        return False
    origin=playing.casefold()
    if not any(token in origin for token in ('geil.to/live/',
                                             'vavoo.to/vavoo-iptv/',
                                             'plugin.video.vavoo.tv.plus/')):
        return False
    title=xbmc.getInfoLabel('VideoPlayer.Title').strip()
    key=canonical(title)
    if not key:
        return False
    # No network access solely for the OSD: look up stored station metadata.
    raw=load_json('channels.json',{}).get('channels',[])
    matches=[ch for ch in channels._group_channels(raw) if ch.get('normalized')==key] if raw else []
    if len(matches)!=1:
        return False
    ch=matches[0]
    win.setProperty('NFTV.Id',ch['id'])
    win.setProperty('NFTV.Channel',ch['name'])
    win.setProperty('NFTV.Active','1')
    win.setProperty('NFTV.Direct','1')
    win.setProperty('NFTV.PlayingFile',playing)
    log('Direkte Vavoo-Wiedergabe erkannt: %s' % ch['name'])
    return True


def _update_osd():
    """Make XMLTV available to Kodi's ordinary video OSD (not native PVR)."""
    import xbmcgui
    from resources.lib import channels,epg,art
    from resources.lib.common import friendly_time, ASSETS
    from resources.lib.playback import clear
    win=xbmcgui.Window(10000)
    cid=win.getProperty('NFTV.Id')
    if not cid:
        # Native Vavoo Plus playback can also be launched outside Nebelflix.
        if not _detect_external_vavoo(win,xbmc.Player()):
            return
        cid=win.getProperty('NFTV.Id')
    if win.getProperty('NFTV.Pending')=='1':
        return
    player=xbmc.Player()
    if win.getProperty('NFTV.Active')!='1' or not player.isPlayingVideo():
        # Do not lose the stop guard, even if Kodi re-invokes the plugin.
        if win.getProperty('NFTV.Active')=='1':
            win.setProperty('NFTV.StopGuard',cid+'|'+str(time.time()+25))
            win.setProperty('NFTV.UserStop','1')
        if win.getProperty('NFTV.ReturnGuide') == '1' and win.getProperty('NFTV.GuideAlive') != 'true':
            win.setProperty('NFTV.RestoreGuide', '1')
        clear();return
    # Timeshift and HLS segments can change getPlayingFile() while the same
    # channel continues. Don't clear the station/EPG on a filename change.
    # Explicit StopObserver handles user stops instead.
    ch=channels.find_channel(cid)
    if not ch:
        clear();return
    with epg.connect() as conn:
        indexed=epg.guide_index(conn)
        current=epg.programme(ch,conn=conn,index=indexed)
    now=current.get('now')
    upcoming=current.get('next')
    nowtxt=('%s–%s · %s' % (friendly_time(now[0]),friendly_time(now[1]),now[2])) if now else ''
    nexttxt=('%s–%s · %s' % (friendly_time(upcoming[0]),friendly_time(upcoming[1]),upcoming[2])) if upcoming else ''
    if not nowtxt:
        nowtxt='Keine Programmdaten für diesen Sender'
        if win.getProperty('NFTV.Now') != nowtxt:
            log('EPG ohne laufende Sendung: %s (id=%s; quelle=%s)' % (
                ch.get('name',''),cid,current.get('source','')),xbmc.LOGWARNING)
    display='Jetzt: '+nowtxt if now else nowtxt
    if nexttxt:
        display+='[CR]Danach: '+nexttxt
    win.setProperty('NFTV.Now',nowtxt)
    win.setProperty('NFTV.Next',nexttxt)
    win.setProperty('NFTV.Plot',display)
    logo=art.logo_for(ch,current)
    poster=current.get('programme_art','')
    if poster and poster.startswith('https://'):
        poster=art.cached_url(poster) or poster
    # XMLTV programme art is optional. An actual channel logo is a safe
    # fallback; the Kodi generic video-camera graphic is not informative.
    win.setProperty('NFTV.Logo',logo or str(ASSETS / 'icon.png'))
    win.setProperty('NFTV.Poster',poster or logo or str(ASSETS / 'icon.png'))


class StopObserver(xbmc.Player):
    def onPlayBackStopped(self):
        self._stopped()
    def onPlayBackEnded(self):
        self._stopped()
    def _stopped(self):
        import xbmcgui
        win=xbmcgui.Window(10000)
        cid=win.getProperty('NFTV.Id')
        if cid and win.getProperty('NFTV.Active')=='1':
            win.setProperty('NFTV.StopGuard',cid+'|'+str(time.time()+25))
            win.setProperty('NFTV.UserStop','1')
            if win.getProperty('NFTV.ReturnGuide') == '1' and win.getProperty('NFTV.GuideAlive') != 'true':
                win.setProperty('NFTV.RestoreGuide', '1')
            from resources.lib.playback import clear
            clear()

def _maintenance():
    try:
        from resources.lib import epg,channels,art
        cached=channels.channel_list()
        if cached:
            epg.refresh_due(limit=1,target_channels=cached)
            if epg.source_status():
                art.cache_batch(cached,limit=12)
    except Exception as exc:
        log('Background EPG update deferred: %s'%exc,xbmc.LOGWARNING)


def run():
    observer=StopObserver()
    monitor=xbmc.Monitor()
    if monitor.waitForAbort(15):
        return
    maintenance_thread = None
    last_network=0
    last_osd=0
    while not monitor.abortRequested():
        import xbmcgui
        win = xbmcgui.Window(10000)
        if (win.getProperty('NFTV.RestoreGuide') == '1'
                and win.getProperty('NFTV.GuideAlive') != 'true'
                and not xbmc.Player().isPlayingVideo()
                and xbmcgui.getCurrentWindowDialogId() in (0, 9999)):
            win.clearProperty('NFTV.RestoreGuide')
            win.clearProperty('NF.RestorePlaybackFocus')
            win.setProperty('NF.PlaybackOwner', 'tv')
            xbmc.executebuiltin('RunPlugin(plugin://plugin.video.nebelflix.tv/?action=guide)')
        now=time.monotonic()
        if now-last_osd>=3:
            try:_update_osd()
            except Exception as exc:log('OSD-Update: %s'%exc,xbmc.LOGWARNING)
            last_osd=now
        conf=settings()
        if conf.get('sync_started') and now-last_network>=60:
            # Network work at startup/due time only; the main loop keeps
            # the on-screen EPG clock responsive between refreshes.
            last_network=now
            if maintenance_thread is None or not maintenance_thread.is_alive():
                maintenance_thread = threading.Thread(target=_maintenance, daemon=True)
                maintenance_thread.start()
        if monitor.waitForAbort(2):
            break


if __name__=='__main__':
    run()
