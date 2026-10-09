(() => {
  "use strict";

  const TOKEN_KEY = "school.authToken";
  const SESSION_KEY = "school.authContext";
  const DEFAULT_TIMEOUT = 15000;
  const state = {
    token: readSession(TOKEN_KEY),
    tenantId: null,
    schoolName: null,
    whatsappPhoneNumberId: null,
  };
  let refreshPromise = null;

  try {
    Object.assign(state, JSON.parse(readSession(SESSION_KEY) || "{}"));
  } catch {
    sessionStorage.removeItem(SESSION_KEY);
  }

  function readSession(key) {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function writeSession(key, value) {
    try {
      sessionStorage.setItem(key, value);
    } catch {
      // The app still works when browser storage is disabled.
    }
  }

  function announce(kind, message = "", retry = null) {
    window.dispatchEvent(new CustomEvent("schoolapi:status", {
      detail: { kind, message, retry },
    }));
  }

  function plainError(status, data, timedOut) {
    if (timedOut) return "The school service is taking longer than expected. Please try again.";
    if (!navigator.onLine) return "You are offline. Check your connection and try again.";
    if (status === 400 || status === 422) {
      const detail = data && typeof data.detail === "string" ? data.detail : "";
      return detail || "Please check the information and try again.";
    }
    if (status === 401) return "Your session expired. Please try again.";
    if (status === 403) return "You do not have access to this information.";
    if (status === 404) return "That information could not be found.";
    if (status === 409) return "This change conflicts with existing information.";
    if (status === 429) return "The service is busy. Please wait a moment and try again.";
    if (status >= 500) return "The school service is waking up or temporarily unavailable. Please try again.";
    return "Something went wrong. Please try again.";
  }

  function saveAuth(auth) {
    state.token = auth.access_token;
    state.tenantId = auth.tenant_id;
    state.schoolName = auth.school_name;
    state.whatsappPhoneNumberId = auth.whatsapp_phone_number_id;
    writeSession(TOKEN_KEY, state.token);
    writeSession(SESSION_KEY, JSON.stringify({
      tenantId: state.tenantId,
      schoolName: state.schoolName,
      whatsappPhoneNumberId: state.whatsappPhoneNumberId,
    }));
    return state;
  }

  function clearAuth() {
    state.token = null;
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      // Ignore storage restrictions.
    }
  }

  async function refreshAuth() {
    if (!refreshPromise) {
      clearAuth();
      refreshPromise = request("/auth/demo-token", { timeout: 20000, retry: true }, true)
        .then(saveAuth)
        .finally(() => {
          refreshPromise = null;
        });
    }
    return refreshPromise;
  }

  async function request(path, options = {}, alreadyRetried = false) {
    const {
      auth = false,
      timeout = DEFAULT_TIMEOUT,
      retry = false,
      headers: optionHeaders = {},
      ...fetchOptions
    } = options;
    const headers = { ...optionHeaders };
    if (fetchOptions.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    if (auth) {
      if (!state.token) await refreshAuth();
      headers.Authorization = `Bearer ${state.token}`;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    const slowTimer = window.setTimeout(() => {
      announce("slow", "The school service is starting. This can take a few seconds.");
    }, 2500);

    try {
      const response = await fetch(path, {
        ...fetchOptions,
        headers,
        signal: controller.signal,
      });
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }

      if (response.status === 401 && auth && !alreadyRetried) {
        await refreshAuth();
        return request(path, options, true);
      }
      if (!response.ok) {
        const error = new Error(plainError(response.status, data, false));
        error.status = response.status;
        throw error;
      }
      announce("ready");
      return data;
    } catch (error) {
      const timedOut = error.name === "AbortError";
      const message = error.status
        ? error.message
        : plainError(0, null, timedOut);
      const safeRetry = retry || !fetchOptions.method || fetchOptions.method === "GET";
      const wrapped = new Error(message);
      wrapped.status = error.status || 0;
      announce("error", message, safeRetry ? () => request(path, options) : null);
      throw wrapped;
    } finally {
      window.clearTimeout(timer);
      window.clearTimeout(slowTimer);
    }
  }

  async function ensureAuth() {
    if (state.token && state.tenantId) return state;
    return refreshAuth();
  }

  function teacherHeaders(teacherId) {
    return teacherId ? { "X-Teacher-Id": teacherId } : {};
  }

  function guardianLabel(guardian) {
    const className = guardian.section
      ? `${guardian.grade}-${guardian.section}`
      : guardian.grade;
    const consent = guardian.has_consent ? "" : " - no consent";
    return `${guardian.name} (${guardian.relation || "guardian"} of ${guardian.student_name}, ${className})${consent}`;
  }

  async function fillGuardianSelect(select) {
    const guardians = await window.SchoolAPI.listGuardians();
    select.replaceChildren();
    guardians.forEach((guardian) => {
      const option = document.createElement("option");
      option.value = String(guardian.id);
      option.textContent = guardianLabel(guardian);
      select.appendChild(option);
    });
    return guardians;
  }

  window.SchoolAPI = {
    state,
    request,
    ensureAuth,
    clearAuth,
    guardianLabel,
    fillGuardianSelect,
    async health() {
      return request("/health", { retry: true });
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
      const current = await ensureAuth();
      const payload = {
        entry: [{ changes: [{ value: {
          metadata: { phone_number_id: current.whatsappPhoneNumberId },
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
