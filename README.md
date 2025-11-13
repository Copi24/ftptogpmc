# FTP to Google Photos Transfer

Automated script to transfer large 3D movie files (~30GB) from FTP servers to Google Photos using GitHub Actions.

## Features

- 🚀 **Smart Resumption**: Automatically resumes from where it left off if connection drops or workflow times out
- 📊 **State Tracking**: Persistent state file tracks completed/failed uploads across workflow runs  
- 🔄 **Download Resume**: Resumes partial downloads if interrupted
- ⏭️ **Skip Completed**: Automatically skips files already successfully uploaded
- 💾 **Maximum Disk Space**: Uses ~60GB of available disk space (up from 14GB) via LVM optimization
- 📁 **Automatic Discovery**: Recursively scans FTP server for large movie files (.mkv, .iso, etc.)
- 📂 **Folder Organization**: Uploads files directly to albums matching FTP folder structure (e.g., "Blockbuster Movies/Avatar (2009)")
  - Automatically creates or reuses existing albums (no duplicates)
  - Preserves folder hierarchy in flat naming scheme (Google Photos doesn't support nested folders)
  - Smart continuation tracks which files uploaded to which albums
- 🛡️ **Robust Error Handling**: Retry logic with stall detection and exponential backoff
- ⏱️ **GitHub Actions**: Runs automatically on schedule or manually via workflow dispatch
- 🗑️ **Auto Cleanup**: Deletes local files immediately after successful upload to free space
- 🌲 **FTP Structure Tree**: Generate complete manifest of FTP directory structure (see [FTP_TREE_GENERATOR.md](FTP_TREE_GENERATOR.md))

## Setup

### 1. GitHub Repository Setup

1. Create a new GitHub repository
2. Clone this repository:
   ```bash
   git clone <your-repo-url>
   cd ftptogpmc
   ```

### 2. Configure Secrets

Go to your repository Settings → Secrets and variables → Actions and add:

#### `GP_AUTH_DATA`
Your Google Photos authentication data. Get it using one of these methods:

**Option 1 - ReVanced (No root required):**
1. Install Google Photos ReVanced on your device
2. Install GmsCore: https://github.com/ReVanced/GmsCore/releases
3. Connect device via ADB
4. Run: `adb logcat | grep "auth%2Fphotos.native"`
5. Remove Google Account from GmsCore
6. Open Google Photos ReVanced and log in
7. Copy the log line from `androidId=` to the end

**Option 2 - Official APK (Root required):**
1. Get a rooted Android device or emulator (Android 9-13 recommended)
2. Install HTTP Toolkit
3. Configure interception: `Android Device via ADB`
4. Filter: `contains(https://www.googleapis.com/auth/photos.native)`
5. Open Google Photos and log in
6. Copy the request body from the intercepted request

#### `RCLONE_CONFIG`
Your rclone configuration file content. Example:

```ini
[3DFlickFix]
type = ftp
host = sputnik.whatbox.ca
user = your_username
port = 13017
explicit_tls = true
pass = your_password
no_check_certificate = true

[3DFlickFix2]
type = ftp
host = tamarind.whatbox.ca
user = your_username
port = 13017
explicit_tls = true
pass = your_password
no_check_certificate = true

[3DFlickFix3]
type = ftp
host = challenger.whatbox.ca
user = your_username
port = 13017
explicit_tls = true
pass = your_password
no_check_certificate = true

[3DFF]
type = combine
upstreams = Sputnik=3DFlickFix: Tamarind=3DFlickFix2: Challenger=3DFlickFix3:
```

### 3. Configure File Filters (Optional)

Edit `ftp_to_gphotos.py` to adjust:

- `MIN_FILE_SIZE`: Minimum file size (default: 20GB)
- `MAX_FILE_SIZE`: Maximum file size (default: 50GB)
- `SUPPORTED_EXTENSIONS`: File extensions to process (default: `.mkv`, `.iso`, `.mp4`, `.m4v`)
- `RCLONE_REMOTE`: Remote name in rclone config (default: `3DFF`)

## Usage

### Manual Run

1. Go to Actions tab in your repository
2. Select "FTP to Google Photos Transfer"
3. Click "Run workflow"
4. Monitor the logs

### Scheduled Run

The workflow runs daily at 2 AM UTC. You can modify the schedule in `.github/workflows/transfer.yml`:

```yaml
schedule:
  - cron: '0 2 * * *'  # Daily at 2 AM UTC
```

### Local Run

```bash
# Install dependencies
pip install -r requirements.txt

# Install rclone
# Linux: curl https://rclone.org/install.sh | sudo bash
# Windows: Download from https://rclone.org/downloads/

# Configure rclone
rclone config

# Set environment variable
export GP_AUTH_DATA="your_auth_data_here"

# Run script
python3 ftp_to_gphotos.py
```

## How It Works

1. **State Loading**: Loads previous upload state from GitHub Actions artifacts (if exists)
2. **Discovery**: Depth-first traversal of FTP directories to find movie files
3. **Filtering**: Identifies large movie files matching size and extension criteria
4. **Smart Skip**: Skips files already successfully uploaded in previous runs
5. **Resumable Download**: Downloads files using `rclone copy` with resume support
6. **Folder Detection**: Extracts folder path from FTP location (e.g., "Blockbuster Movies/Avatar (2009)")
7. **Upload with Folders**: Uploads to Google Photos using `gpmc` library with original quality (unlimited)
   - Files are uploaded directly to albums matching their FTP folder structure
   - Album names use full path (e.g., "Blockbuster Movies/Avatar (2009)")
   - GPMC automatically creates albums or reuses existing ones (no duplicates)
   - Files at root level are uploaded without an album
8. **State Update**: Marks file as completed in persistent state file with album info
9. **Cleanup**: Deletes local file immediately after successful upload
10. **State Persistence**: Uploads state file as artifact for next workflow run
11. **Logging**: Detailed logs saved to `ftp_to_gphotos.log` and uploaded as artifact

## Logs

Logs are automatically uploaded as GitHub Actions artifacts. You can download them from the Actions tab.

Logs include:
- File discovery progress
- Download progress and speeds
- Upload progress and media keys
- Error messages and retry attempts
- Disk space information

## Troubleshooting

### "rclone not found"
- Ensure rclone is installed: `rclone version`
- Check PATH includes rclone binary location

### "GP_AUTH_DATA not set"
- Verify secret is set in GitHub repository settings
- For local runs, export: `export GP_AUTH_DATA="your_data"`

### "Insufficient disk space"
- GitHub Actions runners now have ~60GB free space (using maximize-build-space action)
- Script automatically checks available space before processing
- Maximum supported file size is 50GB (with safety buffer)
- Files up to 35GB should process reliably

### "Upload failed"
- Check Google Photos auth data is still valid
- Verify internet connection stability
- Check logs for specific error messages

### Slow transfers
- FTP servers can be slow for large files
- Script includes retry logic and progress reporting
- Consider running during off-peak hours

## Limitations

- GitHub Actions runners have ~60GB usable storage (optimized via LVM)
- Maximum file size: 50GB (files 35GB and under are most reliable)
- FTP transfer speed depends on server performance (typically slow for 30GB+ files)
- Google Photos API requires full file download before upload (true streaming not possible)
- Workflow timeout: 6 hours max per run (but resumes automatically on next run)

## License

MIT License

## State Management

The script uses a persistent `upload_state.json` file to track progress:

```json
{
  "version": "2.0",
  "last_updated": "2024-01-15T10:30:00",
  "completed": {
    "/Movies/Avatar 3D (2009)/Avatar.3D.2009.1080p.mkv": {
      "media_key": "AF1QipNh...",
      "size": 32212254720,
      "timestamp": "2024-01-15T10:30:00",
      "album_name": "Movies/Avatar 3D (2009)"
    }
  },
  "failed": {
    "/Movies/LargeFile.mkv": {
      "attempts": 2,
      "last_error": "Connection timeout",
      "last_failed": "2024-01-15T09:00:00"
    }
  },
  "stats": {
    "total_uploaded": 5,
    "total_failed": 1,
    "total_bytes": 150000000000
  }
}
```

The state file is:
- ✅ Automatically saved after each file operation
- 📤 Uploaded as GitHub Actions artifact (90 day retention)
- 📥 Downloaded at the start of each workflow run
- 🔄 Enables seamless resumption across multiple runs

## FTP Structure Tree Generator

Need to organize files in Google Photos based on their original FTP structure? Use the **Generate FTP Structure Tree** workflow to create a complete manifest of the FTP directory structure.

### Quick Start

1. Go to **Actions** → **Generate FTP Structure Tree**
2. Click **Run workflow**
3. Download the generated manifest files from artifacts:
   - `ftp_structure_manifest.json` - Complete structure in JSON format
   - `ftp_structure_tree.txt` - Human-readable tree visualization

### Use Cases

- **Reorganize Google Photos**: Create albums matching original FTP folders
- **Track ISO Conversions**: Identify which files were originally ISO (converted to MKV)
- **Plan File Moves**: See complete structure before reorganizing uploaded files

📖 **Full documentation**: See [FTP_TREE_GENERATOR.md](FTP_TREE_GENERATOR.md) for detailed usage, examples, and API reference.

## Organize Files in Google Photos

**Note**: As of the latest update, files are **automatically uploaded to their correct folders** during the transfer workflow. The organize workflow is now **optional** and only needed if:
- You want to reorganize files uploaded with older versions (before folder support)
- You need to fix folder organization for files that were uploaded incorrectly

### Automatic Folder Organization (Default)

The main transfer workflow now automatically:
- Detects the folder path from the FTP structure (e.g., "Blockbuster Movies/Avatar (2009)")
- Uploads files directly to albums with those names
- Creates albums or reuses existing ones (no duplicates)
- Tracks album names in the state file for smart continuation

**No additional steps needed!** Files are organized as they upload.

### Manual Reorganization (Optional)

If you need to reorganize files uploaded with older versions, use the **Organize Google Photos Files** workflow.

#### Quick Start

1. **Prerequisites**: Run the workflows above to upload files and generate the manifest
2. Go to **Actions** → **Organize Google Photos Files**
3. Click **Run workflow** with **dry_run: true** to preview
4. Review the logs to verify the organization plan
5. Run again with **dry_run: false** to actually organize files

#### What It Does

- Reads the FTP manifest to understand folder structure
- Maps ISO files to their MKV equivalents (from upload conversion)
- Creates albums in Google Photos matching FTP folder paths
- Moves uploaded files into their corresponding albums

### Example

FTP structure:
```
Blockbuster Movies/
└── Avatar (2009)/
    ├── Avatar.3D.2009.iso → uploaded as Avatar.3D.2009.mkv
    └── Avatar.2009.3D.BluRay.mkv
```

Result in Google Photos:
- Album: "Avatar (2009)"
  - Contains: Avatar.3D.2009.mkv, Avatar.2009.3D.BluRay.mkv

📖 **Full documentation**: See [ORGANIZE_GPHOTOS.md](ORGANIZE_GPHOTOS.md) for detailed usage and troubleshooting.

## Acknowledgments

- [google_photos_mobile_client](https://github.com/xob0t/google_photos_mobile_client) - Google Photos upload library
- [rclone](https://rclone.org/) - FTP file transfer tool
- [maximize-build-space](https://github.com/easimon/maximize-build-space) - GitHub Actions disk space optimizer

