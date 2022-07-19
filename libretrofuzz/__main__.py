#! /usr/bin/env python3

#this downloads thumbnails for retroarch playlists
#it uses fuzzy matching to find the most similar name to the names, based on the playlist description.
#there may be false positives, especially if the thumbnail server does not have the game but does have
#another similarly named game - happens on series or playlists where multiple versions of a game coexist.

#Although a game playlist entry may have a different db this script doesn't handle that to simplify
#the caching of names, since it's rare, so it assumes all entries in a playlist will have the same system.

from pathlib import Path
from typing import Optional, List
from urllib.request import unquote, quote
from tempfile import TemporaryDirectory
from contextlib import asynccontextmanager, contextmanager
from itertools import chain
from struct import unpack
import json
import os
import sys
import io
import re
import zlib
import fnmatch
import collections
import shutil
import unicodedata
import asyncio
from prompt_toolkit.input import create_input
from rapidfuzz import process, fuzz
from bs4 import BeautifulSoup
from questionary import Style, select
from httpx import RequestError, Client, AsyncClient
from tqdm import tqdm
import typer

###########################################
################ README ###################
###########################################

README = """
Fuzzy Retroarch thumbnail downloader

In Retroarch, when you use the manual scanner to get non-standard games or hacks in playlists, thumbnails often fail to download.

These programs, for each game label on a playlist, download the most similar name image to display in retroarch.

There are several options to fit unusual labels, but you can just run them to get the most restrictive default.

If you use libretro-fuzz, it will ask for the playlist and system if they're not provided.

If you use libretro-fuzzall, it will attempt to match the playlist names to the server system names, and will skip custom playlist names.

Besides those differences, if no retroarch.cfg is provided, both programs try to use the default retroarch.cfg.

Example:

 libretro-fuzz --no-subtitle --before '_'

 The Retroplay WHDLoad set has labels like 'MonkeyIsland2_v1.3_0020' after a manual scan. These labels don't have subtitles and all the metadata is not separated from the name by brackets. Select the playlist that contains those whdloads and the system name 'Commodore - Amiga' to download from the libretro amiga thumbnails.

Note that the system name you download from doesn't have to be the same as the playlist name.

If the thumbnail server contains games from multiple releases for the system (like 'ScummVM'), be careful using extra options since it is easy to end up with 'slightly wrong' covers.

Example:

 libretro-fuzz --no-meta --no-merge

 After downloading 'ScummVM' thumbnails (and not before, to minimize false positives), we'd like to try to pickup a few covers from 'DOS' thumbnails and skip download if there a risk of mixing thumbnails from 'DOS' and 'ScummVM' for a single game.
 Choose the ScummVM playlist and DOS system name, and covers would be downloaded with risk of false positives: CD vs floppy covers, USA vs Japan covers, or another platform vs DOS.

Because of this increased risk of false positives with options, the default is to count everything except hack metadata as part of the match, and the default pre-selected system name to be the same as the playlist name, which is safest.

False positives will then mostly be from the thumbnail server not having a single thumbnail of the game, and the program selecting the best match it can which is still good enough to pass the similarity test. Common false positives from this are sequels or prequels, or different releases, most often regions/languages.

Example:

 libretro-fuzz --no-subtitle --before '_' --filter '[Ii]shar*'

 The best way to solve these issues is to upload the right cover to the respective libretro-thumbnail subproject with the correct name of the game variant. Then you can redownload just the updated thumbnails with a label, in this example, the Ishar series in the WHDLoad playlist.

To update this program with pip installed, type:

pip install --force-reinstall https://github.com/i30817/libretrofuzz/archive/master.zip
    """


###########################################
########### SCRIPT SETTINGS ###############
###########################################


CONFIDENCE = 100
MAX_RETRIES = 3

if sys.platform == 'win32': #this is for 64 bits too
    #this order is to make 'portable' installs have priority in windows, a concept that doesn't exist in linux or macosx
    #these are the default 32 and 64 bits installer paths, since there is no way to know what the user choses, check the defaults only.
    CONFIG = Path(r'C:/RetroArch-Win64/retroarch.cfg')
    if not CONFIG.exists():
        CONFIG = Path(r'C:/RetroArch/retroarch.cfg')
        if not CONFIG.exists():
            typer.echo('Portable install default location config not found, trying with APPDATA location')
            var = os.getenv('APPDATA')
            if var:
                CONFIG = Path(var, 'RetroArch', 'retroarch.cfg')
elif sys.platform == 'darwin':
    CONFIG = Path(Path.home(), 'Library', 'Application Support', 'RetroArch', 'retroarch.cfg')
else: #all the rest based on linux. If they arent based on linux, they'll try the else and fail harmlessly later
    var = os.getenv('XDG_CONFIG_HOME')
    if var:
        CONFIG = Path(var, 'retroarch', 'retroarch.cfg')
    else:
        CONFIG = Path(Path.home(), '.config', 'retroarch', 'retroarch.cfg')

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
        with open(self.file_name, 'rb') as file:
            header = file.read(6)
        with open(self.file_name, 'rb') as file:
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

#uses the class above to read either a normal json or a compressed json playlist then return the list of (label,db_name) tuples
def readPlaylist(playlist: Path):
    names = []
    with RzipReader(playlist).open() as f:
        data = json.load(f)
        for r in data['items']:
            assert 'label' in r and r['label'].strip() != '', f'\n{json.dumps(r,indent=4)} of playlist {playlist} has no label'
            assert 'db_name' in r and r['db_name'].endswith('.lpl'), f'\n{json.dumps(r,indent=4)} of playlist {playlist} has no valid db_name'
            #add the label name and the db name (it's a playlist name, minus the extension '.lpl')
            names.append( (r['label'], r['db_name'][:-4]) )
    return names

#The main part of the program, what orders titles to be 'more similar' or less to the local labels (after the normalization)
class TitleScorer(object):
    def __init__(self):
        #rapidfuzz says to use range 0-100, but this doesn't (it's much easier that way), so it uses internal api to prevent a possible early exit at == 100
        self._RF_ScorerPy = { 'get_scorer_flags': lambda **kwargs: {'optimal_score': 200, 'worst_score': 0, 'flags': (1 << 6)} }

    def __call__(self, s1, s2, processor=None, score_cutoff=None):
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
            #score_cutoff needs to be 0 from a combination of 3 factors that create a bug:
            #1. the caller of this, extractOne passes the 'current best score' as score_cutoff
            #2. the token_set_ratio function returns 0 if the calculated score < score_cutoff
            #3. 'current best score' includes the prefix, which this call can't include in 2.
            similarity = fuzz.token_set_ratio(s1,s2,processor=None,score_cutoff=0)
            #Combine the scorer with a common prefix heuristic to give priority to longer similar
            #names, this helps prevents false positives for shorter strings which token set ratio
            #is prone because it sets score to 100 if one string words are completely on the other.
            return similarity + prefix

#keyboard listener, to interrupt downloads or stop the program
class StopDownload(Exception):
    def __init__(self):
        super().__init__()
class StopProgram(Exception):
    def __init__(self):
        super().__init__()

skip = False
escape  = False
def checkDownload():
    '''threading.get_native_id() in this and other acesses of these variables
       confirms all accesses are in synchronous functions on one thread so
       there is no need to use any lock, async or not.
    '''
    global skip
    global escape
    if escape:
        raise StopProgram()
    if skip:
        skip = False
        raise StopDownload()
def checkEscape():
    global escape
    if escape:
        raise StopProgram()

@asynccontextmanager
async def lock_keys() -> None:
    '''locks out the user from most keys for this console but
       not combinations, so user kill still works, alt+tab etc
       it also serves as a quit program and skip download shortcut
    '''
    done = asyncio.Event()
    input = create_input()
    def keys_ready():
        global skip
        global escape
        #escape needs flush in unix platforms, so this chain
        for key_press in chain(input.read_keys(), input.flush_keys()):
            if key_press.key == 'escape' or key_press.key == 'c-c': #esc or control-c
                escape = True
                done.set()
            else:
                skip = True

    with input.raw_mode():
        with input.attach(keys_ready):
            typer.echo(typer.style(f' Press escape to quit, and most other non-meta keys to skip downloads', bold=True))
            yield done

def getDirectoryPath(cfg: Path, setting: str):
    '''returns paths inside of a cfg file setting'''
    with open(cfg) as f:
        file_content = '[DUMMY]\n' + f.read()
    import configparser
    configParser = configparser.RawConfigParser()
    configParser.read_string(file_content)
    dirp = os.path.expanduser(configParser['DUMMY'][setting].strip('\t ').strip('"'))
    return Path(dirp)
    
def test_common_errors(cfg: Path, playlist: str, system: str):
    '''returns a tuple with (playlist_dir: Path, thumbnail_dir: Path, PLAYLISTS: [Path], SYSTEMS: [str]) '''
    
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
    if playlist and Path(playlist_dir, playlist) not in PLAYLISTS:
        typer.echo(f'Unknown user provided playlist: {playlist}')
        raise typer.Abort()

    try:
        with Client() as client:
            page = client.get('https://thumbnails.libretro.com/', timeout=15)
            soup = BeautifulSoup(page.text, 'html.parser')
        SYSTEMS = [ unquote(node.get('href')[:-1]) for node in soup.find_all('a') if node.get('href').endswith('/') and not node.get('href').endswith('../') ]
    except RequestError as err:
        typer.echo(f'Could not get the remote thumbnail system names')
        raise typer.Abort()
    if system and system not in SYSTEMS:
        typer.echo(f'The user provided system name {system} does not match any remote thumbnail system names')
        raise typer.Abort()
    
    return (playlist_dir, thumbnails_directory, PLAYLISTS, SYSTEMS)

            
def mainfuzzsingle(cfg: Path = typer.Argument(CONFIG, help='Path to the retroarch cfg file. If not default, asked from the user.'),
        playlist: str = typer.Option(None, metavar='NAME', help='Playlist name with labels used for thumbnail fuzzy matching. If not provided, asked from the user.'),
        system: str = typer.Option(None, metavar='NAME', help='Directory name in the server to download thumbnails. If not provided, asked from the user.'),
        delay: float = typer.Option(1.5, min=0, max=5, help='Delay in seconds before downloading game thumbnails to allow the user to skip them.'),
        filters: Optional[List[str]] = typer.Option(None, '--filter', metavar='GLOB', help='Restricts downloads to game labels globs - not paths - in the playlist, can be used multiple times and matches reset thumbnails, --filter \'*\' downloads all.'),
        nomerge: bool = typer.Option(False, '--no-merge', help='Disables missing thumbnails download for a label if there is at least one in cache to avoid mixing thumbnails from different server directories on repeated calls. No effect if called with --filter.'),
        nofail: bool = typer.Option(False, '--no-fail', help='Download any score. To restrict or retry use --filter.'),
        nometa: bool = typer.Option(False, '--no-meta', help='Ignores () delimited metadata and may cause false positives. Forced with --before.'),
        hack: bool = typer.Option(False, '--hack', help='Matches [] delimited metadata and may cause false positives, Best used if the hack has thumbnails. Ignored with --before.'),
        nosubtitle: bool = typer.Option(False, '--no-subtitle', help='Remove subtitle after \' - \' or \': \' for mismatched labels and server names. \':\' can\'t occur in server names, so if the server has \'Name_ subtitle.png\' and not \'Name - subtitle.png\' (uncommon), this option doesn\'t help. To restrict or retry use --filter.'),
        before: Optional[str] = typer.Option(None, help='Use only the part of the label before TEXT to match. TEXT may not be inside of brackets of any kind, may cause false positives but some labels do not have traditional separators. Forces metadata to be ignored.'),
        verbose: bool = typer.Option(False, '--verbose', help='Shows the failures, score and normalized local and server names in output (score >= 100 is succesful).')
    ):
    f"""{README}"""
    if playlist and not playlist.endswith('.lpl'):
        playlist = playlist + '.lpl'
    
    playlist_dir, thumbnails_directory, PLAYLISTS, SYSTEMS = test_common_errors(cfg, playlist, system)
    
    custom_style = Style([
        ('answer', 'fg:green bold'),      # submitted answer text behind the question
    ])
    
    #ask user for these 2 arguments if they're still not set
    if not playlist:
        display_playlists = list(map(os.path.basename, PLAYLISTS))
        playlist = select('Which playlist do you want to download thumbnails for?', display_playlists, style=custom_style, qmark='').ask()
        if not playlist:
            raise typer.Exit()
    
    if not system:
        #start with the playlist system selected, if any
        playlist_sys = playlist[:-4]
        question = 'Which directory should be used to download thumbnails?'
        system = select(question, SYSTEMS, qmark='', style=custom_style, default=playlist_sys if playlist_sys in SYSTEMS else None).ask()
        if not system:
            raise typer.Exit()
    
    async def runit():
        try:
            async with lock_keys():
                names = readPlaylist(Path(playlist_dir, playlist))
                typer.echo(typer.style(f'{playlist} -> {system}', bold=True))
                await downloader(names, system, delay, filters, nomerge, nofail, nometa, hack, nosubtitle, verbose, before, thumbnails_directory)
        except StopProgram as e:
            typer.echo(f'\nCancelled by user\n')
            raise typer.Exit()
        except RuntimeError as err:
            typer.echo(f'{playlist}: {err}')
            raise typer.Abort()
    asyncio.run(runit(), debug=False)

def mainfuzzall(cfg: Path = typer.Argument(CONFIG, help='Path to the retroarch cfg file. If not default, asked from the user.'),
        delay: float = typer.Option(1.5, min=0, max=5, help='Delay in seconds before downloading game thumbnails to allow the user to skip them.'),
        filters: Optional[List[str]] = typer.Option(None, '--filter', metavar='GLOB', help='Restricts downloads to game labels globs - not paths - in the playlist, can be used multiple times and matches reset thumbnails, --filter \'*\' downloads all.'),
        nomerge: bool = typer.Option(False, '--no-merge', help='Disables missing thumbnails download for a label if there is at least one in cache to avoid mixing thumbnails from different server directories on repeated calls. No effect if called with --filter.'),
        nofail: bool = typer.Option(False, '--no-fail', help='Download any score. To restrict or retry use --filter.'),
        nometa: bool = typer.Option(False, '--no-meta', help='Ignores () delimited metadata and may cause false positives. Forced with --before.'),
        hack: bool = typer.Option(False, '--hack', help='Matches [] delimited metadata and may cause false positives, Best used if the hack has thumbnails. Ignored with --before.'),
        nosubtitle: bool = typer.Option(False, '--no-subtitle', help='Remove subtitle after \' - \' or \': \' for mismatched labels and server names. \':\' can\'t occur in server names, so if the server has \'Name_ subtitle.png\' and not \'Name - subtitle.png\' (uncommon), this option doesn\'t help. To restrict or retry use --filter.'),
        before: Optional[str] = typer.Option(None, help='Use only the part of the label before TEXT to match. TEXT may not be inside of brackets of any kind, may cause false positives but some labels do not have traditional separators. Forces metadata to be ignored.'),
        verbose: bool = typer.Option(False, '--verbose', help='Shows the failures, score and normalized local and server names in output (score >= 100 is succesful).')
    ):
    f"""{README}"""
    playlist_dir, thumbnails_directory, PLAYLISTS, SYSTEMS = test_common_errors(cfg, None, None)
    
    notInSystems = [ (playlist, os.path.basename(playlist)[:-4]) for playlist in PLAYLISTS if os.path.basename(playlist)[:-4] not in SYSTEMS]
    for playlist, system in notInSystems:
        typer.echo(typer.style(f'Custom playlist skipped: ', fg=typer.colors.RED, bold=True)+typer.style(f'{system}.lpl', bold=True))
        PLAYLISTS.remove(playlist)
    inSystems = [ (playlist, os.path.basename(playlist)[:-4]) for playlist in PLAYLISTS ]
    
    async def runit():
        there_was_a_error = []
        try:
            async with lock_keys():
                for playlist, system in inSystems:
                    try:
                        names = readPlaylist(playlist)
                        typer.echo(typer.style(f'{system}.lpl -> {system}', bold=True))
                        await downloader(names, system, delay, filters, nomerge, nofail, nometa, hack, nosubtitle, verbose, before, thumbnails_directory)
                    except RuntimeError as err:
                        there_was_a_error.append((playlist,err))
            if there_was_a_error:
                typer.echo('Playlists returned errors when trying to download thumbnails:')
                for playlist, err in there_was_a_error:
                    typer.echo(f'{playlist}: {err}')    
                raise typer.Abort()
        except StopProgram as e:
            typer.echo(f'\nCancelled by user\n')
            raise typer.Exit()
    asyncio.run(runit(), debug=False)

async def downloader(names: [(str,str)],
               system: str,
               delay: float,
               filters: Optional[List[str]],
               nomerge: bool, nofail: bool, nometa: bool, hack: bool, nosubtitle: bool, verbose: bool,
               before: Optional[str],
               thumbnails_directory: Path
               ):
    #not a error to pass a empty playlist
    if len(names) == 0:
        return
    
    lr_thumbs = 'https://thumbnails.libretro.com/'+quote(system) #then get the thumbnails from the system name
    thumbs = collections.namedtuple('Thumbs', ['Named_Boxarts', 'Named_Titles', 'Named_Snaps'])
    args = []
    try:
        for tdir in ['/Named_Boxarts/', '/Named_Titles/', '/Named_Snaps/']:
            lr_thumb = lr_thumbs+tdir
            async with AsyncClient() as client:
                r = await client.get(lr_thumb, timeout=15)
                #not found is ok, since some server system directories do not have all the subdirectories
                if r.status_code == 404:
                    l1 = {}
                else:
                    #will go to except if there is a another error
                    r.raise_for_status()
                    soup = BeautifulSoup(r.text, 'html.parser')
                    l1 = { unquote(Path(node.get('href')).name[:-4]) : lr_thumb+node.get('href') for node in soup.find_all('a') if node.get('href').endswith('.png')}
            args.append(l1)
    except RequestError as err:
        typer.echo(f'Could not get the remote thumbnail filenames')
        raise typer.Abort()
    #not a error for the server to have no thumbnails for the system (unusual though)
    if all(map(lambda x: len(x) == 0, args)):
        return
    
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
    
    #these two methods will be unnecessary once at python 3.9 is widespread in distros (ie: after ubuntu 20.04 is not supported)
    def removesuffix(name: str, suf: str):
        if name.endswith(suf):
            return name[:-len(suf)]
        return name

    def removeprefix(name: str, pre: str):
        if name.startswith(pre):
            return name[len(pre):]
        return name
    
    def normalizer(t):
        if nometa:
            t = removeparenthesis(t,'(',')')
        if not hack:
            t = removeparenthesis(t,'[',']')
        #change all common ascci symbol characters we aren't going to use after this (, and ')
        t = replacemany(t, '_()[]{}-.!?#"', ' ')
        #strips just because the user may have made a mistake naming the source
        #(or the replacement above introduce boundary spaces)
        t = t.strip()
        #beginning and end definite articles in several european languages (people move them)
        #make sure we're only removing the capitalized start and end forms with spaces
        t = removesuffix(t, ', The')
        t = removeprefix(t, 'The ')
        t = removesuffix(t, ', Los')
        t = removeprefix(t, 'Los ')
        t = removesuffix(t, ', Las')
        t = removeprefix(t, 'Las ')
        t = removesuffix(t, ', Les')
        t = removeprefix(t, 'Les ')
        t = removesuffix(t, ', Le')
        t = removeprefix(t, 'Le ')
        t = removesuffix(t, ', La')
        t = removeprefix(t, 'La ')
        t = removesuffix(t, ', L\'')
        #L' sometimes ommits the space so always remove L' at the start even without space
        t = removeprefix(t, 'L\'')  #if there is a extra space the next join will remove it
        t = removesuffix(t, ', Der')
        t = removeprefix(t, 'Der ')
        t = removesuffix(t, ', Die')
        t = removeprefix(t, 'Die ')
        t = removesuffix(t, ', Das')
        t = removeprefix(t, 'Das ')
        t = removesuffix(t, ', El')
        t = removeprefix(t, 'El ')
        t = removesuffix(t, ', Os')
        t = removeprefix(t, 'Os ')
        t = removesuffix(t, ', As')
        t = removeprefix(t, 'As ')
        t = removesuffix(t, ', O')
        t = removeprefix(t, 'O ')
        t = removesuffix(t, ', A')
        t = removeprefix(t, 'A ')
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
        #remove the symbols used in the definite article normalization
        t = replacemany(t, ',\'', '')
        #normalize case
        t = t.lower()
        #this makes sure that if a remote name has ' and ' instead of ' _ ' to replace ' & ' it works
        #': ' doesn't need this because ':' is a forbidden character and both '_' and '-' turn to ''
        t = t.replace(' and ',  '')
        #although all names have spaces (now), the local names may have weird spaces,
        #so to equalize them after the space dependent checks (this also strips)
        t = ''.join(t.split())
        #remove diacritics (does nothing to asian languages diacritics, only for 2 to 1 character combinations)
        t = u''.join([c for c in unicodedata.normalize('NFKD', t) if not unicodedata.combining(c)])
        return t
    
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
    title_scorer = TitleScorer()
    #normalize with or without subtitles, besides the remote_names this is used on the iterated local names later
    norm = nosubtitle_normalizer if nosubtitle else normalizer
    #we choose the highest similarity of all 3 directories, since no mixed matches are allowed
    remote_names = set()
    remote_names.update(thumbs.Named_Boxarts.keys(), thumbs.Named_Titles.keys(), thumbs.Named_Snaps.keys())
    #turn into a set, original key and normalized value.
    remote_names = { x : norm(x) for x in remote_names }
    #temporary dir for downloads (required to prevent clobbering of files in case of no internet and filters being used)
    #parent directory of this temp dir is the same as the retroarch thumbnail dir to make moving the file just renaming it, not copy it
    #it may seem strange to use a tmp dir for a single file, but mktemp (the name, not open file version) is deprecated because of
    #a security risk of MitM. Not sure if this helps with that, but at least it won't stop working in the future once that is removed.
    with TemporaryDirectory(prefix='libretrofuzz', dir=thumbnails_directory) as tmpdir:
        for (name,destination) in names:
            #if called escape without being in a download zone, exit right away without a cancel print
            checkEscape()
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
            
            #make sure that local labels without any space and 'weird' capitalization get
            #split into spaces to normalize definite articles in normalization
            #(CamelCaseNames for labels are common when there are no spaces).
            #note that metadata like (US) turns into ( U S), since the spaces will get removed
            #after the check for definite articles, before being compared, it doesn't matter
            if ' ' not in nameaux:
                nameaux = ' '.join([s for s in re.split('([A-Z][^A-Z]*)', nameaux) if s])
                
            #unlike the server thumbnails, normalization wasn't done yet
            nameaux = norm(nameaux)
            
            #operate on cache (to speed up by not applying normalization every iteration)
            norm_thumbnail, i_max, thumbnail = process.extractOne(nameaux, remote_names, scorer=title_scorer,processor=None,score_cutoff=None) or (None, 0, None)

            #formating legos
            zero_format    = '  0 ' if verbose else ''
            prefix_format  = '{:>3} '.format(str(int(i_max))) if verbose else ''
            name_format    = f'{nameaux} -> {norm_thumbnail}' if verbose else f'{name} -> {thumbnail}'
            success_format = f'{prefix_format}{typer.style("Success", fg=typer.colors.GREEN, bold=True)}: {name_format}'
            failure_format = f'{prefix_format}{typer.style("Failure", fg=typer.colors.RED, bold=True)}: {name_format}'
            cancel_format  = f'{prefix_format}{typer.style("Skipped", fg=(135,135,135), bold=True)}: {name_format}'
            nomerge_format = f'{zero_format}{typer.style("Nomerge", fg=typer.colors.BLUE, bold=True)}: {name_format}'
            pending_format = f'{prefix_format}{typer.style("Pending", fg=typer.colors.MAGENTA, bold=True)}: {name_format}'
            if thumbnail and ( i_max >= CONFIDENCE or nofail ):
                #Thumbnails download destination is based on the db_name playlist on each and every playlist entry.
                #I'm not sure if those can differ in the same playlist, but to be safe, create them in each iteration of the loop.
                thumb_dir = Path(thumbnails_directory,destination)
                allow = True
                if not filters and nomerge:
                    #to implement no-merge you have to disable downloads on 'at least one' thumbnail (including user added ones)
                    missing_thumbs = 0
                    missing_server_thumbs = 0
                    for dirname in thumbs._fields:
                        p = Path(thumb_dir, dirname, name+'.png')
                        if not p.exists():
                            missing_thumbs += 1
                            if thumbnail in getattr(thumbs, dirname):
                                missing_server_thumbs += 1
                    allow = missing_thumbs == 3
                    #despite the above, print only for when it would download if it was allowed, otherwise it is confusing
                    if not allow and missing_server_thumbs > 0:
                        typer.echo(nomerge_format)
                if allow:
                    downloaded_list = []
                    try:
                        first_await = True
                        for dirname in thumbs._fields:
                            parent = Path(thumb_dir, dirname)
                            real = Path(parent, name + '.png')
                            tmp_parent = Path(tmpdir, dirname)
                            temp = Path(tmp_parent, name + '.png')
                            
                            #something to download
                            thumbset = getattr(thumbs, dirname)
                            if thumbnail in thumbset:
                                os.makedirs(parent, exist_ok=True)
                                os.makedirs(tmp_parent, exist_ok=True)
                                
                                thumbnail_type = dirname[6:-1]+': '
                                thumb_format   = f'{pending_format}' + '{bar:-10b}' f'{thumbnail_type}' '|{bar:10}|'
                                retry_count = MAX_RETRIES
                                downloaded = False
                                
                                async def download():
                                    nonlocal downloaded
                                    nonlocal retry_count
                                    nonlocal first_await
                                    with open(temp, 'w+b') as f:
                                        try:
                                            async with AsyncClient() as client:
                                                async with client.stream('GET', thumbset[thumbnail], timeout=15) as r:
                                                    length = int(r.headers['Content-Length'])
                                                    with tqdm.wrapattr(f, 'write', total=length, dynamic_ncols=True, bar_format=thumb_format, leave=False) as w:
                                                        #to give some minor time to cancel particular downloads the first time
                                                        if first_await:
                                                            first_await = False
                                                            await asyncio.sleep(delay)
                                                        async for chunk in r.aiter_raw(4096):
                                                            checkDownload()
                                                            w.write(chunk)
                                            downloaded = True
                                        except RequestError as e:
                                            retry_count = retry_count - 1
                                            downloaded = False
                                            if retry_count == 0:
                                                typer.echo(f'Exception: {e}', err=True)
                                        finally:
                                            if downloaded:
                                                downloaded_list.append((temp, real))
                                #with filters/reset you always download, but without,
                                #you only download if the file doesn't exist already (and isn't downloaded to temp already)
                                if filters:
                                    while not downloaded and retry_count > 0:
                                        await download()
                                else:
                                    while not downloaded and not real.exists() and retry_count > 0:
                                        await download()
                            elif filters:
                                #nothing to download but we want to remove images that may be there in the case of --filter.
                                real.unlink(missing_ok=True)
                    except StopProgram as e:
                        typer.echo(cancel_format)
                        raise e
                    except StopDownload as e:
                        downloaded_list = []
                        typer.echo(cancel_format)
                    for (temp, real) in downloaded_list:
                        shutil.move(temp, real)
                    if downloaded_list:
                        typer.echo(success_format)
            elif verbose:
                typer.echo(failure_format)

def fuzzsingle():
    typer.run(mainfuzzsingle)

def fuzzall():
    typer.run(mainfuzzall)

if __name__ == "__main__":
    typer.echo('Please run libretro-fuzz or libretro-fuzzall instead of running the script directly')
    raise typer.Abort()
