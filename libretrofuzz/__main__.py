#! /usr/bin/env python3



#dependency install for testing: pip3 install thefuzz thefuzz[speedup] beautifulsoup4 typer[all] pick

#this downloads thumbnails for retroarch playlists
#it uses fuzzy matching to find the most similar name to the names, based on the playlist description.
#there may be false positives, especially if the thumbnail server does not have the game but does have
#another similarly named game - happens on series or playlists where multiple versions of a game coexist.

#Although a game playlist entry may have a different db this script doesn't handle that to simplify
#the caching of names, since it's rare, so it assumes all entries in a playlist will have the same system.




from pathlib import Path
from typing import Optional
from pick import pick
import typer
import json
import os
import io
import re
import string
from thefuzz import process, fuzz
from urllib.request import urlopen
import collections
import shutil
from bs4 import BeautifulSoup
from urllib.error import HTTPError, URLError
from urllib.request import unquote, quote


###########################################
########### SCRIPT SETTINGS ###############
###########################################


CONFIDENCE = 100

CONFIG = Path(Path.home(), '.config', 'retroarch', 'retroarch.cfg')

#00-1f are ascii control codes, rest is 'normal' illegal windows filename chars according to powershell + &
forbidden	=	r'[\u0022\u003c\u003e\u007c\u0000\u0001\u0002\u0003\u0004\u0005\u0006\u0007\u0008' + \
				r'\u0009\u000a\u000b\u000c\u000d\u000e\u000f\u0010\u0011\u0012\u0013\u0014\u0015' + \
				r'\u0016\u0017\u0018\u0019\u001a\u001b\u001c\u001d\u001e\u001f\u003a\u002a\u003f\u005c\u002f\u0026]' 

def getPlaylistsPath(cfg: Path):
	with open(cfg) as f:
	    file_content = '[DUMMY]\n' + f.read()
	import configparser
	configParser = configparser.RawConfigParser()
	configParser.read_string(file_content)
	playlist_dir = os.path.expanduser(configParser['DUMMY']['playlist_directory'].strip('"'))
	return Path(playlist_dir)

def getThumbnailsPath(cfg: Path):
	with open(cfg) as f:
	    file_content = '[DUMMY]\n' + f.read()
	import configparser
	configParser = configparser.RawConfigParser()
	configParser.read_string(file_content)
	thumbnails_directory = os.path.expanduser(configParser['DUMMY']['thumbnails_directory'].strip('"'))
	return Path(thumbnails_directory)

def mainaux(cfg: Path = typer.Argument(CONFIG, help='Path to the retroarch cfg file. If not provided, asked from the user.'),
		playlist: str = typer.Option(None, help='Playlist name to download thumbnails for. If not provided, asked from the user.'),
		system: str = typer.Option(None, help='Directory in the server to download thumbnails. If not provided, asked from the user.'),
		fail: bool = typer.Option(True, help=f'Fail if the similarity score is under {CONFIDENCE}, --no-fail may cause false positives, but can increase matches in sets with nonstandard names.'),
		meta: bool = typer.Option(True, help='Match name () delimited metadata, --no-meta may cause false positives, but can increase matches in sets with nonstandard names.'),
		dump: bool = typer.Option(False, help='Match name [] delimited metadata, --dump may cause false positives, but can increase matches for hacks, if the hack has thumbnails.'),
		subtitle: bool = typer.Option(True, help='Match name before the last hyphen, --no-subtitle may cause false positives, but can increase matches in sets with incomplete names.'),
		rmspaces: bool = typer.Option(False, help='Instead of uniquifying spaces in normalization, remove them, --rmspaces may cause false negatives, but some sets do not have spaces in the title. Best used with --no-dump --no-meta --no-subtitle.'),
		before: Optional[str] = typer.Option(None, help='Use only the part of the name before TEXT to match. TEXT may not be inside of a parenthesis of any kind. This operates only on the playlist names, implies --nodump and --no-meta and may cause false positives but some sets do not have traditional separators.')
	):
	"""
	libretrofuzz downloads covers from the libretro thumbnails server and adapts their names to current playlist names.
	To update this program with pip installed, type:

	pip3 install --upgrade git+https://github.com/i30817/libretrofuzz.git
	"""
	if not cfg.exists() or not cfg.is_file():
		typer.echo(f'Invalid Retroarch cfg file: {cfg}')
		raise typer.Abort()
	
	playlist_dir = getPlaylistsPath(cfg)
	
	if not playlist_dir.exists() or not playlist_dir.is_dir():
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
	destination = os.path.basename(playlist)[:-4] #to allow playlists different thumbnail sources than the system name use the playlist name
	thumb_dir = Path(getThumbnailsPath(cfg),destination) 
	lr_thumbs = 'https://thumbnails.libretro.com/'+quote(system) #then get the thumbnails from the system name
	
	#save a 'source/system' config file to only download files when no thumbnail of the 3 types exists, when they're not being downloaded from the original source.
	#this is to minimize cases where you download from a remote source system and then try to fill misses with another system and end up with thumbnails from both
	#in a single game entry.
	config_source = Path(thumb_dir, 'source')
	system_source = None
	if config_source.exists():
		with open(config_source) as f:
			system_source = f.readline()
	else:
		with open(config_source, 'w') as f:
			f.writelines([system])
			system_source = system
	
	if system_source != system:
		print(f'Warning: original system {system_source} != current system {system}, if any thumbnail of the same name in the the set (boxart,snap,title) already exists, download of the missing ones will be skipped. Delete {config_source} to change original system and allow mixed downloads, or delete particular thumbnails files if you do not want to mix')
	
	names = []
	
	with open(playlist) as f:
		data = json.load(f)
		for r in data['items']:
			names.append(r['label'])
			
	if len(names) == 0:
		typer.echo(f'No names found in playlist {playlist}')
		raise typer.Abort()
	
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
	
	#before implies that the names of the playlists may be cut, so the dump and meta matching must be disabled
	if before:
		dump = False
		meta = False
	
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
		if not meta:
			t = removeparenthesis(t,'(',')')
		if not dump:
			t = removeparenthesis(t,'[',']')
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
		#definite articles in several european languages
		#with two forms because people keep moving them to the end
		t = t.replace(', The', '')
		t = t.replace('The ',  '')
		t = t.replace(', Le', '')
		t = t.replace('Le ',  '')
		t = t.replace(', La', '')
		t = t.replace('La ',  '')
		t = t.replace(', Les', '')
		t = t.replace('Les ',  '')
		t = t.replace(', Der', '')
		t = t.replace('Der ',  '')
		t = t.replace(', Die', '')
		t = t.replace('Die ',  '')
		t = t.replace(', Das', '')
		t = t.replace('Das ',  '')
		t = t.replace(', El', '')
		t = t.replace('El ',  '')
		t = t.replace(', Los', '')
		t = t.replace('Los ',  '')
		t = t.replace(', Las', '')
		t = t.replace('Las ',  '')
		t = t.replace(', O', '')
		t = t.replace('O ',  '')
		t = t.replace(', A', '')
		t = t.replace('A ',  '')
		t = t.replace(', Os', '')
		t = t.replace('Os ',  '')
		t = t.replace(', As', '')
		t = t.replace('As ',  '')
		t = t.replace('\'',  '')
		#remove all punctuation
		t = replacemany(t, '.!?#', '')
		#remove all metacharacters
		t = replacemany(t, '_()[]{},-', ' ')
		if rmspaces:
			t = re.sub('\s', '', t).lower().strip()
		else:
			t = re.sub('\s+', ' ', t).lower().strip()
		return t
	
	def myscorer(s1, s2, force_ascii=True, full_process=True):
		similarity = fuzz.token_set_ratio(s1,s2,force_ascii,full_process)
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
	
	def nosubtitle(t,subtitle_marker=' - '):
		#Ignore metadata (but do not delete) and get the string before it
		no_meta = re.search(r'(^[^[({]*)', t)
		#last subtitle marker and everything there until the end (last because i noticed that 'subsubtitles' exist, 
		#for instance, ultima 7 - part 1|2 - subtitle
		subtitle = re.search(rf'.*({subtitle_marker}.*)', no_meta.group(1) if no_meta else t)
		if subtitle:
			t = t[0:subtitle.start(1)] + ' ' + t[subtitle.end(1):]
		return t
	
	def nosubtitle_normalizer(t):
		return normalizer(nosubtitle(t))
	
	#preprocess data so it's not redone every loop iteration.
	
	#normalize with or without subtitles, besides the remote_names this is used on the iterated local names later
	norm = normalizer if subtitle else nosubtitle_normalizer
	#we choose the highest similarity of all 3 directories, since no mixed matches are allowed
	remote_names = set()
	remote_names.update(thumbs.Named_Boxarts.keys(), thumbs.Named_Snaps.keys(), thumbs.Named_Titles.keys())
	#turn into a list of tuples, original and normalized
	remote_names = list(map(lambda x: (x, norm(x)), remote_names))
	
	for name in names:
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
		if not subtitle:
			nameaux = nosubtitle(nameaux, ': ')
		
		#only the local names should have forbidden characters
		name = re.sub(forbidden, '_', name )
		nameaux = re.sub(forbidden, '_', nameaux )
		#unlike the server thumbnails, this wasn't done yet
		nameaux = norm(nameaux)

		#operate on tuples to have a inbuilt cache (to speed up by not applying normalization every iteration)
		(thumbnail, _), i_max = process.extractOne((_,nameaux), remote_names, processor=lambda x: x[1], scorer=myscorer)
		
		if thumbnail != '' and ( i_max >= CONFIDENCE or not fail ):
			#when the original system source is different from the current system source do not allow downloads
			#unless all 3 possible thumbnails do not exist, to prevent mixed downloads
			allow = True
			if system_source != system:
				def thumbcheck(thumb_path):
					p = Path(thumb_dir, thumb_path, name+'.png')
					return not p.exists() or os.path.getsize(p) == 0
				allow = all(map(thumbcheck, thumbs._fields))
			if allow:
				print("{:>5}".format(str(i_max)+'% ') + f'Success: {nameaux} -> {norm(thumbnail)}')
				for dirname in thumbs._fields:
					thumbmap = getattr(thumbs, dirname)
					if thumbnail in thumbmap:
						p = Path(thumb_dir, dirname)
						os.makedirs(p, exist_ok=True)
						p = Path(p, name + '.png')
						#broken file
						if p.exists() and os.path.getsize(p) == 0:
							p.unlink(missing_ok=True)
						#will only happen if a new image or the user deletes a existing image,
						#still opened in w+b mode in case i change my mind
						retry_count = 3
						while not p.exists() and retry_count > 0:
							with open(p, 'w+b') as f:
								try:
									f.write(urlopen(thumbmap[thumbnail], timeout=30).read())
								except Exception as e:
									print(e)
									retry_count = retry_count - 1
									p.unlink(missing_ok=True)
		else:
			print("{:>5}".format(str(i_max)+'% ') + f'Failure: {nameaux} -> {norm(thumbnail)}')

def main():
	typer.run(mainaux)
	return 0

if __name__ == "__main__":
	typer.run(mainaux)
