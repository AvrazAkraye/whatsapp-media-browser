#!/usr/bin/env python3
"""
Build a local, filterable browser of WhatsApp for Mac's downloaded media.

Reads (never writes) WhatsApp's media folder and its ChatStorage.sqlite in
read-only mode, makes small preview images next to this script, and writes
data.js for index.html. Nothing leaves this machine. Run it again to refresh;
previews already made are kept.
"""
import json, os, sqlite3, subprocess, hashlib, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path.home() / 'Library/Group Containers/group.net.whatsapp.WhatsApp.shared'
MEDIA = ROOT / 'Message/Media'
DB = ROOT / 'ChatStorage.sqlite'
THUMBS = HERE / 'thumbs'
THUMBS.mkdir(exist_ok=True)
APPLE_EPOCH = 978307200  # seconds from 1970 to 2001-01-01

KIND = {}
for e in ['opus', 'm4a', 'aac', 'mp3', 'ogg', 'amr', 'wav']: KIND[e] = 'voice'
for e in ['jpg', 'jpeg', 'png', 'heic', 'gif']: KIND[e] = 'image'
for e in ['webp']: KIND[e] = 'sticker'
for e in ['mp4', 'mov', '3gp', 'm4v', 'avi', 'mkv']: KIND[e] = 'video'
for e in ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'txt', 'zip', 'rar', 'rtf', 'pages', 'key', 'numbers']: KIND[e] = 'document'
SKIP = {'thumb', 'mmsthumb', 'favicon'}

# ── chat names and message dates, from WhatsApp's own database ─────────────
names, by_path = {}, {}
try:
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    c = con.cursor()
    sessions = {pk: (jid or '', name or '') for pk, jid, name in c.execute('select Z_PK, ZCONTACTJID, ZPARTNERNAME from ZWACHATSESSION')}
    for jid, name in sessions.values():
        if jid and name: names.setdefault(jid, name)
    votes = {}
    for path, date, fromme, sess, title in c.execute(
        'select m.ZMEDIALOCALPATH, g.ZMESSAGEDATE, g.ZISFROMME, g.ZCHATSESSION, m.ZTITLE '
        'from ZWAMEDIAITEM m left join ZWAMESSAGE g on g.Z_PK = m.ZMESSAGE where m.ZMEDIALOCALPATH is not null'):
        if not path: continue
        rel = path[len('Media/'):] if path.startswith('Media/') else path
        folder = rel.split('/')[0]
        name = sessions.get(sess, ('', ''))[1]
        if name: votes.setdefault(folder, {}).setdefault(name, 0); votes[folder][name] = votes[folder].get(name, 0) + 1
        by_path[rel] = {'date': int(date + APPLE_EPOCH) if date else None, 'me': bool(fromme), 'title': title or ''}
    for folder, v in votes.items():
        names[folder] = max(v.items(), key=lambda x: x[1])[0]
    con.close()
except Exception as e:  # the page still works without names
    print('could not read chat names:', e)

def chat_name(folder):
    if folder in names: return names[folder]
    if folder.endswith('@s.whatsapp.net'):
        num = folder.split('@')[0]
        return f'+{num}' if num.isdigit() and num != '0' else 'Status / other'
    if folder.endswith('@g.us'): return 'Group ' + folder.split('@')[0][-6:]
    if folder.endswith('@lid'): return 'Chat …' + folder.split('@')[0][-5:]
    return folder

# ── the files ──────────────────────────────────────────────────────────────
items, jobs = [], []
for dirpath, _, files in os.walk(MEDIA):
    for fn in files:
        ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
        if ext in SKIP: continue
        full = Path(dirpath) / fn
        rel = str(full.relative_to(MEDIA))
        try: st = full.stat()
        except OSError: continue
        kind = KIND.get(ext, 'other')
        info = by_path.get(rel, {})
        tid = hashlib.sha1(rel.encode()).hexdigest()[:16]
        thumb = ''
        if kind in ('image', 'sticker', 'video'):
            out = THUMBS / f'{tid}.jpg'
            thumb = f'thumbs/{tid}.jpg'
            if not out.exists(): jobs.append((kind, full, out))
        items.append({
            'p': str(full), 'c': rel.split('/')[0], 'k': kind, 'x': ext, 's': st.st_size,
            'd': info.get('date') or int(st.st_mtime), 'm': 1 if info.get('me') else 0,
            't': thumb, 'n': info.get('title', '')[:120],
        })

def make(job):
    kind, src, out = job
    try:
        if kind == 'video':
            subprocess.run(['ffmpeg', '-v', 'quiet', '-y', '-ss', '1', '-i', str(src), '-frames:v', '1',
                            '-vf', 'scale=320:-2', '-q:v', '5', str(out)], timeout=30)
            if not out.exists():  # shorter than a second
                subprocess.run(['ffmpeg', '-v', 'quiet', '-y', '-i', str(src), '-frames:v', '1',
                                '-vf', 'scale=320:-2', '-q:v', '5', str(out)], timeout=30)
        else:
            subprocess.run(['sips', '-s', 'format', 'jpeg', '-Z', '320', str(src), '--out', str(out)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        pass

t0 = time.time()
with ThreadPoolExecutor(8) as pool:
    list(pool.map(make, jobs))
for it in items:
    if it['t'] and not (HERE / it['t']).exists(): it['t'] = ''

chats = {}
for it in items:
    ch = chats.setdefault(it['c'], {'id': it['c'], 'name': chat_name(it['c']), 'size': 0, 'n': 0})
    ch['size'] += it['s']; ch['n'] += 1
(HERE / 'data.js').write_text(
    'window.MEDIA=' + json.dumps(items, ensure_ascii=False, separators=(',', ':')) + ';\n'
    'window.CHATS=' + json.dumps(sorted(chats.values(), key=lambda x: -x['size']), ensure_ascii=False) + ';\n'
    f'window.BUILT={int(time.time())};\n', encoding='utf-8')
print(f'{len(items)} files, {sum(i["s"] for i in items)/1e9:.2f} GB, {len(chats)} chats, '
      f'{len(jobs)} previews made in {time.time()-t0:.0f}s')
