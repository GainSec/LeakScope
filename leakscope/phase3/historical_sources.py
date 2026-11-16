"""Metered archive index records; never request archived target URLs."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit, urlunsplit

from . import passive_sources as passive

DEFAULT_INDEX = 'CC-MAIN-2026-43'  # Official catalog verified 2026-11-09.
INDEX_PATTERN = re.compile(r'CC-MAIN-20\d{2}-(?:0[1-9]|[1-4]\d|5[0-3])\Z', re.ASCII)
MAX_PAGES = 1000000


def validate_index(value):
    if not isinstance(value,str) or not INDEX_PATTERN.fullmatch(value):
        raise ValueError('Use a Common Crawl archive ID such as CC-MAIN-2026-43')
    return value


def _time(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{14}',value):return None
    try:return datetime.strptime(value,'%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).isoformat()
    except ValueError:return None


def collect(source,seed,*,config,spend,context):
    if source=='wayback':return _wayback(seed,config=config,spend=spend,context=context)
    if source!='commoncrawl':raise ValueError('Unknown historical source')
    result={'candidates':[],'leads':[],'notes':[],'cacheable':True}
    if seed.get('kind')!='domain':return result
    name,root=passive._root(seed['value'])
    index=validate_index(context.get('commoncrawl_index',DEFAULT_INDEX))
    query={'url':root,'matchType':'domain','output':'json','pageSize':1,'fl':'url,timestamp,status,mime,digest'}
    base='https://index.commoncrawl.org/'+index+'-index?'
    cursor=context.get('cursor') or {}
    demo=bool(context.get('demo'))
    try:
        if cursor:
            if (not isinstance(cursor,dict) or set(cursor)-{'stage','page','pages','index'} or cursor.get('stage')!='page'
                or cursor.get('index')!=index or type(cursor.get('page')) is not int or type(cursor.get('pages')) is not int
                or not 0<=cursor['page']<cursor['pages']<=MAX_PAGES):raise ValueError('Invalid historical source cursor')
        elif not demo:
            meta=passive._request(base+urlencode({**query,'showNumPages':'true'}),source=source,config=config,spend=spend,context=context)
            if not isinstance(meta,dict) or type(meta.get('pages')) is not int or not 0<=meta['pages']<=MAX_PAGES:
                raise passive.LookupError('Common Crawl returned invalid pagination metadata')
            if meta['pages']==0:return result
            cursor={'stage':'page','page':0,'pages':meta['pages'],'index':index}
        else:cursor={'stage':'page','page':0,'pages':1,'index':index}
        # Save verified metadata before the next reservation, so budget pause
        # resumes the data page without repeating the metadata request.
        result['continuation']=dict(cursor)
        params={**query,'page':cursor['page']}
        if demo:params['limit']=1
        source_url=base+urlencode(params)
        raw=passive._request(source_url,source=source,config=config,spend=spend,context=context,text=True)
        if not isinstance(raw,str):raise passive.LookupError('Common Crawl returned invalid index text')
        by_name={};malformed=0
        for line in raw.splitlines():
            if not line.strip():continue
            try:row=json.loads(line)
            except (ValueError,RecursionError):malformed+=1;continue
            if not isinstance(row,dict):malformed+=1;continue
            value=row.get('url')
            if not isinstance(value,str) or len(value)>8192 or any(ord(c)<32 or ord(c)==127 for c in value):continue
            try:
                parsed=urlsplit(value)
                if parsed.scheme not in {'http','https'} or not parsed.hostname or '*' in parsed.hostname or parsed.username or parsed.password:continue
                host,_=passive._root(parsed.hostname)
                if not passive._in_scope(host,root):continue
                port=parsed.port
                authority=host+(':'+str(port) if port else '')
                archived=urlunsplit((parsed.scheme,authority,parsed.path[:1024] or '/','',''))
            except (ValueError,TypeError):continue
            proof=passive._proof('commoncrawl',source_url,root,'historical_crawl_url',30,
                observed_at=_time(row.get('timestamp')),collected_at=passive._now(),historical=True,archive=index,
                archived_url=archived,record_type='archived_url_index',
                status=passive._text(row.get('status'),16),mime=passive._text(row.get('mime'),120),
                excerpt='Common Crawl archived this URL; no target request was made and historical presence is not current reachability or ownership proof.')
            candidate=by_name.get(host)
            if candidate is None:
                if demo and by_name:break
                candidate={'domain':host,'relation':'historical','evidence':[],'identifiers':[]};by_name[host]=candidate
            if proof not in candidate['evidence'] and len(candidate['evidence'])<12:candidate['evidence'].append(proof)
        result['candidates']=list(by_name.values());result.pop('continuation',None)
        if malformed:result['notes'].append('Some malformed archive records were omitted; coverage is partial.')
        if demo:result['notes'].append('Demo queried one minimal archive page and retained at most one name; archive coverage is not exhaustive.')
        elif cursor['page']+1<cursor['pages']:result['continuation']={**cursor,'page':cursor['page']+1}
    except passive.CollectionBlocked as error:
        result['cacheable']=False;result['blocked']=error.reason
        result['continuation']=dict(cursor)
        result['notes'].append('Common Crawl '+error.reason+' reached; the verified archive page is retained for resume.')
    except passive.LookupError as error:
        result['cacheable']=False;result['failed']=True;result.pop('continuation',None)
        result['notes'].append('Common Crawl: '+str(error)+'; missing or unavailable archive records do not prove absence.')
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        result['cacheable']=False;result['failed']=True;result.pop('continuation',None)
        result['notes'].append('Common Crawl response or cursor could not be safely parsed; no automatic retry was made.')
    return result


WAYBACK_FIELDS = ('original', 'timestamp', 'statuscode', 'mimetype', 'digest')
WAYBACK_PAGE_ROWS = 3000


def _wayback(seed, *, config, spend, context):
    result = {'candidates': [], 'leads': [], 'notes': [], 'cacheable': True}
    if seed.get('kind') != 'domain':
        return result
    _, root = passive._root(seed['value'])
    base = 'https://web.archive.org/cdx/search/cdx?'
    query = {'url': root, 'matchType': 'domain', 'output': 'json', 'fl': ','.join(WAYBACK_FIELDS)}
    cursor = context.get('cursor') or {}
    demo = bool(context.get('demo'))
    try:
        if cursor:
            if (not isinstance(cursor, dict) or set(cursor) != {'stage', 'page', 'pages'}
                    or cursor['stage'] != 'page' or type(cursor['page']) is not int
                    or type(cursor['pages']) is not int
                    or not 0 <= cursor['page'] < cursor['pages'] <= MAX_PAGES):
                raise ValueError('Invalid Wayback page cursor')
        elif not demo:
            pages = passive._request(
                base + urlencode({**query, 'pageSize': 1, 'showNumPages': 'true'}),
                source='wayback', config=config, spend=spend, context=context,
            )
            if type(pages) is not int or not 0 <= pages <= MAX_PAGES:
                raise passive.LookupError('Wayback returned invalid pagination metadata')
            if pages == 0:
                result['notes'].append('Wayback returned no indexed pages; archive coverage does not establish absence.')
                return result
            cursor = {'stage': 'page', 'page': 0, 'pages': pages}
        if not demo:
            result['continuation'] = dict(cursor)
        params = {**query, 'limit': 1} if demo else {**query, 'pageSize': 1, 'page': cursor['page']}
        source_url = base + urlencode(params)
        data = passive._request(source_url, source='wayback', config=config, spend=spend, context=context)
        if not isinstance(data, list):
            raise passive.LookupError('Wayback returned an invalid capture list')
        if data and (data[0] != list(WAYBACK_FIELDS) or len(data) > WAYBACK_PAGE_ROWS + 1):
            raise passive.LookupError('Wayback returned invalid fields or an oversized index page')
        candidates = {}
        malformed = 0
        for row in data[1:]:
            if not isinstance(row, list) or len(row) != len(WAYBACK_FIELDS) or any(not isinstance(value, str) for value in row):
                malformed += 1
                continue
            fields = dict(zip(WAYBACK_FIELDS, row))
            value = fields['original']
            if len(value) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in value):
                continue
            try:
                parsed = urlsplit(value)
                if parsed.scheme not in {'http', 'https'} or not parsed.hostname or '*' in parsed.hostname or parsed.username or parsed.password:
                    continue
                host, _ = passive._root(parsed.hostname)
                if not passive._in_scope(host, root):
                    continue
                port = parsed.port
                authority = host + (':' + str(port) if port else '')
                archived = urlunsplit((parsed.scheme, authority, parsed.path[:1024] or '/', '', ''))
            except (ValueError, TypeError):
                continue
            proof = passive._proof(
                'wayback', source_url, root, 'historical_crawl_url', 30,
                observed_at=_time(fields['timestamp']), collected_at=passive._now(), historical=True,
                archive='Wayback Machine', archived_url=archived, record_type='archived_url_index',
                status=fields['statuscode'][:16], mime=fields['mimetype'][:120],
                excerpt='Wayback indexed this archived URL. Capture dates describe historical records; current reachability and ownership require separate evidence.',
            )
            candidate = candidates.setdefault(host, {'domain': host, 'relation': 'historical', 'evidence': [], 'identifiers': []})
            if proof not in candidate['evidence'] and len(candidate['evidence']) < 12:
                candidate['evidence'].append(proof)
            if demo:
                break
        result['candidates'] = list(candidates.values())
        result.pop('continuation', None)
        if malformed:
            result['notes'].append('Some malformed Wayback capture rows were omitted; coverage is partial.')
        if not candidates:
            result['notes'].append('No matching Wayback capture names were retained; archive coverage does not establish absence.')
        if demo:
            result['notes'].append('Demo requested one archived capture and retained at most one name; archive coverage is partial.')
        elif cursor['page'] + 1 < cursor['pages']:
            result['continuation'] = {**cursor, 'page': cursor['page'] + 1}
    except passive.CollectionBlocked as error:
        result.update(cacheable=False, blocked=error.reason)
        if not demo:
            result['continuation'] = dict(cursor)
        result['notes'].append('Wayback ' + error.reason + ' reached; pending index work is retained for resume.')
    except passive.LookupError as error:
        result.update(cacheable=False, failed=True)
        result.pop('continuation', None)
        result['notes'].append('Wayback: ' + str(error) + '; missing or unavailable archive records do not establish absence.')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        result.update(cacheable=False, failed=True)
        result.pop('continuation', None)
        result['notes'].append('Wayback response or page cursor could not be safely parsed; no automatic retry was made.')
    return result
