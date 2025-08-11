#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AsyncTimerManager

Provides an async timer manager to schedule and persist periodic tasks using SQLite.
Ensures tasks run at specified intervals, even across restarts.

Usage:
    from async_timer import init_timer
    timer = init_timer(model)
    timer.register(my_async_func, 3600, "hourly_task")
    timer.start()
"""

import asyncio
import time
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Awaitable, Optional

class AsyncTimerManager:
    def __init__(self, model):
        self.__logger = logging.getLogger(__name__)
        self.__model = model
        self.__db_file = str(Path(self.__model.get_model_config()['db_file']).expanduser().resolve())
        self.__logger.debug(f"Using database file: {self.__db_file}")
        self._tasks = []
        self._running = False
        self._task = None  # Store the background task
        self._db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=5.0)
        # Use WAL mode for better concurrency, DELETE for compatibility with DB Browser for SQLite
        self._db.execute("PRAGMA journal_mode=DELETE")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        with self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS timer_state (
                    name TEXT PRIMARY KEY,
                    last_run REAL
                )
            """)
        
    def register(self, callback: Callable[[], Awaitable[None]], interval: float, name: str) -> None:
        """Register an async function with interval (sec) and a unique name."""
        if not asyncio.iscoroutinefunction(callback):
            raise TypeError("Callback must be an async function")

        last_run = self._load_last_run(name)
        if last_run is None:
            last_run = time.time() - interval  # Run immediately

        task = {
            "name": name,
            "callback": callback,
            "interval": interval,
            "last_run": last_run
        }
        self._tasks.append(task)

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run())

    async def astop(self):
        """Async stop that waits for cleanup."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def stop(self):
        """Sync wrapper if you can’t await."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        # Don't close the database here - let the _run method handle it
        # self._db.close()  # Removed this line

    async def _run(self):
        try:
            while self._running:
                now = time.time()
                coros = []
                for task in self._tasks:
                    due = (now - task["last_run"] >= task["interval"])
                    if due:
                        self.__logger.info(f"Task '{task['name']}' is due (interval: {task['interval']}s, last_run: {task['last_run']}, now: {now})")
                        task["last_run"] = now
                        self._save_last_run(task["name"], now)
                        coros.append(self._run_task(task))
                    else:
                        time_until = task["interval"] - (now - task["last_run"])
                        #self.__logger.debug(f"Task '{task['name']}' not due yet (time_until: {time_until:.1f}s)")

                if coros:
                    self.__logger.info(f"Executing {len(coros)} timer tasks")
                    await asyncio.gather(*coros)

                await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.__logger.info("TimerManager cancelled.")
            try:
                self._save_all_states()
            except Exception as e:
                self.__logger.warning(f"Error saving timer states during shutdown: {e}")
            raise
        finally:
            try:
                self._db.close()
            except Exception as e:
                self.__logger.warning(f"Error closing database: {e}")

    async def _run_task(self, task):
        try:
            await task["callback"]()
        except Exception as e:
            self.__logger.exception(f"Error in callback {task['name']}: {e}")

    def get_time_until_next(self, name: str) -> float:
        now = time.time()
        for task in self._tasks:
            if task["name"] == name:
                elapsed = now - task["last_run"]
                return max(0.0, task["interval"] - elapsed)
        raise KeyError(f"No task registered with name '{name}'")

    def _load_last_run(self, name: str) -> Optional[float]: 
        cur = self._db.cursor()
        try:
            cur.execute("SELECT last_run FROM timer_state WHERE name = ?", (name,))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            self.__logger.warning(f"Error loading timer state for {name}: {e}")
            return None 
        finally:
            cur.close()
        
    def _save_last_run(self, name: str, timestamp: float) -> None:
        try:
            with self._db:  # auto-commit
                self._db.execute("""
                    INSERT INTO timer_state (name, last_run)
                    VALUES (?, ?)
                    ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run
                """, (name, timestamp))
        except Exception as e:
            self.__logger.warning(f"Error saving timer state for {name}: {e}")

    def _save_all_states(self) -> None:
        try:
            with self._db:
                for task in self._tasks:
                    self._db.execute("""
                        INSERT INTO timer_state (name, last_run)
                        VALUES (?, ?)
                        ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run
                    """, (task["name"], task["last_run"]))
        except Exception as e:
            self.__logger.warning(f"Error saving timer states in bulk: {e}")
   
# Singleton instance of AsyncTimerManager, use init_timer(model) to instantiate.
timer = None

def init_timer(model):
    global timer
    if timer is None:
        timer = AsyncTimerManager(model)
    return timer
