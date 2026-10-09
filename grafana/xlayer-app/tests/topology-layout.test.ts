import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Resource } from '../src/infrastructure';
import { topologyLayout } from '../src/topology-layout';
const node = (id: string, kind: string, type: Resource['type'] = 'node', role = 'gpu-node'): Resource => ({ key: `${kind}:${id}`, cluster: 'cluster', id, kind, type, role, mapping: 'configured', collector: 'unknown', resource: id });
const edge = (source: Resource, destination: Resource, relation = 'declared') => ({ source: source.key, destination: destination.key, relation, state: 'configured' });

test('compact Compute / Fabric / Storage placement matches the supplied HTML without changing identities', () => {
  const c0 = node('compute-0', 'compute'), c1 = node('compute-1', 'compute'), fabric = node('fabric', 'compute', 'fabric', 'network');
  const mds = node('mds-0', 'storage', 'node', 'mds'), ds = node('ds-0', 'storage', 'node', 'ds');
  const layout = topologyLayout([ds, c1, fabric, mds, c0], []);
  const positions = layout.nodes.map(box => [box.node.id, box.x, box.y]);
  assert.deepEqual(positions, [['compute-0', 26, 55], ['compute-1', 26, 151], ['fabric', 298, 108], ['mds-0', 570, 31], ['ds-0', 570, 122]]);
  assert.equal(layout.width, 814); assert.equal(layout.height, 326);
  for (const box of layout.nodes) assert.strictEqual(box.node, [c0, c1, fabric, mds, ds].find(value => value.key === box.node.key));
});

test('cubic paths anchor at source/destination card boundaries and retain forward, reverse and loop declarations', () => {
  const compute = node('compute', 'compute'), fabric = node('fabric', 'compute', 'fabric', 'network'), storage = node('ds', 'storage', 'node', 'ds');
  const declarations = [edge(compute, fabric), edge(fabric, storage), edge(storage, compute), edge(compute, compute)];
  const layout = topologyLayout([compute, fabric, storage], declarations);
  assert.deepEqual(layout.edges.map(route => route.edge), declarations);
  for (const route of layout.edges) {
    const source = layout.nodes.find(box => box.node.key === route.edge.source)!, destination = layout.nodes.find(box => box.node.key === route.edge.destination)!;
    assert.ok(route.start.x === source.x || route.start.x === source.x + source.width);
    assert.ok(route.end.x === destination.x || route.end.x === destination.x + destination.width);
    assert.ok(route.start.y >= source.y && route.start.y <= source.y + source.height);
    assert.ok(route.end.y >= destination.y && route.end.y <= destination.y + destination.height);
    assert.match(route.path, /^M .* C /);
  }
  assert.match(layout.edges[2].path, / 8/, 'direct Storage→Compute bypasses the Fabric card, not an inferred hop');
});

test('missing endpoints and device relationships remain in the ledger rather than fabricated diagram resources', () => {
  const compute = node('compute', 'compute'), gpu = node('gpu', 'compute', 'gpu', 'gpu');
  const missing = { source: compute.key, destination: 'missing', relation: 'declared unknown', state: 'unknown' };
  const layout = topologyLayout([compute, gpu], [edge(compute, gpu), missing]);
  assert.equal(layout.nodes.length, 1); assert.equal(layout.edges.length, 0); assert.equal(layout.unroutedEdges.length, 2);
  assert.strictEqual(layout.unroutedEdges[1].edge, missing); assert.match(layout.unroutedEdges[1].reason, /Endpoint/);
});

test('long identity and collector states are preserved without ellipsis while the layout stays deterministic and bounded', () => {
  const nodes = Array.from({ length: 40 }, (_, i) => node(`compute-node-with-a-long-identity-${i}`, 'compute'));
  const before = JSON.stringify(nodes), a = topologyLayout(nodes, []), b = topologyLayout(nodes, []);
  assert.ok(a.nodes.length <= 24); assert.equal(a.limited, true); assert.deepEqual(a, b); assert.equal(JSON.stringify(nodes), before);
  for (const box of a.nodes) { assert.equal(box.nameLines.join(''), box.node.id); assert.equal(box.node.collector, 'unknown'); }
});

test('no configured Fabric keeps the middle column empty and direct relationships intact', () => {
  const compute = node('compute', 'compute'), storage = node('storage', 'storage', 'node', 'ds'), declaration = edge(compute, storage);
  const layout = topologyLayout([compute, storage], [declaration]);
  assert.equal(layout.columns[1].shown, 0); assert.equal(layout.nodes.filter(box => box.column === 1).length, 0);
  assert.strictEqual(layout.edges[0].edge, declaration);
});
