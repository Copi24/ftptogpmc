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

    def get_content(self):
        if os.path.exists(self.file_path):
            return open(self.file_path, 'rb')
        return BytesIO(b"Log file not found")

    def get_etag(self):
        return None

    def support_etag(self):
        return False

    def support_ranges(self):
        return False

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
                w(f"Cache Path: {self.provider.cache_path}")
                if self.provider.cache_path and self.provider.cache_path.exists():
                    w("Cache file exists")
                    w(f"Size: {self.provider.cache_path.stat().st_size}")
                    
                    with sqlite3.connect(self.provider.cache_path) as conn:
                        cursor = conn.cursor()
                        
                        # Tables
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        tables = [r[0] for r in cursor.fetchall()]
                        w(f"Tables: {tables}")
                        
                        # Remote Media
                        if 'remote_media' in tables:
                            cursor.execute("PRAGMA table_info(remote_media)")
                            columns = [r[1] for r in cursor.fetchall()]
                            w(f"remote_media columns: {columns}")
                            
                            cursor.execute("SELECT COUNT(*) FROM remote_media")
                            count = cursor.fetchone()[0]
                            w(f"remote_media row count: {count}")
                            
                            w("\n--- First 10 rows from remote_media ---")
                            cursor.execute("SELECT * FROM remote_media LIMIT 10")
                            for row in cursor.fetchall():
                                w(str(row))
                        else:
                            w("ERROR: remote_media table missing!")

                        # Albums
                        if 'albums' in tables:
                             w("\n--- First 5 rows from albums ---")
                             cursor.execute("SELECT * FROM albums LIMIT 5")
                             for row in cursor.fetchall():
                                w(str(row))
                        
                        # Check Merged Albums Logic
                        w("\n--- Merged Albums Logic Check ---")
                        if 'remote_media' in tables:
                            cursor.execute("SELECT file_name FROM remote_media LIMIT 20")
                            files = [r[0] for r in cursor.fetchall()]
                            w(f"Sample file_names: {files}")
                            
                            merged_albums = {}
                            for file_path in files:
                                parts = file_path.split('/')
                                if len(parts) > 1:
                                    album_raw = parts[0]
                                    w(f"  Found album in path: {album_raw}")
                                else:
                                    w(f"  No album in path: {file_path}")

                else:
                    w("Cache file MISSING")
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
