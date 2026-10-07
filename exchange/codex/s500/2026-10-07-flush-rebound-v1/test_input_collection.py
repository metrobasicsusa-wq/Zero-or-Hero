"""Focused checks for acquisition denominator and timestamp-quality accounting."""
import unittest
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from collect_minutes import coverage

class CoverageChecks(unittest.TestCase):
 def setUp(self):
  self.start=datetime(2026,1,2,9,30,tzinfo=ZoneInfo('America/New_York'));self.end=self.start+timedelta(minutes=390)
 def bar(self,minute=0,**kw):
  b={'t':(self.start+timedelta(minutes=minute)).astimezone(timezone.utc).isoformat().replace('+00:00','Z'),'o':10,'h':11,'l':9,'c':10.5,'v':100};b.update(kw);return b
 def measure(self,bars,complete=True,end=None):return coverage('2026-01-02','EXAMPLE',bars,self.start,end or self.end,complete,'complete' if complete else 'failed')
 def test_no_rows_preserves_missing_denominator_and_request_failure(self):
  r=self.measure([],False);self.assertEqual(len(r['missing_regular_session_timestamps']),390);self.assertFalse(r['request_complete']);self.assertEqual(r['returned_bars'],0)
 def test_duplicate_invalid_and_outside_are_not_silently_corrected(self):
  r=self.measure([self.bar(),self.bar(),self.bar(1,h=9),self.bar(390)])
  self.assertEqual(r['returned_bars'],4);self.assertEqual(len(r['duplicate_timestamps']),1);self.assertEqual(len(r['invalid_ohlcv_timestamps']),1);self.assertEqual(len(r['outside_regular_session_timestamps']),1);self.assertEqual(r['regular_session_timestamps_present'],2)
 def test_early_search_coverage_and_order(self):
  r=self.measure([self.bar(i) for i in reversed(range(90))]);self.assertEqual(r['early30_present'],30);self.assertEqual(r['search90_present'],90);self.assertEqual(len(r['out_of_order_bar_indices']),89)
 def test_early_close_respects_actual_minutes(self):
  r=self.measure([],end=self.start+timedelta(minutes=210));self.assertEqual(r['expected_regular_session_minutes'],210);self.assertEqual(len(r['missing_regular_session_timestamps']),210)
if __name__=='__main__':unittest.main(verbosity=2)
