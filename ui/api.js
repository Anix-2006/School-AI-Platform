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
    let detail = data && typeof data.detail === "string" ? data.detail : "";
    if (!detail && Array.isArray(data?.detail)) {
      detail = data.detail.map((issue) => {
        const field = Array.isArray(issue.loc) ? issue.loc.at(-1) : "";
        return `${field ? `${field}: ` : ""}${issue.msg || "invalid value"}`;
      }).join("; ");
    }
    if (status === 400 || status === 422) {
      return detail || "Please check the information and try again.";
    }
    if (status === 401) return "Your session expired. Please try again.";
    if (status === 403) return "You do not have access to this information.";
    if (status === 404) return "That information could not be found.";
    if (status === 409) return detail || "This change conflicts with existing information.";
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

  const translations = Object.freeze({
    en: Object.freeze({
      appName: "School Parent AI",
      parentChat: "Parent chat",
      parentChatLead: "Ask about your child’s school day.",
      child: "Child",
      language: "Interface language",
      attendance: "Attendance",
      homework: "Homework",
      syllabus: "Syllabus",
      rank: "Rank card",
      updates: "Updates",
      updatesHelp: "Choose an action to ask for current information.",
      structuredUnavailable: "Structured update cards are not available from this API. Answers below come from the school assistant.",
      message: "Message",
      messagePlaceholder: "Ask about your child…",
      send: "Send",
      sending: "Sending",
      retry: "Try again",
      newConversation: "New conversation",
      welcome: "How can we help with your child’s school day?",
      noGuardians: "No linked children are available.",
      loadingChildren: "Loading children…",
      schoolConnected: "School service connected",
      noConsent: "Consent is required before the assistant can reply.",
      helper: "Helper",
      askHelper: "Ask a helper",
      helperLead: "Choose a topic. The orchestrator will send it to the right school helper.",
      dailyUpdate: "Daily update",
      dailyHelp: "Attendance, homework, and daily care.",
      academic: "Learning",
      academicHelp: "Syllabus, learning progress, and milestones.",
      parentTeacher: "Parent–teacher",
      parentTeacherHelp: "Messages, meetings, or concerns for a teacher.",
      communication: "School information",
      communicationHelp: "Timings, holidays, events, fees, and notices.",
      routedTo: "Routed to",
      decidedByClassifier: "intent classifier",
      decidedByRouter: "AI router",
      confidence: "confidence",
      whatsapp: "WhatsApp",
      whatsappLead: "Preview how a parent message is handled through the school’s WhatsApp channel.",
      sendAs: "Send as",
      customSender: "Someone else (unregistered number)",
      phoneNumber: "Phone number",
      registered: "Registered guardian",
      simulation: "Simulation only",
      channelConnected: "WhatsApp business number configured",
      channelNotConfigured: "WhatsApp business number is not configured; webhook delivery is simulated.",
      noReply: "No reply",
      webhookNote: "Registered guardians with consent can receive a reply. Custom numbers demonstrate the safe no-reply path.",
      printResponse: "Print response",
      agent: "Helper",
      typing: "School helper is typing",
      emptyMessage: "Write a message before sending.",
    }),
    hi: Object.freeze({
      appName: "स्कूल पैरेंट AI",
      parentChat: "अभिभावक चैट",
      parentChatLead: "अपने बच्चे के स्कूल दिवस के बारे में पूछें।",
      child: "बच्चा",
      language: "इंटरफ़ेस भाषा",
      attendance: "उपस्थिति",
      homework: "गृहकार्य",
      syllabus: "पाठ्यक्रम",
      rank: "रैंक कार्ड",
      updates: "अपडेट",
      updatesHelp: "नई जानकारी पूछने के लिए कोई विकल्प चुनें।",
      structuredUnavailable: "इस API में संरचित अपडेट कार्ड उपलब्ध नहीं हैं। नीचे के उत्तर स्कूल सहायक से आते हैं।",
      message: "संदेश",
      messagePlaceholder: "अपने बच्चे के बारे में पूछें…",
      send: "भेजें",
      sending: "भेजा जा रहा है",
      retry: "फिर कोशिश करें",
      newConversation: "नई बातचीत",
      welcome: "हम आपके बच्चे के स्कूल दिवस में कैसे मदद करें?",
      noGuardians: "कोई जुड़ा हुआ बच्चा उपलब्ध नहीं है।",
      loadingChildren: "बच्चे लोड हो रहे हैं…",
      schoolConnected: "स्कूल सेवा जुड़ी है",
      noConsent: "सहायक के उत्तर से पहले सहमति आवश्यक है।",
      helper: "सहायक",
      askHelper: "सहायक से पूछें",
      helperLead: "विषय चुनें। सही स्कूल सहायक उत्तर देगा।",
      dailyUpdate: "दैनिक अपडेट",
      dailyHelp: "उपस्थिति, गृहकार्य और दैनिक देखभाल।",
      academic: "पढ़ाई",
      academicHelp: "पाठ्यक्रम, सीखने की प्रगति और उपलब्धियाँ।",
      parentTeacher: "अभिभावक–शिक्षक",
      parentTeacherHelp: "शिक्षक के लिए संदेश, बैठक या चिंता।",
      communication: "स्कूल जानकारी",
      communicationHelp: "समय, छुट्टियाँ, कार्यक्रम, शुल्क और सूचनाएँ।",
      routedTo: "भेजा गया",
      decidedByClassifier: "इंटेंट क्लासिफ़ायर",
      decidedByRouter: "AI राउटर",
      confidence: "विश्वास",
      whatsapp: "व्हाट्सऐप",
      whatsappLead: "देखें कि स्कूल का व्हाट्सऐप चैनल संदेश कैसे संभालता है।",
      sendAs: "इस रूप में भेजें",
      customSender: "कोई अन्य (अपंजीकृत नंबर)",
      phoneNumber: "फ़ोन नंबर",
      registered: "पंजीकृत अभिभावक",
      simulation: "केवल सिमुलेशन",
      channelConnected: "व्हाट्सऐप बिज़नेस नंबर कॉन्फ़िगर है",
      channelNotConfigured: "व्हाट्सऐप बिज़नेस नंबर कॉन्फ़िगर नहीं है; वेबहुक डिलीवरी सिमुलेट होगी।",
      noReply: "कोई उत्तर नहीं",
      webhookNote: "सहमति वाले पंजीकृत अभिभावक उत्तर पा सकते हैं। कस्टम नंबर सुरक्षित नो-रिप्लाई पथ दिखाते हैं।",
      printResponse: "उत्तर प्रिंट करें",
      agent: "सहायक",
      typing: "स्कूल सहायक लिख रहा है",
      emptyMessage: "भेजने से पहले संदेश लिखें।",
    }),
    te: Object.freeze({
      appName: "స్కూల్ పేరెంట్ AI",
      parentChat: "తల్లిదండ్రుల చాట్",
      parentChatLead: "మీ పిల్లల పాఠశాల రోజు గురించి అడగండి.",
      child: "పిల్లలు",
      language: "ఇంటర్‌ఫేస్ భాష",
      attendance: "హాజరు",
      homework: "హోంవర్క్",
      syllabus: "సిలబస్",
      rank: "ర్యాంక్ కార్డ్",
      updates: "అప్‌డేట్‌లు",
      updatesHelp: "ప్రస్తుత సమాచారం అడగడానికి ఒక చర్యను ఎంచుకోండి.",
      structuredUnavailable: "ఈ APIలో నిర్మిత అప్‌డేట్ కార్డ్‌లు అందుబాటులో లేవు. దిగువ సమాధానాలు పాఠశాల సహాయకుడి నుండి వస్తాయి.",
      message: "సందేశం",
      messagePlaceholder: "మీ పిల్లల గురించి అడగండి…",
      send: "పంపండి",
      sending: "పంపుతోంది",
      retry: "మళ్లీ ప్రయత్నించండి",
      newConversation: "కొత్త సంభాషణ",
      welcome: "మీ పిల్లల పాఠశాల రోజుకు మేము ఎలా సహాయపడగలం?",
      noGuardians: "లింక్ చేసిన పిల్లలు అందుబాటులో లేరు.",
      loadingChildren: "పిల్లలను లోడ్ చేస్తోంది…",
      schoolConnected: "పాఠశాల సేవ కనెక్ట్ అయింది",
      noConsent: "సహాయకుడు సమాధానం ఇవ్వడానికి ముందు సమ్మతి అవసరం.",
      helper: "సహాయకుడు",
      askHelper: "సహాయకుడిని అడగండి",
      helperLead: "ఒక అంశాన్ని ఎంచుకోండి. సరైన పాఠశాల సహాయకుడు సమాధానం ఇస్తారు.",
      dailyUpdate: "రోజువారీ అప్‌డేట్",
      dailyHelp: "హాజరు, హోంవర్క్ మరియు రోజువారీ సంరక్షణ.",
      academic: "అభ్యాసం",
      academicHelp: "సిలబస్, అభ్యాస పురోగతి మరియు మైలురాళ్లు.",
      parentTeacher: "తల్లిదండ్రులు–ఉపాధ్యాయులు",
      parentTeacherHelp: "ఉపాధ్యాయునికి సందేశాలు, సమావేశాలు లేదా ఆందోళనలు.",
      communication: "పాఠశాల సమాచారం",
      communicationHelp: "సమయాలు, సెలవులు, కార్యక్రమాలు, ఫీజులు మరియు ప్రకటనలు.",
      routedTo: "పంపబడింది",
      decidedByClassifier: "ఇంటెంట్ క్లాసిఫయర్",
      decidedByRouter: "AI రౌటర్",
      confidence: "నమ్మకం",
      whatsapp: "వాట్సాప్",
      whatsappLead: "పాఠశాల వాట్సాప్ ఛానెల్ సందేశాన్ని ఎలా నిర్వహిస్తుందో చూడండి.",
      sendAs: "ఇలా పంపండి",
      customSender: "మరొకరు (నమోదు కాని నంబర్)",
      phoneNumber: "ఫోన్ నంబర్",
      registered: "నమోదిత సంరక్షకుడు",
      simulation: "సిమ్యులేషన్ మాత్రమే",
      channelConnected: "వాట్సాప్ బిజినెస్ నంబర్ కాన్ఫిగర్ అయింది",
      channelNotConfigured: "వాట్సాప్ బిజినెస్ నంబర్ కాన్ఫిగర్ కాలేదు; వెబ్‌హుక్ డెలివరీ సిమ్యులేట్ అవుతుంది.",
      noReply: "సమాధానం లేదు",
      webhookNote: "సమ్మతి ఉన్న నమోదిత సంరక్షకులు సమాధానం పొందగలరు. కస్టమ్ నంబర్లు సురక్షిత నో-రిప్లై మార్గాన్ని చూపుతాయి.",
      printResponse: "సమాధానం ప్రింట్ చేయండి",
      agent: "సహాయకుడు",
      typing: "పాఠశాల సహాయకుడు టైప్ చేస్తున్నారు",
      emptyMessage: "పంపే ముందు సందేశం రాయండి.",
    }),
  });

  function normalizeLanguage(language) {
    const shortCode = String(language || "en").toLowerCase().split(/[-_]/)[0];
    return Object.hasOwn(translations, shortCode) ? shortCode : "en";
  }

  function t(key, language = "en") {
    const code = normalizeLanguage(language);
    return translations[code][key] || translations.en[key] || key;
  }

  function localize(root = document, language = "en") {
    const code = normalizeLanguage(language);
    root.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = t(element.dataset.i18n, code);
    });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      element.setAttribute("placeholder", t(element.dataset.i18nPlaceholder, code));
    });
    document.documentElement.lang = code;
  }

  async function listParentContexts() {
    const [guardians, students] = await Promise.all([
      window.SchoolAPI.listGuardians(),
      window.SchoolAPI.listStudents(),
    ]);
    const studentsById = new Map(students.map((student) => [String(student.id), student]));
    return guardians.map((guardian) => ({
      ...guardian,
      age_tier: studentsById.get(String(guardian.student_id))?.age_tier || "primary_lower",
    }));
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
    translations,
    normalizeLanguage,
    t,
    localize,
    listParentContexts,
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
