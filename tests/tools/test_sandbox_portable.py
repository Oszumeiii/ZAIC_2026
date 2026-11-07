"""Real spawned-process execution, timeout cleanup and error propagation (no model/API)."""
import multiprocessing
import unittest

import pandas as pd

from app.tools.sandbox import CodeTimeout, _run_subprocess, run_code


class PortableSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tables = {'sales': pd.DataFrame({'amount': [2, 3]})}

    def test_real_code_and_helpers(self):
        code = ('result_type="number"\n'
                'result=float(to_number(tables["sales"]["amount"]).sum())')
        self.assertEqual(_run_subprocess(code, self.tables, '2026-08-27', 10), ('number', 5.0))

    def test_public_interface_does_not_mutate_original_tables(self):
        code = 'tables["sales"].loc[0,"amount"]=100\nresult_type="number"\nresult=100'
        self.assertEqual(run_code(code, self.tables, '2026-08-27', 10), ('number', 100))
        self.assertEqual(self.tables['sales'].iloc[0, 0], 2)

    def test_timeout_stops_the_worker(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with self.assertRaises(CodeTimeout):
            _run_subprocess('while True: pass', self.tables, '2026-08-27', 1)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    def test_key_error_propagates_for_model_repair(self):
        with self.assertRaises(KeyError):
            _run_subprocess('result=tables["missing"]', self.tables, '2026-08-27', 10)

    def test_large_result_does_not_deadlock_pipe(self):
        kind, values = _run_subprocess('result_type="list"\nresult=list(range(30000))',
                                       self.tables, '2026-08-27', 10)
        self.assertEqual(kind, 'list')
        self.assertEqual((len(values), values[-1]), (30000, 29999))

    def test_worker_exit_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, 'không trả kết quả'):
            _run_subprocess('import os\nos._exit(3)', self.tables, '2026-08-27', 10)


if __name__ == '__main__':
    unittest.main()
