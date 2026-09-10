/**
 * The walk that aims PW Review's release run.
 *
 * Getting this wrong in one direction re-runs the sampler for nothing; in the
 * other, the keepers are delivered to nobody. Both are silent, so both are
 * tested here.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { outputsDownstream, type NodeId } from '../src/core/reach.ts';

function graph(edges: Record<string, NodeId[]>, outputs: NodeId[], modes: Record<string, number> = {}) {
  return {
    next: (id: NodeId) => edges[String(id)] ?? [],
    isOutput: (id: NodeId) => outputs.map(String).includes(String(id)),
    mode: (id: NodeId) => modes[String(id)] ?? 0,
  };
}

describe('outputsDownstream', () => {
  it('finds outputs through any depth of ordinary nodes', () => {
    const g = graph({ 1: [2, 3], 2: [4], 3: [5] }, [4, 5]);
    assert.deepEqual(outputsDownstream(1, g).sort(), ['4', '5']);
  });

  it('never returns the start node or anything upstream of it', () => {
    const g = graph({ 0: [1], 1: [2] }, [0, 1, 2]);
    assert.deepEqual(outputsDownstream(1, g), ['2']);
  });

  it('walks through a bypassed node but does not aim at it', () => {
    const g = graph({ 1: [2], 2: [3] }, [2, 3], { 2: 4 });
    assert.deepEqual(outputsDownstream(1, g), ['3']);
  });

  it('stops at a muted node', () => {
    const g = graph({ 1: [2], 2: [3] }, [3], { 2: 2 });
    assert.deepEqual(outputsDownstream(1, g), []);
  });

  it('survives a cycle and reports each output once', () => {
    const g = graph({ 1: [2, 3], 2: [3], 3: [2] }, [2, 3]);
    assert.deepEqual(outputsDownstream(1, g).sort(), ['2', '3']);
  });

  it('returns ids as strings, which is what the server compares against', () => {
    const g = graph({ 1: [7] }, [7]);
    assert.deepEqual(outputsDownstream(1, g), ['7']);
  });
});
