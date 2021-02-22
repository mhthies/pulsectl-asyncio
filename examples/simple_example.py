import asyncio
import pulsectl_asyncio

async def main():
    async with pulsectl_asyncio.PulseAsync('volume-increaser') as pulse:
        for sink in await pulse.sink_list():
            await pulse.volume_change_all_chans(sink, 0.1)

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
