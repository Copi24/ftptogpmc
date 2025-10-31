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

try:
    from state_manager import StateManager
except ImportError:
    print("ERROR: state_manager.py not found")
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
# Using direct FTP (no combine remote) for better reliability
RCLONE_REMOTE = "Challenger"  # Direct FTP to Challenger server
# Other direct servers available
RCLONE_REMOTES = ["Challenger", "Tamarind", "Sputnik"]
MIN_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1GB minimum (to avoid tiny files)
MAX_FILE_SIZE = 50 * 1024 * 1024 * 1024  # 50GB maximum (with maximize-build-space we get ~60GB!)
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
    Returns list of dicts with file info, sorted by size (smallest first).
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
        
        # Sort by size (smallest first) - more likely to complete on unstable FTP
        files.sort(key=lambda x: x['size'])
        if files:
            logger.info(f"  📊 Sorted {len(files)} files by size (smallest first)")
        
        return files
        
    except Exception as e:
        logger.warning(f"Error listing files in {path}: {e}")
        return []


def traverse_and_process_depth_first(remote: str, auth_data: str, temp_dir: Path, 
                                     min_size: int, max_size: int, extensions: List[str],
                                     state: StateManager,
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
            if process_file(remote, file_info, auth_data, temp_dir, state):
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
                state, subpath, depth + 1
            )
            successful += sub_success
            failed += sub_failed
    
    return successful, failed


def stream_file_from_ftp(remote: str, remote_path: str, local_path: Path, chunk_size: int = CHUNK_SIZE, attempt: int = 1) -> bool:
    """
    Stream a file from FTP to local path using rclone copy.
    Optimized for streaming servers - uses large buffers and no timeouts.
    Returns True if successful, False otherwise.
    """
    logger.info(f"🎬 Streaming {remote_path} to {local_path}... (attempt {attempt})")
    
    # Ensure parent directory exists
    local_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Keep partial file for resume - FTP supports REST command
    if local_path.exists():
        partial_size = local_path.stat().st_size
        logger.info(f"🔄 Found partial download: {partial_size / (1024**3):.2f}GB - will resume!")
        # rclone will automatically skip if file complete, resume if partial
    
    process = None
    try:
        # Use rclone copy with progress reporting and optimized settings
        # Copy to parent directory - rclone will preserve filename
        remote_dir = os.path.dirname(remote_path).strip('/')
        remote_filename = os.path.basename(remote_path)
        
        # Streaming-optimized settings for fast FTP server
        cmd = [
            'rclone', 'copy',
            f'{remote}:{remote_path}',
            str(local_path.parent),
            '--progress',
            '--buffer-size', '256M',  # LARGE buffer for streaming
            '--transfers', '1',
            '--checkers', '1',
            '--low-level-retries', '10',
            '--retries', '5',
            '--stats', '30s',
            '--log-level', 'INFO',
            '--timeout', '0',  # NO timeout - let it stream!
            '--contimeout', '300s',  # 5 minutes for initial connection
            '--no-traverse',  # Don't list, just copy
            '--no-check-dest',  # Don't verify before copy
            '--streaming-upload-cutoff', '0',  # Stream everything
            '--use-mmap',  # Use memory mapping for efficiency
        ]
        
        logger.info(f"Starting download: {' '.join(cmd)}")
        start_time = time.time()
        last_progress_time = start_time
        last_transferred_gb = 0.0
        stall_start_time = None
        MAX_STALL_TIME = 300  # 5 minutes - allow for streaming variations
        
        # Run with real-time output
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Stream output with stall detection
        for line in process.stdout:
            line = line.strip()
            if line:
                current_time = time.time()
                
                # Extract current transferred amount to detect stalls
                if 'Transferred:' in line and 'GiB' in line:
                    try:
                        # Parse "Transferred:   1.233 GiB / 7.098 GiB"
                        parts = line.split('Transferred:')[1].strip()
                        current_str = parts.split('GiB')[0].strip().split('/')[0].strip()
                        current_gb = float(current_str)
                        
                        # Check if we're making progress
                        if abs(current_gb - last_transferred_gb) < 0.01:  # Less than 10MB progress
                            if stall_start_time is None:
                                stall_start_time = current_time
                                logger.warning(f"⚠️ Download stalled at {current_gb:.3f}GB")
                            else:
                                stall_duration = current_time - stall_start_time
                                if stall_duration > MAX_STALL_TIME:
                                    logger.error(f"💀 Download STUCK at {current_gb:.3f}GB for {stall_duration:.0f}s - KILLING")
                                    process.kill()
                                    time.sleep(1)
                                    break
                                elif stall_duration > 60:
                                    logger.warning(f"⏳ Still stalled at {current_gb:.3f}GB ({stall_duration:.0f}s)")
                        else:
                            # Progress detected
                            if stall_start_time is not None:
                                logger.info(f"✓ Progress resumed from {last_transferred_gb:.3f}GB to {current_gb:.3f}GB")
                            stall_start_time = None
                            last_transferred_gb = current_gb
                    except Exception as e:
                        logger.debug(f"Failed to parse transfer amount: {e}")
                
                # Log progress every 30 seconds
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
    file_size_gb = file_path.stat().st_size / (1024**3)
    logger.info("=" * 80)
    logger.info(f"🚀 STARTING UPLOAD TO GOOGLE PHOTOS")
    logger.info(f"File: {file_path.name}")
    logger.info(f"Size: {file_size_gb:.2f}GB")
    logger.info(f"Settings: UNLIMITED STORAGE (use_quota=False, saver=False)")
    logger.info("=" * 80)
    
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"📤 Upload attempt {attempt}/{retries} starting...")
            
            # Create client with auth data
            client = Client(auth_data=auth_data)
            logger.info("✓ Client initialized successfully")
            
            start_time = time.time()
            logger.info(f"⏰ Upload started at {time.strftime('%H:%M:%S')}")
            
            # Upload with unlimited storage settings
            result = client.upload(
                target=str(file_path),
                show_progress=True,  # This will show progress in console
                threads=1,
                force_upload=False,
                use_quota=False,  # ← UNLIMITED STORAGE
                saver=False  # ← ORIGINAL QUALITY
            )
            
            elapsed = time.time() - start_time
            
            logger.info(f"⏱️ Upload operation completed in {elapsed:.1f}s")
            logger.info(f"📊 Result: {result}")
            
            if result and str(file_path) in result:
                media_key = result[str(file_path)]
                speed = file_path.stat().st_size / elapsed / (1024**2) if elapsed > 0 else 0
                
                logger.info("=" * 80)
                logger.info(f"✅ UPLOAD SUCCESSFUL!")
                logger.info(f"📸 Media key: {media_key}")
                logger.info(f"⚡ Speed: {speed:.2f}MB/s")
                logger.info(f"⏱️ Time: {elapsed:.1f}s")
                logger.info(f"💾 Storage: UNLIMITED (original quality)")
                logger.info(f"🔗 File should now be visible in Google Photos!")
                logger.info("=" * 80)
                return media_key
            else:
                logger.error(f"❌ Upload returned unexpected result: {result}")
                if attempt < retries:
                    logger.info(f"⏳ Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                    continue
                
        except Exception as e:
            logger.error(f"❌ Upload attempt {attempt} failed with exception:")
            logger.error(f"   Error: {str(e)}")
            logger.error(f"   Type: {type(e).__name__}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
            
            if attempt < retries:
                logger.info(f"⏳ Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
            else:
                logger.error(f"💔 All {retries} upload attempts failed for {file_path.name}")
    
    logger.error("=" * 80)
    logger.error(f"❌ UPLOAD FAILED COMPLETELY")
    logger.error("=" * 80)
    return None


def process_file(remote: str, file_info: Dict, auth_data: str, temp_dir: Path, state: StateManager) -> bool:
    """
    Process a single file: download from FTP and upload to Google Photos.
    Uses state manager to track progress and enable resumption.
    Returns True if successful, False otherwise.
    """
    remote_path = file_info['path']
    file_name = os.path.basename(remote_path)
    # Sanitize filename for filesystem
    safe_name = "".join(c for c in file_name if c.isalnum() or c in "._- ")
    local_path = temp_dir / safe_name
    
    # Check if already completed
    if state.is_completed(remote_path):
        logger.info(f"⏭️  Skipping {file_name} - already uploaded successfully")
        return True
    
    # Check if should retry failed file
    if state.is_failed(remote_path) and not state.should_retry(remote_path):
        logger.info(f"⏭️  Skipping {file_name} - exceeded max retry attempts")
        return False
    
    logger.info("=" * 80)
    logger.info(f"Processing: {remote_path}")
    logger.info(f"Size: {file_info['size_gb']:.2f}GB")
    logger.info(f"Local path: {local_path}")
    
    if state.is_failed(remote_path):
        attempts = state.get_failure_count(remote_path)
        logger.info(f"⚠️  Previous failures: {attempts} - retrying...")
    
    logger.info("=" * 80)
    
    # Mark as in progress
    state.mark_in_progress(remote_path, file_info['size'])
    
    # Check available disk space before downloading
    stat = shutil.disk_usage(temp_dir)
    free_space_gb = stat.free / (1024**3)
    required_space_gb = file_info['size_gb'] + 2  # File size + 2GB buffer
    
    if free_space_gb < required_space_gb:
        logger.error(f"❌ Insufficient disk space!")
        logger.error(f"   Available: {free_space_gb:.1f}GB")
        logger.error(f"   Required: {required_space_gb:.1f}GB")
        logger.error(f"   Skipping this file (too large for available space)")
        return False
    
    logger.info(f"✓ Disk space check: {free_space_gb:.1f}GB available, {required_space_gb:.1f}GB needed")
    
    # Try download with aggressive retries (don't give up easily!)
    max_download_attempts = 5  # More attempts for large streaming files
    download_success = False
    
    for attempt in range(1, max_download_attempts + 1):
        if attempt > 1:
            wait_time = 60  # Fixed 60 second wait between attempts
            logger.info(f"⏳ Waiting {wait_time}s before retry...")
            time.sleep(wait_time)
        
        logger.info(f"🔄 Download attempt {attempt}/{max_download_attempts} for {file_name}")
        download_success = stream_file_from_ftp(remote, remote_path, local_path, attempt=attempt)
        
        if download_success:
            logger.info(f"✅ Download successful on attempt {attempt}!")
            break
        else:
            logger.warning(f"❌ Download attempt {attempt}/{max_download_attempts} failed")
            
            if attempt < max_download_attempts:
                logger.info(f"💪 Will retry (attempt {attempt + 1}/{max_download_attempts})...")
            else:
                logger.error(f"💔 All {max_download_attempts} download attempts exhausted")
                logger.error(f"📝 File marked as FAILED - will retry on next workflow run")
    
    if not download_success:
        error_msg = f"Failed to download after {max_download_attempts} attempts"
        logger.error(f"❌ {error_msg}")
        logger.error(f"⚠️ This file will be retried on next workflow run")
        state.mark_failed(remote_path, error_msg)
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
    logger.info("=" * 80)
    logger.info(f"📤 Preparing to upload to Google Photos")
    logger.info(f"File verified and ready: {local_path.name}")
    logger.info("=" * 80)
    
    media_key = upload_to_google_photos(local_path, auth_data)
    
    if media_key:
        logger.info("=" * 80)
        logger.info(f"✅ ✅ ✅ COMPLETE SUCCESS! ✅ ✅ ✅")
        logger.info(f"📁 File: {file_name}")
        logger.info(f"🔑 Media Key: {media_key}")
        logger.info(f"📸 Status: NOW IN GOOGLE PHOTOS")
        logger.info(f"💾 Quality: ORIGINAL (unlimited)")
        logger.info("=" * 80)
        
        # Mark as completed in state
        state.mark_completed(remote_path, file_info['size'], media_key)
        
        # Clean up local file to free space immediately
        try:
            local_path.unlink()
            logger.info(f"🗑️ Deleted local file: {local_path}")
        except Exception as e:
            logger.warning(f"Failed to delete local file: {e}")
        return True
    else:
        error_msg = "Upload failed after all retries"
        logger.error("=" * 80)
        logger.error(f"❌ ❌ ❌ UPLOAD FAILED ❌ ❌ ❌")
        logger.error(f"File: {file_name}")
        logger.error(f"Local file kept at: {local_path}")
        logger.error("=" * 80)
        
        # Mark as failed in state
        state.mark_failed(remote_path, error_msg)
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
    
    # Initialize state manager
    state = StateManager()
    logger.info("📊 Loaded upload state")
    state.print_summary()
    
    # Create temporary directory
    # Use /workspace if available (GitHub Actions maximize-build-space mount point)
    # Otherwise fall back to /tmp
    workspace_dir = Path('/workspace')
    if workspace_dir.exists() and workspace_dir.is_dir():
        temp_dir = workspace_dir / 'ftp_gphotos_temp'
        temp_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Using workspace directory: {temp_dir} (large disk space)")
    else:
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
            MIN_FILE_SIZE, MAX_FILE_SIZE, SUPPORTED_EXTENSIONS,
            state
        )
        
        logger.info("=" * 80)
        logger.info(f"Processing complete: {successful} successful, {failed} failed")
        logger.info("=" * 80)
        
        # Print final state summary
        state.print_summary()
        
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

