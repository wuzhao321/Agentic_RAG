import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
// Same request handler as the deployment artifact; no credentials in browser code.
const source = await readFile('dist/server/index.js', 'utf8');
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const server = createServer(async (incoming, outgoing) => {
  try {
    const body = ['GET', 'HEAD'].includes(incoming.method) ? undefined : incoming;
    const request = new Request(`http://127.0.0.1:4173${incoming.url}`, { method: incoming.method, headers: incoming.headers, body, ...(body ? { duplex: 'half' } : {}) });
    const response = await worker.fetch(request, { RAG_BACKEND_URL: process.env.RAG_BACKEND_URL, RAG_BACKEND_TOKEN: process.env.RAG_BACKEND_TOKEN });
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch { outgoing.writeHead(500); outgoing.end('Preview error'); }
});
server.listen(4173, '127.0.0.1', () => console.log('Local: http://127.0.0.1:4173'));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => process.exit(0)));
