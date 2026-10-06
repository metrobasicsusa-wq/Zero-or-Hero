"""Regression checks added after the actual summary-null failure.

Evaluate the production monthly event-count expressions on synthetic rows.
These are not part of the original 62 pre-arithmetic tests.
"""
import ast,copy,unittest
from pathlib import Path

TREE=ast.parse(Path(__file__).with_name('run_feasibility.py').read_text())

def production_count(field,group):
    for node in ast.walk(TREE):
        if isinstance(node,ast.Dict):
            for key,value in zip(node.keys,node.values):
                if isinstance(key,ast.Constant) and key.value==field:
                    return eval(compile(ast.Expression(value),'<monthly-count-expression>','eval'),{'group':group})
    raise AssertionError('Monthly expression not found')

def rows(counts):
    return [{'source_event_evidence':{'bars_count':x,'trades_count':x}} for x in counts]

class IndependentSummaryNullTests(unittest.TestCase):
    def test_all_unknown_is_no_positive_records_not_an_exception(self):
        group=rows([None,None]);snapshot=copy.deepcopy(group)
        for field in ['rows_with_bar_records','rows_with_trade_records']:self.assertEqual(production_count(field,group),0)
        self.assertEqual(group,snapshot)
        self.assertIsNone(group[0]['source_event_evidence']['trades_count'])

    def test_zero_and_unknown_excluded_positive_rows_counted_once(self):
        group=rows([None,0,1,20,None,0])
        for field in ['rows_with_bar_records','rows_with_trade_records']:self.assertEqual(production_count(field,group),2)

    def test_bar_and_trade_availability_are_not_substitutes(self):
        group=[{'source_event_evidence':{'bars_count':1,'trades_count':None}},
               {'source_event_evidence':{'bars_count':0,'trades_count':5}},
               {'source_event_evidence':{'bars_count':None,'trades_count':2}}]
        self.assertEqual(production_count('rows_with_bar_records',group),1)
        self.assertEqual(production_count('rows_with_trade_records',group),2)

if __name__=='__main__':unittest.main(verbosity=2)
