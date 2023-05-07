"""
This module provides an asynchronous high-level interface for PulseAudio actions, based on ctypes wrappers of libpulse
and PulseAudio object abstraction from *pulsectl* package.

The code of this module is completely based on *pulsectl* version 20.5.1 (Git revision 471428c). PulseObject class
definitions have been removed to be imported from the original *pulsectl* module instead. The `Pulse` class has been
modified to the `PulseAsync` class with an asynchronous interface.

Copyright (c) 2014 George Filipkin, 2016 Mike Kazantsev, 2021 Michael Thies
"""
import asyncio
import inspect
import functools as ft
from typing import Optional, AsyncIterator, Coroutine
from contextlib import suppress
import sys
from warnings import warn

from .pa_asyncio_mainloop import PythonMainLoop
from pulsectl.pulsectl import (
	PulseError, PulseEventTypeEnum, PulseEventFacilityEnum, PulseEventInfo,
	PulseEventMaskEnum, PulseLoopStop, PulseOperationFailed, PulseIndexError, PulseSinkInfo, PulseSourceInfo,
	PulseCardInfo, PulseSinkInputInfo, PulseSourceOutputInfo, PulseClientInfo, PulseServerInfo, PulseModuleInfo,
	is_list, PulseOperationInvalid, PulsePortInfo, PulseExtStreamRestoreInfo, PulseUpdateEnum, is_str,
	assert_pulse_object, PulseDisconnected, unicode, Enum)
from pulsectl import _pulsectl as c


class _pulse_op_cb:
	"""
	Async context manager class, used to create a future in PulseAsync's _actions dict and the corresponding
	callback method to be passed to the PulseAudio action call for resolving the future. The `__aexit__()` method is
	the place, where the actual asynchronous magic of this library happens.

	In `pulsectl`, it's implemented as a method of the Pulse class with @contextmanager, however, we need
	@asynccontextmanager here, which is only available since Python 3.7.
	"""
	def __init__(self, async_pulse: "PulseAsync", raw=False):
		self.raw = raw
		self.future = None
		self.async_pulse = async_pulse

	async def __aenter__(self):
		loop = asyncio.get_event_loop()
		self.future = loop.create_future()

		def cb(s=True):
			if s:
				loop.call_soon_threadsafe(self.future.set_result, None)
			else:
				loop.call_soon_threadsafe(self.future.set_exception, PulseOperationFailed())

		if not self.raw:
			cb = c.PA_CONTEXT_SUCCESS_CB_T(lambda ctx, s, d, cb=cb: cb(s))
		self.async_pulse.waiting_futures.add(self.future)
		return cb

	async def __aexit__(self, exc_type, exc_val, exc_tb):
		try:
			if not exc_type:
				await self.future
		finally:
			self.async_pulse.waiting_futures.discard(self.future)


class PulseAsync(object):

	_ctx = None

	def __init__(self, client_name=None, server=None, loop: Optional[asyncio.AbstractEventLoop] = None):
		'''Connects to specified pulse server by default.
			Specifying "connect=False" here prevents that, but be sure to call connect() later.
			"connect=False" can also be used here to
				have control over options passed to connect() method.'''
		if loop:
			warn("The parameter 'loop' is deprecated and will be removed in future versions, since it is no longer necessary", DeprecationWarning)

		self.name = client_name or 'pulsectl'
		self.server = server
		self._connected = asyncio.Event(**({"loop": loop} if sys.version_info[:2] < (3, 8) else {}))
		self._disconnected = asyncio.Event(**({"loop": loop} if sys.version_info[:2] < (3, 8) else {}))
		self._disconnected.set()
		self._ctx = self._loop = None
		self.init(loop)

	def init(self, loop: Optional[asyncio.AbstractEventLoop]):
		self._pa_state_cb = c.PA_STATE_CB_T(self._pulse_state_cb)
		self._pa_subscribe_cb = c.PA_SUBSCRIBE_CB_T(self._pulse_subscribe_cb)

		self._loop = PythonMainLoop(loop or asyncio.get_event_loop())

		self._ctx_init()
		self.event_types = sorted(PulseEventTypeEnum._values.values())
		self.event_facilities = sorted(PulseEventFacilityEnum._values.values())
		self.event_masks = sorted(PulseEventMaskEnum._values.values())
		self.event_callback = None
		self.waiting_futures = set()

		chan_names = dict()
		for n in range(256):
			name = c.pa.channel_position_to_string(n)
			if name is None: break
			chan_names[n] = name
		self.channel_list_enum = Enum('channel_pos', chan_names)

	def _ctx_init(self):
		if self._ctx:
			self.disconnect()
			c.pa.context_unref(self._ctx)
		self._ctx = c.pa.context_new(self._loop.api_pointer, self.name)
		self._connected.clear()
		self._disconnected.clear()
		c.pa.context_set_state_callback(self._ctx, self._pa_state_cb, None)
		c.pa.context_set_subscribe_callback(self._ctx, self._pa_subscribe_cb, None)

	async def connect(self, autospawn=False, wait=False, timeout=None):
		'''Connect to pulseaudio server.
			"autospawn" option will start new pulse daemon, if necessary.
			Specifying "wait" option will make function block until pulseaudio server appears.
			"timeout" (in seconds) will raise asyncio.TimeoutError if connection not established within it.'''
		if self._connected.is_set() or self._disconnected.is_set():
			self._ctx_init()
		flags = 0
		if not autospawn:
			flags |= c.PA_CONTEXT_NOAUTOSPAWN
		if wait:
			flags |= c.PA_CONTEXT_NOFAIL
		try:
			c.pa.context_connect(self._ctx, self.server, flags, None)
			if not timeout:
				await self._wait_disconnect_or(self._connected.wait())
			else:
				await asyncio.wait_for(self._wait_disconnect_or(self._connected.wait()), timeout)
		except (c.pa.CallError, PulseDisconnected) as e:
			self._disconnected.set()
			raise PulseError('Failed to connect to pulseaudio server') from e
		except asyncio.TimeoutError:
			self.disconnect()
			await self._disconnected.wait()
			raise

	@property
	def connected(self):
		return self._connected.is_set()

	def disconnect(self):
		if not self._ctx or self._disconnected.is_set():
			return
		c.pa.context_disconnect(self._ctx)

	def close(self):
		if not self._loop: return
		try:
			self.disconnect()
			c.pa.context_unref(self._ctx)
			self._loop.stop(0)
		finally: self._ctx = self._loop = None

	def __enter__(self): return self
	def __exit__(self, err_t, err, err_tb): self.close()

	async def __aenter__(self):
		await self.connect()
		return self

	async def __aexit__(self, err_t, err, err_tb):
		self.close()

	async def _wait_disconnect_or(self, coroutine: Coroutine):
		loop = asyncio.get_event_loop()
		wait_disconnected = loop.create_task(self._disconnected.wait())
		other_task = loop.create_task(coroutine)
		try:
			done, pending = await asyncio.wait((wait_disconnected, other_task), return_when=asyncio.FIRST_COMPLETED)
		except BaseException:  # Catches all Exception subclasses *and* (more important) CancelledError
			for task in (wait_disconnected, other_task):
				task.cancel()
			raise
		for task in pending:
			task.cancel()
		if other_task in pending:
			raise PulseDisconnected()
		else:
			return other_task.result()

	def _pulse_state_cb(self, ctx, _userdata):
		state = c.pa.context_get_state(ctx)
		if state >= c.PA_CONTEXT_READY:
			if state == c.PA_CONTEXT_READY:
				self._disconnected.clear()
				self._connected.set()
			elif state in [c.PA_CONTEXT_FAILED, c.PA_CONTEXT_TERMINATED]:
				self._connected.clear()
				self._disconnected.set()
				for future in self.waiting_futures:
					future.set_exception(PulseDisconnected())

	def _pulse_subscribe_cb(self, ctx, ev, idx, userdata):
		if not self.event_callback: return
		n = ev & c.PA_SUBSCRIPTION_EVENT_FACILITY_MASK
		ev_fac = PulseEventFacilityEnum._c_val(n, 'ev.facility.{}'.format(n))
		n = ev & c.PA_SUBSCRIPTION_EVENT_TYPE_MASK
		ev_t = PulseEventTypeEnum._c_val(n, 'ev.type.{}'.format(n))
		try: self.event_callback(PulseEventInfo(ev_t, ev_fac, idx))
		except PulseLoopStop: self._loop_stop = True

	def _pulse_info_cb(self, info_cls, data_list, done_cb, ctx, info, eof, userdata):
		# No idea where callbacks with "userdata != NULL" come from,
		#  but "info" pointer in them is always invalid, so they are discarded here.
		# Looks like some kind of mixup or corruption in libpulse memory?
		# See also: https://github.com/mk-fg/python-pulse-control/issues/35
		if userdata is not None: return
		# Empty result list and conn issues are checked elsewhere.
		# Errors here are non-descriptive (errno), so should not be useful anyway.
		# if eof < 0: done_cb(s=False)
		if eof: done_cb()
		else: data_list.append(info_cls(info[0]))

	def _pulse_get_list(cb_t, pulse_func, info_cls, singleton=False, index_arg=True):
		async def _wrapper_method(self, index=None):
			data = list()
			async with _pulse_op_cb(self, raw=True) as cb:
				cb = cb_t(
					ft.partial(self._pulse_info_cb, info_cls, data, cb) if not singleton else
					lambda ctx, info, userdata, cb=cb: data.append(info_cls(info[0])) or cb() )
				try:
					pa_op = pulse_func( self._ctx,
						*([index, cb, None] if index is not None else [cb, None]) )
				except c.ArgumentError as err: raise TypeError(err.args)
				except c.pa.CallError as err: raise PulseOperationInvalid(err.args[-1])
			c.pa.operation_unref(pa_op)
			data = data or list()
			if index is not None or singleton:
				if not data: raise PulseIndexError(index)
				data, = data
			return data
		_wrapper_method.__name__ = '...'
		_wrapper_method.__doc__ = 'Signature: func({})'.format(
			'' if pulse_func.__name__.endswith('_list') or singleton or not index_arg else 'index' )
		return _wrapper_method

	get_sink_by_name = _pulse_get_list(
		c.PA_SINK_INFO_CB_T,
		c.pa.context_get_sink_info_by_name, PulseSinkInfo )
	get_source_by_name = _pulse_get_list(
		c.PA_SOURCE_INFO_CB_T,
		c.pa.context_get_source_info_by_name, PulseSourceInfo )
	get_card_by_name = _pulse_get_list(
		c.PA_CARD_INFO_CB_T,
		c.pa.context_get_card_info_by_name, PulseCardInfo )

	sink_input_list = _pulse_get_list(
		c.PA_SINK_INPUT_INFO_CB_T,
		c.pa.context_get_sink_input_info_list, PulseSinkInputInfo )
	sink_input_info = _pulse_get_list(
		c.PA_SINK_INPUT_INFO_CB_T,
		c.pa.context_get_sink_input_info, PulseSinkInputInfo )
	source_output_list = _pulse_get_list(
		c.PA_SOURCE_OUTPUT_INFO_CB_T,
		c.pa.context_get_source_output_info_list, PulseSourceOutputInfo )
	source_output_info = _pulse_get_list(
		c.PA_SOURCE_OUTPUT_INFO_CB_T,
		c.pa.context_get_source_output_info, PulseSourceOutputInfo )

	sink_list = _pulse_get_list(
		c.PA_SINK_INFO_CB_T, c.pa.context_get_sink_info_list, PulseSinkInfo )
	sink_info = _pulse_get_list(
		c.PA_SINK_INFO_CB_T, c.pa.context_get_sink_info_by_index, PulseSinkInfo )
	source_list = _pulse_get_list(
		c.PA_SOURCE_INFO_CB_T, c.pa.context_get_source_info_list, PulseSourceInfo )
	source_info = _pulse_get_list(
		c.PA_SOURCE_INFO_CB_T, c.pa.context_get_source_info_by_index, PulseSourceInfo )
	card_list = _pulse_get_list(
		c.PA_CARD_INFO_CB_T, c.pa.context_get_card_info_list, PulseCardInfo )
	card_info = _pulse_get_list(
		c.PA_CARD_INFO_CB_T, c.pa.context_get_card_info_by_index, PulseCardInfo )
	client_list = _pulse_get_list(
		c.PA_CLIENT_INFO_CB_T, c.pa.context_get_client_info_list, PulseClientInfo )
	client_info = _pulse_get_list(
		c.PA_CLIENT_INFO_CB_T, c.pa.context_get_client_info, PulseClientInfo )
	server_info = _pulse_get_list(
		c.PA_SERVER_INFO_CB_T, c.pa.context_get_server_info, PulseServerInfo, singleton=True )
	module_info = _pulse_get_list(
		c.PA_MODULE_INFO_CB_T, c.pa.context_get_module_info, PulseModuleInfo )
	module_list = _pulse_get_list(
		c.PA_MODULE_INFO_CB_T, c.pa.context_get_module_info_list, PulseModuleInfo )

	def _pulse_method_call(pulse_op, func=None, index_arg=True):
		'''Creates following synchronous wrapper for async pa_operation callable:
			wrapper(index, ...) -> pulse_op(index, [*]args_func(...))
			index_arg=False: wrapper(...) -> pulse_op([*]args_func(...))'''
		async def _wrapper(self, *args, **kws):
			if index_arg:
				if 'index' in kws: index = kws.pop('index')
				else: index, args = args[0], args[1:]
			pulse_args = func(*args, **kws) if func else list()
			if not is_list(pulse_args): pulse_args = [pulse_args]
			if index_arg: pulse_args = [index] + list(pulse_args)
			async with _pulse_op_cb(self) as cb:
				try: pulse_op(self._ctx, *(list(pulse_args) + [cb, None]))
				except c.ArgumentError as err: raise TypeError(err.args)
				except c.pa.CallError as err: raise PulseOperationInvalid(err.args[-1])

		signature = inspect.signature(func or (lambda: None))
		if index_arg:
			signature.replace(
				parameters=
				[inspect.Parameter("index", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
				+ list(signature.parameters.values()))
		_wrapper.__name__ = '...'
		_wrapper.__doc__ = 'Signature: func' + str(signature)
		if func.__doc__: _wrapper.__doc__ += '\n\n' + func.__doc__
		return _wrapper

	card_profile_set_by_index = _pulse_method_call(
		c.pa.context_set_card_profile_by_index, lambda profile_name: profile_name )

	sink_default_set = _pulse_method_call(
		c.pa.context_set_default_sink, index_arg=False,
		func=lambda sink: sink.name if isinstance(sink, PulseSinkInfo) else sink )
	source_default_set = _pulse_method_call(
		c.pa.context_set_default_source, index_arg=False,
		func=lambda source: source.name if isinstance(source, PulseSourceInfo) else source )

	sink_input_mute = _pulse_method_call(
		c.pa.context_set_sink_input_mute, lambda mute=True: mute )
	sink_input_move = _pulse_method_call(
		c.pa.context_move_sink_input_by_index, lambda sink_index: sink_index )
	sink_mute = _pulse_method_call(
		c.pa.context_set_sink_mute_by_index, lambda mute=True: mute )
	sink_input_volume_set = _pulse_method_call(
		c.pa.context_set_sink_input_volume, lambda vol: vol.to_struct() )
	sink_volume_set = _pulse_method_call(
		c.pa.context_set_sink_volume_by_index, lambda vol: vol.to_struct() )
	sink_suspend = _pulse_method_call(
		c.pa.context_suspend_sink_by_index, lambda suspend=True: suspend )
	sink_port_set = _pulse_method_call(
		c.pa.context_set_sink_port_by_index,
		lambda port: port.name if isinstance(port, PulsePortInfo) else port )

	source_output_mute = _pulse_method_call(
		c.pa.context_set_source_output_mute, lambda mute=True: mute )
	source_output_move = _pulse_method_call(
		c.pa.context_move_source_output_by_index, lambda sink_index: sink_index )
	source_mute = _pulse_method_call(
		c.pa.context_set_source_mute_by_index, lambda mute=True: mute )
	source_output_volume_set = _pulse_method_call(
		c.pa.context_set_source_output_volume, lambda vol: vol.to_struct() )
	source_volume_set = _pulse_method_call(
		c.pa.context_set_source_volume_by_index, lambda vol: vol.to_struct() )
	source_suspend = _pulse_method_call(
		c.pa.context_suspend_source_by_index, lambda suspend=True: suspend )
	source_port_set = _pulse_method_call(
		c.pa.context_set_source_port_by_index,
		lambda port: port.name if isinstance(port, PulsePortInfo) else port )


	async def module_load(self, name, args=''):
		if is_list(args): args = ' '.join(args)
		name, args = map(c.force_bytes, [name, args])
		data = list()
		async with _pulse_op_cb(self, raw=True) as cb:
			cb = c.PA_CONTEXT_INDEX_CB_T(
				lambda ctx, index, userdata, cb=cb: data.append(index) or cb() )
			try: c.pa.context_load_module(self._ctx, name, args, cb, None)
			except c.pa.CallError as err: raise PulseOperationInvalid(err.args[-1])
		index, = data
		if index == c.PA_INVALID:
			raise PulseError('Failed to load module: {} {}'.format(name, args))
		return index

	module_unload = _pulse_method_call(c.pa.context_unload_module, None)


	async def stream_restore_test(self):
		'Returns module-stream-restore version int (e.g. 1) or None if it is unavailable.'
		data = list()
		async with _pulse_op_cb(self, raw=True) as cb:
			cb = c.PA_EXT_STREAM_RESTORE_TEST_CB_T(
				lambda ctx, version, userdata, cb=cb: data.append(version) or cb() )
			try: c.pa.ext_stream_restore_test(self._ctx, cb, None)
			except c.pa.CallError as err: raise PulseOperationInvalid(err.args[-1])
		version, = data
		return version if version != c.PA_INVALID else None

	stream_restore_read = _pulse_get_list(
		c.PA_EXT_STREAM_RESTORE_READ_CB_T,
		c.pa.ext_stream_restore_read, PulseExtStreamRestoreInfo, index_arg=False )
	stream_restore_list = stream_restore_read # for consistency with other *_list methods

	@ft.partial(_pulse_method_call, c.pa.ext_stream_restore_write, index_arg=False)
	def stream_restore_write( obj_name_or_list,
			mode='merge', apply_immediately=False, **obj_kws ):
		'''Update module-stream-restore db entry for specified name.
			Can be passed PulseExtStreamRestoreInfo object or list of them as argument,
				or name string there and object init keywords (e.g. volume, mute, channel_list, etc).
			"mode" is PulseUpdateEnum value of
				'merge' (default), 'replace' or 'set' (replaces ALL entries!!!).'''
		mode = PulseUpdateEnum[mode]._c_val
		if is_str(obj_name_or_list):
			obj_name_or_list = PulseExtStreamRestoreInfo(obj_name_or_list, **obj_kws)
		if isinstance(obj_name_or_list, PulseExtStreamRestoreInfo):
			obj_name_or_list = [obj_name_or_list]
		# obj_array is an array of structs, laid out contiguously in memory, not pointers
		obj_array = (c.PA_EXT_STREAM_RESTORE_INFO * len(obj_name_or_list))()
		for n, obj in enumerate(obj_name_or_list):
			obj_struct, dst_struct = obj.to_struct(), obj_array[n]
			for k,t in obj_struct._fields_: setattr(dst_struct, k, getattr(obj_struct, k))
		return mode, obj_array, len(obj_array), int(bool(apply_immediately))

	@ft.partial(_pulse_method_call, c.pa.ext_stream_restore_delete, index_arg=False)
	def stream_restore_delete(obj_name_or_list):
		'''Can be passed string name,
			PulseExtStreamRestoreInfo object or a list of any of these.'''
		if is_str(obj_name_or_list, PulseExtStreamRestoreInfo):
			obj_name_or_list = [obj_name_or_list]
		name_list = list((obj.name if isinstance( obj,
			PulseExtStreamRestoreInfo ) else obj) for obj in obj_name_or_list)
		name_struct = (c.c_char_p * len(name_list))()
		name_struct[:] = list(map(c.force_bytes, name_list))
		return [name_struct]


	async def default_set(self, obj):
		'Set passed sink or source to be used as default one by pulseaudio server.'
		assert_pulse_object(obj)
		method = {
			PulseSinkInfo: self.sink_default_set,
			PulseSourceInfo: self.source_default_set }.get(type(obj))
		if not method: raise NotImplementedError(type(obj))
		await method(obj)

	async def mute(self, obj, mute=True):
		assert_pulse_object(obj)
		method = {
			PulseSinkInfo: self.sink_mute,
			PulseSinkInputInfo: self.sink_input_mute,
			PulseSourceInfo: self.source_mute,
			PulseSourceOutputInfo: self.source_output_mute }.get(type(obj))
		if not method: raise NotImplementedError(type(obj))
		await method(obj.index, mute)
		obj.mute = mute

	async def port_set(self, obj, port):
		assert_pulse_object(obj)
		method = {
			PulseSinkInfo: self.sink_port_set,
			PulseSourceInfo: self.source_port_set }.get(type(obj))
		if not method: raise NotImplementedError(type(obj))
		await method(obj.index, port)
		obj.port_active = port

	async def card_profile_set(self, card, profile):
		assert_pulse_object(card)
		if is_str(profile):
			profile_dict = dict((p.name, p) for p in card.profile_list)
			if profile not in profile_dict:
				raise PulseIndexError( 'Card does not have'
					' profile with specified name: {!r}'.format(profile) )
			profile = profile_dict[profile]
		await self.card_profile_set_by_index(card.index, profile.name)
		card.profile_active = profile

	async def volume_set(self, obj, vol):
		assert_pulse_object(obj)
		method = {
			PulseSinkInfo: self.sink_volume_set,
			PulseSinkInputInfo: self.sink_input_volume_set,
			PulseSourceInfo: self.source_volume_set,
			PulseSourceOutputInfo: self.source_output_volume_set }.get(type(obj))
		if not method: raise NotImplementedError(type(obj))
		await method(obj.index, vol)
		obj.volume = vol

	async def volume_set_all_chans(self, obj, vol):
		assert_pulse_object(obj)
		obj.volume.value_flat = vol
		await self.volume_set(obj, obj.volume)

	async def volume_change_all_chans(self, obj, inc):
		assert_pulse_object(obj)
		obj.volume.values = [max(0, v + inc) for v in obj.volume.values]
		await self.volume_set(obj, obj.volume)

	async def volume_get_all_chans(self, obj):
		# Purpose of this func can be a bit confusing, being here next to set/change ones
		'''Get "flat" volume float value for info-object as a mean of all channel values.
			Note that this DOES NOT query any kind of updated values from libpulse,
				and simply returns value(s) stored in passed object, i.e. same ones for same object.'''
		assert_pulse_object(obj)
		return obj.volume.value_flat

	async def _event_mask_set(self, *masks):
		mask = 0
		for m in masks: mask |= PulseEventMaskEnum[m]._c_val
		async with _pulse_op_cb(self) as cb:
			c.pa.context_subscribe(self._ctx, mask, cb, None)

	async def subscribe_events(self, *masks) -> AsyncIterator[PulseEventInfo]:
		'''Subscribes to PulseAudio events with the given event masks and creates an asynchronous
				iterator, yielding the events as they are received from the server.
			This method is an alternative to `event_callback_set` and `event_mask_set`.

			Raises a `PulseDisconnect` exception when the connection to the server is lost.
			Raises StopIteration (returns silently from for loop) when the event subscription is
				cancelled via `event_callback_set(None)`'''
		if self.event_callback is not None:
			raise RuntimeError('Only a single subscribe_events generator can be used at a time.')
		queue = asyncio.Queue()
		self.event_callback = queue.put_nowait
		try:
			await self._event_mask_set(*masks)
			while True:
				yield await self._wait_disconnect_or(queue.get())
		finally:
			self.event_callback = None
			if self._connected.is_set():
				await self._event_mask_set('null')

	async def get_peak_sample(self, source, timeout, stream_idx=None):
		'''Returns peak (max) value in 0-1.0 range for samples in source/stream within timespan.
			"source" can be either int index of pulseaudio source
				(i.e. source.index), its name (source.name), or None to use default source.
			Resulting value is what pulseaudio returns as
				PA_SAMPLE_FLOAT32NE float after "timeout" seconds.
			If specified source does not exist, 0 should be returned after timeout.
			This can be used to detect if there's any sound
				on the microphone or any sound played through a sink via its monitor_source index,
				or same for any specific stream connected to these (if "stream_idx" is passed).
			Sample stream masquerades as
				application.id=org.PulseAudio.pavucontrol to avoid being listed in various mixer apps.
			Example - get peak for specific sink input "si" for 0.8 seconds:
				await pulse.get_peak_sample(await pulse.sink_info(si.sink).monitor_source, 0.8, si.index)'''
		samples = [0.0]

		async def subscriber():
			async for volume in self.subscribe_peak_sample(source, 25, stream_idx):
				samples[0] = max(samples[0], volume)

		task = asyncio.get_event_loop().create_task(subscriber())
		try:
			await asyncio.wait_for(task, timeout)
		except asyncio.TimeoutError:
			task.cancel()
			with suppress(asyncio.CancelledError):
				await task

		return min(1.0, samples[0])

	async def subscribe_peak_sample(self, source, rate=25, stream_idx=None, allow_suspend=False
			) -> AsyncIterator[float]:
		"""
		Subscribe to a (downsampled) audio stream to monitor audio volume.

		Using PulseAudio's `stream_connect_record` method, the stream can either be a normal record stream from a
		source, a stream from a sink monitor source or a monitor stream of a specific sink input. To monitor the volume,
		we use the PA_SAMPLE_FLOAT32NE sample format. This method returns an asynchronous generator, which yields the
		volume samples as they are streamed from the PulseAudio server.

		Example usage for monitoring a source (e.g. microphone input) with 5Hz::

		  async for volume in pulse.subscribe_peak_sample(source.name, rate=5):
		      print("volume =", volume)

		Example usage for monitoring a sink input (i.e. application output):

		  async for volume in pulse.subscribe_peak_sample((await pulse.sink_info(sink_input.sink)).monitor_source,
		                                                  stream_idx=sink_input.index):
		      print("volume = ", volume)

		:param source: Name (!) of the source to monitor its volume. Use `PulseSinkInfo.monitor_source` to get the
			correct source name for monitoring a sink or an input of that sink.
		:param rate: Sample rate, i.e. rate of volume measurements yielded by the generator in 1/second
		:param stream_idx: When `source` is a sink monitor source, specify the index (!) of a sink input, to monitor
			this single sink input stream instead of the sink sum signal.
		:param allow_suspend: If True, the flat DONT_INHIBIT_AUTO_SUSPEND is set on the stream, such that Pulse Audio
			will automatically suspend the source or sink after some seconds, despite our monitor stream running. This
			is useful for monitoring sinks, but prevents actively monitoring sources for more than a few seconds.
		"""
		proplist = c.pa.proplist_from_string('')
		ss = c.PA_SAMPLE_SPEC(format=c.PA_SAMPLE_FLOAT32NE, rate=rate, channels=1)
		s = c.pa.stream_new_with_proplist(self._ctx, 'peak detect', c.byref(ss), None, proplist)
		queue = asyncio.Queue()
		c.pa.proplist_free(proplist)

		@c.PA_STREAM_REQUEST_CB_T
		def read_cb(s, nbytes, _userdata):
			bs = c.c_int(nbytes)
			buff = c.c_void_p()
			c.pa.stream_peek(s, buff, c.byref(bs))
			try:
				if not buff or bs.value < 4:
					return
				queue.put_nowait(c.cast(buff, c.POINTER(c.c_float))[0])
			finally:
				# stream_drop() flushes buffered data (incl. buff=NULL "hole" data)
				# stream.h: "should not be called if the buffer is empty"
				if bs.value:
					c.pa.stream_drop(s)

		if stream_idx is not None:
			c.pa.stream_set_monitor_stream(s, stream_idx)
		c.pa.stream_set_read_callback(s, read_cb, None)
		if source is not None:
			source = unicode(source).encode('utf-8')

		flags = c.PA_STREAM_DONT_MOVE | c.PA_STREAM_PEAK_DETECT | c.PA_STREAM_ADJUST_LATENCY
		if allow_suspend:
			flags |= c.PA_STREAM_DONT_INHIBIT_AUTO_SUSPEND
		try:
			c.pa.stream_connect_record(
				s, source,
				c.PA_BUFFER_ATTR(fragsize=4, maxlength=2**32-1),
				flags)
		except c.pa.CallError:
			c.pa.stream_unref(s)
			raise

		try:
			while True:
				yield await self._wait_disconnect_or(queue.get())
		finally:
			try:
				c.pa.stream_disconnect(s)
			except c.pa.CallError:
				pass  # stream was removed
			c.pa.stream_unref(s)

	async def play_sample(self, name, sink=None, volume=1.0, proplist_str=None):
		'''Play specified sound sample,
				with an optional sink object/name/index, volume and proplist string parameters.
			Sample must be stored on the server in advance, see e.g. "pacmd list-samples".
			See also libcanberra for an easy XDG theme sample loading, storage and playback API.'''
		if isinstance(sink, PulseSinkInfo): sink = sink.index
		sink = str(sink) if sink is not None else None
		proplist = c.pa.proplist_from_string(proplist_str) if proplist_str else None
		volume = int(round(volume*c.PA_VOLUME_NORM))
		async with _pulse_op_cb(self) as cb:
			try:
				if not proplist:
					c.pa.context_play_sample(self._ctx, name, sink, volume, cb, None)
				else:
					c.pa.context_play_sample_with_proplist(
						self._ctx, name, sink, volume, proplist, cb, None )
			except c.pa.CallError as err: raise PulseOperationInvalid(err.args[-1])
