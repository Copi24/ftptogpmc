#!/usr/bin/env python3
"""
Stream large 3D movie files from FTP server to Google Photos.
Uses rclone to access FTP and gpmc to upload to Google Photos.
Designed to work within GitHub Actions storage constraints.
"""

import os
import sys
import subprocess
import json
import logging
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Optional
import time

try:
    from gpmc import Client
except ImportError:
    print("ERROR: gpmc library not installed. Install with: pip install https://github.com/xob0t/google_photos_mobile_client/archive/refs/heads/main.zip")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('ftp_to_gphotos.log')
    ]
)
logger = logging.getLogger(__name__)

# Configuration
RCLONE_REMOTE = "3DFF"
MIN_FILE_SIZE = 20 * 1024 * 1024 * 1024  # 20GB minimum
MAX_FILE_SIZE = 50 * 1024 * 1024 * 1024  # 50GB maximum (sanity check)
SUPPORTED_EXTENSIONS = ['.mkv', '.iso', '.mp4', '.m4v']
CHUNK_SIZE = 64 * 1024 * 1024  # 64MB chunks for streaming
MAX_RETRIES = 3
RETRY_DELAY = 60  # seconds


def check_rclone_installed() -> bool:
    """Check if rclone is installed and accessible."""
    try:
        result = subprocess.run(['rclone', 'version'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info(f"rclone version: {result.stdout.strip().split()[0]}")
            return True
        return False
    except FileNotFoundError:
        logger.error("rclone not found in PATH")
        return False
    except Exception as e:
        logger.error(f"Error checking rclone: {e}")
        return False


def list_large_movie_files(remote: str, min_size: int, max_size: int, extensions: List[str]) -> List[Dict]:
    """
    List large movie files from FTP using rclone.
    Returns list of dicts with file info.
    """
    logger.info(f"Scanning {remote} for large movie files...")
    logger.info(f"Looking for files between {min_size / (1024**3):.1f}GB and {max_size / (1024**3):.1f}GB")
    logger.info(f"Extensions: {', '.join(extensions)}")
    
    files = []
    
    try:
        # Use rclone ls to get file details recursively
        # rclone ls is already recursive and only lists files
        cmd = [
            'rclone', 'ls', f'{remote}:'
        ]
        
        logger.info(f"Running command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        
        if result.returncode != 0:
            logger.error(f"rclone ls failed: {result.stderr}")
            return files
        
        # Parse output - format is: size path
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                try:
                    file_size = int(parts[0])
                    file_path = parts[1]
                    
                    if not any(file_path.lower().endswith(ext.lower()) for ext in extensions):
                        continue
                    
                    if min_size <= file_size <= max_size:
                        files.append({
                            'path': file_path,
                            'size': file_size,
                            'size_gb': file_size / (1024**3)
                        })
                        logger.info(f"Found candidate: {file_path} ({file_size / (1024**3):.2f}GB)")
                except ValueError:
                    continue
        
        logger.info(f"Found {len(files)} large movie files matching criteria")
        return files
        
    except subprocess.TimeoutExpired:
        logger.error("rclone command timed out")
        return files
    except Exception as e:
        logger.error(f"Error listing files: {e}", exc_info=True)
        return files


def stream_file_from_ftp(remote: str, remote_path: str, local_path: Path, chunk_size: int = CHUNK_SIZE) -> bool:
    """
    Stream a file from FTP to local path using rclone copy.
    Returns True if successful, False otherwise.
    """
    logger.info(f"Streaming {remote_path} to {local_path}...")
    
    # Ensure parent directory exists
    local_path.parent.mkdir(parents=True, exist_ok=True)
    
    process = None
    try:
        # Use rclone copy with progress reporting and optimized settings
        # Copy to parent directory - rclone will preserve filename
        remote_dir = os.path.dirname(remote_path).strip('/')
        remote_filename = os.path.basename(remote_path)
        
        cmd = [
            'rclone', 'copy',
            f'{remote}:{remote_path}',
            str(local_path.parent),
            '--progress',
            '--no-check-dest',
            '--buffer-size', '32M',
            '--transfers', '1',
            '--checkers', '1',
            '--low-level-retries', '3',
            '--retries', '3',
            '--stats', '30s',
            '--log-level', 'INFO',
            '--timeout', '300s',
            '--contimeout', '60s'
        ]
        
        logger.info(f"Starting download: {' '.join(cmd)}")
        start_time = time.time()
        last_progress_time = start_time
        
        # Run with real-time output
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Stream output with periodic progress updates
        for line in process.stdout:
            line = line.strip()
            if line:
                # Log progress every 30 seconds
                current_time = time.time()
                if current_time - last_progress_time >= 30:
                    logger.info(f"rclone progress: {line}")
                    last_progress_time = current_time
                elif 'ETA' in line or 'Transferred' in line or 'Errors' in line:
                    logger.info(f"rclone: {line}")
        
        process.wait()
        
        elapsed = time.time() - start_time
        
        if process.returncode == 0:
            if local_path.exists():
                file_size = local_path.stat().st_size
                speed = file_size / elapsed / (1024**2) if elapsed > 0 else 0
                logger.info(f"Download completed: {file_size / (1024**3):.2f}GB in {elapsed:.1f}s ({speed:.2f}MB/s)")
                return True
            else:
                logger.error(f"Download completed but file not found at {local_path}")
                # Check if file exists with different name (case sensitivity)
                parent_files = list(local_path.parent.glob('*'))
                if parent_files:
                    logger.info(f"Files in directory: {[f.name for f in parent_files]}")
                return False
        else:
            logger.error(f"Download failed with return code {process.returncode}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("Download timed out")
        if process:
            process.kill()
        return False
    except Exception as e:
        logger.error(f"Error streaming file: {e}", exc_info=True)
        if process:
            try:
                process.kill()
            except:
                pass
        return False


def upload_to_google_photos(file_path: Path, auth_data: str, retries: int = MAX_RETRIES) -> Optional[str]:
    """
    Upload a file to Google Photos using gpmc.
    Returns the media key if successful, None otherwise.
    """
    logger.info(f"Uploading {file_path.name} ({file_path.stat().st_size / (1024**3):.2f}GB) to Google Photos...")
    
    for attempt in range(1, retries + 1):
        try:
            client = Client(auth_data=auth_data)
            
            logger.info(f"Upload attempt {attempt}/{retries}")
            start_time = time.time()
            
            result = client.upload(
                target=str(file_path),
                show_progress=True,
                threads=1,
                force_upload=False,
                use_quota=False,
                saver=False
            )
            
            elapsed = time.time() - start_time
            
            if result and str(file_path) in result:
                media_key = result[str(file_path)]
                speed = file_path.stat().st_size / elapsed / (1024**2) if elapsed > 0 else 0
                logger.info(f"Upload successful! Media key: {media_key}")
                logger.info(f"Upload completed in {elapsed:.1f}s ({speed:.2f}MB/s)")
                return media_key
            else:
                logger.warning(f"Upload returned unexpected result: {result}")
                if attempt < retries:
                    logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                    continue
                
        except Exception as e:
            logger.error(f"Upload attempt {attempt} failed: {e}")
            if attempt < retries:
                logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
            else:
                logger.error(f"All upload attempts failed for {file_path.name}")
    
    return None


def process_file(remote: str, file_info: Dict, auth_data: str, temp_dir: Path) -> bool:
    """
    Process a single file: download from FTP and upload to Google Photos.
    Returns True if successful, False otherwise.
    """
    remote_path = file_info['path']
    file_name = os.path.basename(remote_path)
    # Sanitize filename for filesystem
    safe_name = "".join(c for c in file_name if c.isalnum() or c in "._- ")
    local_path = temp_dir / safe_name
    
    logger.info("=" * 80)
    logger.info(f"Processing: {remote_path}")
    logger.info(f"Size: {file_info['size_gb']:.2f}GB")
    logger.info(f"Local path: {local_path}")
    logger.info("=" * 80)
    
    # Stream file from FTP
    download_success = stream_file_from_ftp(remote, remote_path, local_path)
    
    if not download_success:
        logger.error(f"Failed to download {remote_path}")
        return False
    
    # Verify file exists - rclone preserves original filename
    # Check for exact match first, then search for similar names
    if not local_path.exists():
        # Try original filename (rclone preserves it)
        original_local_path = temp_dir / file_name
        if original_local_path.exists():
            local_path = original_local_path
            logger.info(f"Found file with original name: {local_path.name}")
        else:
            # Try to find the file by searching for files with similar name
            parent_files = list(local_path.parent.glob('*'))
            matching_files = [f for f in parent_files if file_name.lower() in f.name.lower()]
            
            if matching_files:
                local_path = matching_files[0]
                logger.info(f"Found file with similar name: {local_path.name}")
            else:
                logger.error(f"File not found after download: {local_path}")
                logger.info(f"Expected filename: {file_name}")
                logger.info(f"Available files: {[f.name for f in parent_files]}")
                return False
    
    # Verify file size
    actual_size = local_path.stat().st_size
    expected_size = file_info['size']
    size_diff = abs(actual_size - expected_size)
    
    if size_diff > 10 * 1024 * 1024:  # Allow 10MB difference for FTP inconsistencies
        logger.warning(f"Size mismatch: expected {expected_size / (1024**3):.2f}GB, "
                      f"got {actual_size / (1024**3):.2f}GB (diff: {size_diff / (1024**2):.2f}MB)")
    else:
        logger.info(f"File size verified: {actual_size / (1024**3):.2f}GB")
    
    # Upload to Google Photos
    media_key = upload_to_google_photos(local_path, auth_data)
    
    if media_key:
        logger.info(f"Successfully processed {file_name} -> {media_key}")
        # Clean up local file to free space immediately
        try:
            local_path.unlink()
            logger.info(f"Deleted local file: {local_path}")
        except Exception as e:
            logger.warning(f"Failed to delete local file: {e}")
        return True
    else:
        logger.error(f"Failed to upload {file_name}")
        # Keep file for manual retry if needed (but warn about disk space)
        logger.warning(f"Local file kept at {local_path} for manual retry")
        return False


def main():
    """Main function."""
    logger.info("=" * 80)
    logger.info("FTP to Google Photos Transfer Script")
    logger.info("=" * 80)
    
    # Get auth data from environment or fail
    auth_data = os.environ.get('GP_AUTH_DATA')
    if not auth_data:
        logger.error("GP_AUTH_DATA environment variable not set!")
        sys.exit(1)
    
    logger.info("Auth data found (length: {} chars)".format(len(auth_data)))
    
    # Check rclone
    if not check_rclone_installed():
        logger.error("rclone is required but not found!")
        sys.exit(1)
    
    # Check rclone config
    rclone_config = os.environ.get('RCLONE_CONFIG', os.path.expanduser('~/.config/rclone/rclone.conf'))
    if not os.path.exists(rclone_config):
        logger.warning(f"rclone config not found at {rclone_config}")
        logger.info("Will use rclone config from current directory or default location")
    
    # Create temporary directory
    temp_dir = Path(tempfile.mkdtemp(prefix='ftp_gphotos_'))
    logger.info(f"Using temporary directory: {temp_dir}")
    
    try:
        # List large movie files
        files = list_large_movie_files(RCLONE_REMOTE, MIN_FILE_SIZE, MAX_FILE_SIZE, SUPPORTED_EXTENSIONS)
        
        if not files:
            logger.warning("No large movie files found matching criteria")
            return
        
        logger.info(f"Found {len(files)} files to process")
        
        # Process each file
        successful = 0
        failed = 0
        
        for i, file_info in enumerate(files, 1):
            logger.info(f"\nProcessing file {i}/{len(files)}")
            
            # Check available disk space (need at least file size + 5GB buffer)
            if sys.platform != 'win32':
                stat = shutil.disk_usage(temp_dir)
                free_space = stat.free
                required_space = file_info['size'] + 5 * 1024 * 1024 * 1024
                
                if free_space < required_space:
                    logger.error(f"Insufficient disk space: {free_space / (1024**3):.1f}GB free, "
                               f"{required_space / (1024**3):.1f}GB required")
                    failed += 1
                    continue
            
            if process_file(RCLONE_REMOTE, file_info, auth_data, temp_dir):
                successful += 1
            else:
                failed += 1
            
            # Small delay between files
            time.sleep(5)
        
        logger.info("=" * 80)
        logger.info(f"Processing complete: {successful} successful, {failed} failed")
        logger.info("=" * 80)
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        # Cleanup
        try:
            logger.info(f"Cleaning up temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Failed to cleanup temp directory: {e}")


if __name__ == '__main__':
    main()

