import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
const W = 800, H = 540;
const fill = d => (d.type === 'root' ? '#222' : d.bot >= 60 ? '#D85A30' : '#534AB7');
const rad = d => (d.type === 'root' ? 9 : 4 + d.influence * 14);

export default function Graph({ data, sel, hl, onPick, cut }) {
  const ref = useRef();
  useEffect(() => {
    const nodes = data.nodes.map(d => ({ ...d })), links = data.links.map(d => ({ ...d }));
    const casc = nodes.some(d => typeof d.depth === 'number');  // thread cascade: root at the top, arrows = direction of spread
    const svg = d3.select(ref.current); svg.selectAll('*').remove();
    svg.append('defs').append('marker').attr('id', 'arr').attr('viewBox', '0 -4 8 8').attr('refX', 16).attr('markerWidth', 6).attr('markerHeight', 6)
      .attr('orient', 'auto').append('path').attr('d', 'M0,-4L8,0L0,4').attr('fill', '#999');
    const box = svg.append('g');
    svg.call(d3.zoom().scaleExtent([0.3, 5]).on('zoom', e => box.attr('transform', e.transform))).on('dblclick.zoom', null);
    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(casc ? 50 : 35))
      .force('charge', d3.forceManyBody().strength(casc ? -90 : -45))
      .force('collide', d3.forceCollide(d => rad(d) + 2));
    if (casc) sim.force('y', d3.forceY(d => Math.min(H - 30, 40 + d.depth * 80)).strength(0.9)).force('x', d3.forceX(W / 2).strength(0.04));
    else sim.force('x', d3.forceX(W / 2).strength(0.07)).force('y', d3.forceY(H / 2).strength(0.07));
    const l = box.append('g').attr('stroke', '#aaa').attr('stroke-opacity', 0.6).selectAll('line').data(links).join('line')
      .attr('marker-end', casc ? 'url(#arr)' : null);
    const n = box.append('g').selectAll('g').data(nodes).join('g').attr('class', 'n').style('cursor', 'pointer')
      .on('click', (e, d) => onPick(d.id))
      .call(d3.drag()
        .on('start', (e, d) => { sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on('end', (e, d) => { sim.alphaTarget(0); d.fx = d.fy = null; }));
    n.append('circle').attr('r', rad).attr('fill', fill);
    n.filter(d => d.type === 'root' || d.influence > 0.3 || d.bot >= 60).append('text').text(d => d.label)
      .attr('dy', d => rad(d) + 11).attr('text-anchor', 'middle').attr('font-size', 11).attr('fill', 'currentColor');
    n.append('title').text(d => `${d.id}\ninfluence ${d.influence} | bot score ${d.bot}/100${d.misinfo ? ` | misinfo ${Math.round(d.misinfo * 100)}%` : ''}`);
    sim.on('tick', () => {
      nodes.forEach(d => { d.x = Math.max(10, Math.min(W - 10, d.x)); d.y = Math.max(10, Math.min(H - 10, d.y)); });
      l.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      n.attr('transform', d => `translate(${d.x},${d.y})`);
    });
    return () => sim.stop();
  }, [data]);

  useEffect(() => {
    const h = new Set(hl || []);
    d3.select(ref.current).selectAll('circle')
      .attr('stroke', d => (d.id === sel ? '#378ADD' : h.has(d.id) ? '#EF9F27' : d.misinfo > 0.5 ? '#E24B4A' : 'none'))
      .attr('stroke-width', d => (d.id === sel || h.has(d.id) ? 4 : 2));
  }, [sel, hl, data]);

  useEffect(() => {
    const v = d => (d.t <= cut ? null : 'none'), r = d3.select(ref.current);
    r.selectAll('.n').style('display', v); r.selectAll('line').style('display', v);
  }, [cut, data]);

  return <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="graph" />;
}
