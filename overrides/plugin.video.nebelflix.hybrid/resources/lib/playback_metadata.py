"""Small playback metadata adapter; contains no API credentials."""
def get_play_meta(self, media_type, tmdb_id):
    """Playback needs identities and aliases, not credits/provider catalogues."""
    if media_type not in ('movie', 'tvshow') or not str(tmdb_id).isdigit():
        return None
    endpoint = 'movie' if media_type == 'movie' else 'tv'
    extra = 'alternative_titles' if endpoint == 'movie' else 'external_ids,alternative_titles'
    data = self._call(endpoint + '/' + str(tmdb_id), 'append_to_response=' + extra)
    if not isinstance(data, dict) or str(data.get('id')) != str(tmdb_id):
        return None
    data = dict(data, mediatype=media_type)
    if not data.get('origin_country'):
        data.pop('origin_country', None)
    if isinstance(data.get('external_ids'), dict):
        data['external_ids'] = dict(data['external_ids'])
        data['external_ids'].setdefault('imdb_id', '')
        data['external_ids'].setdefault('tvdb_id', '')
    return self._formatSuper(data, '', lightweight=True)
