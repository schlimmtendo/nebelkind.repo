"""Single source of truth for Nebelflix playback history.

Stored in Hybrid's existing playcount.db, independently of skin widget caches.
No content scans, network calls, or changes to favourites/playcount tables.
"""
import json
import sqlite3
import time
from contextlib import closing

TABLE = 'nebelflix_history'
FINISH_REMAINING = 15 * 60


def _database():
    from resources.lib.playcountDB import playcountDB
    return playcountDB


def connect(database=None):
    con = sqlite3.connect(database or _database(), timeout=8)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=8000')
    con.execute('''CREATE TABLE IF NOT EXISTS nebelflix_history (
        media TEXT NOT NULL, identity TEXT NOT NULL,
        tmdb_id TEXT NOT NULL DEFAULT '', imdb_id TEXT NOT NULL DEFAULT '',
        season INTEGER NOT NULL DEFAULT 0, episode INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL DEFAULT '', showtitle TEXT NOT NULL DEFAULT '',
        playback_name TEXT NOT NULL DEFAULT '',
        art TEXT NOT NULL DEFAULT '{}',
        position REAL NOT NULL DEFAULT 0, duration REAL NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(media, identity, season, episode)
    )''')
    con.commit()
    return con


def _string(value):
    return str(value or '').strip()


def key(meta):
    media = 'episode' if meta.get('mediatype') in ('episode', 'tvshow', 'tv') and meta.get('episode') is not None else 'movie'
    tmdb = _string(meta.get('tmdb_id'))
    imdb = _string(meta.get('imdb_id') or meta.get('imdb'))
    if tmdb.isdigit():
        identity = 'tmdb:' + tmdb
    elif imdb.startswith('tt') and imdb[2:].isdigit():
        identity = 'imdb:' + imdb
    else:
        # Avoid ambiguous/title-only rows that could delete unrelated titles.
        return None
    return media, identity, int(meta.get('season') or 0) if media == 'episode' else 0, int(meta.get('episode') or 0) if media == 'episode' else 0


def is_complete(position, duration, media='movie'):
    try:
        position, duration = float(position), float(duration)
    except (ValueError, TypeError):
        return False
    if duration <= 0 or position < 5:
        return False
    # Never mark a half-watched episode complete. Allow at most five minutes
    # of credits, and require at least 90 percent for short episodes.
    margin = min(5 * 60, duration * 0.1)
    return duration - position <= margin


def save(meta, position, duration, completed=False, database=None):
    ident = key(meta)
    if not ident:
        return False
    try:
        position, duration = max(0.0, float(position)), max(0.0, float(duration))
    except (ValueError, TypeError):
        return False
    if duration <= 0:
        return False
    finished = bool(completed or is_complete(position, duration, ident[0]))
    if position < 10 and not finished:
        return False
    art = {x: _string(meta.get(x)) for x in ('poster', 'fanart', 'clearlogo') if meta.get(x)}
    title = _string(meta.get('title') or meta.get('tvshowtitle'))
    if not title:
        return False
    with closing(connect(database)) as con:
        con.execute('''INSERT INTO nebelflix_history
            (media,identity,tmdb_id,imdb_id,season,episode,title,showtitle,playback_name,art,position,duration,completed,updated)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(media,identity,season,episode) DO UPDATE SET
            title=excluded.title,showtitle=excluded.showtitle,playback_name=excluded.playback_name,
            art=CASE WHEN excluded.art='{}' THEN nebelflix_history.art ELSE excluded.art END,
            position=excluded.position,duration=excluded.duration,
            completed=excluded.completed,updated=excluded.updated''',
            (ident[0], ident[1], _string(meta.get('tmdb_id')),
             _string(meta.get('imdb_id') or meta.get('imdb')), ident[2], ident[3], title,
             _string(meta.get('tvshowtitle') or (title if ident[0] == 'episode' else '')),
             _string(meta.get('history_name') or title), json.dumps(art), 0 if finished else position, duration,
             int(finished), time.time()))
        con.commit()
    return True


def find(meta, database=None):
    ident = key(meta)
    if not ident:
        return None
    with closing(connect(database)) as con:
        result = con.execute('SELECT * FROM nebelflix_history WHERE media=? AND identity=? AND season=? AND episode=?', ident).fetchone()
        return dict(result) if result else None


def entries(media='all', include_completed=False, limit=250, database=None):
    query = 'SELECT * FROM nebelflix_history WHERE 1=1'
    params = []
    if not include_completed:
        query += ' AND completed=0 AND position>0'
    if media in ('movie', 'episode'):
        query += ' AND media=?'; params.append(media)
    query += ' ORDER BY updated DESC LIMIT ?'; params.append(max(1,min(2000,int(limit))))
    with closing(connect(database)) as con:
        return [dict(r) for r in con.execute(query, params).fetchall()]


def remove(media, identity, season=0, episode=0, database=None):
    import re
    if media not in ('movie', 'episode') or not re.fullmatch(r'(?:tmdb:[0-9]+|imdb:tt[0-9]+)', identity or ''):
        return False
    try:
        season, episode = int(season), int(episode)
    except (TypeError, ValueError):
        return False
    if not (0 <= season <= 9999 and 0 <= episode <= 9999):
        return False
    with closing(connect(database)) as con:
        result = con.execute('DELETE FROM nebelflix_history WHERE media=? AND identity=? AND season=? AND episode=?',
                             (media, identity, int(season), int(episode)))
        con.commit()
        return result.rowcount > 0


def clear(media='all', database=None):
    if media not in ('all', 'movie', 'episode'):
        return 0
    with closing(connect(database)) as con:
        if media == 'all':
            cur = con.execute('DELETE FROM nebelflix_history')
        else:
            cur = con.execute('DELETE FROM nebelflix_history WHERE media=?', (media,))
        con.commit()
        return cur.rowcount


def forget_bookmark(playback_name):
    if not playback_name:
        return
    try:
        import hashlib
        from resources.lib import bookmarkDB
        key = hashlib.md5(playback_name.encode('utf-8')).hexdigest()
        # 'bookmarks' is Hybrid's actual storage (saved as bookmarks.pcl).
        bookmarkDB.remove_query(key, 'bookmarks')
    except Exception:
        pass


def remove_with_bookmark(media, identity, season=0, episode=0, database=None):
    with closing(connect(database)) as con:
        row = con.execute('SELECT playback_name FROM nebelflix_history WHERE media=? AND identity=? AND season=? AND episode=?',
                          (media, identity, int(season), int(episode))).fetchone()
    if not row:
        return False
    result = remove(media, identity, season, episode, database)
    if result:
        clear_legacy_entry(media, identity, season, episode, database)
        if database is None and row['playback_name']:
            with closing(connect(database)) as con:
                shared = con.execute('SELECT 1 FROM nebelflix_history WHERE playback_name=? LIMIT 1',
                                     (row['playback_name'],)).fetchone()
            if not shared:
                forget_bookmark(row['playback_name'])
    return result


def clear_with_bookmarks(media='all', database=None):
    rows = entries(media=media, include_completed=True, limit=2000, database=database)
    count = clear(media, database)
    if count and database is None:
        for row in rows:
            forget_bookmark(row.get('playback_name'))
    return count


def legacy_entries(database=None, limit=500):
    """Older Hybrid rows, kept visible only in the management dialog.

    Legacy resume_position can be a fractional value, so do not use it as an
    absolute number of seconds and do not use it for the new Continue widget.
    """
    results = []
    try:
        with closing(connect(database)) as con:
            for table, media in (('movie', 'movie'), ('episode', 'episode')):
                try:
                    columns = {r['name'] for r in con.execute('PRAGMA table_info(%s)' % table)}
                    required = {'title','name','tmdb_id','imdb_id','lastplayed'}
                    if not required.issubset(columns):
                        continue
                    optional = (', season, episode' if media == 'episode' else ', 0 AS season, 0 AS episode')
                    result = con.execute('SELECT title, name, tmdb_id, imdb_id, lastplayed' + optional +
                        ' FROM %s WHERE lastplayed IS NOT NULL ORDER BY lastplayed DESC LIMIT ?' % table, (limit,))
                    for row in result:
                        data = dict(row)
                        ident = ('tmdb:' + str(data['tmdb_id']) if str(data['tmdb_id'] or '').isdigit() else
                                 'imdb:' + str(data['imdb_id']) if str(data['imdb_id'] or '').startswith('tt') else '')
                        if not ident:
                            continue
                        data.update(identity=ident, media=media, legacy=True,
                                    season=int(data.get('season') or 0), episode=int(data.get('episode') or 0))
                        results.append(data)
                except sqlite3.DatabaseError:
                    continue
    except sqlite3.DatabaseError:
        pass
    return results


def clear_legacy_entry(media, identity, season=0, episode=0, database=None):
    """Only clear legacy last-played/resume columns; preserve watched counts."""
    if media not in ('movie', 'episode') or not identity.startswith(('tmdb:', 'imdb:')):
        return 0
    column = 'tmdb_id' if identity.startswith('tmdb:') else 'imdb_id'
    ident = identity.split(':', 1)[1]
    if not ident:
        return 0
    table = 'movie' if media == 'movie' else 'episode'
    sql = 'UPDATE %s SET lastplayed=NULL, resume_position=NULL, total_time=NULL WHERE %s=?' % (table, column)
    args = [ident]
    if table == 'episode':
        sql += ' AND season=? AND episode=?'; args.extend([int(season),int(episode)])
    with closing(connect(database)) as con:
        columns = {row['name'] for row in con.execute('PRAGMA table_info(%s)' % table)}
        # Fresh Kodi profiles can have the old schema before the normal migration.
        # Do not turn an otherwise successful targeted removal into an error.
        if not {'lastplayed', 'resume_position', 'total_time', column}.issubset(columns):
            return 0
        changed = con.execute(sql, args).rowcount
        con.commit()
        return changed


def backup_before_bulk(database=None):
    """Keep one replaceable backup generation; never remove it before replacement succeeds."""
    import os
    from pathlib import Path
    dbfile = Path(database or _database())
    destination = dbfile.with_name(dbfile.name + '.history-backup-latest')
    temporary = destination.with_name(destination.name + '.tmp')
    bookmark = dbfile.parent / 'bookmarks.pcl'
    bookmark_backup = bookmark.with_name(bookmark.name + '.history-backup-latest')
    bookmark_temp = bookmark_backup.with_name(bookmark_backup.name + '.tmp')
    try:
        with closing(connect(database)) as source, closing(sqlite3.connect(str(temporary))) as target:
            source.backup(target)
        if database is None and bookmark.exists():
            import shutil
            shutil.copy2(bookmark, bookmark_temp)
        os.replace(temporary, destination)
        if database is None:
            if bookmark_temp.exists():os.replace(bookmark_temp, bookmark_backup)
            elif bookmark_backup.exists():bookmark_backup.unlink()
        # Only our history backup files; unrelated settings backups stay intact.
        for original in ([dbfile, bookmark] if database is None else [dbfile]):
            latest = original.with_name(original.name + '.history-backup-latest')
            for old in original.parent.glob(original.name + '.history-backup-*'):
                if old != latest and old.is_file():old.unlink()
    finally:
        for pending in (temporary, bookmark_temp):
            if pending.exists():pending.unlink()
    return str(destination)


def clear_all_history_with_backup(database=None):
    """Optional deliberate reset of new AND old history; save DB first."""
    backup = backup_before_bulk(database)
    count = clear_with_bookmarks(database=database)
    legacy = legacy_entries(database=database, limit=100000)
    for row in legacy:
        clear_legacy_entry(row['media'], row['identity'], row['season'], row['episode'], database)
    if database is None:
        # Hybrid's legacy bookmark store is only resume points, and has been
        # copied alongside the DB backup before this explicit full reset.
        try:
            import os
            path = os.path.join(os.path.dirname(_database()), 'bookmarks.pcl')
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
    # A full reset includes legacy watched flags, even rows without a resume point.
    with closing(connect(database)) as con:
        tables={row['name'] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('movie','episode','tvshow','season'):
            if table not in tables:continue
            columns={row['name'] for row in con.execute('PRAGMA table_info('+table+')')}
            assignments=[name+'=NULL' for name in ('lastplayed','resume_position','total_time') if name in columns]
            if 'playcount' in columns:assignments.append('playcount=0')
            if assignments:con.execute('UPDATE '+table+' SET '+','.join(assignments))
        con.commit()
    return count, len(legacy), backup


def reset_watched_with_backup(database=None):
    """Deliberate watched-only reset; preserve partial playback and its bookmarks."""
    backup=backup_before_bulk(database)
    with closing(connect(database)) as con:
        con.execute('BEGIN IMMEDIATE')
        completed=[dict(row) for row in con.execute('SELECT * FROM nebelflix_history WHERE completed=1')]
        count=con.execute('DELETE FROM nebelflix_history WHERE completed=1').rowcount
        tables={row['name'] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('movie','episode','tvshow','season'):
            if table not in tables:continue
            columns={row['name'] for row in con.execute('PRAGMA table_info('+table+')')}
            if {'playcount','resume_position','total_time','lastplayed'}.issubset(columns):
                for raw in con.execute('SELECT rowid,* FROM '+table+' WHERE playcount>0').fetchall():
                    old=dict(raw)
                    if not is_complete(old.get('resume_position'),old.get('total_time'),table):continue
                    ident='tmdb:'+str(old['tmdb_id']) if old.get('tmdb_id') else 'imdb:'+str(old.get('imdb_id') or '')
                    partial=con.execute('SELECT 1 FROM nebelflix_history WHERE media=? AND identity=? AND season=? AND episode=? AND completed=0 AND position>0',(table,ident,int(old.get('season') or 0) if table=='episode' else 0,int(old.get('episode') or 0) if table=='episode' else 0)).fetchone()
                    if not partial:con.execute('UPDATE '+table+' SET resume_position=NULL,total_time=NULL,lastplayed=NULL WHERE rowid=?',(old['rowid'],))
            if 'playcount' in columns:con.execute('UPDATE '+table+' SET playcount=0 WHERE playcount>0')
        # Completed legacy resume rows must not reappear as half-watched after reset.
        # Ongoing rows are explicitly excluded, regardless of old playcount flags.
        for row in completed:
            table='movie' if row['media']=='movie' else 'episode'
            if table not in tables:continue
            columns={r['name'] for r in con.execute('PRAGMA table_info('+table+')')}
            field='tmdb_id' if row['tmdb_id'] else 'imdb_id';ident=row[field]
            if not {field,'resume_position','total_time','lastplayed'}.issubset(columns):continue
            query='UPDATE '+table+' SET resume_position=NULL,total_time=NULL,lastplayed=NULL WHERE '+field+'=?'
            args=[ident]
            if table=='episode':query+=' AND season=? AND episode=?';args.extend([row['season'],row['episode']])
            con.execute(query,args)
        con.commit()
    if database is None:
        for row in completed:forget_bookmark(row.get('playback_name'))
    return count,backup
