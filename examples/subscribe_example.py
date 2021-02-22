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
