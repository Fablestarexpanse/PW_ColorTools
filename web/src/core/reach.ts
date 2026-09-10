/**
 * Which output nodes sit downstream of a node.
 *
 * PW Review's release run is queued as a partial run aimed at exactly these,
 * so the run that delivers the keepers touches nothing else in the graph: no
 * other preview, no text node, no loader that happens to be an output. Pure,
 * so the walk is tested without a graph.
 */

export type NodeId = number | string;

export interface Reach {
  /** Nodes fed by this node's outputs. */
  next: (id: NodeId) => NodeId[];
  /** Whether ComfyUI treats the node as an output. */
  isOutput: (id: NodeId) => boolean;
  /** Muted nodes stop the walk; bypassed ones pass it through but are no target. */
  mode: (id: NodeId) => number;
}

const MUTED = 2;
const BYPASSED = 4;

export function outputsDownstream(start: NodeId, reach: Reach): string[] {
  const seen = new Set<string>([String(start)]);
  const found: string[] = [];
  const queue = [...reach.next(start)];
  while (queue.length) {
    const id = queue.shift()!;
    const key = String(id);
    if (seen.has(key)) continue;
    seen.add(key);
    const mode = reach.mode(id);
    if (mode === MUTED) continue;
    if (mode !== BYPASSED && reach.isOutput(id)) found.push(key);
    queue.push(...reach.next(id));
  }
  return found;
}
