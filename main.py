"""
Task Manager and Control System for Reverse Osmosis Filtering

This module manages an asynchronous task queue for controlling a water filtration
system. It provides mechanisms for scheduling tasks, handling button inputs,
and executing automatic flushing based on predefined timing configurations.

Key Features:
- Manages sequential execution of tasks
- Supports task cancellation and automatic task continuation
- Stores execution history for analysis
- Provides debugging logs for monitoring and troubleshooting
"""

from machine import Pin
import time
import uasyncio
import ujson
import os

# Configuration constants
CONFIG_FILE = 'config.json'
CONFIG = {
    'flush_sec': 10,
    'disposal_sec': 60,
    'filter_sec': 120,
    'auto_flush_sec': 8 * 60 * 60,
    'water_clean_sec': 5 * 60,
}

MIN_FILTER_DURATION = 30  # Minimum duration for filtering

# Hardware pin configuration
PIN_BUZZER = Pin(15, Pin.OUT)
PIN_BUTTON = Pin(16, Pin.IN, Pin.PULL_UP)
PIN_VALVE1 = Pin(0, Pin.OUT)
PIN_VALVE2 = Pin(1, Pin.OUT)
PIN_VALVE3 = Pin(2, Pin.OUT)
PIN_VALVE4 = Pin(3, Pin.OUT)

class DummyTask():
    """
    A placeholder class representing a completed task.
    Used to handle cases where no task is currently running.
    """
    def done(self):
        return True

# Log file configuration
LOG_FILE = 'log.txt'
TEMP_FILE = 'log_tmp.txt'
MAX_FILE_SIZE = 250 * 1024  # 250 KB
LINES_TO_REMOVE = 250  # Number of lines to remove when trimming log file


def debug(message, func='unknown'):
    """
    Logs debug messages with timestamps for system monitoring.

    Args:
        message (str): The message to be logged.
        func (str): The function where the message originates.
    """
    timestamp = time.localtime()
    time_str = "{:04}-{:02}-{:02} {:02}:{:02}:{:02}".format(
        timestamp[0], timestamp[1], timestamp[2], timestamp[3], timestamp[4], timestamp[5])
    msg = f"{time_str} -- [{func:>16}] -- {message}\n"
    #print(msg.strip())  # remove for online debugging

    with open(LOG_FILE, 'a') as log_file:
        log_file.write(msg)

    if os.stat(LOG_FILE)[6] > MAX_FILE_SIZE:
        trim_log_file()


def trim_log_file():
    """
    Trims the log file when it exceeds the maximum size limit.
    """
    with open(LOG_FILE, 'r') as log_file, open(TEMP_FILE, 'w') as temp_file:
        for _ in range(LINES_TO_REMOVE):
            log_file.readline()
        while True:
            line = log_file.readline()
            if not line:
                break
            temp_file.write(line)
    os.remove(LOG_FILE)
    os.rename(TEMP_FILE, LOG_FILE)


def read_config():
    """
    Reads the configuration from a JSON file.
    Returns:
        dict: Configuration dictionary if the file exists, otherwise an empty dictionary.
    """
    try:
        with open(CONFIG_FILE, 'r') as f:
            return ujson.loads(f.read())
    except OSError:
        return {}


def write_config(config):
    """
    Writes the updated configuration to the JSON file.

    Args:
        config (dict): The configuration dictionary to save.
    """
    with open(CONFIG_FILE, 'w') as f:
        f.write(ujson.dumps(config))


class TaskManager:
    """
    Manages task execution and scheduling for the filtration system.

    Attributes:
        loop (uasyncio event loop): The event loop handling asynchronous execution.
        task_list (list): Queue storing pending tasks.
        current_task (uasyncio.Task): Currently running task.
        current_task_type (str): Type identifier of the running task.
        current_task_start (float): Start time of the running task.
        task_running (bool): Flag indicating if a task is running.
        max_history (int): Maximum number of completed tasks to retain.
        completed_tasks (list): Stores completed task records.
    """
    def __init__(self, loop, max_history=10):
        self.loop = loop
        self.task_list = []
        self.current_task = None
        self.current_task_type = None
        self.current_task_start = None
        self.task_running = False
        self.max_history = max_history
        self.completed_tasks = []

    def add_task(self, task_func, task_type, *args):
        """
        Adds a new task to the execution queue.

        Args:
            task_func (coroutine function): The function representing the task.
            task_type (str): Identifier for the task type.
            *args: Arguments passed to the task function.
        """
        self.task_list.append((task_func, task_type, args))
        debug(f"Task added: {task_type}", "TaskManager")

        # Start execution if no task is currently running
        if self.current_task is None:
            self.loop.create_task(self.run_next_task())

    async def run_next_task(self):
        """
        Executes the next task in the queue asynchronously.
        """
        if self.task_running or not self.task_list:
            return

        self.task_running = True
        task_func, task_type, args = self.task_list.pop(0)
        self.current_task_type = task_type
        self.current_task_start = time.time()
        debug(f"Starting task: {task_type} at {self.current_task_start}", "TaskManager")

        try:
            self.current_task = self.loop.create_task(task_func(*args))
            await self.current_task
            self._add_completed_task(task_type, self.current_task_start, time.time(), True)
            debug(f"Task {task_type} completed at {task_end_time}", "TaskManager")
        except uasyncio.CancelledError:
            self._add_completed_task(task_type, self.current_task_start, time.time(), False)
            debug(f"Task {task_type} was cancelled at {task_end_time}", "TaskManager")

        self.current_task = None
        self.current_task_type = None
        self.current_task_start = None
        self.task_running = False

        return await self.run_next_task()

    def cancel_current_task(self):
        """
        Cancels the currently running task if it exists.
        """
        if self.current_task and not self.current_task.done():
            debug(f"Cancelling task: {self.current_task_type}", "TaskManager")
            self.current_task.cancel()

    def _add_completed_task(self, task_type, task_start_time, task_end_time, has_completed):
        """
        Stores task completion details in history.
        """
        self.completed_tasks.append((task_type, task_start_time, task_end_time, has_completed))
        if len(self.completed_tasks) > self.max_history:
            self.completed_tasks.pop(0)

    def get_completed_tasks(self):
        """
        Retrieves the history of completed tasks.
        """
        return self.completed_tasks


def _set_valves(v1, v2, v3, v4):
    """
    Controls the state of four valves based on the given boolean parameters.
    The valves are inverted before being set (i.e., True means closed, False means open).

    :param v1: State of Valve 1 (Fale = closed, True = open)
    :param v2: State of Valve 2 (Fale = closed, True = open)
    :param v3: State of Valve 3 (Fale = closed, True = open)
    :param v4: State of Valve 4 (Fale = closed, True = open)
    """
    PIN_VALVE1.value(not v1)
    PIN_VALVE2.value(not v2)
    PIN_VALVE3.value(not v3)
    PIN_VALVE4.value(not v4)


def close_valves():
    """Closes all valves by setting them to False."""
    _set_valves(False, False, False, False)


def set_valves_to_flush():
    """Sets the valves to flush mode, allowing water to flow through the membrane for cleaning."""
    _set_valves(True, True, False, False)


def set_valves_to_disposal():
    """Sets the valves to dispose of wastewater after filtration."""
    _set_valves(True, False, True, False)


def set_valves_to_filter():
    """Sets the valves to filtering mode, directing water through the filter for purification."""
    _set_valves(True, False, False, True)


def init():
    """Initializes the system by closing valves and loading configuration settings."""
    debug('Set valves to be closed')
    close_valves()
    CONFIG.update(read_config())
    debug('config read: {}'.format(CONFIG))


async def greeting_beeps():
    """Plays a startup beep sequence to indicate system initialization."""
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.1)
    PIN_BUZZER.value(0)
    await uasyncio.sleep(0.1)
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.5)
    PIN_BUZZER.value(0)


async def finish_beeps():
    """Plays a sequence of beeps to signal the completion of a process."""
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.4)
    PIN_BUZZER.value(0)
    await uasyncio.sleep(0.2)
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.4)
    PIN_BUZZER.value(0)
    await uasyncio.sleep(0.2)
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.4)
    PIN_BUZZER.value(0)


async def short_beep():
    """Plays a short beep sound."""
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.2)
    PIN_BUZZER.value(0)


async def long_beep():
    """Plays a long beep sound."""
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.5)
    PIN_BUZZER.value(0)


async def flush_filter():
    """
    Performs a filter flush by running the flush sequence, followed by disposing of the first filtered water.
    """
    try:
        debug('flush osmose membrane', 'flush_filter')
        set_valves_to_flush()
        await uasyncio.sleep(CONFIG['flush_sec'])
        debug('discard filtered water', 'flush_filter')
        set_valves_to_disposal()
        await uasyncio.sleep(CONFIG['disposal_sec'])
    finally:
        debug('RESET VALVES!', 'flush_filter')
        close_valves()
        await short_beep()


async def filter_water(duration_sec=None):
    """
    Runs the water filtration process for a specified duration or the default configuration time.
    """
    if duration_sec is None:
        duration_sec = CONFIG['filter_sec']

    try:
        debug('filtering water', 'filter_water')
        set_valves_to_filter()
        await uasyncio.sleep(duration_sec)
        debug('filtering done :)', 'filter_water')
        await finish_beeps()
    finally:
        debug('RESET VALVES!', 'filter_water')
        close_valves()


def is_button_pressed():
    """Checks if the system's control button is currently pressed."""
    return PIN_BUTTON.value() == 0


async def handle_button():
    """
    Handles button presses to trigger filtration or flushing events, based on the press duration.
    """
    while True:
        while not is_button_pressed():
            await uasyncio.sleep_ms(20)

        # button pressed
        ms_start = time.ticks_ms()
        while is_button_pressed():
            await uasyncio.sleep_ms(20)

        # button released... calculate press duration
        ms_end = time.ticks_ms()
        ms_duration = ms_end - ms_start

        if ms_duration <= 50:
            # button press too short -> ignored
            continue

        long_pressed = ms_duration > 1500
        if long_pressed:
            debug('Long button press', 'handle_button')
            await long_beep()
        else:
            debug('Short button press', 'handle_button')
            await short_beep()

        # handle scheduling of filtration and flushing tasks based on button press duration
        if task_manager.current_task:
            elapsed_time = time.time() - task_manager.current_task_start
            if task_manager.current_task_type == "FILTERING" and long_pressed and elapsed_time > MIN_FILTER_DURATION:
                # save the new time interval for filtering
                CONFIG['filter_sec'] = max(MIN_FILTER_DURATION, elapsed_time)
                write_config(CONFIG)
                debug('save new time interval: {}'.format(CONFIG['filter_sec']), 'handle_button')
            elif task_manager.current_task_type == "AUTOFLUSH":
                if long_pressed:
                    debug("Schedule long filtering task", "handle_button")
                    task_manager.add_task(filter_water, "FILTER", 60 * 60)
                else:
                    debug("Schedule short filtering task", "handle_button")
                    task_manager.add_task(filter_water, "FILTER")
            debug(f"Cancelling current task {task_manager.current_task_type}", "handle_button")
            task_manager.cancel_current_task()
        else:
            # check whether we need to flush the membrane
            flush_needed = True
            if len(task_manager.completed_tasks) > 0:
                last_completed_task_end_time = task_manager.completed_tasks[-1][2]
                flush_needed = time.time() - last_completed_task_end_time > CONFIG['water_clean_sec']
            if flush_needed:
                debug(f"Schedule flushing task", "handle_button")
                task_manager.add_task(flush_filter, "FLUSH")

            # decide upon new event
            if long_pressed:
                debug("Schedule long filtering task", "handle_button")
                task_manager.add_task(filter_water, "FILTER", 60 * 60)
            else:
                debug("Schedule short filtering task", "handle_button")
                task_manager.add_task(filter_water, "FILTER")

        # kick-off the scheduled tasks
        if task_manager.current_task is None:
            event_loop.create_task(task_manager.run_next_task())


async def auto_flush():
    """
    Periodically checks if auto-flushing is required based on the elapsed time since the last completed task.
    """
    while True:
        await uasyncio.sleep(1)
        if task_manager.current_task or len(task_manager.task_list) > 0:
            continue

        auto_flush_needed = False
        if len(task_manager.completed_tasks) > 0:
            last_completed_task_end_time = task_manager.completed_tasks[-1][2]
            auto_flush_needed = time.time() - last_completed_task_end_time > CONFIG['auto_flush_sec']

        if auto_flush_needed:
            debug('Schedule auto flush task', 'auto_flush')
            task_manager.add_task(flush_filter, "AUTOFLUSH")
            if task_manager.current_task is None:
                event_loop.create_task(task_manager.run_next_task())


# Initialize and start the system
init()
event_loop = uasyncio.get_event_loop()
task_manager = TaskManager(event_loop)
event_loop.run_until_complete(greeting_beeps())
event_loop.create_task(handle_button())
event_loop.create_task(auto_flush())
event_loop.run_forever()


