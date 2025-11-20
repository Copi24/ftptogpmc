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
        """Initialize GPMC client"""
        try:
            self.client = Client(auth_data=GP_AUTH_DATA)
            logger.info(f"Initialized client")
            logger.info(f"Cache path: {self.client.db_path}")
            
            # Try to fetch albums (if method exists, otherwise skip)
            self.albums_map = {}
            # We know get_albums doesn't exist, so we skip it for now
            # and rely on "All Photos" fallback
                
        except Exception as e:
            logger.error(f"Failed to init GPMC client: {e}")
            raise
    
    def get_merged_albums(self) -> Dict[str, List[str]]:
        """Get list of merged albums"""
        # Return empty for now, plus "All Photos" which is handled in get_resource_inst
        return {}
    
    def get_all_media(self, limit=100):
        """Get all media from local cache"""
        files = []
        try:
            if not os.path.exists(self.client.db_path):
                return []
                
            with sqlite3.connect(self.client.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT file_name, media_key, size_bytes FROM remote_media LIMIT ?", (limit,))
                for row in cursor.fetchall():
                    files.append({
                        'name': row[0],
                        'media_key': row[1],
                        'size': row[2] if row[2] else 0
                    })
        except Exception as e:
            logger.error(f"Error getting all media: {e}")
        return files
    
    def get_resource_inst(self, path, environ):
        """Get resource instance for a given path"""
        logger.debug(f"Getting resource for path: {path}")
        
        # Normalize path
        path = path.rstrip('/')
        if not path or path == '/':
            return GPhotosRootCollection('/', environ, self)

        # Check for special files
        name = path.strip('/')
        if name in ['debug.txt', 'webdav.log', 'cache_update.log', 'tunnel.log']:
            if name == 'debug.txt':
                return DebugResource(path, environ, self)
            else:
                return LogResource(path, environ, name)
        
        # Check for "All Photos" folder
        if name == 'All Photos':
            return AllPhotosCollection(path, environ, self)
            
        # Handle files inside All Photos
        parts = path.strip('/').split('/')
        if len(parts) == 2 and parts[0] == 'All Photos':
            file_name = parts[1]
            # We need to find this file. For now, we just list all and find it.
            # Inefficient but works for small limit.
            files = self.get_all_media(limit=1000) # Increase limit for lookup
            for f in files:
                if f['name'] == file_name:
                    return GPhotosResource(path, environ, f, self.client)
            return None
        
        return None

class AllPhotosCollection(DAVCollection):
    """Represents the All Photos folder"""
    def __init__(self, path, environ, provider):
        super().__init__(path, environ)
        self.provider = provider
        
    def get_member_names(self):
        files = self.provider.get_all_media()
        return [f['name'] for f in files]
        
    def get_member(self, name):
        files = self.provider.get_all_media()
        for f in files:
            if f['name'] == name:
                file_path = f"{self.path}/{name}"
                return GPhotosResource(file_path, self.environ, f, self.provider.client)
        return None

class GPhotosRootCollection(DAVCollection):
    """Root collection"""
    def __init__(self, path, environ, provider):
        super().__init__(path, environ)
        self.provider = provider

    def get_member_names(self):
        # Always show All Photos and logs
        return ['All Photos', 'debug.txt', 'webdav.log', 'cache_update.log', 'tunnel.log']

    def get_member(self, name):
        return self.provider.get_resource_inst(f"/{name}", self.environ)

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
        out.write(b"=== WebDAV Server Debug Info ===\n")
        out.write(f"CWD: {os.getcwd()}\n".encode())
        out.write(f"Directory listing: {os.listdir('.')}\n".encode())
        
        # Dump library state to see if we can find albums
        try:
            out.write(b"\n--- Library State Dump ---\n")
            state = self.provider.client.api.get_library_state()
            import json
            # Convert to string safely (handle bytes)
            out.write(str(state).encode())
        except Exception as e:
            out.write(f"\nError dumping library state: {e}\n".encode())
            
        return out

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
