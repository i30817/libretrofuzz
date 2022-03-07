**Fuzzy Retroarch thumbnail downloader**
========================================

In Retroarch, when you use the manual scanner to get non-standard games or hacks, thumbnails often fail to download. 

This program, for each game label on a playlist, downloads the 'most similar' image to display the image in retroarch.

It has several options to fit unusual names, but you can just run it to get the most restrictive default. It will ask for the CFG, playlist and system if they're not provided.

Example:
 ``libretrofuzz --no-subtitle --rmspaces --before '_'``
 
 The Retroplay WHDLoad set has names like ``MonkeyIsland2_v1.3_0020`` after a manual scan. These names don't have subtitles, don't have spaces, and all the metadata is not separated from the name by parenthesis. Then select the playlist that contains those whdloads and the system name ``Commodore - Amiga`` to download from the libretro amiga thumbnails.

Note that the system name you download from doesn't have to be the same as the playlist name.

If the thumbnail server contains games from multiple releases for the system (like ``ScummVM``), be careful using extra options since it's easy to end up with 'slightly wrong' covers.

Example:
 ``libretrofuzz --no-meta``
 
 After downloading ``ScummVM`` thumbnails (and not before, to minimize false positives), we'd like to try to pickup a few covers from ``DOS`` thumbnails.
 Choose the ScummVM playlist and DOS system name, and covers would be downloaded with risk of false positives: CD vs floppy covers, USA vs Japan covers, or another platform vs DOS.

Because of this the default is to count everything except square brackets delimited metadata as part of the match, and the default pre-selected system name to be the same as the playlist name, which is safest.

False positives will then mostly be from the thumbnail server not having a single thumbnail of the game, and the program selecting the best match it can. Common false positives from this are sequels or prequels, or from the server not having a different cover for releases and getting the 'wrong' one, most often regions.

**Usage: fuzzythumbnails [OPTIONS] [CFG]**

Arguments:
  [CFG]  Path to the retroarch cfg file. If not provided, asked from the user.
  [default: ~/.config/retroarch/retroarch.cfg]

Options:
  --playlist TEXT       Playlist name to download thumbnails for. If not
                        provided, asked from the user.
  --system TEXT         Directory in the server to download thumbnails. If not
                        provided, asked from the user.
  --no-merge            --no-merge disables thumbnails download if there is at
                        least one thumbnail type in cache for a name so it
                        avoids mixing thumbnail sources on repeated calls.
  --no-fail             --no-fail ignores the similarity score and may cause
                        more false positives, but can increase matches in sets
                        with nonstandard names.
  --no-meta             --no-meta ignores () delimited metadata and may cause
                        false positives, but can increase matches in sets with
                        nonstandard names.
  --hack                --hack matches [] delimited metadata and may cause
                        false positives, but can increase matches for hacks,
                        if the hack has thumbnails.
  --no-subtitle         --no-subtitle ignores the name after the last hyphen
                        or colon and before metadata and may cause false
                        positives, but can increase matches in sets with
                        incomplete names. Note that colon can only occur in
                        local unix names, not on libretro names.
  --rmspaces            Instead of uniquifying spaces in normalization, remove
                        them, --rmspaces may cause false negatives, but some
                        sets do not have spaces in the title. Best used with
                        --no-meta --no-subtitle.
  --before TEXT         Use only the part of the name before TEXT to match.
                        TEXT may not be inside of a parenthesis of any kind.
                        This operates only on the playlist names, implies
                        --no-meta and may cause false positives but some sets
                        do not have traditional separators.
  --install-completion  Install completion for the current shell.
  --show-completion     Show completion for the current shell, to copy it or
                        customize the installation.
  --help                Show this message and exit.


To install the program, type on the cmd line
 ``pip3 install git+https://github.com/i30817/libretrofuzz.git``

To upgrade the program
 ``pip3 install --upgrade git+https://github.com/i30817/libretrofuzz.git``

To remove
 ``pip3 uninstall libretrofuzz``
