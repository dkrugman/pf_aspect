# Import Throttling Configuration

This document explains how to configure import throttling to prevent database overload when importing photos from cloud services.

## Problem

The original `import_photos.py` started background downloads for all files at once, which could overwhelm the database with:
- Too many concurrent database connections
- Too many simultaneous database write operations
- Memory and resource exhaustion

## Solution

The import system now includes comprehensive throttling and database connection management that:
1. **Limits concurrent downloads** - prevents too many simultaneous file downloads
2. **Limits concurrent database operations** - prevents database connection overload
3. **Processes items in batches** - provides controlled, manageable processing
4. **Adds delays between batches** - gives the system time to recover
5. **Uses separate database connections** - prevents database locking issues
6. **Implements connection pooling** - manages database resources efficiently

## Configuration Options

Add these settings to your `configuration.yaml` file under the `aspect:` section:

```yaml
aspect:
  # ... existing settings ...
  
  # Import throttling configuration to prevent database overload
  max_concurrent_downloads: 5             # maximum concurrent file downloads (default: 5)
  max_concurrent_db_operations: 3         # maximum concurrent database operations (default: 3)
  download_batch_size: 10                 # number of items to process in each batch (default: 10)
```

### Parameter Descriptions

- **`max_concurrent_downloads`**: Maximum number of files that can be downloaded simultaneously
  - **Default**: 5
  - **Range**: 1-20 (recommended: 3-10)
  - **Lower values**: More conservative, less resource usage, slower overall
  - **Higher values**: Faster overall, but more resource intensive

- **`max_concurrent_db_operations`**: Maximum number of database operations that can run simultaneously
  - **Default**: 3
  - **Range**: 1-10 (recommended: 1-5)
  - **Lower values**: Prevents database locking issues
  - **Higher values**: Faster database operations, but risk of conflicts

- **`download_batch_size`**: Number of items to process in each batch before moving to the next
  - **Default**: 10
  - **Range**: 5-50 (recommended: 10-25)
  - **Lower values**: More frequent progress updates, better memory management
  - **Higher values**: Fewer batch transitions, potentially faster overall

## Recommended Configurations

### For Low-Power Devices (Raspberry Pi Zero, etc.)
```yaml
max_concurrent_downloads: 3
max_concurrent_db_operations: 1
download_batch_size: 5
```

### For Standard Devices (Raspberry Pi 3/4, etc.)
```yaml
max_concurrent_downloads: 5
max_concurrent_db_operations: 3
download_batch_size: 10
```

### For High-Power Devices (Desktop, etc.)
```yaml
max_concurrent_downloads: 10
max_concurrent_db_operations: 5
download_batch_size: 25
```

## Testing Throttling

You can test the throttling functionality without running a full import:

```bash
cd /path/to/picframe
source venv_pf_aspect/bin/activate
python test_throttling.py
```

This will simulate the throttling behavior with different configurations to help you find the optimal settings for your system.

## Testing Database Connections

To verify that the database locking issues are resolved:

```bash
cd /path/to/picframe
source venv_pf_aspect/bin/activate
python test_db_connections.py
```

This script tests concurrent database operations to ensure no locking occurs with the new connection management system.

## Monitoring and Logging

The system now provides detailed logging about throttling:

```
2024-01-01 12:00:00 - INFO - Import throttling: max_downloads=5, max_db_ops=3, batch_size=10
2024-01-01 12:00:01 - INFO - Starting background download for 150 items from nixplay with throttling (max_downloads=5, max_db_ops=3, batch_size=10)
2024-01-01 12:00:01 - INFO - Processing batch 1/15 (10 items)
2024-01-01 12:00:05 - INFO - Batch 1/15 complete: 10 successful, 0 failed. Total progress: 10/150
2024-01-01 12:00:06 - INFO - Processing batch 2/15 (10 items)
...
```

## Troubleshooting

### Database Still Getting Overwhelmed
- Reduce `max_concurrent_db_operations` to 1
- Reduce `max_concurrent_downloads` to 2-3
- Reduce `download_batch_size` to 5

### Database Locking Issues
- **Most Common Cause**: Multiple operations using the same database connection
- **Solution**: The system now automatically creates separate connections for each operation
- **If still occurring**: Reduce `max_concurrent_db_operations` to 1
- **Additional**: Check if other processes are accessing the same database file

### Imports Taking Too Long
- Increase `max_concurrent_downloads` (but keep below 10)
- Increase `max_concurrent_db_operations` (but keep below 5)
- Increase `download_batch_size` (but keep below 30)

### Memory Issues
- Reduce `download_batch_size` to 5-10
- Reduce `max_concurrent_downloads` to 2-3

## How It Works

1. **Semaphore Control**: Uses `asyncio.Semaphore` to limit concurrent operations
2. **Batch Processing**: Processes items in configurable batches instead of all at once
3. **Resource Management**: Each batch completes before starting the next
4. **Progress Tracking**: Provides detailed logging of batch progress
5. **Graceful Degradation**: System continues working even if some operations fail
6. **Thread-Safe Database Operations**: Each database operation uses a separate connection
7. **Connection Pooling**: Efficiently manages database connections to prevent locking
8. **Delayed Operations**: Adds small delays between operations to reduce contention

## Performance Impact

- **Conservative settings** (3 downloads, 1 DB op, batch size 5): ~20-30% slower but very stable
- **Default settings** (5 downloads, 3 DB ops, batch size 10): ~10-15% slower but good balance
- **Aggressive settings** (10 downloads, 5 DB ops, batch size 25): ~5% slower but resource intensive

The throttling ensures your system remains responsive and stable during large imports, which is especially important for embedded devices like Raspberry Pi.

## Technical Improvements Made

### Database Connection Management
- **Separate Connections**: Each database operation now uses its own connection
- **Connection Pooling**: Efficiently manages database resources
- **Thread Safety**: All operations are thread-safe and won't cause locking
- **Automatic Cleanup**: Connections are automatically closed after use

### Enhanced Throttling
- **Conservative Defaults**: More conservative default values to prevent issues
- **Delayed Operations**: Small delays between operations to reduce contention
- **Batch Processing**: Items processed in small, manageable batches
- **Progress Monitoring**: Detailed logging of batch progress and completion

### Configuration Flexibility
- **Easy Tuning**: Simple configuration parameters for different system capabilities
- **Preset Recommendations**: Suggested configurations for different device types
- **Runtime Logging**: Clear visibility into throttling behavior during operation
