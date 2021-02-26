import asyncio
import signal
from contextlib import suppress

import pulsectl_asyncio

# import pulsectl
# print('Event types:', pulsectl.PulseEventTypeEnum)
# print('Event facilities:', pulsectl.PulseEventFacilityEnum)
# print('Event masks:', pulsectl.PulseEventMaskEnum)


async def listen():
    async with pulsectl_asyncio.PulseAsync('event-printer') as pulse:
        async for event in pulse.subscribe_events('all'):
            print('Pulse event:', event)


async def main():
    # Run listen() coroutine in task to allow cancelling it
    listen_task = loop.create_task(listen())

    # Schedule listen_task to be cancelled after 10 seconds
    # Alternatively, the PulseAudio event subscription can be ended by breaking/returning from the `async for` loop
    loop.call_later(5, listen_task.cancel)

    # register signal handlers to cancel listener when program is asked to terminate
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        loop.add_signal_handler(sig, listen_task.cancel)

    with suppress(asyncio.CancelledError):
        await listen_task


# Run event loop until main_task finishes
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
