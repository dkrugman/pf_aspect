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
import logging
import sqlite3
import time

# from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .config import DB_JRNL_MODE


class AsyncTimerManager:
    def __init__(self, model):
        self.__logger = logging.getLogger(__name__)
        self.__model = model
        self.__db_file = str(Path(self.__model.get_model_config()["db_file"]).expanduser().resolve())
        self.__logger.debug_detailed(f"Using database file: {self.__db_file}")
        self._tasks = []
        self._running = False
        self._task = None  # Store the background task
        self._running_tasks = set()  # Track running tasks for proper cleanup
        self.__db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=30.0)
        self.__db.row_factory = sqlite3.Row

        self.__db.execute(f"PRAGMA journal_mode={DB_JRNL_MODE}")
        self.__db.execute("PRAGMA synchronous=NORMAL")
        self.__db.execute("PRAGMA foreign_keys=ON")
        self.__db.execute("PRAGMA busy_timeout=30000")
        self.__db.execute("PRAGMA temp_store=MEMORY")
        self.__db.execute("PRAGMA mmap_size=30000000000")
        self.__db.execute("PRAGMA cache_size=10000")
        self.__logger.debug_detailed("AsyncTimerManager DB connection established")
        with self.__db:
            self.__db.execute(
                """
                CREATE TABLE IF NOT EXISTS timer_state (
                    name TEXT PRIMARY KEY,
                    last_run REAL
                )
            """
            )

    def register(self, callback: Callable[[], Awaitable[None]], interval: float, name: str) -> None:
        """Register an async function with interval (sec) and a unique name."""
        if not asyncio.iscoroutinefunction(callback):
            raise TypeError("Callback must be an async function")

        last_run = self._load_last_run(name)
        if last_run is None:
            last_run = time.time() - interval  # Run immediately

        task = {"name": name, "callback": callback, "interval": interval, "last_run": last_run}
        self._tasks.append(task)

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            while self._running:
                now = time.time()
                coros = []
                for task in self._tasks:
                    due = now - task["last_run"] >= task["interval"]
                    if due:
                        # self.__logger.debug_detailed(f"Task '{task['name']}' is due (interval: {task['interval']}s, "
                        #                    f"last_run: {task['last_run']}, now: {now})")
                        task["last_run"] = now
                        self._save_last_run(task["name"], now)
                        coros.append(self._run_task(task))
                    else:
                        pass  # Task not due yet

                if coros:
                    self.__logger.debug_detailed(f"Executing {len(coros)} timer tasks")
                    # Start tasks without awaiting them to avoid blocking the timer loop
                    for coro in coros:
                        task = asyncio.create_task(coro)
                        self._running_tasks.add(task)
                        # Remove task from set when it completes
                        task.add_done_callback(lambda t: self._running_tasks.discard(t))

                await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.__logger.debug_detailed("TimerManager cancelled.")
            # try:
            #     self._save_all_states()
            # except Exception as e:
            #     self.__logger.warning(f"Error saving timer states during shutdown: {e}")
            # raise
        finally:
            try:
                self.__db.close()
            except Exception as e:
                self.__logger.warning(f"Error closing database: {e}")

    async def _run_task(self, task):
        try:
            await task["callback"]()
        except asyncio.CancelledError:
            self.__logger.debug_detailed(f"Task {task['name']} was cancelled")
            # Call cleanup on the callback object if it has one
            try:
                callback_obj = getattr(task["callback"], "__self__", None)
                if callback_obj and hasattr(callback_obj, "cleanup"):
                    self.__logger.debug_detailed(f"Calling cleanup on cancelled {task['name']} callback object")
                    callback_obj.cleanup()
            except Exception as e:
                self.__logger.warning(f"Error calling cleanup on cancelled {task['name']}: {e}")
            raise  # Re-raise CancelledError
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
        cur = self.__db.cursor()
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
        self.__logger.debug_detailed(f"Saving timer state for {name}: {timestamp}")
        try:
            with self.__db:  # auto-commit
                self.__db.execute(
                    """
                    INSERT INTO timer_state (name, last_run)
                    VALUES (?, ?)
                    ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run
                """,
                    (name, timestamp),
                )
        except Exception as e:
            self.__logger.warning(f"Error saving timer state for {name}: {e}")

    def _save_all_states(self) -> None:
        self.__logger.debug_detailed("Saving all timer states")
        try:
            with self.__db:
                for task in self._tasks:
                    self.__db.execute(
                        """
                        INSERT INTO timer_state (name, last_run)
                        VALUES (?, ?)
                        ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run
                    """,
                        (task["name"], task["last_run"]),
                    )
        except Exception as e:
            self.__logger.warning(f"Error saving timer states in bulk: {e}")

    def stop(self):
        """Stop the timer and cancel all running tasks."""
        self.__logger.debug_detailed("Stopping AsyncTimerManager...")

        # Save timer states
        self._save_all_states()

        # Cancel all running tasks FIRST (this will raise CancelledError in callbacks)
        if self._running_tasks:
            self.__logger.debug_detailed(f"Cancelling {len(self._running_tasks)} running timer tasks...")
            for task in self._running_tasks.copy():
                task.cancel()
            self._running_tasks.clear()

        # Stop the main timer loop
        self._running = False
        if self._task and not self._task.done():
            self.__logger.debug_detailed("Cancelling main timer task...")
            self._task.cancel()

        # Note: cleanup is now called automatically when tasks are cancelled in _run_task

        # Close database connection
        try:
            if hasattr(self, "_AsyncTimerManager__db") and self._AsyncTimerManager__db:
                self._AsyncTimerManager__db.close()
                self.__logger.debug_detailed("AsyncTimerManager database connection closed")
        except Exception as e:
            self.__logger.warning(f"Error closing AsyncTimerManager database: {e}")

        self.__logger.debug_detailed("AsyncTimerManager stopped")


# Singleton instance of AsyncTimerManager, use init_timer(model) to instantiate.
timer = None


def init_timer(model):
    global timer
    if timer is None:
        timer = AsyncTimerManager(model)
    return timer
