from machine import Pin
import time
import uasyncio
import ujson

# config values with default values - will be overridden by CONFIG_FILE
CONFIG_FILE = 'config.json'
CONFIG = {
    'flush_sec': 10,
    'disposal_sec': 60,
    'filter_sec': 120,
    'auto_flush_sec': 8 * 60 * 60,
    'water_clean_sec': 5 * 60,
}

PIN_BUZZER = Pin(15, Pin.OUT)
PIN_BUTTON = Pin(16, Pin.IN, Pin.PULL_UP)

PIN_VALVE1 = Pin(0, Pin.OUT)
PIN_VALVE2 = Pin(1, Pin.OUT)
PIN_VALVE3 = Pin(2, Pin.OUT)
PIN_VALVE4 = Pin(3, Pin.OUT)


class DummyTask():
    def done(self):
        return True

last_flush_end = 0
last_reflush_end = 0
last_filtering_end = 0
last_filtering_start = 0

running_task = DummyTask()
running_task_type = None

import os
import time

LOG_FILE = 'log.txt'
TEMP_FILE = 'log_tmp.txt'
MAX_FILE_SIZE = 250 * 1024  # 250 KB
LINES_TO_REMOVE = 1000


def debug(message):
    # Get the current local time as a formatted string
    timestamp = time.localtime()
    time_str = "{:04}-{:02}-{:02} {:02}:{:02}:{:02}".format(
        timestamp[0], timestamp[1], timestamp[2], timestamp[3], timestamp[4], timestamp[5])
    return ### REMOVE FOR DEBUG
    
    # Open the log file in append mode and write the message
    with open(LOG_FILE, 'a') as log_file:
        log_file.write(f"{time_str} - {message}\n")
    
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
    debug('### flush_filter()')
    global last_flush_end, running_task_type
    running_task_type = 'FLUSHING'
    try:
        debug('  flush osmose membrane')
        set_valves_to_flush()
        await uasyncio.sleep(CONFIG['flush_sec'])
        debug('  discard filtered water')
        set_valves_to_disposal()
        await uasyncio.sleep(CONFIG['disposal_sec'])
    except Exception as e:
        debug(f'  Error during flushing: {e}')
    finally:
        last_flush_end = time.time()
        debug('  RESET VALVES!')
        close_valves()
    await short_beep()
    

async def filter_water(duration_sec=None):
    global last_filtering_end, last_filtering_start, running_task_type
    debug('### filter_water()')
    
    if duration_sec is None:
        duration_sec = CONFIG['filter_sec']
        
    # check whether we need to flush the membrane
    flush_needed = time.time() - max(last_flush_end, last_filtering_end) > CONFIG['water_clean_sec']
    if flush_needed:
        try:
            await flush_filter()
            debug('### back in -> filter_water()')
        except Exception as e:
            debug('### back in -> filter_water()')
            debug(f'  Error during flushing: {e}')
    
    # do the filtering
    try:
        running_task_type = 'FILTERING'
        last_filtering_start = time.time()
        debug('  filter water')
        debug('  last_filtering_start = {}'.format(last_filtering_start))
        set_valves_to_filter()
        await uasyncio.sleep(duration_sec)
        debug('  filtering done :)')
        await finish_beeps()
    finally:
        last_filtering_end = time.time()
        debug('  last_filtering_end = {}'.format(last_filtering_end))
        debug('  RESET VALVES!\n')
        close_valves()


def is_button_pressed():
    return PIN_BUTTON.value() == 0


async def handle_button():
    global running_task
    while True:
        # wait for the button to be pressed
        while not is_button_pressed():
            await uasyncio.sleep_ms(20)
        
        # wait for the button to be released
        ms_start = time.ticks_ms()
        while is_button_pressed():
            await uasyncio.sleep_ms(20)
        ms_end = time.ticks_ms()
        ms_duration = ms_end - ms_start

        # do the beep
        long_pressed = ms_duration > 800
        debug('### handle_button() - new loop')
        if long_pressed:           
            debug('  Long button press')
            await long_beep()
        else:
            debug('  Short button press')
            await short_beep()
            
        # decide upon the action
        if not running_task.done():
            debug('  Cancel task {}'.format(running_task_type))
            running_task.cancel()
            #try:
            #    await uasyncio.wait_for_ms(running_task, 100)
            #except uasyncio.TimeoutError:
            #    pass
            if long_pressed and running_task_type == 'FILTERING':
                # save the new time interval for filtering
                CONFIG['filter_sec'] = last_filtering_end - last_filtering_start
                write_config(CONFIG)
                debug('  save new time interval: {}'.format(CONFIG['filter_sec']))
            elif long_pressed and running_task_type == 'FLUSHING':
                # filter directly the water for a long time
                debug('  long filtering')
                running_task = event_loop.create_task(filter_water(60 * 60))
            elif running_task_type == 'FLUSHING':
                # filter directly the water
                debug('  filtering')
                running_task = event_loop.create_task(filter_water())
        else:
            if long_pressed:
                running_task = event_loop.create_task(filter_water(60 * 60))
                debug('  long filtering')
            else:
                running_task = event_loop.create_task(filter_water())
                debug('  filtering')


async def auto_flush():
    global last_flush_end, last_reflush_end, running_task
    while True:
        await uasyncio.sleep(1)
        if not running_task.done():
            # don't do any flushing if a task is running
            # ... the program should never come to this point here ;)
            continue
        
        # check whether we need to do some auto-flushing
        t = time.time()
        auto_flush_needed = t - max(last_flush_end, last_filtering_end) > CONFIG['auto_flush_sec']
        #reflush_needed = t - last_filtering_end > CONFIG['water_clean_sec'] and last_reflush_end < last_filtering_end
        if auto_flush_needed:
            #if reflush_needed:
            #    last_reflush_end = t
            #    debug('REFLUSHING')
            #else:
            debug('### auto_flush() -> FLUSHING')
            running_task = event_loop.create_task(flush_filter())


# init and run all co-routines
init()
event_loop = uasyncio.get_event_loop()
event_loop.run_until_complete(greeting_beeps())
event_loop.create_task(handle_button())
event_loop.create_task(auto_flush())
event_loop.run_forever()


