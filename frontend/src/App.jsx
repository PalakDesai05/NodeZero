import { useEffect, useMemo, useRef, useState } from 'react';
import Graph from './Graph';
import { api } from './api';

const enc = encodeURIComponent, sleep = ms => new Promise(r => setTimeout(r, ms));

export default function App() {
  const [src, setSrc] = useState('sample'), [sources, setSources] = useState(['sample']), [mode, setMode] = useState('topic'), [q, setQ] = useState('');
  const [g, setG] = useState({ nodes: [], links: [] }), [view, setView] = useState('network'), [threads, setThreads] = useState([]), [rid, setRid] = useState('');
  const [stats, setStats] = useState(null), [sum, setSum] = useState(null), [info, setInfo] = useState(null), [sel, setSel] = useState(''), [hl, setHl] = useState([]), [path, setPath] = useState(null);
  const [top, setTop] = useState({ inf: [], bot: [] }), [news, setNews] = useState(null), [rng, setRng] = useState('7d'), [d1, setD1] = useState(''), [d2, setD2] = useState('');
  const [err, setErr] = useState(''), [note, setNote] = useState(''), [busy, setBusy] = useState(false), [fetching, setFetching] = useState(false);
  const [cut, setCut] = useState(0), [play, setPlay] = useState(false), [onlyLinked, setOnlyLinked] = useState(true), [scored, setScored] = useState(false);
  const [txt, setTxt] = useState(''), [det, setDet] = useState(null), timer = useRef();
  const run = f => { setErr(''); setBusy(true); return f().catch(e => setErr(e.message)).finally(() => setBusy(false)); };

  const show = (gr, i) => { setG(gr); setCut(gr.nodes.length ? Math.max(...gr.nodes.map(n => n.t)) : 0); setPlay(false); setInfo(i || null); setSel(''); setHl([]); setPath(null); };
  const side = async r => setSum(await api(`/summary${r ? `?root=${enc(r)}` : ''}`));
  const cascade = async r => { const c = await api(`/cascade?root=${enc(r)}`); setRid(r); setView('cascade'); show(c.graph, c.info); await side(r); };
  const network = async () => { setRid(''); setView('network'); show(await api('/network-graph')); await side(''); };
  const openThread = r => run(() => cascade(r));
  const whole = () => run(network);
  const load = s => run(async () => {
    setScored(false); setStats(await api(`/load?source=${s}`, 'POST')); api('/sources').then(setSources);
    setTop({ inf: await api('/top-influencers'), bot: await api('/top-bot-scores') });
    const t = await api('/threads'); setThreads(t);
    t.length ? await cascade(t[0].root_id) : await network();   // start with the busiest thread's cascade
  });
  useEffect(() => { load(src); }, [src]);

  const pick = id => run(async () => {
    setSel(id); setPath(null); setHl([]);
    setInfo(await api(id.startsWith('root:') ? `/root/${enc(id.slice(5))}` : `/user/${enc(id)}`));
  });
  const trace = () => run(async () => { const r = await api(`/trace-root/${enc(sel)}`); setPath([...r.path, ...(r.cycle_detected ? ['(cycle detected, stopped)'] : [])]); setHl(r.authors); });
  const scoreAll = () => run(async () => { await api('/score-all', 'POST'); setScored(true); rid ? await cascade(rid) : await network(); });
  const check = () => txt.trim() && run(async () => setDet(await api(`/classify?text=${enc(txt)}`)));

  const custom = rng === 'custom' && d1 && d2;
  const newsFor = () => /^https?:/.test(q.trim()) ? null : api(`/news?q=${enc(q.trim())}${custom ? `&after=${d1}&before=${d2}` : `&when=${rng === 'custom' ? '1y' : rng}`}`).then(setNews).catch(e => { setNews([]); setErr(e.message); });
  const search = () => {
    const s = q.trim(); if (!s) return;
    if (mode === 'user') return pick((g.nodes.find(n => n.id.toLowerCase().includes(s.toLowerCase())) || { id: s }).id);
    newsFor();
    run(async () => {
      try {
        const r = await api(`/search-live?q=${enc(s)}${custom ? `&since=${d1}&until=${d2}` : ''}`, 'POST');
        setNote(`Found ${r.posts} posts: Bluesky ${r.bluesky}, Mastodon ${r.mastodon}, Reddit ${r.reddit}, YouTube ${r.youtube}, duplicates removed ${r.duplicates_removed}${r.errors.length ? ' | ' + r.errors.join('; ') : ''}`);
        src === 'search' ? load('search') : setSrc('search');
      } catch (e) { show({ nodes: [], links: [] }); setStats(null); setSum(null); setThreads([]); throw e; }
    });
  };
  const live = () => run(async () => {
    const s = src === 'bluesky' ? 'bluesky' : 'mastodon';
    await api(`/refresh?source=${s}`, 'POST'); setFetching(true); setNote('');
    let st; while ((st = await api(`/refresh-status?source=${s}`)).running) await sleep(3000);
    setFetching(false); setNote(`${s} fetch finished: ${st.log || 'no output'}`); src === s ? load(s) : setSrc(s);
  }).finally(() => setFetching(false));

  const shown = useMemo(() => {
    if (view !== 'network' || !onlyLinked) return g;
    const ids = new Set(g.links.flatMap(l => [l.source, l.target])), nodes = g.nodes.filter(n => ids.has(n.id));
    return nodes.length ? { nodes, links: g.links } : g;
  }, [g, onlyLinked, view]);
  const ts = g.nodes.map(n => n.t), lo = Math.min(...ts), hi = Math.max(...ts);
  useEffect(() => {
    if (!play) return;
    timer.current = setInterval(() => setCut(c => { if (c >= hi) { setPlay(false); return hi; } return c + (hi - lo) / 60; }), 150);
    return () => clearInterval(timer.current);
  }, [play]);
  const togglePlay = () => { if (!play && cut >= hi) setCut(lo); setPlay(!play); };

  const Row = ({ k, v }) => <div className="row"><span>{k.replace(/_/g, ' ')}</span><b>{v}</b></div>;
  return (
    <div className="app">
      <header>
        <b>NodeZero</b>
        <select value={mode} onChange={e => setMode(e.target.value)}><option value="topic">Find threads on a topic</option><option value="user">Find an account</option></select>
        <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && search()} placeholder={mode === 'topic' ? 'Topic, e.g. IPL, flood, election' : 'Username, e.g. user_alpha'} />
        <button onClick={search}>Search</button>
        <button onClick={live} disabled={fetching}>{fetching ? 'Fetching…' : 'Refresh live data'}</button>
        <select value={rng} onChange={e => setRng(e.target.value)}><option value="1d">Last 24h</option><option value="7d">Last 7 days</option><option value="1y">Last year</option><option value="custom">Custom dates</option></select>
        {rng === 'custom' && <><input type="date" value={d1} onChange={e => setD1(e.target.value)} style={{ flex: 0 }} /><input type="date" value={d2} onChange={e => setD2(e.target.value)} style={{ flex: 0 }} /></>}
        <select value={src} onChange={e => setSrc(e.target.value)}>{[...new Set([...sources, src])].map(s => <option key={s}>{s}</option>)}</select>
      </header>
      {err && <div className="err">{err}</div>}
      {note && <div className="stats">{note}</div>}
      <div className="stats">
        {stats && Object.entries(stats).map(([k, v]) => <span key={k}>{k.replace(/_/g, ' ')}: <b>{v}</b></span>)}
        {fetching ? <span>Fetching live data, 1-3 minutes…</span> : busy && <span>Loading…</span>}
      </div>
      <div className="bar">
        <span>Thread</span>
        <select value={rid} onChange={e => e.target.value && openThread(e.target.value)}>
          <option value="">{threads.length ? 'Pick a thread…' : 'No threads'}</option>
          {threads.map(t => <option key={t.root_id} value={t.root_id}>{t.replies} replies · {t.users} users · {t.snippet}</option>)}
        </select>
        <button onClick={whole}>Whole network</button>
        {view === 'network' && <label><input type="checkbox" checked={onlyLinked} onChange={e => setOnlyLinked(e.target.checked)} /> Hide accounts with no replies</label>}
        <span className="muted">{view === 'cascade' ? 'Cascade: root at the top, arrows show how the thread spread' : 'Whole network'}</span>
      </div>
      <main>
        <section>
          <Graph data={shown} sel={sel} hl={hl} onPick={pick} cut={cut} />
          <div className="legend"><i style={{ background: '#222' }} />Root thread <i style={{ background: '#534AB7' }} />Account <i style={{ background: '#D85A30' }} />Likely bot (score ≥ 60) <i className="ring" />Likely misinformation (AI detector) <i className="ring o" />Trace path</div>
          <div className="time">
            <button onClick={togglePlay}>{play ? 'Pause' : 'Play'}</button>
            <input type="range" min={lo} max={hi} step="any" value={cut} onChange={e => { setPlay(false); setCut(+e.target.value); }} />
            <span>{isFinite(cut) ? new Date(cut * 1000).toLocaleString() : ''}</span>
          </div>
        </section>
        <aside>
          <div className="card">
            <h4>{info ? (info.user || 'Thread: ' + (info.opening_post || info.thread_id)) : 'Click a node'}</h4>
            {info && Object.entries(info).map(([k, v]) => Array.isArray(v)
              ? <div key={k}><h5>{k.replace(/_/g, ' ')}</h5>{v.map((x, i) => <p key={i}>{x}</p>)}</div> : <Row key={k} k={k} v={v} />)}
            {info?.user && <button onClick={trace}>Trace root</button>}
            {path && path.map((x, i) => <p key={i}>{i + 1}. {x}</p>)}
          </div>
          {sum && <div className="card">
            <h4>Bot detection</h4>
            <p>{sum.flagged_accounts.length} of {sum.accounts_checked} accounts flagged (score ≥ 60 of 100)</p>
            {!sum.bots.length && <p>No bot-like accounts here.</p>}
            {sum.bots.map(b => <p key={b.user} className="link" onClick={() => pick(b.user)}><span>{b.user}<br /><small>{b.why}</small></span><b>{b.score}</b></p>)}
            <small>Heuristic only: reply volume, username digits, out/in-degree ratio.</small>
          </div>}
          {sum && <div className="card">
            <h4>Key findings</h4>
            <p className="link" onClick={() => pick(sum.root_account)}>Root account <b>{sum.root_account}</b></p>
            <p className="link" onClick={() => pick(sum.top_spreader)}>Top spreader <b>{sum.top_spreader}</b></p>
          </div>}
          <div className="card">
            <h4>Top influencers</h4>{top.inf.map(t => <p key={t.user} className="link" onClick={() => pick(t.user)}>{t.user} <b>{t.score}</b></p>)}
            <h4>Top bot scores</h4>{top.bot.map(t => <p key={t.user} className="link" onClick={() => pick(t.user)}>{t.user} <b>{t.score}</b></p>)}
          </div>
          <div className="card">
            <h4>Experimental AI detector</h4>
            <small>Small demo model, off by default. It does not affect the spread, influence or bot results.</small>
            <button onClick={scoreAll}>{scored ? 'Re-run on all posts' : 'Run on all posts'}</button>
            {sum?.misinfo && <>
              <p><b className={'lvl ' + sum.misinfo.level}>{sum.misinfo.level}</b> · {sum.misinfo.share} of posts flagged ({sum.misinfo.posts} of {sum.misinfo.total})</p>
              {sum.misinfo.riskiest.map((r, i) => <p key={i} className="link" onClick={() => pick(r.user)}><span><b>{r.score}</b> {r.text}</span></p>)}
            </>}
            <textarea rows="3" value={txt} onChange={e => setTxt(e.target.value)} placeholder="Or paste one post / caption to check" />
            <button onClick={check}>Check</button>
            {det && <p><b>{Math.round(det.score * 100)}%</b> {det.verdict}</p>}
          </div>
          {news && <details className="card" open><summary><b>News headlines ({news.length})</b></summary>
            {news.slice(0, 10).map((n, i) => <p key={i}><a href={n.link} target="_blank" rel="noreferrer">{n.title}</a><br /><small>{n.source} · {n.date}</small></p>)}
          </details>}
        </aside>
      </main>
    </div>
  );
}
