#!/usr/bin/env python3
"""
    unwebpack_sourcemap.py
    by rarecoil (github.com/rarecoil/unwebpack-sourcemap)

    Reads Webpack source maps and extracts the disclosed
    uncompiled/commented source code for review. Can detect and
    attempt to read sourcemaps from Webpack bundles with the `-d`
    flag. Puts source into a directory structure similar to dev.
"""

import gevent
from gevent import monkey
monkey.patch_all()
import argparse
import json
import os
import re
import string
import sys
from urllib.parse import urlparse
from unicodedata import normalize

import requests
from bs4 import BeautifulSoup, SoupStrainer

from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

class SourceMapExtractor(object):
    """Primary SourceMapExtractor class. Feed this arguments."""

    _target = None
    _method = "R"
    _attempt_sourcemap_detection = False
    _target_extracted_sourcemaps = []
    _is_local = False
    _path_sanitiser = None
    _f = open("results.txt","a",encoding='utf-8')

    def __init__(self, url,output,method):
        """Initialize the class."""
        """
        
        if not output:
            raise SourceMapExtractorError("output_directory must be set in options.")
        else:
            self._output_directory = os.path.abspath(options['output_directory'])
            if not os.path.isdir(self._output_directory):
                if options['make_directory'] is True:
                    os.mkdir(self._output_directory)
                else:
                    raise SourceMapExtractorError("output_directory does not exist. Pass --make-directory to auto-make it.")
        """
        self._output = output
        self._method = method
        self._target = target
        if self._method == "R":
            self._is_local = False
            self._attempt_sourcemap_detection = True
        elif self._method == "L":
            self._is_local = True 

            self._path_sanitiser = PathSanitiser(output+"/"+target.replace("//","_").replace(":","_").replace("/","_"))

        #self._path_sanitiser = PathSanitiser(self._output_directory)

        #if options['local'] == True:
        #    self._is_local = True

        #if options['detect'] == True:
        #    self._attempt_sourcemap_detection = True

        #self._validate_target(url)


    def run(self):
        """Run extraction process."""
        if self._is_local == False:
            if self._attempt_sourcemap_detection:
                detected_sourcemaps = self._detect_js_sourcemaps(self._target)
                
                for sourcemap in detected_sourcemaps:
                    self._f.write(sourcemap)
                    self._f.flush()
                    self._parse_remote_sourcemap(sourcemap)
            else:
                self._parse_remote_sourcemap(self._target)

        else:
            self._parse_sourcemap(self._target)


    def _validate_target(self, target):
        """Do some basic validation on the target."""
        parsed = urlparse(target)
        if self._is_local is True:
            
            self._target = os.path.abspath(target)
            if not os.path.isfile(self._target):
                raise SourceMapExtractorError("uri_or_file is set to be a file, but doesn't seem to exist. check your path.")
        else:
            if parsed.scheme == "":
                raise SourceMapExtractorError("uri_or_file isn't a URI, and --local was not set. set --local?")
            file, ext = os.path.splitext(parsed.path)
            self._target = target
            if ext != '.map' and self._attempt_sourcemap_detection is False:
                print("WARNING: URI does not have .map extension, and --detect is not flagged.")


    def _parse_remote_sourcemap(self, uri):
        """GET a remote sourcemap and parse it."""
        data = self._get_remote_data(uri)
        if data is not None:
            self._parse_sourcemap(data, True)
        else:
            print("WARNING: Could not retrieve sourcemap from URI %s" % uri)


    def _detect_js_sourcemaps(self, uri):
        """Pull HTML and attempt to find JS files, then read the JS files and look for sourceMappingURL."""
        remote_sourcemaps = []
        data = self._get_remote_data(uri)

        # TODO: scan to see if this is a sourcemap instead of assuming HTML
        print("Detecting sourcemaps in HTML at %s" % uri)
        script_strainer = SoupStrainer("script", src=True)
        try:
            soup = BeautifulSoup(data, "html.parser", parse_only=script_strainer)
        except:
            return []
            #raise SourceMapExtractorError("Could not parse HTML at URI %s" % uri)

        for script in soup:
            source = script['src']
            parsed_uri = urlparse(source)
            next_target_uri = ""
            if parsed_uri.scheme != '':
                next_target_uri = source
            else:
                current_uri = urlparse(uri)
                if source.startswith('//'):
                    next_target_uri = current_uri.scheme + ':' + source
                else:
                    built_uri = current_uri.scheme + "://" + current_uri.netloc + source
                    next_target_uri = built_uri

            js_data = self._get_remote_data(next_target_uri)
            if js_data:
                # get last line of file
                last_line = js_data.split("\n")[-1].strip()
                regex = "\\/\\/#\s*sourceMappingURL=(.*)$"
                matches = re.search(regex, last_line)
                if matches:
                    asset = matches.groups(0)[0].strip()
                    asset_target = urlparse(asset)
                    if asset_target.scheme != '':
                        print("Detected sourcemap at remote location %s" % asset)
                        remote_sourcemaps.append(asset)
                    else:
                        current_uri = urlparse(next_target_uri)
                        asset_uri = current_uri.scheme + '://' + \
                            current_uri.netloc + \
                            os.path.dirname(current_uri.path) + \
                            '/' + asset
                        print("Detected sourcemap at remote location %s" % asset_uri)
                        remote_sourcemaps.append(asset_uri)
        if remote_sourcemaps:
            temppath = self._output+"/"+uri.replace("//","_").replace(":","_").replace("/","_")
            try:
                os.mkdir(temppath)
            except:
                pass
            self._path_sanitiser = PathSanitiser(temppath)
        return remote_sourcemaps


    def _parse_sourcemap(self, target, is_str=False):
        map_data = ""
        if is_str is False:
            if os.path.isfile(target):
                with open(target, 'r', encoding='utf-8', errors='ignore') as f:
                    map_data = f.read()
        else:
            map_data = target

        # with the sourcemap data, pull directory structures
        try:
            map_object = json.loads(map_data)
        except json.JSONDecodeError:
            print("ERROR: Failed to parse sourcemap %s. Are you sure this is a sourcemap?" % target)
            return False
        except:
            return False

        # we need `sourcesContent` and `sources`.
        # do a basic validation check to make sure these exist and agree.
        if 'sources' not in map_object or 'sourcesContent' not in map_object:
            print("ERROR: Sourcemap does not contain sources and/or sourcesContent, cannot extract.")
            return False

        if len(map_object['sources']) != len(map_object['sourcesContent']):
            print("WARNING: sources != sourcesContent, filenames may not match content")

        idx = 0
        for source in map_object['sources']:
            if idx < len(map_object['sourcesContent']):
                path = source
                content = map_object['sourcesContent'][idx]
                idx += 1

                # remove webpack:// from paths
                # and do some checks on it
                write_path = self._get_sanitised_file_path(source)
                if write_path is not None:
                    try:
                        os.makedirs(os.path.dirname(write_path), mode=0o755, exist_ok=True)
                        with open(write_path, 'w', encoding='utf-8', errors='ignore') as f:
                            print("Writing %s..." % os.path.basename(write_path))
                            f.write(content)
                            f.write("\n")
                    except:
                        pass
            else:
                break

    def _get_sanitised_file_path(self, sourcePath):
        """Sanitise webpack paths for separators/relative paths"""
        sourcePath = sourcePath.replace("webpack:///", "")
        exts = sourcePath.split(" ")

        if exts[0] == "external":
            print("WARNING: Found external sourcemap %s, not currently supported. Skipping" % exts[1])
            return None

        path, filename = os.path.split(sourcePath)
        if path[:2] == './':
            path = path[2:]
        if path[:3] == '../':
            path = 'parent_dir/' + path[3:]
        if path[:1] == '.':
            path = ""

        filepath = self._path_sanitiser.make_valid_file_path(path, filename)
        return filepath

    def _get_remote_data(self, uri):
        """Get remote data via http."""
        try:
            result = requests.get(uri,verify=False,timeout=30)
        
            if result.status_code == 200:
                return result.text
            else:
                print("WARNING: Got status code %d for URI %s" % (result.status_code, uri))
                return False
        except:
            return False

class PathSanitiser(object):
    """https://stackoverflow.com/questions/13939120/sanitizing-a-file-path-in-python"""

    EMPTY_NAME = "empty"

    empty_idx = 0
    root_path = ""

    def __init__(self, root_path):
        self.root_path = root_path

    def ensure_directory_exists(self, path_directory):
        if not os.path.exists(path_directory):
            os.makedirs(path_directory)

    def os_path_separators(self):
        seps = []
        for sep in os.path.sep, os.path.altsep:
            if sep:
                seps.append(sep)
        return seps

    def sanitise_filesystem_name(self, potential_file_path_name):
        # Sort out unicode characters
        valid_filename = normalize('NFKD', potential_file_path_name).encode('ascii', 'ignore').decode('ascii')
        # Replace path separators with underscores
        for sep in self.os_path_separators():
            valid_filename = valid_filename.replace(sep, '_')
        # Ensure only valid characters
        valid_chars = "-_.() {0}{1}".format(string.ascii_letters, string.digits)
        valid_filename = "".join(ch for ch in valid_filename if ch in valid_chars)
        # Ensure at least one letter or number to ignore names such as '..'
        valid_chars = "{0}{1}".format(string.ascii_letters, string.digits)
        test_filename = "".join(ch for ch in potential_file_path_name if ch in valid_chars)
        if len(test_filename) == 0:
            # Replace empty file name or file path part with the following
            valid_filename = self.EMPTY_NAME + '_' + str(self.empty_idx)
            self.empty_idx += 1
        return valid_filename

    def get_root_path(self):
        # Replace with your own root file path, e.g. '/place/to/save/files/'
        filepath = self.root_path
        filepath = os.path.abspath(filepath)
        # ensure trailing path separator (/)
        if not any(filepath[-1] == sep for sep in self.os_path_separators()):
            filepath = '{0}{1}'.format(filepath, os.path.sep)
        self.ensure_directory_exists(filepath)
        return filepath

    def path_split_into_list(self, path):
        # Gets all parts of the path as a list, excluding path separators
        parts = []
        while True:
            newpath, tail = os.path.split(path)
            if newpath == path:
                assert not tail
                if path and path not in self.os_path_separators():
                    parts.append(path)
                break
            if tail and tail not in self.os_path_separators():
                parts.append(tail)
            path = newpath
        parts.reverse()
        return parts

    def sanitise_filesystem_path(self, potential_file_path):
        # Splits up a path and sanitises the name of each part separately
        path_parts_list = self.path_split_into_list(potential_file_path)
        sanitised_path = ''
        for path_component in path_parts_list:
            sanitised_path = '{0}{1}{2}'.format(sanitised_path,
                self.sanitise_filesystem_name(path_component),
                os.path.sep)
        return sanitised_path

    def check_if_path_is_under(self, parent_path, child_path):
        # Using the function to split paths into lists of component parts, check that one path is underneath another
        child_parts = self.path_split_into_list(child_path)
        parent_parts = self.path_split_into_list(parent_path)
        if len(parent_parts) > len(child_parts):
            return False
        return all(part1==part2 for part1, part2 in zip(child_parts, parent_parts))

    def make_valid_file_path(self, path=None, filename=None):
        root_path = self.get_root_path()
        if path:
            sanitised_path = self.sanitise_filesystem_path(path)
            if filename:
                sanitised_filename = self.sanitise_filesystem_name(filename)
                complete_path = os.path.join(root_path, sanitised_path, sanitised_filename)
            else:
                complete_path = os.path.join(root_path, sanitised_path)
        else:
            if filename:
                sanitised_filename = self.sanitise_filesystem_name(filename)
                complete_path = os.path.join(root_path, sanitised_filename)
            else:
                complete_path = complete_path
        complete_path = os.path.abspath(complete_path)
        if self.check_if_path_is_under(root_path, complete_path):
            return complete_path
        else:
            return None

class SourceMapExtractorError(Exception):
    pass

def readfile(pfile):
    fp = open(pfile,"r")
    content = fp.read()
    fp.close()
    return content

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A tool to extract code from Webpack sourcemaps. Turns black boxes into gray ones.")
    parser.add_argument("-m", "--method", default="L",help="L:Local, R:Remote")
    parser.add_argument("-d", "--detect", action="store_true", default=False,
        help="Attempt to detect sourcemaps from JS assets in retrieved HTML.")
    parser.add_argument("-o","--output", default="./output/",
        help="Make the output directory if it doesn't exist.")
    
    parser.add_argument("uri_or_file", help="The target URI or file.")

    args = parser.parse_args()
    targets = []
    #print(args["uri_or_file"])
    if args.method=="R" and os.path.exists(args.uri_or_file):
        
        targets = readfile(args.uri_or_file).split("\n")
    else:
        target = sys.argv[1]
        targets.append(target)
    
    for target in targets:
        print(target)
        if target:
            extractor = SourceMapExtractor(target,args.output,args.method)
            extractor.run()
    #extractor = SourceMapExtractor(vars(args))
    #extractor.run()
