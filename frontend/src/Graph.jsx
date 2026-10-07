import { useEffect, useRef } from "react";
import * as d3 from "d3";

export const OTHER_COLOR = "#94a3b8";
const W = 1000, H = 700;

export default function Graph({
  data,
  focus,
  onPick,
  filterIsolated = false,
  isolateHighRisk = false,
}) {
  const ref = useRef();
  const zoomRef = useRef();

  // Community rollups for top 8 and Other
  const allComms = d3
    .rollups(
      data?.nodes || [],
      (v) => v.length,
      (d) => d.community
    )
    .sort((a, b) => b[1] - a[1]);

  const top8 = allComms.slice(0, 8);
  const top8Names = new Set(top8.map(([c]) => c));
  const colorScale = d3
    .scaleOrdinal()
    .domain(top8.map(([c]) => c))
    .range(d3.schemeTableau10.slice(0, 8));

  const otherCount = allComms
    .slice(8)
    .reduce((sum, [, count]) => sum + count, 0);

  const getNodeColor = (comm) => {
    if (top8Names.has(comm)) return colorScale(comm);
    return OTHER_COLOR;
  };

  useEffect(() => {
    if (!data || !data.nodes || !ref.current) return;

    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();

    // Degree count for filtering isolated nodes
    const edgeCounts = {};
    (data.links || []).forEach((l) => {
      edgeCounts[l.source] = (edgeCounts[l.source] || 0) + 1;
      edgeCounts[l.target] = (edgeCounts[l.target] || 0) + 1;
    });

    // High risk clustering
    const highRiskSet = new Set(
      data.nodes
        .filter((n) => n.flagged || (n.misinfo_score && n.misinfo_score >= 0.4))
        .map((n) => n.id)
    );
    const highRiskClusterSet = new Set(highRiskSet);
    if (isolateHighRisk) {
      (data.links || []).forEach((l) => {
        if (highRiskSet.has(l.source)) highRiskClusterSet.add(l.target);
        if (highRiskSet.has(l.target)) highRiskClusterSet.add(l.source);
      });
    }

    // Filter active nodes and links
    const filteredNodes = data.nodes.filter((n) => {
      if (filterIsolated && !edgeCounts[n.id] && !n.is_root) return false;
      if (isolateHighRisk && !highRiskClusterSet.has(n.id)) return false;
      return true;
    });

    const activeIds = new Set(filteredNodes.map((n) => n.id));
    const filteredLinks = (data.links || []).filter(
      (l) => activeIds.has(l.source) && activeIds.has(l.target)
    );

    const nodes = filteredNodes.map((d) => ({ ...d }));
    const links = filteredLinks.map((d) => ({ ...d }));

    const g = svg.append("g").attr("class", "graph-zoom-group");
    const zoom = d3
      .zoom()
      .scaleExtent([0.1, 8])
      .on("zoom", (e) => g.attr("transform", e.transform));
    svg.call(zoom);
    zoomRef.current = { zoom, g, svg, nodes };

    const r = d3
      .scaleSqrt()
      .domain([0, d3.max(nodes, (d) => d.influence) || 1])
      .range([5, 20]);

    // Force simulation configured for fast stabilization
    const sim = d3
      .forceSimulation(nodes)
      .force("link", d3.forceLink(links).id((d) => d.id).distance(45))
      .force("charge", d3.forceManyBody().strength(-28))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("collide", d3.forceCollide((d) => r(d.influence) + 4));

    // RUN THE FORCE LAYOUT FOR A FIXED ~300 TICKS WITHOUT DRAWING
    for (let i = 0; i < 300; ++i) {
      sim.tick();
    }
    sim.stop(); // Stop simulation immediately; no per-tick state updates or drawing

    // Compute bounding box and initial zoom-to-fit
    const xExtent = d3.extent(nodes, (d) => d.x);
    const yExtent = d3.extent(nodes, (d) => d.y);
    if (xExtent[0] !== undefined && xExtent[1] !== undefined) {
      const graphW = xExtent[1] - xExtent[0] || W;
      const graphH = yExtent[1] - yExtent[0] || H;
      const midX = (xExtent[0] + xExtent[1]) / 2;
      const midY = (yExtent[0] + yExtent[1]) / 2;
      const scale = Math.max(
        0.15,
        Math.min(2.0, 0.88 / Math.max(graphW / W, graphH / H))
      );
      const initialTransform = d3.zoomIdentity
        .translate(W / 2 - scale * midX, H / 2 - scale * midY)
        .scale(scale);
      svg.call(zoom.transform, initialTransform);
    }

    // DRAW ONCE: Links
    const linkGroup = g.append("g").attr("class", "links-layer");
    const link = linkGroup
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", "#94a3b8")
      .attr("stroke-opacity", 0.25)
      .attr("stroke-width", (d) =>
        Math.min(3, 0.5 + Math.log(Math.max(1, d.weight || 1)))
      )
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);

    // DRAW ONCE: Nodes
    const nodeGroup = g.append("g").attr("class", "nodes-layer");
    const node = nodeGroup
      .selectAll("circle")
      .data(nodes)
      .join("circle")
      .attr("cx", (d) => d.x)
      .attr("cy", (d) => d.y)
      .attr("r", (d) => r(d.influence))
      .attr("fill", (d) => getNodeColor(d.community))
      .attr("stroke", (d) =>
        d.is_root ? "#0f172a" : d.flagged ? "#dc2626" : "#ffffff"
      )
      .attr("stroke-width", (d) => (d.is_root ? 3.5 : d.flagged ? 3 : 1.2))
      .classed("focus", (d) => d.id === focus)
      .style("cursor", "pointer")
      .on("click", (_, d) => onPick(d.id))
      .call(
        d3
          .drag()
          .on("drag", function (e, d) {
            d.x = e.x;
            d.y = e.y;
            d3.select(this).attr("cx", d.x).attr("cy", d.y);
            link
              .filter((l) => l.source.id === d.id)
              .attr("x1", d.x)
              .attr("y1", d.y);
            link
              .filter((l) => l.target.id === d.id)
              .attr("x2", d.x)
              .attr("y2", d.y);
            labels
              .filter((l) => l.id === d.id)
              .attr("x", d.x)
              .attr("y", d.y);
          })
      );

    // Tooltips detailing metrics and misinformation scoring rationale
    node.append("title").text(
      (d) =>
        `${d.id}\nCommunity: ${d.community}\nBot score: ${d.bot_score}/100${
          d.flagged ? " [FLAGGED BOT]" : ""
        }\nMisinfo risk: ${Math.round(
          (d.misinfo_score || 0.15) * 100
        )}% (scored via sensationalism +0.25, uppercase shouting +0.20, excessive punctuation +0.15)\nPageRank influence: ${d.influence}`
    );

    // Top 10 Influential labels
    const top10Ids = new Set(
      [...nodes]
        .sort((a, b) => (b.influence || 0) - (a.influence || 0))
        .slice(0, 10)
        .map((d) => d.id)
    );

    const labels = g
      .append("g")
      .attr("class", "node-labels")
      .selectAll("text")
      .data(nodes.filter((d) => top10Ids.has(d.id)))
      .join("text")
      .text((d) => d.id)
      .attr("x", (d) => d.x)
      .attr("y", (d) => d.y)
      .attr("font-size", "11px")
      .attr("font-weight", "600")
      .attr("fill", "#0f172a")
      .attr("text-anchor", "middle")
      .attr("dy", (d) => -r(d.influence) - 4)
      .attr("pointer-events", "none")
      .style("paint-order", "stroke")
      .style("stroke", "#ffffff")
      .style("stroke-width", "3px")
      .style("stroke-linejoin", "round");

    // If an initial focus is set, zoom and highlight immediately
    if (focus) {
      const target = nodes.find((n) => n.id === focus);
      if (target && target.x !== undefined) {
        const focusScale = 2.0;
        const targetTransform = d3.zoomIdentity
          .translate(W / 2 - focusScale * target.x, H / 2 - focusScale * target.y)
          .scale(focusScale);
        svg.transition().duration(500).call(zoom.transform, targetTransform);
      }
    }
  }, [data, filterIsolated, isolateHighRisk]);

  // Handle focus changes: highlight and smooth zoom/center to account
  useEffect(() => {
    if (!ref.current) return;
    const svg = d3.select(ref.current);
    svg.selectAll("circle").classed("focus", (d) => d && d.id === focus);

    if (focus && zoomRef.current) {
      const { zoom, nodes } = zoomRef.current;
      const target = (nodes || []).find((n) => n.id === focus);
      if (target && target.x !== undefined) {
        const focusScale = 2.2;
        const targetTransform = d3.zoomIdentity
          .translate(W / 2 - focusScale * target.x, H / 2 - focusScale * target.y)
          .scale(focusScale);
        svg
          .transition()
          .duration(650)
          .ease(d3.easeCubicOut)
          .call(zoom.transform, targetTransform);
      }
    }
  }, [focus]);

  return (
    <div className="graph-container">
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        className="graph"
      />
      <ul className="legend">
        {top8.map(([c, n]) => (
          <li key={c}>
            <i style={{ background: colorScale(c) }} />
            {c} <b>{n}</b>
          </li>
        ))}
        {(otherCount > 0 || allComms.length > 8) && (
          <li key="Other">
            <i style={{ background: OTHER_COLOR }} />
            Other <b>{otherCount}</b>
          </li>
        )}
      </ul>
    </div>
  );
}
