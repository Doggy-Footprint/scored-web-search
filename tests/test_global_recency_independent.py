import copy
import json
import math
import io
from contextlib import redirect_stdout
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import _pathsetup  # noqa: F401
import srcscore as S
from srcscore_core import fetchers, util, cli


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


def expected_recency(policy, age, citations=0, field='ai'):
    for bonus in policy['recency']['fresh_bonuses']:
        if age < bonus['max_age_years']:
            return float(bonus['points'])
    cfg = policy['recency']
    decay = cfg['decay']['max_penalty'] * (
        1 - 0.5 ** ((age - cfg['decay']['grace_years']) /
                      policy['field_halflife_years'][field]))
    if citations >= cfg['classic_exemption_citations']:
        return max(decay, 0)
    if citations >= cfg['classic_softening']['citations']:
        decay *= cfg['classic_softening']['factor']
    return decay


class GlobalRecencyTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(util, 'datetime', FrozenDatetime)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.policy = S.load_policy()

    def row(self, url='https://example.org/article', date=None, injected=None,
            policy=None, use_net=False):
        item = {'url': url, 'title': 'Title'}
        if date is not None:
            item['date'] = date
        return S.score_one(item, policy or self.policy, S.NullCache(), 'ai',
                           use_net, injected)

    def test_json_date_preserved_and_legacy_inputs_unchanged(self):
        item = {'url': 'https://example.org/x', 'title': 'X', 'date': '2024-02-29'}
        self.assertEqual(S.parse_input(json.dumps([item])), [item])
        self.assertEqual(S.parse_input('["https://example.org/x"]'),
                         [{'url': item['url'], 'title': ''}])
        self.assertEqual(S.parse_input(item['url'] + ' | X'),
                         [{'url': item['url'], 'title': 'X'}])

    def test_date_elapsed_utc_leap_and_crossyear(self):
        cases = [('2025-12-31', 1.5), ('2024-02-29', 672.5),
                 ('2026-01-01T11:00:00Z', 1 / 24),
                 ('2026-01-01T13:00:00+02:00', 1 / 24)]
        for date, elapsed_days in cases:
            with self.subTest(date=date):
                self.assertAlmostEqual(util.date_age_years(date), elapsed_days / 365.25)
        for date in ('nonsense', '2025-02-29', '2026-01-01T12:00:01Z', '2030-01-01'):
            with self.subTest(date=date):
                self.assertIsNone(util.date_age_years(date))

    def test_each_non_scholarly_source_gets_recency_once_offline(self):
        for url in ('https://reuters.com/story', 'https://medium.com/post',
                    'https://news.ycombinator.com/item?id=123',
                    'https://github.com/example/project'):
            with self.subTest(url=url):
                dated = self.row(url, '2025-12-31')
                unknown = self.row(url)
                self.assertLess(dated['score'], 100)
                self.assertEqual(dated['score'] - unknown['score'], 4)
                self.assertEqual(dated['meta']['recency_points'], 4)
                self.assertEqual(dated['meta']['date_source'], 'input')
                self.assertEqual(dated['meta']['date'], '2025-12-31')
                self.assertNotIn('date-unknown', dated['flags'])

    def test_unknown_invalid_future_dates_no_recency(self):
        for date in (None, '', 'bad', '2025-02-29', '2027-01-01'):
            with self.subTest(date=date):
                row = self.row(date=date)
                self.assertIn('date-unknown', row['flags'])
                self.assertEqual(row['meta']['recency_points'], 0)
                self.assertIsNone(row['meta']['age_years'])

    def test_scholar_recency_once_preserves_math_and_fixture_priority(self):
        for age, citations in ((0.2, 10), (1.5, 10), (2, 10), (5, 10),
                               (5, 300), (5, 1000)):
            with self.subTest(age=age, citations=citations):
                sch = {'age_years': age, 'date': '2025-12-31', 'year': 2025,
                       'citations': citations, 'peer_reviewed': True}
                on = self.row(date='2025-12-31', injected={'scholar': sch})
                offpolicy = copy.deepcopy(self.policy)
                offpolicy['signals'] = {'recency_decay': False}
                off = self.row(date='2025-12-31', injected={'scholar': sch}, policy=offpolicy)
                points = expected_recency(self.policy, age, citations)
                self.assertAlmostEqual(on['meta']['recency_points'], points)
                self.assertEqual(on['meta']['date_source'], 'scholar-age')
                self.assertEqual(on['meta']['age_years'], age)
                self.assertEqual(on['score'], round(off['score'] + points, 1))

    def test_scholar_full_date_then_year_then_input(self):
        for sch, source in (({'date': '2020-06-15', 'year': 2025}, 'scholar'),
                            ({'date': 'bad', 'year': 2020}, 'scholar'),
                            ({'date': 'bad', 'year': 2030}, 'input')):
            with self.subTest(sch=sch):
                row = self.row(date='2025-12-31', injected={'scholar': dict(sch, citations=10)})
                self.assertEqual(row['meta']['date_source'], source)
                if source == 'input':
                    self.assertAlmostEqual(row['meta']['age_years'], 1.5 / 365.25)
                else:
                    self.assertGreater(row['meta']['age_years'], 5)

    def test_unknown_scholar_keeps_citation_age_fallback(self):
        row = self.row(injected={'scholar': {'citations': 10}})
        cum = min(20, 6.2 * math.log10(11))
        velocity = min(9, 4.2 * math.log10(1 + 10 / 3))
        self.assertEqual(row['score'], round(32 + cum + velocity, 1))
        self.assertIn('date-unknown', row['flags'])

    def test_api_dates_precede_input_only_for_the_dated_resource(self):
        for url, injection, source in (
            ('https://github.com/a/b', {'github': {'stars': 0, 'pushed_at': '2020-01-01T00:00:00Z'}}, 'github'),
            ('https://news.ycombinator.com/item?id=123', {'hn': {'points': 0, 'created_at': '2020-01-01T00:00:00Z'}}, 'hn'),
            ('https://example.org/article', {'hn': {'points': 10, 'created_at': '2020-01-01T00:00:00Z'}}, 'input'),
            ('https://github.com/a/b/blob/main/code.py', {'github': {'stars': 0, 'pushed_at': '2020-01-01T00:00:00Z'}}, 'input')):
            with self.subTest(url=url):
                row = self.row(url, '2025-12-31', injection)
                self.assertEqual(row['meta']['date_source'], source)
                self.assertEqual(row['meta']['recency_points'], 4 if source == 'input' else expected_recency(self.policy, row['meta']['age_years']))

    def test_external_hn_date_alone_is_unknown(self):
        row = self.row(injected={'hn': {'points': 10, 'created_at': '2025-12-31'}})
        self.assertIn('date-unknown', row['flags'])
        self.assertNotEqual(row['meta'].get('date_source'), 'hn')

    def test_invalid_api_date_falls_back_to_input(self):
        for date in ('bad', '2030-01-01'):
            with self.subTest(date=date):
                row = self.row('https://github.com/a/b', '2025-12-31',
                               {'github': {'stars': 0, 'pushed_at': date}})
                self.assertEqual(row['meta']['date_source'], 'input')
                self.assertEqual(row['meta']['recency_points'], 4)

    def test_no_net_uses_input_without_http(self):
        with patch.object(fetchers, 'http_json', side_effect=AssertionError('network forbidden')):
            row = self.row('https://github.com/a/b', '2025-12-31')
        self.assertEqual(row['meta']['date_source'], 'input')
        self.assertEqual(row['meta']['recency_points'], 4)
        self.assertIn('net:off', row['flags'])

    def test_date_age_drives_news_decay_and_field_half_life(self):
        age = (NOW - datetime(2020, 1, 1, tzinfo=timezone.utc)).total_seconds() / (365.25 * 86400)
        for field in self.policy['field_halflife_years']:
            with self.subTest(field=field):
                row = S.score_one({'url': 'https://example.org/article', 'date': '2020-01-01'},
                                  self.policy, S.NullCache(), field, False)
                points = expected_recency(self.policy, age, field=field)
                self.assertAlmostEqual(row['meta']['age_years'], age)
                self.assertAlmostEqual(row['meta']['recency_points'], points)
                self.assertEqual(row['score'], round(32 + points, 1))

    def test_modes_and_official_docs_switch(self):
        for mode in ('academic', 'news', 'non-academic', 'community-opinion', 'official-docs'):
            with self.subTest(mode=mode):
                policy = S.apply_mode(self.policy, mode)
                row = self.row(date='2025-12-31', policy=policy)
                unknown = self.row(policy=policy)
                if mode == 'official-docs':
                    self.assertEqual(row['score'], unknown['score'])
                    self.assertNotIn('date-unknown', unknown['flags'])
                else:
                    self.assertEqual(row['meta']['recency_points'], expected_recency(policy, 1.5 / 365.25))

    def test_mode_default_half_life_landmarks(self):
        for mode in ('academic', 'news', 'non-academic', 'community-opinion'):
            policy = S.apply_mode(self.policy, mode)
            field = policy['defaults']['field']
            grace = policy['recency']['decay']['grace_years']
            half_life = policy['field_halflife_years'][field]
            for multiples, points in ((1, -6), (2, -9)):
                with self.subTest(mode=mode, multiples=multiples):
                    age = grace + multiples * half_life
                    date = (NOW - timedelta(days=365.25 * age)).isoformat()
                    row = S.score_one({'url': 'https://example.org/article', 'date': date},
                                      policy, S.NullCache(), field, False)
                    self.assertAlmostEqual(row['meta']['recency_points'], points)
                    self.assertEqual(row['score'], round(32 + points, 1))

    def test_cli_no_net_dated_json(self):
        items = [{'url': 'https://example.org/fresh', 'date': '2025-12-31'},
                 {'url': 'https://example.org/old', 'date': '2020-01-01'}]
        output = io.StringIO()
        with patch.object(cli.sys, 'stdin', io.StringIO(json.dumps(items))), \
             patch.object(cli, 'Cache', return_value=S.NullCache()), \
             patch.object(cli, 'STATS', {'failed': 0, 'lookups': 0}), \
             patch.object(fetchers, 'http_json', side_effect=AssertionError('network forbidden')), \
             redirect_stdout(output):
            result = cli.main(['--no-net', '--format', 'json', '--workers', '1'])
        self.assertEqual(result, 0)
        rows = json.loads(output.getvalue())
        self.assertEqual(rows[0]['url'], items[0]['url'])
        self.assertEqual(rows[0]['score'], 36)
        self.assertLess(rows[1]['score'], 32)
        self.assertEqual(rows[1]['meta']['date_source'], 'input')

    def test_hn_invalid_or_failed_api_falls_through_to_input(self):
        for result in ({'points': 0, 'created_at': 'bad'},
                       {'points': 0, 'created_at': '2030-01-01'}, fetchers.FETCH_FAILED):
            with self.subTest(result=result), patch('srcscore_core.scoring.hn_item', return_value=result):
                row = self.row('https://news.ycombinator.com/item?id=123', '2025-12-31', use_net=True)
                self.assertEqual(row['meta']['date_source'], 'input')
                self.assertEqual(row['meta']['recency_points'], 4)

    def test_github_failed_api_falls_through_to_input(self):
        with patch('srcscore_core.scoring.github_repo', return_value=fetchers.FETCH_FAILED):
            row = self.row('https://github.com/a/b', '2025-12-31', use_net=True)
        self.assertEqual(row['meta']['date_source'], 'input')
        self.assertEqual(row['meta']['recency_points'], 4)

    def test_embedded_platform_names_are_not_platform_resources(self):
        for url, injected in (
            ('https://example.org/github.com/a/b', {'github': {'stars': 0, 'pushed_at': '2020-01-01'}}),
            ('https://evilnews.ycombinator.com/item?id=123', {'hn': {'points': 0, 'created_at': '2020-01-01'}})):
            with self.subTest(url=url):
                row = self.row(url, '2025-12-31', injected)
                self.assertEqual(row['meta']['date_source'], 'input')

    def test_recency_fetches_when_engagement_disabled(self):
        policy = copy.deepcopy(self.policy)
        policy['signals'] = {'engagement': False, 'recency_decay': True}
        cases = [('https://github.com/a/b', 'github_repo', {'stars': 9999, 'pushed_at': '2025-12-31'}, 'github'),
                 ('https://news.ycombinator.com/item?id=123', 'hn_item', {'points': 9999, 'created_at': '2025-12-31'}, 'hn')]
        for url, function, result, source in cases:
            with self.subTest(url=url), patch('srcscore_core.scoring.' + function, return_value=result) as fetch:
                row = self.row(url, policy=policy, use_net=True)
                fetch.assert_called_once()
                self.assertEqual(row['meta']['date_source'], source)
                self.assertFalse(any(s.startswith(('HN', '★')) for s in row['signals']))


class DateFetcherTests(unittest.TestCase):
    def test_github_date_is_retained(self):
        with patch.object(fetchers, 'http_json', return_value={'stargazers_count': 0, 'pushed_at': '2025-12-31T00:00:00Z'}):
            result = S.github_repo('a', 'b', S.NullCache(), 1)
        self.assertEqual(result['pushed_at'], '2025-12-31T00:00:00Z')

    def test_zero_point_hn_item_keeps_date(self):
        with patch.object(fetchers, 'http_json', return_value={'id': 123, 'points': 0, 'created_at': '2025-12-31T00:00:00Z', 'children': []}):
            result = S.hn_item('123', S.NullCache(), 1)
        self.assertIsNotNone(result)
        self.assertEqual(result['created_at'], '2025-12-31T00:00:00Z')
        self.assertEqual(result['points'], 0)

    def test_enriched_fetchers_do_not_reuse_old_cache_keys(self):
        class OldCache:
            def get(self, key):
                return {'stars': 12} if key == 'gh:a/b' else ({'points': 12} if key == 'hnitem:123' else None)
            def put(self, key, value):
                pass
        cases = [(S.github_repo, ('a', 'b'), {'stargazers_count': 0, 'pushed_at': '2025-12-31'}, 'pushed_at'),
                 (S.hn_item, ('123',), {'id': 123, 'points': 0, 'created_at': '2025-12-31'}, 'created_at')]
        for function, args, response, datekey in cases:
            with self.subTest(function=function.__name__), patch.object(fetchers, 'http_json', return_value=response) as http:
                result = function(*args, OldCache(), 1)
                http.assert_called_once()
                self.assertEqual(result[datekey], '2025-12-31')


if __name__ == '__main__':
    unittest.main()
