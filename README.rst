**Fuzzy Retroarch thumbnail downloader**
========================================

In Retroarch, when you use the manual scanner to get non-standard games or hacks in playlists, thumbnails often fail to download. 

This program, for each game label on a playlist, downloads the 'most similar' image to display the image in retroarch.

It has several options to fit unusual labels, but you can just run it to get the most restrictive default. It will ask for the CFG, playlist and system if they're not provided.

Example:
 ``libretro-fuzz --no-subtitle --rmspaces --before '_'``
 
 The Retroplay WHDLoad set has labels like ``MonkeyIsland2_v1.3_0020`` after a manual scan. These labels don't have subtitles, don't have spaces, and all the metadata is not separated from the name by parenthesis. Select the playlist that contains those whdloads and the system name ``Commodore - Amiga`` to download from the libretro amiga thumbnails.

Note that the system name you download from doesn't have to be the same as the playlist name.

If the thumbnail server contains games from multiple releases for the system (like ``ScummVM``), be careful using extra options since it is easy to end up with 'slightly wrong' covers.

Example:
 ``libretro-fuzz --no-meta --no-merge``
 
 After downloading ``ScummVM`` thumbnails (and not before, to minimize false positives), we'd like to try to pickup a few covers from ``DOS`` thumbnails and skip download if there a risk of mixing thumbnails from ``DOS`` and ``ScummVM`` for a single game.
 Choose the ScummVM playlist and DOS system name, and covers would be downloaded with risk of false positives: CD vs floppy covers, USA vs Japan covers, or another platform vs DOS.

Because of this increased risk of false positives with options, the default is to count everything except hack metadata as part of the match, and the default pre-selected system name to be the same as the playlist name, which is safest.

False positives will then mostly be from the thumbnail server not having a single thumbnail of the game, and the program selecting the best match it can which is still good enough to pass the similarity test. Common false positives from this are sequels or prequels, or different releases, most often regions/languages.

Example:
  ``libretro-fuzz --no-subtitle --rmspaces --before '_' --filter '[Ii]shar*'``
  
  The best way to solve these issues is to upload the right cover to the respective libretro-thumbnail subproject with the correct name of the game variant, even if yours is slightly different (for instance, because it is a hack), as long as it is more similar than another game in the series or variant, it will be chosen. Then you can redownload just the affected thumbnails with a filter, in this example, the Ishar series in the WHDLoad playlist.


**Usage: libretro-fuzz [OPTIONS] [CFG]**

Arguments:
  [CFG]  Path to the retroarch cfg file. If not provided, asked from the user.
  [default: ~/.config/retroarch/retroarch.cfg]

Options:
  --playlist TEXT       Playlist name to download thumbnails for. If not
                        provided, asked from the user.
  --system TEXT         Directory in the server to download thumbnails. If not
                        provided, asked from the user.
  --filter TEXT         Filename glob filter for game labels in the playlist,
                        you can add this option more than once. This is the
                        only way to force a refresh from inside the program if
                        the thumbnails already exist in the cache.
  --no-merge            Disables missing thumbnails download for a label if
                        there is at least one in cache to avoid mixing
                        thumbnails from different server directories on
                        repeated calls. No effect if called with filter since
                        filters delete every match before download.
  --no-fail             Always download for any score. Best used with filter.
  --no-meta             Ignores () delimited metadata and may cause false
                        positives. Forced with --before.
  --hack                Matches [] delimited metadata and may cause false
                        positives, Best used if the hack has thumbnails.
                        Ignored with --before.
  --no-subtitle         Ignores subtitles, ' - ' or ': ' style. Best used if
                        the playlist labels have no subtitles. Note that ':'
                        can only occur in local labels, not on libretro names,
                        so that only matches a long local label to a short
                        name on the server, and in that case you should first
                        try without this option, since long names are more
                        common on the server.
  --rmspaces            Instead of uniquifying spaces in normalization, remove
                        them, for playlists with no spaces in the labels.
  --before TEXT         Use only the part of the label before TEXT to match.
                        TEXT may not be inside of a parenthesis of any kind,
                        may cause false positives but some labels do not have
                        traditional separators. Forces metadata to be ignored.
  --install-completion  Install completion for the current shell.
  --show-completion     Show completion for the current shell, to copy it or
                        customize the installation.
  --help                Show this message and exit.



To install the program, type on the cmd line
+---------------------+-------------------------------------------------------------------------------------+
| Linux               | ``pip3 install https://github.com/i30817/libretrofuzz/archive/master.zip``          |
+---------------------+-------------------------------------------------------------------------------------+
| Windows             | ``python -m pip install https://github.com/i30817/libretrofuzz/archive/master.zip`` |
+---------------------+-------------------------------------------------------------------------------------+
| Run in Windows      | ``python -m libretro-fuzz...``                                                      |
+---------------------+-------------------------------------------------------------------------------------+
 
 In windows, you'll want to check the option to “Add Python to PATH” when installing python, to be able to execute the script from any path of the cmd line.

