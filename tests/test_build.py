"""
Tests for build.py, against a tiny invented WhatsApp folder made on the spot (no real data, no ffmpeg needed).

    python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402

E = build.APPLE_EPOCH


class Container:
    """A made-up WhatsApp data folder: a database and some media files."""

    def __init__(self, root):
        self.root = Path(root) / 'container'
        self.media = self.root / 'Message/Media'
        self.media.mkdir(parents=True)
        self.db = sqlite3.connect(self.root / 'ChatStorage.sqlite')
        self.db.executescript('''
            create table ZWACHATSESSION (Z_PK integer primary key, ZCONTACTJID text, ZPARTNERNAME text);
            create table ZWAMESSAGE (Z_PK integer primary key, ZMESSAGEDATE real, ZISFROMME integer, ZCHATSESSION integer, ZTEXT text);
            create table ZWAMEDIAITEM (Z_PK integer primary key, ZMESSAGE integer, ZMEDIALOCALPATH text, ZTITLE text, ZMOVIEDURATION real);
        ''')
        self.n = 0
        self.chats = {}

    def chat(self, jid, name):
        pk = len(self.chats) + 1
        self.chats[jid] = pk
        self.db.execute('insert into ZWACHATSESSION values (?,?,?)', (pk, jid, name))

    def file(self, jid, name, data=b'x', when=None, me=False, text=None, title=None, dur=None, size=None, register=True):
        d = self.media / jid / '1' / '2'
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(data)
        if size and size > len(data): os.truncate(p, size)
        if register:
            self.n += 1
            self.db.execute('insert into ZWAMESSAGE values (?,?,?,?,?)', (self.n, (when or 1_700_000_000) - E, int(me), self.chats.get(jid), text))
            self.db.execute('insert into ZWAMEDIAITEM values (?,?,?,?,?)', (self.n, self.n, f'Media/{jid}/1/2/{name}', title, dur))
        return p

    def close(self):
        self.db.commit()
        self.db.close()


def load(out):
    text = (Path(out) / 'data.js').read_text(encoding='utf-8')
    media = json.loads(text.split('window.MEDIA=')[1].split(';\nwindow.CHATS=')[0])
    chats = json.loads(text.split('window.CHATS=')[1].split(';\nwindow.INFO=')[0])
    info = json.loads(text.split('window.INFO=')[1].rstrip().rstrip(';'))
    return media, chats, info


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wmb-test-')
        self.c = Container(self.tmp)
        self.out = Path(self.tmp) / 'out'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_build(self, **kw):
        self.c.close()
        with mock.patch('builtins.print'):
            return build.build(self.c.root, self.out, thumbs=kw.pop('thumbs', False), **kw)

    def test_files_chats_and_names(self):
        self.c.chat('15550100001@s.whatsapp.net', 'Mum')
        self.c.chat('120363000000000101@g.us', None)
        self.c.file('15550100001@s.whatsapp.net', 'a.jpg', b'1' * 10)
        self.c.file('15550100001@s.whatsapp.net', 'b.opus', b'2' * 20)
        self.c.file('120363000000000101@g.us', 'c.mp4', b'3' * 30)
        self.c.file('15550100999@s.whatsapp.net', 'd.pdf', b'4' * 40, register=False)  # not in the database
        self.c.file('0@s.whatsapp.net', 'e.jpg', b'5', register=False)
        (self.c.media / '15550100001@s.whatsapp.net' / 'x.thumb').write_bytes(b'x')  # WhatsApp's own thumbnails are skipped
        (self.c.media / '15550100001@s.whatsapp.net' / '.hidden').write_bytes(b'x')
        r = self.run_build()
        media, chats, info = load(self.out)
        self.assertEqual(r['files'], 5)
        self.assertEqual(len(media), 5)
        names = {c['id']: c['name'] for c in chats}
        self.assertEqual(names['15550100001@s.whatsapp.net'], 'Mum')
        self.assertTrue(names['120363000000000101@g.us'].startswith('Group '))
        self.assertEqual(names['15550100999@s.whatsapp.net'], '+15550100999')
        self.assertEqual(names['0@s.whatsapp.net'], 'Status / other')
        self.assertEqual({m['k'] for m in media}, {'image', 'voice', 'video', 'document'})
        self.assertEqual(chats[0]['size'], max(c['size'] for c in chats))  # biggest first
        self.assertEqual(info['format'], build.FORMAT)
        self.assertEqual(sorted(chats, key=lambda c: -c['size']), chats)

    def test_a_group_takes_the_name_most_of_its_messages_say(self):
        self.c.chat('120363000000000101@g.us', 'Family')
        for i in range(3): self.c.file('120363000000000101@g.us', f'{i}.jpg', bytes([i]))
        self.run_build()
        _, chats, _ = load(self.out)
        self.assertEqual(chats[0]['name'], 'Family')

    def test_dates_direction_captions_titles_durations(self):
        self.c.chat('j@g.us', 'Team')
        self.c.file('j@g.us', 'a.mp4', b'v', when=1_750_000_000, me=True, text='Caption here', dur=12.6)
        self.c.file('j@g.us', 'b.pdf', b'd', when=1_760_000_000, title='Invoice.pdf')
        self.run_build()
        media, _, _ = load(self.out)
        by = {Path(m['p']).name: m for m in media}
        self.assertEqual(by['a.mp4']['d'], 1_750_000_000)
        self.assertEqual(by['a.mp4']['m'], 1)
        self.assertEqual(by['a.mp4']['z'], 'Caption here')
        self.assertEqual(by['a.mp4']['u'], 13)
        self.assertEqual(by['b.pdf']['m'], 0)
        self.assertEqual(by['b.pdf']['n'], 'Invoice.pdf')
        self.assertNotIn('z', by['b.pdf'])
        self.assertNotIn('u', by['b.pdf'])

    def test_copies_are_found_by_content_not_name_or_size_alone(self):
        self.c.chat('a@g.us', 'A'); self.c.chat('b@g.us', 'B')
        same = b'the very same bytes' * 50
        self.c.file('a@g.us', 'one.jpg', same)
        self.c.file('b@g.us', 'two.jpg', same)                       # a forward: identical
        self.c.file('b@g.us', 'three.jpg', b'X' + same[1:])          # same size, different content
        self.c.file('a@g.us', 'four.jpg', b'unique')
        r = self.run_build()
        media, _, info = load(self.out)
        by = {Path(m['p']).name: m for m in media}
        self.assertEqual(by['one.jpg']['h'], by['two.jpg']['h'])
        self.assertNotIn('h', by['three.jpg'])
        self.assertNotIn('h', by['four.jpg'])
        self.assertEqual((r['copies'], r['extra']), (1, len(same)))
        self.assertTrue(info['dupes'])

    def test_large_files_are_compared_by_samples_of_their_content(self):
        self.c.chat('a@g.us', 'A')
        big = build.FULL_HASH_MAX + 5_000_000
        head = b'HEAD-ONE' * 1000
        self.c.file('a@g.us', 'v1.mp4', head, size=big)
        self.c.file('a@g.us', 'v2.mp4', head, size=big)                  # identical, and sparse
        self.c.file('a@g.us', 'v3.mp4', b'HEAD-TWO' * 1000, size=big)   # same size, different start
        self.run_build()
        media, _, _ = load(self.out)
        by = {Path(m['p']).name: m for m in media}
        self.assertEqual(by['v1.mp4']['h'], by['v2.mp4']['h'])
        self.assertNotIn('h', by['v3.mp4'])

    def test_only_files_that_share_a_size_are_read(self):
        self.c.chat('a@g.us', 'A')
        self.c.file('a@g.us', 'a.jpg', b'aaa')
        self.c.file('a@g.us', 'b.jpg', b'bbbb')
        self.c.file('a@g.us', 'c.jpg', b'ccccc')
        with mock.patch.object(build, 'fingerprint', side_effect=AssertionError('read a file with a unique size')):
            self.run_build()

    def test_fingerprints_are_kept_between_runs(self):
        self.c.chat('a@g.us', 'A')
        self.c.file('a@g.us', 'a.jpg', b'twin')
        self.c.file('a@g.us', 'b.jpg', b'twin')
        self.c.db.commit()
        self.run_build()
        self.assertTrue((self.out / 'hashes.json').exists())
        self.c.db = sqlite3.connect(self.c.root / 'ChatStorage.sqlite')
        with mock.patch.object(build, 'fingerprint', side_effect=AssertionError('hashed again')):
            self.run_build()
        media, _, _ = load(self.out)
        self.assertEqual(len({m['h'] for m in media}), 1)

    def test_no_dupes_flag(self):
        self.c.chat('a@g.us', 'A')
        self.c.file('a@g.us', 'a.jpg', b'twin'); self.c.file('a@g.us', 'b.jpg', b'twin')
        self.run_build(dupes=False)
        media, _, info = load(self.out)
        self.assertTrue(all('h' not in m for m in media))
        self.assertFalse(info['dupes'])
        self.assertFalse((self.out / 'hashes.json').exists())

    def test_the_database_is_never_changed(self):
        self.c.chat('a@g.us', 'A')
        self.c.file('a@g.us', 'a.jpg', b'x')
        self.c.close()
        db = self.c.root / 'ChatStorage.sqlite'
        before = (hashlib.sha1(db.read_bytes()).hexdigest(), sorted(os.listdir(self.c.root)))
        with mock.patch('builtins.print'):
            build.build(self.c.root, self.out, thumbs=False)
        self.assertEqual((hashlib.sha1(db.read_bytes()).hexdigest(), sorted(os.listdir(self.c.root))), before)

    def test_works_without_a_database(self):
        self.c.file('15550100001@s.whatsapp.net', 'a.jpg', b'x', register=False)
        self.c.close()
        (self.c.root / 'ChatStorage.sqlite').unlink()
        with mock.patch('builtins.print'):
            r = build.build(self.c.root, self.out, thumbs=False)
        media, chats, _ = load(self.out)
        self.assertEqual(r['files'], 1)
        self.assertEqual(chats[0]['name'], '+15550100001')

    def test_a_missing_media_folder_says_what_to_do(self):
        shutil.rmtree(self.c.media)
        self.c.close()
        with self.assertRaises(SystemExit) as cm:
            build.build(self.c.root, self.out, thumbs=False)
        self.assertIn('--container', str(cm.exception))

    def test_no_working_fields_leak_into_the_data(self):
        self.c.chat('a@g.us', 'A')
        self.c.file('a@g.us', 'a.jpg', b'twin'); self.c.file('a@g.us', 'b.jpg', b'twin')
        self.run_build()
        media, _, _ = load(self.out)
        for m in media:
            self.assertFalse([k for k in m if k.startswith('_')])

    def test_names_with_odd_characters_survive(self):
        self.c.chat('a@g.us', 'عائلتي ⚽ "quotes" <b>')
        self.c.file('a@g.us', 'a.jpg', b'x', text='ڕۆژێکی خۆش </script>')
        self.run_build()
        text = (self.out / 'data.js').read_text(encoding='utf-8')
        _, chats, _ = load(self.out)
        self.assertEqual(chats[0]['name'], 'عائلتي ⚽ "quotes" <b>')
        # data.js is loaded by a <script src>, so a caption cannot close anything: it is JSON inside a JS file
        self.assertIn('</script>', text)  # present as data...
        self.assertTrue(text.startswith('window.MEDIA='))  # ...and the file is still only assignments

    def test_command_line(self):
        self.c.chat('a@g.us', 'A'); self.c.file('a@g.us', 'a.jpg', b'x')
        self.c.close()
        with mock.patch('builtins.print'):
            code = build.main(['--container', str(self.c.root), '--out', str(self.out), '--no-thumbs', '--no-dupes', '--jobs', '2'])
        self.assertEqual(code, 0)
        self.assertTrue((self.out / 'data.js').exists())
        self.assertFalse((self.out / 'thumbs').exists())


class DemoTest(unittest.TestCase):
    def test_the_demo_builds_and_is_invented(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
        import make_demo
        tmp = tempfile.mkdtemp(prefix='wmb-demo-')
        try:
            with mock.patch('builtins.print'):
                make_demo.main(['--dest', tmp, '--files', '80', '--seed', '3'])
            media, chats, _ = load(Path(tmp) / 'site')
            self.assertGreater(len(media), 70)
            self.assertTrue(any(m.get('h') for m in media) or len(media) < 100)
            for c in chats:
                if c['id'].endswith('@s.whatsapp.net'):
                    self.assertTrue(c['id'].startswith('1555010') or c['id'] == '0@s.whatsapp.net', c['id'])  # only fictional 555-01xx numbers
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
