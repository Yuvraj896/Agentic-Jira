import { useEffect, useMemo, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || body.error || `Request failed (${response.status})`);
  return body;
}

function shortDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function StatusPill({ children }) {
  const text = children || 'Unknown';
  const className = String(text).toLowerCase().replaceAll(' ', '-');
  return <span className={`status status-${className}`}>{text}</span>;
}

const CHART_COLORS = ['#216649', '#f27a3d', '#7b61a8', '#d1a727', '#3f78a8', '#8c4f3e', '#68a35b'];

function InsightChart({ visualization }) {
  if (!visualization?.data?.length) return null;
  const maximum = Math.max(...visualization.data.map((item) => item.value), 1);
  const total = visualization.data.reduce((sum, item) => sum + item.value, 0);
  let cursor = 0;
  const gradient = visualization.data.map((item, index) => {
    const start = cursor;
    cursor += (item.value / total) * 100;
    return `${CHART_COLORS[index % CHART_COLORS.length]} ${start}% ${cursor}%`;
  }).join(', ');

  return (
    <section className="insight-chart" aria-label={visualization.title}>
      <h4>{visualization.title}</h4>
      {visualization.type === 'pie' ? (
        <div className="pie-layout">
          <div className="pie-chart" style={{ background: `conic-gradient(${gradient})` }} aria-hidden="true" />
          <div className="chart-legend">
            {visualization.data.map((item, index) => (
              <div key={item.label}><i style={{ background: CHART_COLORS[index % CHART_COLORS.length] }} /><span>{item.label}</span><strong>{item.value}</strong></div>
            ))}
          </div>
        </div>
      ) : (
        <div className="bar-chart">
          {visualization.data.map((item, index) => (
            <div className="bar-row" key={item.label}>
              <span>{item.label}</span>
              <div><i style={{ width: `${Math.max(4, (item.value / maximum) * 100)}%`, background: CHART_COLORS[index % CHART_COLORS.length] }} /></div>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ConnectScreen({ onConnected }) {
  const [pat, setPat] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const data = await api('/api/session/connect', { method: 'POST', body: JSON.stringify({ pat }) });
      setPat('');
      onConnected(data.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="connect-shell">
      <section className="connect-story">
        <div className="brand-mark">AJ</div>
        <p className="eyebrow">RADCOM · INTERNAL</p>
        <h1>Ask Jira.<br />See the evidence.</h1>
        <p className="lede">A read-only, live view of AIM work. No downloads, warehouse, or hidden mutations.</p>
        <div className="trust-list">
          <span>01</span><p>Your PAT stays in backend memory.</p>
          <span>02</span><p>Every answer shows its JQL.</p>
          <span>03</span><p>Jira permissions remain authoritative.</p>
        </div>
      </section>
      <section className="connect-card-wrap">
        <form className="connect-card" onSubmit={submit}>
          <p className="eyebrow">JIRA DATA CENTER</p>
          <h2>Connect your account</h2>
          <p className="muted">Create a token in <a href="https://jira-prod.radcom.co.il:8443/secure/ViewProfile.jspa?selectedTab=com.atlassian.pats.pats-plugin:jira-user-personal-access-tokens" target="_blank" rel="noreferrer">Jira Personal access tokens</a>, then paste it here. Do not add it to <code>.env</code>. It is discarded when you disconnect or the server restarts.</p>
          <label htmlFor="pat">Personal access token</label>
          <input
            id="pat"
            type="password"
            value={pat}
            onChange={(event) => setPat(event.target.value)}
            placeholder="Paste token"
            autoComplete="off"
            required
          />
          {error && <div className="error-box">{error}</div>}
          <button className="primary wide" type="submit" disabled={loading || !pat.trim()}>
            {loading ? 'Checking Jira…' : 'Connect to Jira'}
          </button>
          <p className="microcopy">Eight-hour idle timeout · HTTP-only session · Read-only application</p>
        </form>
      </section>
    </main>
  );
}

function ResultsTable({ result, onPage, onIssue }) {
  if (!result) return <div className="empty-state">Run a question or JQL search to see live Jira issues.</div>;
  const pageCount = Math.max(1, Math.ceil(result.total / result.page_size));
  return (
    <section className="results-card">
      <div className="results-head">
        <div><p className="eyebrow">LIVE JIRA RESULT</p><h2>{result.total.toLocaleString()} issues</h2></div>
        <div className="page-controls">
          <button className="ghost small" disabled={result.page <= 1} onClick={() => onPage(result.page - 1)}>←</button>
          <span>{result.page} / {pageCount}</span>
          <button className="ghost small" disabled={result.page >= pageCount} onClick={() => onPage(result.page + 1)}>→</button>
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Issue</th><th>Status</th><th>Priority</th><th>Assignee</th><th>Updated</th></tr></thead>
          <tbody>
            {result.issues.map((issue) => (
              <tr key={issue.key} onClick={() => onIssue(issue.key)}>
                <td><strong>{issue.key}</strong><span className="summary">{issue.summary}</span></td>
                <td><StatusPill>{issue.status}</StatusPill></td>
                <td>{issue.priority || '—'}</td>
                <td>{issue.assignee || 'Unassigned'}</td>
                <td>{shortDate(issue.updated)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!result.issues.length && <div className="empty-inline">No issues matched this JQL.</div>}
    </section>
  );
}

function IssuePanel({ issue, loading, onClose }) {
  if (!issue && !loading) return null;
  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside className="issue-drawer" onMouseDown={(event) => event.stopPropagation()}>
        <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
        {loading ? <div className="drawer-loading">Loading issue…</div> : (
          <>
            <p className="eyebrow">{issue.issue_type || 'ISSUE'} · {issue.project}</p>
            <h2>{issue.key}</h2>
            <h3>{issue.summary}</h3>
            <div className="detail-grid">
              <div><span>Status</span><StatusPill>{issue.status}</StatusPill></div>
              <div><span>Priority</span><strong>{issue.priority || '—'}</strong></div>
              <div><span>Assignee</span><strong>{issue.assignee}</strong></div>
              <div><span>Reporter</span><strong>{issue.reporter || '—'}</strong></div>
            </div>
            <a className="jira-link" href={issue.url} target="_blank" rel="noreferrer">Open in Jira ↗</a>
            <section className="drawer-section"><h4>Description</h4><p className="preline">{issue.description || 'No description.'}</p></section>
            {!!issue.components?.length && <section className="drawer-section"><h4>Components</h4><p>{issue.components.join(', ')}</p></section>}
            {!!Object.keys(issue.fields || {}).length && (
              <section className="drawer-section"><h4>Custom fields</h4>
                <dl className="field-list">{Object.entries(issue.fields).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl>
              </section>
            )}
            <section className="drawer-section"><h4>Comments <span>{issue.comments?.length || 0}</span></h4>
              {issue.comments?.length ? issue.comments.map((comment) => (
                <article className="comment" key={comment.id}><div><strong>{comment.author}</strong><time>{shortDate(comment.created)}</time></div><p className="preline">{comment.body}</p></article>
              )) : <p className="muted">No comments.</p>}
            </section>
          </>
        )}
      </aside>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [tab, setTab] = useState('ask');
  const [teams, setTeams] = useState([]);
  const [teamKey, setTeamKey] = useState('AIM');
  const [question, setQuestion] = useState('');
  const [jql, setJql] = useState('project = AIM ORDER BY updated DESC');
  const [messages, setMessages] = useState([]);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selectedIssue, setSelectedIssue] = useState(null);
  const [issueLoading, setIssueLoading] = useState(false);

  useEffect(() => {
    Promise.all([
      api('/api/session').then((data) => setUser(data.user)).catch(() => null),
      api('/api/teams').then(setTeams).catch(() => null),
    ]).finally(() => setCheckingSession(false));
  }, []);

  const activeTeam = useMemo(() => teams.find((team) => team.key === teamKey), [teams, teamKey]);

  async function disconnect() {
    await api('/api/session', { method: 'DELETE' }).catch(() => null);
    setUser(null); setMessages([]); setResult(null); setSelectedIssue(null); setError('');
  }

  async function runSearch(query = jql, page = 1, pageSize = result?.page_size || 25) {
    setBusy(true); setError('');
    try {
      const data = await api('/api/jira/search', {
        method: 'POST',
        body: JSON.stringify({ jql: query, page, page_size: pageSize }),
      });
      setJql(data.jql); setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function ask(event) {
    event.preventDefault();
    const prompt = question.trim();
    if (!prompt || busy) return;
    const prior = messages.slice(-10).map(({ role, content }) => ({ role, content }));
    setMessages((current) => [...current, { role: 'user', content: prompt }]);
    setQuestion(''); setBusy(true); setError('');
    try {
      const data = await api('/api/agent/chat', {
        method: 'POST',
        body: JSON.stringify({ message: prompt, history: prior, team_key: teamKey }),
      });
      setMessages((current) => [...current, { role: 'assistant', content: data.answer, warnings: data.warnings, jql: data.jql, visualization: data.visualization }]);
      if (data.jql) {
        setJql(data.jql);
        setResult({ jql: data.jql, total: data.total || 0, page: 1, page_size: Math.max(data.issues.length, 25), issues: data.issues });
      }
    } catch (err) {
      setError(err.message);
      setMessages((current) => [...current, { role: 'assistant', content: `I could not complete that request: ${err.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  async function openIssue(key) {
    setIssueLoading(true); setSelectedIssue(null); setError('');
    try {
      setSelectedIssue(await api(`/api/jira/issues/${encodeURIComponent(key)}`));
    } catch (err) {
      setError(err.message);
    } finally {
      setIssueLoading(false);
    }
  }

  if (checkingSession) return <div className="boot-screen">Starting AIM Jira Assistant…</div>;
  if (!user) return <ConnectScreen onConnected={setUser} />;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><span>AJ</span><div><strong>AIM Jira Assistant</strong><small>Read-only intelligence</small></div></div>
        <div className="top-actions">
          <div className="connection-dot"><i />{user.display_name}</div>
          <button className="ghost" onClick={disconnect}>Disconnect</button>
        </div>
      </header>

      <main className="workspace">
        <section className="hero-row">
          <div><p className="eyebrow">AIM WORKSPACE</p><h1>What needs attention?</h1><p>Ask in plain English or inspect Jira with exact JQL.</p></div>
          <div className="team-card"><span>Active team</span><strong>{activeTeam?.display_name || teamKey}</strong><small>{activeTeam?.verified ? 'Mapping verified' : 'Mapping needs live verification'}</small></div>
        </section>

        <nav className="tabs" aria-label="Search mode">
          <button className={tab === 'ask' ? 'active' : ''} onClick={() => setTab('ask')}>Ask Jira</button>
          <button className={tab === 'jql' ? 'active' : ''} onClick={() => setTab('jql')}>JQL Search</button>
        </nav>

        {tab === 'ask' ? (
          <section className="ask-panel">
            {!messages.length && (
              <div className="starter-grid">
                {[
                  'Visualize the current AIM sprint by status',
                  'Summarize the current AIM sprint',
                  'Show Major AIM issues assigned to yuvraj',
                ].map((starter) => <button key={starter} onClick={() => setQuestion(starter)}>{starter}<span>↗</span></button>)}
              </div>
            )}
            <div className="conversation">
              {messages.map((message, index) => (
                <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
                  <span>{message.role === 'user' ? 'YOU' : 'AIM ASSISTANT'}</span>
                  <p className="preline">{message.content}</p>
                  <InsightChart visualization={message.visualization} />
                  {message.warnings?.map((warning) => <div className="warning" key={warning}>{warning}</div>)}
                  {message.jql && <button className="jql-chip" onClick={() => { setJql(message.jql); setTab('jql'); }}>View and edit JQL →</button>}
                </article>
              ))}
              {busy && <article className="message assistant thinking"><span>AIM ASSISTANT</span><p>Reading live Jira data…</p></article>}
            </div>
            <form className="ask-box" onSubmit={ask}>
              <textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask about AIM issues, priorities, owners, or progress…" rows="3" />
              <div><small>OpenAI · {activeTeam?.display_name || 'AIM'} · read only</small><button className="primary" disabled={busy || !question.trim()}>Ask Jira <span>→</span></button></div>
            </form>
          </section>
        ) : (
          <section className="jql-panel">
            <div className="jql-editor">
              <div><p className="eyebrow">JQL</p><span>Runs directly against Jira with your permissions.</span></div>
              <textarea value={jql} onChange={(event) => setJql(event.target.value)} rows="4" spellCheck="false" />
              <button className="primary" onClick={() => runSearch(jql, 1, 25)} disabled={busy || !jql.trim()}>{busy ? 'Searching…' : 'Run search →'}</button>
            </div>
          </section>
        )}

        {error && <div className="error-box global-error">{error}</div>}
        <ResultsTable result={result} onPage={(page) => runSearch(result.jql, page, result.page_size)} onIssue={openIssue} />
      </main>
      <IssuePanel issue={selectedIssue} loading={issueLoading} onClose={() => { setSelectedIssue(null); setIssueLoading(false); }} />
    </div>
  );
}
