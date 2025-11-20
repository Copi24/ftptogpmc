#!/usr/bin/env python3
"""
Google Photos WebDAV Server

Exposes Google Photos content as a WebDAV server for VR headsets.
Features:
- Merges folders with similar names (e.g. "Movie" and "Movie$")
- Streams files directly from Google Photos
- Uses Google Photos API directly for album listing (bypassing local cache issues)
"""

import os
import sys
import logging
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
from io import BytesIO

from wsgidav.wsgidav_app import WsgiDAVApp
from wsgidav.dav_provider import DAVProvider, DAVCollection, DAVNonCollection
from wsgidav.dav_error import DAVError, HTTP_NOT_FOUND
from cheroot import wsgi

try:
    from gpmc import Client
    import requests
except ImportError:
    print("ERROR: gpmc or requests library not installed.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
WEBDAV_PORT = 8080
WEBDAV_HOST = "0.0.0.0"
GP_AUTH_DATA = os.environ.get("GP_AUTH_DATA", "")


class GPhotosResource(DAVNonCollection):
    """Represents a file in Google Photos"""
    
    def __init__(self, path, environ, file_info, client):
        super().__init__(path, environ)
        self.file_info = file_info
        self.client = client
        self.name = file_info['name']
        self.size = int(file_info.get('size', 0))
        self.media_key = file_info['media_key']
        
    def get_content_length(self):
        return self.size
    
    def get_content_type(self):
        # Determine content type from filename
        ext = os.path.splitext(self.name)[1].lower()
        content_types = {
            '.mkv': 'video/x-matroska',
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime',
            '.m4v': 'video/x-m4v',
            '.jpg': 'image/jpeg',
            '.png': 'image/png',
        }
        return content_types.get(ext, 'application/octet-stream')
    
    def get_creation_date(self):
        return None
    
    def get_last_modified(self):
        return None
        
    def get_etag(self):
        return None

    def support_etag(self):
        return False

    def support_ranges(self):
        return False
    
    def get_content(self):
        """Stream file from Google Photos"""
        logger.info(f"Streaming file: {self.name}")
        try:
            # Get media item info
            item = self.client.api.get_media_item(self.media_key)
            if not item:
                raise DAVError(HTTP_NOT_FOUND, f"Media item not found: {self.media_key}")
            
            # Get download URL
            # For videos, 'dv' = download video. For images, 'd' = download.
            download_url = f"{item['baseUrl']}=dv"
            
            # Stream the content
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            
            # Return raw stream which is file-like and compatible with WsgiDAV
            return response.raw
            
        except Exception as e:
            logger.error(f"Error streaming file: {e}")
            raise DAVError(HTTP_NOT_FOUND, str(e))

class GPhotosCollection(DAVCollection):
    """Represents a folder (merged album) in Google Photos"""
    
    def __init__(self, path, environ, album_name, provider):
        super().__init__(path, environ)
        self.album_name = album_name
        self.provider = provider
        
    def get_member_names(self):
        """List files in this album"""
        files = self.provider.get_files_in_album(self.album_name)
        return [f['name'] for f in files]
    
    def get_member(self, name):
        """Get a specific file"""
        files = self.provider.get_files_in_album(self.album_name)
        for f in files:
            if f['name'] == name:
                file_path = f"{self.path}/{name}"
                return GPhotosResource(file_path, self.environ, f, self.provider.client)
        return None


class GPhotosProvider(DAVProvider):
    """WebDAV provider for Google Photos"""
    
    def __init__(self):
        super().__init__()
        self.client = None
        self.albums_map = {}
        self._init_client()
        
    def _init_client(self):
        """Initialize GPMC client and fetch albums"""
        try:
            self.client = Client(auth_data=GP_AUTH_DATA)
            logger.info(f"Initialized client")
            
            # Fetch albums from API
            logger.info("Fetching albums from Google Photos API...")
            self.albums_map = {}
            try:
                # get_albums returns a generator or list of album dicts
                albums = self.client.api.get_albums()
                count = 0
                for album in albums:
                    count += 1
                    title = album.get('title', 'Untitled')
                    
                    # Merging logic
                    clean_title = title.rstrip('$')
                    
                    if clean_title not in self.albums_map:
                        self.albums_map[clean_title] = []
                    self.albums_map[clean_title].append(album)
                    
                logger.info(f"Fetched {count} albums from API")
                logger.info(f"Merged into {len(self.albums_map)} logical albums: {list(self.albums_map.keys())}")
                
            except Exception as e:
                logger.error(f"Failed to fetch albums: {e}", exc_info=True)
                self.albums_map = {}
                
        except Exception as e:
            logger.error(f"Failed to init GPMC client: {e}")
            raise
    
    def get_merged_albums(self) -> Dict[str, List[str]]:
        """Get list of merged albums"""
        return {k: [a.get('title') for a in v] for k, v in self.albums_map.items()}
    
    def get_files_in_album(self, merged_album_name: str) -> List[Dict]:
        """Get all files in a merged album via API"""
        if merged_album_name not in self.albums_map:
            return []
            
        files = []
        try:
            for album in self.albums_map[merged_album_name]:
                album_id = album.get('id')
                logger.info(f"Fetching media for album: {album.get('title')} ({album_id})")
                
                # Fetch media items for this album
                # search_media returns generator of media items
                media_items = self.client.api.search_media(album_id=album_id)
                
                for item in media_items:
                    # Extract necessary info
                    filename = item.get('filename')
                    if not filename:
                        continue
                        
                    # Calculate size if possible, else default
                    meta = item.get('mediaMetadata', {})
                    w = int(meta.get('width', 0))
                    h = int(meta.get('height', 0))
                    # Rough estimate if size not provided (video size usually not in metadata directly as bytes)
                    # We'll use a dummy size if not present, as WebDAV clients might need it for progress bars but streaming works without exact size
                    size = 100000000 # 100MB dummy
                        
                    files.append({
                        'name': filename,
                        'media_key': item.get('id'),
                        'size': size,
                        'mimeType': item.get('mimeType')
                    })
                            
        except Exception as e:
            logger.error(f"Error getting files for album {merged_album_name}: {e}")
            
        return files
    
    def get_resource_inst(self, path, environ):
        """Get resource instance for a given path"""
        logger.debug(f"Getting resource for path: {path}")
        
        # Normalize path
        path = path.rstrip('/')
        if not path or path == '/':
            # Root collection
            return GPhotosRootCollection('/', environ, self)

        # Check for special files
        name = path.strip('/')
        if name in ['debug.txt', 'webdav.log', 'cache_update.log', 'tunnel.log']:
            if name == 'debug.txt':
                return DebugResource(path, environ, self)
            else:
                return LogResource(path, environ, name)
        
        # Split path
        parts = path.strip('/').split('/')
        
        if len(parts) == 1:
            # Album folder
            album_name = parts[0]
            albums = self.get_merged_albums()
            if album_name in albums:
                return GPhotosCollection(path, environ, album_name, self)
            return None
            
        elif len(parts) == 2:
            # File in album
            album_name = parts[0]
            file_name = parts[1]
            files = self.get_files_in_album(album_name)
            for f in files:
                if f['name'] == file_name:
                    return GPhotosResource(path, environ, f, self.client)
            return None
        
        return None

class LogResource(DAVNonCollection):
    """Exposes a local log file"""
    def __init__(self, path, environ, file_path):
        super().__init__(path, environ)
        self.file_path = file_path
        self.name = os.path.basename(file_path)
        self.size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    def get_content_length(self):
        return self.size

    def get_content_type(self):
        return "text/plain"

    def get_creation_date(self):
        return None

    def get_last_modified(self):
        return None
        
    def get_etag(self):
        return None

    def support_etag(self):
        return False

    def support_ranges(self):
        return False

    def get_content(self):
        if os.path.exists(self.file_path):
            return open(self.file_path, 'rb')
        return BytesIO(b"Log file not found")

class DebugResource(DAVNonCollection):
    """Exposes server debug info as a text file"""
    def __init__(self, path, environ, provider):
        super().__init__(path, environ)
        self.provider = provider
        self.name = "debug.txt"
        self.size = 0 

    def get_content_length(self):
        return len(self.get_content().getvalue())

    def get_content_type(self):
        return "text/plain"

    def get_creation_date(self):
        return None

    def get_last_modified(self):
        return None
        
    def get_etag(self):
        return None

    def support_etag(self):
        return False

    def support_ranges(self):
        return False

    def get_content(self):
        out = BytesIO()
        def w(s):
            try:
                out.write(str(s).encode('utf-8') + b"\n")
            except:
                pass

        try:
            w("=== WebDAV Server Debug Info ===")
            w(f"CWD: {os.getcwd()}")
            w(f"Directory listing: {os.listdir('.')}")
            
            if self.provider:
                w(f"Albums Map Keys: {list(self.provider.albums_map.keys())}")
                w(f"Total Albums: {len(self.provider.albums_map)}")
            else:
                w("Provider is None")
                
        except Exception as e:
            w(f"CRITICAL ERROR: {e}")
            import traceback
            w(traceback.format_exc())
            
        out.seek(0)
        return out

class GPhotosRootCollection(DAVCollection):
    """Root collection showing all merged albums and debug files"""
    
    def __init__(self, path, environ, provider):
        super().__init__(path, environ)
        self.provider = provider
        self.special_files = ['debug.txt', 'webdav.log', 'cache_update.log', 'tunnel.log']
        
    def get_member_names(self):
        """List all merged albums + debug files"""
        albums = self.provider.get_merged_albums()
        return list(albums.keys()) + self.special_files
    
    def get_member(self, name):
        """Get a specific album or debug file"""
        if name == 'debug.txt':
            return DebugResource('/debug.txt', self.environ, self.provider)
        elif name in ['webdav.log', 'cache_update.log', 'tunnel.log']:
            return LogResource(f'/{name}', self.environ, name)
            
        albums = self.provider.get_merged_albums()
        if name in albums:
            return GPhotosCollection(f"/{name}", self.environ, name, self.provider)
        return None


def main():
    """Start WebDAV server"""
    config = {
        "host": WEBDAV_HOST,
        "port": WEBDAV_PORT,
        "provider_mapping": {
            "/": GPhotosProvider(),
        },
        "simple_dc": {"user_mapping": {"*": {"user": {"password": "12345"}}}},
        "verbose": 1,
    }
    
    app = WsgiDAVApp(config)
    
    server_args = {
        "bind_addr": (WEBDAV_HOST, WEBDAV_PORT),
        "wsgi_app": app,
    }
    
    server = wsgi.Server(**server_args)
    logger.info(f"Starting WebDAV server on {WEBDAV_HOST}:{WEBDAV_PORT}")
    logger.info("Username: user, Password: 12345")
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Stopping server...")
        server.stop()

if __name__ == "__main__":
    if not GP_AUTH_DATA:
        print("CRITICAL: GP_AUTH_DATA is missing! Cannot start.")
        sys.exit(1)
    main()
