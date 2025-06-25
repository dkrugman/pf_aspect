import asyncio, time, logging, sqlite3, threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

class AsyncTimerManager:
    def __init__(self, model):
        self.__logger = logging.getLogger(__name__)
        self.__model = model
        self.__db_file = self.__model.get_model_config()['db_file']
        self.__db_file = str(Path(self.__model.get_model_config()['db_file']).expanduser().resolve())
        self.__logger.debug(f"Using database file: {self.__db_file}")
        self._tasks = []
        self._running = False
        self._db = sqlite3.connect(self.__db_file, check_same_thread=True)
        self._db_lock = threading.Lock()
        with self._db_lock:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS timer_state (
                    name TEXT PRIMARY KEY,
                    last_run REAL
                )
            """)
            self._db.commit()
        
    def register(self, callback, interval: float, name: str):
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
            asyncio.create_task(self._run())

    def stop(self):
        self._running = False
        self._save_all_states()
        self._db.close()

    async def _run(self):
        try:
            while self._running:
                now = time.time()
                coros = []

                for task in self._tasks:
                    if now - task["last_run"] >= task["interval"]:
                        self.__logger.debug(f"Scheduling: {task['name']}")
                        task["last_run"] = now
                        self._save_last_run(task["name"], now)
                        coros.append(self._safe_execute(task))

                if coros:
                    await asyncio.gather(*coros)

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            self.__logger.info("TimerManager cancelled.")
            self._save_all_states()

    async def _safe_execute(self, task):
        try:
            await task["callback"]()
        except Exception as e:
            self.__logger.exception(f"Error in callback {task['name']}: {e}")

    def _load_last_run(self, name):
        with self._db_lock:
            cur = self._db.cursor()
            cur.execute("SELECT last_run FROM timer_state WHERE name = ?", (name,))
            row = cur.fetchone()
        return row[0] if row else None

    def _save_last_run(self, name, timestamp):
        with self._db_lock:    
            self._db.execute(
                "INSERT OR REPLACE INTO timer_state (name, last_run) VALUES (?, ?)",
                (name, timestamp)
            )
            self._db.commit()

    def _save_all_states(self):
        for task in self._tasks:
            self._save_last_run(task["name"], task["last_run"])

# Singleton instance of AsyncTimerManager
timer = None

def init_timer(model):
    global timer
    if timer is None:
        timer = AsyncTimerManager(model)
    return timer