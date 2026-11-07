import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from astock.market import fundamentals as fund
from astock.market.earnings import build_earnings_packet, earnings_text, fetch_earnings_data
from astock.ai.ai_context import build_research_context
from astock.ai.evidence import context_evidence, with_web_evidence, audit_citations
from astock.ai import ai_agent
from astock.research import research_services as services
from astock.search.search_workflow import SearchReport, SearchBudget, research_search
from astock.search import search_tools


class TestFinancialPacket(unittest.TestCase):
    def test_nonempty_orderbook_does_not_skip_missing_valuation(self):
        ak = SimpleNamespace(stock_bid_ask_em=Mock(return_value=pd.DataFrame(
            {"item": ["buy_1", "最新"], "value": [80.19, 80.19]})))
        tx = {"市盈率": 20, "市净率": 3, "总市值": 150_000_000_000}
        with patch.object(fund, "_fetch_spot_tencent", return_value=tx) as backup:
            result = fund.fetch_spot_extra("002384", ak_mod=ak)
        backup.assert_called_once()
        self.assertEqual(result['buy_1'], 80.19)
        self.assertTrue(fund.has_valuation(result))

    def test_partial_valuation_merges_full_market_backup(self):
        ak = SimpleNamespace(stock_bid_ask_em=Mock(side_effect=RuntimeError()),
                             stock_zh_a_spot_em=Mock(return_value=pd.DataFrame([
                                 {"代码": "002384", "市盈率-动态": 21, "市净率": 3, "总市值": 150_000_000_000}])))
        with patch.object(fund, '_fetch_spot_tencent', return_value={"市盈率": 20, "市净率": "--"}):
            result = fund.fetch_spot_extra('002384', ak_mod=ak)
        self.assertEqual(fund._pick_val(result, '市盈率'), 20)
        self.assertEqual(fund._pick_val(result, '市净率'), 3)
        self.assertTrue(fund.has_valuation(result))

    def test_single_quarter_yoy_qoq_and_q4_reset(self):
        income = pd.DataFrame({
            '报告日': ['20250930', '20250630', '20250331', '20241231', '20240930', '20240630'],
            '营业收入': [450, 270, 100, 400, 280, 180],
            '营业成本': [300, 190, 80, 300, 220, 140],
            '归属于母公司股东的净利润': [54, 30, 10, 40, 28, 18],
            '净利润': [60, 33, 11, 44, 30, 20]})
        cash = pd.DataFrame({'报告日': ['20250930', '20250630'],
                             '经营活动产生的现金流量净额': [40, 20]})
        p = build_earnings_packet(pd.DataFrame(), {'income': income, 'cashflow': cash})
        q3 = p['quarters']['2025-09-30']
        self.assertEqual(q3['revenue'], 180)
        self.assertEqual(q3['parent_profit'], 24)
        self.assertAlmostEqual(q3['yoy']['revenue'], 80)
        self.assertAlmostEqual(q3['qoq']['revenue'], (180/170-1)*100)
        self.assertEqual(p['quarters']['2024-12-31']['revenue'], 120)
        self.assertEqual(p['quarters']['2025-03-31']['revenue'], 100)
        self.assertIn('经营现金流/合并净利润=0.74倍', earnings_text(p))

    def test_missing_quarter_and_negative_comparison_not_fabricated(self):
        abstract = pd.DataFrame({'报告期': ['2025-09-30', '2024-09-30', '2025-03-31'],
                                 '营业总收入': [300, 200, 100], '净利润': [30, -10, 1]})
        p = build_earnings_packet(abstract)
        self.assertNotIn('2025-09-30', p['quarters'])
        self.assertEqual(p['quarters']['2025-03-31']['abstract_profit'], 1)
        self.assertIsNone(p['quarters']['2025-03-31']['yoy']['abstract_profit'])

    def test_old_indicator_is_not_mislabeled_as_latest_report(self):
        indicator = pd.DataFrame({'日期': ['2024-12-31'], '销售毛利率(%)': [10]})
        abstract = pd.DataFrame({'报告期': ['2025-09-30'], '营业总收入': [300]})
        snap = fund.build_fundamentals(indicator, abstract)
        self.assertIsNone(snap['profit']['gross_margin'])
        self.assertEqual(snap['period'], '2025-09-30')
        self.assertIsNone(fund._to_num(True))

    def test_raw_statements_survive_abstract_failure_and_parent_only_rows_excluded(self):
        income = pd.DataFrame({'报告日': ['20250331', '20250331'],
                                '类型': ['合并累计', '母公司累计'], '营业收入': [100, 20]})
        snap = fund.build_fundamentals(pd.DataFrame(), pd.DataFrame(), earnings_data={'income': income})
        self.assertTrue(snap['ok'])
        self.assertIn('营收=100', fund.fundamentals_text(snap))

    def test_fetch_isolates_interface_failures(self):
        ak = SimpleNamespace(stock_financial_report_sina=Mock(side_effect=RuntimeError('private')),
                             stock_zygc_em=Mock(return_value=pd.DataFrame()))
        result = fetch_earnings_data('002384', ak_mod=ak)
        self.assertEqual(set(result['errors']), {'income', 'balance', 'cashflow', 'segments'})
        self.assertNotIn('private', str(result['errors']))


class TestEvidenceGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ASTOCK_DB_PATH': os.path.join(self.tmp.name, 'research.db')})
        self.env.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)

    @staticmethod
    def answer(ref='[F1]'):
        return ('## 业绩拆解\n营收增长 ' + ref + '\n## 盈利质量\n现金流需要复核\n'
                '## 下一跟踪指标\n跟踪应收账款\n## 待确认问题\n分部订单尚未获取')

    def test_context_keeps_company_and_fundamentals_beyond_recent_history(self):
        context = build_research_context({'technical_report': '技术' * 2500,
                                          'fundamentals': '营收100亿元', 'personal_thesis': '（尚未建立个人投资假设）'},
                                         instrument='东山精密（002384）')
        messages = [{'role': 'user', 'content': context}]
        messages += [{'role': 'user', 'content': '追问'}] * 10
        registry, scope = context_evidence(messages)
        self.assertEqual(scope['code'], '002384')
        self.assertIn('100亿元', scope['fundamentals'])
        self.assertFalse(registry['P1']['available'])
        self.assertEqual(audit_citations('[T37] [F1]', registry)['coverage'], 50)

    def test_many_urls_cannot_inflate_citations_or_bypass_partial_research(self):
        report = {'status': 'partial', 'gaps': ['原文未读'], 'sources': [], 'claims': []}
        services.archive_research_run('002384', '东山精密', '业绩', self.answer(),
                                      sources=[('链接', f'https://example.com/{i}') for i in range(30)],
                                      evidence_registry={'F1': {'available': True}}, search_report=report)
        run = services.list_research_runs('002384')[0]
        parsed = json.loads(run['structured_json'])
        self.assertEqual(parsed['citations'], ['[F1]'])
        self.assertEqual(run['status'], 'incomplete')
        self.assertFalse(parsed['verification']['complete'])
        self.assertIn('原文未读', parsed['verification']['gaps'])

    def test_fake_reference_and_placeholder_sections_cannot_pass(self):
        task = services.start_research_task('002384', '东山精密', '财报', 'earnings')
        answer = self.answer('[T999]').replace('营收增长', '数据不足')
        services.archive_research_run('002384', '东山精密', '财报', answer, task_id=task,
                                      evidence_registry={'F1': {'available': True}}, task_type='earnings')
        run = services.list_research_runs('002384')[0]
        self.assertEqual(run['coverage'], 0)
        self.assertEqual(run['status'], 'incomplete')
        parsed = json.loads(run['structured_json'])
        self.assertIn('supporting_facts', parsed['missing_fields'])
        stages = json.loads(services.list_research_tasks('002384')[0]['stage_json'])
        self.assertEqual(stages['cross_validate'], 'needs_review')

    def test_complete_earnings_answer_does_not_require_price_scenarios(self):
        services.archive_research_run('002384', '东山精密', '财报', self.answer(), task_type='earnings',
                                      evidence_registry={'F1': {'available': True}})
        self.assertEqual(services.list_research_runs('002384')[0]['status'], 'complete')

    def test_read_page_needs_real_quote_support_not_just_an_existing_url(self):
        report = {'status': 'complete', 'sources': [{'source_id': 'W1', 'read_status': 'read',
                                                    'body': '营业收入100亿元'}], 'claims': []}
        self.assertFalse(with_web_evidence({}, report)['W1']['available'])
        report['claims'] = [{'status': 'single_source', 'evidence': [{'source_id': 'W1', 'quote': '营业收入100亿元'}]}]
        self.assertTrue(with_web_evidence({}, report)['W1']['available'])


class TestResearchRecovery(unittest.TestCase):
    def test_first_query_cannot_fill_source_capacity_before_other_questions(self):
        report = research_search('研究两个子问题', config={},
                                 decision_fn=lambda *a: {'queries': ['first', 'second']},
                                 search_fn=lambda q, *a: [(f'{q}{i}', f'https://example.com/{q}/{i}', '') for i in range(20)],
                                 budget=SearchBudget(rounds=1, queries=2, sources=4, pages=0, recovery_pages=0))
        self.assertEqual(len(report.sources), 4)
        self.assertEqual({d['discovered_by'] for d in report.sources}, {'first', 'second'})

    def test_seeded_sources_keep_ids_claims_and_allow_new_read_budget(self):
        seeds = [{'source_id': 'W1', 'title': '已读财报', 'url': 'https://company.example/old',
                  'body': 'Original report revenue is 100 million dollars.', 'read_status': 'read'}]
        claims = [{'text': '旧财报营收', 'status': 'single_source',
                   'evidence': [{'source_id': 'W1', 'quote': '100 million dollars'}]}]
        report = research_search('补查现金流', config={}, seed_sources=seeds, seed_claims=claims,
                                 search_fn=lambda *a: [('新公告', 'https://company.example/new', '')],
                                 read_fn=lambda *a: 'The new official filing confirms operating cash flow is positive.',
                                 budget=SearchBudget(rounds=1, queries=1, pages=1))
        self.assertEqual(report.sources[0]['source_id'], 'W1')
        self.assertEqual(report.sources[1]['source_id'], 'W2')
        self.assertEqual(report.pages_used, 1)
        self.assertEqual(report.sources[1]['read_status'], 'read')
        self.assertEqual(report.claims[0]['evidence'][0]['source_id'], 'W1')

    def test_all_failed_reads_cannot_exceed_attempt_cap(self):
        counter = iter(range(100))
        def search(*a):
            return [(f'报道{i}', f'https://example.com/{i}', '') for i in [next(counter), next(counter), next(counter)]]
        read = Mock(return_value='读取失败：403')
        report = research_search('财报', config={}, search_fn=search, read_fn=read,
                                 budget=SearchBudget(rounds=8, pages=1, recovery_pages=1, queries=8))
        self.assertEqual(read.call_count, 2)
        self.assertEqual(report.status, 'partial')

    def test_failed_reads_get_bounded_spare_attempts_and_title_research(self):
        calls = []
        def search(query, freshness):
            calls.append(query)
            if len(calls) == 1:
                return [('财报报道', 'https://news.google.com/rss/articles/1', '')]
            return [('原始财报', 'https://company.example/report', '')]
        def read(url):
            return '读取失败：403' if 'google.com' in url else 'The original company report confirms revenue of 100 million dollars.'
        report = research_search('财报', config={}, search_fn=search, read_fn=read,
                                 budget=SearchBudget(pages=1, recovery_pages=1, rounds=3, queries=3))
        self.assertEqual(report.pages_used, 2)
        self.assertEqual(sum(d['read_status'] == 'read' for d in report.sources), 1)
        self.assertTrue(any('-site:news.google.com' in q for q in calls))

    def test_writer_can_request_one_followup_with_stable_source_ids(self):
        first = SearchReport('财报', sources=[], gaps=['需现金流'], status='partial')
        second = SearchReport('财报', status='complete', sources=[{
            'source_id': 'W1', 'title': '现金流', 'url': 'https://company.example/report',
            'published': '', 'engine': 'test', 'read_status': 'read', 'snippet': '',
            'body': '经营现金流100亿元', 'excerpt': '经营现金流100亿元'}], claims=[{
                'status': 'single_source', 'text': '经营现金流100亿元',
                'evidence': [{'source_id': 'W1', 'quote': '经营现金流100亿元'}]}])
        research = Mock(side_effect=[first, second])
        context = build_research_context({'fundamentals': '营业收入100亿元'}, instrument='东山精密（002384）')
        with patch.object(ai_agent, 'chat_once', side_effect=[
                {'content': '<research_followup>["东山精密 经营现金流 公告"]</research_followup>'},
                {'content': '经营现金流100亿元 [W1]；无效 [T37]'}]):
            events = list(ai_agent.run_agent({}, 'model', 'key', [
                {'role': 'user', 'content': context}, {'role': 'user', 'content': '解读业绩'}], research_fn=research))
        self.assertEqual(research.call_count, 2)
        self.assertEqual(research.call_args_list[0].kwargs['scope']['code'], '002384')
        self.assertIn('seed_sources', research.call_args.kwargs)
        self.assertIn('[W1]', events[-1][1])
        self.assertNotIn('[T37]', events[-1][1])
        audit = next(v for k, v in events if k == 'validation')
        self.assertFalse(audit['complete'])

    def test_google_aggregator_without_redirect_is_not_a_read_article(self):
        response = Mock(url='https://news.google.com/rss/articles/1', text='Google news navigation' * 10)
        with patch.object(search_tools, '_engine_get', return_value=response), \
                patch.object(search_tools.agent_reach_backend, 'read_page') as jina:
            text = search_tools.tool_read_page(response.url)
        self.assertTrue(text.startswith('读取失败'))
        jina.assert_not_called()

    def test_pdf_can_be_read_without_system_binary(self):
        reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: '原始公告正文')])
        with patch.object(search_tools.shutil, 'which', return_value=None), \
                patch('pypdf.PdfReader', return_value=reader):
            self.assertEqual(search_tools._extract_pdf_text(b'%PDF-test'), '[PDF第1页]\n原始公告正文')


if __name__ == '__main__':
    unittest.main()
