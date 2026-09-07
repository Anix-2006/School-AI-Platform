(() => {
  const state = { token: null, tenantId: null };

  async function request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    if (options.auth) {
      if (!state.token) throw new Error("Not authenticated yet");
      headers.Authorization = `Bearer ${state.token}`;
    }
    const res = await fetch(path, { ...options, headers });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }
    if (!res.ok) {
      let detail = text.slice(0, 240);
      if (data && data.detail != null) {
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
      throw new Error(detail || `${res.status} ${res.statusText}`);
    }
    return data;
  }

  async function ensureAuth() {
    if (state.token) return state;
    const auth = await request("/auth/demo-token");
    state.token = auth.access_token;
    state.tenantId = auth.tenant_id;
    return state;
  }

  window.SchoolAPI = {
    state,
    request,
    ensureAuth,
    async health() {
      return request("/health");
    },
    async listStudents() {
      await ensureAuth();
      return request("/students", { auth: true });
    },
    async createStudent(payload) {
      await ensureAuth();
      return request("/students", {
        method: "POST",
        body: JSON.stringify(payload),
        auth: true,
      });
    },
    async chat(payload) {
      await ensureAuth();
      return request("/chat", {
        method: "POST",
        body: JSON.stringify(payload),
        auth: true,
      });
    },
    async runDailyBatch() {
      await ensureAuth();
      return request("/ops/daily-batch", { method: "POST", auth: true });
    },
    async runInsightScan() {
      await ensureAuth();
      return request("/ops/insight-scan", { method: "POST", auth: true });
    },
  };
})();
