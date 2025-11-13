#!/usr/bin/env python3
"""
State manager for tracking upload progress and enabling smart resumption.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Set
from datetime import datetime

STATE_FILE = "upload_state.json"

class StateManager:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = Path(state_file)
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        """Load state from file or create new state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                
                # Migrate old format (v1.0) to new format (v2.0)
                if state.get('version') == '1.0' and isinstance(state.get('completed'), list):
                    print("ℹ️  Migrating state from v1.0 to v2.0 format...")
                    old_completed = state['completed']
                    state['completed'] = {}
                    # Convert list to dict (no media keys available from old format)
                    for file_path in old_completed:
                        state['completed'][file_path] = {
                            'media_key': None,  # Not available in old format
                            'size': 0,
                            'timestamp': state.get('last_updated', datetime.utcnow().isoformat())
                        }
                    state['version'] = '2.0'
                    print(f"✅ Migrated {len(old_completed)} files to new format")
                
                return state
            except Exception as e:
                print(f"Warning: Could not load state file: {e}")
                return self._create_new_state()
        return self._create_new_state()
    
    def _create_new_state(self) -> Dict:
        """Create new empty state."""
        return {
            'version': '2.0',  # Version 2.0 uses dict for completed with media keys
            'last_updated': datetime.utcnow().isoformat(),
            'completed': {},      # Files successfully uploaded (path -> {media_key, size, timestamp})
            'failed': {},         # Files that failed with attempt count
            'in_progress': None,  # Currently processing file
            'skipped': [],        # Files skipped (too large, etc)
            'stats': {
                'total_uploaded': 0,
                'total_failed': 0,
                'total_bytes': 0
            }
        }
    
    def _save_state(self):
        """Save current state to file."""
        self.state['last_updated'] = datetime.utcnow().isoformat()
        try:
            # Write to temp file first, then rename (atomic)
            temp_file = self.state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(self.state, f, indent=2)
            temp_file.replace(self.state_file)
        except Exception as e:
            print(f"Warning: Could not save state: {e}")
    
    def is_completed(self, file_path: str) -> bool:
        """Check if file was already successfully uploaded."""
        return file_path in self.state['completed']
    
    def is_failed(self, file_path: str) -> bool:
        """Check if file has failed before."""
        return file_path in self.state['failed']
    
    def get_failure_count(self, file_path: str) -> int:
        """Get number of times file has failed."""
        return self.state['failed'].get(file_path, {}).get('attempts', 0)
    
    def should_retry(self, file_path: str, max_failures: int = 3) -> bool:
        """Check if we should retry a failed file."""
        if not self.is_failed(file_path):
            return True
        return self.get_failure_count(file_path) < max_failures
    
    def mark_in_progress(self, file_path: str, size_bytes: int):
        """Mark file as currently being processed."""
        self.state['in_progress'] = {
            'path': file_path,
            'size': size_bytes,
            'started_at': datetime.utcnow().isoformat()
        }
        self._save_state()
    
    def mark_completed(self, file_path: str, size_bytes: int, media_key: str, album_name: str = None):
        """Mark file as successfully uploaded."""
        if file_path not in self.state['completed']:
            self.state['stats']['total_uploaded'] += 1
            self.state['stats']['total_bytes'] += size_bytes
        
        # Store with media key and metadata (v2.0 format)
        self.state['completed'][file_path] = {
            'media_key': media_key,
            'size': size_bytes,
            'timestamp': datetime.utcnow().isoformat(),
            'album_name': album_name  # Store album name for tracking
        }
        
        # Remove from failed if it was there
        if file_path in self.state['failed']:
            del self.state['failed'][file_path]
        
        self.state['in_progress'] = None
        self._save_state()
    
    def mark_failed(self, file_path: str, reason: str):
        """Mark file as failed."""
        if file_path not in self.state['failed']:
            self.state['failed'][file_path] = {
                'attempts': 0,
                'last_error': '',
                'first_failed': datetime.utcnow().isoformat()
            }
        
        self.state['failed'][file_path]['attempts'] += 1
        self.state['failed'][file_path]['last_error'] = reason
        self.state['failed'][file_path]['last_failed'] = datetime.utcnow().isoformat()
        self.state['stats']['total_failed'] += 1
        
        self.state['in_progress'] = None
        self._save_state()
    
    def mark_skipped(self, file_path: str, reason: str):
        """Mark file as skipped."""
        if file_path not in self.state['skipped']:
            self.state['skipped'].append(file_path)
        self.state['in_progress'] = None
        self._save_state()
    
    def get_completed_files(self) -> List[str]:
        """Get list of completed files."""
        return self.state['completed']
    
    def get_failed_files(self) -> Dict:
        """Get dictionary of failed files with details."""
        return self.state['failed']
    
    def get_stats(self) -> Dict:
        """Get upload statistics."""
        return self.state['stats']
    
    def print_summary(self):
        """Print a summary of current state."""
        print("=" * 80)
        print("📊 UPLOAD STATE SUMMARY")
        print("=" * 80)
        print(f"✅ Completed: {len(self.state['completed'])} files")
        print(f"❌ Failed: {len(self.state['failed'])} files")
        print(f"⏭️  Skipped: {len(self.state['skipped'])} files")
        print(f"📦 Total uploaded: {self.state['stats']['total_bytes'] / (1024**3):.2f}GB")
        
        if self.state['in_progress']:
            print(f"🔄 In progress: {self.state['in_progress']['path']}")
        
        if self.state['failed']:
            print(f"\n⚠️  Files with failures:")
            for path, info in self.state['failed'].items():
                print(f"   • {path}: {info['attempts']} attempts")
        
        print("=" * 80)

