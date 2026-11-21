"""Skin catalogue and playback entry points for xShip Hybrid."""
import json
import sys
from urllib.parse import urlencode
from resources.lib import control
from resources.lib import load_timing


def route(params):
    params = dict(params)
    info = params.pop('info')
    media = params.pop('tmdb_type', 'movie')
    params.pop('with_id', None)
    if info == 'watch_providers':
        params['action'] = 'tvProviders' if media == 'tv' else 'movieProviders'
    elif info == 'search':
        params.update(action='hybridSearch', media_type=media,
                      page=params.get('page', '1'))
    elif info == 'provider':
        return _provider_route(params, media)
    elif info in ('details', 'seasons', 'play'):
        from resources.lib.tmdb import cTMDB
        kind = 'tvshow' if media == 'tv' else 'movie'
        tmdb = cTMDB()
        ident = params['tmdb_id']
        if ident.startswith('tt') and ident[2:].isdigit():
            ident = str(tmdb.getTmdbId(ident, season=1 if media == 'tv' else 0) or '')
        if not ident.isdigit():
            raise ValueError('Keine gueltige TMDB-ID fuer diesen Titel gefunden')
        params['tmdb_id'] = ident
        with load_timing.span('hybrid','playback.metadata',status=info):
            from resources.lib.playback_metadata import get_play_meta
            meta = get_play_meta(tmdb,kind,ident) if info=='play' else None
            if not meta:
                meta = tmdb.get_meta(kind, '', '', ident, advanced='true')
        if not meta:
            raise ValueError('Keine Metadaten fuer diesen Titel gefunden')
        meta.update(tmdb_id=ident, mediatype=kind)
        meta.setdefault('systitle', meta.get('title', ''))
        meta.setdefault('sysname', meta.get('title', ''))
        for key in ('season', 'episode'):
            if params.get(key) is not None:
                meta[key] = int(params[key])
        override = control.getSetting('series.play.mode' if media == 'tv' else 'movie.play.mode')
        meta['select'] = {'1': '0', '2': '1', '3': '2'}.get(override, control.getSetting('hosts.mode'))
        action = 'seasons' if media == 'tv' and 'episode' not in meta else 'play'
        if info == 'details' and media == 'movie':
            from resources.lib.indexers.movies import movies
            movies().getDirectory({'list': [dict(meta, id=params['tmdb_id'])], 'presorted': True})
            return None
        params.update(action=action, sysmeta=json.dumps(meta))
    else:
        allowed = ('trending_day', 'trending_week', 'popular', 'top_rated', 'upcoming', 'now_playing', 'on_the_air', 'airing_today', 'discover')
        if info not in allowed or media not in ('movie', 'tv'):
            raise ValueError('Unbekannte Katalogkategorie')
        page = params.pop('page', '1')
        params.pop('action', None)
        if info.startswith('trending_'):
            endpoint = 'trending/' + media + '/' + info.split('_')[1]
        elif info == 'discover':
            endpoint = 'discover/' + media
        else:
            endpoint = media + '/' + info
        params = dict(action='listings', media_type=media, next_pages=page,
                      url='catalogue/' + endpoint + ('?' + urlencode(params) if params else ''))
    sys.argv[2] = '?' + urlencode(params)
    return params


_PROVIDER_NAMES = {
    'netflix': ('netflix',),
    'disney': ('disney+', 'disney plus'),
    'prime': ('amazon prime video', 'prime video', 'amazon video'),
    'rtl': ('rtl+', 'rtl plus'),
    'wow': ('wow', 'sky wow'),
    'paramount': ('paramount+', 'paramount plus'),
}


def _provider_route(params, media):
    slug = params.get('slug', '')
    if slug not in _PROVIDER_NAMES or media not in ('all', 'movie', 'tv'):
        raise ValueError('Unbekannter Streaming-Anbieter')
    if media == 'all':
        # Native Kodi directory browser: same two catalogue routes as skin widgets.
        from resources.lib.indexers.navigator import navigator
        from resources.lib import control
        nav = navigator()
        for kind, label, icon in (('movie','Filme','movies.png'),
                                  ('tv','Serien','tvshows.png')):
            uri = sys.argv[0] + '?' + urlencode({
                'info':'provider','slug':slug,'tmdb_type':kind})
            nav.addDirectoryItem(label,uri,icon,'DefaultVideos.png',isAction=False)
        nav._endDirectory()
        return None
    from resources.lib.indexers.listings import listings
    providers = listings()._getProviders(media)
    aliases = _PROVIDER_NAMES[slug]
    matches = []
    for provider in providers:
        name = str(provider.get('provider_name') or '').casefold().strip()
        if name in aliases or any(name.startswith(alias+' ') for alias in aliases):
            matches.append(provider)
    if not matches:
        from resources.lib import control
        control.infoDialog('Streaming-Anbieter nicht gefunden', icon='WARNING')
        from resources.lib.indexers.navigator import navigator
        navigator()._endDirectory()
        return None
    provider_id = matches[0]['provider_id']
    return route({'info':'discover','tmdb_type':media,
                  'with_watch_providers':str(provider_id),
                  'watch_region':'DE','with_watch_monetization_types':'flatrate',
                  'sort_by':'popularity.desc'})
