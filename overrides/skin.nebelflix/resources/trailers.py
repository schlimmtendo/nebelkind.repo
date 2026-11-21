"""MultiTrailer resolution only; playback and cancellation belong to the skin."""
def resolve(item):
    from multitrailer import api,sources,filters
    from urllib.parse import urlparse
    media=item.get('Type');ident=str(item.get('TMDB') or '')
    if media not in ('movie','tv') or not ident.isdigit():return None
    for language,locale in (('de','de-DE'),('en','en-US')):
        try:videos=sources.from_tmdb(media,int(ident),locale=locale) or []
        except Exception:continue
        candidates=[v for v in videos if v.get('language')==language and v.get('type')=='Trailer'
            and not v.get('fan_made') and not filters.is_fan_made(v.get('title',''),v.get('channel'))]
        candidates.sort(key=lambda v:(bool(v.get('official')),v.get('published','')),reverse=True)
        for candidate in candidates:
            try:
                stream,_=api.resolve_video_item(candidate,max_quality=2160,ask_quality=False,prefer_dash=False)
            except Exception:continue
            if stream and stream.get('url') and urlparse(stream['url']).hostname not in ('127.0.0.1','localhost','::1'):
                return dict(stream,title=item.get('Title','Trailer'),language=language)
    return None


def play(player, stream, windowed):
    import xbmcgui
    from urllib.parse import urlencode
    li = xbmcgui.ListItem(label=stream.get('title', 'Trailer'), path=stream['url'])
    li.setProperty('IsPlayable', 'true')
    if stream.get('isa_manifest_type'):
        li.setProperty('inputstream', 'inputstream.adaptive')
        headers = urlencode(stream.get('http_headers') or {})
        if headers:
            li.setProperty('inputstream.adaptive.manifest_headers', headers)
            li.setProperty('inputstream.adaptive.stream_headers', headers)
        li.setMimeType(stream.get('mime_type') or ('application/dash+xml'
                       if stream['isa_manifest_type'] == 'mpd' else 'application/vnd.apple.mpegurl'))
        li.setContentLookup(False)
    player.play(stream['url'], li, windowed=windowed)
