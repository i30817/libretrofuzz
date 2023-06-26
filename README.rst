**Fuzzy Retroarch thumbnail downloader**
========================================

In Retroarch, when you use the manual scanner to get nonstandard games or hacks in playlists, thumbnails often fail to download.

These programs, for each game label on a playlist, download the most similar named image to display in retroarch.

There are several options to fit unusual labels and increase fuzziness, but you can just run them to get a adequate default that is neither too strict or lax.

If you still want more thumbnails, using ``libretro-fuzz --min 80 --delay 15 --delay-after 15`` works (smaller ``--min`` increases fuzz), with some delays introduced to check if you want to keep the game selected for the thumbnails. If you prefer only exact matches, use ``--min 100``.

If you use ``libretro-fuzz``, it will download for a single playlist by asking for the playlist and system if they're not provided.
If you use ``libretro-fuzzall``, it will download for all playlists with standard libretro names, and will skip custom playlists.

Besides those differences, if no retroarch.cfg is provided, both programs try to use the default retroarch.cfg.

If `chafa <https://github.com/hpjansson/chafa>`_ is installed, the program will display new thumbnails of a game, with gray border for images already in use and with green border for new images. Chafa works better with a recent release and on a `sixel <https://en.wikipedia.org/wiki/Sixel>`_ or `kitty <https://sw.kovidgoyal.net/kitty/graphics-protocol/>`_ compatible shell.

Example:
 | ``libretro-fuzz --system 'Commodore - Amiga' --before '_'``

 The Retroplay WHDLoad set has labels like ``MonkeyIsland2_v1.3_0020`` after a manual scan. These labels *often* don't have subtitles (but not always) and all the metadata is not separated from the name by brackets. Select the playlist that contains those whdloads to download from the libretro amiga thumbnails.

Note that the system name you download from doesn't have to be the same as the playlist name.

If the thumbnail server contains games from multiple releases for the system (like ``ScummVM``), be careful using extra options since it is easy to end up with 'slightly wrong' covers.

Example:
 ``libretro-fuzz --no-meta --no-merge``

 After downloading ``ScummVM`` thumbnails (and not before, to minimize false positives), we'd like to try to pickup a few covers from ``DOS`` thumbnails and skip download if there a risk of mixing thumbnails from ``DOS`` and ``ScummVM`` for a single game.
 Choose the ScummVM playlist and DOS system name, and covers would be downloaded with risk of false positives: CD vs floppy covers, USA vs Japan covers, or another platform vs DOS.

Because of this increased risk of false positives with options, the default is to count everything except hack metadata as part of the match and the default pre-selected system name to be the same as the playlist name, which is safest.

A common scenario is the thumbnail server not having a single thumbnail of the game, and the program selecting the best match it can which is still good enough to pass, like a sequel, prequel, or different release, most often regions/languages. It's not recommended to use ``--min`` less than 90 without ``--filter`` to a specific game, or at least ``--delay/--delay-after`` to be able to cancel.

Example:
  ``libretro-fuzz --system 'Commodore - Amiga' --before '_' --filter '[Ii]shar*'``

  The best way to solve these issues is to upload the right cover to the respective libretro-thumbnail subproject with the correct name of the game variant. Then you can redownload just the updated thumbnails with a label, in this example, because of ``--filter``, the Ishar series in the WHDLoad playlist would redownload because the glob used matches all names that start with 'Ishar' or 'ishar'.

To debug why a game is not being matched, SHORT=1 before the command will display the simplified names checked for similarity.

libretro-fuzzall/libretro-fuzz [OPTIONS] [CFG]
  :CFG:                 Path to the retroarch cfg file. If not default, asked from the user.

                        Linux default:   ``~/.config/retroarch/retroarch.cfg``

                        Windows default: ``%APPDATA%/RetroArch/retroarch.cfg``

                        MacOS default:   ``~/Library/Application Support/RetroArch/config/retroarch.cfg``

  --playlist <NAME libretro-fuzz only>
                        | Playlist name with labels used for thumbnail fuzzy matching.
                        | If not provided, asked from the user.
  --system <NAME libretro-fuzz only>
                        | Directory name in the server to download thumbnails.
                        | If not provided, asked from the user.
  --delay-after SECS    | Seconds after download to skip replacing thumbnails, enter continues.
                        | No-op with ``--no-image``.
                        | [1<=x<=60]
  --delay SECS          | Seconds to skip thumbnails download, enter continues.
                        | [1<=x<=60]
  --filter GLOB         | Restricts downloads to game labels globs - not paths - in the playlist, can be used multiple times.
                        | Resets thumbnails, ``--filter '*'`` redownloads all.
  --min SCORE           | 0=any, 100≃equal, 90=default. No-op with ``--no-fail``.
                        | [default: 90; 0<=x<=100]
  --no-fail             Download any score. Equivalent to ``--min 0``.
  --no-image            Don't show images even with chafa installed.
  --no-merge            | Disables missing thumbnails download for a label if there is at least one in cache to avoid mixing thumbnails from different server directories on repeated calls.
                        | No-op with ``--filter``.
  --no-meta             | Ignores () delimited metadata.
                        | May cause false positives.
                        | Forced with ``--before``.
  --hack                | Matches [] delimited metadata, best used if the hack has thumbnails.
                        | May cause false positives.
                        | Ignored with ``--before``.
  --before TEXT         | Use only the part of the label before TEXT to match.
                        | TEXT may not be inside of brackets of any kind.
                        | May cause false positives.
                        | Forces ignoring metadata.
  --address URL         | URL with libretro-thumbnails server, for local files:
                        | Go to RA thumbnail dir/git clone/unzip packs;
                        | Run ``'python3 -m http.server'`` in parent dir;
                        | Then use ``--address 'http://localhost:8000'``.
                        | [default: https://thumbnails.libretro.com]
  --dry-run             Print results only, no delay or image download.
  --limit GAMES         | Show a number of winners or losers.
                        | Any equal score winners can download images.
                        | [default: 1; x>=1]
  --verbose             Show failed matches.
  --install-completion  Install completion for the current shell.
  --show-completion     Show completion for the current shell, to copy it or customize the installation.
  --help                Show this message and exit.



To install the program, type on the cmd line

+----------------+---------------------------------------------------------------------------------------------+
| Latest release | ``pip install --force-reinstall libretrofuzz``                                              |
+----------------+---------------------------------------------------------------------------------------------+
| Current code   | ``pip install --force-reinstall https://github.com/i30817/libretrofuzz/archive/master.zip`` |
+----------------+---------------------------------------------------------------------------------------------+

In windows, you'll want to check the option to “Add Python to PATH” when installing python, to be able to install and execute the script from any path of the cmd line.
