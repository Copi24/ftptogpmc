# Quick Start Guide

## 🚀 Automated Setup (Recommended)

### Using GitHub CLI (gh)

**Windows:**
```cmd
setup.bat
```

**Linux/Mac:**
```bash
chmod +x setup.sh
./setup.sh
```

This will:
1. Create a GitHub repository
2. Push all files
3. Show you where to add secrets

### Manual Setup

1. **Create GitHub Repository**
   ```bash
   git add .
   git commit -m "Initial commit"
   gh repo create ftptogpmc --public --source=. --push
   ```

2. **Add Secrets**
   - Go to: `https://github.com/YOUR_USERNAME/ftptogpmc/settings/secrets/actions`
   - Add `GP_AUTH_DATA`: Your Google Photos auth data
   - Add `RCLONE_CONFIG`: Your rclone configuration (full content)

3. **Run Workflow**
   - Go to Actions tab
   - Click "FTP to Google Photos Transfer"
   - Click "Run workflow"

## 📋 What This Does

1. **Scans FTP Server** - Finds all large movie files (20-50GB, .mkv/.iso)
2. **Downloads** - Streams files from FTP using rclone (optimized for slow connections)
3. **Uploads** - Uploads to Google Photos using gpmc (unlimited storage)
4. **Cleans Up** - Deletes local files immediately after upload to save space
5. **Logs Everything** - Detailed logs saved and uploaded as artifacts

## ⚙️ Configuration

Edit `ftp_to_gphotos.py` to customize:

- `MIN_FILE_SIZE`: Minimum file size (default: 20GB)
- `MAX_FILE_SIZE`: Maximum file size (default: 50GB)  
- `SUPPORTED_EXTENSIONS`: File types to process
- `RCLONE_REMOTE`: Remote name in rclone config (default: `3DFF`)

## 🔍 Monitoring

- **GitHub Actions**: View runs in Actions tab
- **Logs**: Download from Actions artifacts
- **Local**: Check `ftp_to_gphotos.log`

## ⚠️ Important Notes

- GitHub Actions has ~14GB free disk space
- Files are processed one at a time
- Each file is deleted immediately after successful upload
- Workflow timeout: 6 hours (can process multiple files)
- FTP transfers can be slow - this is normal

## 🆘 Troubleshooting

**Workflow fails immediately:**
- Check secrets are set correctly
- Verify rclone config format

**Connection timeout:**
- Normal for slow FTP servers
- Script has retry logic built-in

**Disk space error:**
- Files are cleaned up after upload
- If issue persists, reduce `MAX_FILE_SIZE` or process fewer files

**Need help?**
- Check logs in Actions artifacts
- Review `ftp_to_gphotos.log` for detailed errors

