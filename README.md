# FTP to Google Photos Transfer

Automated script to transfer large 3D movie files (~30GB) from FTP servers to Google Photos using GitHub Actions.

## Features

- **Streaming Transfer**: Efficiently streams large files from FTP to Google Photos without storing complete files locally
- **Automatic Discovery**: Recursively scans FTP server for large movie files (.mkv, .iso, etc.)
- **Error Handling**: Robust retry logic and detailed logging
- **GitHub Actions**: Runs automatically on schedule or manually via workflow dispatch
- **Storage Efficient**: Designed to work within GitHub Actions storage constraints

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
user = Lomusire
port = 13017
explicit_tls = true
pass = your_password
no_check_certificate = true

[3DFlickFix2]
type = ftp
host = tamarind.whatbox.ca
user = Lomusire
port = 13017
explicit_tls = true
pass = your_password
no_check_certificate = true

[3DFlickFix3]
type = ftp
host = challenger.whatbox.ca
user = Lomusire
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

1. **Discovery**: Uses `rclone lsf` to recursively list all files on the FTP server
2. **Filtering**: Identifies large movie files matching size and extension criteria
3. **Streaming**: Downloads each file using `rclone copy` with optimized settings
4. **Upload**: Uploads to Google Photos using `gpmc` library
5. **Cleanup**: Deletes local file immediately after successful upload
6. **Logging**: Detailed logs saved to `ftp_to_gphotos.log` and uploaded as artifact

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
- GitHub Actions runners have ~14GB free space
- Script automatically checks available space before processing
- If files are too large, consider running fewer files per workflow

### "Upload failed"
- Check Google Photos auth data is still valid
- Verify internet connection stability
- Check logs for specific error messages

### Slow transfers
- FTP servers can be slow for large files
- Script includes retry logic and progress reporting
- Consider running during off-peak hours

## Limitations

- GitHub Actions runners have limited storage (~14GB free)
- Files larger than available space cannot be processed in single run
- FTP transfer speed depends on server performance
- Google Photos has upload limits (may vary by account)

## License

MIT License

## Acknowledgments

- [google_photos_mobile_client](https://github.com/xob0t/google_photos_mobile_client) - Google Photos upload library
- [rclone](https://rclone.org/) - FTP file transfer tool

