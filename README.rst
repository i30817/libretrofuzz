  **Fuzzy Retroarch thumbnail downloader**

In Retroarch, when you use the manual scanner to get non-standard games or hacks, thumbnails often fail to download. 

This program, for each game label on a playlist, downloads the 'most similar' image, and creates a symlink (linux) or copy (windows) to display the image in retroarch.

It has several options to fit unusual game names, but you can just run it. It will ask for the CFG, playlist and system if they're not provided.

Example: the Retroplay WHDLoad set has names like ``MonkeyIsland2_v1.3_0020`` after a manual scan.

These names don't have subtitles, don't have spaces, and all the metadata is not separated from the name by parenthesis.

To get a good number of hits in this set you could call: 
 ``libretrofuzz --no-subtitle --rmspaces --before '_'``

Then select the playlist that contains those whdloads and the system name `Commodore - Amiga` to download from the libretro amiga thumbnails.

Note that the system name you download from doesn't have to be the same as the playlist name.

If your playlist contains games from multiple releases (like ScummVM), be careful using this trick because it's easy to end up with 'slightly wrong' covers.

Example: After downloading thumbnails for 'ScummVM' (and not before, to minimize false positives), we'd like to try to pickup a few covers from the DOS database.

You could call: 
  ``libretrofuzz --no-meta``

Then chose the ScummVM playlist and DOS system name, and a few extra covers would be downloaded at the cost of these types of false positives: CD vs floppy covers, USA vs Japan covers, or another platform vs DOS.

Because of this the default is to count metadata as part of the matching, and the default pre-selected system name to be the same as the playlist name, which is safest.
False positives will then mostly be from the thumbnail server not having a single thumbnail of the game, and the program selecting a sequel or prequel as the 'most similar', or from the server not having a different cover for releases and getting the 'wrong' one (if libretro doesn't have japanese covers and the set has english names with (Japan) appened only, you're likely to get a english cover even in the same system).


**Usage: fuzzythumbnails [OPTIONS] [CFG]**

Arguments: 
  [CFG]  Path to the retroarch cfg file. If not provided, asked from the user.
         [default: ~/.config/retroarch/retroarch.cfg]

Options: 
  --playlist TEXT             Playlist name to download thumbnails for. If not
                              provided, asked from the user.
  --system TEXT               Directory in the server to download thumbnails
                              from.
  --fail, --no-fail           Fail if the similarity score is under 100, --no-
                              fail may cause false positives, but can increase
                              matches in sets with nonstandard names.
                              [default: fail]
  --meta, --no-meta           Match name () delimited metadata, --no-meta may
                              cause false positives, but can increase matches
                              in sets with nonstandard names.  [default: meta]
  --dump, --no-dump           Match name [] delimited metadata, --dump may
                              cause false positives, but can increase matches
                              for hacks, if the hack has thumbnails.
                              [default: no-dump]
  --subtitle, --no-subtitle   Match name before the last hyphen, --no-subtitle
                              may cause false positives, but can increase
                              matches in sets with incomplete names.
                              [default: subtitle]
  --rmspaces, --no-rmspaces   Instead of uniquifying spaces in normalization,
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
