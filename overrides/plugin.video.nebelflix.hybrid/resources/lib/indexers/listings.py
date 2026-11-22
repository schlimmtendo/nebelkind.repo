

#2021-07-15
# edit 2026-06-25

import sys, os, datetime
import json
from urllib.parse import urlencode
from resources.lib.requestHandler import cRequestHandler
from resources.lib import control
from resources.lib import log_utils
if int(control.getKodiVersion()) >= 20: from resources.lib.listitem import ListItemInfoTag

_params = dict(control.parse_qsl(sys.argv[2].replace('?', ''))) if len(sys.argv) > 1 else dict()

#TV-Film
# {"genres":[{"id":10759,"name":"Action & Adventure"},{"id":16,"name":"Animation"},{"id":35,"name":"Komödie"},{"id":80,"name":"Krimi"},{"id":99,"name":"Dokumentarfilm"},{"id":18,"name":"Drama"},{"id":10751,"name":"Familie"},{"id":10762,"name":"Kids"},{"id":9648,"name":"Mystery"},{"id":10763,"name":"News"},{"id":10764,"name":"Reality"},{"id":10765,"name":"Sci-Fi & Fantasy"},{"id":10766,"name":"Soap"},{"id":10767,"name":"Talk"},{"id":10768,"name":"War & Politics"},{"id":37,"name":"Western"}]}

_genresTv = [{"id":10759,"name":"Action & Abenteuer"},{"id":16,"name":"Animation"},{"id":35,"name":"Komödie"},{"id":80,"name":"Krimi"},{"id":18,"name":"Drama"},{"id":10751,"name":"Familie"},
             {"id":10762,"name":"Kids"},{"id":9648,"name":"Mystery"},{"id":10764,"name":"Reality"},{"id":10765,"name":"Sci-Fi & Fantasy"},{"id":10768,"name":"War & Politics"},
             {"id":37,"name":"Western"}]

_genresMovie = [{"id":28,"name":"Action"},{"id":12,"name":"Abenteuer"},{"id":16,"name":"Animation"},{"id":35,"name":"Komödie"},{"id":80,"name":"Krimi"},{"id":18,"name":"Drama"},
                {"id":10751,"name":"Familie"},{"id":14,"name":"Fantasy"},{"id":36,"name":"Historie"},{"id":27,"name":"Horror"},{"id":10402,"name":"Musik"},{"id":9648,"name":"Mystery"},
                {"id":10749,"name":"Liebesfilm"},{"id":878,"name":"Science Fiction"},{"id":10770,"name":"TV-Film"},{"id":53,"name":"Thriller"},{"id":10752,"name":"Kriegsfilm"},{"id":37,"name":"Western"}]

class listings:
    def __init__(self):
        self.URL = 'https://api.themoviedb.org/3/discover'
        from resources.lib.tmdb import cTMDB
        self.api_key = control.getSetting('api.tmdb') or cTMDB().api_key
        self.lang = 'de'
        self.list = []
        self.total_pages = 0
        self.query = ''
        self.media_type = ''
        #self.year_params = 'release_date.gte=%s-01-01&release_date.lte=%s-12-31&with_release_type=2|3&without_genres=%s&sort_by=popularity.desc'
        #self.year_params = 'primary_release_year=%s&with_release_type=2|3&without_genres=%s&sort_by=vote_count.desc' #&sort_by= vote_average.desc / popularity.desc / vote_count
        self.genres_params = ''
        self.popular_link = ''
        self.datetime = datetime.datetime.utcnow()

    def get(self, params):
        try:
            if params.get('next_pages'):
                next_pages = int(params.get('next_pages'))
            else:
                next_pages = 1

            self.media_type = params.get('media_type')
            url = params.get('url')

            if url.startswith('catalogue/'):
                params['presorted'] = True

            # Special URL keywords with specific TMDB discover parameters
            if url == 'neu':
                # Neu: Digital/Physical/TV releases (type 4|5|6) in last 30 days
                from datetime import datetime, timedelta
                today = datetime.today()
                fromday = today - timedelta(days=90)
                # url = 'with_release_type=4|5|6&release_date.gte=%s&release_date.lte=%s&sort_by=release_date.desc&vote_count.gte=20' % (
                #     fromday.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
                url = 'primary_release_date.gte=%s&primary_release_date.lte=%s&sort_by=primary_release_date.desc&vote_count.gte=20' % (
                    fromday.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
                params['presorted'] = True
                params.update({'filter_providers': True, 'digital_released': True, 'sort_by_date': True})
            elif url == 'imkino':
                # Im Kino: Theatrical releases (premiere + theatrical) in last 60 days
                from datetime import datetime, timedelta
                today = datetime.today()
                fromday = today - timedelta(days=60)
                url = 'with_release_type=2|3&release_date.gte=%s&release_date.lte=%s&sort_by=release_date.desc&vote_count.gte=20' % (
                    fromday.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
                params['presorted'] = True
                params.update({'cinema_only': True, 'sort_by_date': True})
            elif url == 'trend':
                # Im Trend: Digital releases in last 90 days, sorted by popularity
                from datetime import datetime, timedelta
                today = datetime.today()
                fromday = today - timedelta(days=90)
                url = 'with_release_type=4|5|6&release_date.gte=%s&release_date.lte=%s&sort_by=popularity.desc&vote_count.gte=20' % (
                    fromday.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
                params['presorted'] = True
                params.update({'digital_released': True})
            elif url == 'topbewertet':
                # Top bewertet: Top 100 (5 TMDB pages), daily shuffle, paged display
                url = 'vote_count.gte=1000&sort_by=vote_average.desc'
                params['presorted'] = True
                all_movies = []
                for p in range(1, 6):
                    page_list, _ = self._call(url, append_to_response='page=%s' % p)
                    all_movies.extend(page_list)
                import random
                seed = '%s_topbewertet' % self.datetime.strftime('%Y-%m-%d')
                random.Random(seed).shuffle(all_movies)
                page_size = 20
                start = (next_pages - 1) * page_size
                list = all_movies[start:start + page_size]
                total_pages = (len(all_movies) + page_size - 1) // page_size
            elif url == 'trending':
                # Heute im Trend: echter trending/{typ}/day-Endpunkt (in _call gebaut), schon nach Popularitaet sortiert
                params['presorted'] = True

            # Genre: fetch 2 TMDB pages per user page (compensates for
            # client-side filtering by votes/rating/CJK which removes many entries)
            # then shuffle with a daily seed so the page shows fresh variety each day
            if 'with_genres=' in url and not url.startswith('catalogue/'):
                tmdb_page = (next_pages - 1) * 2 + 1
                list1, total_pages = self._call(url, append_to_response='page=%s' % tmdb_page)
                list2, _ = self._call(url, append_to_response='page=%s' % (tmdb_page + 1))
                list = list1 + list2
                import random
                seed = '%s_%s_%s' % (self.datetime.strftime('%Y-%m-%d'), url, next_pages)
                random.Random(seed).shuffle(list)
                total_pages = (total_pages + 1) // 2
            else:
                append_to_response = 'page=%s' % next_pages
                list, total_pages = self._call(url, append_to_response=append_to_response)

            params.update({'list': list})
            params.update({'next_pages': next_pages})
            params.update({'total_pages': total_pages})
            params.update({'media_type': self.media_type})
            import xbmc
            xbmc.log('[xShip-Listings] url_key=%s list_type=%s len=%d' % (
                params.get('url', '?'), type(list[0]).__name__ if list else '?', len(list)), xbmc.LOGINFO)
            if url.startswith('catalogue/'):
                from resources.lib.skin_catalogue import directory
                return directory(params)
            if self.media_type == 'movie':
                from resources.lib.indexers import movies
                movies.movies().getDirectory(params)
            else:
                from resources.lib.indexers import tvshows
                tvshows.tvshows().getDirectory(params)
            return self.list
        except Exception as e:
            import xbmc
            xbmc.log('[xShip-Listings] get() error: %s' % e, xbmc.LOGERROR)
            import traceback
            xbmc.log('[xShip-Listings] %s' % traceback.format_exc(), xbmc.LOGERROR)

    def movieYears(self):
        year = int(self.datetime.strftime('%Y'))
        decade_start = year - (year % 10)
        for d in range(decade_start, 1940, -10):
            self.list.append({'name': '%ser' % d, 'url': str(d), 'image': 'years.png', 'action': 'movieYearsByDecade'})
        self.media_type = 'movie'
        self.addDirectory(self.list)
        return self.list

    def movieYearsByDecade(self, params):
        decade = int(params.get('url'))
        year = int(self.datetime.strftime('%Y'))
        without = '99,10762,10767,10766,16' # doku, kids, Talk, Soap, Animation
        end_year = min(decade + 9, year)
        for i in range(end_year, decade - 1, -1):
            self.list.append({'name': str(i), 'url': 'primary_release_year=%s&with_release_type=2|3&without_genres=%s&sort_by=vote_count.desc' % (str(i), without), 'image': 'years.png', 'action': 'listings'})
        self.media_type = 'movie'
        self.addDirectory(self.list)
        return self.list

    def movieGenres(self):
        without = '99,16'
        self.media_type = 'movie'
        for i in _genresMovie:
            if i['id'] == 16: without = '99'
            self.list.append({'name': i['name'], 'url': 'with_genres=%s&without_genres=%s&sort_by=vote_count.desc' % (str(i['id']), without), 'image': 'genres.png', 'action': 'listings'})
        self.addDirectory(self.list)
        return self.list

    def tvGenres(self):
        without = '99,10762,10767,10766,16' # doku, kids, Talk, Soap, Animation
        self.media_type = 'tv'
        for i in _genresTv:
            if i['id'] == 16 or i['id'] == 10762: without = '99,10767,10766'
            self.list.append({'name': i['name'], 'url': 'with_genres=%s&without_genres=%s&sort_by=vote_count.desc' % (str(i['id']), without), 'image': 'genres.png', 'action': 'listings'})
        self.addDirectory(self.list)
        return self.list

    def movieProviders(self):
        self.media_type = 'movie'
        for p in self._getProviders('movie'):
            self.list.append({'name': p['provider_name'], 'url': 'with_watch_providers=%s&watch_region=DE&sort_by=popularity.desc' % p['provider_id'], 'image': 'most-popular.png', 'action': 'listings'})
        self.addDirectory(self.list)
        return self.list

    def tvProviders(self):
        self.media_type = 'tv'
        for p in self._getProviders('tv'):
            self.list.append({'name': p['provider_name'], 'url': 'with_watch_providers=%s&watch_region=DE&sort_by=popularity.desc' % p['provider_id'], 'image': 'most-popular.png', 'action': 'listings'})
        self.addDirectory(self.list)
        return self.list

    def _getProviders(self, media_type):
        # Streaming-Anbieter (DE) ueber TMDB watch/providers holen, nach DE-Anzeigeprioritaet sortiert.
        # watch_region=DE filtert auf in DE verfuegbare Anbieter; Fehler -> leere Liste (Menue bleibt nutzbar).
        try:
            url = 'https://api.themoviedb.org/3/watch/providers/%s?api_key=%s&language=de-DE&watch_region=DE' % (media_type, self.api_key)
            oRequestHandler = cRequestHandler(url, ignoreErrors=True)
            oRequestHandler.cacheTime = 60 * 60 * 24    # 1 Tag (wie Genres/Discover)
            response = oRequestHandler.request()
            providers = json.loads(response).get('results', [])
            providers.sort(key=lambda p: p.get('display_priorities', {}).get('DE', 999))
            return providers
        except Exception as e:
            log_utils.log('[listings] _getProviders() Fehler: %s' % e, log_utils.LOGERROR)
            return []

    def tvYears(self):
        year = int(self.datetime.strftime('%Y'))
        decade_start = (year // 10) * 10
        for d in range(decade_start, 1940, -10):
            self.list.append({'name': '%ser' % d, 'url': str(d), 'image': 'years.png', 'action': 'tvYearsByDecade'})
        self.media_type = 'tv'
        self.addDirectory(self.list)
        return self.list

    def tvYearsByDecade(self, params):
        year = int(self.datetime.strftime('%Y'))
        decade = int(params.get('url'))
        without = '99,10762,10767,10766' # doku, kids, Talk, Soap
        end = min(decade + 9, year)
        for i in range(end, decade - 1, -1):
            self.list.append({'name': str(i), 'url': 'first_air_date_year=%s&without_genres=%s&sort_by=vote_count.desc' % (str(i), without), 'image': 'years.png', 'action': 'listings'})
        self.media_type = 'tv'
        self.addDirectory(self.list)
        return self.list


    def _call(self, url, append_to_response=''):
        import xbmc
        xbmc.log('[xShip-Listings] _call start: media_type=%s url=%s' % (self.media_type, url[:60]), xbmc.LOGINFO)
        if url.startswith('catalogue/'):
            endpoint = url[len('catalogue/'):]
            url = 'https://api.themoviedb.org/3/' + endpoint
            url += ('&' if '?' in url else '?') + urlencode({'api_key': self.api_key, 'language': 'de-DE', 'region': 'DE'})
            if append_to_response:
                url += '&' + append_to_response
        elif url == 'trending':
            # Trending: eigener Endpunkt (nicht discover) -> keine discover-Filter (vote_count/release_type/without_genres)
            url = 'https://api.themoviedb.org/3/trending/%s/day?api_key=%s&language=de-DE&region=DE' % (self.media_type, self.api_key)
            if append_to_response:
                url += '&%s' % append_to_response
        else:
            url = '%s/%s?api_key=%s&language=de-DE&region=DE&include_adult=false&include_video=false&%s' % (self.URL, self.media_type, self.api_key, url)
            if 'vote_count.gte' not in url: url += '&vote_count.gte=20'
            if 'with_release_type' in url and 'release_date.lte' not in url:
                url += '&release_date.lte=%s' % self.datetime.strftime('%Y-%m-%d')
            if not 'without_genres' in url: url += '&without_genres=99' # doku
            if append_to_response:
                url += '&%s' % append_to_response
        from resources.lib import release_policy
        from urllib.parse import urlsplit, parse_qsl, urlunsplit
        parsed=urlsplit(url);pieces=parsed.path.split('/3/',1)
        endpoint=pieces[-1]
        endpoint,query,preview=release_policy.query_policy(endpoint,dict(parse_qsl(parsed.query)))
        url=urlunsplit((parsed.scheme,parsed.netloc,'/3/'+endpoint,urlencode(query),''))
        oRequestHandler = cRequestHandler(url, ignoreErrors=True)
        oRequestHandler.cacheTime = 60 * 60 * 24    # 1 Tag
        response = oRequestHandler.request()
        try:
            status= oRequestHandler.getStatus()
            if int(status) != 200:
                log_utils.log('[listings] _call() TMDB-Abfrage fehlgeschlagen fuer Url "%s" Statuscode: %s'
                              % (url, status), log_utils.LOGERROR)
                return [], 0
        except Exception as e:
            log_utils.log('[listings] _call() TMDB-Abfrage "%s"  Fehler: %s'
                          % (url, e), log_utils.LOGERROR)
            return [], 0

        try:
            data = json.loads(response)
        except (ValueError, TypeError):
            log_utils.log('TMDB Discover Key %s... fehlgeschlagen (kein JSON)' % self.api_key[:8], log_utils.LOGWARNING)
            return [], 0
        # if 'status_code' in data and data['status_code'] == 34:
        if 'status_code' in data and data['status_code'] in (7, 10, 34):
            log_utils.log('TMDB Discover Key %s... abgelehnt (status %s)' % (self.api_key[:8], data['status_code']), log_utils.LOGWARNING)
            return [], 0

        if endpoint.startswith('collection/'):
            data=dict(data,results=sorted(data.get('parts') or [],key=lambda r:r.get('release_date') or '9999'),total_pages=1)
        list = []
        for i in data['results']:
            if release_policy.visible(i,preview):list.append(i)

        return list, data['total_pages']


    def addDirectory(self, items):
        if items is None or len(items) == 0:
            control.idle()
            sys.exit()
        sysaddon = sys.argv[0]
        syshandle = int(sys.argv[1])
        addonPoster, addonFanart, addonThumb, artPath = control.addonFanart(), control.addonFanart(), control.addonThumb(), control.artPath()

        for i in items:
            try:
                name = i['name']
                if self.media_type == 'movie':
                    if 'primary_release_year' in i['url']:
                        plot = 'Filme aus dem Jahr:  %s' % name
                    else:
                        plot = 'Filme aus der Kategorie:  %s' % name
                else:
                    plot = 'Serien aus der Kategorie:  %s' % name
# TODO
                try:
                    poster = os.path.join(artPath, i['image'])
                    thumb = os.path.join(artPath, i['image'])
                    if not os.path.isfile(poster):
                        poster = 'https://raw.githubusercontent.com/xsupdater/xsupdater/backup/logos/media/%s' % i['image']
                        thumb = 'https://raw.githubusercontent.com/xsupdater/xsupdater/backup/logos/media/%s' % i['image']
                except:
                    thumb = addonThumb
                    poster = addonPoster

                url = '%s?action=%s' % (sysaddon, i['action'])
                url += '&media_type=%s' % self.media_type
                try:
                    url += '&url=%s' % control.quote_plus(i['url'])
                except:
                    pass

                item = control.item(label=name, offscreen=True)
                # item.setArt({'icon': thumb, 'thumb': thumb})
                item.setArt({'thumb': thumb, 'poster': poster})
                if not addonFanart is None: item.setArt({'fanart': addonFanart})

                #  -> gesehen/ungesehen im cm und "Keine Informationen verfügbar" ausblenden (abhängig vom Skin)
                if int(control.getKodiVersion()) <= 19:
                    item.setInfo('video', {'overlay': 4, 'plot': plot})
                else:
                    info_tag = ListItemInfoTag(item, 'video')
                    info_tag.set_info({'overlay': 4, 'plot': plot})
                item.setIsFolder(True)

                control.addItem(handle=syshandle, url=url, listitem=item, isFolder=True)
            except:
                pass

        control.content(syshandle, 'videos')
        control.plugincategory(syshandle, control.addonVersion)
        control.endofdirectory(syshandle, cacheToDisc=True)

