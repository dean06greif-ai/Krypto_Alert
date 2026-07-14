// Minimal single-admin auth helper (token in localStorage).
export const getToken = () => localStorage.getItem('admin_token');
export const setToken = (t) => localStorage.setItem('admin_token', t);
export const clearToken = () => localStorage.removeItem('admin_token');
export const isAdmin = () => !!getToken();
export const authHeaders = () => {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
};
