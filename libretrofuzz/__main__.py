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
import subprocess
import configparser
#external libraries
import typer
from PIL import Image, ImageOps
from prompt_toolkit.input import create_input
from rapidfuzz import process, fuzz
from bs4 import BeautifulSoup
from questionary import Style, select
from httpx import RequestError, HTTPStatusError, Client, AsyncClient
from tqdm import trange, tqdm

#makes a class with these fields, which are the subdir names on the server system dir of the types of thumbnails
Thumbs = collections.namedtuple('Thumbs', ['Named_Boxarts', 'Named_Titles', 'Named_Snaps'])

###########################################
########### SCRIPT SETTINGS ###############
###########################################


CONFIDENCE = 100
MAX_RETRIES = 3
#00-1f are ascii control codes, rest is 'normal' illegal windows filename chars according to powershell + &
forbidden = r'[\u0022\u003c\u003e\u007c\u0000\u0001\u0002\u0003\u0004\u0005\u0006\u0007\u0008' + \
            r'\u0009\u000a\u000b\u000c\u000d\u000e\u000f\u0010\u0011\u0012\u0013\u0014\u0015' + \
            r'\u0016\u0017\u0018\u0019\u001a\u001b\u001c\u001d\u001e\u001f\u003a\u002a\u003f\u005c\u002f\u0026]'
#external terminal image viewer application
viewer = None

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
    CONFIG = Path(Path.home(), 'Library', 'Application Support', 'RetroArch', 'config', 'retroarch.cfg')
else: #all the rest based on linux. If they arent based on linux, they'll try the else and fail harmlessly later
    var = os.getenv('XDG_CONFIG_HOME')
    if var:
        CONFIG = Path(var, 'retroarch', 'retroarch.cfg')
    else:
        CONFIG = Path(Path.home(), '.config', 'retroarch', 'retroarch.cfg')

#-----------------------------------------------------------------------------
#keyboard listener, and exceptions to interrupt downloads or stop the program
#-----------------------------------------------------------------------------

class StopPlaylist(Exception):
    '''this is thrown when http status 521 happens.
    cloudflare uses when it can't find the server.
    Note, parts of server might still be available
    so this only stops a playlist in libretro-fuzzall'''
    def __init__(self):
        super().__init__()
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
    '''blocks key echoing for this console and recognizes most keys
       including many combinations, user kill still works, alt+tab...
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

#----------------non contextual str manipulation------------------------
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

def if_not_spaced_split_camelcase(name: str):
    """if the name is a no-space string, split the camelcase, if any"""
    if ' ' not in name:
        name = ' '.join([s for s in re.split('([A-Z][^A-Z]*)', name) if s])
    return name

def removefirst(name: str, suf: str):
    return name.replace(suf, '', 1)

def removeprefix(name: str, pre: str):
    if name.startswith(pre):
        return name[len(pre):]
    return name


#----------------Used to check the existence of a sixtel compatible terminal image viewer-------------------------------
def which(executable):
    flips = shutil.which(executable)
    if not flips:
        flips = shutil.which(executable, path=os.path.dirname(__file__))
    if not flips:
        flips = shutil.which(executable, path=os.getcwd())
    return flips


#-----------------------------------------------------------------------------------------------------------------------
#The heart of the program, what orders titles to be 'more similar' or less to the local labels (after the normalization)
#-----------------------------------------------------------------------------------------------------------------------
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


#-----------------------------------------------------------------------------------------------------------------------------
# Normalization functions, part of the functions that change both local labels and remote names to be more similar to compare
#-----------------------------------------------------------------------------------------------------------------------------
def normalizer(t, nometa, hack):
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
    t = removefirst(t, ', The')
    t = removeprefix(t, 'The ')
    t = removefirst(t, ', Los')
    t = removeprefix(t, 'Los ')
    t = removefirst(t, ', Las')
    t = removeprefix(t, 'Las ')
    t = removefirst(t, ', Les')
    t = removeprefix(t, 'Les ')
    t = removefirst(t, ', Le')
    t = removeprefix(t, 'Le ')
    t = removefirst(t, ', La')
    t = removeprefix(t, 'La ')
    t = removefirst(t, ', L\'')
    #L' sometimes ommits the space so always remove L' at the start even without space
    t = removeprefix(t, 'L\'')  #if there is a extra space the next join will remove it
    t = removefirst(t, ', Der')
    t = removeprefix(t, 'Der ')
    t = removefirst(t, ', Die')
    t = removeprefix(t, 'Die ')
    t = removefirst(t, ', Das')
    t = removeprefix(t, 'Das ')
    t = removefirst(t, ', El')
    t = removeprefix(t, 'El ')
    t = removefirst(t, ', Os')
    t = removeprefix(t, 'Os ')
    t = removefirst(t, ', As')
    t = removeprefix(t, 'As ')
    t = removefirst(t, ', O')
    t = removeprefix(t, 'O ')
    t = removefirst(t, ', A')
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

def nosubtitle_normalizer(t, nometa, hack):
    return normalizer(nosubtitle_aux(t), nometa, hack)


#---------------------------------------------------------------------------------
# Initalization functions, since there are two main programs so the code is reused
#---------------------------------------------------------------------------------
class RzipReader(object):
    """used to abstract the libretro compressed playlist format"""
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
                    yield io.TextIOWrapper(f)
            else:
                yield io.TextIOWrapper(file)

def readPlaylistAndPrepareDirectories(playlist: Path, temp_dir: Path, thumbnails_dir: Path):
    '''create directories that are children of temp_dir and thumbnails_dir that have the
       subdirs needed to move files created on them from one to the other, so you don't
       need to care to create directories for every file processed.
       
       return a list of game names and 'db_names' (stripped of extension): [(names: str,db_names: str)]
       db_names without extension are the system directory names libretro searchs for the thumbnail.
    '''
    names = []
    dbs   = set()
    try:
        with RzipReader(playlist).open() as f:
            data = json.load(f)
            for r in data['items']:
                assert 'label' in r and r['label'].strip() != '', f'\n{json.dumps(r,indent=4)} of playlist {playlist} has no label'
                assert 'db_name' in r and r['db_name'].endswith('.lpl'), f'\n{json.dumps(r,indent=4)} of playlist {playlist} has no valid db_name'
                #add the label name and the db name (it's a playlist name, minus the extension '.lpl')
                db = r['db_name'][:-4]
                dbs.add(db)
                names.append( (r['label'], db) )
    except json.JSONDecodeError:
        #older version of the playlist format, this has no error correction; the extra lines after the
        #game entries can be between 0 and 5, because retroarch will ignore lines missing at the end.
        with RzipReader(playlist).open() as f:
            #make sure not to count empty lines, which might break the assumptions made here
            data = [ x for x in map(str.strip, f.readlines()) if x ]
            gamelineslen = len(data) - (len(data) % 6)
            for i in range(0,gamelineslen, 6):
                name = data[i+1]
                db   = data[i+5][:-4]
                dbs.add(db)
                names.append( (name, db) )
    #create the directories we will 'maybe' need. This is not so problematic
    #as it seems since likely len(dbs) == 1, so 6 directories per playlist
    #versus having os.makedirs called hundred of times for larger playlists
    #this is vulnerable to ToCToU deletion but everything is with directories
    for parent in [temp_dir, thumbnails_dir]:
        for db in dbs:
            for dirname in Thumbs._fields:
                os.makedirs(Path(parent, db, dirname), exist_ok=True)
    return names

def getDirectoryPath(cfg: Path, setting: str):
    '''returns paths inside of a cfg file setting'''
    with open(cfg) as f:
        file_content = '[DUMMY]\n' + f.read()
    configParser = configparser.RawConfigParser()
    configParser.read_string(file_content)
    dirp = os.path.expanduser(configParser['DUMMY'][setting].strip('\t ').strip('"'))
    return Path(dirp)

def test_common_errors(cfg: Path, playlist: str, system: str):
    '''returns a tuple with (playlist_dir: Path, thumbnail_dir: Path, PLAYLISTS: [Path], SYSTEMS: [str]) '''
    global viewer
    viewer = which('chafa')
    if not viewer:
        typer.echo(f'Shell image viewer chafa was not found')
    if not cfg or not cfg.is_file():
        typer.echo(f'Invalid Retroarch cfg file: {cfg}')
        raise typer.Exit(code=1)
    thumbnails_directory = getDirectoryPath(cfg, 'thumbnails_directory')
    if not thumbnails_directory.is_dir():
        typer.echo(f'Invalid Retroarch thumbnails directory: {thumbnails_directory}')
        raise typer.Exit(code=1)
    playlist_dir = getDirectoryPath(cfg, 'playlist_directory')
    if not playlist_dir.is_dir():
        typer.echo(f'Invalid Retroarch playlist directory: {playlist_dir}')
        raise typer.Exit(code=1)
    PLAYLISTS = list(playlist_dir.glob('./*.lpl'))
    if not PLAYLISTS:
        typer.echo(f'Retroarch cfg file has empty playlist directory: {playlist_dir}')
        raise typer.Exit(code=1)
    if playlist and Path(playlist_dir, playlist) not in PLAYLISTS:
        typer.echo(f'Unknown user provided playlist: {playlist}')
        raise typer.Exit(code=1)

    try:
        with Client() as client:
            page = client.get('https://thumbnails.libretro.com/', timeout=15)
            soup = BeautifulSoup(page.text, 'html.parser')
        SYSTEMS = [ unquote(node.get('href')[:-1]) for node in soup.find_all('a') if node.get('href').endswith('/') and not node.get('href').endswith('../') ]
    except (RequestError,HTTPStatusError) as err:
        typer.echo(f'Could not get the remote thumbnail system names')
        raise typer.Exit(code=1)
    if system and system not in SYSTEMS:
        typer.echo(f'The user provided system name {system} does not match any remote thumbnail system names')
        raise typer.Exit(code=1)
    return (playlist_dir, thumbnails_directory, sorted(PLAYLISTS), sorted(SYSTEMS))


#####################
# Main programs code
#####################
def mainfuzzsingle(cfg: Path = typer.Argument(CONFIG, help='Path to the retroarch cfg file. If not default, asked from the user.'),
        playlist: str = typer.Option(None, metavar='NAME', help='Playlist name with labels used for thumbnail fuzzy matching. If not provided, asked from the user.'),
        system: str = typer.Option(None, metavar='NAME', help='Directory name in the server to download thumbnails. If not provided, asked from the user.'),
        wait_after: Optional[float] = typer.Option(None, '--delay-after', min=1, max=10, clamp=True, metavar='FLOAT', help='Seconds after download to skip replacing thumbnails. No effect if called with --no-image.'),
        wait_before: Optional[float] = typer.Option(None, '--delay', min=1, max=10, clamp=True, metavar='FLOAT', help='Seconds to skip thumbnails download.'),
        filters: Optional[List[str]] = typer.Option(None, '--filter', metavar='GLOB', help='Restricts downloads to game labels globs - not paths - in the playlist, can be used multiple times and matches reset thumbnails, --filter \'*\' downloads all.'),
        noimage: bool = typer.Option(False, '--no-image', help='Don\'t show images even with chafa installed.'),
        nomerge: bool = typer.Option(False, '--no-merge', help='Disables missing thumbnails download for a label if there is at least one in cache to avoid mixing thumbnails from different server directories on repeated calls. No effect if called with --filter.'),
        nofail: bool = typer.Option(False, '--no-fail', help='Download any score. To restrict or retry use --filter.'),
        nosubtitle: bool = typer.Option(False, '--no-subtitle', help='Remove subtitle after \' - \' or \': \' for mismatched labels and server names. \':\' can\'t occur in server names, so if the server has \'Name_ subtitle.png\' and not \'Name - subtitle.png\' (uncommon), this option doesn\'t help. To restrict or retry use --filter.'),
        nometa: bool = typer.Option(False, '--no-meta', help='Ignores () delimited metadata and may cause false positives. Forced with --before.'),
        hack: bool = typer.Option(False, '--hack', help='Matches [] delimited metadata and may cause false positives, Best used if the hack has thumbnails. Ignored with --before.'),
        before: Optional[str] = typer.Option(None, help='Use only the part of the label before TEXT to match. TEXT may not be inside of brackets of any kind, may cause false positives but some labels do not have traditional separators. Forces metadata to be ignored.'),
        verbose: bool = typer.Option(False, '--verbose', help='Shows the failures, score and normalized local and server names in output (score >= 100 is succesful).')
    ):
    if playlist and not playlist.lower().endswith('.lpl'):
        playlist = playlist + '.lpl'
    
    playlist_dir, thumbnails_dir, PLAYLISTS, SYSTEMS = test_common_errors(cfg, playlist, system)
    
    custom_style = Style([
        ('answer', 'fg:green bold'),
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
        system = select(question, SYSTEMS, style=custom_style, qmark='', default=playlist_sys if playlist_sys in SYSTEMS else None).ask()
        if not system:
            raise typer.Exit()
    
    async def runit():
        try:
            async with lock_keys(), AsyncClient() as client:
                #temporary dir for downloads (required to prevent clobbering of files in case of no internet and filters being used)
                #parent directory of this temp dir is the same as the retroarch thumbnail dir to make moving the file just renaming it, not copy it
                with TemporaryDirectory(prefix='libretrofuzz', dir=thumbnails_dir) as tmpdir:
                    typer.echo(typer.style(f'{playlist} -> {system}', bold=True))
                    names = readPlaylistAndPrepareDirectories(Path(playlist_dir, playlist), tmpdir, thumbnails_dir)
                    await downloader(names,system,wait_before,wait_after,filters,noimage,nomerge,nofail,nometa,hack,nosubtitle,verbose,before,tmpdir,thumbnails_dir,client)
        except StopPlaylist as e:
            typer.echo(typer.style(f'Cloudflare is down for {system}', fg=typer.colors.RED, bold=True))
            raise typer.Exit(code=1)
        except StopProgram as e:
            typer.echo(f'Cancelled by user')
            raise typer.Exit()
    asyncio.run(runit(), debug=False)

def mainfuzzall(cfg: Path = typer.Argument(CONFIG, help='Path to the retroarch cfg file. If not default, asked from the user.'),
        wait_after: Optional[float] = typer.Option(None, '--delay-after', min=1, max=10, clamp=True, metavar='FLOAT', help='Seconds after download to skip replacing thumbnails. No effect if called with --no-image.'),
        wait_before: Optional[float] = typer.Option(None, '--delay', min=1, max=10, clamp=True, metavar='FLOAT', help='Seconds to skip thumbnails download.'),
        filters: Optional[List[str]] = typer.Option(None, '--filter', metavar='GLOB', help='Restricts downloads to game labels globs - not paths - in the playlist, can be used multiple times and matches reset thumbnails, --filter \'*\' downloads all.'),
        noimage: bool = typer.Option(False, '--no-image', help='Don\'t show images even with chafa installed.'),
        nomerge: bool = typer.Option(False, '--no-merge', help='Disables missing thumbnails download for a label if there is at least one in cache to avoid mixing thumbnails from different server directories on repeated calls. No effect if called with --filter.'),
        nofail: bool = typer.Option(False, '--no-fail', help='Download any score. To restrict or retry use --filter.'),
        nosubtitle: bool = typer.Option(False, '--no-subtitle', help='Remove subtitle after \' - \' or \': \' for mismatched labels and server names. \':\' can\'t occur in server names, so if the server has \'Name_ subtitle.png\' and not \'Name - subtitle.png\' (uncommon), this option doesn\'t help. To restrict or retry use --filter.'),
        nometa: bool = typer.Option(False, '--no-meta', help='Ignores () delimited metadata and may cause false positives. Forced with --before.'),
        hack: bool = typer.Option(False, '--hack', help='Matches [] delimited metadata and may cause false positives, Best used if the hack has thumbnails. Ignored with --before.'),
        before: Optional[str] = typer.Option(None, help='Use only the part of the label before TEXT to match. TEXT may not be inside of brackets of any kind, may cause false positives but some labels do not have traditional separators. Forces metadata to be ignored.'),
        verbose: bool = typer.Option(False, '--verbose', help='Shows the failures, score and normalized local and server names in output (score >= 100 is succesful).')
    ):
    playlist_dir, thumbnails_dir, PLAYLISTS, SYSTEMS = test_common_errors(cfg, None, None)
    
    notInSystems = [ (playlist, os.path.basename(playlist)[:-4]) for playlist in PLAYLISTS if os.path.basename(playlist)[:-4] not in SYSTEMS]
    for playlist, system in notInSystems:
        PLAYLISTS.remove(playlist)
    inSystems = [ (playlist, os.path.basename(playlist)[:-4]) for playlist in PLAYLISTS ]
    
    async def runit():
        try:
            async with lock_keys(), AsyncClient() as client:
                with TemporaryDirectory(prefix='libretrofuzz', dir=thumbnails_dir) as tmpdir:
                    for playlist, system in notInSystems:
                        typer.echo(typer.style(f'Custom playlist skipped: ', fg=typer.colors.RED, bold=True)+typer.style(f'{system}.lpl', bold=True))
                    for playlist, system in inSystems:
                        typer.echo(typer.style(f'{system}.lpl -> {system}', bold=True))
                        names = readPlaylistAndPrepareDirectories(playlist, tmpdir, thumbnails_dir)
                        try:
                            await downloader(names,system,wait_before,wait_after,filters,noimage,nomerge,nofail,nometa,hack,nosubtitle,verbose,before,tmpdir,thumbnails_dir,client)
                        except StopPlaylist as e:
                            typer.echo(typer.style(f'Cloudflare is down for {system}', fg=typer.colors.RED, bold=True))
        except StopProgram as e:
            typer.echo(f'Cancelled by user')
            raise typer.Exit()
    asyncio.run(runit(), debug=False)

async def downloadgamenames(client, system):
    """returns [ dict(Game_Name, Game_Url), dict(Game_Name, Game_Url), dict(Game_Name, Game_Url) ]
        for each of the server directories '/Named_Boxarts/', '/Named_Titles/', '/Named_Snaps/'
        (potentially some of these dicts may be empty if the server doesn't have the directory)
    """
    lr_thumbs = 'https://thumbnails.libretro.com/'+quote(system) #then get the thumbnails from the system name
    args = []
    try:
        for tdir in ['/Named_Boxarts/', '/Named_Titles/', '/Named_Snaps/']:
            lr_thumb = lr_thumbs+tdir
            response = ''
            async with client.stream('GET', lr_thumb, timeout=15) as r:
                async for chunk in r.aiter_text(4096):
                    checkEscape()
                    response += chunk
            #not found is ok, since some server system directories do not have all the subdirectories
            if r.status_code == 404:
                l1 = {}
            elif r.status_code == 521:
                raise StopPlaylist()
            else:
                #will go to except if there is a another error
                r.raise_for_status()
                soup = BeautifulSoup(response, 'html.parser')
                l1 = { unquote(Path(node.get('href')).name[:-4]) : lr_thumb+node.get('href') for node in soup.find_all('a') if node.get('href').endswith('.png')}
            args.append(l1)
    except (RequestError,HTTPStatusError) as err:
        typer.echo(str(err))
        raise typer.Exit(code=1)
    return args

async def downloader(names: [(str,str)],
               system: str,
               wait_before: Optional[float],
               wait_after: Optional[float],
               filters: Optional[List[str]],
               noimage : bool, nomerge: bool, nofail: bool, nometa: bool, hack: bool, nosubtitle: bool, verbose: bool,
               before: Optional[str],
               tmpdir: Path,
               thumbnails_dir: Path,
               client: AsyncClient
               ):
    #not a error to pass a empty playlist
    if len(names) == 0:
        return
    
    thumbs = Thumbs._make( await downloadgamenames(client, system) )
    
    #before implies that the names of the playlists may be cut, so the hack and meta matching must be disabled
    if before:
        hack = False
        nometa = True
    
    #preprocess data so it's not redone every loop iteration.
    title_scorer = TitleScorer()
    #normalize with or without subtitles, besides the remote_names this is used on the iterated local names later
    norm = nosubtitle_normalizer if nosubtitle else normalizer
    #we choose the highest similarity of all 3 directories, since no mixed matches are allowed
    remote_names = set()
    remote_names.update(thumbs.Named_Boxarts.keys(), thumbs.Named_Titles.keys(), thumbs.Named_Snaps.keys())
    #turn into a set, original key and normalized value.
    remote_names = { x : norm(x, nometa, hack) for x in remote_names }
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
        
        #CamelCaseNames for local labels are common when there are no spaces,
        #do this to normalize definite articles in normalization with spaces only (minimizes changes)
        nameaux = if_not_spaced_split_camelcase(nameaux)
        #unlike the server thumbnails, normalization wasn't done yet
        nameaux = norm(nameaux, nometa, hack)
        
        #operate on cache (to speed up by not applying normalization every iteration)
        #the normalization can make it so that the winner has the same score as the runner up(s) so to make sure we catch at least
        #two thumbnails for cases where that happens, we check both best scores if we can't find a thumb in a server directory
        result = process.extract(nameaux, remote_names, scorer=title_scorer,processor=None,limit=2,score_cutoff=None)
        norm_thumbnail, i_max, thumbnail = (None, 0, None)
        thumbnail2 = None
        if len(result) == 1:
            norm_thumbnail, i_max, thumbnail = result[0]
        elif len(result) == 2:
            norm_thumbnail, i_max, thumbnail = result[0]
            if result[0][1] == result[1][1]: #equal scores
                thumbnail2 = result[1][2]
        #formating legos
        zeroth_format  = '  0 ' if verbose else ''
        prefix_format  = '{:>3} '.format(str(int(i_max))) if verbose else ''
        name_format    = f'{nameaux} -> {norm_thumbnail}' if verbose else f'{name} -> {thumbnail}'
        success_format = f'{prefix_format}{typer.style("Success",   fg=typer.colors.GREEN, bold=True)}: {name_format}'
        failure_format = f'{prefix_format}{typer.style("Failure",     fg=typer.colors.RED, bold=True)}: {name_format}'
        missing_format = f'{prefix_format}{typer.style("Missing",     fg=typer.colors.RED, bold=True)}:'
        cancel_format  = f'{prefix_format}{typer.style("Skipped",        fg=(135,135,135), bold=True)}: {name_format}'
        nomerge_format = f'{zeroth_format}{typer.style("Nomerge",        fg=(128,128,128), bold=True)}: {name_format}'
        getting_format = f'{prefix_format}{typer.style("Getting",    fg=typer.colors.BLUE, bold=True)}: {name_format}'
        waiting_format = f'{prefix_format}{typer.style("Waiting",  fg=typer.colors.YELLOW, bold=True)}: {name_format}' '{bar:-9b} {remaining_s:2.1f}s: {bar:10u}'
        if thumbnail and ( i_max >= CONFIDENCE or nofail ):
            #these parent directories were created when reading the playlist, more efficient than doing it a playlist game loop
            real_thumb_dir = Path(thumbnails_dir,destination)
            down_thumb_dir = Path(tmpdir,destination)
            allow = True
            if not filters and nomerge:
                #to implement no-merge you have to disable downloads on 'at least one' thumbnail (including user added ones)
                missing_thumbs = 0
                missing_server_thumbs = 0
                for dirname in Thumbs._fields:
                    real = Path(real_thumb_dir, dirname, name + '.png')
                    if not real.exists():
                        missing_thumbs += 1
                        if thumbnail in getattr(thumbs, dirname) or thumbnail2 in getattr(thumbs, dirname):
                            missing_server_thumbs += 1
                allow = missing_thumbs == 3
                #despite the above, print only for when it would download if it was allowed, otherwise it is confusing
                if not allow and missing_server_thumbs > 0:
                    typer.echo(nomerge_format)
            if allow:
                first_wait      = wait_before is not None
                downloaded_once = False
                downloaded_dict = dict() #dictionary of thumbnailtype -> (old Path, new Path), paths may not exist
                try:
                    for dirname in Thumbs._fields:
                        real = Path(real_thumb_dir, dirname, name + '.png')
                        temp = Path(down_thumb_dir, dirname, name + '.png')
                        downloaded_dict[dirname] = (real, temp)
                        #something to download
                        thumbmap = getattr(thumbs, dirname)
                        url = thumbmap[thumbnail] if thumbnail in thumbmap else thumbmap.get(thumbnail2, None)
                        #with filters/reset you always download, and without only if it doesn't exist already.
                        if url and (filters or not real.exists()):
                            download_format = f'{getting_format}' '{bar:-9b}' f'{dirname[6:-1]}' ' {percentage:3.0f}%: {bar:10u}'
                            if await download(client,url,temp,download_format,missing_format,waiting_format,first_wait,wait_before,MAX_RETRIES):
                                first_wait = False
                                downloaded_once = True
                        elif filters:
                            #nothing to download but we want to remove images that may be there in the case of --filter.
                            real.unlink(missing_ok=True)
                    if not noimage and viewer and downloaded_once:
                        displayImages(downloaded_dict)
                        if wait_after is not None:
                            await printwait(wait_after, waiting_format)
                except StopProgram as e:
                    typer.echo(cancel_format)
                    raise e
                except StopDownload as e:
                    downloaded_once = False
                    typer.echo(cancel_format)
                if downloaded_once:
                    for (old, new) in downloaded_dict.values():
                        if new.exists():
                            shutil.move(new, old)
                    typer.echo(success_format)
        elif verbose:
            typer.echo(failure_format)

async def printwait(wait : Optional[float], waiting_format: str):
    count = int(wait/0.1)
    for i in trange(count, dynamic_ncols=True, bar_format=waiting_format, colour='YELLOW', leave=False):
        checkDownload()
        await asyncio.sleep(0.1)

async def download(client, url, destination, download_format, missing_format, waiting_format, first_wait, wait_before, max_retries):
    '''returns True if downloaded. To download, it must have waited, if first_wait is True. Exceptions may happen instead of returning False, but they
    are all caught outside the caller loop of thumbnail types that downloads, so the first_wait guard gets reset again and only waits once per game'''
    while True:
        try:
            async with client.stream('GET', url, timeout=15) as r:
                if r.status_code == 521: #cloudflare exploded, skip the whole playlist
                    raise StopPlaylist()
                if r.status_code == 404: #broken image or symlink link, skip just this thumb
                    typer.echo(missing_format + ' ' + url)
                    return False
                r.raise_for_status()     #error before reading the header goes into retrying
                length = int(r.headers['Content-Length'])
                if length < 100:         #obviously corrupt 'thumbnail', skip this thumb
                    typer.echo(missing_format + ' ' + url)
                    return False
                with open(destination, 'w+b') as f:
                    if first_wait:
                        await printwait(wait_before, waiting_format)
                    with tqdm.wrapattr(f, 'write', total=length, dynamic_ncols=True, bar_format=download_format, colour='BLUE', leave=False) as w:
                        async for chunk in r.aiter_raw(4096):
                            checkDownload()
                            w.write(chunk)
            return True
        except IOError as e:
            typer.echo(str(e))
            raise typer.Exit(code=1)            
        except (RequestError,HTTPStatusError) as e:
            if max_retries <= 0:
                typer.echo(str(e))
                raise typer.Exit(code=1)
            max_retries -= 1

def displayImages(downloaded: dict):
    '''dict has all the tuple (old, new) with the key of the thumbnail type str (the files may not exist)
       this method will display the new images with a green border and the old with a gray border and missing as... missing
    '''
    imgs = dict()
    colors = dict()
    BORDER_SIZE = 4
    #first create images for the thumbnails
    #single frame pillow images automatically close when
    #their image data (not header) is accessed, so you
    #don't have to worry about closing 'the original'
    for k,i in downloaded.items():
        old, new = i
        #do not create the border yet, wait for the end of the method
        if new.exists():
            imgs[k] = Image.open(new).convert('RGBA')
            colors[k] = (135,255,0)
        elif old.exists():
            imgs[k] = Image.open(old).convert('RGBA')
            colors[k] = (128,128,128)
        else:
            #default transparent images, small enough to give primacy
            #to the others while still showing 'something missing'
            #size taken from the SNES snapshot default resolution
            imgs[k] = Image.new('RGBA', (256,224), (255, 0, 0, 0))
            colors[k] = (0,0,0,0) #transparent 'border'
    box   = imgs.get('Named_Boxarts', None)
    title = imgs.get('Named_Titles', None)
    snap  = imgs.get('Named_Snaps', None)
    #we are trying to make a rectangle, where the left side has the boxart, and the right side has the snap and title, stacked vertically.
    #the height of left and right will be the largest height on the available thumbnails, except for the borders (added after the resizes, so they look the same)
    wanted_box_y = max([ i.size[1] for i in imgs.values() ])
    x,y = box.size
    if y != wanted_box_y:
        x = max(round(x * wanted_box_y / y), 1)
        y = wanted_box_y
    box = box.resize((x,y))
    #the right side will adjust the width until title and snap are the same width and the desired height is reached (minus inner borders).
    wanted_y = wanted_box_y - BORDER_SIZE*2
    x1, y1 = title.size
    x2, y2 = snap.size
    #Formulas to derive the value of samex (same width they need to reach wanted_y):
    #wanted_y = yx1 + yx2 and yx1 = y1 * samex / x1 and yx2 = y2 * samex / x2
    #then:
    #wanted_y = y1/x1 * samex + y2/x2 * samex <=>
    #wanted_y/samex = y1/x1 + y2/x2           <=>
    #samex = wanted_y / (y1/x1 + y2/x2)
    x1,y1 = title.size
    x2,y2 = snap.size
    samex = wanted_y / (y1/x1 + y2/x2)
    new_y1 = max(1, round(y1 * samex / x1))
    new_y2 = max(1, round(y2 * samex / x2))
    samex  = max(1, round(samex))
    title = title.resize((samex, new_y1))
    snap  = snap.resize((samex, new_y2))
    #add borders
    box   = ImageOps.expand(box,   border=(BORDER_SIZE,)*4, fill=colors['Named_Boxarts'])
    title = ImageOps.expand(title, border=(BORDER_SIZE,)*4, fill=colors['Named_Titles'])
    snap  = ImageOps.expand(snap,  border=(BORDER_SIZE,)*4, fill=colors['Named_Snaps'])
    #create a 'paste' image
    combined = Image.new('RGBA', (box.size[0] + title.size[0], box.size[1]) )
    combined.paste(box, box=(0,0))
    combined.paste(title, box=(box.size[0],0))
    combined.paste(snap, box=(box.size[0], title.size[1]))
    #save it, print TODO if a _good_ python sixtel library happens, replace this
    with io.BytesIO() as f:
        combined.save(f, format='png')
        subprocess.run([viewer, '-'], input=f.getbuffer())

def fuzzsingle():
    typer.run(mainfuzzsingle)

def fuzzall():
    typer.run(mainfuzzall)

if __name__ == "__main__":
    typer.echo('Please run libretro-fuzz or libretro-fuzzall instead of running the script directly')
    raise typer.Exit(code=1)
