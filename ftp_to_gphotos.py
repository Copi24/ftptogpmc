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
# Try individual servers if combined fails
RCLONE_REMOTES = ["3DFlickFix", "3DFlickFix2", "3DFlickFix3"]
MIN_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1GB minimum (to avoid tiny files)
MAX_FILE_SIZE = 100 * 1024 * 1024 * 1024  # 100GB maximum (sanity check)
SUPPORTED_EXTENSIONS = ['.mkv', '.iso', '.mp4', '.m4v', '.avi', '.m2ts']
CHUNK_SIZE = 64 * 1024 * 1024  # 64MB chunks for streaming
MAX_RETRIES = 3
RETRY_DELAY = 60  # seconds
RCLONE_TIMEOUT = 600  # 10 minutes timeout for operations


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


def list_directories(remote: str, path: str = "") -> List[str]:
    """
    List directories in a path using rclone lsd.
    Returns list of directory names.
    """
    try:
        cmd = [
            'rclone', 'lsd', f'{remote}:{path}',
            '--timeout', '120s',
            '--contimeout', '60s',
            '--low-level-retries', '5',
            '--retries', '3'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        
        if result.returncode != 0:
            logger.warning(f"Failed to list directories in {path}: {result.stderr[:200]}")
            return []
        
        dirs = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            # lsd output format: size date time size name
            # Example: "          -1 2025-10-31 10:33:09        -1 Challenger"
            parts = line.split()
            if len(parts) >= 5:
                # Skip first 4 columns: size, date, time, size
                dir_name = ' '.join(parts[4:])  # Handle names with spaces
                dirs.append(dir_name)
                logger.debug(f"Found directory: {dir_name}")
        
        return dirs
        
    except Exception as e:
        logger.warning(f"Error listing directories in {path}: {e}")
        return []


def list_files_in_directory(remote: str, path: str, min_size: int, max_size: int, extensions: List[str]) -> List[Dict]:
    """
    List files in a specific directory (non-recursive) using rclone ls.
    Returns list of dicts with file info.
    """
    try:
        # Use --max-depth 1 to only list files in this directory
        cmd = [
            'rclone', 'ls', f'{remote}:{path}',
            '--max-depth', '1',
            '--timeout', '300s',
            '--contimeout', '60s',
            '--low-level-retries', '5',
            '--retries', '3'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=420)
        
        if result.returncode != 0:
            logger.warning(f"Failed to list files in {path}: {result.stderr[:200]}")
            return []
        
        files = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                try:
                    file_size = int(parts[0])
                    file_name = parts[1]
                    
                    # Check if it's a supported file type
                    if not any(file_name.lower().endswith(ext.lower()) for ext in extensions):
                        continue
                    
                    # Check size
                    if min_size <= file_size <= max_size:
                        full_path = os.path.join(path, file_name) if path else file_name
                        files.append({
                            'path': full_path,
                            'size': file_size,
                            'size_gb': file_size / (1024**3)
                        })
                        logger.info(f"  Found: {file_name} ({file_size / (1024**3):.2f}GB)")
                except ValueError:
                    continue
        
        return files
        
    except Exception as e:
        logger.warning(f"Error listing files in {path}: {e}")
        return []


def traverse_and_process_depth_first(remote: str, auth_data: str, temp_dir: Path, 
                                     min_size: int, max_size: int, extensions: List[str],
                                     path: str = "", depth: int = 0) -> tuple:
    """
    Traverse directories depth-first and process files immediately.
    Returns (successful_count, failed_count).
    """
    indent = "  " * depth
    display_path = path if path else '(root)'
    logger.info(f"{indent}📁 Scanning: {display_path}")
    
    successful = 0
    failed = 0
    
    # Process files in current directory
    files = list_files_in_directory(remote, path, min_size, max_size, extensions)
    if files:
        logger.info(f"{indent}✓ Found {len(files)} file(s) in this directory")
        for file_info in files:
            if process_file(remote, file_info, auth_data, temp_dir):
                successful += 1
            else:
                failed += 1
    
    # Get subdirectories
    subdirs = list_directories(remote, path)
    if subdirs:
        logger.info(f"{indent}↳ Found {len(subdirs)} subdirectory(ies): {', '.join(subdirs)}")
        for subdir in subdirs:
            # Use forward slashes for paths (rclone standard)
            if path:
                subpath = f"{path}/{subdir}"
            else:
                subpath = subdir
            
            # Recursively process subdirectory
            sub_success, sub_failed = traverse_and_process_depth_first(
                remote, auth_data, temp_dir, min_size, max_size, extensions, 
                subpath, depth + 1
            )
            successful += sub_success
            failed += sub_failed
    
    return successful, failed


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
            '--buffer-size', '16M',  # Smaller buffer for unstable connections
            '--transfers', '1',
            '--checkers', '1',
            '--low-level-retries', '10',  # More retries
            '--retries', '10',  # More retries
            '--stats', '30s',
            '--log-level', 'INFO',
            '--timeout', '600s',  # 10 minute timeout
            '--contimeout', '120s',  # 2 minute connection timeout
            '--tpslimit', '10',  # Limit to 10 transactions per second
            '--tpslimit-burst', '0'  # No burst
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
    
    # Try download with retries
    max_download_attempts = 2
    download_success = False
    
    for attempt in range(1, max_download_attempts + 1):
        if attempt > 1:
            logger.info(f"Download attempt {attempt}/{max_download_attempts} for {file_name}")
            time.sleep(30)  # Wait before retry
        
        download_success = stream_file_from_ftp(remote, remote_path, local_path)
        
        if download_success:
            break
        else:
            logger.warning(f"Download attempt {attempt} failed")
            if attempt < max_download_attempts:
                logger.info("Will retry...")
    
    if not download_success:
        logger.error(f"Failed to download {remote_path} after {max_download_attempts} attempts - SKIPPING")
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
    rclone_config = os.path.expanduser('~/.config/rclone/rclone.conf')
    if not os.path.exists(rclone_config):
        logger.warning(f"rclone config not found at {rclone_config}")
        logger.info("Rclone will use default config location or environment")
    else:
        logger.info(f"Using rclone config at {rclone_config}")
    
    # Create temporary directory
    temp_dir = Path(tempfile.mkdtemp(prefix='ftp_gphotos_'))
    logger.info(f"Using temporary directory: {temp_dir}")
    
    try:
        # Traverse and process files depth-first
        logger.info("=" * 80)
        logger.info("Starting depth-first traversal and processing")
        logger.info(f"Min file size: {MIN_FILE_SIZE / (1024**3):.1f}GB")
        logger.info(f"Max file size: {MAX_FILE_SIZE / (1024**3):.1f}GB")
        logger.info(f"Extensions: {', '.join(SUPPORTED_EXTENSIONS)}")
        logger.info("=" * 80)
        
        successful, failed = traverse_and_process_depth_first(
            RCLONE_REMOTE, auth_data, temp_dir,
            MIN_FILE_SIZE, MAX_FILE_SIZE, SUPPORTED_EXTENSIONS
        )
        
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

