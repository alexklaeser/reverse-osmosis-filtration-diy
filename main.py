from machine import Pin
import time
import uasyncio
import ujson
import os
from collections import deque


# config values with default values - will be overridden by CONFIG_FILE
CONFIG_FILE = 'config.json'
CONFIG = {
    'flush_sec': 10,
    'disposal_sec': 60,
    'filter_sec': 120,
    'auto_flush_sec': 8 * 60 * 60,
    'water_clean_sec': 5 * 60,
}

MIN_FILTER_DURATION = 30

PIN_BUZZER = Pin(15, Pin.OUT)
PIN_BUTTON = Pin(16, Pin.IN, Pin.PULL_UP)

PIN_VALVE1 = Pin(0, Pin.OUT)
PIN_VALVE2 = Pin(1, Pin.OUT)
PIN_VALVE3 = Pin(2, Pin.OUT)
PIN_VALVE4 = Pin(3, Pin.OUT)


class DummyTask():
    def done(self):
        return True

LOG_FILE = 'log.txt'
TEMP_FILE = 'log_tmp.txt'
MAX_FILE_SIZE = 250 * 1024  # 250 KB
LINES_TO_REMOVE = 1000


def debug(message, func='unknown'):
    # Get the current local time as a formatted string
    timestamp = time.localtime()
    time_str = "{:04}-{:02}-{:02} {:02}:{:02}:{:02}".format(
        timestamp[0], timestamp[1], timestamp[2], timestamp[3], timestamp[4], timestamp[5])
    msg = f"{time_str} -- [{func:>16}] -- {message}\n"
    print(msg)
    #return ### REMOVE FOR DEBUG

    # Open the log file in append mode and write the message
    with open(LOG_FILE, 'a') as log_file:
        log_file.write(msg)

    # Check the size of the log file
    if os.stat(LOG_FILE)[6] > MAX_FILE_SIZE:
        trim_log_file()


def trim_log_file():
    # Open the log file and temporary file
    with open(LOG_FILE, 'r') as log_file, open(TEMP_FILE, 'w') as temp_file:
        # Skip the first N lines
        for _ in range(LINES_TO_REMOVE):
            log_file.readline()

        # Write the remaining lines to the temporary file
        while True:
            line = log_file.readline()
            if not line:
                break
            temp_file.write(line)

    # Replace the original log file with the temporary file
    os.remove(LOG_FILE)
    os.rename(TEMP_FILE, LOG_FILE)


def read_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            config_data = f.read()
            config = ujson.loads(config_data)
            return config
    except OSError:
        pass
    return {}


def write_config(config):
    config_data = ujson.dumps(config)
    with open(CONFIG_FILE, 'w') as f:
        f.write(config_data)


class TaskManager:
    def __init__(self, loop, max_history=10):
        self.loop = loop  # Use the shared event loop
        self.task_list = []  # Queue for pending tasks
        self.current_task = None
        self.current_task_type = None  # Identifies the type of task
        self.current_task_start = None  # Start time of current task
        self.task_running = False
        self.max_history = max_history
        self.completed_tasks = []

    def add_task(self, task_func, task_type, *args):
        """Add a task to the queue."""
        self.task_list.append((task_func, task_type, args))
        debug(f"Task added: {task_type}", "TaskManager")

        # Start execution if no task is currently running
        if self.current_task is None:
            self.loop.create_task(self.run_next_task())

    async def run_next_task(self):
        """Run the next task in the queue safely."""
        if self.task_running:
            return  # Prevent duplicate execution

        if len(self.task_list) == 0:
            return  # nothing else to do

        self.task_running = True  # 🚀 Lock to prevent re-entry
        task_func, task_type, args = self.task_list.pop(0)
        self.current_task_type = task_type
        self.current_task_start = time.time()
        debug(f"Starting task: {task_type} at {self.current_task_start}", "TaskManager.run_next_task")

        try:
            self.current_task = self.loop.create_task(task_func(*args))
            await self.current_task
            task_end_time = time.time()
            self._add_completed_task(task_type, self.current_task_start, task_end_time, True)
            debug(f"Task {task_type} completed at {task_end_time}", "TaskManager.run_next_task")

        except uasyncio.CancelledError:
            task_end_time = time.time()
            debug(f"Task {task_type} was cancelled at {task_end_time}", "TaskManager.run_next_task")
            self._add_completed_task(task_type, self.current_task_start, task_end_time, False)

        # Reset task tracking
        self.current_task = None
        self.current_task_type = None
        self.current_task_start = None
        self.task_running = False  # Unlock

        return await self.run_next_task()  # Start next task automatically


    def cancel_current_task(self):
        """Cancel the current task and move to the next one."""
        if self.current_task and not self.current_task.done():
            debug(f"Cancelling task: {self.current_task_type}", "TaskManager.cancel_current_task")
            self.current_task.cancel()

    def _add_completed_task(self, task_type, task_start_time, task_end_time, has_completed):
        self.completed_tasks.append((task_type, task_start_time, task_end_time, has_completed))
        if len(self.completed_tasks) > self.max_history:
            self.completed_tasks.pop(0)

    def get_completed_tasks(self):
        """Return a list of the last N completed tasks with timestamps."""
        return self.completed_tasks


def _set_valves(v1, v2, v3, v4):
    PIN_VALVE1.value(not v1)
    PIN_VALVE2.value(not v2)
    PIN_VALVE3.value(not v3)
    PIN_VALVE4.value(not v4)


def close_valves():
    _set_valves(False, False, False, False)


def set_valves_to_flush():
    _set_valves(True, True, False, False)


def set_valves_to_disposal():
    _set_valves(True, False, True, False)


def set_valves_to_filter():
    _set_valves(True, False, False, True)


def init():
    debug('Set valves to be closed')
    close_valves()
    CONFIG.update(read_config())
    debug('config read: {}'.format(CONFIG))


async def greeting_beeps():
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.1)
    PIN_BUZZER.value(0)
    await uasyncio.sleep(0.1)
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.5)
    PIN_BUZZER.value(0)


async def finish_beeps():
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
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.2)
    PIN_BUZZER.value(0)


async def long_beep():
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.5)
    PIN_BUZZER.value(0)


async def flush_filter():
    debug('### start', 'flush_filter')
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
    debug('### end', 'flush_filter')


async def filter_water(duration_sec=None):
    debug('### start', 'filter_water')
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
    debug('### end', 'filter_water')


def is_button_pressed():
    return PIN_BUTTON.value() == 0


async def handle_button():
    while True:
        # wait for the button to be pressed
        while not is_button_pressed():
            await uasyncio.sleep_ms(20)
        debug('### button pressed', 'handle_button')

        # wait for the button to be released
        ms_start = time.ticks_ms()
        while is_button_pressed():
            await uasyncio.sleep_ms(20)
        ms_end = time.ticks_ms()
        ms_duration = ms_end - ms_start
        debug('### button released (press duration: {} ms)'.format(ms_duration), 'handle_button')
        if ms_duration <= 50:
            debug('### button press too short - ignored', 'handle_button')
            continue

        # do the beep
        long_pressed = ms_duration > 1500
        if long_pressed:
            debug('Long button press', 'handle_button')
            await long_beep()
        else:
            debug('Short button press', 'handle_button')
            await short_beep()

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
    while True:
        await uasyncio.sleep(1)
        if task_manager.current_task or len(task_manager.task_list) > 0:
            # don't do any flushing if a task is running
            # ... the program should never come to this point here ;)
            continue

        # check whether we need to do some auto-flushing
        auto_flush_needed = False
        if len(task_manager.completed_tasks) > 0:
            last_completed_task_end_time = task_manager.completed_tasks[-1][2]
            auto_flush_needed = time.time() - last_completed_task_end_time > CONFIG['auto_flush_sec']
        if auto_flush_needed:
            #if reflush_needed:
            #    last_reflush_end = t
            #    debug('REFLUSHING')
            #else:
            debug('Schedule auto flush task', 'auto_flush')
            task_manager.add_task(flush_filter, "AUTOFLUSH")

            # kick-off the scheduled tasks
            if task_manager.current_task is None:
                event_loop.create_task(task_manager.run_next_task())


# init and run all co-routines
init()
event_loop = uasyncio.get_event_loop()
task_manager = TaskManager(event_loop)
event_loop.run_until_complete(greeting_beeps())
event_loop.create_task(handle_button())
event_loop.create_task(auto_flush())
event_loop.run_forever()

