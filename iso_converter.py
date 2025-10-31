#!/usr/bin/env python3
"""
Fast ISO to MKV converter for Blu-ray/DVD images.
Uses remuxing (no re-encoding) for ultra-fast conversion.
"""

import subprocess
import logging
from pathlib import Path
import os
import shutil

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
        
        logger.info(f"Running: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Monitor progress
        for line in process.stdout:
            if 'Duration:' in line or 'time=' in line or 'frame=' in line:
                logger.info(f"ffmpeg: {line.strip()}")
        
        process.wait()
        
        if process.returncode == 0 and output_file.exists():
            input_size = input_file.stat().st_size
            output_size = output_file.stat().st_size
            logger.info(f"✅ Remux complete! {input_size / (1024**3):.2f}GB → {output_size / (1024**3):.2f}GB")
            return True
        else:
            logger.error(f"❌ Remux failed with code {process.returncode}")
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
    """Clean up mounted or extracted files."""
    if not extract_point or not extract_point.exists():
        return
    
    try:
        # Try unmount first
        result = subprocess.run(
            ['sudo', 'umount', str(extract_point)],
            capture_output=True,
            timeout=30,
            stderr=subprocess.DEVNULL
        )
        if result.returncode == 0:
            extract_point.rmdir()
            logger.info(f"🗑️ Unmounted and cleaned up {extract_point}")
            return
    except:
        pass
    
    # If mount failed, it's an extract directory - delete it
    try:
        if extract_point.is_dir():
            shutil.rmtree(extract_point)
            logger.info(f"🗑️ Cleaned up extracted directory {extract_point}")
    except Exception as e:
        logger.warning(f"Failed to cleanup {extract_point}: {e}")

