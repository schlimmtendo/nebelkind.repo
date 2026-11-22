"""On-demand detail enrichment: one bounded request, no widget fan-out."""
import json
import time
import uuid
from pathlib import Path
import xbmcvfs

ROOT=Path(xbmcvfs.translatePath('special://profile/addon_data/skin.nebelflix/'))/'details-cache'


def fields(data,media):
    def names(rows):return ', '.join(str(r.get('name') or '') for r in rows if r.get('name'))
    rating=float(data.get('vote_average') or 0)
    facts=[]
    if rating:facts.append('TMDB: %.1f / 10 (%s Bewertungen)' % (rating,data.get('vote_count') or 0))
    release=data.get('release_date') or data.get('first_air_date') or ''
    if release:facts.append('Startdatum: '+release)
    runtime=data.get('runtime') or next(iter(data.get('episode_run_time') or []),0)
    if runtime:facts.append('Laufzeit: %s Minuten' % runtime)
    if data.get('number_of_seasons'):facts.append('Staffeln: %s · Folgen: %s' % (data['number_of_seasons'],data.get('number_of_episodes') or '–'))
    genres=names(data.get('genres') or [])
    if genres:facts.append('Genres: '+genres)
    certification=''
    if media=='movie':
        for row in (data.get('release_dates') or {}).get('results',[]):
            if row.get('iso_3166_1')=='DE':
                certification=next((r['certification'] for r in row.get('release_dates',[]) if r.get('certification')), '')
    else:
        certification=next((r.get('rating','') for r in (data.get('content_ratings') or {}).get('results',[]) if r.get('iso_3166_1')=='DE'),'')
    if certification:facts.append('Altersfreigabe (DE): '+certification)
    credits=data.get('credits') or {}
    creators=names(data.get('created_by') or []) if media=='tv' else names([r for r in credits.get('crew',[]) if r.get('job')=='Director'])
    if creators:facts.append(('Serienidee: ' if media=='tv' else 'Regie: ')+creators)
    studios=names(data.get('production_companies') or [])
    if studios:facts.append('Studios: '+studios)
    countries=names(data.get('production_countries') or [])
    if countries:facts.append('Produktionsländer: '+countries)
    cast='\n\n'.join(str(r.get('name'))+('  ·  '+str(r['character']) if r.get('character') else '') for r in credits.get('cast',[])[:20] if r.get('name'))
    result=dict(Details='\n\n'.join(facts) or 'Keine weiteren Angaben verfügbar.',Cast=cast or 'Keine Besetzung hinterlegt.',Tagline=data.get('tagline') or '')
    directors=[r for r in credits.get('crew',[]) if r.get('job')=='Director' and r.get('id')]
    if directors:result['DirectorID']=str(directors[0]['id'])
    writers=names([r for r in credits.get('crew',[]) if r.get('job') in ('Writer','Screenplay')])
    if writers:result['Details']+='\n\nDrehbuch: '+writers
    collection=data.get('belongs_to_collection') or {}
    if collection.get('name'):
        result['Details']+='\n\nFilmreihe: '+collection['name']
        if str(collection.get('id') or '').isdigit():result['CollectionID']=str(collection['id'])
    result['Stars']='★'*max(0,min(5,int(rating/2+0.5)))+'☆'*(5-max(0,min(5,int(rating/2+0.5)))) if rating else ''
    result['RatingLabel']='%.1f / 10 · %s Stimmen' % (rating,data.get('vote_count') or 0) if rating else ''
    for index,person in enumerate([r for r in credits.get('cast',[]) if r.get('name')][:20],1):
        result['Actor%s.Name'%index]=str(person['name'])
        result['Actor%s.Role'%index]=str(person.get('character') or '')
        result['Actor%s.ID'%index]=str(person.get('id') or '')
        result['Actor%s.Image'%index]='https://image.tmdb.org/t/p/w185'+person['profile_path'] if person.get('profile_path') else ''
    result['CastReady']='true'
    return result


def fetch(item):
    media=item.get('Type');ident=str(item.get('TMDB') or '')
    if media not in ('movie','tv') or not ident.isdigit():return {}
    target=ROOT/(media+'-'+ident+'.json')
    try:
        saved=json.loads(target.read_text(encoding='utf8'))
        if saved.get('schema')==3 and time.time()-saved['time']<86400:return saved['fields']
    except (OSError,ValueError,KeyError,TypeError):pass
    from multitrailer import tmdb
    import requests
    response=requests.get(tmdb.BASE+'/'+media+'/'+ident,params=dict(api_key=tmdb.DEFAULT_TMDB_KEY,language='de-DE',append_to_response='credits,release_dates' if media=='movie' else 'credits,content_ratings'),timeout=(2,5))
    response.raise_for_status();data=response.json()
    if str(data.get('id'))!=ident:raise ValueError('Unexpected detail identity')
    result=fields(data,media)
    ROOT.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(dict(time=time.time(),fields=result,schema=3),ensure_ascii=False),encoding='utf8');temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
    return result
