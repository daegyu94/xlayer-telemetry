import type { Resource } from './infrastructure';

export type TopologyRelationship = { source: string; destination: string; relation: string; state: string };
export type TopologyPoint = { x: number; y: number };
export type TopologyNodeBox = { node: Resource; column: number; x: number; y: number; width: number; height: number; nameLines: string[]; roleLines: string[] };
export type TopologyRoute = { edge: TopologyRelationship; index: number; start: TopologyPoint; end: TopologyPoint; path: string };
export type TopologyUnroutedEdge = { edge: TopologyRelationship; index: number; reason: string };
const natural = (a: string, b: string) => a.localeCompare(b, 'en', { numeric: true });
const columnFor = (node: Resource) => node.type === 'fabric' ? 1 : node.kind === 'storage' ? 2 : 0;
const storageOrder = (node: Resource) => /(^|-)(mds|metadata)(-|$)/i.test(node.role) ? 0 : /(^|-)(ds|data|storage-node)(-|$)/i.test(node.role) ? 1 : 2;
const lines = (value: string, size: number) => value.match(new RegExp(`.{1,${size}}`, 'gu')) || [''];

/** Compact mockup layout; query, resource identity and declared relationships stay unchanged. */
export function topologyLayout(nodes: Resource[], edges: TopologyRelationship[]) {
  const eligible = nodes.filter(node => node.type === 'fabric' || (['compute', 'storage'].includes(node.kind) && ['node', 'unknown'].includes(node.type)));
  const columns = [0, 1, 2].map(column => {
    const all = eligible.filter(node => columnFor(node) === column).sort((a, b) => natural(a.cluster, b.cluster)
      || (column === 2 ? storageOrder(a) - storageOrder(b) : 0) || natural(a.id, b.id) || natural(a.key, b.key));
    let y = [55, 108, 31][column];
    const boxes = all.slice(0, 8).map(node => {
      const nameLines = lines(node.id, 24), roleLines = lines(`${node.kind}/${node.role} · ${node.mapping}`, 36);
      const height = 67 + (nameLines.length - 1) * 15 + (roleLines.length - 1) * 11;
      const box = { node, column, x: [26, 298, 570][column], y, width: 218, height, nameLines, roleLines };
      y += [96, 96, 91][column] + height - 67;
      return box;
    });
    return { column, title: ['COMPUTE', 'NETWORK', 'STORAGE'][column], x: [27, 300, 570][column], y: [27, 78, 19][column], total: all.length, shown: boxes.length, boxes };
  });
  const boxes = columns.flatMap(column => column.boxes), byKey = new Map(boxes.map(box => [box.node.key, box]));
  const known = new Map(nodes.map(node => [node.key, node]));
  const routes: TopologyRoute[] = [], unroutedEdges: TopologyUnroutedEdge[] = [];
  let width = 814;
  edges.forEach((edge, index) => {
    const source = byKey.get(edge.source), destination = byKey.get(edge.destination);
    let reason: string | undefined;
    if (!known.has(edge.source) || !known.has(edge.destination)) reason = 'Endpoint not present in returned inventory';
    else if (!source || !destination) reason = [known.get(edge.source), known.get(edge.destination)].some(node => node && ['gpu', 'nic', 'ssd'].includes(node.type))
      ? 'Device relationship · inspect component inventory and its declared resource map'
      : 'Compact diagram node limit or component outside displayed columns';
    else if (routes.length >= 96) reason = 'Compact diagram edge limit · declaration retained below';
    if (reason || !source || !destination) { unroutedEdges.push({ edge, index, reason: reason || 'Endpoint unavailable' }); return; }
    const forward = source.column < destination.column;
    const start = { x: source.x + (forward || source.column === destination.column ? source.width : 0), y: source.y + source.height / 2 };
    const end = { x: destination.x + (forward ? 0 : destination.width), y: destination.y + destination.height / 2 };
    let path: string;
    if (source.node.key === destination.node.key) {
      start.y = source.y + source.height * .3; end.y = source.y + source.height * .7;
      const outside = start.x + 38; width = Math.max(width, outside + 16);
      path = `M ${start.x} ${start.y} C ${outside} ${start.y}, ${outside} ${end.y}, ${end.x} ${end.y}`;
    } else if (source.column === destination.column) {
      const outside = Math.max(start.x, end.x) + 26; width = Math.max(width, outside + 16);
      path = `M ${start.x} ${start.y} C ${outside} ${start.y}, ${outside} ${end.y}, ${end.x} ${end.y}`;
    } else if (Math.abs(source.column - destination.column) === 2 && columns[1].shown > 0) {
      // A direct declaration stays separate from Fabric; drawing through its
      // card would visually invent an intermediate hop.
      const direction = forward ? 1 : -1, a = start.x + direction * 60, b = end.x - direction * 60;
      path = `M ${start.x} ${start.y} C ${start.x + direction * 30} ${start.y}, ${a} 8, ${a} 8 C ${a + (b - a) / 3} 8, ${b - (b - a) / 3} 8, ${b} 8 C ${b} 8, ${end.x - direction * 30} ${end.y}, ${end.x} ${end.y}`;
    } else {
      const midpoint = (start.x + end.x) / 2;
      path = `M ${start.x} ${start.y} C ${midpoint} ${start.y}, ${midpoint} ${end.y}, ${end.x} ${end.y}`;
    }
    routes.push({ edge, index, start, end, path });
  });
  return { width, height: Math.max(326, ...boxes.map(box => box.y + box.height + 42)),
    columns: columns.map(({ boxes: _boxes, ...column }) => column), nodes: boxes, edges: routes, unroutedEdges,
    limited: eligible.length > boxes.length || unroutedEdges.some(item => /limit/.test(item.reason)) };
}
