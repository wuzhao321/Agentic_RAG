import { readFile, writeFile, mkdir } from 'node:fs/promises';
const assets = {};
for (const [path, file, type] of [['/', 'index.html', 'text/html'], ['/style.css', 'style.css', 'text/css'], ['/app.js', 'app.js', 'text/javascript']]) {
  assets[path] = { body: await readFile(`public/${file}`, 'utf8'), type: `${type}; charset=utf-8` };
}
const source = await readFile('worker/index.js', 'utf8');
await mkdir('dist/server', { recursive: true });
await writeFile('dist/server/index.js', `const ASSET_BUNDLE = ${JSON.stringify(assets)};\n${source}`);
try {
  const manifest = await readFile('.openai/hosting.json', 'utf8');
  await mkdir('dist/.openai', { recursive: true }); await writeFile('dist/.openai/hosting.json', manifest);
} catch (error) { if (error.code !== 'ENOENT') throw error; }
console.log('Built knowledge workspace Worker.');
