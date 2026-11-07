function createQuickLook(send) {
  const element = id => document.getElementById(`quick-${id}`);
  const panel = element('look');
  const periods = [
    ['当日', 'day'], ['近2日', '2'], ['近3日', '3'], ['近5日', '5'],
    ['近10日', '10'], ['近20日', '20'], ['近60日', '60'], ['近120日', '120'],
    ['近250日', '250'], ['年初至今', 'ytd'],
  ];
  let active = null;
  let dwellTimer, leaveTimer, pollTimer, expiryTimer;

  function textNode(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function price(value) {
    return Number.isFinite(value) && value > 0 ? value.toFixed(2) : '—';
  }

  function money(value) {
    if (!Number.isFinite(value)) return '—';
    if (value >= 1e12) return `${(value / 1e12).toFixed(2)} 万亿`;
    if (value >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`;
    if (value >= 1e4) return `${(value / 1e4).toFixed(2)} 万`;
    return `${value.toFixed(0)} 元`;
  }

  function showChange(node, value) {
    node.textContent = Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
    node.classList.toggle('quick-up', value > 0);
    node.classList.toggle('quick-down', value < 0);
  }

  function anchor(event) {
    if (event.clientX || event.clientY) return {x: event.clientX, y: event.clientY};
    const rect = event.currentTarget.getBoundingClientRect();
    return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
  }

  function position() {
    if (!active) return;
    let top = 8;
    let bottom = innerHeight - 8;
    try {
      const frame = window.frameElement?.getBoundingClientRect();
      if (frame) {
        top = Math.max(top, 8 - frame.top);
        bottom = Math.min(bottom, window.parent.innerHeight - frame.top - 8);
      }
    } catch { /* The iframe bounds are enough on other origins. */ }
    panel.style.maxHeight = `${Math.min(640, Math.max(80, bottom - top))}px`;
    const {x, y} = active.point;
    const width = panel.offsetWidth;
    const side = x + width + 18 < innerWidth - 8 ? x + 18 : x - width - 18;
    panel.style.left = `${Math.max(8, Math.min(side, innerWidth - width - 8))}px`;
    panel.style.top = `${Math.max(top, Math.min(y - 25, bottom - panel.offsetHeight))}px`;
  }

  function renderHistory() {
    const history = active.detail?.history;
    const ready = history?.status === 'ready';
    const unavailable = active.timedOut || history?.status === 'unavailable';
    const cells = periods.map(([label, key]) => {
      const cell = textNode('div', '', 'quick-return');
      const change = document.createElement('strong');
      showChange(change, key === 'day' ? active.stock.change : history?.returns?.[key]);
      cell.append(textNode('span', label), change);
      return cell;
    });
    element('returns').replaceChildren(...cells);
    const historySource = history?.source ? `${history.source} · ` : '';
    element('history-note').textContent = ready ? `${historySource}已收盘前复权日线 · 截至 ${history.date}`
      : unavailable ? '历史日线暂未返回' : '正在加载历史日线…';
    element('retry').hidden = ready || !unavailable;
  }

  function renderQuote() {
    const {stock, sector, detail} = active;
    const quote = detail?.quote;
    element('name').textContent = stock.name;
    element('symbol').textContent = [stock.code, sector, detail?.market].filter(Boolean).join(' · ');
    const latest = price(quote?.['最新价'] ?? stock.price);
    element('price').textContent = latest === '—' ? latest : `¥${latest}`;
    showChange(element('change'), quote?.['涨跌幅'] ?? stock.change);
    const turnover = quote?.['换手率'];
    const rows = [
      ['总市值', money(quote?.['总市值'] ?? stock.cap)], ['流通市值', money(quote?.['流通市值'])],
      ['成交额', money(quote?.['成交额'] ?? stock.amount)],
      ['换手率', Number.isFinite(turnover) ? `${turnover.toFixed(2)}%` : '—'],
      ['今开', price(quote?.['今开'])], ['昨收', price(quote?.['昨收'])],
      ['最高', price(quote?.['最高'])], ['最低', price(quote?.['最低'])],
    ];
    element('quote').replaceChildren(...rows.map(([label, value]) => {
      const row = document.createElement('div');
      row.append(textNode('dt', label), textNode('dd', value));
      return row;
    }));
    element('source').textContent = detail
      ? `${detail.source} · 获取于 ${detail.fetched_at.slice(0, 19).replace('T', ' ')} 北京时间`
      : '来源：当前云图快照';
    element('watch').disabled = !detail || detail.watched;
    element('watch').textContent = detail?.watched ? '已加入自选 ✓' : '加入自选 ☆';
    element('watch-status').textContent = detail?.watch_error || '';
    element('watch-status').hidden = !detail?.watch_error;
  }

  function renderPeers() {
    const peers = active.detail?.peers || [];
    const query = element('peer-search').value.toLowerCase().replace(/\s+/g, '');
    const sort = element('peer-sort').value;
    const visible = peers.filter(peer => peer.name.toLowerCase().replace(/\s+/g, '').includes(query)
      || peer.code.includes(query));
    visible.sort((a, b) => {
      if (sort === 'code') return a.code.localeCompare(b.code);
      const difference = sort === 'up' ? b.change - a.change : a.change - b.change;
      return difference || a.code.localeCompare(b.code);
    });
    element('peer-count').textContent = peers.length ? `${visible.length}/${peers.length} 只` : '';
    if (!visible.length) {
      const message = active.detail ? (peers.length ? '没有匹配的同行' : '暂无同行行情') : '正在读取同行行情…';
      element('peer-list').replaceChildren(textNode('p', message, 'quick-muted'));
      return;
    }
    element('peer-list').replaceChildren(...visible.map(peer => {
      const button = textNode('button', '', 'quick-peer');
      const identity = textNode('span', '', 'quick-peer-name');
      identity.append(textNode('strong', peer.name), textNode('small', peer.code, 'quick-muted'));
      const change = document.createElement('strong');
      showChange(change, peer.change);
      button.append(identity, textNode('span', price(peer.price)), change);
      button.addEventListener('click', () => open(peer, active.sector, active.point, true));
      return button;
    }));
  }

  function stopPoll() {
    clearTimeout(pollTimer);
    pollTimer = undefined;
  }

  function clearHistoryTimers() {
    stopPoll();
    clearTimeout(expiryTimer);
  }

  function schedulePoll() {
    if (pollTimer !== undefined) return;
    pollTimer = setTimeout(() => {
      pollTimer = undefined;
      const status = active?.detail?.history?.status;
      if (!active || active.timedOut || status === 'ready' || status === 'unavailable') return;
      send('poll', active.stock.code);
      // Repeated loading replies can be identical and Streamlit may skip delivering them.
      schedulePoll();
    }, 750);
  }

  function request(action) {
    clearHistoryTimers();
    active.timedOut = false;
    if (active.detail && action === 'retry') active.detail.history = {status: 'loading'};
    expiryTimer = setTimeout(() => {
      stopPoll();
      active.timedOut = true;
      renderHistory();
    }, 15000);
    send(action, active.stock.code);
    schedulePoll();
  }

  function close() {
    clearTimeout(dwellTimer);
    clearTimeout(leaveTimer);
    clearHistoryTimers();
    active = null;
    panel.hidden = true;
  }

  function showPin() {
    element('pin').textContent = active.pinned ? '取消固定' : '固定';
    element('pin').setAttribute('aria-pressed', String(active.pinned));
  }

  function open(stock, sector, point, pinned) {
    clearTimeout(dwellTimer);
    clearTimeout(leaveTimer);
    const detail = active?.stock.code === stock.code ? active.detail : null;
    active = {stock, sector, point, pinned, detail, timedOut: false};
    if (!detail) element('peer-search').value = '';
    panel.hidden = false;
    showPin();
    renderQuote();
    renderHistory();
    renderPeers();
    position();
    request(pinned ? 'select' : 'preview');
  }

  function enter(stock, sector, event) {
    if (active?.pinned || event.pointerType === 'touch') return;
    clearTimeout(leaveTimer);
    clearTimeout(dwellTimer);
    if (active?.stock.code === stock.code) return;
    const point = anchor(event);
    dwellTimer = setTimeout(() => open(stock, sector, point, false), 380);
  }

  function leave() {
    clearTimeout(dwellTimer);
    clearTimeout(leaveTimer);
    if (!active?.pinned) leaveTimer = setTimeout(close, 220);
  }

  function accept(detail) {
    if (!active || detail?.code !== active.stock.code) return;
    const newPeers = active.detail?.fetched_at !== detail.fetched_at;
    active.detail = detail;
    if (Number.isFinite(detail.quote?.['涨跌幅'])) active.stock = {...active.stock, change: detail.quote['涨跌幅']};
    renderQuote();
    renderHistory();
    if (newPeers) renderPeers();
    position();
    if (detail.history.status !== 'loading') clearHistoryTimers();
    else if (!active.timedOut) schedulePoll();
  }

  panel.addEventListener('pointerenter', () => {
    clearTimeout(dwellTimer);
    clearTimeout(leaveTimer);
  });
  panel.addEventListener('pointerleave', leave);
  panel.addEventListener('focusin', () => clearTimeout(leaveTimer));
  element('pin').addEventListener('click', () => {
    active.pinned = !active.pinned;
    showPin();
  });
  element('close').addEventListener('click', close);
  element('retry').addEventListener('click', () => {
    request('retry');
    renderHistory();
  });
  element('watch').addEventListener('click', () => {
    element('watch').disabled = true;
    stopPoll();
    send('watch', active.stock.code);
  });
  element('research').addEventListener('click', () => {
    clearHistoryTimers();
    send('research', active.stock.code);
  });
  element('peer-search').addEventListener('input', renderPeers);
  element('peer-sort').addEventListener('change', renderPeers);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && active) close();
  });

  return {enter, leave, accept, position, select: (stock, sector, event) => open(stock, sector, anchor(event), true)};
}
