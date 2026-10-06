import copy
import json
import unittest
from news_analysis import analyze_candidate, article_key, build_global_versions, fingerprint, timestamp_ns


def candidate():
    return {'candidate_id':'2026-01-05__A','symbol':'A','date':'2026-01-05','chronological_index':1,
        'news_request_start':'2026-01-02T21:00:00+00:00','news_request_end':'2026-01-05T14:30:00+00:00'}


def article(**changes):
    a={'id':101,'headline':'Company says new contract signed','summary':'FDA designation and earnings outlook discussed',
       'created_at':'2026-01-05T13:00:00Z','updated_at':'2026-01-05T13:00:01Z',
       'symbols':['A'],'source':'source','url':'https://example.test/news/101','images':[]}
    a.update(changes);return a


def analyze(rows,other=None,fetch_complete=True):
    index=build_global_versions({'one':rows,'other':other or []})
    return analyze_candidate(candidate(),rows,index,fetch_complete)


class TimestampTests(unittest.TestCase):
    def test_exact_nanosecond_and_offset_equivalence(self):
        a=timestamp_ns('2026-01-05T14:30:00Z')
        self.assertEqual(timestamp_ns('2026-01-05T09:30:00-05:00'),a)
        self.assertEqual(timestamp_ns('2026-01-05T14:30:00.000000001Z'),a+1)
        self.assertEqual(timestamp_ns('2026-01-05T14:29:59.999999999Z'),a-1)

    def test_fraction_padding_and_pre_epoch(self):
        self.assertEqual(timestamp_ns('1970-01-01T00:00:00.1Z'),100000000)
        self.assertEqual(timestamp_ns('1969-12-31T23:59:59.999999999Z'),-1)

    def test_invalid_or_ambiguous_timestamps_rejected(self):
        for value in [None,1,True,'2026-01-05','2026-01-05T14:30:00','2026-01-05T14:30:00-00:00',
            '2026-01-05T14:30:00.0000000001Z','2026-02-30T14:30:00Z',
            '2026-01-05T24:00:00Z','2026-01-05T14:30:60Z','2026-01-05T14:30:00+25:00']:
            with self.subTest(value=value),self.assertRaises(ValueError):timestamp_ns(value)


class NewsAnalysisTests(unittest.TestCase):
    def test_clean_metadata_is_not_confirmed_catalyst(self):
        r=analyze([article()])
        self.assertEqual(r['status'],'metadata_ready_for_review')
        self.assertEqual(r['verified_catalyst_count'],0)
        self.assertFalse(r['articles'][0]['independently_verified_catalyst'])
        self.assertEqual(set(r['articles'][0]['keyword_hints']),{'contract','regulatory','earnings','guidance'})

    def test_strict_created_window_boundaries(self):
        for stamp in ['2026-01-02T21:00:00Z','2026-01-05T14:30:00Z','2026-01-05T14:30:00.000000001Z']:
            r=analyze([article(created_at=stamp,updated_at=stamp)])
            self.assertEqual(r['status'],'no_metadata_ready')
        r=analyze([article(created_at='2026-01-02T21:00:00.000000001Z')])
        self.assertEqual(r['status'],'metadata_ready_for_review')

    def test_nanosecond_before_open_allowed_but_at_open_not(self):
        r=analyze([article(created_at='2026-01-05T14:29:59.999999999Z',updated_at='2026-01-05T14:29:59.999999999Z')])
        self.assertEqual(r['status'],'metadata_ready_for_review')
        r=analyze([article(updated_at='2026-01-05T14:30:00.000000001Z')])
        self.assertIn('updated_not_strictly_before_open',r['articles'][0]['reasons'])

    def test_updated_equal_created_allowed(self):
        self.assertEqual(analyze([article(updated_at='2026-01-05T13:00:00Z')])['status'],'metadata_ready_for_review')

    def test_missing_malformed_and_reversed_updated_quarantined(self):
        for value,reason in [(None,'missing_updated_at'),('bad','malformed_updated_at'),
                             ('2026-01-05T12:59:59Z','updated_before_created')]:
            r=analyze([article(updated_at=value)])
            self.assertIn(reason,r['articles'][0]['reasons'])
            self.assertEqual(r['status'],'no_metadata_ready')

    def test_missing_created_quarantined(self):
        self.assertIn('missing_created_at',analyze([article(created_at=None)])['articles'][0]['reasons'])

    def test_exact_symbol_tag_required(self):
        for symbols in [['AA'],['a'],[],None,'A',['A',42]]:
            self.assertEqual(analyze([article(symbols=symbols)])['status'],'no_metadata_ready')
        self.assertEqual(analyze([article(symbols=['B','A'])])['status'],'metadata_ready_for_review')

    def test_bad_ids_never_certify_metadata(self):
        for identity in [None,True,False,1.0,[],{},'', '   ']:
            r=analyze([article(id=identity)])
            self.assertIn('invalid_news_id',r['articles'][0]['reasons'])
            self.assertIsNone(r['articles'][0]['article_key'])

    def test_integer_and_string_ids_allowed_but_same_numeric_identity(self):
        self.assertEqual(analyze([article(id='text-id')])['status'],'metadata_ready_for_review')
        self.assertEqual(article_key(article(id=101)),article_key(article(id='101')))
        r=analyze([article(id=101)],other=[article(id='101')])
        self.assertEqual(r['status'],'no_metadata_ready')

    def test_global_same_id_variant_quarantines_all_windows(self):
        original=article();changed=article(headline='Different headline')
        versions=build_global_versions({'one':[original],'two':[changed]})
        self.assertEqual(len(versions[article_key(original)]),2)
        for row in [original,changed]:
            r=analyze_candidate(candidate(),[row],versions)
            self.assertIn('conflicting_global_news_id_versions',r['articles'][0]['reasons'])

    def test_conflict_checks_entire_payload_not_only_headline(self):
        for changed in [article(summary='Changed summary'),article(images=['new']),article(author='new'),
                        article(updated_at='2026-01-05T13:00:02Z')]:
            self.assertEqual(analyze([article()],other=[changed])['status'],'no_metadata_ready')

    def test_future_version_also_quarantines_earlier_favorable_version(self):
        r=analyze([article()],other=[article(updated_at='2026-02-01T00:00:00Z')])
        self.assertEqual(r['status'],'no_metadata_ready')

    def test_exact_duplicates_collapse_with_all_row_indexes_preserved(self):
        r=analyze([article(),copy.deepcopy(article()),article()])
        self.assertEqual(r['input_article_rows'],3);self.assertEqual(r['unique_payload_rows'],1)
        self.assertEqual(r['exact_duplicate_rows_collapsed'],2)
        self.assertEqual(r['articles'][0]['input_row_indexes'],[0,1,2])
        self.assertEqual(r['metadata_ready_input_rows'],3)

    def test_exact_duplicate_other_window_is_not_conflict(self):
        self.assertEqual(analyze([article()],other=[article()])['status'],'metadata_ready_for_review')

    def test_invalid_duplicates_remain_counted_per_input_row(self):
        r=analyze([article(id=None),article(id=None)])
        self.assertEqual(r['row_reason_counts']['invalid_news_id'],2)
        self.assertEqual(r['articles'][0]['occurrences'],2)

    def test_no_articles_means_missing_source_not_no_event(self):
        r=analyze([])
        self.assertEqual(r['status'],'source_no_articles');self.assertEqual(r['source_evidence_status'],'source_missing')
        self.assertTrue(r['no_articles_is_not_no_event'])

    def test_incomplete_retrieval_has_priority_even_with_clean_record(self):
        r=analyze([article()],fetch_complete=False)
        self.assertEqual(r['status'],'retrieval_incomplete')
        self.assertEqual(r['metadata_ready_unique_payloads'],1)
        self.assertEqual(analyze([],fetch_complete=False)['status'],'retrieval_incomplete')

    def test_order_by_updated_cannot_cause_created_early_stop(self):
        rows=[article(id=1,created_at='2026-01-06T00:00:00Z',updated_at='2026-01-06T00:00:00Z'),article(id=2)]
        r=analyze(rows)
        self.assertEqual(r['status'],'metadata_ready_for_review')
        self.assertEqual(r['input_article_rows'],2);self.assertEqual(r['metadata_ready_unique_payloads'],1)

    def test_keywords_never_needed_for_time_clean_status(self):
        r=analyze([article(headline='Company leadership changes',summary='')])
        self.assertEqual(r['status'],'metadata_ready_for_review')
        self.assertEqual(r['articles'][0]['keyword_hints'],[])

    def test_empty_whitespace_or_missing_headline_quarantined(self):
        for headline in ('',' \t\n',None,42):
            r=analyze([article(headline=headline)])
            self.assertEqual(r['status'],'no_metadata_ready')
            self.assertIn('missing_or_empty_headline',r['articles'][0]['reasons'])

    def test_public_output_has_no_raw_headline_or_summary_or_id(self):
        a=article(headline='PRIVATE_HEADLINE_MARKER',summary='PRIVATE_SUMMARY_MARKER')
        r=analyze([a]);text=json.dumps(r)
        self.assertNotIn(a['headline'],text);self.assertNotIn(a['summary'],text)
        self.assertNotIn('"id":',text);self.assertEqual(r['articles'][0]['source_payload_sha256'],fingerprint(a))
        self.assertTrue(r['articles'][0]['headline_sha256'])

    def test_malformed_article_is_retained(self):
        r=analyze([None,[],42])
        self.assertEqual(r['unique_payload_rows'],3)
        self.assertEqual(r['row_reason_counts']['article_payload_not_object'],3)

    def test_missing_global_index_is_not_assumed_valid(self):
        r=analyze_candidate(candidate(),[article()],{})
        self.assertIn('payload_missing_from_global_version_index',r['articles'][0]['reasons'])

    def test_invalid_candidate_window_quarantines_metadata(self):
        c=candidate();c['news_request_start']=c['news_request_end']
        r=analyze_candidate(c,[article()],build_global_versions({'x':[article()]}))
        self.assertEqual(r['status'],'no_metadata_ready');self.assertIn('invalid_candidate_time_window',r['candidate_issues'])

    def test_json_key_order_is_not_a_payload_version(self):
        a=article();b=dict(reversed(list(a.items())))
        self.assertEqual(fingerprint(a),fingerprint(b))
        self.assertEqual(len(build_global_versions({'a':[a,b]})[article_key(a)]),1)


if __name__=='__main__':unittest.main()
