#!/usr/bin/env python3
"""
Native Python FTP downloader with resume support.
Much more reliable than rclone for large file transfers.
"""

import ftplib
import os
import socket
from pathlib import Path
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

class FTPDownloader:
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
            self.ftp.sock.settimeout(600)  # 10 minute timeout
            
            logger.info(f"Connecting to {self.host}:{self.port}...")
            self.ftp.connect(self.host, self.port)
            self.ftp.login(self.user, self.password)
            
            if self.use_tls:
                self.ftp.prot_p()  # Enable encryption for data channel
            
            # Set binary mode
            self.ftp.voidcmd('TYPE I')
            
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
    
    def get_file_size(self, remote_path: str) -> Optional[int]:
        """Get remote file size."""
        try:
            self.ftp.voidcmd('TYPE I')
            size = self.ftp.size(remote_path)
            return size
        except Exception as e:
            logger.warning(f"Could not get file size: {e}")
            return None
    
    def download_file(self, remote_path: str, local_path: Path, chunk_size: int = 8192) -> bool:
        """
        Download file with resume support.
        Uses FTP REST command to resume from partial downloads.
        """
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Get remote file size
        remote_size = self.get_file_size(remote_path)
        if remote_size is None:
            logger.error("Could not determine remote file size")
            return False
        
        logger.info(f"Remote file size: {remote_size / (1024**3):.2f}GB")
        
        # Check for partial download
        resume_pos = 0
        if local_path.exists():
            resume_pos = local_path.stat().st_size
            if resume_pos == remote_size:
                logger.info("✅ File already complete!")
                return True
            elif resume_pos > 0:
                logger.info(f"🔄 Resuming from {resume_pos / (1024**3):.2f}GB")
        
        # Open file in append mode if resuming, write mode otherwise
        mode = 'ab' if resume_pos > 0 else 'wb'
        
        try:
            with open(local_path, mode) as f:
                # Send REST command to resume from position
                if resume_pos > 0:
                    self.ftp.voidcmd(f'REST {resume_pos}')
                
                # Start transfer
                start_time = time.time()
                last_update = start_time
                bytes_downloaded = resume_pos
                
                # Optimized callback - minimize overhead
                callback_count = 0
                def callback(data):
                    nonlocal bytes_downloaded, last_update, callback_count
                    f.write(data)
                    bytes_downloaded += len(data)
                    callback_count += 1
                    
                    # Progress update every 5 seconds (skip work most of the time)
                    now = time.time()
                    if now - last_update >= 5:
                        elapsed = now - start_time
                        speed_mbps = ((bytes_downloaded - resume_pos) / elapsed) / (1024**2)
                        progress_pct = (bytes_downloaded / remote_size) * 100
                        eta_seconds = (remote_size - bytes_downloaded) / (speed_mbps * 1024**2) if speed_mbps > 0 else 0
                        
                        logger.info(f"📥 {progress_pct:.1f}% - {bytes_downloaded / (1024**3):.2f}/{remote_size / (1024**3):.2f}GB - {speed_mbps:.1f}MB/s - ETA {eta_seconds/60:.0f}m")
                        last_update = now
                
                # Retrieve file
                self.ftp.retrbinary(f'RETR {remote_path}', callback, blocksize=chunk_size)
                
            # Verify download
            final_size = local_path.stat().st_size
            if final_size == remote_size:
                elapsed = time.time() - start_time
                avg_speed = (final_size - resume_pos) / elapsed / (1024**2)
                logger.info(f"✅ Download complete! Average speed: {avg_speed:.1f}MB/s")
                return True
            else:
                logger.error(f"Size mismatch: got {final_size}, expected {remote_size}")
                return False
                
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return False


def download_with_retry(host: str, user: str, password: str, port: int,
                       remote_path: str, local_path: Path, 
                       max_attempts: int = 5) -> bool:
    """Download file with automatic retry and reconnection."""
    
    for attempt in range(1, max_attempts + 1):
        logger.info(f"📥 Download attempt {attempt}/{max_attempts}")
        
        downloader = FTPDownloader(host, user, password, port, use_tls=True)
        
        try:
            if not downloader.connect():
                logger.error("Failed to connect to FTP server")
                downloader.disconnect()
                if attempt < max_attempts:
                    wait_time = 30 * attempt
                    logger.info(f"⏳ Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                continue
            
            # Use larger chunks for better performance (8MB for streaming)
            success = downloader.download_file(remote_path, local_path, chunk_size=8*1024*1024)  # 8MB chunks
            downloader.disconnect()
            
            if success:
                return True
            
            if attempt < max_attempts:
                wait_time = 30 * attempt
                logger.info(f"⏳ Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                
        except Exception as e:
            logger.error(f"Attempt {attempt} failed: {e}")
            downloader.disconnect()
            if attempt < max_attempts:
                wait_time = 30 * attempt
                logger.info(f"⏳ Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
    
    logger.error(f"❌ All {max_attempts} attempts failed")
    return False

