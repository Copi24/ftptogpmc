#!/usr/bin/env python3
"""
Generate a complete tree/manifest of all folders and files in the FTP server.
This preserves the original structure so files can be sorted or moved in Google Photos.
Note: ISO files have been converted to MKV during upload.
"""

import os
import sys
import subprocess
import json
import logging
import traceback
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('ftp_tree_generation.log')
    ]
)
logger = logging.getLogger(__name__)

# Configuration
FTP_SERVERS = {
    "Challenger": {"host": "challenger.whatbox.ca", "port": 13017},
    "Tamarind": {"host": "tamarind.whatbox.ca", "port": 13017},
    "Sputnik": {"host": "sputnik.whatbox.ca", "port": 13017},
}
CURRENT_SERVER = "Challenger"  # Using Challenger (Blockbuster Movies)


def check_rclone_installed() -> bool:
    """Check if rclone is installed and accessible."""
    try:
        result = subprocess.run(['rclone', 'version'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info(f"✓ rclone version: {result.stdout.strip().split()[1]}")
            return True
        return False
    except FileNotFoundError:
        logger.error("❌ rclone not found in PATH")
        return False
    except Exception as e:
        logger.error(f"❌ Error checking rclone: {e}")
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
            logger.warning(f"⚠️  Failed to list directories in {path}: {result.stderr[:200]}")
            return []
        
        dirs = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            # lsd output format: size date time name
            # Example: "          -1 2025-10-31 10:33:09        -1 Challenger"
            # Note: First field is size, followed by date, time, and directory name
            parts = line.split()
            if len(parts) >= 4:
                # Skip first 3 columns: size, date, time
                dir_name = ' '.join(parts[3:])  # Handle names with spaces
                dirs.append(dir_name)
                logger.debug(f"  Found directory: {dir_name}")
        
        return dirs
        
    except Exception as e:
        logger.warning(f"⚠️  Error listing directories in {path}: {e}")
        return []


def list_all_files(remote: str, path: str = "") -> List[Dict]:
    """
    List ALL files in a directory (non-recursive) using rclone ls.
    Returns list of dicts with file info.
    """
    try:
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
            logger.warning(f"⚠️  Failed to list files in {path}: {result.stderr[:200]}")
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
                    
                    full_path = os.path.join(path, file_name) if path else file_name
                    # Normalize path separators
                    full_path = full_path.replace('\\', '/')
                    
                    files.append({
                        'name': file_name,
                        'path': full_path,
                        'size': file_size,
                        'size_gb': round(file_size / (1024**3), 2),
                        'size_mb': round(file_size / (1024**2), 2)
                    })
                except ValueError:
                    continue
        
        return files
        
    except Exception as e:
        logger.warning(f"⚠️  Error listing files in {path}: {e}")
        return []


def traverse_ftp_tree(remote: str, path: str = "", depth: int = 0) -> Dict:
    """
    Recursively traverse FTP directory structure and build a complete tree.
    Returns a dictionary representing the directory structure.
    """
    indent = "  " * depth
    display_path = path if path else '(root)'
    logger.info(f"{indent}📁 Scanning: {display_path}")
    
    tree_node = {
        'type': 'directory',
        'name': os.path.basename(path) if path else 'root',
        'path': path,
        'files': [],
        'subdirectories': [],
        'total_files': 0,
        'total_size': 0
    }
    
    # List all files in current directory
    files = list_all_files(remote, path)
    if files:
        logger.info(f"{indent}  ✓ Found {len(files)} file(s)")
        tree_node['files'] = files
        tree_node['total_files'] = len(files)
        tree_node['total_size'] = sum(f['size'] for f in files)
    
    # Get subdirectories and recurse
    subdirs = list_directories(remote, path)
    if subdirs:
        logger.info(f"{indent}  ↳ Found {len(subdirs)} subdirectory(ies)")
        for subdir in subdirs:
            # Use forward slashes for paths (rclone standard)
            if path:
                subpath = f"{path}/{subdir}"
            else:
                subpath = subdir
            
            # Recursively process subdirectory
            subtree = traverse_ftp_tree(remote, subpath, depth + 1)
            tree_node['subdirectories'].append(subtree)
            
            # Aggregate counts
            tree_node['total_files'] += subtree['total_files']
            tree_node['total_size'] += subtree['total_size']
    
    return tree_node


def generate_text_tree(tree_node: Dict, indent: str = "", is_last: bool = True) -> str:
    """
    Generate a human-readable text representation of the tree structure.
    Uses box-drawing characters for a nice visual tree.
    """
    lines = []
    
    # Current node
    name = tree_node['name']
    total_files = tree_node['total_files']
    total_size_gb = round(tree_node['total_size'] / (1024**3), 2)
    
    prefix = "└── " if is_last else "├── "
    
    if tree_node['type'] == 'directory':
        lines.append(f"{indent}{prefix}📁 {name}/ ({total_files} files, {total_size_gb} GB)")
        
        # Extension for children
        extension = "    " if is_last else "│   "
        
        # List files
        files = tree_node.get('files', [])
        subdirs = tree_node.get('subdirectories', [])
        
        # Show files first
        for i, file_info in enumerate(files):
            is_last_item = (i == len(files) - 1) and len(subdirs) == 0
            file_prefix = "└── " if is_last_item else "├── "
            file_size = file_info['size_mb']
            size_str = f"{file_info['size_gb']} GB" if file_size >= 1024 else f"{file_size} MB"
            lines.append(f"{indent}{extension}{file_prefix}📄 {file_info['name']} ({size_str})")
        
        # Show subdirectories
        for i, subdir in enumerate(subdirs):
            is_last_subdir = (i == len(subdirs) - 1)
            lines.append(generate_text_tree(subdir, indent + extension, is_last_subdir))
    
    return '\n'.join(lines)


def save_manifest(tree: Dict, output_file: str = "ftp_structure_manifest.json"):
    """
    Save the tree structure as a JSON manifest file.
    """
    manifest = {
        'metadata': {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'server': CURRENT_SERVER,
            'server_host': FTP_SERVERS[CURRENT_SERVER]['host'],
            'note': 'ISO files were converted to MKV during upload to Google Photos'
        },
        'structure': tree,
        'statistics': {
            'total_files': tree['total_files'],
            'total_size_bytes': tree['total_size'],
            'total_size_gb': round(tree['total_size'] / (1024**3), 2)
        }
    }
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        logger.info(f"✅ Manifest saved to {output_file}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save manifest: {e}")
        return False


def save_text_tree(tree_text: str, output_file: str = "ftp_structure_tree.txt"):
    """
    Save the human-readable tree to a text file.
    """
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("FTP SERVER DIRECTORY STRUCTURE\n")
            f.write("=" * 80 + "\n")
            f.write(f"Server: {CURRENT_SERVER} ({FTP_SERVERS[CURRENT_SERVER]['host']})\n")
            f.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
            f.write("Note: ISO files were converted to MKV during upload to Google Photos\n")
            f.write("=" * 80 + "\n\n")
            f.write(tree_text)
        logger.info(f"✅ Tree visualization saved to {output_file}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save tree: {e}")
        return False


def main():
    """Main function to generate FTP structure tree."""
    logger.info("=" * 80)
    logger.info("FTP STRUCTURE TREE GENERATOR")
    logger.info("=" * 80)
    logger.info(f"Server: {CURRENT_SERVER}")
    logger.info(f"Host: {FTP_SERVERS[CURRENT_SERVER]['host']}:{FTP_SERVERS[CURRENT_SERVER]['port']}")
    logger.info("")
    
    # Check rclone
    if not check_rclone_installed():
        logger.error("❌ rclone is not installed. Please install it first.")
        sys.exit(1)
    
    # Generate the tree
    logger.info("📡 Starting FTP directory traversal...")
    logger.info("")
    
    try:
        tree = traverse_ftp_tree(CURRENT_SERVER)
        
        logger.info("")
        logger.info("=" * 80)
        logger.info("📊 SCAN COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Total files found: {tree['total_files']}")
        logger.info(f"Total size: {round(tree['total_size'] / (1024**3), 2)} GB")
        logger.info("")
        
        # Generate text visualization
        logger.info("📝 Generating tree visualization...")
        tree_text = generate_text_tree(tree)
        
        # Save outputs
        logger.info("💾 Saving outputs...")
        manifest_saved = save_manifest(tree)
        tree_saved = save_text_tree(tree_text)
        
        if manifest_saved and tree_saved:
            logger.info("")
            logger.info("✅ SUCCESS! Files generated:")
            logger.info("   • ftp_structure_manifest.json - Complete structure with metadata (JSON)")
            logger.info("   • ftp_structure_tree.txt - Human-readable tree visualization")
            logger.info("")
            logger.info("These files preserve the original FTP structure and can be used to:")
            logger.info("   - Sort files into correct folders in Google Photos")
            logger.info("   - Move files based on original directory structure")
            logger.info("   - Track which ISO files were converted to MKV")
            return 0
        else:
            logger.error("❌ Failed to save one or more output files")
            return 1
            
    except KeyboardInterrupt:
        logger.info("\n⚠️  Interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"❌ Error during tree generation: {e}")
        logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
