# pulsectl-asyncio

This library provides an Python 3 asyncio interface on top of the [pulsectl](https://github.com/mk-fg/python-pulse-control) library for monitoring and controlling the PulseAudio sound server.

*pulsectl* is a Python ctypes wrapper of the PulseAudio client C library `libpulse`, providing a high-level interface to PulseAudio's source/sink/stream handling and volume mixing. 
It has originally been forked from the internal code of the [pulsemixer](https://github.com/GeorgeFilipkin/pulsemixer/) command line application.

Although libpulse provides a callback-based asynchronous C API for the communication with the PulseAudio server, *pulsectl* only exposes a blocking Python interface, letting libpulse's internal event loop spin until a response is received for each request.
In the [README file](https://github.com/mk-fg/python-pulse-control/blob/master/README.rst#event-handling-code-threads) and [Issue #11](https://github.com/mk-fg/python-pulse-control/issues/11#issuecomment-259560564) of *pulsectl*, different ways of integrating the library into asynchronous Python applications are discussed.
However, none of these ways provides seamless integration into Python's asyncio event loop framework.

*pulsectl-asyncio* uses a ctypes-based Python implementation of the `main_loop_api` of libpulse to use a Python asyncio event loop for libpulse's asynchronous event handling.
With this event handling in place, no blocking calls into *libpulse* are required, so an asynchronous version for the high-level API of *pulsectl* can be provided.
The `PulseAsync`, provided by *pulsectl-asyncio*, exactly mimics the `Pulse` class from *pulsectl*, except that all methods are declared `async` and asynchronously await the actions' results.
Additionally, the API fo subscribing PulseAudio server events has been changed from a callback-based interface (`event_callback_set()` etc.) to a more asnycio-nic interface using an async generator. 


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
import pulsectl_asyncio

# import pulsectl
# print('Event types:', pulsectl.PulseEventTypeEnum)
# print('Event facilities:', pulsectl.PulseEventFacilityEnum)
# print('Event masks:', pulsectl.PulseEventMaskEnum)


async def main():
    async with pulsectl_asyncio.PulseAsync('event-printer') as pulse:
        async for event in pulse.subscribe_events('all'):
            print('Pulse event:', event)

# cancel() Task or `break` from `for` loop to end loop

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
```
