#! /usr/bin/env python3
#dependency install: pip3 install thefuzz thefuzz[speedup] beautifulsoup4 typer[all] pick

#this downloads thumbnails for retroarch playlists
#it uses fuzzy matching to find the most similar name to the names, based on the playlist description.
#there may be false positives, especially if the thumbnail server does not have the game but does have
#another similarly named game - happens on series a lot.

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

#00-1f are ascii control codes, rest is 'normal' illegal windows filename chars according to powershell
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
		system: str = typer.Option(None, help='Directory in the server to download thumbnails from.'),
		fail: bool = typer.Option(True, help=f'Fail if the similarity score is under {CONFIDENCE}, --no-fail may cause false positives, but can increase matches in sets with nonstandard names.'),
		meta: bool = typer.Option(True, help='Match name () delimited metadata, --no-meta may cause false positives, but can increase matches in sets with nonstandard names.'),
		dump: bool = typer.Option(False, help='Match name [] delimited metadata, --dump may cause false positives, but can increase matches for hacks, if the hack has thumbnails.'),
		subtitle: bool = typer.Option(True, help='Match name before the last hyphen, --no-subtitle may cause false positives, but can increase matches in sets with incomplete names.'),
		rmspaces: bool = typer.Option(False, help='Instead of uniquifying spaces in normalization, remove them, --rmspaces may cause false negatives, but some sets do not have spaces in the title. Best used with --no-dump --no-meta --no-subtitle.'),
		before: Optional[str] = typer.Option(None, help='Use only the part of the name before TEXT to match. TEXT may not be inside of a parenthesis of any kind. This operates only on the playlist names, implies --nodump and --no-meta and may cause false positives but some sets do not have traditional separators.')
	):
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
	thumb_dir = Path(getThumbnailsPath(cfg),system)
	lr_thumbs = 'https://thumbnails.libretro.com/'+quote(system)
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
			l1 = { unquote(Path(node.get('href')).name) : lr_thumb+node.get('href') for node in soup.find_all('a') if node.get('href').endswith('.png')}
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
	
	for name in names:
		#this is tricky: to be able to see the thumbnails, 
		#the filenames must match the playlist labels, minus forbidden characters
		#but to allow the 'before' command, the string sent to fuzzy matching must not have things after 'before'
		#in order to not find the wrong 'before' if the before string is '_' (the forbidden chararacter replacement)
		#which it is in the example and set that inspired this, save just the index, then send the substring for fuzzy matching
		before_index = -1
		if before:
			#Ignore metadata and get the string before it
			no_meta = re.search(r'(^[^[({]*)', name)
			if no_meta:
				before_index = no_meta.group(1).find(before)
		#only the local names should have forbidden characters
		name = re.sub(forbidden, '_', name )
		#the shortname will have the 'main' thumbnail download if there are multiple versions,
		#and will be symlinked by possibly multiple names, so it should remove metadata to only download once
		shortname = re.sub(r'\([^)]*\)', '', name)
		shortname = re.sub(r'\[[^]]*\]', '', shortname)
		
		shortname = shortname.strip() + '.png'
		name = name + '.png'

		def replacemany(our_str, to_be_replaced, replace_with):
			for nextchar in to_be_replaced:
				our_str = our_str.replace(nextchar, replace_with)
			return our_str
		
		def normalizer(t):
			#remove extension for possible strip and to shorten the string length
			t = t[:-4]
			if not meta:
				t = re.sub(r'\([^)]*\)', '', t)
			if not dump:
				t = re.sub(r'\[[^]]*\]', '', t)
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
			t = t.replace(', The', '')
			t = t.replace('The ',  '')
			t = t.replace(', Le', '')
			t = t.replace('Le ',  '')
			t = t.replace(', La', '')
			t = t.replace('La ',  '')
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
			
		def nosubtitle_normalizer(t):
			#Ignore metadata and get the string before it
			no_meta = re.search(r'(^[^[({]*)', t)
			if no_meta :
				subtitle = re.search(r'( - .*)', no_meta.group(1))
				if subtitle:
					t = t[0:subtitle.start(1)] + ' ' + t[subtitle.end(1):]
			return normalizer(t)
		
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
				#this removes many false positives and few false negatives.
				return 0
			else:
				if s1 == s2:
					return 200
				return similarity + prefix
		
		#to make sure we have the highest similar name from the 3 possible directories, 
		#check them and chose the 'highest' for all, if it actually exists.	
		remote_names = set()
		remote_names.update(thumbs.Named_Boxarts.keys(), thumbs.Named_Snaps.keys(), thumbs.Named_Titles.keys())
		#with or without subtitles
		norm = normalizer if subtitle else nosubtitle_normalizer
		#with or without everything before the 'before' string
		nameaux = name[0:before_index] + '.png' if before_index != -1 else name
		thumbnail, i_max = process.extractOne(nameaux, remote_names, processor=norm, scorer=myscorer)
		
		if thumbnail != '' and ( i_max >= CONFIDENCE or not fail ):
			print("{:>5}".format(str(i_max)+'% ') + f'Success: {norm(nameaux)} -> {norm(thumbnail)}')
			o = os.getcwd()
			for dirname in thumbs._fields:
				thumbmap = getattr(thumbs, dirname)
				if thumbnail in thumbmap:
					p = Path(thumb_dir, dirname)
					os.makedirs(p, exist_ok=True)
					os.chdir(p)
					p = Path(p, shortname)
					#if a new match has better chance than a old, remove the old symlink
					#on unpriviledged windows, nothing is a symlink
					if p.is_symlink() or (p.exists() and os.path.getsize(p) == 0):
						try:
							os.unlink(p)
						except Exception as e:
							pass
					#will only happen if the user deletes a existing image, but 
					#still opened in w+b mode in case i change my mind
					while not p.exists():
						with open(p, 'w+b') as f:
							try:
								f.write(urlopen(thumbmap[thumbnail], timeout=30).read())
							except Exception as e:
								print(e)
								p.unlink(missing_ok=True)
					try:
						os.unlink(name)
					except Exception as e:
						pass
					try:
						os.symlink(shortname,name)
					except OSError as e:
						#windows unprivileged users can't create symlinks
						shutil.copyfile(shortname,name)
			os.chdir(o)
		else:
			print("{:>5}".format(str(i_max)+'% ') + f'Failure: {norm(nameaux)} -> {norm(thumbnail)}')

def main():
	typer.run(mainaux)
	return 0

if __name__ == "__main__":
	typer.run(mainaux)
