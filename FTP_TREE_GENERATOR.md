# FTP Structure Tree Generator

This workflow generates a complete manifest/tree file of all folders and files on the FTP server, preserving the original directory structure.

## Purpose

After uploading all movies to Google Photos (where everything was uploaded to the main directory), this tool helps you:
- **Understand the original FTP structure** - See how files were organized on the source
- **Plan reorganization** - Use the manifest to sort files into correct folders in Google Photos
- **Track ISO conversions** - Know which files were originally ISO (converted to MKV during upload)

## Output Files

The workflow generates two files:

### 1. `ftp_structure_manifest.json`
Complete structure in JSON format with metadata:
```json
{
  "metadata": {
    "generated_at": "2024-01-15T10:30:00",
    "server": "Challenger",
    "server_host": "challenger.whatbox.ca",
    "note": "ISO files were converted to MKV during upload to Google Photos"
  },
  "structure": {
    "type": "directory",
    "name": "root",
    "path": "",
    "files": [...],
    "subdirectories": [...],
    "total_files": 150,
    "total_size": 4500000000000
  },
  "statistics": {
    "total_files": 150,
    "total_size_bytes": 4500000000000,
    "total_size_gb": 4194.30
  }
}
```

### 2. `ftp_structure_tree.txt`
Human-readable tree visualization:
```
FTP SERVER DIRECTORY STRUCTURE
================================================================================
Server: Challenger (challenger.whatbox.ca)
Generated: 2024-01-15T10:30:00
Note: ISO files were converted to MKV during upload to Google Photos
================================================================================

└── 📁 root/ (150 files, 4194.30 GB)
    ├── 📁 3D Movies/ (50 files, 1500.50 GB)
    │   ├── 📄 Avatar.3D.2009.1080p.mkv (30.5 GB)
    │   ├── 📄 Gravity.3D.2013.1080p.mkv (25.2 GB)
    │   └── 📁 Blockbuster/ (20 files, 600.0 GB)
    │       ├── 📄 Movie1.mkv (30.0 GB)
    │       └── 📄 Movie2.iso (28.5 GB)
    └── 📁 Other Movies/ (100 files, 2693.80 GB)
        └── ...
```

## Usage

### Manual Run (Recommended)

1. Go to the **Actions** tab in your GitHub repository
2. Select **"Generate FTP Structure Tree"** workflow
3. Click **"Run workflow"**
4. Wait for completion (typically 5-30 minutes depending on FTP size)
5. Download artifacts from the workflow run:
   - `ftp-structure-manifest` - Contains both JSON and TXT files
   - `tree-generation-logs` - Detailed logs if you need to debug

### Scheduled Run

The workflow runs automatically on the 1st of each month at midnight UTC. You can modify the schedule in `.github/workflows/generate-ftp-tree.yml`:

```yaml
schedule:
  - cron: '0 0 1 * *'  # Monthly on the 1st
```

### Local Run

You can also run the script locally:

```bash
# Install rclone if not already installed
curl https://rclone.org/install.sh | sudo bash

# Configure rclone with your FTP credentials
rclone config

# Run the script
python3 generate_ftp_tree.py

# Output files will be created in the current directory:
# - ftp_structure_manifest.json
# - ftp_structure_tree.txt
```

## Using the Manifest

### Example: Reorganizing Google Photos

1. Download the manifest files from GitHub Actions artifacts
2. Open `ftp_structure_tree.txt` to see the visual structure
3. Open `ftp_structure_manifest.json` for programmatic access
4. Use the structure information to:
   - Create corresponding albums in Google Photos
   - Move files to match the original directory structure
   - Identify which ISO files are now MKV

### Example: Python Script to Process Manifest

```python
import json

# Load the manifest
with open('ftp_structure_manifest.json') as f:
    manifest = json.load(f)

# Get all files recursively
def get_all_files(node, path=""):
    files = []
    for file in node.get('files', []):
        files.append({
            'name': file['name'],
            'path': file['path'],
            'size_gb': file['size_gb'],
            'folder': path
        })
    for subdir in node.get('subdirectories', []):
        subpath = f"{path}/{subdir['name']}" if path else subdir['name']
        files.extend(get_all_files(subdir, subpath))
    return files

all_files = get_all_files(manifest['structure'])

# Example: Find all ISO files (now MKV in Google Photos)
iso_files = [f for f in all_files if f['name'].lower().endswith('.iso')]
print(f"Found {len(iso_files)} ISO files (converted to MKV)")

# Example: Group by folder
from collections import defaultdict
by_folder = defaultdict(list)
for f in all_files:
    by_folder[f['folder']].append(f)

for folder, files in sorted(by_folder.items()):
    print(f"\n{folder}/")
    for f in files:
        print(f"  - {f['name']} ({f['size_gb']} GB)")
```

## Configuration

You can modify the script to use different FTP servers by editing `generate_ftp_tree.py`:

```python
# Change the current server
CURRENT_SERVER = "Tamarind"  # Options: Challenger, Tamarind, Sputnik

# Or add new servers
FTP_SERVERS = {
    "MyServer": {"host": "ftp.example.com", "port": 21},
    # ...
}
```

## Troubleshooting

### "rclone not found"
- Ensure rclone is installed: `rclone version`
- In GitHub Actions, this is handled automatically

### "Failed to list directories"
- Check that `RCLONE_CONFIG` secret is set correctly in repository settings
- Verify FTP credentials are valid
- Check FTP server is accessible

### Timeout issues
- For very large FTP structures (10,000+ files), increase the workflow timeout:
  ```yaml
  timeout-minutes: 120  # 2 hours
  ```

### Empty manifest
- Verify the FTP server has files
- Check logs for connection errors
- Try running locally to debug

## Notes

- **ISO Conversion**: Remember that ISO files were automatically converted to MKV during upload to Google Photos
- **Retention**: Manifest artifacts are kept for 365 days (1 year)
- **Performance**: The script uses rclone's optimized FTP listing, but large structures may take time
- **Incremental**: Each run generates a fresh snapshot; it doesn't track changes over time

## Related Workflows

- **transfer.yml** - Main workflow that uploads files from FTP to Google Photos
- **monitor-and-restart.yml** - Monitors and restarts the transfer workflow

## License

MIT License
