"""Render TMDB catalogue responses directly for skin widgets."""
import sys
from urllib.parse import urlencode
import xbmcgui
import xbmcplugin


def directory(params):
    handle = int(sys.argv[1])
    media = params['media_type']
    rows = []
    from resources.lib.watch_status import decorate
    from resources.lib.playcountDB import playcountDB
    records=[dict(row,Type=media) for row in params.get('list',[])]
    records=decorate(records,playcountDB)
    for data in records:
        ident = str(data['id'])
        title = data.get('title') or data.get('name') or ''
        item = xbmcgui.ListItem(label=title, offscreen=True)
        tag = item.getVideoInfoTag()
        tag.setTitle(title)
        tag.setPlaycount(1 if data.get('WatchState')=='watched' else 0)
        item.setProperty('NF.WatchState',data.get('WatchState',''))
        item.setProperty('NF.WatchPercent',str(data.get('WatchPercent',0)))
        tag.setPlot(data.get('overview') or '')
        tag.setMediaType('movie' if media == 'movie' else 'tvshow')
        tag.setUniqueIDs({'tmdb': ident}, 'tmdb')
        date = data.get('release_date') or data.get('first_air_date') or ''
        if date:
            tag.setPremiered(date)
            tag.setYear(int(date[:4]))
        tag.setRating(float(data.get('vote_average') or 0), int(data.get('vote_count') or 0))
        art = {}
        for key, field, size in (('poster', 'poster_path', 'w500'), ('fanart', 'backdrop_path', 'w1280')):
            if data.get(field):
                art[key] = 'https://image.tmdb.org/t/p/' + size + data[field]
        item.setArt(art)
        query = dict(info='play' if media == 'movie' else 'seasons', tmdb_type=media, tmdb_id=ident)
        path = sys.argv[0] + '?' + urlencode(query)
        item.setProperty('IsPlayable', 'true' if media == 'movie' else 'false')
        rows.append((path, item, media == 'tv'))
    page = int(params.get('next_pages') or 1)
    if page < int(params.get('total_pages') or 0):
        query = dict(action='listings', media_type=media, url=params['url'], next_pages=page + 1)
        rows.append((sys.argv[0] + '?' + urlencode(query), xbmcgui.ListItem(label='Nächste Seite'), True))
    xbmcplugin.setContent(handle, 'movies' if media == 'movie' else 'tvshows')
    xbmcplugin.addDirectoryItems(handle, rows, len(rows))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=True)
