"""Explicit trailer requests are independent of automatic spotlight playback."""
import json
import time
from bounded_workers import SearchExecutor


class ManualTrailer:
    def __init__(self,api):
        self.api=api
        self.pool=SearchExecutor(max_workers=1)
        self.job=None
        self.request=None
        self.started=0
        self.token=''
        self.playing=False

    def valid(self):
        if not self.request:return False
        if self.request['context']=='spotlight':
            return self.api.prop('Page')==self.request['page'] and self.api.spotlight_visible()
        return self.api.xbmc.getCondVisibility('Window.IsVisible(1191)')

    def clear(self):
        if self.job:self.job.cancel()
        self.job=None
        self.api.prop('ManualTrailerBusy','')

    def stop_preview(self):
        if self.api.prop('PlaybackOwner')=='manual_preview' and self.api.prop('ManualPreviewToken')==self.token:
            self.api.PLAYER.stop()
            self.api.prop('PlaybackOwner','')
            self.api.prop('ManualPreviewToken','')
            self.api.prop('ManualPreviewPlaying','')
            self.api.restore_spotlight_view()
        self.playing=False
        self.api.prop('ManualTrailerActive','')

    def tick(self):
        raw=self.api.prop('ManualTrailerRequest')
        if raw:
            self.api.prop('ManualTrailerRequest','')
            if self.playing:self.stop_preview()
            self.clear()
            self.request=json.loads(raw)
            self.token=str(time.monotonic_ns())
            self.started=time.monotonic()
            self.api.prop('ManualTrailerBusy','true')
            self.job=self.pool.submit(self.api.resolve_trailer,dict(self.request['item']))
        if self.playing:
            if self.api.prop('PlaybackOwner')!='manual_preview':
                self.playing=False;self.api.prop('ManualTrailerActive','')
            elif not self.valid() or self.api.prop('ManualPreviewToken')!=self.token:
                self.stop_preview()
            elif not self.api.PLAYER.isPlaying() and time.monotonic()-self.started>15:
                self.stop_preview()
        if self.job is None:return
        if not self.valid():self.clear();return
        if time.monotonic()-self.started>30:
            self.clear();self.api.notification('Die Trailer-Suche dauert zu lange. Bitte erneut versuchen.');return
        if not self.job.done():return
        try:stream=self.job.result()
        except Exception as exc:
            self.api.log('Manual trailer lookup failed: '+type(exc).__name__)
            stream=None
        self.clear()
        if not stream:
            self.api.notification('Für diesen Titel ist kein Trailer verfügbar.');return
        if self.api.PLAYER.isPlaying():return
        import trailers
        windowed=self.request['context']=='spotlight'
        if windowed:
            self.api.prop('ManualPreviewToken',self.token);self.api.prop('ManualPreviewPlaying','')
            self.api.prop('PlaybackOwner','manual_preview');self.api.prop('ManualTrailerActive','true')
            self.playing=True;self.started=time.monotonic()
        else:
            self.api.prop('PlaybackOwner','trailer')
            self.api.prop('TrailerFile','')
            self.api.xbmc.executebuiltin('Dialog.Close(1191)')
        self.api.log('Manual trailer start: %s:%s language=%s'%(self.request['item'].get('Type'),self.request['item'].get('TMDB'),stream.get('language','unknown')))
        try:trailers.play(self.api.PLAYER,stream,windowed=windowed)
        except Exception as exc:
            self.api.log('Manual trailer playback failed: '+type(exc).__name__)
            if windowed:self.stop_preview()
            self.playing=False;self.api.prop('ManualTrailerActive','')
            self.api.notification('Der Trailer konnte nicht gestartet werden.')

    def close(self):
        if self.playing:self.stop_preview()
        self.playing=False
        self.clear();self.api.prop('ManualTrailerActive','');self.pool.shutdown(wait=False)
