const B = import.meta.env.VITE_API || 'http://localhost:8000';
export const api = async (p, m = 'GET') => {
  const r = await fetch(B + p, { method: m });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Request failed');
  return j;
};
