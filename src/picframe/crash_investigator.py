#!/usr/bin/env python3
"""
Crash Investigator for Picframe

This module provides comprehensive crash investigation capabilities including:
- Stack trace capture
- Memory usage monitoring
- System resource monitoring
- Crash dump generation
- Automatic recovery mechanisms
"""

import os
import sys
import time
import signal
import logging
import traceback
import threading
import psutil
import gc
from datetime import datetime
from typing import Optional, Dict, Any
import json

class CrashInvestigator:
    """Comprehensive crash investigation and monitoring for picframe."""
    
    def __init__(self, log_file: Optional[str] = None):
        self.log_file = log_file or "picframe_crash.log"
        self.crash_count = 0
        self.last_crash_time = None
        self.monitoring = False
        self.monitor_thread = None
        
        # Set up logging
        self.logger = logging.getLogger(__name__)
        self._setup_logging()
        
        # Install signal handlers
        self._install_signal_handlers()
        
        # Start monitoring
        self.start_monitoring()
    
    def _setup_logging(self):
        """Set up crash-specific logging."""
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # File handler for crash logs
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.setLevel(logging.DEBUG)
    
    def _install_signal_handlers(self):
        """Install signal handlers for crash detection."""
        signal.signal(signal.SIGSEGV, self._segfault_handler)
        signal.signal(signal.SIGABRT, self._abort_handler)
        signal.signal(signal.SIGFPE, self._fpe_handler)
        signal.signal(signal.SIGILL, self._ill_handler)
        signal.signal(signal.SIGBUS, self._bus_handler)
        
        # Graceful shutdown signals
        signal.signal(signal.SIGTERM, self._graceful_shutdown)
        signal.signal(signal.SIGINT, self._graceful_shutdown)
    
    def _segfault_handler(self, signum, frame):
        """Handle segmentation faults."""
        self.logger.critical("SEGFAULT DETECTED! Signal: %s", signum)
        self._capture_crash_info("SEGFAULT", signum, frame)
        sys.exit(1)
    
    def _abort_handler(self, signum, frame):
        """Handle abort signals."""
        self.logger.critical("ABORT SIGNAL DETECTED! Signal: %s", signum)
        self._capture_crash_info("ABORT", signum, frame)
        sys.exit(1)
    
    def _fpe_handler(self, signum, frame):
        """Handle floating point exceptions."""
        self.logger.critical("FLOATING POINT EXCEPTION! Signal: %s", signum)
        self._capture_crash_info("FPE", signum, frame)
        sys.exit(1)
    
    def _ill_handler(self, signum, frame):
        """Handle illegal instruction exceptions."""
        self.logger.critical("ILLEGAL INSTRUCTION! Signal: %s", signum)
        self._capture_crash_info("ILL", signum, frame)
        sys.exit(1)
    
    def _bus_handler(self, signum, frame):
        """Handle bus errors."""
        self.logger.critical("BUS ERROR! Signal: %s", signum)
        self._capture_crash_info("BUS", signum, frame)
        sys.exit(1)
    
    def _graceful_shutdown(self, signum, frame):
        """Handle graceful shutdown signals."""
        self.logger.info("Graceful shutdown signal received: %s", signum)
        self.stop_monitoring()
        sys.exit(0)
    
    def _capture_crash_info(self, crash_type: str, signum: int, frame):
        """Capture comprehensive crash information."""
        self.crash_count += 1
        self.last_crash_time = datetime.now()
        
        crash_info = {
            "crash_type": crash_type,
            "signal_number": signum,
            "timestamp": self.last_crash_time.isoformat(),
            "crash_count": self.crash_count,
            "process_info": self._get_process_info(),
            "memory_info": self._get_memory_info(),
            "system_info": self._get_system_info(),
            "stack_trace": self._get_stack_trace(),
            "thread_info": self._get_thread_info(),
            "open_files": self._get_open_files(),
            "environment": dict(os.environ)
        }
        
        # Log crash info
        self.logger.critical("=== CRASH REPORT ===")
        self.logger.critical("Crash Type: %s", crash_type)
        self.logger.critical("Signal: %s", signum)
        self.logger.critical("Timestamp: %s", self.last_crash_time)
        self.logger.critical("Crash Count: %d", self.crash_count)
        
        # Save detailed crash dump
        self._save_crash_dump(crash_info)
        
        # Try to capture additional system state
        self._capture_system_state()
    
    def _get_process_info(self) -> Dict[str, Any]:
        """Get current process information."""
        try:
            process = psutil.Process()
            return {
                "pid": process.pid,
                "ppid": process.ppid(),
                "name": process.name(),
                "cmdline": process.cmdline(),
                "create_time": process.create_time(),
                "cpu_percent": process.cpu_percent(),
                "memory_percent": process.memory_percent(),
                "num_threads": process.num_threads(),
                "status": process.status()
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_memory_info(self) -> Dict[str, Any]:
        """Get memory usage information."""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            return {
                "rss": memory_info.rss,
                "vms": memory_info.vms,
                "percent": process.memory_percent(),
                "system_total": psutil.virtual_memory().total,
                "system_available": psutil.virtual_memory().available,
                "system_percent": psutil.virtual_memory().percent
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_system_info(self) -> Dict[str, Any]:
        """Get system information."""
        try:
            return {
                "cpu_count": psutil.cpu_count(),
                "cpu_percent": psutil.cpu_percent(interval=1),
                "load_average": psutil.getloadavg(),
                "disk_usage": psutil.disk_usage('/'),
                "uptime": time.time() - psutil.boot_time()
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_stack_trace(self) -> str:
        """Get current stack trace."""
        try:
            return ''.join(traceback.format_stack())
        except Exception as e:
            return f"Error getting stack trace: {e}"
    
    def _get_thread_info(self) -> Dict[str, Any]:
        """Get thread information."""
        try:
            process = psutil.Process()
            threads = process.threads()
            return {
                "thread_count": len(threads),
                "threads": [
                    {
                        "id": t.id,
                        "user_time": t.user_time,
                        "system_time": t.system_time
                    } for t in threads
                ]
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_open_files(self) -> Dict[str, Any]:
        """Get information about open files."""
        try:
            process = psutil.Process()
            open_files = process.open_files()
            return {
                "file_count": len(open_files),
                "files": [f.path for f in open_files[:10]]  # Limit to first 10
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _save_crash_dump(self, crash_info: Dict[str, Any]):
        """Save crash dump to file."""
        try:
            dump_file = f"picframe_crash_{self.last_crash_time.strftime('%Y%m%d_%H%M%S')}.json"
            with open(dump_file, 'w') as f:
                json.dump(crash_info, f, indent=2, default=str)
            self.logger.info("Crash dump saved to: %s", dump_file)
        except Exception as e:
            self.logger.error("Failed to save crash dump: %s", e)
    
    def _capture_system_state(self):
        """Capture additional system state information."""
        try:
            # Force garbage collection
            gc.collect()
            
            # Log memory after GC
            process = psutil.Process()
            memory_after = process.memory_info()
            self.logger.info("Memory after GC - RSS: %s, VMS: %s", 
                           memory_after.rss, memory_after.vms)
            
        except Exception as e:
            self.logger.error("Error capturing system state: %s", e)
    
    def start_monitoring(self):
        """Start background monitoring."""
        if self.monitoring:
            return
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.logger.info("Crash monitoring started")
    
    def stop_monitoring(self):
        """Stop background monitoring."""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1)
        self.logger.info("Crash monitoring stopped")
    
    def _monitor_loop(self):
        """Background monitoring loop."""
        while self.monitoring:
            try:
                # Monitor memory usage
                process = psutil.Process()
                memory_info = process.memory_info()
                memory_percent = process.memory_percent()
                
                # Log if memory usage is high
                if memory_percent > 80:
                    self.logger.warning("High memory usage: %.1f%% (RSS: %s, VMS: %s)", 
                                      memory_percent, memory_info.rss, memory_info.vms)
                
                # Monitor CPU usage
                cpu_percent = process.cpu_percent()
                if cpu_percent > 90:
                    self.logger.warning("High CPU usage: %.1f%%", cpu_percent)
                
                # Check for zombie processes
                zombie_count = len([p for p in psutil.process_iter() if p.status() == 'zombie'])
                if zombie_count > 0:
                    self.logger.warning("Zombie processes detected: %d", zombie_count)
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                self.logger.error("Error in monitoring loop: %s", e)
                time.sleep(60)  # Wait longer on error
    
    def get_crash_summary(self) -> Dict[str, Any]:
        """Get summary of crash information."""
        return {
            "crash_count": self.crash_count,
            "last_crash_time": self.last_crash_time.isoformat() if self.last_crash_time else None,
            "monitoring_active": self.monitoring,
            "log_file": self.log_file
        }
    
    def cleanup_old_crash_dumps(self, max_age_days: int = 7):
        """Clean up old crash dump files."""
        try:
            current_time = time.time()
            max_age_seconds = max_age_days * 24 * 3600
            
            for filename in os.listdir('.'):
                if filename.startswith('picframe_crash_') and filename.endswith('.json'):
                    filepath = os.path.join('.', filename)
                    file_age = current_time - os.path.getmtime(filepath)
                    
                    if file_age > max_age_seconds:
                        os.remove(filepath)
                        self.logger.info("Removed old crash dump: %s", filename)
                        
        except Exception as e:
            self.logger.error("Error cleaning up crash dumps: %s", e)

# Global instance
_crash_investigator = None

def get_crash_investigator() -> CrashInvestigator:
    """Get the global crash investigator instance."""
    global _crash_investigator
    if _crash_investigator is None:
        _crash_investigator = CrashInvestigator()
    return _crash_investigator

def install_crash_handlers():
    """Install crash handlers for the current process."""
    get_crash_investigator()
    return _crash_investigator

if __name__ == "__main__":
    # Test the crash investigator
    investigator = CrashInvestigator()
    print("Crash investigator initialized. Press Ctrl+C to exit.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        investigator.stop_monitoring()
        print("Exiting...")


