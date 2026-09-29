# WhatsApp Media Browser

See what is using the space in **WhatsApp for Mac**. This is a small local page that lists every photo, video,
voice note, document and sticker WhatsApp has downloaded to your Mac, so you can find the chats and files that
take the most room. Filter by chat, type, size, date and sent or received; search by chat or file name; sort by
size or date; play voice notes right in the page.

Everything stays on your Mac. There is no server, no account and no network access: a Python script reads
WhatsApp's folder, and a single HTML page shows the result.

> Not affiliated with, endorsed by or connected to WhatsApp or Meta. "WhatsApp" is a trademark of its owner.

## How it works

1. `build.py` walks WhatsApp's downloaded-media folder and reads chat names and message dates from WhatsApp's own
   database (`ChatStorage.sqlite`). It opens the database **read-only** and never changes anything in WhatsApp's
   folder.
2. It makes small preview images for photos, stickers and videos and writes them to `thumbs/` next to the script.
   Previews already made are kept, so running it again is quick.
3. It writes `data.js`, the list the page reads.
4. `index.html` shows that list. Open it in your browser.

## Use it

Requirements: macOS with WhatsApp for Mac installed, and Python 3 (nothing to `pip install`). Photos and stickers
are previewed with the `sips` tool that ships with macOS. Video previews need
[ffmpeg](https://ffmpeg.org) (`brew install ffmpeg`); without it, videos show an icon instead of a picture.

```sh
git clone https://github.com/AvrazAkraye/whatsapp-media-browser.git
cd whatsapp-media-browser
python3 build.py        # about a minute for tens of thousands of files
open index.html
```

macOS may ask whether your terminal may access data from other apps, because WhatsApp's files live in its group
container (`~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared`). Allow it, or run the script again
after granting your terminal access in System Settings → Privacy & Security.

Run `python3 build.py` again whenever you want to refresh the list.

## Freeing space

Clicking a file opens it from WhatsApp's folder. If your browser does not let a local page open it, use **Copy
path** and open it in Finder (⌘⇧G). To delete media, use WhatsApp itself (Settings → Storage and data → Manage
storage) rather than removing files from the folder, so WhatsApp's own records stay correct.

## Privacy

`data.js` and `thumbs/` contain your chat names, file names and picture previews. They are listed in
`.gitignore` so they are never committed. **Do not publish them or share screenshots of the page without
hiding the chat names.**

## Limits

- It shows only media that has been downloaded to this Mac, not what is still on your phone or in WhatsApp's cloud.
- Chat names come from WhatsApp's database; a chat with no saved name is shown by its number or a short id.
- macOS only, and only the WhatsApp for Mac app.

## License

[MIT](LICENSE).
