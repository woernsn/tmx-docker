import { readFileSync, writeFileSync } from 'node:fs';

const path = '/app/src/services/publishing/publicUrl.ts';
const source = readFileSync(path, 'utf8');
const marker = '  const url = new URL(globalThis.location.href);';
if (source.split(marker).length !== 2) throw new Error('TMX public URL source changed');

// The upstream localhost fallback assumes the viewer is on the next port.
// In this image TMX is at / and the viewer is on the same origin at /pub/.
const rootUrl = "  if (globalThis.location.pathname === '/') return `${globalThis.location.origin}/pub`;\n\n";
writeFileSync(path, source.replace(marker, rootUrl + marker));
