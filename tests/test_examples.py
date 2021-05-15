import atexit
import os
import signal
import sys
import unittest
import io

from pulsectl.tests.test_with_dummy_instance import dummy_pulse_cleanup, dummy_pulse_init


class ExamplesTest(unittest.TestCase):
	proc = tmp_dir = None

	@classmethod
	def setUpClass(cls):
		assert not cls.proc and not cls.tmp_dir, [cls.proc, cls.tmp_dir]
		for sig in 'hup', 'term', 'int':
			signal.signal(getattr(signal, 'sig{}'.format(sig).upper()), lambda sig,frm: sys.exit())
		atexit.register(cls.tearDownClass)
		cls.instance_info = dummy_pulse_init()
		for k, v in cls.instance_info.items():
			setattr(cls, k, v)
		os.environ['XDG_RUNTIME_DIR'] = cls.tmp_dir

	@classmethod
	def tearDownClass(cls):
		dummy_pulse_cleanup(cls.instance_info)
		cls.proc = cls.tmp_dir = None

	def test_simple(self):
		import examples.simple_example

	def test_subscribe(self):
		import examples.subscribe_example

	def test_subscribe_peak(self):
		_stdout = sys.stdout
		sys.stdout = io.StringIO()
		try:
			import examples.subscribe_peak_example
		finally:
			sys.stdout = _stdout
