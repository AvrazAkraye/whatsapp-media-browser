#!/usr/bin/env python3
"""
Build a local, filterable browser of WhatsApp for Mac's downloaded media.

Reads (never writes) WhatsApp's media folder and its ChatStorage.sqlite in
read-only mode, makes small preview images next to this script, and writes
data.js for index.html. Nothing leaves this machine. Run it again to refresh;
previews and file fingerprints already made are kept.

    python3 build.py                 # your WhatsApp for Mac, results next to this script
    python3 build.py --no-thumbs     # skip the preview images (much faster)
    python3 build.py --no-dupes      # skip looking for copies of the same file
    python3 build.py --open          # open the page when done
    python3 build.py --container DIR --out DIR   # another location (used by the demo and the tests)

Standard library only. Previews use `sips` (macOS) for pictures and ffmpeg, when installed, for videos.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONTAINER = Path.home() / 'Library/Group Containers/group.net.whatsapp.WhatsApp.shared'
APPLE_EPOCH = 978307200  # seconds from 1970 to 2001-01-01
FORMAT = 2  # bump when data.js changes shape, so index.html can tell an old one

KIND = {}
for e in ['opus', 'm4a', 'aac', 'mp3', 'ogg', 'oga', 'amr', 'wav', 'caf']: KIND[e] = 'voice'
for e in ['jpg', 'jpeg', 'png', 'heic', 'heif', 'gif', 'bmp', 'tif', 'tiff']: KIND[e] = 'image'
for e in ['webp']: KIND[e] = 'sticker'
for e in ['mp4', 'mov', '3gp', 'm4v', 'avi', 'mkv', 'webm']: KIND[e] = 'video'
for e in ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'txt', 'zip', 'rar', 'rtf', 'pages', 'key',
          'numbers', 'odt', 'ods', 'odp', 'epub', 'json', 'vcf']: KIND[e] = 'document'
SKIP = {'thumb', 'mmsthumb', 'favicon'}

FULL_HASH_MAX = 16 * 1024 * 1024  # up to this size a file is fingerprinted whole
SAMPLE = 1024 * 1024              # above it, four samples of this size: the start, two inner points and the end


# ── chat names, dates, captions and durations, from WhatsApp's own database ─
def read_database(db):
    """Return (names, by_path): chat names by folder or contact id, and what the database knows of each file."""
    names, by_path = {}, {}
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        c = con.cursor()
        sessions = {pk: (jid or '', name or '') for pk, jid, name in c.execute('select Z_PK, ZCONTACTJID, ZPARTNERNAME from ZWACHATSESSION')}
        for jid, name in sessions.values():
            if jid and name: names.setdefault(jid, name)
        votes = {}
        try:
            rows = list(c.execute(
                'select m.ZMEDIALOCALPATH, g.ZMESSAGEDATE, g.ZISFROMME, g.ZCHATSESSION, m.ZTITLE, g.ZTEXT, m.ZMOVIEDURATION '
                'from ZWAMEDIAITEM m left join ZWAMESSAGE g on g.Z_PK = m.ZMESSAGE where m.ZMEDIALOCALPATH is not null'))
        except sqlite3.OperationalError:  # another WhatsApp version: fewer columns, but the page still works
            rows = [r + (None, None) for r in c.execute(
                'select m.ZMEDIALOCALPATH, g.ZMESSAGEDATE, g.ZISFROMME, g.ZCHATSESSION, m.ZTITLE '
                'from ZWAMEDIAITEM m left join ZWAMESSAGE g on g.Z_PK = m.ZMESSAGE where m.ZMEDIALOCALPATH is not null')]
        for path, date, fromme, sess, title, text, dur in rows:
            if not path: continue
            rel = path[len('Media/'):] if path.startswith('Media/') else path
            folder = rel.split('/')[0]
            name = sessions.get(sess, ('', ''))[1]
            if name:
                votes.setdefault(folder, {}).setdefault(name, 0)
                votes[folder][name] += 1
            by_path[rel] = {
                'date': int(date + APPLE_EPOCH) if date else None, 'me': bool(fromme),
                'title': (title or '')[:120], 'text': (text or '').strip()[:200],
                'dur': int(round(dur)) if isinstance(dur, (int, float)) and dur > 0 else 0,
            }
        for folder, v in votes.items():
            names[folder] = max(v.items(), key=lambda x: x[1])[0]
        con.close()
    except Exception as e:  # the page still works without names
        print('could not read chat names:', e)
    return names, by_path


def chat_name(folder, names):
    if folder in names: return names[folder]
    if folder.endswith('@s.whatsapp.net'):
        num = folder.split('@')[0]
        return f'+{num}' if num.isdigit() and num != '0' else 'Status / other'
    if folder.endswith('@g.us'): return 'Group ' + folder.split('@')[0][-6:]
    if folder.endswith('@lid'): return 'Chat …' + folder.split('@')[0][-5:]
    return folder


# ── the files ──────────────────────────────────────────────────────────────
def scan(media, by_path, thumbs_dir, want_thumbs):
    """Walk the media folder. Returns (items, preview jobs)."""
    items, jobs = [], []
    for dirpath, _, files in os.walk(media):
        for fn in files:
            ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
            if ext in SKIP or fn.startswith('.'): continue
            full = Path(dirpath) / fn
            rel = str(full.relative_to(media))
            try: st = full.stat()
            except OSError: continue
            kind = KIND.get(ext, 'other')
            info = by_path.get(rel, {})
            tid = hashlib.sha1(rel.encode()).hexdigest()[:16]
            thumb = ''
            if want_thumbs and kind in ('image', 'sticker', 'video'):
                out = thumbs_dir / f'{tid}.jpg'
                thumb = f'thumbs/{tid}.jpg'
                if not out.exists(): jobs.append((kind, full, out))
            item = {
                'p': str(full), 'c': rel.split('/')[0], 'k': kind, 'x': ext, 's': st.st_size,
                'd': info.get('date') or int(st.st_mtime), 'm': 1 if info.get('me') else 0, 't': thumb,
                '_rel': rel, '_mt': st.st_mtime_ns,
            }
            if info.get('title'): item['n'] = info['title']
            if info.get('text'): item['z'] = info['text']
            if info.get('dur'): item['u'] = info['dur']
            items.append(item)
    return items, jobs


def make_preview(job):
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
        pass  # no ffmpeg, an unreadable file, a timeout: that file simply has no preview


# ── copies of the same file ────────────────────────────────────────────────
def fingerprint(path, size):
    """A short fingerprint: the whole file when it is small, else the size plus four samples of it."""
    h = hashlib.sha1(str(size).encode())
    with open(path, 'rb') as f:
        if size <= FULL_HASH_MAX:
            for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
        else:
            for off in (0, size // 3, 2 * size // 3, max(0, size - SAMPLE)):
                f.seek(off)
                h.update(f.read(SAMPLE))
    return h.hexdigest()[:12]


def find_copies(items, cache_file, jobs=8):
    """Give each file that has an identical twin the same `h`. Only files that share a size are read."""
    by_size = {}
    for it in items:
        if it['s'] > 0: by_size.setdefault(it['s'], []).append(it)
    candidates = [it for group in by_size.values() if len(group) > 1 for it in group]
    try: cache = json.loads(cache_file.read_text(encoding='utf-8'))
    except Exception: cache = {}
    fresh = {}
    todo = []
    for it in candidates:
        hit = cache.get(it['_rel'])
        if hit and hit[0] == it['s'] and hit[1] == it['_mt']:
            it['_fp'] = hit[2]
            fresh[it['_rel']] = hit
        else:
            todo.append(it)

    def work(it):
        try: it['_fp'] = fingerprint(it['p'], it['s'])
        except OSError: it['_fp'] = ''
    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(work, todo))
    for it in todo:
        if it.get('_fp'): fresh[it['_rel']] = [it['s'], it['_mt'], it['_fp']]
    try: cache_file.write_text(json.dumps(fresh, separators=(',', ':')), encoding='utf-8')
    except OSError: pass
    twins = {}
    for it in candidates:
        if it.get('_fp'): twins.setdefault(it['_fp'], []).append(it)
    copies = extra = 0
    for fp, group in twins.items():
        if len(group) < 2: continue
        for it in group: it['h'] = fp
        copies += len(group) - 1
        extra += (len(group) - 1) * group[0]['s']
    return copies, extra


# ── putting it together ────────────────────────────────────────────────────
def build(container, out, thumbs=True, dupes=True, jobs=8):
    media = container / 'Message/Media'
    out.mkdir(parents=True, exist_ok=True)
    thumbs_dir = out / 'thumbs'
    if thumbs: thumbs_dir.mkdir(exist_ok=True)
    if not media.is_dir():
        raise SystemExit(f'No WhatsApp media folder at {media}\nIs WhatsApp for Mac installed? Use --container to point at another location.')

    names, by_path = read_database(container / 'ChatStorage.sqlite')
    items, todo = scan(media, by_path, thumbs_dir, thumbs)

    t0 = time.time()
    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(make_preview, todo))
    for it in items:
        if it['t'] and not (out / it['t']).exists(): it['t'] = ''

    copies = extra = 0
    if dupes: copies, extra = find_copies(items, out / 'hashes.json', jobs)

    chats = {}
    for it in items:
        ch = chats.setdefault(it['c'], {'id': it['c'], 'name': chat_name(it['c'], names), 'size': 0, 'n': 0})
        ch['size'] += it['s']
        ch['n'] += 1
    for it in items:
        for k in ('_rel', '_mt', '_fp'): it.pop(k, None)
    info = {'format': FORMAT, 'built': int(time.time()), 'dupes': bool(dupes)}
    (out / 'data.js').write_text(
        'window.MEDIA=' + json.dumps(items, ensure_ascii=False, separators=(',', ':')) + ';\n'
        'window.CHATS=' + json.dumps(sorted(chats.values(), key=lambda x: -x['size']), ensure_ascii=False) + ';\n'
        'window.INFO=' + json.dumps(info) + ';\n', encoding='utf-8')
    line = (f'{len(items)} files, {sum(i["s"] for i in items) / 1e9:.2f} GB, {len(chats)} chats, '
            f'{len(todo)} previews made in {time.time() - t0:.0f}s')
    if dupes: line += f', {copies} copies of other files ({extra / 1e9:.2f} GB)'
    print(line)
    return {'files': len(items), 'chats': len(chats), 'copies': copies, 'extra': extra}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the WhatsApp media browser's data.")
    ap.add_argument('--container', type=Path, default=DEFAULT_CONTAINER, help="WhatsApp's data folder (default: WhatsApp for Mac's)")
    ap.add_argument('--out', type=Path, default=HERE, help='where data.js, thumbs/ and hashes.json go (default: next to this script)')
    ap.add_argument('--no-thumbs', action='store_true', help='do not make preview images')
    ap.add_argument('--no-dupes', action='store_true', help='do not look for copies of the same file')
    ap.add_argument('--jobs', type=int, default=8, help='parallel workers for previews and fingerprints (default 8)')
    ap.add_argument('--open', action='store_true', help='open the page in your browser when done')
    args = ap.parse_args(argv)
    build(args.container.expanduser(), args.out.expanduser().resolve(), not args.no_thumbs, not args.no_dupes, max(1, args.jobs))
    page = args.out.expanduser().resolve() / 'index.html'
    if args.open and page.exists():
        import webbrowser
        webbrowser.open(page.as_uri())
    return 0


if __name__ == '__main__':
    sys.exit(main())
