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
                # 350 is intermediate response (pending) - means "accepted, send RETR now"
                if resume_pos > 0:
                    try:
                        # Send REST command manually to handle 350 intermediate response
                        self.ftp.putcmd(f'REST {resume_pos}')
                        # Read response - 350 is expected (intermediate)
                        response = self.ftp.getresp()
                        logger.info(f"REST response: {response}")
                        # 350 means "pending" - server accepted, waiting for RETR
                        # This is correct, proceed with RETR
                        if response[0] == '3':  # 3xx is intermediate (350)
                            logger.info("✓ REST accepted (350) - server ready for RETR")
                        elif response[0] == '2':  # 2xx is final success
                            logger.info("✓ REST accepted")
                        else:
                            logger.error(f"Unexpected REST response: {response}")
                            return False
                    except Exception as e:
                        logger.error(f"REST command failed: {e}")
                        return False
                
                # Start transfer
                start_time = time.time()
                last_update = start_time
                bytes_downloaded = resume_pos
                
                def callback(data):
                    nonlocal bytes_downloaded, last_update
                    f.write(data)
                    bytes_downloaded += len(data)
                    
                    # Progress update every 5 seconds
                    now = time.time()
                    if now - last_update >= 5:
                        elapsed = now - start_time
                        speed_mbps = ((bytes_downloaded - resume_pos) / elapsed) / (1024**2)
                        progress_pct = (bytes_downloaded / remote_size) * 100
                        eta_seconds = (remote_size - bytes_downloaded) / (speed_mbps * 1024**2) if speed_mbps > 0 else 0
                        
                        logger.info(f"📥 {progress_pct:.1f}% - {bytes_downloaded / (1024**3):.2f}/{remote_size / (1024**3):.2f}GB - {speed_mbps:.1f}MB/s - ETA {eta_seconds/60:.0f}m")
                        last_update = now
                
                # Retrieve file with longer timeout for large files
                # Increase socket timeout during transfer
                self.ftp.sock.settimeout(1800)  # 30 minutes for large file transfers
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
            import traceback
            logger.error(f"Download failed: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            logger.debug(f"Traceback: {traceback.format_exc()}")
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
            
            success = downloader.download_file(remote_path, local_path, chunk_size=1024*1024)  # 1MB chunks
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

