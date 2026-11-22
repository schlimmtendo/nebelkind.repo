"""TMDB premiere dates separate released catalogues from previews."""
from datetime import date, timedelta
from urllib.parse import urlparse, parse_qs


def upcoming(source):
    query=parse_qs(urlparse(source).query)
    inner=(query.get('url') or [source])[0]
    return '/upcoming' in inner or 'nf_upcoming=true' in inner


def release_date(item):
    value=item.get('release_date') or item.get('first_air_date') or item.get('ReleaseDate') or ''
    try:return date.fromisoformat(str(value)[:10])
    except (ValueError,TypeError):return None


def visible(item, preview=False, today=None):
    today=today or date.today()
    released=release_date(item)
    return bool(released and (released>today if preview else released<=today))


def query_policy(endpoint,params,today=None):
    today=today or date.today();params=dict(params)
    preview=endpoint.endswith('/upcoming') or params.pop('nf_upcoming','')=='true'
    media='tv' if endpoint.endswith('/tv') or endpoint.startswith('tv/') or '/tv/' in endpoint else 'movie'
    if preview:
        endpoint='discover/'+media
        field='first_air_date' if media=='tv' else 'primary_release_date'
        params[field+'.gte']=(today+timedelta(days=1)).isoformat()
        params.setdefault('sort_by',field+'.asc')
    elif endpoint.startswith('discover/'):
        field='first_air_date' if media=='tv' else 'primary_release_date'
        params[field+'.lte']=min(params.get(field+'.lte',today.isoformat()),today.isoformat())
    return endpoint,params,preview
