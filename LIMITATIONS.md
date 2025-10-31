# Fundamental Limitations

## Why True Streaming Upload Isn't Possible

### The Problem
You want to stream directly: **FTP → Google Photos** without storing the full file.

### Why It Can't Work

**Google Photos Mobile API Requirements:**
1. ✅ **File hash** - Must be calculated from complete file BEFORE upload
2. ✅ **Total size** - Required in upload request headers
3. ✅ **Random access** - API may request specific byte ranges
4. ✅ **Retry support** - If chunk 50/100 fails, needs to re-send that chunk
5. ✅ **Metadata** - Video info extracted before upload starts

**What gpmc actually does:**
```python
def upload(file_path):
    # PHASE 1: PRE-UPLOAD (needs full file)
    file_size = os.path.getsize(file_path)  # ← Need full file
    file_hash = calculate_sha256(file_path)  # ← Need full file
    metadata = extract_video_info(file_path)  # ← Need full file
    
    # PHASE 2: UPLOAD REQUEST
    response = api.initiate_upload(size=file_size, hash=file_hash)
    upload_url = response['upload_url']
    
    # PHASE 3: STREAMING UPLOAD (this part streams!)
    with open(file_path, 'rb') as f:
        for chunk in read_chunks(f):
            upload_chunk(upload_url, chunk)
            if failed:
                f.seek(previous_position)  # ← Need random access
                retry_chunk()
    
    # PHASE 4: VERIFICATION
    api.verify_upload(file_hash)  # ← Check against pre-calculated hash
```

### Technical Alternatives (None Work)

**❌ Named Pipe (FIFO)**
```bash
mkfifo /tmp/video.mkv
rclone cat ftp:file.mkv > /tmp/video.mkv &
python upload.py /tmp/video.mkv
```
**Why it fails:**
- Can't calculate hash (need full file first)
- Can't seek() on pipe for retries
- Can't know total size upfront

**❌ Split into smaller chunks**
```
Split 30GB into 10x 3GB chunks
Upload each chunk separately
```
**Why it fails:**
- Google Photos needs complete video file
- Can't play split video files
- Metadata extraction needs complete file

**❌ On-the-fly hash calculation**
```
Calculate hash while downloading
```
**Why it fails:**
- Still need full file on disk
- Hash needed BEFORE upload starts
- Doesn't reduce storage requirement

## The Real Solution

### For GitHub Actions (Current Approach) ✅
```
Strategy: Focus on files that FIT in available space
- Limit: 12GB max file size
- Sort: Smallest files first
- Retry: 5 attempts per file
- Result: Successfully processes ~1-12GB files
```

### For Unlimited File Sizes 🚀
**Run locally or on VPS:**
```bash
# On your PC or cloud server:
git clone https://github.com/Copi24/ftptogpmc
cd ftptogpmc
pip install -r requirements.txt
export GP_AUTH_DATA="your_auth_data"
python3 ftp_to_gphotos.py
```

**Benefits:**
- ✅ No storage limits
- ✅ Handle 30GB+ files
- ✅ More stable internet
- ✅ Can pause/resume
- ✅ Faster processing

### Cloud Options
**DigitalOcean Droplet ($4/month):**
- 25GB storage
- Can handle most files
- Leave running 24/7

**Hetzner Cloud ($3.5/month):**
- 20GB storage  
- Great for European servers
- Fast transfer speeds

**AWS EC2 Free Tier:**
- 30GB storage
- Free for 12 months
- Pay only for bandwidth

## Summary

| Method | Storage Needed | Max File Size | Cost |
|--------|---------------|---------------|------|
| GitHub Actions | 14GB | 12GB | Free |
| Local PC | Unlimited | Unlimited | Free |
| VPS ($4/mo) | 25GB+ | 25GB+ | $4/mo |

**Bottom line:** The API simply requires the complete file. There's no workaround for this fundamental requirement. Focus on files that fit, or run locally for unlimited sizes.

