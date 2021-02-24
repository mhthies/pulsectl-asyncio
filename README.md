# pulsectl-asyncio

This library provides an Python 3 asyncio interface on top of the [pulsectl](https://github.com/mk-fg/python-pulse-control) library for monitoring and controlling the PulseAudio sound server.

*pulsectl* is a Python ctypes wrapper of the PulseAudio client C library `libpulse`, providing a high-level interface to PulseAudio's source/sink/stream handling and volume mixing. 
It has originally been forked from the internal code of the [pulsemixer](https://github.com/GeorgeFilipkin/pulsemixer/) command line application.

Although libpulse provides a callback-based asynchronous C API for the communication with the PulseAudio server, *pulsectl* only exposes a blocking Python interface, letting libpulse's internal event loop spin until a response is received for each request.
In the [README file](https://github.com/mk-fg/python-pulse-control/blob/master/README.rst#event-handling-code-threads) and [Issue #11](https://github.com/mk-fg/python-pulse-control/issues/11#issuecomment-259560564) of *pulsectl*, different ways of integrating the library into asynchronous Python applications are discussed.
However, none of these ways provides seamless integration into Python's asyncio event loop framework.

*pulsectl-asyncio* uses a ctypes-based Python implementation of the `main_loop_api` of libpulse to use a Python asyncio event loop for libpulse's asynchronous event handling.
With this event handling in place, no blocking calls into *libpulse* are required, so an asynchronous version for the high-level API of *pulsectl* can be provided:
The `PulseAsync` class, provided by *pulsectl-asyncio*, exactly mimics the `Pulse` class from *pulsectl*, except that all methods are declared `async` and asynchronously await the actions' results.
Additionally, the API for subscribing to PulseAudio server events has been changed from a callback-based interface (`event_callback_set()` etc.) to a more asnycio-nic interface using an async generator.

*pulsectl-asyncio* depends on *pulsectl* to reuse its ctype wrappers of *libpulse* as well as the `PulseObject` classes, which are used for modelling the PulseAudio action result structures as Python objects.
The high-level API class `PulseAsync` has been copied from *pulsectl* and modified for asynchronous control flow.
Thus, its architecture and major parts of its code are still similar to *pulsectl*'s code.

For more info about the API, the returned PulseObject objects and other value types, volume specification, etc., please refer to [*pulsectl*'s README file](https://github.com/mk-fg/python-pulse-control/blob/master/README.rst#notes).  


## Usage Examples

(heavily inspired by *pulsectl*'s [README file](https://github.com/mk-fg/python-pulse-control/blob/master/README.rst#usage))

Simple example:

```python
import asyncio
import pulsectl_asyncio

async def main():
    async with pulsectl_asyncio.PulseAsync('volume-increaser') as pulse:
        for sink in await pulse.sink_list():
            await pulse.volume_change_all_chans(sink, 0.1)

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
``` 

Listening for server state change events:

```python
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
    listen_task = asyncio.create_task(listen())

    # register signal handlers to cancel listener when program is asked to terminate
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        loop.add_signal_handler(sig, listen_task.cancel)
    # Alternatively, the PulseAudio event subscription can be ended by breaking/returning from the `async for` loop

    with suppress(asyncio.CancelledError):
        await listen_task

# Run event loop until main_task finishes
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
```

Misc other tinkering:

```python
import asyncio
import pulsectl_asyncio

async def main():
    pulse = pulsectl_asyncio.PulseAsync('my-client-name')
    await pulse.connect()

    print(await pulse.sink_list())
    # [<PulseSinkInfo at 7f85cfd053d0 - desc='Built-in Audio', index=0L, mute=0, name='alsa-speakers', channels=2, volumes='44.0%, 44.0%'>]
    print(await pulse.sink_input_list())
    # [<PulseSinkInputInfo at 7fa06562d3d0 - index=181L, mute=0, name='mpv Media Player', channels=2, volumes='25.0%, 25.0%'>]

    print((await pulse.sink_input_list())[0].proplist)  # Note the parentheses around `await` and the method call
    # {'application.icon_name': 'mpv',
    #  'application.language': 'C',
    #  'application.name': 'mpv Media Player',
    #  ...
    #  'native-protocol.version': '30',
    #  'window.x11.display': ':1.0'}

    print(await pulse.source_list())
    # [<PulseSourceInfo at 7fcb0615d8d0 - desc='Monitor of Built-in Audio', index=0L, mute=0, name='alsa-speakers.monitor', channels=2, volumes='100.0%, 100.0%'>,
    #  <PulseSourceInfo at 7fcb0615da10 - desc='Built-in Audio', index=1L, mute=0, name='alsa-mic', channels=2, volumes='100.0%, 100.0%'>]

    sink = (await pulse.sink_list())[0]
    await pulse.volume_change_all_chans(sink, -0.1)
    await pulse.volume_set_all_chans(sink, 0.5)

    print((await pulse.server_info()).default_sink_name)
    # 'alsa_output.pci-0000_00_14.2.analog-stereo'
    await pulse.default_set(sink)

    card = (await pulse.card_list())[0]
    print(card.profile_list)
    # [<PulseCardProfileInfo at 7f02e7e88ac8 - description='Analog Stereo Input', n_sinks=0, n_sources=1, name='input:analog-stereo', priority=60>,
    #  <PulseCardProfileInfo at 7f02e7e88b70 - description='Analog Stereo Output', n_sinks=1, n_sources=0, name='output:analog-stereo', priority=6000>,
    #  ...
    #  <PulseCardProfileInfo at 7f02e7e9a4e0 - description='Off', n_sinks=0, n_sources=0, name='off', priority=0>]

    await pulse.card_profile_set(card, 'output:hdmi-stereo')

    pulse.close()  # No await here!

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
```
