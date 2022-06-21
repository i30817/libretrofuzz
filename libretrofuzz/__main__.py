#! /usr/bin/env python3


#this downloads thumbnails for retroarch playlists
#it uses fuzzy matching to find the most similar name to the names, based on the playlist description.
#there may be false positives, especially if the thumbnail server does not have the game but does have
#another similarly named game - happens on series or playlists where multiple versions of a game coexist.

#Although a game playlist entry may have a different db this script doesn't handle that to simplify
#the caching of names, since it's rare, so it assumes all entries in a playlist will have the same system.




from pathlib import Path
from typing import Optional, List
from pick import pick
import typer
import json
import os
import sys
import io
import re
import fnmatch
import zlib
from rapidfuzz import process, fuzz
from urllib.request import urlopen
import collections
import shutil
from bs4 import BeautifulSoup
from urllib.error import HTTPError, URLError
from urllib.request import unquote, quote
from tempfile import TemporaryDirectory
from contextlib import contextmanager
from struct import unpack




###########################################
########### SCRIPT SETTINGS ###############
###########################################


CONFIDENCE = 100
MAX_RETRIES = 3

if sys.platform == 'win32': #don't be fooled, this is for 64 bits too
    CONFIG = Path(r'C:/RetroArch-Win64/retroarch.cfg') #64bits default installer path
    if not CONFIG.exists():
        CONFIG = Path(r'C:/RetroArch/retroarch.cfg') #fallback to the 32 bits default installer path
elif sys.platform == 'darwin':
    CONFIG = Path(Path.home(), 'Documents', 'Retroarch', 'retroarch.cfg') #what I _think_ is the default on macosx
elif sys.platform.startswith('linux'):
    CONFIG = Path(Path.home(), '.config', 'retroarch', 'retroarch.cfg') #default installer path in unix
else:
    CONFIG = None #give up

#00-1f are ascii control codes, rest is 'normal' illegal windows filename chars according to powershell + &
forbidden = r'[\u0022\u003c\u003e\u007c\u0000\u0001\u0002\u0003\u0004\u0005\u0006\u0007\u0008' + \
            r'\u0009\u000a\u000b\u000c\u000d\u000e\u000f\u0010\u0011\u0012\u0013\u0014\u0015' + \
            r'\u0016\u0017\u0018\u0019\u001a\u001b\u001c\u001d\u001e\u001f\u003a\u002a\u003f\u005c\u002f\u0026]'

#handle for the retroarch specific compressed playlist fileformat
class RzipReader(object):
    def __init__(self, file_name):
        self.file_name = file_name

    @contextmanager
    def open(self):
        try:
            file = open(self.file_name, 'rb')
            header = os.pread(file.fileno(), 6, 0) #this function resets the stream
            if header.decode() == '#RZIPv':
                file.read(8) #skip all the header parts
                chunksize = unpack('<I', file.read(4) )[0] #little endian
                totalsize = unpack('<Q', file.read(8) )[0]
                checksize = 0
                #collect all the file into a 'byte file' object
                with io.BytesIO() as f:
                    #for each chunk of zlib compressed file parts
                    bsize = file.read(4)
                    while bsize != b'':
                        size = unpack('<I', bsize)[0]
                        dbytes = zlib.decompress(file.read(size))
                        checksize += len(dbytes)
                        f.write( dbytes )
                        bsize = file.read(4)
                    assert checksize == totalsize, f'{checksize} != {totalsize}'
                    f.seek(0) #reset for the next reader.
                    yield f
            else:
                yield file
        finally:
            file.close()

def getDirectoryPath(cfg: Path, directory: str):
    with open(cfg) as f:
        file_content = '[DUMMY]\n' + f.read()
    import configparser
    configParser = configparser.RawConfigParser()
    configParser.read_string(file_content)
    dirp = os.path.expanduser(configParser['DUMMY'][directory].strip('\t ').strip('"'))
    return Path(dirp)

def mainaux(cfg: Path = typer.Argument(CONFIG, help='Path to the retroarch cfg file. If not default, asked from the user.'),
        playlist: str = typer.Option(None, metavar='NAME', help='Playlist name with labels used for thumbnail fuzzy matching. If not provided, asked from the user.'),
        system: str = typer.Option(None, metavar='NAME', help='Directory name in the server to download thumbnails. If not provided, asked from the user.'),
        filters: Optional[List[str]] = typer.Option(None, '--reset', metavar='FILTER', help='Restricts downloads to game labels globs - not paths - in the playlist, can be used multiple times and matches reset thumbnails, --reset \'*\' downloads all.'),
        nomerge: bool = typer.Option(False, '--no-merge', help='Disables missing thumbnails download for a label if there is at least one in cache to avoid mixing thumbnails from different server directories on repeated calls. No effect if called with --reset.'),
        nofail: bool = typer.Option(False, '--no-fail', help='Download any score. Best used with --reset as filter.'),
        nometa: bool = typer.Option(False, '--no-meta', help='Ignores () delimited metadata and may cause false positives. Forced with --before.'),
        hack: bool = typer.Option(False, '--hack', help='Matches [] delimited metadata and may cause false positives, Best used if the hack has thumbnails. Ignored with --before.'),
        nosubtitle: bool = typer.Option(False, '--no-subtitle', help='Ignores subtitles after \' - \' or \': \' from both the server names and labels. Best used with --reset, unless all of the playlist has no subtitles. Note, \':\' can not occur in server filenames, so if the server has \'Name_ subtitle.png\' and not \'Name - subtitle.png\' (uncommon), you should try first without this option.'),
        rmspaces: bool = typer.Option(False, '--rmspaces', help='Instead of uniquifying spaces in normalization, remove them, for playlists with no spaces in the labels.'),
        before: Optional[str] = typer.Option(None, help='Use only the part of the label before TEXT to match. TEXT may not be inside of brackets of any kind, may cause false positives but some labels do not have traditional separators. Forces metadata to be ignored.')
    ):
    """
Fuzzy Retroarch thumbnail downloader

In Retroarch, when you use the manual scanner to get non-standard games or hacks in playlists, thumbnails often fail to download.

This program, for each game label on a playlist, downloads the 'most similar' image to display the image in retroarch.

It has several options to fit unusual labels, but you can just run it to get the most restrictive default. It will ask for the CFG, playlist and system if they're not provided.

Example:

 libretro-fuzz --no-subtitle --rmspaces --before '_'

 The Retroplay WHDLoad set has labels like 'MonkeyIsland2_v1.3_0020' after a manual scan. These labels don't have subtitles, don't have spaces, and all the metadata is not separated from the name by brackets. Select the playlist that contains those whdloads and the system name 'Commodore - Amiga' to download from the libretro amiga thumbnails.

Note that the system name you download from doesn't have to be the same as the playlist name.

If the thumbnail server contains games from multiple releases for the system (like 'ScummVM'), be careful using extra options since it is easy to end up with 'slightly wrong' covers.

Example:

 libretro-fuzz --no-meta --no-merge

 After downloading 'ScummVM' thumbnails (and not before, to minimize false positives), we'd like to try to pickup a few covers from 'DOS' thumbnails and skip download if there a risk of mixing thumbnails from 'DOS' and 'ScummVM' for a single game.
 Choose the ScummVM playlist and DOS system name, and covers would be downloaded with risk of false positives: CD vs floppy covers, USA vs Japan covers, or another platform vs DOS.

Because of this increased risk of false positives with options, the default is to count everything except hack metadata as part of the match, and the default pre-selected system name to be the same as the playlist name, which is safest.

False positives will then mostly be from the thumbnail server not having a single thumbnail of the game, and the program selecting the best match it can which is still good enough to pass the similarity test. Common false positives from this are sequels or prequels, or different releases, most often regions/languages.

Example:

 libretro-fuzz --no-subtitle --rmspaces --before '_' --reset '[Ii]shar*'

 The best way to solve these issues is to upload the right cover to the respective libretro-thumbnail subproject with the correct name of the game variant. Then you can redownload just the updated thumbnails with a label, in this example, the Ishar series in the WHDLoad playlist.

To update this program with pip installed, type:

pip install --force-reinstall https://github.com/i30817/libretrofuzz/archive/master.zip
    """
    if not cfg or not cfg.is_file():
        typer.echo(f'Invalid Retroarch cfg file: {cfg}')
        raise typer.Abort()
    
    thumbnails_directory = getDirectoryPath(cfg, 'thumbnails_directory')
    if not thumbnails_directory.is_dir():
        typer.echo(f'Invalid Retroarch thumbnails directory: {thumbnails_directory}')
        raise typer.Abort()
    
    playlist_dir = getDirectoryPath(cfg, 'playlist_directory')
    
    if not playlist_dir.is_dir():
        typer.echo(f'Invalid Retroarch playlist directory: {playlist_dir}')
        raise typer.Abort()
    
    PLAYLISTS = list(playlist_dir.glob('./*.lpl'))
    
    if not PLAYLISTS:
        typer.echo(f'Retroarch cfg file has empty playlist directory: {playlist_dir}')
        raise typer.Abort()
    
    if playlist and not playlist.endswith('.lpl'):
        playlist = playlist + '.lpl'
    
    if playlist and Path(playlist_dir, playlist) not in PLAYLISTS:
        typer.echo(f'Unknown user provided playlist: {playlist}')
        raise typer.Abort()
    
    if not playlist: #ask user for which
        displayplaylists = list(map(os.path.basename, PLAYLISTS))
        playlist, _ = pick(displayplaylists, 'Which playlist do you want to download thumbnails for?')
    
    try:
        soup = BeautifulSoup(urlopen('https://thumbnails.libretro.com/', timeout=10), 'html.parser')
        SYSTEMS = [ unquote(node.get('href')[:-1]) for node in soup.find_all('a') if node.get('href').endswith('/') and not node.get('href').endswith('../') ]
    except (HTTPError, URLError) as err:
        typer.echo(f'Could not get the remote thumbnail system names')
        raise typer.Abort()
    
    if system and system not in SYSTEMS:
        typer.echo(f'The user provided system name {system} does not match any remote thumbnail system names')
        raise typer.Abort()
    
    if not system:
        try:
            default_i = SYSTEMS.index(playlist[:-4]) #start with the playlist system selected, if any
        except ValueError:
             default_i = 0
        system, _ = pick(SYSTEMS, 'Which directory in the thumbnail server should be used to download thumbnails?', default_index=default_i)
    
    playlist = Path(playlist_dir, playlist)
    
    names = []
    with RzipReader(playlist).open() as f:
        data = json.load(f)
        for r in data['items']:
            assert 'label' in r and r['label'].strip() != '', f'\n{json.dumps(r,indent=4)} of playlist {playlist} has no label'
            assert 'db_name' in r and r['db_name'].endswith('.lpl'), f'\n{json.dumps(r,indent=4)} of playlist {playlist} has no valid db_name'
            #add the label name and the db name (it's a playlist name, minus the extension '.lpl')
            names.append( (r['label'], r['db_name'][:-4]) )
    
    if len(names) == 0:
        typer.echo(f'No names found in playlist {playlist}')
        raise typer.Abort()
    
    lr_thumbs = 'https://thumbnails.libretro.com/'+quote(system) #then get the thumbnails from the system name
    thumbs = collections.namedtuple('Thumbs', ['Named_Boxarts', 'Named_Snaps', 'Named_Titles'])
    args = []
    for tdir in ['/Named_Boxarts/', '/Named_Snaps/', '/Named_Titles/']:
        lr_thumb = lr_thumbs+tdir
        try:
            soup = BeautifulSoup(urlopen(lr_thumb, timeout=10), 'html.parser')
            l1 = { unquote(Path(node.get('href')).name[:-4]) : lr_thumb+node.get('href') for node in soup.find_all('a') if node.get('href').endswith('.png')}
        except HTTPError as err:
            l1 = {} #some do not have one or more of these
        args.append(l1)
    
    if all(map(lambda x: len(x) == 0, args)):
        typer.echo(f'No thumbnails found at {lr_thumbs}')
        raise typer.Abort()
    
    thumbs = thumbs._make( args )
    
    #before implies that the names of the playlists may be cut, so the hack and meta matching must be disabled
    if before:
        hack = False
        nometa = True
    
    def removeparenthesis(s, open_p='(', close_p=')'):
        nb_rep = 1
        while (nb_rep):
            a = fr'\{open_p}[^{close_p}{open_p}]*\{close_p}'
            (s, nb_rep) = re.subn(a, '', s)
        return s
    
    def replacemany(our_str, to_be_replaced, replace_with):
        for nextchar in to_be_replaced:
            our_str = our_str.replace(nextchar, replace_with)
        return our_str

    def normalizer(t):
        if nometa:
            t = removeparenthesis(t,'(',')')
        if not hack:
            t = removeparenthesis(t,'[',']')
        #Tries to make roman numerals in the range 1-20 equivalent to normal numbers (to handle names that change it).
        #If both sides are roman numerals there is no harm done if XXIV gets turned into 204 in both sides.
        #Problem only occurs if they're different and would occur even without this transformation.
        t = t.replace('XVIII', '18')
        t = t.replace('XVII',  '17')
        t = t.replace('XVI' ,  '16')
        t = t.replace('XIII',  '13')
        t = t.replace('XII' ,  '12')
        t = t.replace('XIV' ,  '14')
        t = t.replace('XV'  ,  '15')
        t = t.replace('XIX',   '19')
        t = t.replace('XX',   '20')
        t = t.replace('XI',   '11')
        t = t.replace('VIII', '8')
        t = t.replace('VII',  '7')
        t = t.replace('VI' ,  '6')
        t = t.replace('III',  '3')
        t = t.replace('II' ,  '2')
        t = t.replace('IV' ,  '4')
        t = t.replace('V'  ,  '5')
        t = t.replace('IX',   '9')
        t = t.replace('X',   '10')
        t = t.replace('I',    '1')
        #change all metacharacters to space (spaces will be uniquified or removed next)
        t = replacemany(t, '_()[]{}-', ' ')
        #although the remote names always have spaces, the local names may not have
        #so in order for normalization/removal of tokens with spaces to work on 'both sides'
        #we should normalize the spaces right away in both sides, then specialize the tokens
        if rmspaces:
            t = re.sub('\s', '', t)
            s = ''
        else:
            t = re.sub('\s+', ' ', t)
            s = ' '
        #beginning definite articles in several european languages
        #with two forms because people keep moving them to the end
        t = t.replace(f',{s}The', '')
        t = t.replace(f'The{s}',  '')
        t = t.replace(f',{s}Le', '')
        t = t.replace(f'Le{s}',  '')
        t = t.replace(f',{s}La', '')
        t = t.replace(f'La{s}',  '')
        t = t.replace(f',{s}L\'', '')
        #as a abbreviation these sometimes doesn't have space at the start even without --rmspaces
        t = t.replace(f'L\' ',  '')
        t = t.replace(f'L\'',  '')
        t = t.replace(f',{s}Les', '')
        t = t.replace(f'Les{s}',  '')
        t = t.replace(f',{s}Der', '')
        t = t.replace(f'Der{s}',  '')
        t = t.replace(f',{s}Die', '')
        t = t.replace(f'Die{s}',  '')
        t = t.replace(f',{s}Das', '')
        t = t.replace(f'Das{s}',  '')
        t = t.replace(f',{s}El', '')
        t = t.replace(f'El{s}',  '')
        t = t.replace(f',{s}Los', '')
        t = t.replace(f'Los{s}',  '')
        t = t.replace(f',{s}Las', '')
        t = t.replace(f'Las{s}',  '')
        t = t.replace(f',{s}O', '')
        t = t.replace(f'O{s}',  '')
        t = t.replace(f',{s}A', '')
        t = t.replace(f'A{s}',  '')
        t = t.replace(f',{s}Os', '')
        t = t.replace(f'Os{s}',  '')
        t = t.replace(f',{s}As', '')
        t = t.replace(f'As{s}',  '')
        #remove all punctuation, '&' is already a forbidden character so it was replaced by '_' then ' ' or '' above
        t = replacemany(t, ',.!?#\'', '')
        #this makes sure that if a remote name has ' and ' instead of ' _ ' to replace ' & ' it works (spaces optional).
        #': ' doesn't need this because ':' is a forbidden character and both '_' and '-' turn to ' '
        t = t.lower().replace(f'{s}and{s}',  f'{s}')
        return t.strip()
    
    def myscorer(s1, s2, processor=None, score_cutoff=None):
        similarity = fuzz.token_set_ratio(s1,s2,processor=processor,score_cutoff=score_cutoff)
        #combine the token set ratio scorer with a common prefix heuristic to give priority to longer similar names
        #This helps prevents false positives for shorter strings
        #which token set ratio is prone to because it sets score to 100
        #if one string words are completely on the other
        
        prefix = len(os.path.commonprefix([s1, s2]))
        if prefix <= 2 and len(s1) != len(s2):
            #ideally this branch wouldn't exist, but since many games do not have
            #images, they get caught up on a short title '100' from token_set_ratio
            #without the real title to win the similarity+prefix heuristic
            #this removes many false positives and causes few false negatives.
            return 0
        else:
            if s1 == s2:
                return 200
            return similarity + prefix
    
    def nosubtitle_aux(t,subtitle_marker=' - '):
        #Ignore metadata (but do not delete) and get the string before it
        no_meta = re.search(r'(^[^[({]*)', t)
        #last subtitle marker and everything there until the end (last because i noticed that 'subsubtitles' exist,
        #for instance, ultima 7 - part 1|2 - subtitle
        subtitle = re.search(rf'.*({subtitle_marker}.*)', no_meta.group(1) if no_meta else t)
        if subtitle:
            t = t[0:subtitle.start(1)] + ' ' + t[subtitle.end(1):]
        return t
    
    def nosubtitle_normalizer(t):
        return normalizer(nosubtitle_aux(t))
    
    #preprocess data so it's not redone every loop iteration.
    
    #normalize with or without subtitles, besides the remote_names this is used on the iterated local names later
    norm = nosubtitle_normalizer if nosubtitle else normalizer
    #we choose the highest similarity of all 3 directories, since no mixed matches are allowed
    remote_names = set()
    remote_names.update(thumbs.Named_Boxarts.keys(), thumbs.Named_Snaps.keys(), thumbs.Named_Titles.keys())
    #turn into a dict, original key and normalized value
    remote_names = { x : norm(x) for x in remote_names }
    #temporary dir for downloads (required to prevent clobbering of files in case of no internet and filters being used)
    #parent directory of this temp dir is the same as the retroarch thumbnail dir to make moving the file just renaming it, not copy it
    #it may seem strange to use a tmp dir for a single file, but mktemp (the name, not open file version) is deprecated because of
    #a security risk of MitM. Not sure if this helps with that, but at least it won't stop working in the future once that is removed.
    with TemporaryDirectory(prefix='libretrofuzz', dir=thumbnails_directory) as tmpdir:
        for (name,destination) in names:
            #if the user used filters, filter everything that doesn't match any of the globs
            if filters and not any(map(lambda x : fnmatch.fnmatch(name, x), filters)):
                continue
            
            #to simplify this code, the forbidden characters are replaced twice,
            #on the string that is going to be the filename and the modified string copy of that that is going to be matched.
            #it could be done only once, but that would require separating the colon character for subtitle matching,
            #and the 'before' operation would have to find the index before the match to apply it after. A mess.
            
            nameaux = name
            
            #'before' has priority over subtitle removal
            if before:
                #Ignore metadata and get the string before it
                no_meta = re.search(r'(^[^[({]*)', nameaux)
                if no_meta:
                    before_index = no_meta.group(1).find(before)
                    if before_index != -1:
                        nameaux = nameaux[0:before_index]
            
            #there is a second form of subtitles, which doesn't appear in the thumbnail server directly but can appear in linux game names
            #that can be more faithful to the real name. It uses the colon character, which is forbidden in windows and only applies to filenames.
            #Note that this does mean that if the servername has 'Name_ subtitle.png' and not 'Name - subtitle.png' there is less chance of a match,
            #but that is rarer on the server than the opposite.
            #not to mention that this only applies if the user signals 'no-subtitle', which presumably means they tried without it - which does match.
            if nosubtitle:
                nameaux = nosubtitle_aux(nameaux, ': ')
            
            #only the local names should have forbidden characters
            name = re.sub(forbidden, '_', name )
            nameaux = re.sub(forbidden, '_', nameaux )
            #unlike the server thumbnails, this wasn't done yet
            nameaux = norm(nameaux)
            
            #operate on cache (to speed up by not applying normalization every iteration)
            norm_thumbnail, i_max, thumbnail = process.extractOne(nameaux, remote_names, scorer=myscorer,processor=None,score_cutoff=None) or (None, 0, None)
            if thumbnail and ( i_max >= CONFIDENCE or nofail ):
                #This is tricky - thumbnails download destination is not based on the playlist name (because the user can use other names),
                #or the core name or even the scan dir. It's based on the db_name playlist on each and every playlist entry.
                #Now I'm not sure if those can differ in the same playlist, but to be safe, create them in each iteration of the loop.
                #This is just for the destination, not the source.
                thumb_dir = Path(thumbnails_directory,destination)
                #if no filtering and merge is turned off, only download if all thumbnail types are missing
                allow = True
                if not filters and nomerge:
                    def thumbcheck(thumb_path):
                        p = Path(thumb_dir, thumb_path, name+'.png')
                        return not p.exists() or os.path.getsize(p) == 0
                    allow = all(map(thumbcheck, thumbs._fields))
                if allow:
                    any_download = False
                    for dirname in thumbs._fields:
                        parent = Path(thumb_dir, dirname)
                        real = Path(parent, name + '.png')
                        tmp_parent = Path(tmpdir, dirname)
                        temp = Path(tmp_parent, name + '.png')
                        
                        #defective file, do this before checking if you have something to download
                        #should probably check if it is a valid png instead or in addition.
                        if real.exists() and os.path.getsize(real) == 0:
                            real.unlink(missing_ok=True)
                        
                        #something to download
                        thumbmap = getattr(thumbs, dirname)
                        if thumbnail in thumbmap:
                            os.makedirs(parent, exist_ok=True)
                            os.makedirs(tmp_parent, exist_ok=True)
                            
                            retry_count = MAX_RETRIES
                            downloaded = False
                            
                            def download():
                                nonlocal downloaded
                                nonlocal retry_count
                                with open(temp, 'w+b') as f:
                                    try:
                                        f.write(urlopen(thumbmap[thumbnail], timeout=15).read())
                                        downloaded = True
                                    except Exception as e:
                                        retry_count = retry_count - 1
                                        downloaded = False
                                        if retry_count == 0:
                                            print(f'Exception: {e}, label: {name}, thumbnail-type: {dirname}, tempfile: {temp}', file=sys.stderr)
                                    finally:
                                        if downloaded:
                                            shutil.move(temp, real)
                            if filters:
                                while not downloaded and retry_count > 0:
                                    download()
                            else:
                                while not real.exists() and retry_count > 0:
                                    download()
                            any_download = any_download or downloaded
                        elif filters:
                            #nothing to download but we want to remove images that may be there in the case of --reset.
                            real.unlink(missing_ok=True)
                    if any_download:
                        print("{:>5}".format(str(int(i_max))+'% ') + f'Success: {nameaux} -> {norm_thumbnail}')
                else:
                    print("{:>5}".format(str(0)+'% ') + f'Skipped: {nameaux} -> {norm_thumbnail}')
            else:
                print("{:>5}".format(str(int(i_max))+'% ') + f'Failure: {nameaux} -> {norm_thumbnail}')

def main():
    typer.run(mainaux)
    return 0

if __name__ == "__main__":
    typer.run(mainaux)
