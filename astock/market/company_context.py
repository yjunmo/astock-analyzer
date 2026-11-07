"""综合研究的业务资料、历史估值与盈利预期。不同数据保留独立时点和口径。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
import re

import pandas as pd

from astock.market.fundamentals import _to_num


def fetch_company_context(code6: str, ak_mod=None, http_get=None) -> dict:
    if not re.fullmatch(r"\d{6}", str(code6)):
        raise ValueError("需要6位证券代码")
    if ak_mod is None:
        import akshare as ak_mod
    if http_get is None:
        import requests
        http_get = requests.get
    from astock.market.data_fetcher import default_request_timeout

    def forecast():
        response = http_get('https://datacenter-web.eastmoney.com/api/data/v1/get', timeout=10,
                            params={'reportName': 'RPT_WEB_RESPREDICT', 'columns': 'WEB_RESPREDICT',
                                    'filter': f'(SECURITY_CODE="{code6}")', 'pageSize': 10, 'pageNumber': 1})
        response.raise_for_status()
        rows = (response.json().get('result') or {}).get('data') or []
        return next((r for r in rows if str(r.get('SECURITY_CODE', '')).zfill(6) == code6), {})

    jobs = {'business': lambda: ak_mod.stock_zyjs_ths(symbol=code6),
            'history': lambda: ak_mod.stock_value_em(symbol=code6), 'forecast': forecast}
    result = {'retrieved_at': datetime.now().isoformat(timespec='seconds'), 'errors': {}}
    pool = ThreadPoolExecutor(max_workers=3)
    with default_request_timeout(10):
        futures = {key: pool.submit(fn) for key, fn in jobs.items()}
        done, _ = wait(futures.values(), timeout=20)
        for key, future in futures.items():
            try:
                if future not in done:
                    raise TimeoutError()
                result[key] = future.result()
                if result[key] is None or (isinstance(result[key], pd.DataFrame) and result[key].empty) or (
                        isinstance(result[key], dict) and not result[key]):
                    result['errors'][key] = 'empty'
            except Exception as exc:
                result['errors'][key] = type(exc).__name__
        pool.shutdown(wait=False, cancel_futures=True)
    return result


def company_context_text(data: dict) -> dict:
    business, valuation = [], []
    frame = data.get('business')
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        business = ['业务资料：同花顺主营介绍（资料更新日期未知，不代表最新季度业务占比）。']
        for key in ('主营业务', '产品类型', '产品名称', '经营范围'):
            if key in frame and pd.notna(frame.iloc[0][key]):
                business.append(f'{key}：{str(frame.iloc[0][key])[:1000]}')
    history = data.get('history')
    price = None
    if isinstance(history, pd.DataFrame) and not history.empty and '数据日期' in history:
        history = history.copy()
        history['_date'] = pd.to_datetime(history['数据日期'], errors='coerce')
        history = history.dropna(subset=['_date']).sort_values('_date').drop_duplicates('_date', keep='last')
        if not history.empty:
            row = history.iloc[-1]
            price = _to_num(row.get('当日收盘价'))
            valuation.append(f"历史估值来源：东方财富估值分析；报价日={row['_date'].date()}，收盘价={price}元。")
            for column in ('PE(TTM)', 'PE(静)', '市净率', '市销率'):
                if column not in history:
                    continue
                values = pd.to_numeric(history.tail(252)[column], errors='coerce').replace([float('inf'), -float('inf')], float('nan'))
                values = values[values > 0].dropna()
                current = _to_num(row.get(column))
                if current is not None and current > 0 and not values.empty:
                    percentile = (values <= current).mean() * 100
                    valuation.append(f'{column}={current:.2f}；近{len(values)}个有效交易日分位={percentile:.1f}%，'
                                     f'中位数={values.median():.2f}，窗口={history.tail(252)["_date"].iloc[0].date()}至{row["_date"].date()}。')
            valuation.append('历史分位只描述自身历史位置；并购、业务结构和盈利周期变化会影响可比性，不能据此直接判定便宜。')
    forecast = data.get('forecast') or {}
    if isinstance(forecast, dict) and forecast:
        valuation.append(f"盈利预期来源：东方财富研报汇总；采集={data.get('retrieved_at', '')}，"
                         f"机构数={forecast.get('RATING_ORG_NUM', '未知')}；预测底稿更新日期={forecast.get('UPDATE_DATE') or '未知'}。")
        for idx in range(1, 5):
            year, mark, eps = forecast.get(f'YEAR{idx}'), str(forecast.get(f'YEAR_MARK{idx}') or ''), _to_num(forecast.get(f'EPS{idx}'))
            if year is None or eps is None:
                continue
            label = {'A': '历史实际', 'E': '机构预测'}.get(mark, '口径未标明')
            text = f'{year}年EPS={eps:.3f}元（{label}）'
            if mark == 'E' and price is not None and price > 0 and eps > 0:
                text += f'；按上述报价计算预期PE={price/eps:.2f}倍'
            valuation.append(text + '。')
        valuation.append('机构EPS属于预测，不能当公司指引或已实现利润；未取得同季度一致预期时不能判定财报超预期。'
                         '同业估值和业务可比性须另查，不把A/H不同股价、市值与股本混算目标价。')
    if data.get('errors'):
        valuation.append('未获取接口：' + '、'.join(data['errors']) + '；缺失维度需检索补齐。')
    return {'business': '\n'.join(business) if len(business) > 1 else '（未取得主营业务资料）',
            'valuation': '\n'.join(valuation) if any('来源' in x for x in valuation) else '（未取得历史估值与机构预测）'}


def comparable_evidence(candidates: list, *, fetch_fn=None) -> dict:
    """模型只提候选；实际数值和业务资料独立获取，不从模型记忆补数字。"""
    fetch_fn = fetch_fn or fetch_company_context
    peers = [p for p in candidates if re.fullmatch(r'\d{6}', str(p.get('code', '')))][:3]
    if not peers:
        return {}
    pool = ThreadPoolExecutor(max_workers=3)
    futures = [pool.submit(fetch_fn, peer['code']) for peer in peers]
    done, _ = wait(futures, timeout=25)
    result = {}
    for i, (peer, future) in enumerate(zip(peers, futures), 4):
        try:
            if future not in done:
                continue
            data = future.result()
            text = company_context_text(data)
            if text['valuation'].startswith('（未取得'):
                continue
            actual_name = (data.get('forecast') or {}).get('SECURITY_NAME_ABBR') or peer.get('name') or '名称待核'
            body = (f"候选可比公司：{actual_name}（{peer['code']}）；选择理由（待核实）：{peer.get('reason', '')}。\n"
                    + text['business'] + '\n' + text['valuation']
                    + '\n仅为候选对照。比较前须确认产品、客户、成长阶段、会计与盈利预测口径，不能把业务不同的整家公司PE直接套用。')
            result[f'F{i}'] = {'kind': 'local', 'available': True, 'text': body,
                               'code': peer['code'], 'name': actual_name,
                               'url': f"https://data.eastmoney.com/gzfx/detail/{peer['code']}.html",
                               'retrieved_at': data.get('retrieved_at', '')}
        except Exception:
            continue
    pool.shutdown(wait=False, cancel_futures=True)
    return result
