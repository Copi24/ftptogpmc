#!/usr/bin/env python3
"""
Native Python FTP directory lister.
Provides reliable directory and file listing using ftplib instead of rclone.
"""

import ftplib
import socket
import logging
from typing import List, Dict, Optional
from datetime import datetime
import re

logger = logging.getLogger(__name__)


class FTPLister:
    def __init__(self, host: str, user: str, password: str, port: int = 21, use_tls: bool = True):
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.use_tls = use_tls
        self.ftp = None
        
    def connect(self):
        """Connect to FTP server with TLS."""
        try:
            if self.use_tls:
                self.ftp = ftplib.FTP_TLS()
            else:
                self.ftp = ftplib.FTP()
            
            # Set long timeout for slow connections
            self.ftp.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.ftp.sock.settimeout(300)  # 5 minute timeout
            
            logger.info(f"Connecting to {self.host}:{self.port}...")
            self.ftp.connect(self.host, self.port)
            self.ftp.login(self.user, self.password)
            
            if self.use_tls:
                self.ftp.prot_p()  # Enable encryption for data channel
            
            logger.info(f"✅ Connected to {self.host}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    def disconnect(self):
        """Safely disconnect from FTP server."""
        if self.ftp:
            try:
                self.ftp.quit()
            except:
                try:
                    self.ftp.close()
                except:
                    pass
            self.ftp = None
    
    def list_directories(self, path: str = "") -> List[str]:
        """
        List directories in a path.
        Returns list of directory names.
        """
        try:
            # Use MLSD if available (more reliable and structured)
            dirs = []
            try:
                # Try to change to the directory first
                original_cwd = self.ftp.pwd()
                if path:
                    self.ftp.cwd(path)
                
                # List entries using MLSD (machine-readable listing)
                try:
                    for name, facts in self.ftp.mlsd():
                        if name in ['.', '..']:
                            continue
                        if facts.get('type') == 'dir':
                            dirs.append(name)
                            logger.debug(f"  Found directory: {name}")
                except ftplib.error_perm:
                    # MLSD not supported, fall back to NLST
                    logger.debug("MLSD not supported, using LIST fallback")
                    
                    # Get all entries
                    entries = []
                    self.ftp.retrlines('LIST', entries.append)
                    
                    for entry in entries:
                        # Parse Unix-style directory listing
                        # Format: drwxr-xr-x 2 user group size date time name
                        if entry.startswith('d'):  # Directory
                            parts = entry.split(None, 8)
                            if len(parts) >= 9:
                                name = parts[8]
                                if name not in ['.', '..']:
                                    dirs.append(name)
                                    logger.debug(f"  Found directory: {name}")
                
                # Return to original directory
                if path:
                    self.ftp.cwd(original_cwd)
                
                return dirs
                
            except ftplib.error_perm as e:
                logger.warning(f"Permission error listing {path}: {e}")
                return []
                
        except Exception as e:
            logger.warning(f"Error listing directories in {path}: {e}")
            return []
    
    def list_files(self, path: str = "") -> List[Dict]:
        """
        List files in a directory (non-recursive).
        Returns list of dicts with file info.
        """
        try:
            # Try to change to the directory first
            original_cwd = self.ftp.pwd()
            if path:
                try:
                    self.ftp.cwd(path)
                except ftplib.error_perm as e:
                    logger.warning(f"Cannot access directory {path}: {e}")
                    return []
            
            files = []
            
            # Try MLSD first (more reliable and structured)
            try:
                for name, facts in self.ftp.mlsd():
                    if name in ['.', '..']:
                        continue
                    if facts.get('type') in ['file', None]:  # None means regular file
                        try:
                            # Get size from facts or use SIZE command
                            size = int(facts.get('size', 0))
                            if size == 0:
                                # Try SIZE command
                                try:
                                    self.ftp.voidcmd('TYPE I')
                                    size = self.ftp.size(name)
                                except:
                                    pass
                            
                            full_path = f"{path}/{name}" if path else name
                            
                            files.append({
                                'name': name,
                                'path': full_path,
                                'size': size,
                                'size_gb': round(size / (1024**3), 2),
                                'size_mb': round(size / (1024**2), 2)
                            })
                        except Exception as e:
                            logger.debug(f"Error processing file {name}: {e}")
                            
            except ftplib.error_perm:
                # MLSD not supported, fall back to LIST
                logger.debug("MLSD not supported for files, using LIST fallback")
                
                entries = []
                self.ftp.retrlines('LIST', entries.append)
                
                for entry in entries:
                    # Parse Unix-style file listing
                    # Format: -rw-r--r-- 1 user group size date time name
                    if entry.startswith('-'):  # Regular file
                        parts = entry.split(None, 8)
                        if len(parts) >= 9:
                            name = parts[8]
                            try:
                                size = int(parts[4])
                            except:
                                # Try SIZE command
                                try:
                                    self.ftp.voidcmd('TYPE I')
                                    size = self.ftp.size(name)
                                except:
                                    size = 0
                            
                            full_path = f"{path}/{name}" if path else name
                            
                            files.append({
                                'name': name,
                                'path': full_path,
                                'size': size,
                                'size_gb': round(size / (1024**3), 2),
                                'size_mb': round(size / (1024**2), 2)
                            })
            
            # Return to original directory
            if path:
                self.ftp.cwd(original_cwd)
            
            return files
            
        except Exception as e:
            logger.warning(f"Error listing files in {path}: {e}")
            return []


def list_directories_with_retry(host: str, user: str, password: str, port: int,
                                 path: str = "", max_attempts: int = 3) -> List[str]:
    """List directories with automatic retry and reconnection."""
    
    for attempt in range(1, max_attempts + 1):
        lister = FTPLister(host, user, password, port, use_tls=True)
        
        try:
            if not lister.connect():
                logger.error("Failed to connect to FTP server")
                lister.disconnect()
                if attempt < max_attempts:
                    import time
                    wait_time = 10 * attempt
                    logger.info(f"⏳ Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                continue
            
            dirs = lister.list_directories(path)
            lister.disconnect()
            return dirs
            
        except Exception as e:
            logger.error(f"Attempt {attempt} failed: {e}")
            lister.disconnect()
            if attempt < max_attempts:
                import time
                wait_time = 10 * attempt
                logger.info(f"⏳ Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
    
    logger.error(f"❌ All {max_attempts} attempts failed to list directories in {path}")
    return []


def list_files_with_retry(host: str, user: str, password: str, port: int,
                          path: str = "", max_attempts: int = 3) -> List[Dict]:
    """List files with automatic retry and reconnection."""
    
    for attempt in range(1, max_attempts + 1):
        lister = FTPLister(host, user, password, port, use_tls=True)
        
        try:
            if not lister.connect():
                logger.error("Failed to connect to FTP server")
                lister.disconnect()
                if attempt < max_attempts:
                    import time
                    wait_time = 10 * attempt
                    logger.info(f"⏳ Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                continue
            
            files = lister.list_files(path)
            lister.disconnect()
            return files
            
        except Exception as e:
            logger.error(f"Attempt {attempt} failed: {e}")
            lister.disconnect()
            if attempt < max_attempts:
                import time
                wait_time = 10 * attempt
                logger.info(f"⏳ Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
    
    logger.error(f"❌ All {max_attempts} attempts failed to list files in {path}")
    return []
