"""
Program Summary:

This script is designed for a Raspberry Pi Pico runing MicroPython with the
goal of controlling a set of 4 valves for a reverse osmosis filtration system.
The system is controlled with one button. Its main features are:
- Automatic flushing of the osmosis membrane every few hours to avoid the
  development of germs in the different filters.
- Automatic disposal of the first filtered water that (that contains more
  particles due to the lowered pressure in the osmosis membrane during its
  idle time).
- Setting a fixed time interval for walter filtration to yield a specific
  amount of water. This time interval can be stored via a long button press.

The script is structured to be asynchronous, allowing it to handle multiple
operations efficiently without blocking the main execution flow.
"""

# Importing necessary libraries for hardware control and asynchronous operations
from machine import Pin
import time
import uasyncio
import ujson
import os

# Configuration values with default settings.
# These settings are used for various timing operations in the script and can be overridden by an external configuration file.
CONFIG_FILE = 'config.json'  # Name of the external configuration file.
CONFIG = {
    'flush_sec': 10,          # Time in seconds for the flush operation.
    'disposal_sec': 60,       # Time in seconds for the disposal operation.
    'filter_sec': 120,        # Time in seconds for the filter operation.
    'auto_flush_sec': 8 * 60 * 60,  # Time in seconds for automatic flushing (here, 8 hours).
    'water_clean_sec': 5 * 60,      # Time in seconds for water cleaning operation.
}

MIN_FILTER_DURATION = 30  # Minimum duration for filtering

# GPIO pin setup for various components connected to the microcontroller.
PIN_BUZZER = Pin(15, Pin.OUT)  # Buzzer pin, set as output.
PIN_BUTTON = Pin(16, Pin.IN, Pin.PULL_UP)  # Button pin, set as input with pull-up resistor.


# Pins for controlling valves or other actuators.
PIN_VALVE1 = Pin(0, Pin.OUT)  # Valve 1 control pin.
PIN_VALVE2 = Pin(1, Pin.OUT)  # Valve 2 control pin.
PIN_VALVE3 = Pin(2, Pin.OUT)  # Valve 3 control pin.
PIN_VALVE4 = Pin(3, Pin.OUT)  # Valve 4 control pin.

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
    Reads configuration settings from an external JSON file.

    This function attempts to open and read a JSON file specified by the global variable CONFIG_FILE.
    If successful, it parses the JSON content into a Python dictionary and returns it. This allows
    the program to use externally defined configurations, providing flexibility and ease of adjustments
    without modifying the code.
    Returns:
        dict: A dictionary containing configuration settings. If the file reading fails (e.g., file not found),
        the function returns an empty dictionary as a fallback, ensuring the program continues to run with
        default settings.

    Exception Handling:
        OSError: This exception is caught to handle cases where the file might not exist or be accessible.
        Instead of crashing the program, the function silently passes the exception and returns an empty
        dictionary. This design choice prioritizes the program's continuous operation, but it may be worth
        logging such errors for debugging and maintenance purposes.
    """
    try:
        with open(CONFIG_FILE, 'r') as f:
            return ujson.loads(f.read())
    except OSError:
        return {}


def write_config(config):
    """
    Writes the provided configuration settings to an external JSON file.

    This function takes a dictionary of configuration settings, converts it into a JSON string,
    and writes it to the file specified by the CONFIG_FILE global variable. This allows for
    persisting updated configurations externally, making them available for subsequent runs
    of the program or other related systems.

    Args:
        config (dict): A dictionary containing configuration settings to be written.
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
    Internal convenient function that ontrols the state of the 4 valves based on the arguments.

    Each parameter (v1, v2, v3, v4) corresponds to a specific valve and determines its state.
    The function uses the 'value' method of each PIN_VALVE object to set the state. Notably,
    the actual state is set to the logical NOT of the input parameters. This implies that a
    True value in any argument will turn OFF the corresponding valve, and a False will turn it ON.

    Args:
        v1, v2, v3, v4 (bool): Boolean values indicating the desired state of valves 1, 2, 3, and 4,
                               respectively. True to turn OFF the valve, False to turn it ON.
    """
    PIN_VALVE1.value(not v1)
    PIN_VALVE2.value(not v2)
    PIN_VALVE3.value(not v3)
    PIN_VALVE4.value(not v4)


def close_valves():
    """
    Closes all valves.

    This function calls the _set_valves function with all arguments set to False,
    effectively turning all the valves ON (closed state) as per the _set_valves logic.
    """
    _set_valves(False, False, False, False)


def set_valves_to_flush():
    """
    Configures valves for the flushing operation.

    This function sets the first two valves to an OFF (open) state and the last two valves
    to an ON (closed) state, tailored for the flushing process.
    """
    _set_valves(True, True, False, False)


def set_valves_to_disposal():
    """
    Sets valves configuration for the disposal operation.

    Adjusts the valve states specifically for disposing the filtered water. Here, valves 1
    and 3 are set to OFF (open), while valves 2 and 4 are ON (closed).
    """
    _set_valves(True, False, True, False)


def set_valves_to_filter():
    """
    Configures the valves for the filtering process.

    For the filtering operation, this function opens valves 1 and 4 (setting them to OFF),
    while closing valves 2 and 3 (setting them to ON).
    """
    _set_valves(True, False, False, True)


def init():
    """
    Initializes the system by setting valves to a closed state and loading configuration settings.

    The function outputs messages to indicate the progress of these actions, aiding in debugging and
    monitoring the initialization process.
    """
    debug('Set valves to be closed')
    close_valves()
    CONFIG.update(read_config())
    debug('config read: {}'.format(CONFIG))


async def greeting_beeps():
    """
    Plays a sequence of 1x short beep and 1x long beep as a greeting.
    """
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.1)
    PIN_BUZZER.value(0)
    await uasyncio.sleep(0.1)
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.5)
    PIN_BUZZER.value(0)


async def finish_beeps():
    """
    Plays a sequence of 3x long beeps to indicate completion.
    """
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
    """
    Emits a short beep after a short button press.
    """
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.2)
    PIN_BUZZER.value(0)


async def long_beep():
    """
    Emits a long beep after a long button press.
    """
    PIN_BUZZER.value(1)
    await uasyncio.sleep(0.5)
    PIN_BUZZER.value(0)


async def flush_filter():
    """
    Asynchronous function to perform a flushing operation of the filtration system.

    This function manages the process of flushing the osmosis membrane and discarding the first part of the filtered water.
    It controls the valves' states to facilitate these operations and uses asynchronous sleeping to
    maintain them for configured durations. The operation timestamps and task types are updated accordingly.
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
    Asynchronous function to perform water filtering.

    Initiates the water filtering process with a specified duration. If the duration is not provided,
    it defaults to a value from the configuration. The function also checks if a membrane flush is needed
    before starting the filtering. It updates global tracking variables and handles the valve states for filtering.

    Args:
        duration_sec (int, optional): The duration for which the water should be filtered. Defaults to None,
                                      in which case it uses the 'filter_sec' value from CONFIG.
    """
    # Determine the filtering duration based on the provided argument or default configuration.
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
    """
    Check if the button is pressed.

    Returns:
        bool: True if the button is pressed (LOW state), False otherwise.
    """
    return PIN_BUTTON.value() == 0


async def handle_button():
    """
    First main loop to andle button presses to trigger filtration or flushing events, based on the press duration.
    """
    while True:
        # wait for the button to be pressed
        while not is_button_pressed():
            await uasyncio.sleep_ms(20)

        # wait for the button to be released
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
    Second main loop to periodically check if auto-flushing is required based on the elapsed time since the last completed task.
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


