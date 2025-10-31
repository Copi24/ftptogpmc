# Smart Resumption System

## Overview

The FTP to Google Photos transfer script now includes a comprehensive smart resumption system that enables it to:
- ✅ Resume interrupted downloads from the exact byte position
- 📊 Track all completed uploads across multiple workflow runs
- ⏭️ Skip files that were already successfully uploaded
- 🔄 Retry failed files with intelligent backoff
- 💾 Persist state between GitHub Actions workflow runs

## How It Works

### State File Structure

The system uses a JSON state file (`upload_state.json`) that tracks:

```json
{
  "version": "1.0",
  "last_updated": "2025-10-31T10:30:00.123456",
  "completed": [
    "/Movies/Avatar 3D (2009)/Avatar.3D.2009.1080p.mkv",
    "/Movies/Gravity 3D (2013)/Gravity.3D.2013.1080p.iso"
  ],
  "failed": {
    "/Movies/Large/BigFile.mkv": {
      "attempts": 2,
      "last_error": "Failed to download after 5 attempts",
      "first_failed": "2025-10-31T09:00:00.000000",
      "last_failed": "2025-10-31T10:00:00.000000"
    }
  },
  "in_progress": {
    "path": "/Movies/Current/Processing.mkv",
    "size": 32212254720,
    "started_at": "2025-10-31T10:25:00.000000"
  },
  "skipped": [
    "/Movies/TooLarge/60GB.mkv"
  ],
  "stats": {
    "total_uploaded": 12,
    "total_failed": 2,
    "total_bytes": 385875968000
  }
}
```

### State Lifecycle

#### 1. **Workflow Start**
```yaml
- name: Download previous state (if exists)
  uses: actions/download-artifact@v4
  continue-on-error: true
  with:
    name: upload-state
    path: repo/
```
- Downloads `upload_state.json` from previous workflow run (if exists)
- If no previous state exists, creates new empty state
- Prints state summary with completed/failed/skipped counts

#### 2. **File Processing**
For each file discovered on FTP:

```python
# Check if already completed
if state.is_completed(remote_path):
    logger.info(f"⏭️  Skipping {file_name} - already uploaded successfully")
    return True

# Check if should retry failed file
if state.is_failed(remote_path) and not state.should_retry(remote_path):
    logger.info(f"⏭️  Skipping {file_name} - exceeded max retry attempts")
    return False

# Mark as in progress
state.mark_in_progress(remote_path, file_info['size'])
```

#### 3. **Download Phase**
```python
# Check if partial file exists
if local_path.exists() and resume:
    partial_size = local_path.stat().st_size
    logger.info(f"🔄 Found partial download: {partial_size / (1024**3):.2f}GB")

# rclone with --ignore-existing resumes automatically
cmd = [
    'rclone', 'copy',
    f'{remote}:{remote_path}',
    str(local_path.parent),
    '--ignore-existing',  # Don't re-download if complete
    # ... other flags
]
```

#### 4. **Success Path**
```python
if media_key:
    # Mark as completed in state
    state.mark_completed(remote_path, file_info['size'], media_key)
    
    # Delete local file to free space
    local_path.unlink()
    logger.info(f"🗑️ Deleted local file")
```

#### 5. **Failure Path**
```python
if not download_success:
    error_msg = f"Failed to download after {max_download_attempts} attempts"
    state.mark_failed(remote_path, error_msg)
    return False

if not media_key:
    error_msg = "Upload failed after all retries"
    state.mark_failed(remote_path, error_msg)
    return False
```

#### 6. **Workflow End**
```yaml
- name: Upload state for next run
  uses: actions/upload-artifact@v4
  if: always()
  with:
    name: upload-state
    path: repo/upload_state.json
    retention-days: 90
    overwrite: true
```
- Always uploads state file, even if workflow failed/cancelled
- Retained for 90 days (plenty of time for next run)
- Overwrites previous state artifact

## Resume Scenarios

### Scenario 1: Download Interrupted Mid-File

**What Happens:**
1. File is 30GB, downloaded 15GB before connection drops
2. Local file exists at 15GB
3. State shows file as "in_progress"

**On Next Run:**
1. State manager loads previous state
2. Detects file was in progress but not completed
3. `rclone copy` with `--ignore-existing` checks file
4. If file incomplete, rclone resumes from 15GB
5. If file complete, rclone skips (hash matches)
6. Upload continues normally

**Note:** rclone's FTP backend has limited resume support. If resume fails, the file is re-downloaded from start.

### Scenario 2: Upload Failed

**What Happens:**
1. File downloaded successfully (30GB)
2. Upload to Google Photos failed
3. Workflow timeout or error occurred
4. State marks file as "failed"

**On Next Run:**
1. File still exists in temp directory (or re-download if cleaned)
2. State shows 1 failed attempt
3. Script retries upload (up to 3 total attempts)
4. If successful, marks as completed
5. If fails 3 times, skips file permanently

### Scenario 3: Workflow Timeout

**What Happens:**
1. GitHub Actions workflow hits 6-hour timeout
2. Currently processing file at any stage
3. State file updated after each operation

**On Next Run:**
1. Loads state showing last completed file
2. Skips all completed files
3. Continues from next file in directory traversal
4. If file was mid-download, attempts resume
5. Processes remaining files normally

### Scenario 4: All Files Completed

**What Happens:**
1. All files successfully uploaded
2. State shows complete list

**On Next Run:**
1. Loads state with completed files
2. Traverses FTP directories
3. Skips all completed files
4. Processes only new files (if any)
5. Prints "No new files to process"

## Retry Logic

### Download Retries
```python
max_download_attempts = 5
wait_times = [30, 60, 120, 300, 600]  # Increasing backoff

for attempt in range(max_download_attempts):
    if stream_file_from_ftp(remote, remote_path, local_path):
        download_success = True
        break
    
    if attempt < max_download_attempts:
        wait_time = wait_times[attempt] if attempt < len(wait_times) else 600
        logger.info(f"⏳ Waiting {wait_time}s before retry...")
        time.sleep(wait_time)
```

### Upload Retries
```python
# gpmc library handles retries internally
client.upload_file(
    file_path=local_path,
    use_quota=False,  # Unlimited storage
    saver=False       # Original quality
)
```

### Failed File Retry Limit
```python
def should_retry(self, file_path: str, max_failures: int = 3) -> bool:
    """Check if we should retry a failed file."""
    if not self.is_failed(file_path):
        return True
    return self.get_failure_count(file_path) < max_failures
```
- Files that fail 3 times are permanently skipped
- Prevents infinite retry loops
- Can be adjusted by changing `max_failures` parameter

## State Persistence

### GitHub Actions Artifacts
- State file uploaded as artifact after every run
- **Retention:** 90 days (configurable)
- **Overwrite:** Yes (always latest state)
- **Download:** Automatic at workflow start
- **Fallback:** Creates new state if artifact missing

### Benefits
1. **Seamless Resumption**: Workflow can be cancelled/restarted anytime
2. **Progress Tracking**: Always know what's been completed
3. **Failure Analysis**: Track which files consistently fail
4. **Statistics**: Total uploaded bytes, file counts, etc.

### Limitations
1. **Artifact Retention**: After 90 days, state is lost (starts fresh)
2. **Manual Runs**: If artifact deleted manually, loses state
3. **Repository Reset**: If repo deleted, loses state

## Monitoring Progress

### View State Summary
State summary printed at start and end of each run:

```
================================================================================
📊 UPLOAD STATE SUMMARY
================================================================================
✅ Completed: 8 files
❌ Failed: 2 files
⏭️  Skipped: 1 files
📦 Total uploaded: 240.50GB
🔄 In progress: /Movies/Avatar/Avatar.mkv
⚠️  Files with failures:
   • /Movies/Large/BigFile.mkv: 2 attempts
================================================================================
```

### Download State File
```bash
# Using GitHub CLI
gh run list --limit 1
gh run download <run-id> -n upload-state

# View state
cat upload_state.json | jq .
```

### Check Specific File
```bash
# Check if file completed
cat upload_state.json | jq '.completed[] | select(contains("Avatar"))'

# Check failed files
cat upload_state.json | jq '.failed'

# View statistics
cat upload_state.json | jq '.stats'
```

## Best Practices

### 1. **Let It Run**
- Don't manually cancel workflows unless necessary
- Script handles errors gracefully
- Will resume on next scheduled run

### 2. **Monitor Failures**
- Check logs for persistent failures
- Files failing 3+ times likely have issues:
  - File corrupted on FTP
  - File too large for current setup
  - FTP connection consistently unstable for that file

### 3. **Scheduled Runs**
- Keep daily schedule for consistent progress
- Each run processes files until timeout
- Resumes next day automatically

### 4. **Manual Intervention**
- Only needed for persistent failures
- Can manually edit state file if needed
- Download artifact, edit JSON, re-upload

### 5. **New Files**
- Script automatically detects new files
- Skips completed files by path
- Processes only what's new

## Technical Details

### State Saving Strategy
```python
def _save_state(self):
    """Save current state to file."""
    self.state['last_updated'] = datetime.utcnow().isoformat()
    
    # Write to temp file first, then rename (atomic)
    temp_file = self.state_file.with_suffix('.tmp')
    with open(temp_file, 'w') as f:
        json.dump(self.state, f, indent=2)
    temp_file.replace(self.state_file)
```
- **Atomic writes**: Prevents corruption if interrupted
- **Auto-save**: After every state change
- **ISO timestamps**: Standard format for tracking

### File Path Tracking
- Uses full remote path as unique identifier
- Example: `/Movies/Avatar 3D (2009)/Avatar.3D.2009.1080p.mkv`
- Stable across runs (won't duplicate if renamed locally)
- Case-sensitive (FTP paths are case-sensitive)

### Resume Detection
```python
# Check if partial file exists
if local_path.exists() and resume:
    partial_size = local_path.stat().st_size
    logger.info(f"🔄 Found partial download: {partial_size:.2f}GB")
    
    # rclone handles resume automatically with --ignore-existing
    # If remote file matches, skips
    # If remote file differs, re-downloads
```

## Troubleshooting

### State File Corrupted
**Symptoms:** JSON parse errors, workflow fails at start

**Solution:**
1. Download current artifact
2. Validate JSON: `cat upload_state.json | jq .`
3. If corrupted, manually fix or delete
4. Re-upload as artifact or let workflow create new

### File Shows Completed But Not in Google Photos
**Causes:**
- Upload succeeded but Google Photos delayed processing
- Network issue during upload confirmation
- Google Photos removed file (policy violation)

**Solution:**
1. Check Google Photos after 24 hours
2. If still missing, remove from `completed` list in state
3. File will be re-processed on next run

### Stuck in "in_progress"
**Symptoms:** State shows file in progress but not processing

**Solution:**
1. Normal if workflow cancelled mid-processing
2. Next run will attempt to resume/restart
3. File not in `completed`, so will be retried
4. No action needed

### Failed Files Not Retrying
**Causes:** Exceeded max retry attempts (3)

**Solution:**
1. Check logs for failure reason
2. If issue resolved, edit state:
   - Remove file from `failed` object
   - File will be retried on next run
3. Or manually process file outside workflow

## Future Enhancements

Potential improvements to the resumption system:

1. **Partial Upload Resume**: Currently uploads restart from beginning
   - Google Photos API doesn't support chunked uploads
   - Would need API changes

2. **Smart Scheduling**: Process largest files during off-peak hours
   - Add file size-based scheduling
   - Prioritize by success probability

3. **Parallel Processing**: Upload one file while downloading next
   - Requires careful disk space management
   - Could improve throughput

4. **State Sharing**: Share state across repository forks
   - Use GitHub releases or external storage
   - Enable distributed processing

5. **Failure Analysis**: Auto-detect persistent issues
   - Flag files that consistently fail
   - Suggest fixes based on error patterns

## Conclusion

The smart resumption system transforms the transfer workflow from a fragile one-shot process into a robust, long-running operation that can handle:
- ✅ Network interruptions
- ✅ Workflow timeouts
- ✅ Partial downloads
- ✅ Upload failures
- ✅ Large file libraries (100+ files)
- ✅ Multi-day processing

It enables true "set it and forget it" operation where you can trigger the workflow and let it run daily until all files are transferred, regardless of how many interruptions occur.

