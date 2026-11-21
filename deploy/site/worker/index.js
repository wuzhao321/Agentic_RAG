// ASSET_BUNDLE is inserted by scripts/build.mjs. No runtime dependencies.
const securityHeaders = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'same-origin',
  'Cache-Control': 'no-store',
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self' https://chatgpt.com https://*.chatgpt.com",
};
function json(body, status = 200) { return new Response(JSON.stringify(body), { status, headers: { ...securityHeaders, 'Content-Type': 'application/json; charset=utf-8' } }); }
function error(status, code, message) { return json({ error: { code, message } }, status); }
function backendURL(env) {
  try { const url = new URL(env.RAG_BACKEND_URL); if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/' || !env.RAG_BACKEND_TOKEN) return null; return url; } catch { return null; }
}
function route(path, method) {
  if (path === '/review' && method === 'GET') return '/api/review';
  if (path === '/api/status' && method === 'GET') return path;
  if (path === '/api/query' && method === 'POST') return path;
  if (path === '/api/documents' && ['GET', 'POST'].includes(method)) return '/documents';
  if (path === '/api/ingest-jobs' && ['GET', 'POST'].includes(method)) return '/ingest-jobs';
  if (/^\/api\/ingest-jobs\/[a-f0-9]{32}$/.test(path) && method === 'GET') return path.slice(4);
  if (/^\/api\/ingest-jobs\/[a-f0-9]{32}\/retry$/.test(path) && method === 'POST') return path.slice(4);
  return null;
}
export default {
  async fetch(request, env = {}) {
    const url = new URL(request.url), method = request.method;
    const origin = request.headers.get('Origin');
    if (origin && origin !== url.origin) return error(403, 'invalid_origin', '仅允许同源请求');
    if (request.headers.get('Sec-Fetch-Site') === 'cross-site' && !['GET', 'HEAD'].includes(method)) return error(403, 'invalid_origin', '仅允许同源请求');
    if (url.pathname === '/api/connection' && method === 'GET') return json({ configured: Boolean(backendURL(env)) });
    const targetPath = route(url.pathname, method);
    if (targetPath) {
      const backend = backendURL(env);
      if (!backend) return error(503, 'backend_unconfigured', '站点尚未连接知识库服务');
      const target = new URL(targetPath, backend); target.search = url.search;
      const headers = new Headers({ 'Authorization': `Bearer ${env.RAG_BACKEND_TOKEN}`, 'Accept': 'application/json' });
      for (const name of ['Content-Type', 'Idempotency-Key']) if (request.headers.has(name)) headers.set(name, request.headers.get(name));
      const contentLength = request.headers.get('Content-Length');
      if (contentLength && (!/^\d+$/.test(contentLength) || Number(contentLength) > 20 * 1024 * 1024 + 256 * 1024)) return error(413, 'body_too_large', '单个上传文件不能超过 20 MB');
      let size = 0;
      const body = !['GET', 'HEAD'].includes(method) && request.body ? request.body.pipeThrough(new TransformStream({
        transform(chunk, controller) { size += chunk.byteLength; if (size > 20 * 1024 * 1024 + 256 * 1024) throw Error('body_too_large'); controller.enqueue(chunk); },
      })) : undefined;
      try {
        const response = await fetch(target, { method, headers, body, redirect: 'manual', signal: AbortSignal.timeout(240000), ...(body ? { duplex: 'half' } : {}) });
        if (response.status >= 300 && response.status < 400) return error(502, 'backend_redirect', '知识库服务地址配置有误');
        if (response.status === 401 || response.status === 403) return error(502, 'backend_auth', '知识库连接凭证或访问配置无效');
        if (!(response.headers.get('Content-Type') || '').includes('application/json')) return error(502, 'backend_response', '知识库服务暂时不可用');
        if (targetPath === '/api/review' && response.ok) {
          const review = await response.json();
          return new Response(review.html, { headers: { ...securityHeaders, 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': review.policy } });
        }
        return new Response(response.body, { status: response.status, headers: { ...securityHeaders, 'Content-Type': 'application/json; charset=utf-8' } });
      } catch (cause) {
        if (size > 20 * 1024 * 1024 + 256 * 1024) return error(413, 'body_too_large', '单个上传文件不能超过 20 MB');
        if (cause.name === 'TimeoutError') return error(504, 'backend_timeout', '处理超时，请稍后新建会话再试。任务状态可在入库任务中查看。');
        return error(502, 'backend_unreachable', '暂时无法连接知识库服务，请稍后重试');
      }
    }
    if (['GET', 'HEAD'].includes(method) && ASSET_BUNDLE[url.pathname]) {
      const asset = ASSET_BUNDLE[url.pathname]; return new Response(method === 'HEAD' ? null : asset.body, { headers: { ...securityHeaders, 'Content-Type': asset.type } });
    }
    return error(404, 'not_found', '页面或接口不存在');
  },
};
