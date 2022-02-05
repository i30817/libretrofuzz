  **Fuzzy Retroarch thumbnail downloader**

In Retroarch, when you use the manual scanner to get non-standard games or hacks, thumbnails often fail to download. 

This program, for each game label on a playlist, downloads the 'most similar' image, and creates a symlink (linux) or copy (windows) to display the image in retroarch.

It has several options to fit unusual game names, but you can just run it. It will ask for the CFG, playlist and system if they're not provided.

Example: the Retroplay WHDLoad set has names like ``MonkeyIsland2_v1.3_0020`` after a manual scan.

These names don't have subtitles, don't have spaces, and all the metadata is not separated from the name by parenthesis.

To get a good number of hits in this set you could call:
 ``libretrofuzz --no-subtitle --rmspaces --before '_'``

Or with probably more false positives (--no-meta is needed because it's applied to server thumbnail names too):
 ``libretrofuzz --no-subtitle --rmspaces --no-meta --no-fail``


Usage: fuzzythumbnails.py [OPTIONS] [CFG]

Arguments:
  [CFG]  Path to the retroarch cfg file.  [default:
         ~/.config/retroarch/retroarch.cfg]

Options:
  --playlist TEXT             Playlist name to download thumbnails for.
                              If not provided, asked from the user.
  --system TEXT               Directory in the server to download thumbnails
                              from. If not provided, asked from the user.
  --fail no-fail              Fail if the similarity score is under 100, --no-
                              fail may cause false positives, but can increase
                              matches in sets with nonstandard names.
                              [default: fail]
  --meta no-meta              Match name () delimited metadata, --no-meta may
                              cause false positives, but can increase matches
                              in sets with nonstandard names.  [default: meta]
  --dump no-dump              Match name [] delimited metadata, --dump may
                              cause false positives, but can increase matches
                              for hacks, if the hack has thumbnails.
                              [default: no-dump]
  --subtitle no-subtitle      Match name before the last hyphen, --no-subtitle
                              may cause false positives, but can increase
                              matches in sets with incomplete names.
                              [default: subtitle]
  --rmspaces no-rmspaces      Instead of uniquifying spaces in normalization,
                              remove them, --rmspaces may cause false
                              negatives, but some sets do not have spaces in
                              the title. Best used with --no-dump --no-meta
                              --no-subtitle.  [default: no-rmspaces]
  --before TEXT               Use only the part of the name before TEXT to
                              match. TEXT may not be inside of a parenthesis
                              of any kind. This operates only on the playlist
                              names, implies --nodump and --no-meta and may
                              cause false positives but some sets do not have
                              traditional separators.
  --install-completion        Install completion for the current shell.
  --show-completion           Show completion for the current shell, to copy
                              it or customize the installation.
  --help                      Show this message and exit.


To install the program, type on the cmd line
 ``pip3 install git+https://github.com/i30817/libretrofuzz.git``

To remove:
 ``pip3 uninstall libretrofuzz``
