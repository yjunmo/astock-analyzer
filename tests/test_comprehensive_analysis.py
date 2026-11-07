import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import pandas as pd

from astock.ai.analysis_workflow import (
    DIMENSIONS, default_plan, refine_plan, validate_dimension_answers,
    coverage_summary, validate_brief, analysis_quality, json_stage,
)
from astock.ai.ai_context import build_research_context
from astock.ai.evidence import context_evidence
from astock.ai import ai_agent
from astock.market.company_context import company_context_text, fetch_company_context, comparable_evidence
from astock.research import research_services as services
from astock.research.research_analytics import compare_recent_performance, relative_performance_text
from astock.search.search_workflow import SearchBudget, SearchReport, research_search


def context():
    return build_research_context({
        'fundamentals': '报告期：2026-09-30\n营收增长30%，现金流/净利润0.8倍，负债率55%，客户集中风险。',
        'business': '主营光模块与PCB。公司布局产能，行业需求增长但竞争加剧，需要核对份额。',
        'valuation': 'PE(TTM)=30，近252日分位60%。2027年预测EPS=4元，尚需验证订单兑现。',
        'market_ctx': '大盘下跌2%，行业下跌4%；董事会披露回购提议，但尚未实施。',
        'technical_report': '日线均线空头排列，成交放大，支撑仅为技术参考。',
        'multi_period': '周线偏空，月线仍向上，周期不同。',
        'snapshot': '2026-11-06 收盘价80元', 'bars': '2026-11-06|80|放量'},
        instrument='测试公司（002384）')


def brief():
    return {'thesis': '需求增长支持经营，但当前估值需要订单和现金流进一步兑现。',
            'drivers': [{'title': '经营增长与估值兑现', 'dimension_ids': ['financial', 'valuation'],
                         'evidence_ids': ['F1', 'F3'],
                         'mechanism': ['订单增长改善收入', '兑现不足会压低预期盈利并提高估值风险'],
                         'alternative': '并购并表或低基数也可能解释增长，尚不能归因为持续内生增长。',
                         'invalidation': '后续订单和现金流连续未兑现则推翻经营改善判断。'}]}


def review(plan, verdict='pass', issues=None):
    refs = {'business': ['F2'], 'financial': ['F1'], 'valuation': ['F3'], 'industry': ['F2'],
            'catalysts': ['M1'], 'risks': ['F1'], 'market': ['M1'], 'technical': ['T1', 'T2']}
    return {'verdict': verdict, 'issues': issues or [], 'dimension_answers': [
        {'dimension_id': d['id'], 'status': 'supported', 'evidence_ids': refs[d['id']],
         'answer': d['label'] + '已结合实际资料解释对经营、预期与兑现时间的影响。', 'limitations': ''}
        for d in plan['dimensions']]}


class TestComprehensivePlan(unittest.TestCase):
    def test_structured_review_retries_truncation_with_more_output_space(self):
        chat = Mock(side_effect=[{'content': '', 'finish_reason': 'length', 'reasoning_content': 'private reasoning'},
                                 {'content': '{"verdict":"pass","dimension_answers":[]}', 'finish_reason': 'stop'}])
        result = json_stage('review', '只返回JSON', {}, {'api_key': 'key', 'model': 'model',
                            'provider_cfg': {'base_url': 'https://example.com', 'protocol': 'openai'}}, chat_fn=chat, max_tokens=4800)
        self.assertEqual(result['verdict'], 'pass')
        self.assertEqual(chat.call_args_list[0].kwargs['max_tokens'], 8192)
        self.assertEqual(chat.call_args_list[1].kwargs['max_tokens'], 16384)
        self.assertNotIn('private reasoning', str(chat.call_args_list[1]))

    def test_panorama_is_not_misrouted_by_earnings_words(self):
        plan = default_plan('<task type="panorama">综合分析利润、估值与下跌原因</task>',
                            {'name': '测试公司', 'code': '002384', 'report_period': '2026-09-30'})
        self.assertEqual(plan['task_type'], 'panorama')
        self.assertEqual({d['id'] for d in plan['dimensions']}, set(DIMENSIONS))
        self.assertEqual({q['dimension'] for q in plan['queries']}, set(DIMENSIONS) - {'technical'})
        self.assertIn('2026 第三季度报告', next(q['query'] for q in plan['queries'] if q['dimension'] == 'financial'))

    def test_model_cannot_delete_nonfinancial_dimensions(self):
        base = default_plan('全面分析', {'name': '测试公司', 'code': '002384'})
        result = refine_plan(base, {'dimensions': [{'id': 'financial', 'question': '利润增长来源？'}],
                                    'queries': [{'dimension': 'financial', 'query': '测试公司财报'}]})
        self.assertEqual(len(result['dimensions']), 8)
        self.assertEqual(len(result['queries']), 7)
        self.assertEqual(next(d['question'] for d in result['dimensions'] if d['id'] == 'financial'), '利润增长来源？')

    def test_task_intents_keep_different_priorities(self):
        risk = default_plan('<task type="risk_audit">盈利与估值风险</task>', {})
        trade = default_plan('<task type="trade_plan">交易计划及财报约束</task>', {})
        earnings = default_plan('财报怎么解读', {})
        self.assertEqual(risk['task_type'], 'risk')
        self.assertEqual(trade['task_type'], 'trade')
        self.assertIn('technical', {d['id'] for d in trade['dimensions']})
        self.assertNotIn('technical', {d['id'] for d in earnings['dimensions']})

    def test_free_form_multi_topic_request_is_comprehensive(self):
        self.assertEqual(default_plan('结合财报、行业供需和估值判断', {})['task_type'], 'panorama')
        self.assertEqual(default_plan('帮我看看这家公司', {})['task_type'], 'panorama')
        self.assertEqual(default_plan('只解读财报里的利润和现金流', {})['task_type'], 'earnings')

    def test_headings_and_fake_refs_do_not_cover_questions(self):
        plan = default_plan('综合分析', {})
        rows = validate_dimension_answers([
            {'dimension_id': 'business', 'status': 'supported', 'answer': '业务与竞争位置', 'evidence_ids': ['F2']},
            {'dimension_id': 'financial', 'status': 'supported', 'answer': '已取得足够的利润增长和现金流证据。', 'evidence_ids': ['F999']}],
            plan['dimensions'], {'F2': {'available': True}})
        self.assertEqual(coverage_summary(plan['dimensions'], rows)['coverage'], 0)

    def test_indicator_list_is_not_a_causal_brief(self):
        registry, _ = context_evidence([{'role': 'user', 'content': context()}])
        raw = {'drivers': [{'title': '各指标', 'evidence_ids': ['F1'], 'dimension_ids': ['financial'],
                            'mechanism': ['营收增长'], 'alternative': '', 'invalidation': ''}]}
        self.assertFalse(validate_brief(raw, registry)['complete'])
        self.assertTrue(validate_brief(brief(), registry)['complete'])

    def test_unproved_driver_is_discarded_without_blocking_supported_analysis(self):
        registry, _ = context_evidence([{'role': 'user', 'content': context()}])
        value = brief()
        value['drivers'].append({'title': '未证实消息', 'evidence_ids': ['W999']})
        checked = validate_brief(value, registry)
        self.assertEqual(checked['discarded_drivers'], 1)
        self.assertTrue(checked['complete'])

    def test_high_issue_overrides_a_model_pass(self):
        registry, scope = context_evidence([{'role': 'user', 'content': context()}])
        plan = default_plan('综合分析', scope)
        audited = review(plan, issues=[{'severity': 'high', 'reason': '用均线推导了股价概率'}])
        quality = analysis_quality(plan, validate_brief(brief(), registry), audited, registry)
        self.assertEqual(quality['coverage'], 100)
        self.assertFalse(quality['complete'])


class TestDimensionResearch(unittest.TestCase):
    def test_new_evidence_is_retained_beyond_first_sixteen_claims(self):
        old = [{'text': f'旧事实{i}', 'status': 'single_source',
                'evidence': [{'source_id': 'W1', 'quote': f'old factual quote {i:02d}'}]} for i in range(16)]
        source = {'title': '已读资料', 'url': 'https://company.example/old',
                  'body': ' '.join(f'old factual quote {i:02d}' for i in range(16))}
        report = research_search('核验后续资料', config={}, seed_sources=[source], seed_claims=old,
                                 decision_fn=lambda *a: {'queries': ['补查'], 'claims': [
                                     {'text': '新事实', 'evidence': [{'source_id': 'W2', 'quote': 'new material evidence'}]}]},
                                 search_fn=lambda *a: [('新资料', 'https://company.example/new', '')],
                                 read_fn=lambda *a: 'The later original filing contains new material evidence about orders.',
                                 budget=SearchBudget(rounds=1, queries=1, pages=1))
        self.assertEqual(len(report.claims), 17)
        self.assertEqual(report.claims[-1]['text'], '新事实')

    def test_residual_uncertainty_does_not_block_a_supported_conditional_answer(self):
        from astock.ai.evidence import with_web_evidence, audit_citations
        dimension = [default_plan('财报', {})['dimensions'][1]]
        report = research_search('财报', config={}, scope={'research_dimensions': dimension},
                                 decision_fn=lambda *a: {'queries': ['财报'], 'done': True,
                                                        'gaps': ['后续订单尚未公布'], 'critical_gaps': [],
                                                        'claims': [{'text': '已披露增长', 'evidence': [{'source_id': 'W1', 'quote': 'confirmed revenue growth'}]}],
                                                        'dimension_answers': [{'dimension_id': 'financial', 'status': 'supported',
                                                                              'answer': '已披露营收增长支持本期增长事实，后续订单不确定。', 'evidence_ids': ['W1']}]},
                                 search_fn=lambda *a: [('财报', 'https://company.example/a', '')],
                                 read_fn=lambda *a: 'The company confirmed revenue growth in the original financial report.',
                                 budget=SearchBudget(rounds=1, queries=1, pages=1))
        self.assertEqual(report.status, 'complete')
        audit = audit_citations('本期增长已披露 [W1]', with_web_evidence({}, report.to_dict()), report.to_dict())
        self.assertTrue(audit['complete'])
        self.assertEqual(audit['uncertainties'], ['后续订单尚未公布'])

    def test_one_earnings_claim_cannot_end_a_comprehensive_search(self):
        dimensions = [d for d in default_plan('综合分析', {})['dimensions'] if d['id'] in {'financial', 'industry'}]
        def decide(system, user):
            return {'queries': ['测试公司财报'], 'done': True, 'claims': [{
                'text': '订单增长', 'evidence': [{'source_id': 'W1', 'quote': 'confirmed revenue growth'}]}],
                'dimension_answers': [{'dimension_id': 'financial', 'status': 'supported',
                                      'answer': '公司营收增长且现金流为正，经营数据支持增长事实。', 'evidence_ids': ['W1']}]}
        report = research_search('综合分析', config={}, scope={'research_dimensions': dimensions},
                                 decision_fn=decide, search_fn=lambda *a: [('财报', 'https://company.example/a', '')],
                                 read_fn=lambda *a: 'The company confirmed revenue growth and positive cash flow in its official filing.',
                                 budget=SearchBudget(rounds=1, queries=1, pages=1))
        self.assertEqual(report.status, 'partial')
        self.assertIn('行业与产业链', ' '.join(report.gaps))
        self.assertEqual(next(d for d in report.dimension_answers if d['dimension_id'] == 'industry')['status'], 'missing')

    def test_failed_quote_cannot_support_a_dimension(self):
        dimensions = [default_plan('财报', {})['dimensions'][1]]
        report = research_search('财报', config={}, scope={'research_dimensions': dimensions},
                                 decision_fn=lambda *a: {'queries': ['财报'], 'done': True,
                                                        'claims': [{'text': '假事实', 'evidence': [{'source_id': 'W1', 'quote': 'fabricated quote'}]}],
                                                        'dimension_answers': [{'dimension_id': 'financial', 'status': 'supported',
                                                                              'answer': '这段回答声称解释了营收增长与盈利质量。', 'evidence_ids': ['W1']}]},
                                 search_fn=lambda *a: [('财报', 'https://company.example/a', '')],
                                 read_fn=lambda *a: 'The original official financial report has no such factual quotation.',
                                 budget=SearchBudget(rounds=1, queries=1, pages=1))
        self.assertEqual(report.status, 'partial')
        self.assertEqual(report.dimension_answers[0]['status'], 'missing')


class TestIntegratedAnalysis(unittest.TestCase):
    def messages(self):
        return [{'role': 'user', 'content': context()}, {'role': 'user', 'content': '全面综合分析'}]

    def test_offline_mode_runs_plan_synthesis_review_and_revision_without_search(self):
        calls = []
        def stage(phase, system, payload):
            calls.append(phase)
            if phase == 'plan':
                return {}
            if phase.startswith('synthesize'):
                return brief()
            if phase == 'review':
                self.assertIn('必然上涨', payload['draft'])
                self.assertNotIn('dimension_answers', payload)
                self.assertNotIn('brief', payload)
                return review(payload['plan'], verdict='revise', issues=[{
                    'severity': 'high', 'claim': '必然上涨', 'reason': '增长不能直接证明股价上涨', 'fix': '区分经营价值、估值与短期价格'}])
            self.assertNotIn('必然上涨', payload['draft'])
            return review(payload['plan'])
        search = Mock()
        with patch.object(ai_agent, 'chat_once', side_effect=[
                {'content': '利润增长所以必然上涨 [F1]'},
                {'content': '经营改善仍需与估值及短期市场条件区分 [F1][F3][T1]'}]) as writer:
            events = list(ai_agent.run_agent({}, 'model', 'key', self.messages(), mode='off', research_fn=search, stage_fn=stage))
        search.assert_not_called()
        self.assertEqual(writer.call_count, 2)
        self.assertEqual(calls, ['plan', 'synthesize', 'review', 'review_after_revision'])
        audit = next(v for k, v in events if k == 'validation')
        self.assertTrue(audit['complete'])
        self.assertEqual(audit['analysis_quality']['coverage'], 100)
        self.assertNotIn('必然上涨', events[-1][1])

    def test_valid_citations_do_not_hide_missing_industry_and_valuation_analysis(self):
        def stage(phase, system, payload):
            if phase.startswith('synthesize'):
                return brief()
            if phase.startswith('review'):
                result = review(payload['plan'])
                result['dimension_answers'] = [d for d in result['dimension_answers'] if d['dimension_id'] == 'financial']
                return result
            return {}
        with patch.object(ai_agent, 'chat_once', return_value={'content': '只有利润增长 [F1]'}):
            events = list(ai_agent.run_agent({}, 'model', 'key', self.messages(), mode='off', stage_fn=stage))
        audit = next(v for k, v in events if k == 'validation')
        self.assertEqual(audit['coverage'], 100)
        self.assertEqual(audit['analysis_quality']['coverage'], 12.5)
        self.assertFalse(audit['complete'])
        self.assertIn('综合研究审查尚未通过', events[-1][1])

    def test_review_failure_is_visible_and_does_not_silently_approve(self):
        def stage(phase, system, payload):
            if phase.startswith('synthesize'):
                return brief()
            if phase.startswith('review'):
                raise RuntimeError('private-token')
            return {}
        with patch.object(ai_agent, 'chat_once', return_value={'content': '条件性研究判断 [F1]'}):
            events = list(ai_agent.run_agent({}, 'model', 'key', self.messages(), mode='off', stage_fn=stage))
        audit = next(v for k, v in events if k == 'validation')
        self.assertFalse(audit['complete'])
        self.assertFalse(audit['analysis_quality']['reviewed'])
        self.assertNotIn('private-token', str(events))

    def test_revision_is_checked_for_new_invalid_references(self):
        def stage(phase, system, payload):
            if phase.startswith('synthesize'):
                return brief()
            if phase == 'review':
                return review(payload['plan'], verdict='revise', issues=[{'severity': 'high', 'reason': '原结论需要修订'}])
            if phase == 'review_after_revision':
                return review(payload['plan'])
            return {}
        with patch.object(ai_agent, 'chat_once', side_effect=[{'content': '原稿 [F1]'}, {'content': '修订稿 [T999]'}]):
            events = list(ai_agent.run_agent({}, 'model', 'key', self.messages(), mode='off', stage_fn=stage))
        audit = next(v for k, v in events if k == 'validation')
        self.assertFalse(audit['complete'])
        self.assertIn('T999', audit['invalid_citations'])

    def test_review_can_spend_the_single_followup_on_a_material_gap(self):
        first = SearchReport('综合研究', status='partial', gaps=['行业原文不足'])
        second = SearchReport('综合研究', status='complete', sources=[{
            'source_id': 'W1', 'title': '行业原文', 'url': 'https://company.example/industry',
            'published': '', 'engine': 'test', 'read_status': 'read', 'snippet': '', 'excerpt': '行业原文',
            'body': '行业需求增长但同业扩产会加剧竞争'}], claims=[{
                'status': 'single_source', 'text': '需求及供给',
                'evidence': [{'source_id': 'W1', 'quote': '行业需求增长但同业扩产'}]}])
        search = Mock(side_effect=[first, second])
        def stage(phase, system, payload):
            if phase.startswith('synthesize'):
                return brief()
            if phase == 'review':
                result = review(payload['plan'], verdict='revise', issues=[{'severity': 'high', 'reason': '行业结论缺原文'}])
                result['followup_queries'] = ['测试公司 行业原始需求及供给数据']
                return result
            if phase == 'review_after_revision':
                return review(payload['plan'])
            return {}
        with patch.object(ai_agent, 'chat_once', side_effect=[{'content': '草稿 [F1]'}, {'content': '修订 [F1][W1]'}]):
            events = list(ai_agent.run_agent({}, 'model', 'key', self.messages(), research_fn=search, stage_fn=stage))
        self.assertEqual(search.call_count, 2)
        self.assertTrue(next(v for k, v in events if k == 'validation')['complete'])
        self.assertIn('[W1]', events[-1][1])

    def test_archive_preserves_dimension_findings_instead_of_marking_all_complete(self):
        registry, scope = context_evidence(self.messages())
        plan = default_plan('全面综合分析', scope)
        raw_review = review(plan)
        raw_review['dimension_answers'] = raw_review['dimension_answers'][:1]
        quality = analysis_quality(plan, validate_brief(brief(), registry), raw_review, registry)
        quality['plan'] = plan
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ASTOCK_DB_PATH': os.path.join(directory, 'research.db')}):
            task = services.start_research_task('002384', '测试公司', '综合分析', 'panorama')
            services.update_task_plan(task, plan)
            answer = ('## 核心结论\n结论方向：中性\n## 综合分析\n经营改善 [F1]\n'
                      '## 证据冲突与风险\n估值需要订单兑现\n## 失效条件\n现金流恶化\n'
                      '## 下一跟踪指标\n订单与现金流\n## 待确认问题\n行业竞争证据缺失')
            services.archive_research_run('002384', '测试公司', '综合分析', answer, task_type='panorama', task_id=task,
                                          evidence_registry=registry,
                                          citation_validation={'complete': False, 'coverage': 100, 'gaps': quality['gaps'], 'analysis_quality': quality})
            run = services.list_research_runs('002384')[0]
            parsed = json.loads(run['structured_json'])
            self.assertEqual(run['status'], 'incomplete')
            self.assertEqual(parsed['analysis_quality']['coverage'], 12.5)
            statuses = json.loads(services.list_research_tasks('002384')[0]['subquestion_status_json'])
            self.assertEqual(statuses[0]['status'], 'complete')
            self.assertEqual(statuses[1]['status'], 'needs_review')


class TestCompanyContext(unittest.TestCase):
    @staticmethod
    def peer_data(code):
        return {'history': pd.DataFrame({'数据日期': ['2026-11-06'], '当日收盘价': [100], 'PE(TTM)': [25]}),
                'forecast': {'SECURITY_NAME_ABBR': '来源中的真实名称', 'YEAR2': 2027, 'YEAR_MARK2': 'E', 'EPS2': 5},
                'business': pd.DataFrame([{'主营业务': '光模块'}]), 'retrieved_at': '2026-11-07'}

    def test_candidates_use_fetched_numbers_and_source_names(self):
        result = comparable_evidence([{'code': '300308', 'name': '模型提供的名字', 'reason': '候选光模块业务对照'}], fetch_fn=self.peer_data)
        self.assertEqual(result['F4']['name'], '来源中的真实名称')
        self.assertIn('预期PE=20.00倍', result['F4']['text'])
        self.assertIn('仅为候选对照', result['F4']['text'])
        failed = comparable_evidence([{'code': '300308'}], fetch_fn=Mock(side_effect=RuntimeError('private-token')))
        self.assertEqual(failed, {})

    def test_peer_evidence_is_available_to_writer_and_citation_audit(self):
        def stage(phase, system, payload):
            if phase == 'plan':
                return {'comparables': [{'code': '300308', 'name': '候选公司', 'reason': '主营业务对照'}]}
            if phase.startswith('synthesize'):
                value = brief()
                value['drivers'][0]['evidence_ids'] = ['F1', 'F4']
                return value
            value = review(payload['plan'])
            for row in value['dimension_answers']:
                if row['dimension_id'] == 'valuation':
                    row['evidence_ids'] = ['F4']
            return value
        search = Mock(return_value=SearchReport('综合研究', status='complete'))
        with patch.object(ai_agent, 'chat_once', return_value={'content': '候选同业数据需核对业务可比性 [F4][F1]'}) as writer:
            events = list(ai_agent.run_agent({}, 'model', 'key', [
                {'role': 'user', 'content': context()}, {'role': 'user', 'content': '综合分析'}],
                research_fn=search, stage_fn=stage, peer_data_fn=self.peer_data))
        self.assertTrue(next(v for k, v in events if k == 'validation')['complete'])
        self.assertIn('F4', next(v for k, v in events if k == 'supplemental_evidence'))
        self.assertIn('来源中的真实名称', str(writer.call_args.args[3]))

    def test_valuation_history_and_estimates_keep_distinct_periods_and_meanings(self):
        data = {'history': pd.DataFrame({'数据日期': ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06'],
                                         '当日收盘价': [80] * 5, 'PE(TTM)': [-1, 0, 20, 40, 30]}),
                'forecast': {'RATING_ORG_NUM': 5, 'YEAR1': 2025, 'YEAR_MARK1': 'A', 'EPS1': 2,
                             'YEAR2': 2027, 'YEAR_MARK2': 'E', 'EPS2': 4}, 'retrieved_at': '2026-11-07',
                'business': pd.DataFrame([{'主营业务': '光模块与PCB'}])}
        texts = company_context_text(data)
        self.assertIn('近3个有效交易日分位=66.7%', texts['valuation'])
        self.assertIn('2025年EPS=2.000元（历史实际）。', texts['valuation'])
        self.assertIn('2027年EPS=4.000元（机构预测）；按上述报价计算预期PE=20.00倍', texts['valuation'])
        self.assertIn('预测底稿更新日期=未知', texts['valuation'])
        self.assertIn('光模块与PCB', texts['business'])

    def test_forecast_fetch_filters_by_security_and_does_not_download_entire_market(self):
        response = Mock()
        response.json.return_value = {'result': {'data': [{'SECURITY_CODE': '002384', 'YEAR2': 2027}]}}
        get = Mock(return_value=response)
        ak = SimpleNamespace(stock_zyjs_ths=lambda **kw: pd.DataFrame(), stock_value_em=lambda **kw: pd.DataFrame())
        data = fetch_company_context('002384', ak_mod=ak, http_get=get)
        self.assertEqual(get.call_args.kwargs['params']['filter'], '(SECURITY_CODE="002384")')
        self.assertEqual(data['forecast']['YEAR2'], 2027)

    def test_failed_company_data_is_not_available_evidence(self):
        texts = company_context_text({'errors': {'history': 'TimeoutError'}})
        bundle = build_research_context(texts, instrument='测试公司（002384）')
        registry, _ = context_evidence([{'role': 'user', 'content': bundle}])
        self.assertFalse(registry['F2']['available'])
        self.assertFalse(registry['F3']['available'])


class TestMarketComparison(unittest.TestCase):
    def test_returns_compare_exact_dates_and_ignore_future_index_values(self):
        stock = pd.DataFrame({'date': pd.date_range('2026-11-02', periods=5, freq='D'), 'close': [100, 98, 95, 90, 80]})
        benchmark = pd.DataFrame({'date': ['2026-11-02', '2026-11-03', '2026-11-05', '2026-11-06', '2026-11-09'],
                                  'close': [100, 101, 102, 100, 500]})
        rows = compare_recent_performance(stock, benchmark, windows=(1, 2, 4))
        self.assertEqual([r['window'] for r in rows], [1, 4])
        self.assertEqual(rows[-1]['end'], '2026-11-06')
        self.assertAlmostEqual(rows[-1]['excess_pp'], -20)
        self.assertIn('不能独立证明某事件', relative_performance_text(stock, benchmark, 'sz399001', 'qfq'))
        self.assertEqual(compare_recent_performance(stock, benchmark.iloc[:3]), [])


if __name__ == '__main__':
    unittest.main()
