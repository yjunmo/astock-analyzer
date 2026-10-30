"""个人投研数据仓库：自选股、投资假设、研究快照与新闻证据。

只使用 Python 标准库 SQLite，数据默认保存在项目内 ``.astock/research.db``。
每次调用独立连接，适配 Streamlit rerun 和新闻采集线程；WAL 模式避免读写互相阻塞。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / ".astock" / "research.db"


def _db_path() -> Path:
    raw = os.environ.get("ASTOCK_DB_PATH", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_DB


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")
    _init(conn)
    return conn


def _init(conn: sqlite3.Connection) -> None:
    # 先读取迁移版本。旧库没有 schema_meta 时按 0 处理；所有迁移均为
    # CREATE IF NOT EXISTS / 缺列补齐，因此可安全重复执行且不会清空用户数据。
    try:
        old_version = int(conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError):
        old_version = 0
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
          symbol TEXT PRIMARY KEY,
          name TEXT NOT NULL DEFAULT '',
          tags TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS theses (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          thesis TEXT NOT NULL DEFAULT '',
          catalysts TEXT NOT NULL DEFAULT '',
          risks TEXT NOT NULL DEFAULT '',
          invalidation TEXT NOT NULL DEFAULT '',
          conviction INTEGER NOT NULL DEFAULT 3,
          next_review TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_theses_symbol_time
          ON theses(symbol, created_at DESC);
        CREATE TABLE IF NOT EXISTS snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          price REAL,
          chg_pct REAL,
          report_text TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time
          ON snapshots(symbol, created_at DESC);
        CREATE TABLE IF NOT EXISTS evidence (
          evidence_id TEXT PRIMARY KEY,
          symbol TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          url TEXT NOT NULL DEFAULT '',
          snippet TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          tier TEXT NOT NULL DEFAULT '',
          published TEXT NOT NULL DEFAULT '',
          quality_score INTEGER NOT NULL DEFAULT 0,
          payload_json TEXT NOT NULL DEFAULT '{}',
          first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_evidence_symbol_time
          ON evidence(symbol, last_seen DESC);
        CREATE TABLE IF NOT EXISTS schema_meta (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          question TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          tags TEXT NOT NULL DEFAULT '',
          due_date TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_stocks (
          project_id INTEGER NOT NULL,
          symbol TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          role TEXT NOT NULL DEFAULT 'watch',
          note TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(project_id,symbol),
          FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS hypothesis_evidence (
          thesis_id INTEGER NOT NULL,
          evidence_id TEXT NOT NULL,
          stance TEXT NOT NULL DEFAULT 'pending',
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          PRIMARY KEY(thesis_id,evidence_id),
          FOREIGN KEY(thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
          FOREIGN KEY(evidence_id) REFERENCES evidence(evidence_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS research_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER,
          symbol TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          task_type TEXT NOT NULL DEFAULT 'custom',
          question TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          prompt_version TEXT NOT NULL DEFAULT '',
          answer TEXT NOT NULL DEFAULT '',
          structured_json TEXT NOT NULL DEFAULT '{}',
          evidence_json TEXT NOT NULL DEFAULT '[]',
          stage_json TEXT NOT NULL DEFAULT '{}',
          coverage REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'complete',
          baseline_price REAL,
          baseline_date TEXT NOT NULL DEFAULT '',
          valid_until TEXT NOT NULL DEFAULT '',
          outcome TEXT NOT NULL DEFAULT '',
          outcome_note TEXT NOT NULL DEFAULT '',
          reviewed_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runs_symbol_time ON research_runs(symbol,created_at DESC);
        CREATE TABLE IF NOT EXISTS research_tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER,
          symbol TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          task_type TEXT NOT NULL DEFAULT 'custom',
          question TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'planning',
          current_stage TEXT NOT NULL DEFAULT 'plan',
          subquestions_json TEXT NOT NULL DEFAULT '[]',
          subquestion_status_json TEXT NOT NULL DEFAULT '[]',
          stage_json TEXT NOT NULL DEFAULT '{}',
          evidence_count INTEGER NOT NULL DEFAULT 0,
          coverage REAL NOT NULL DEFAULT 0,
          conflict_count INTEGER NOT NULL DEFAULT 0,
          unresolved_json TEXT NOT NULL DEFAULT '[]',
          run_id INTEGER,
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL,
          FOREIGN KEY(run_id) REFERENCES research_runs(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_symbol_time
          ON research_tasks(symbol,created_at DESC);
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY,
          symbol TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          event_type TEXT NOT NULL DEFAULT 'other',
          title TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          stance TEXT NOT NULL DEFAULT 'neutral',
          impact TEXT NOT NULL DEFAULT 'low',
          verified TEXT NOT NULL DEFAULT 'unknown',
          status TEXT NOT NULL DEFAULT 'new',
          earliest_at TEXT NOT NULL DEFAULT '',
          latest_at TEXT NOT NULL DEFAULT '',
          authority_source TEXT NOT NULL DEFAULT '',
          source_count INTEGER NOT NULL DEFAULT 1,
          contradiction TEXT NOT NULL DEFAULT '',
          priced_in TEXT NOT NULL DEFAULT 'unknown',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_symbol_time ON events(symbol,latest_at DESC);
        CREATE TABLE IF NOT EXISTS event_evidence (
          event_id TEXT NOT NULL,
          evidence_id TEXT NOT NULL,
          relation TEXT NOT NULL DEFAULT 'report',
          PRIMARY KEY(event_id,evidence_id),
          FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
          FOREIGN KEY(evidence_id) REFERENCES evidence(evidence_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS entity_nodes (
          node_id TEXT PRIMARY KEY,
          symbol TEXT NOT NULL DEFAULT '',
          node_type TEXT NOT NULL,
          label TEXT NOT NULL,
          attrs_json TEXT NOT NULL DEFAULT '{}',
          confidence REAL NOT NULL DEFAULT 0.5,
          verified INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entity_edges (
          edge_id TEXT PRIMARY KEY,
          symbol TEXT NOT NULL DEFAULT '',
          source_id TEXT NOT NULL,
          target_id TEXT NOT NULL,
          relation TEXT NOT NULL,
          evidence_id TEXT NOT NULL DEFAULT '',
          confidence REAL NOT NULL DEFAULT 0.5,
          verified INTEGER NOT NULL DEFAULT 0,
          attrs_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(source_id) REFERENCES entity_nodes(node_id) ON DELETE CASCADE,
          FOREIGN KEY(target_id) REFERENCES entity_nodes(node_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_edges_symbol ON entity_edges(symbol);
        CREATE TABLE IF NOT EXISTS alert_rules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER,
          symbol TEXT NOT NULL DEFAULT '',
          name TEXT NOT NULL DEFAULT '',
          rule_type TEXT NOT NULL,
          operator TEXT NOT NULL DEFAULT 'contains',
          threshold TEXT NOT NULL DEFAULT '',
          severity TEXT NOT NULL DEFAULT 'medium',
          enabled INTEGER NOT NULL DEFAULT 1,
          cooldown_minutes INTEGER NOT NULL DEFAULT 60,
          last_checked TEXT NOT NULL DEFAULT '',
          last_triggered TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alert_rule_id INTEGER,
          dedup_key TEXT NOT NULL UNIQUE,
          symbol TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL,
          message TEXT NOT NULL DEFAULT '',
          severity TEXT NOT NULL DEFAULT 'medium',
          status TEXT NOT NULL DEFAULT 'unread',
          evidence_url TEXT NOT NULL DEFAULT '',
          webhook_status TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          triggered_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(alert_rule_id) REFERENCES alert_rules(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status,triggered_at DESC);
        CREATE TABLE IF NOT EXISTS portfolios (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS positions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          portfolio_id INTEGER NOT NULL,
          symbol TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          shares REAL NOT NULL DEFAULT 0,
          cost_price REAL NOT NULL DEFAULT 0,
          industry TEXT NOT NULL DEFAULT '',
          tags TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL,
          UNIQUE(portfolio_id,symbol),
          FOREIGN KEY(portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS event_studies (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          benchmark TEXT NOT NULL DEFAULT '',
          event_date TEXT NOT NULL,
          returns_json TEXT NOT NULL DEFAULT '{}',
          excess_json TEXT NOT NULL DEFAULT '{}',
          comparability REAL NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          UNIQUE(event_id,symbol,benchmark)
        );
        CREATE TABLE IF NOT EXISTS source_health (
          engine TEXT PRIMARY KEY,
          ok INTEGER NOT NULL DEFAULT 0,
          result_count INTEGER NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT '',
          latency_ms INTEGER NOT NULL DEFAULT 0,
          successes INTEGER NOT NULL DEFAULT 0,
          failures INTEGER NOT NULL DEFAULT 0,
          checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entity_aliases (
          symbol TEXT NOT NULL,
          alias TEXT NOT NULL,
          alias_type TEXT NOT NULL DEFAULT 'custom',
          verified INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          PRIMARY KEY(symbol,alias)
        );
        """
    )
    _ensure_columns(conn, "theses", {
        "project_id": "INTEGER", "validation_metrics": "TEXT NOT NULL DEFAULT ''",
        "valid_until": "TEXT NOT NULL DEFAULT ''"})
    _ensure_columns(conn, "snapshots", {
        "fundamentals_json": "TEXT NOT NULL DEFAULT '{}'",
        "news_json": "TEXT NOT NULL DEFAULT '{}'",
        "thesis_json": "TEXT NOT NULL DEFAULT '{}'",
        "ai_run_id": "INTEGER"})
    _ensure_columns(conn, "evidence", {
        "canonical_url": "TEXT NOT NULL DEFAULT ''",
        "full_text": "TEXT NOT NULL DEFAULT ''",
        "content_hash": "TEXT NOT NULL DEFAULT ''",
        "provider": "TEXT NOT NULL DEFAULT ''",
        "score_json": "TEXT NOT NULL DEFAULT '{}'",
        "event_id": "TEXT NOT NULL DEFAULT ''",
        "item_kind": "TEXT NOT NULL DEFAULT 'first_report'",
        "page_refs": "TEXT NOT NULL DEFAULT ''",
        "fetch_status": "TEXT NOT NULL DEFAULT 'pending'",
        "fetch_error": "TEXT NOT NULL DEFAULT ''"})
    _ensure_columns(conn, "events", {
        "cluster_key": "TEXT NOT NULL DEFAULT ''",
        "earliest_source": "TEXT NOT NULL DEFAULT ''",
        "latest_source": "TEXT NOT NULL DEFAULT ''"})
    _ensure_columns(conn, "research_runs", {"task_id": "INTEGER"})
    _ensure_columns(conn, "research_tasks", {
        "subquestion_status_json": "TEXT NOT NULL DEFAULT '[]'"})
    _ensure_columns(conn, "source_health", {
        "last_success_at": "TEXT NOT NULL DEFAULT ''",
        "last_error_at": "TEXT NOT NULL DEFAULT ''",
        "rate_limited_until": "TEXT NOT NULL DEFAULT ''"})
    try:
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
          evidence_id UNINDEXED, symbol UNINDEXED, title, snippet, full_text, source,
          tokenize='unicode61')""")
        fts_count = int(conn.execute("SELECT COUNT(*) FROM evidence_fts").fetchone()[0])
        evidence_count = int(conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0])
        # 只在首次迁移或索引明显缺失时重建。正常请求不再反复扫描全文库。
        if old_version < 2 or (evidence_count and not fts_count):
            conn.execute("DELETE FROM evidence_fts")
            conn.execute("""INSERT INTO evidence_fts(evidence_id,symbol,title,snippet,full_text,source)
              SELECT evidence_id,symbol,title,snippet,full_text,source FROM evidence""")
    except sqlite3.OperationalError:
        pass
    conn.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','3')")


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    existing = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def db_location() -> str:
    return str(_db_path())


def meta_get(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def meta_set(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
                     (str(key), str(value)))


def connection() -> sqlite3.Connection:
    """领域服务使用的事务连接；调用方必须用 with 或显式关闭。"""
    return _connect()


def evidence_id_for(symbol: str, title: str, url: str) -> str:
    raw = (f"{str(symbol).zfill(6)}|"
           f"{str(url or '').split('#')[0].split('?')[0].lower()}|{title or ''}")
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:32]


def canonicalize_url(url: str) -> str:
    try:
        p = urlsplit(str(url or "").strip())
        kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                if not k.lower().startswith("utm_") and k.lower() not in
                {"spm", "from", "source", "ref", "track", "share_token"}]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"),
                           urlencode(kept), ""))
    except Exception:
        return str(url or "").split("#", 1)[0]


def add_watchlist(symbol: str, name: str = "", tags: str = "", note: str = "") -> None:
    symbol = str(symbol or "").strip().zfill(6)
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO watchlist(symbol,name,tags,note,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET
                 name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE watchlist.name END,
                 tags=CASE WHEN excluded.tags<>'' THEN excluded.tags ELSE watchlist.tags END,
                 note=CASE WHEN excluded.note<>'' THEN excluded.note ELSE watchlist.note END,
                 updated_at=excluded.updated_at""",
            (symbol, name.strip(), tags.strip(), note.strip(), now, now),
        )


def remove_watchlist(symbol: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol=?", (str(symbol).zfill(6),))


def is_watched(symbol: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM watchlist WHERE symbol=?",
                           (str(symbol).zfill(6),)).fetchone()
    return bool(row)


def list_watchlist() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT w.*,
               (SELECT COUNT(*) FROM evidence e WHERE e.symbol=w.symbol) AS evidence_count,
               (SELECT MAX(created_at) FROM snapshots s WHERE s.symbol=w.symbol) AS last_snapshot,
               (SELECT next_review FROM theses t WHERE t.symbol=w.symbol
                 ORDER BY id DESC LIMIT 1) AS next_review
               FROM watchlist w ORDER BY w.updated_at DESC""").fetchall()
    return [dict(r) for r in rows]


def save_thesis(symbol: str, name: str, *, thesis: str, catalysts: str,
                risks: str, invalidation: str, conviction: int,
                next_review: str = "", validation_metrics: str = "",
                valid_until: str = "", project_id: Optional[int] = None) -> int:
    add_watchlist(symbol, name)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO theses(symbol,name,thesis,catalysts,risks,invalidation,
               conviction,next_review,created_at,validation_metrics,valid_until,project_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(symbol).zfill(6), name.strip(), thesis.strip(), catalysts.strip(),
             risks.strip(), invalidation.strip(), max(1, min(int(conviction), 5)),
             str(next_review or ""), _now(), validation_metrics.strip(),
             str(valid_until or ""), project_id),
        )
        return int(cur.lastrowid)


def latest_thesis(symbol: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM theses WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (str(symbol).zfill(6),)).fetchone()
    return dict(row) if row else None


def thesis_context(symbol: str) -> str:
    """给 AI 的紧凑个人研究证据；明确它是待审计假设，不是既定事实。"""
    row = latest_thesis(symbol)
    if not row:
        return "（尚未建立个人投资假设）"
    return (
        f"记录时间：{row['created_at']}；信念强度：{row['conviction']}/5；"
        f"下次复核：{row['next_review'] or '未设定'}\n"
        f"核心假设：{row['thesis'] or '未填写'}\n"
        f"预期催化：{row['catalysts'] or '未填写'}\n"
        f"风险与反方证据：{row['risks'] or '未填写'}\n"
        f"失效条件：{row['invalidation'] or '未填写'}\n"
        "注意：以上是用户待验证的研究假设，不是事实；必须用其他证据审计。"
    )


def thesis_history(symbol: str, limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM theses WHERE symbol=? ORDER BY id DESC LIMIT ?",
            (str(symbol).zfill(6), int(limit))).fetchall()
    return [dict(r) for r in rows]


def save_snapshot(symbol: str, name: str, *, price: Optional[float],
                  chg_pct: Optional[float], report_text: str,
                  payload: Optional[dict] = None, fundamentals: Optional[dict] = None,
                  news: Optional[dict] = None, thesis: Optional[dict] = None,
                  ai_run_id: Optional[int] = None) -> int:
    add_watchlist(symbol, name)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO snapshots(symbol,name,price,chg_pct,report_text,
               payload_json,created_at,fundamentals_json,news_json,thesis_json,ai_run_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (str(symbol).zfill(6), name.strip(), price, chg_pct,
             report_text or "", json.dumps(payload or {}, ensure_ascii=False,
                                             default=str), _now(),
             json.dumps(fundamentals or {}, ensure_ascii=False, default=str),
             json.dumps(news or {}, ensure_ascii=False, default=str),
             json.dumps(thesis or {}, ensure_ascii=False, default=str), ai_run_id),
        )
        return int(cur.lastrowid)


def list_snapshots(symbol: str, limit: int = 30) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT *
               FROM snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?""",
            (str(symbol).zfill(6), int(limit))).fetchall()
    return [dict(r) for r in rows]


def save_news_items(symbol: str, items: Iterable) -> int:
    """把新闻条目增量写入证据库；同一 URL/标题只更新时间，不重复膨胀。"""
    symbol = str(symbol or "").strip().zfill(6)
    now = _now()
    count = 0
    with _connect() as conn:
        for item in items or []:
            title = str(getattr(item, "title", "") or "").strip()
            url = str(getattr(item, "url", "") or "").strip()
            if not title and not url:
                continue
            eid = evidence_id_for(symbol, title, url)
            channel = str(getattr(item, "channel", "") or "")
            try:
                from news_intel import channel_tier
                tier = channel_tier(channel)
            except Exception:
                tier = ""
            payload = {
                "stance": getattr(item, "stance", ""),
                "impact": getattr(item, "impact", ""),
                "verified": getattr(item, "verified", ""),
                "reason": getattr(item, "reason", ""),
            }
            canonical = canonicalize_url(url)
            content_hash = hashlib.sha256(
                f"{title}|{getattr(item, 'snippet', '')}".encode("utf-8", "ignore")
            ).hexdigest() if title else ""
            score = dict(getattr(item, "quality_breakdown", {}) or {})
            score.update({"quality": int(getattr(item, "quality_score", 0) or 0),
                          "tier": tier, "reason": getattr(item, "reason", ""),
                          "cluster_size": int(getattr(item, "cluster_size", 1) or 1)})
            conn.execute(
                """INSERT INTO evidence(evidence_id,symbol,title,url,snippet,source,
                   tier,published,quality_score,payload_json,first_seen,last_seen,
                   canonical_url,content_hash,score_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(evidence_id) DO UPDATE SET
                     snippet=excluded.snippet, source=excluded.source,
                     tier=excluded.tier, published=excluded.published,
                     quality_score=MAX(evidence.quality_score,excluded.quality_score),
                     payload_json=excluded.payload_json,last_seen=excluded.last_seen,
                     canonical_url=excluded.canonical_url,
                     content_hash=CASE WHEN evidence.full_text<>'' THEN evidence.content_hash
                       ELSE excluded.content_hash END,
                     score_json=excluded.score_json""",
                (eid, symbol, title, url,
                 str(getattr(item, "snippet", "") or "")[:2000],
                 str(getattr(item, "source_name", "") or channel), tier,
                 str(getattr(item, "published", "") or ""),
                 int(getattr(item, "quality_score", 0) or 0),
                 json.dumps(payload, ensure_ascii=False), now, now, canonical, content_hash,
                 json.dumps(score, ensure_ascii=False)),
            )
            count += 1
        try:
            conn.execute("DELETE FROM evidence_fts WHERE symbol=?", (symbol,))
            conn.execute("""INSERT INTO evidence_fts(evidence_id,symbol,title,snippet,full_text,source)
              SELECT evidence_id,symbol,title,snippet,full_text,source FROM evidence WHERE symbol=?""",
                         (symbol,))
        except sqlite3.OperationalError:
            pass
    return count


def list_evidence(symbol: str, limit: int = 80, query: str = "") -> list[dict]:
    params: list = [str(symbol).zfill(6)]
    where = "symbol=?"
    if query.strip():
        where += " AND (title LIKE ? OR snippet LIKE ? OR source LIKE ?)"
        term = f"%{query.strip()}%"
        params.extend([term, term, term])
    params.append(int(limit))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM evidence WHERE {where} "  # noqa: S608 - 固定片段
            "ORDER BY quality_score DESC,last_seen DESC LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def search_evidence(query: str, symbol: str = "", limit: int = 30) -> list[dict]:
    """FTS5/BM25 本地优先检索；FTS 不可用或无结果时回退 LIKE。"""
    q = str(query or "").strip()
    if not q:
        return list_evidence(symbol, limit=limit) if symbol else []
    with _connect() as conn:
        try:
            sql = """SELECT e.*, bm25(evidence_fts,0,0,4,2,1,1) AS bm25_score
                     FROM evidence_fts JOIN evidence e USING(evidence_id)
                     WHERE evidence_fts MATCH ?"""
            params: list = [q]
            if symbol:
                sql += " AND e.symbol=?"
                params.append(str(symbol).zfill(6))
            sql += " ORDER BY bm25_score LIMIT ?"
            params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()
            if rows:
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            pass
        term = f"%{q}%"
        where = "(title LIKE ? OR snippet LIKE ? OR full_text LIKE ? OR source LIKE ?)"
        params = [term, term, term, term]
        if symbol:
            where += " AND symbol=?"
            params.append(str(symbol).zfill(6))
        params.append(int(limit))
        rows = conn.execute(
            f"SELECT *,0 AS bm25_score FROM evidence WHERE {where} "
            "ORDER BY quality_score DESC,last_seen DESC LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def update_evidence_content(evidence_id: str, *, full_text: str,
                            canonical_url: str = "", provider: str = "",
                            page_refs: str = "") -> None:
    text = str(full_text or "")[:200_000]
    digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest() if text else ""
    with _connect() as conn:
        conn.execute("""UPDATE evidence SET full_text=?,content_hash=?,
          canonical_url=CASE WHEN ?<>'' THEN ? ELSE canonical_url END,
          provider=CASE WHEN ?<>'' THEN ? ELSE provider END,
          page_refs=CASE WHEN ?<>'' THEN ? ELSE page_refs END,last_seen=?,
          fetch_status='complete',fetch_error=''
          WHERE evidence_id=?""",
                     (text, digest, canonical_url, canonical_url, provider, provider,
                      page_refs, page_refs, _now(), evidence_id))
        try:
            row = conn.execute("SELECT * FROM evidence WHERE evidence_id=?",
                               (evidence_id,)).fetchone()
            if row:
                conn.execute("DELETE FROM evidence_fts WHERE evidence_id=?", (evidence_id,))
                conn.execute("""INSERT INTO evidence_fts
                  (evidence_id,symbol,title,snippet,full_text,source) VALUES(?,?,?,?,?,?)""",
                             (row["evidence_id"], row["symbol"], row["title"],
                              row["snippet"], row["full_text"], row["source"]))
        except sqlite3.OperationalError:
            pass


def update_evidence_fetch_status(evidence_id: str, status: str, error: str = "") -> None:
    with _connect() as conn:
        conn.execute("UPDATE evidence SET fetch_status=?,fetch_error=?,last_seen=? WHERE evidence_id=?",
                     (status, str(error)[:300], _now(), evidence_id))


def save_source_health(engine: str, ok: bool, count: int, error: str = "",
                       latency_ms: int = 0) -> None:
    now = _now()
    rate_until = ((datetime.now() + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
                  if "429" in str(error) else "")
    with _connect() as conn:
        conn.execute("""INSERT INTO source_health(engine,ok,result_count,error,latency_ms,
          successes,failures,checked_at,last_success_at,last_error_at,rate_limited_until)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(engine) DO UPDATE SET ok=excluded.ok,result_count=excluded.result_count,
          error=excluded.error,latency_ms=excluded.latency_ms,
          successes=source_health.successes+excluded.successes,
          failures=source_health.failures+excluded.failures,checked_at=excluded.checked_at,
          last_success_at=CASE WHEN excluded.ok=1 THEN excluded.checked_at
            ELSE source_health.last_success_at END,
          last_error_at=CASE WHEN excluded.ok=0 THEN excluded.checked_at
            ELSE source_health.last_error_at END,
          rate_limited_until=CASE WHEN excluded.ok=1 THEN ''
            WHEN excluded.rate_limited_until<>'' THEN excluded.rate_limited_until
            ELSE source_health.rate_limited_until END""",
                     (engine, int(ok), int(count), str(error)[:300], int(latency_ms),
                      int(ok), int(not ok), now, now if ok else "",
                      "" if ok else now, rate_until))


def list_source_health() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM source_health ORDER BY engine").fetchall()
    return [dict(r) for r in rows]


def upsert_entity_alias(symbol: str, alias: str, alias_type: str = "custom",
                        verified: bool = False) -> None:
    alias = str(alias or "").strip()
    if not alias:
        return
    with _connect() as conn:
        conn.execute("""INSERT INTO entity_aliases(symbol,alias,alias_type,verified,created_at)
          VALUES(?,?,?,?,?) ON CONFLICT(symbol,alias) DO UPDATE SET
          alias_type=excluded.alias_type,verified=MAX(entity_aliases.verified,excluded.verified)""",
                     (str(symbol).zfill(6), alias, alias_type, int(verified), _now()))


def list_entity_aliases(symbol: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM entity_aliases WHERE symbol=? ORDER BY verified DESC,alias",
                            (str(symbol).zfill(6),)).fetchall()
    return [dict(r) for r in rows]


def overview() -> dict:
    with _connect() as conn:
        row = conn.execute(
            """SELECT
              (SELECT COUNT(*) FROM watchlist) AS watchlist,
              (SELECT COUNT(*) FROM theses) AS theses,
              (SELECT COUNT(*) FROM snapshots) AS snapshots,
              (SELECT COUNT(*) FROM evidence) AS evidence,
              (SELECT COUNT(*) FROM projects) AS projects,
              (SELECT COUNT(*) FROM events) AS events,
              (SELECT COUNT(*) FROM notifications WHERE status='unread') AS alerts""").fetchone()
    return dict(row)


def export_dossier(symbol: str) -> str:
    code = str(symbol).zfill(6)
    thesis = latest_thesis(code)
    snaps = list_snapshots(code, 10)
    evidence = list_evidence(code, 20)
    name = (thesis or {}).get("name") or (snaps[0].get("name") if snaps else code)
    lines = [f"# {name}（{code}）个人研究档案", ""]
    if thesis:
        lines += ["## 当前投资假设", thesis.get("thesis") or "（未填写）", "",
                  "### 催化剂", thesis.get("catalysts") or "（未填写）", "",
                  "### 主要风险", thesis.get("risks") or "（未填写）", "",
                  "### 失效条件", thesis.get("invalidation") or "（未填写）", "",
                  f"信念强度：{thesis.get('conviction')}/5　复核日期：{thesis.get('next_review') or '未设定'}",
                  f"\n记录时间：{thesis.get('created_at')}", ""]
    lines.append("## 研究快照")
    for s in snaps:
        lines.append(f"- {s['created_at']}：价格 {s.get('price') or '--'}，涨跌幅 {s.get('chg_pct') or 0:+.2f}%")
    lines += ["", "## 重点证据"]
    for e in evidence:
        title = e.get("title") or "未命名"
        url = e.get("url") or ""
        label = f"[{title}]({url})" if url else title
        lines.append(f"- {e.get('published') or '时间未知'}｜{e.get('source') or e.get('tier')}｜{label}")
    return "\n".join(lines) + "\n"
