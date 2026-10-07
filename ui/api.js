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
    state.schoolName = auth.school_name;
    state.whatsappPhoneNumberId = auth.whatsapp_phone_number_id;
    return state;
  }

  function teacherHeaders(teacherId) {
    return teacherId ? { "X-Teacher-Id": teacherId } : {};
  }

  function guardianLabel(g) {
    const cls = g.section ? `${g.grade}-${g.section}` : g.grade;
    const consent = g.has_consent ? "" : " - no consent";
    return `${g.name} (${g.relation || "guardian"} of ${g.student_name}, ${cls})${consent}`;
  }

  async function fillGuardianSelect(select) {
    const guardians = await window.SchoolAPI.listGuardians();
    select.innerHTML = guardians
      .map((g) => `<option value="${g.id}">${guardianLabel(g)}</option>`)
      .join("");
    return guardians;
  }

  window.SchoolAPI = {
    state,
    request,
    ensureAuth,
    guardianLabel,
    fillGuardianSelect,
    async health() {
      return request("/health");
    },
    async listGuardians() {
      await ensureAuth();
      return request("/guardians", { auth: true });
    },
    async listTeachers() {
      await ensureAuth();
      return request("/teachers", { auth: true });
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
    async sendWhatsAppWebhook(fromNumber, text) {
      const s = await ensureAuth();
      const payload = {
        entry: [{ changes: [{ value: {
          metadata: { phone_number_id: s.whatsappPhoneNumberId },
          messages: [{ from: fromNumber, type: "text", text: { body: text } }],
        } }] }],
      };
      return request("/webhooks/whatsapp", { method: "POST", body: JSON.stringify(payload) });
    },
    async runDailyBatch() {
      await ensureAuth();
      return request("/ops/daily-batch", { method: "POST", auth: true });
    },
    async runInsightScan() {
      await ensureAuth();
      return request("/ops/insight-scan", { method: "POST", auth: true });
    },
    async teacherMe(teacherId) {
      await ensureAuth();
      return request("/teachers/me", { auth: true, headers: teacherHeaders(teacherId) });
    },
    async teacherTimetable(teacherId) {
      await ensureAuth();
      return request("/teachers/me/timetable", { auth: true, headers: teacherHeaders(teacherId) });
    },
    async teacherTasks(teacherId) {
      await ensureAuth();
      return request("/teachers/me/tasks", { auth: true, headers: teacherHeaders(teacherId) });
    },
    async completeTeacherTask(taskId, teacherId) {
      await ensureAuth();
      return request(`/teachers/me/tasks/${encodeURIComponent(taskId)}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "done" }),
        auth: true,
        headers: teacherHeaders(teacherId),
      });
    },
    async teacherSyllabus(teacherId) {
      await ensureAuth();
      return request("/teachers/me/syllabus", { auth: true, headers: teacherHeaders(teacherId) });
    },
    async teacherStudents(teacherId) {
      await ensureAuth();
      return request("/teachers/me/students", { auth: true, headers: teacherHeaders(teacherId) });
    },
  };
})();
