#!/usr/bin/env python3
"""
Make a made-up WhatsApp for Mac data folder, and build the page's data from it, so the browser can be tried,
tested and photographed without anyone's real chats.

    python3 tools/make_demo.py                 # writes demo/container and demo/site, prints the page's address
    python3 tools/make_demo.py --dest /some/where --files 1200 --seed 7

Everything in it is invented: the chats, the people, the numbers (555-01xx numbers are reserved for fiction), the
captions. The pictures are generated gradients, the videos are ffmpeg test patterns, the voice notes are tones.
A few files are made to *look* large (sparse files: they take no disk space), so the size views have something to show.
"""
import argparse
import os
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import wave
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APPLE_EPOCH = build.APPLE_EPOCH

# name, folder (its id), is a group
CHATS = [
    ('Family group', '120363000000000101@g.us', True),
    ('عائلتي', '120363000000000102@g.us', True),
    ('Work team', '120363000000000103@g.us', True),
    ('Book club', '120363000000000104@g.us', True),
    ('Football friends ⚽', '120363000000000105@g.us', True),
    ('کۆمەڵی هاوڕێیان', '120363000000000106@g.us', True),
    ('University 2024', '120363000000000107@g.us', True),
    ('Travel plans', '120363000000000108@g.us', True),
    ('Mum', '15550100001@s.whatsapp.net', False),
    ('Dad', '15550100002@s.whatsapp.net', False),
    ('Sara (Design)', '15550100003@s.whatsapp.net', False),
    ('Neighbours', '120363000000000109@g.us', True),
    ('Cooking recipes', '120363000000000110@g.us', True),
    ('Project Alpha', '120363000000000111@g.us', True),
    (None, '15550100999@s.whatsapp.net', False),   # a number that is not in the address book
    (None, '0@s.whatsapp.net', False),             # statuses
]
# roughly how much of the library each chat holds
WEIGHT = [9, 6, 8, 3, 7, 4, 5, 4, 5, 3, 3, 2, 3, 4, 1, 2]

CAPTIONS = ['Dinner last night', 'Trip to the lake', 'Look at this!', 'Happy birthday 🎂', 'New sofa', 'Match day', 'Slides for Monday',
            'Recipe 👇', 'ڕۆژێکی خۆش', 'صباح الخير', 'Sunset from the roof', 'Final version', 'See you there', 'Photos from the wedding']
DOCS = [('Invoice_2026-03.pdf', 'pdf'), ('Lecture notes week 4.pdf', 'pdf'), ('Budget 2026.xlsx', 'xlsx'), ('Contract draft.docx', 'docx'),
        ('Meeting minutes.docx', 'docx'), ('Photos-archive.zip', 'zip'), ('Recipe book.pdf', 'pdf'), ('Timetable.pdf', 'pdf'),
        ('Reading list.txt', 'txt'), ('Slides.pptx', 'pptx')]


def png(w, h, pixel):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w): raw += bytes(pixel(x, y))
    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 6)) + chunk(b'IEND', b''))


def picture(rng, w=480, h=360):
    """A generated 'photo': a two-colour gradient, a few soft discs and some grain."""
    hue = [rng.randrange(40, 230) for _ in range(6)]
    discs = [(rng.randrange(w), rng.randrange(h), rng.randrange(30, 110), [rng.randrange(60, 255) for _ in range(3)]) for _ in range(rng.randrange(2, 6))]
    grain = rng.random() * 14

    def pixel(x, y):
        t = (x / w + y / h) / 2
        c = [hue[i] * (1 - t) + hue[i + 3] * t for i in range(3)]
        for cx, cy, r, col in discs:
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d < r:
                k = (1 - d / r) * 0.7
                c = [c[i] * (1 - k) + col[i] * k for i in range(3)]
        n = (rng.random() - 0.5) * grain
        return [max(0, min(255, int(v + n))) for v in c]
    return png(w, h, pixel)


def tone(path, rng, seconds):
    """A short ogg/opus voice-note-like clip: a few tone bursts (needs ffmpeg with libopus), else a wav of the same."""
    wav = path.with_suffix('.wav.tmp')
    rate = 16000
    with wave.open(str(wav), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
        import math
        frames = bytearray()
        f0 = rng.randrange(180, 420)
        for i in range(int(rate * seconds)):
            env = 0.5 * (1 + math.sin(i / rate * rng.uniform(2, 5)))
            frames += struct.pack('<h', int(9000 * env * math.sin(2 * math.pi * f0 * (1 + 0.3 * math.sin(i / rate * 3)) * i / rate)))
        f.writeframes(bytes(frames))
    ok = False
    if shutil.which('ffmpeg') and path.suffix == '.opus':
        ok = subprocess.run(['ffmpeg', '-v', 'quiet', '-y', '-i', str(wav), '-c:a', 'libopus', '-b:a', '16k', str(path)]).returncode == 0
    if not ok:
        path.write_bytes(wav.read_bytes())
    wav.unlink()


def video(path, rng, seconds):
    subprocess.run(['ffmpeg', '-v', 'quiet', '-y', '-f', 'lavfi', '-i', f'gradients=size=320x240:rate=15:duration={seconds}:speed=0.03:seed={rng.randrange(1000)}:n=3',
                    '-f', 'lavfi', '-i', f'sine=frequency={rng.randrange(200, 800)}:duration={seconds}',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '32', '-c:a', 'aac', '-b:a', '24k', '-shortest', str(path)], check=True)


def sticker(path, rng):
    tmp = path.with_suffix('.png.tmp')
    tmp.write_bytes(picture(rng, 256, 256))
    if not (shutil.which('ffmpeg') and subprocess.run(['ffmpeg', '-v', 'quiet', '-y', '-i', str(tmp), '-c:v', 'libwebp', str(path)]).returncode == 0):
        shutil.copy(tmp, path)
    tmp.unlink()


def make(dest, files=700, seed=2026):
    rng = random.Random(seed)
    container = dest / 'container'
    if container.exists(): shutil.rmtree(container)
    media = container / 'Message/Media'
    media.mkdir(parents=True)
    have_ffmpeg = bool(shutil.which('ffmpeg'))
    work = dest / '_bases'
    work.mkdir(exist_ok=True)

    # a few base videos, pictures and voice notes that are then reused, so some files are copies of others
    bases = {'image': [], 'video': [], 'voice': [], 'sticker': []}
    for i in range(90):
        p = work / f'i{i}.png'; p.write_bytes(picture(rng, rng.choice([480, 640]), rng.choice([360, 480]))); bases['image'].append((p, 0))
    if have_ffmpeg:
        for i in range(14):
            p = work / f'v{i}.mp4'; s = rng.randrange(3, 9); video(p, rng, s); bases['video'].append((p, s))
        for i in range(14):
            p = work / f's{i}.webp'; sticker(p, rng); bases['sticker'].append((p, 0))
    for i in range(30):
        p = work / f'a{i}.opus'; s = rng.randrange(3, 40); tone(p, rng, s); bases['voice'].append((p, s))

    con = sqlite3.connect(container / 'ChatStorage.sqlite')
    con.executescript('''
        create table ZWACHATSESSION (Z_PK integer primary key, ZCONTACTJID text, ZPARTNERNAME text);
        create table ZWAMESSAGE (Z_PK integer primary key, ZMESSAGEDATE real, ZISFROMME integer, ZCHATSESSION integer, ZTEXT text);
        create table ZWAMEDIAITEM (Z_PK integer primary key, ZMESSAGE integer, ZMEDIALOCALPATH text, ZTITLE text, ZMOVIEDURATION real);
    ''')
    for i, (name, jid, group) in enumerate(CHATS, 1):
        con.execute('insert into ZWACHATSESSION values (?,?,?)', (i, jid, name))
    now = time.time()
    n_msg = 0
    kinds = ['image'] * 46 + ['voice'] * 30 + ['video'] * 8 + ['document'] * 7 + ['sticker'] * 9
    if not have_ffmpeg: kinds = [k for k in kinds if k not in ('video', 'sticker')]
    used_pairs = []
    for n in range(files):
        ci = rng.choices(range(len(CHATS)), weights=WEIGHT)[0]
        name, jid, group = CHATS[ci]
        kind = rng.choice(kinds)
        age_days = int(rng.triangular(0, 800, 40))  # more recent than old
        when = now - age_days * 86400 - rng.randrange(86400)
        me = rng.random() < 0.3
        h1, h2 = '%x' % rng.randrange(16), '%x' % rng.randrange(16)
        folder = media / jid / h1 / h2
        folder.mkdir(parents=True, exist_ok=True)
        uid = '%032X' % rng.getrandbits(128)
        title, caption, dur = '', '', 0
        if kind == 'document':
            title, ext = rng.choice(DOCS)
            path = folder / f'{uid}.{ext}'
            size = rng.choice([40_000, 180_000, 900_000, 2_400_000])
            path.write_bytes(b'%PDF-1.4\n' + os.urandom(min(size, 60_000)) if ext == 'pdf' else os.urandom(min(size, 60_000)))
            if rng.random() < 0.10: os.truncate(path, rng.choice([60, 120, 250]) * 1_000_000)
        else:
            src, seconds = rng.choice(bases[kind])
            ext = src.suffix.lstrip('.')
            path = folder / f'{uid}.{ext}'
            earlier = [u for u in used_pairs if u[2] == kind]
            forward = kind in ('image', 'video') and bool(earlier) and rng.random() < 0.16
            if forward:
                # a forward: the very same bytes as a file already in another chat
                pick = rng.choice(earlier)
                size = pick[0].stat().st_size
                with pick[0].open('rb') as src_file:
                    path.write_bytes(src_file.read(2_000_000))
                if size >= 2_000_000: os.truncate(path, size)  # a sparse original is copied as sparse
                seconds = pick[1]
            else:
                # every other file is its own: a base picture, video or tone plus a few bytes only it has
                path.write_bytes(src.read_bytes() + os.urandom(rng.randrange(8, 400)))
            used_pairs.append((path, seconds, kind))
            dur = seconds
            if kind == 'video' and not forward and rng.random() < 0.45:
                # look large without taking the space: a sparse file
                os.truncate(path, rng.choice([18, 45, 120, 260, 420, 800]) * 1_000_000 + path.stat().st_size % 997)
            if kind == 'image' and rng.random() < 0.5:
                caption = rng.choice(CAPTIONS)
            if kind == 'video' and rng.random() < 0.4:
                caption = rng.choice(CAPTIONS)
        n_msg += 1
        con.execute('insert into ZWAMESSAGE values (?,?,?,?,?)', (n_msg, when - APPLE_EPOCH, 1 if me else 0, ci + 1, caption or None))
        con.execute('insert into ZWAMEDIAITEM values (?,?,?,?,?)', (n_msg, n_msg, f'Media/{jid}/{h1}/{h2}/{path.name}', title or None, float(dur) if dur else None))
    con.commit()
    con.close()
    shutil.rmtree(work)
    return container


def main(argv=None):
    ap = argparse.ArgumentParser(description='Make an invented WhatsApp data folder and build the page from it.')
    ap.add_argument('--dest', type=Path, default=ROOT / 'demo')
    ap.add_argument('--files', type=int, default=700)
    ap.add_argument('--seed', type=int, default=2026)
    args = ap.parse_args(argv)
    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    container = make(dest, args.files, args.seed)
    site = dest / 'site'
    site.mkdir(exist_ok=True)
    shutil.rmtree(site / 'thumbs', ignore_errors=True)  # previews and fingerprints of an earlier demo would not match
    (site / 'hashes.json').unlink(missing_ok=True)
    shutil.copy(ROOT / 'index.html', site / 'index.html')
    build.build(container, site)
    print('open', (site / 'index.html').as_uri())
    return 0


if __name__ == '__main__':
    sys.exit(main())
