import asyncio
import signal
from contextlib import suppress

import pulsectl_asyncio


async def listen(pulse: pulsectl_asyncio.PulseAsync, source_name: str):
    async for level in pulse.subscribe_peak_sample(source_name, rate=5):
        print('\x1b[2K\x1b[0E', end='')  # return to beginning of line
        num_o = round(level * 80)
        print('O' * num_o + '-' * (80-num_o), end='', flush=True)


async def main():
    """
    Monitor output level of the default sink.
    """
    async with pulsectl_asyncio.PulseAsync('peak-listener') as pulse:
        # Get name of monitor_source of default sink
        server_info = await pulse.server_info()
        default_sink_info = await pulse.get_sink_by_name(server_info.default_sink_name)
        source_name = default_sink_info.monitor_source_name

        # Start listening/monitoring task
        listen_task = loop.create_task(listen(pulse, source_name))

        # Schedule listen_task to be cancelled after 10 seconds
        # Alternatively, the PulseAudio event subscription can be ended by breaking/returning from the `async for` loop
        loop.call_later(5, listen_task.cancel)

        # register signal handlers to cancel listener when program is asked to terminate
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            loop.add_signal_handler(sig, listen_task.cancel)

        with suppress(asyncio.CancelledError):
            await listen_task
            print()


# Run event loop until main_task finishes
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
