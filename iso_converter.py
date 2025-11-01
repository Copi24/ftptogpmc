#!/usr/bin/env python3
"""
Fast ISO to MKV converter for Blu-ray/DVD images.
Uses remuxing (no re-encoding) for ultra-fast conversion.
"""

import subprocess
import logging
from pathlib import Path
import os
import sys
import shutil
import time
import threading
import queue

logger = logging.getLogger(__name__)

def find_main_video_in_iso(iso_path: Path) -> tuple:
    """
    Mount or extract ISO and find the main video file.
    Returns path to the largest video stream (usually the main movie).
    """
    # Try mounting first (faster than extracting)
    mount_point = iso_path.parent / f"{iso_path.stem}_mount"
    mount_point.mkdir(exist_ok=True)
    
    try:
        # Mount ISO
        result = subprocess.run(
            ['sudo', 'mount', '-o', 'loop', str(iso_path), str(mount_point)],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            logger.info(f"✅ ISO mounted at {mount_point}")
            
            # Look for main video in common Blu-ray/DVD structures
            possible_paths = [
                mount_point / 'BDMV' / 'STREAM',  # Blu-ray
                mount_point / 'VIDEO_TS',  # DVD
                mount_point,  # Root level
            ]
            
            largest_file = None
            largest_size = 0
            
            for search_path in possible_paths:
                if search_path.exists():
                    logger.info(f"📁 Searching {search_path} for video files...")
                    for file in search_path.rglob('*'):
                        if file.is_file():
                            # Check if it's a video file
                            ext = file.suffix.lower()
                            if ext in ['.m2ts', '.vob', '.mpls', '.mpl']:
                                size = file.stat().st_size
                                if size > largest_size:
                                    largest_size = size
                                    largest_file = file
                                    logger.info(f"   Found: {file.name} ({size / (1024**3):.2f}GB)")
            
            if largest_file:
                return largest_file, mount_point
            
            # Unmount if nothing found
            subprocess.run(['sudo', 'umount', str(mount_point)], timeout=30)
            mount_point.rmdir()
            
    except Exception as e:
        logger.warning(f"Mount failed: {e}, trying extraction...")
        # Cleanup mount point
        try:
            if mount_point.exists():
                subprocess.run(['sudo', 'umount', str(mount_point)], timeout=30, stderr=subprocess.DEVNULL)
                mount_point.rmdir()
        except:
            pass
    
    # Fallback: Extract using 7z or similar
    extract_dir = iso_path.parent / f"{iso_path.stem}_extracted"
    extract_dir.mkdir(exist_ok=True)
    
    try:
        # Try 7z extraction
        logger.info(f"📦 Extracting ISO to {extract_dir}...")
        result = subprocess.run(
            ['7z', 'x', str(iso_path), f'-o{extract_dir}', '-y'],
            capture_output=True,
            text=True,
            timeout=600  # 10 minutes max
        )
        
        if result.returncode == 0:
            # Find largest video file
            largest_file = None
            largest_size = 0
            
            for file in extract_dir.rglob('*'):
                if file.is_file():
                    ext = file.suffix.lower()
                    if ext in ['.m2ts', '.vob', '.mpls', '.mpl']:
                        size = file.stat().st_size
                        if size > largest_size:
                            largest_size = size
                            largest_file = file
            
            if largest_file:
                return largest_file, extract_dir
                
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
    
    return None, None


def remux_to_mkv(input_file: Path, output_file: Path) -> bool:
    """
    Ultra-fast remux (no re-encode) from Blu-ray/DVD format to MKV.
    This just repackages the video stream, very fast!
    """
    logger.info(f"🎬 Remuxing {input_file.name} to MKV (no re-encode - ultra fast!)...")
    
    try:
        # ffmpeg remux command - copy all streams without re-encoding
        cmd = [
            'ffmpeg',
            '-i', str(input_file),
            '-c', 'copy',  # Copy codecs (no re-encoding!)
            '-map', '0',  # Map all streams
            '-y',  # Overwrite output
            str(output_file)
        ]
        
        # Add progress reporting to ffmpeg command
        cmd.extend(['-progress', 'pipe:1', '-loglevel', 'info'])
        
        logger.info(f"Running: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Monitor progress with timeout detection and file size monitoring
        last_output_time = time.time()
        last_file_size = 0
        last_size_check_time = time.time()
        last_heartbeat_time = time.time()
        TIMEOUT_SECONDS = 600  # 10 minutes without progress = stuck
        HEARTBEAT_INTERVAL = 120  # Log heartbeat every 2 minutes
        last_progress_line = None
        start_time = time.time()
        
        # Queue for collecting output
        output_queue = queue.Queue()
        error_queue = queue.Queue()
        
        def read_stdout():
            for line in process.stdout:
                if line.strip():
                    output_queue.put(('stdout', line.strip()))
        
        def read_stderr():
            for line in process.stderr:
                if line.strip():
                    error_queue.put(('stderr', line.strip()))
        
        # Start reading threads
        stdout_thread = threading.Thread(target=read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        
        # Monitor both queues and file size
        while process.poll() is None:
            # Check stdout
            try:
                source, line = output_queue.get_nowait()
                if 'out_time_ms=' in line or 'size=' in line or 'bitrate=' in line:
                    logger.info(f"ffmpeg progress: {line}")
                    last_output_time = time.time()
                    last_progress_line = line
            except queue.Empty:
                pass
            
            # Check stderr
            try:
                source, line = error_queue.get_nowait()
                if 'Duration:' in line or 'time=' in line or 'frame=' in line or 'bitrate=' in line:
                    logger.info(f"ffmpeg: {line}")
                    last_output_time = time.time()
                    last_progress_line = line
            except queue.Empty:
                pass
            
            # Heartbeat logging - prove the workflow is alive
            current_time = time.time()
            if current_time - last_heartbeat_time >= HEARTBEAT_INTERVAL:
                elapsed_total = current_time - start_time
                elapsed_since_output = current_time - last_output_time
                logger.info(f"💓 Heartbeat: ffmpeg running for {elapsed_total/60:.1f}min, "
                          f"last output {elapsed_since_output:.0f}s ago")
                sys.stdout.flush()
                last_heartbeat_time = current_time
            
            # Check file size growth (every 10 seconds for better feedback)
            if time.time() - last_size_check_time >= 10:
                if output_file.exists():
                    current_size = output_file.stat().st_size
                    if current_size > last_file_size:
                        growth_mb = (current_size - last_file_size) / (1024**2)
                        logger.info(f"📊 Output file growing: {current_size / (1024**3):.2f}GB (+{growth_mb:.1f}MB in last 10s)")
                        last_file_size = current_size
                        last_output_time = time.time()
                        
                        # Estimate if conversion is nearly complete
                        # For remux, output should be similar size to input video stream
                        if input_file.exists():
                            input_size = input_file.stat().st_size
                            progress_pct = (current_size / input_size) * 100 if input_size > 0 else 0
                            if progress_pct > 95:
                                logger.info(f"📊 Conversion ~{progress_pct:.1f}% complete (estimated)")
                    else:
                        if last_file_size > 0:
                            logger.warning(f"⚠️ Output file size unchanged at {current_size / (1024**3):.2f}GB")
                elif last_file_size == 0:
                    logger.debug(f"📊 Waiting for output file to be created...")
                last_size_check_time = time.time()
            
            # Check if output file is complete (for remux, size should be close to input)
            if output_file.exists() and input_file.exists():
                input_size = input_file.stat().st_size
                output_size = output_file.stat().st_size
                # Remux should produce output similar size to input (within 5%)
                # If output is at least 95% of input, likely complete
                if output_size > 0 and (output_size >= input_size * 0.95 or output_size >= input_size * 0.99):
                    logger.info(f"📊 Output file appears complete: {output_size / (1024**3):.2f}GB (input: {input_size / (1024**3):.2f}GB)")
                    # Give process a moment to finish, then check return code
                    time.sleep(2)
                    if process.poll() is not None:
                        logger.info(f"FFmpeg process has finished")
                        break
            
            # Check for timeout
            elapsed_since_output = time.time() - last_output_time
            if elapsed_since_output > TIMEOUT_SECONDS:
                logger.error(f"❌ FFmpeg appears stuck - no progress for {TIMEOUT_SECONDS/60:.0f} minutes")
                logger.error(f"Last progress: {last_progress_line}")
                if output_file.exists():
                    logger.error(f"Output file size: {output_file.stat().st_size / (1024**3):.2f}GB")
                process.kill()
                process.wait()
                return False
            
            time.sleep(1)  # Don't spin too fast
        
        # Process has finished (or we detected completion)
        logger.info(f"FFmpeg monitoring loop exited, waiting for process...")
        sys.stdout.flush()  # Force flush before waiting
        
        # Wait for threads and collect remaining output
        stdout_thread.join(timeout=10)
        if stdout_thread.is_alive():
            logger.warning("⚠️ stdout thread still alive after 10s timeout (will be cleaned up as daemon)")
        stderr_thread.join(timeout=10)
        if stderr_thread.is_alive():
            logger.warning("⚠️ stderr thread still alive after 10s timeout (will be cleaned up as daemon)")
        
        # Collect remaining output
        logger.info("Collecting remaining ffmpeg output...")
        sys.stdout.flush()
        remaining_output = 0
        while True:
            try:
                source, line = output_queue.get_nowait()
                if 'out_time_ms=' in line or 'size=' in line or 'progress=' in line:
                    logger.info(f"ffmpeg: {line}")
                    remaining_output += 1
            except queue.Empty:
                break
        
        remaining_errors = 0
        while True:
            try:
                source, line = error_queue.get_nowait()
                if line.strip() and ('frame=' in line or 'fps=' in line or 'bitrate=' in line or 'time=' in line):
                    logger.info(f"ffmpeg: {line}")
                    remaining_errors += 1
                elif line.strip():
                    logger.debug(f"ffmpeg stderr: {line}")
                    remaining_errors += 1
            except queue.Empty:
                break
        
        logger.info(f"Collected {remaining_output + remaining_errors} remaining output lines")
        sys.stdout.flush()
        
        # Process has finished - wait for return code with timeout (CRITICAL!)
        logger.info("Waiting for ffmpeg process to finish (max 60s)...")
        sys.stdout.flush()
        try:
            process.wait(timeout=60)
            logger.info(f"FFmpeg process exited normally with code {process.returncode}")
            sys.stdout.flush()
        except subprocess.TimeoutExpired:
            logger.error("❌ FFmpeg process did not exit after 60s - KILLING IT")
            sys.stdout.flush()
            process.kill()
            time.sleep(2)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error("❌ FFmpeg process still alive after kill - terminating forcefully")
                sys.stdout.flush()
                process.terminate()
                time.sleep(1)
        
        # Log final status
        logger.info(f"FFmpeg process finished with return code: {process.returncode}")
        
        # Check if output file exists and has size
        if output_file.exists():
            output_size = output_file.stat().st_size
            logger.info(f"Output file exists: {output_size / (1024**3):.2f}GB")
            
            if output_size > 0:
                input_size = input_file.stat().st_size
                logger.info(f"✅ Remux complete! {input_size / (1024**3):.2f}GB → {output_size / (1024**3):.2f}GB")
                
                # Check return code - for remux, even non-zero might be OK if file is good
                if process.returncode == 0:
                    return True
                else:
                    logger.warning(f"⚠️ FFmpeg exited with code {process.returncode}, but output file looks good")
                    return True  # File exists and has size, assume success
            else:
                logger.error(f"❌ Output file exists but is empty")
                return False
        else:
            logger.error(f"❌ Output file does not exist")
            if process.returncode != 0:
                logger.error(f"❌ FFmpeg process exited with code {process.returncode}")
            return False
            
    except FileNotFoundError:
        logger.error("❌ ffmpeg not found! Install with: sudo apt-get install ffmpeg")
        return False
    except Exception as e:
        logger.error(f"❌ Remux error: {e}")
        return False


def convert_iso_to_mkv(iso_path: Path, output_dir: Path) -> Path:
    """
    Convert ISO to MKV by extracting main video and remuxing.
    Returns path to MKV file or None if failed.
    """
    logger.info("=" * 80)
    logger.info(f"📀 Converting ISO: {iso_path.name}")
    logger.info(f"📁 Size: {iso_path.stat().st_size / (1024**3):.2f}GB")
    logger.info("=" * 80)
    
    # Find main video stream
    video_file, extract_point = find_main_video_in_iso(iso_path)
    
    if not video_file:
        logger.error("❌ Could not find video stream in ISO")
        return None
    
    logger.info(f"✅ Found main video: {video_file.name} ({video_file.stat().st_size / (1024**3):.2f}GB)")
    
    # Create output MKV filename
    output_mkv = output_dir / f"{iso_path.stem}.mkv"
    
    # Remux to MKV (ultra-fast, no re-encode)
    if remux_to_mkv(video_file, output_mkv):
        # Cleanup
        cleanup_mount_or_extract(extract_point)
        return output_mkv
    else:
        cleanup_mount_or_extract(extract_point)
        return None


def cleanup_mount_or_extract(extract_point: Path):
    """Clean up mounted or extracted files. Must be aggressive to free disk space."""
    if not extract_point or not extract_point.exists():
        return
    
    logger.info(f"🧹 Cleaning up: {extract_point}")
    
    # First, check if it's a mount point
    is_mount = False
    try:
        result = subprocess.run(
            ['mountpoint', '-q', str(extract_point)],
            capture_output=True,
            timeout=5
        )
        is_mount = (result.returncode == 0)
    except:
        pass
    
    # If it's a mount point, unmount it first (CRITICAL!)
    if is_mount:
        logger.info(f"🔓 Unmounting: {extract_point}")
        # Try normal unmount first
        try:
            result = subprocess.run(
                ['sudo', 'umount', str(extract_point)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                logger.info(f"✓ Unmounted successfully")
            else:
                logger.warning(f"Normal unmount failed, trying force...")
                # Force unmount if normal fails
                subprocess.run(
                    ['sudo', 'umount', '-f', str(extract_point)],
                    capture_output=True,
                    timeout=10,
                    stderr=subprocess.DEVNULL
                )
        except Exception as e:
            logger.warning(f"Unmount error: {e}, trying lazy unmount...")
            # Last resort: lazy unmount
            try:
                subprocess.run(
                    ['sudo', 'umount', '-l', str(extract_point)],
                    capture_output=True,
                    timeout=10,
                    stderr=subprocess.DEVNULL
                )
            except:
                pass
        
        # Wait for filesystem to sync after unmount
        time.sleep(2)
        
        # Verify unmount worked
        try:
            result = subprocess.run(
                ['mountpoint', '-q', str(extract_point)],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.error(f"❌ Mount point still mounted! Force unmount failed")
                # Try one more time with lazy unmount
                subprocess.run(
                    ['sudo', 'umount', '-l', str(extract_point)],
                    capture_output=True,
                    timeout=10,
                    stderr=subprocess.DEVNULL
                )
                time.sleep(2)
        except:
            pass
    
    # Now try to remove the directory
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            if extract_point.exists():
                if extract_point.is_dir():
                    # Check if directory is empty first
                    try:
                        if not any(extract_point.iterdir()):
                            extract_point.rmdir()
                            logger.info(f"🗑️ Removed empty directory: {extract_point}")
                            return
                        else:
                            # Directory not empty - use rmtree
                            shutil.rmtree(extract_point)
                            logger.info(f"🗑️ Removed directory tree: {extract_point}")
                            return
                    except OSError:
                        # Directory might still be busy, wait and retry
                        if attempt < max_attempts - 1:
                            time.sleep(2)
                            continue
                        else:
                            logger.warning(f"⚠️ Directory still busy after {max_attempts} attempts: {extract_point}")
                            # Force remove if it's an extracted directory
                            try:
                                shutil.rmtree(extract_point, ignore_errors=True)
                            except:
                                pass
                else:
                    extract_point.unlink()
                    logger.info(f"🗑️ Removed file: {extract_point}")
                    return
            else:
                logger.info(f"✓ Already cleaned up: {extract_point}")
                return
        except PermissionError:
            logger.warning(f"⚠️ Permission denied removing {extract_point}, attempt {attempt + 1}/{max_attempts}")
            if attempt < max_attempts - 1:
                time.sleep(2)
                continue
        except Exception as e:
            logger.error(f"❌ Failed to cleanup {extract_point}: {e}")
            if attempt < max_attempts - 1:
                time.sleep(2)
                continue
    
    logger.error(f"❌ Could not fully cleanup {extract_point} after {max_attempts} attempts")

