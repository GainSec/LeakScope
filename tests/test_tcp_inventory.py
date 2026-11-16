"""Local sockets and deterministic clocks validate a bounded connect inventory."""
import importlib
import importlib.util
import socket
import threading
import time
from concurrent.futures import Future
from unittest.mock import patch

from django.test import SimpleTestCase


class ImmediateExecutor:
    def __init__(self,**kwargs):pass
    def submit(self,fn,*args):
        future=Future()
        try:future.set_result(fn(*args))
        except Exception as exc:future.set_exception(exc)
        return future
    def shutdown(self,**kwargs):pass


class TcpInventoryTests(SimpleTestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('leakscope.phase3.tcp_inventory'),'TCP inventory adapter must exist')
        return importlib.import_module('leakscope.phase3.tcp_inventory')

    def test_port_expressions_normalize_and_full_range_is_supported(self):
        module=self.module()
        normalized=module.validate_scan_options({'ports':'443, 80,81-83,80','rate_per_second':10,'max_seconds':30})
        self.assertEqual(normalized['ports'],'80-83,443')
        self.assertEqual(normalized['timeout_seconds'],.3)
        self.assertEqual(module.validate_scan_options({})['ports'],'1-65535')

    def test_invalid_options_and_nonliteral_addresses_cannot_connect(self):
        module=self.module()
        for options in [{'ports':'0'},{'ports':'65536'},{'ports':'80-'},{'ports':'80,,81'}, {'ports':'80;whoami'},
                        {'ports':'80'*501},{'ports':80},{'rate_per_second':0},{'rate_per_second':101},
                        {'rate_per_second':True},{'max_seconds':14},{'max_seconds':901},
                        {'timeout_seconds':0},{'timeout_seconds':1.1},{'timeout_seconds':float('nan')}, {'write':True}]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):module.validate_scan_options(options)
        with patch.object(module.socket,'socket',side_effect=AssertionError('No socket')):
            for address in ['example.com','https://127.0.0.1','127.0.0.1:80','::1%lo','0.0.0.0','224.0.0.1','255.255.255.255',
                            '::ffff:0.0.0.0','::ffff:224.0.0.1','::ffff:255.255.255.255']:
                with self.subTest(address=address):
                    with self.assertRaises(ValueError):module.scan_tcp(address,{'ports':'80'})

    def test_loopback_open_and_closed_ports_are_reported_without_application_data(self):
        module=self.module()
        with socket.socket() as listener,socket.socket() as closed:
            listener.bind(('127.0.0.1',0));listener.listen(2)
            closed.bind(('127.0.0.1',0))
            ports=[listener.getsockname()[1],closed.getsockname()[1]]
            result=module.scan_tcp('127.0.0.1',{'ports':','.join(map(str,ports)),'rate_per_second':100})
            listener.settimeout(1);connection,_=listener.accept()
            with connection:
                connection.settimeout(1)
                self.assertEqual(connection.recv(1),b'','Inventory must send no application bytes')
        self.assertEqual(result['open_ports'],[ports[0]])
        self.assertEqual(result['attempted'],2)
        self.assertEqual(result['requested'],2)
        self.assertFalse(result['partial'])
        self.assertIsNone(result['next_port'])
        self.assertEqual(result['remaining_ports'],'')

    def test_pacing_uses_configured_rate_without_bursting(self):
        module=self.module();clock=[0.0];starts=[]
        def probe(*args):
            starts.append(clock[0]);return {'attempted':True,'status':'closed'}
        with patch.object(module,'ThreadPoolExecutor',ImmediateExecutor),patch.object(module,'_probe',side_effect=probe),\
             patch.object(module.time,'monotonic',side_effect=lambda:clock[0]),\
             patch.object(module.time,'sleep',side_effect=lambda duration:clock.__setitem__(0,clock[0]+duration)):
            result=module.scan_tcp('127.0.0.1',{'ports':'1-3','rate_per_second':2})
        self.assertEqual(result['attempted'],3)
        self.assertTrue(all(later-earlier>=.499 for earlier,later in zip(starts,starts[1:])),starts)

    def test_cancel_before_first_probe_and_deadline_do_not_fake_attempts(self):
        module=self.module()
        with patch.object(module,'_probe',side_effect=AssertionError('No probe')):
            result=module.scan_tcp('127.0.0.1',{'ports':'80,443'},should_stop=lambda:True)
            self.assertEqual(result['attempted'],0)
            self.assertEqual(result['next_port'],80)
            self.assertEqual(result['remaining_ports'],'80,443')
            self.assertTrue(result['partial'])
            with patch.object(module.time,'monotonic',side_effect=[0,16,16,16,16,16]):
                result=module.scan_tcp('127.0.0.1',{'ports':'80','max_seconds':15})
            self.assertEqual(result['attempted'],0)
            self.assertTrue(result['partial'])

    def test_cancellation_resume_excludes_only_completed_work(self):
        module=self.module();completed=[]
        def probe(address,port,*args):completed.append(port);return {'attempted':True,'status':'open'}
        with patch.object(module,'ThreadPoolExecutor',ImmediateExecutor),patch.object(module,'_probe',side_effect=probe):
            result=module.scan_tcp('127.0.0.1',{'ports':'80-83','rate_per_second':100},should_stop=lambda:len(completed)>=2)
        self.assertEqual(result['attempted'],2)
        self.assertEqual(result['open_ports'],[80,81])
        self.assertEqual(result['next_port'],82)
        self.assertEqual(result['remaining_ports'],'82-83')

    def test_timeouts_are_attempted_but_never_claimed_open(self):
        module=self.module()
        with patch.object(module,'ThreadPoolExecutor',ImmediateExecutor),patch.object(module,'_probe',return_value={'attempted':True,'status':'timeout'}):
            result=module.scan_tcp('127.0.0.1',{'ports':'80','rate_per_second':100})
        self.assertEqual(result['attempted'],1)
        self.assertEqual(result['open_ports'],[])
        self.assertEqual(result['outcomes']['timeout'],1)
        self.assertIsNone(result['next_port'])

    def test_open_output_limit_is_explicit_and_retains_resume_work(self):
        module=self.module();clock=[0.0]
        with patch.object(module,'MAX_OPEN_PORTS',2),patch.object(module,'ThreadPoolExecutor',ImmediateExecutor),\
             patch.object(module,'_probe',return_value={'attempted':True,'status':'open'}),\
             patch.object(module.time,'monotonic',side_effect=lambda:clock[0]),\
             patch.object(module.time,'sleep',side_effect=lambda duration:clock.__setitem__(0,clock[0]+duration)):
            result=module.scan_tcp('127.0.0.1',{'ports':'1-5','rate_per_second':100})
        self.assertEqual(result['open_ports'],[1,2])
        self.assertTrue(result['partial'])
        self.assertTrue(result['output_limited'])
        self.assertEqual(result['remaining_ports'],'3-5')

    def test_ipv6_loopback_uses_numeric_destination(self):
        module=self.module()
        try:
            listener=socket.socket(socket.AF_INET6,socket.SOCK_STREAM);listener.bind(('::1',0));listener.listen(1)
        except OSError:
            self.skipTest('IPv6 loopback unavailable')
        with listener:
            port=listener.getsockname()[1]
            result=module.scan_tcp('::1',{'ports':str(port),'rate_per_second':100})
        self.assertEqual(result['open_ports'],[port])

    def test_callback_failure_is_reported_without_more_probes(self):
        module=self.module()
        def fail():raise RuntimeError('sensitive callback exception')
        result=module.scan_tcp('127.0.0.1',{'ports':'80'},should_stop=fail)
        self.assertTrue(result['partial'])
        self.assertEqual(result['attempted'],0)
        self.assertNotIn('sensitive',str(result['errors']))

    def test_pending_final_probe_waits_without_spinning_cancellation_checks(self):
        module=self.module();checks=[]
        def probe(*args):time.sleep(.06);return {'attempted':True,'status':'closed'}
        with patch.object(module,'_probe',side_effect=probe):
            result=module.scan_tcp('127.0.0.1',{'ports':'80','rate_per_second':100},should_stop=lambda:checks.append(True) or False)
        self.assertEqual(result['attempted'],1)
        self.assertLess(len(checks),30)

    def test_parallel_cancellation_preserves_a_hole_before_a_completed_port(self):
        module=self.module();stop=threading.Event()
        def probe(address,port,*args):
            if port==80:time.sleep(.06);return {'attempted':False,'status':'deadline'}
            stop.set();return {'attempted':True,'status':'closed'}
        with patch.object(module,'_probe',side_effect=probe):
            result=module.scan_tcp('127.0.0.1',{'ports':'80-82','rate_per_second':100},should_stop=stop.is_set)
        self.assertEqual(result['attempted'],1)
        self.assertEqual(result['completed_prefix'],0)
        self.assertEqual(result['remaining_ports'],'80,82')
        self.assertEqual(result['next_port'],80)

    def test_concurrent_probe_count_never_exceeds_the_bounded_pool(self):
        module=self.module();counts={'active':0,'maximum':0};lock=threading.Lock()
        def probe(*args):
            with lock:
                counts['active']+=1;counts['maximum']=max(counts['maximum'],counts['active'])
            time.sleep(.025)
            with lock:counts['active']-=1
            return {'attempted':True,'status':'closed'}
        with patch.object(module,'MAX_CONCURRENCY',2),patch.object(module,'_probe',side_effect=probe):
            result=module.scan_tcp('127.0.0.1',{'ports':'1-6','rate_per_second':100})
        self.assertEqual(result['attempted'],6)
        self.assertLessEqual(counts['maximum'],2)
        self.assertEqual(counts['active'],0)
