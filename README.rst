**Fuzzy Retroarch thumbnail downloader**
========================================

In Retroarch, when you use the manual scanner to get non-standard games or hacks in playlists, thumbnails often fail to download.

These programs, for each game label on a playlist, download the most similar name image to display in retroarch.

There are several options to fit unusual labels, but you can just run them to get the most restrictive default.

If you use libretro-fuzz, it will ask for the playlist and system if they're not provided.

If you use libretro-fuzzall, it will attempt to match the playlist names to the server system names, and will skip custom playlist names.

Besides those differences, if no retroarch.cfg is provided, both programs try to use the default retroarch.cfg.

Example:
 ``libretro-fuzz --no-subtitle --before '_'``
 
 The Retroplay WHDLoad set has labels like ``MonkeyIsland2_v1.3_0020`` after a manual scan. These labels don't have subtitles and all the metadata is not separated from the name by brackets. Select the playlist that contains those whdloads and the system name ``Commodore - Amiga`` to download from the libretro amiga thumbnails.

Note that the system name you download from doesn't have to be the same as the playlist name.

If the thumbnail server contains games from multiple releases for the system (like ``ScummVM``), be careful using extra options since it is easy to end up with 'slightly wrong' covers.

Example:
 ``libretro-fuzz --no-meta --no-merge``
 
 After downloading ``ScummVM`` thumbnails (and not before, to minimize false positives), we'd like to try to pickup a few covers from ``DOS`` thumbnails and skip download if there a risk of mixing thumbnails from ``DOS`` and ``ScummVM`` for a single game.
 Choose the ScummVM playlist and DOS system name, and covers would be downloaded with risk of false positives: CD vs floppy covers, USA vs Japan covers, or another platform vs DOS.

Because of this increased risk of false positives with options, the default is to count everything except hack metadata as part of the match, and the default pre-selected system name to be the same as the playlist name, which is safest.

False positives will then mostly be from the thumbnail server not having a single thumbnail of the game, and the program selecting the best match it can which is still good enough to pass the similarity test. Common false positives from this are sequels or prequels, or different releases, most often regions/languages.

Example:
  ``libretro-fuzz --no-subtitle --before '_' --filter '[Ii]shar*'``
  
  The best way to solve these issues is to upload the right cover to the respective libretro-thumbnail subproject with the correct name of the game variant. Then you can redownload just the updated thumbnails with a label, in this example, the Ishar series in the WHDLoad playlist.

Usage:
  **libretro-fuzz [OPTIONS] [CFG]**
  
  **libretro-fuzzall [OPTIONS] [CFG]**
  
  libretro-fuzzall doesn't have the --playlist and --system options

Arguments:
  [CFG]  Path to the retroarch cfg file. If not default, asked from the user.
  
  [linux default:   ~/.config/retroarch/retroarch.cfg]
  
  [windows default: %APPDATA%/RetroArch/retroarch.cfg]
  
  [MacOS default:   ~/Library/Application Support/RetroArch/retroarch.cfg]

Options:
  --playlist NAME       Playlist name with labels used for thumbnail fuzzy
                        matching. If not provided, asked from the user.
  --system NAME         Directory name in the server to download thumbnails.
                        If not provided, asked from the user.
  --delay FLOAT         Delay in seconds before downloading game thumbnails to
                        allow the user to skip them.  [default: 1.5; 0<=x<=5]
  --filter GLOB         Restricts downloads to game labels globs - not paths -
                        in the playlist, can be used multiple times and
                        matches reset thumbnails, --filter '*' downloads all.
  --no-merge            Disables missing thumbnails download for a label if
                        there is at least one in cache to avoid mixing
                        thumbnails from different server directories on
                        repeated calls. No effect if called with --filter.
  --no-fail             Download any score. To restrict or retry use --filter.
  --no-meta             Ignores () delimited metadata and may cause false
                        positives. Forced with --before.
  --hack                Matches [] delimited metadata and may cause false
                        positives, Best used if the hack has thumbnails.
                        Ignored with --before.
  --no-subtitle         Remove subtitle after ' - ' or ': ' for mismatched
                        labels and server names. ':' can't occur in server
                        names, so if the server has 'Name\_ subtitle.png' and
                        not 'Name - subtitle.png' (uncommon), this option
                        doesn't help. To restrict or retry use --filter.
  --before TEXT         Use only the part of the label before TEXT to match.
                        TEXT may not be inside of brackets of any kind, may
                        cause false positives but some labels do not have
                        traditional separators. Forces metadata to be ignored.
  --verbose             Shows the failures, score and normalized local and
                        server names in output (score >= 100 is succesful).
  --install-completion  Install completion for the current shell.
  --show-completion     Show completion for the current shell, to copy it or
                        customize the installation.
  --help                Show this message and exit.



To install the program, type on the cmd line

+---------------------+-------------------------------------------------------------------------------------------------------+
| Linux               | ``pip install --force-reinstall https://github.com/i30817/libretrofuzz/archive/master.zip``           |
+---------------------+-------------------------------------------------------------------------------------------------------+
| Windows             | ``python -m pip install --force-reinstall https://github.com/i30817/libretrofuzz/archive/master.zip`` |
+---------------------+-------------------------------------------------------------------------------------------------------+

In windows, you'll want to check the option to “Add Python to PATH” when installing python, to be able to execute the script from any path of the cmd line.
