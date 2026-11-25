# -*- coding: utf-8 -*-
"""Internal NebelFlix next-episode metadata; no external Up Next dependency."""
import hashlib
import json
from datetime import date
from urllib.parse import urlencode


from resources.lib.tmdb import cTMDB


def _number(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _stable_id(show_id, season, episode):
    raw = '%s:%s:%s' % (show_id, season, episode)
    return int(hashlib.sha1(raw.encode('utf-8')).hexdigest()[:7], 16)


def _aired(row):
    air_date = row.get('air_date') or row.get('premiered')
    if not air_date:
        return False  # no scheduled date: never autoplay unaired/missing data
    try:
        return date.fromisoformat(air_date[:10]) <= date.today()
    except (ValueError, TypeError):
        return False


def _episode_in_season(rows, requested):
    return next((row for row in rows if _number(row.get('episode_number')) == requested), None)


def next_episode(current):
    """Return (provider metadata, plugin URL) or None for no confirmed next episode."""
    show_id = _number(current.get('tmdb_id'))
    season = _number(current.get('season'))
    episode = _number(current.get('episode'))
    if not show_id or season < 1 or episode < 1 or current.get('mediatype') == 'movie':
        return None
    api = cTMDB()
    season_info = api.get_meta_seasons(show_id, season, advanced='true') or {}
    rows = season_info.get('episodes') or []
    current_row = _episode_in_season(rows, episode)
    next_season = season
    next_row = _episode_in_season(rows, episode + 1)
    if next_row is None:
        max_seasons = _number(current.get('number_of_seasons'))
        # If the max season is unknown, test only the immediately following season.
        if max_seasons and season >= max_seasons:
            return None
        next_season = season + 1
        season_info = api.get_meta_seasons(show_id, next_season, advanced='true') or {}
        rows = season_info.get('episodes') or []
        next_row = _episode_in_season(rows, 1)
    if next_row is None or not _aired(next_row):
        return None
    next_number = _number(next_row.get('episode_number'))
    if not next_number:
        return None
    episode_title = next_row.get('name') or 'Episode %s' % next_number
    show_title = current.get('title') or current.get('tvshowtitle') or ''
    if not show_title:
        return None
    next_meta = {key: current[key] for key in (
        'title', 'originaltitle', 'systitle', 'year', 'imdb_id', 'imdbnumber',
        'tmdb_id', 'tvdb_id', 'aliases', 'mediatype', 'number_of_seasons',
        'poster', 'fanart', 'clearlogo', 'plot', 'originallanguage') if key in current}
    next_meta.update(title=show_title, season=next_season, episode=next_number,
                     number_of_episodes=len(rows), playcount=0, select='2' if current.get('_autoplay_queue') else '1',
                     premiered=next_row.get('air_date') or '')
    next_meta['_autoplay_queue']=bool(current.get('_autoplay_queue'))
    next_meta['plot'] = next_row.get('overview') or current.get('plot') or ''
    plugin_url = 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode({
        'action': 'play',
        'sysmeta': json.dumps(next_meta, ensure_ascii=False, separators=(',', ':'))
    })
    id_current = _number((current_row or {}).get('id')) or _stable_id(show_id, season, episode)
    id_next = _number(next_row.get('id')) or _stable_id(show_id, next_season, next_number)
    art = {'thumb': ('https://image.tmdb.org/t/p/w500' + next_row['still_path'])
           if next_row.get('still_path') else next_meta.get('poster') or '',
           'poster': next_meta.get('poster') or '',
           'fanart': next_meta.get('fanart') or ''}
    art['tvshow.poster'] = art['poster']
    art['tvshow.fanart'] = art['fanart']
    item = {'episodeid': id_next, 'tvshowid': show_id,
            'season': next_season, 'episode': next_number,
            'title': episode_title, 'showtitle': show_title,
            'plot': next_row.get('overview') or '', 'art': art,
            'firstaired': next_row.get('air_date') or '',
            'playcount': 0, 'runtime': _number(next_row.get('runtime')) * 60}
    return {'current_episode': {'episodeid': id_current, 'tvshowid': show_id},
            'next_episode': item, 'play_url': plugin_url}
