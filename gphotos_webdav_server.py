#!/usr/bin/env python3
"""
Google Photos WebDAV Server

Exposes Google Photos content as a WebDAV server for VR headsets.
Features:
- Merges folders with similar names (e.g. "Movie" and "Movie$")
- Streams files directly from Google Photos
- Uses local GPMC cache for directory structure
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
        self.size = file_info['size']
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
    
    def get_content(self):
        """Stream file from Google Photos"""
        logger.info(f"Streaming file: {self.name}")
        try:
            # Get media item info
            item = self.client.api.get_media_item(self.media_key)
            if not item:
                raise DAVError(HTTP_NOT_FOUND, f"Media item not found: {self.media_key}")
            
            # Get download URL
            download_url = f"{item['baseUrl']}=dv"
            
            # Stream the content
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            
            # Return as BytesIO for DAV
            content = BytesIO()
            for chunk in response.iter_content(chunk_size=8192):
                content.write(chunk)
            content.seek(0)
            return content
            
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
        self.cache_path = None
        self._init_client()
        
    def _init_client(self):
        """Initialize GPMC client"""
        try:
            self.client = Client(auth_data=GP_AUTH_DATA)
            self.cache_path = self.client.db_path
            logger.info(f"Initialized with cache: {self.cache_path}")
        except Exception as e:
            logger.error(f"Failed to init GPMC client: {e}")
            raise
    
    def get_merged_albums(self) -> Dict[str, List[str]]:
        """Get list of albums, merging names ending with '$'"""
        if not self.cache_path or not self.cache_path.exists():
            return {}
            
        merged_albums = {}
        try:
            with sqlite3.connect(self.cache_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT file_name FROM remote_media")
                files = [r[0] for r in cursor.fetchall()]
                
                for file_path in files:
                    parts = file_path.split('/')
                    if len(parts) > 1:
                        album_raw = parts[0]
                        album_clean = album_raw.rstrip('$')
                        
                        if album_clean not in merged_albums:
                            merged_albums[album_clean] = set()
                        merged_albums[album_clean].add(album_raw)
                        
        except Exception as e:
            logger.error(f"Error reading cache: {e}")
            
        return {k: list(v) for k, v in merged_albums.items()}
    
    def get_files_in_album(self, merged_album_name: str) -> List[Dict]:
        """Get all files in a merged album"""
        if not self.cache_path or not self.cache_path.exists():
            return []
            
        files = []
        try:
            with sqlite3.connect(self.cache_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT file_name, media_key, size_bytes FROM remote_media")
                all_media = cursor.fetchall()
                
                for file_path, media_key, size in all_media:
                    parts = file_path.split('/')
                    if len(parts) > 1:
                        album_raw = parts[0]
                        album_clean = album_raw.rstrip('$')
                        
                        if album_clean == merged_album_name:
                            files.append({
                                'name': parts[-1],
                                'path': file_path,
                                'media_key': media_key,
                                'size': size or 0
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


class GPhotosRootCollection(DAVCollection):
    """Root collection showing all merged albums"""
    
    def __init__(self, path, environ, provider):
        super().__init__(path, environ)
        self.provider = provider
        
    def get_member_names(self):
        """List all merged albums"""
        albums = self.provider.get_merged_albums()
        return list(albums.keys())
    
    def get_member(self, name):
        """Get a specific album"""
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
    
    server = wsgi.Server(
        bind_addr=(WEBDAV_HOST, WEBDAV_PORT),
        wsgi_app=app,
    )
    
    logger.info(f"Starting WebDAV server on {WEBDAV_HOST}:{WEBDAV_PORT}")
    logger.info("Username: user, Password: 12345")
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.stop()


if __name__ == '__main__':
    main()
