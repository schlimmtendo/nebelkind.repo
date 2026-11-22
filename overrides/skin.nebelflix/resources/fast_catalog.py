"""Read-only TMDB catalog widgets without starting a Hybrid plugin interpreter.

Playback, search, personal lists and unknown addon routes remain with Hybrid.
Uses the installed metadata dependency's configuration; no keys are stored here.
"""
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse
import load_timing
import release_policy

CACHE={}
LOCK=threading.Lock()
ENDPOINT=re.compile(r'(?:collection/[0-9]+|trending/(?:movie|tv)/(?:day|week)|discover/(?:movie|tv)|'
                    r'(?:movie|tv)/(?:popular|top_rated|upcoming|now_playing|on_the_air|airing_today))$')


def route(url):
    parsed=urlparse(url)
    if parsed.scheme!='plugin' or parsed.netloc!='plugin.video.nebelflix.hybrid':
        return None
    query={key:values[0] for key,values in parse_qs(parsed.query).items()}
    if query.get('action')!='listings':
        return None
    catalog=urlparse(query.get('url',''))
    if not catalog.path.startswith('catalogue/'):
        return None
    endpoint=catalog.path[len('catalogue/'):]
    if not ENDPOINT.fullmatch(endpoint):
        return None
    allowed={'with_cast','with_crew','with_genres','without_genres','sort_by','with_watch_providers','watch_region',
             'with_watch_monetization_types','with_original_language','vote_count.gte',
             'vote_average.gte','primary_release_date.gte','primary_release_date.lte',
             'first_air_date.gte','first_air_date.lte','nf_upcoming'}
    params={key:values[0] for key,values in parse_qs(catalog.query).items() if key in allowed}
    params.update(language='de-DE',region='DE',include_adult='false',page=query.get('next_pages','1'))
    try:params['page']=max(1,min(500,int(params['page'])))
    except ValueError:return None
    media='movie' if '/movie' in endpoint or endpoint.startswith(('movie/','collection/')) else 'tv'
    return endpoint,params,media,query


def fetch(url):
    parsed=route(url)
    if parsed is None:
        return None
    endpoint,params,media,query=parsed
    endpoint,params,preview=release_policy.query_policy(endpoint,params)
    key=(endpoint,tuple(sorted(params.items())),preview)
    with LOCK:
        cached=CACHE.get(key)
        if cached and time.monotonic()-cached[0]<300:
            return cached[1]
    from multitrailer import tmdb
    import requests
    started=time.monotonic()
    try:
        parameters=dict(params,api_key=tmdb.DEFAULT_TMDB_KEY)
        response=requests.get(tmdb.BASE+'/'+endpoint,params=parameters,timeout=(2,4))
        response.raise_for_status()
        data=response.json()
        if isinstance(data,dict) and endpoint.startswith('collection/'):
            data=dict(data,results=sorted(data.get('parts') or [],key=lambda r:r.get('release_date') or '9999'),total_pages=1)
        if not isinstance(data,dict) or not isinstance(data.get('results'),list):
            raise ValueError('TMDB catalog response missing results')
        items=[]
        for result in data['results']:
            ident=result.get('id')
            title=result.get('title') or result.get('name')
            if not isinstance(ident,int) or not title or not release_policy.visible(result,preview):
                continue
            poster=result.get('poster_path') or ''
            backdrop=result.get('backdrop_path') or poster
            items.append(dict(Title=title,Plot=result.get('overview') or '',
                ReleaseDate=result.get('release_date') or result.get('first_air_date') or '',Upcoming=preview,
                Year=(result.get('release_date') or result.get('first_air_date') or '')[:4],
                Rating=str(result.get('vote_average') or ''),Type=media,TMDB=str(ident),
                DBType='movie' if media=='movie' else 'tvshow',Genres='',Clearlogo='',
                Poster=tmdb.IMG+'/w342'+poster if poster else '',
                Fanart=tmdb.IMG+'/w780'+backdrop if backdrop else '',
                Path='plugin://plugin.video.nebelflix.hybrid/?'+urlencode(
                    dict(info='details',tmdb_type=media,tmdb_id=ident))))
        value=(items,params['page']<min(int(data.get('total_pages') or 0),500))
        with LOCK:
            if len(CACHE)>=32:
                CACHE.pop(min(CACHE,key=lambda entry:CACHE[entry][0]))
            CACHE[key]=(time.monotonic(),value)
        return value
    finally:
        load_timing.record('skin','catalog.metadata',started,status='direct')
