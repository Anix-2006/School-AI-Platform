(() => {
  "use strict";

  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("sidebarBackdrop");
  const openBtn = document.getElementById("openSidebar");
  const closeBtn = document.getElementById("closeSidebar");
  const main = document.querySelector("main");
  const desktopQuery = window.matchMedia("(min-width: 1024px)");
  let desktopSchool = null;
  let returnFocus = null;

  if (main) {
    main.id = main.id || "main-content";
    const skipLink = document.createElement("a");
    skipLink.className = "skip-link";
    skipLink.href = `#${main.id}`;
    skipLink.textContent = "Skip to main content";
    document.body.prepend(skipLink);
    skipLink.addEventListener("click", () => {
      main.tabIndex = -1;
      main.focus();
    });
  }

  const status = document.createElement("div");
  status.className = "connection-banner";
  status.hidden = true;
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  const statusText = document.createElement("span");
  const retryButton = document.createElement("button");
  retryButton.type = "button";
  retryButton.className = "connection-retry";
  retryButton.textContent = "Try again";
  status.append(statusText, retryButton);
  document.querySelector(".topbar")?.insertAdjacentElement("afterend", status);

  function showStatus(message, retry) {
    statusText.textContent = message;
    retryButton.hidden = typeof retry !== "function";
    retryButton.onclick = retry
      ? () => Promise.resolve(retry()).catch(() => {})
      : null;
    status.hidden = false;
  }

  function updateOnlineStatus() {
    if (navigator.onLine) {
      status.hidden = true;
      return;
    }
    showStatus("You are offline. Some school information may be unavailable.", null);
  }

  function openSidebar() {
    if (!sidebar || !backdrop) return;
    returnFocus = document.activeElement;
    sidebar?.classList.add("open");
    backdrop?.classList.add("open");
    sidebar?.setAttribute("aria-hidden", "false");
    backdrop?.setAttribute("aria-hidden", "false");
    if ("inert" in sidebar) sidebar.inert = false;
    openBtn?.setAttribute("aria-expanded", "true");
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => closeBtn?.focus());
  }

  function closeSidebar(restoreFocus = true) {
    sidebar?.classList.remove("open");
    backdrop?.classList.remove("open");
    sidebar?.setAttribute("aria-hidden", "true");
    backdrop?.setAttribute("aria-hidden", "true");
    if (sidebar && "inert" in sidebar) sidebar.inert = true;
    openBtn?.setAttribute("aria-expanded", "false");
    document.body.style.overflow = "";
    if (restoreFocus && returnFocus instanceof HTMLElement) returnFocus.focus();
  }

  function syncSidebarMode() {
    if (desktopQuery.matches) {
      sidebar?.classList.add("open");
      sidebar?.setAttribute("aria-hidden", "false");
      backdrop?.classList.remove("open");
      backdrop?.setAttribute("aria-hidden", "true");
      if (sidebar && "inert" in sidebar) sidebar.inert = false;
      openBtn?.setAttribute("aria-expanded", "true");
      document.body.style.overflow = "";
      return;
    }
    closeSidebar(false);
  }

  openBtn?.setAttribute("aria-controls", "sidebar");
  openBtn?.setAttribute("aria-expanded", "false");
  sidebar?.setAttribute("aria-hidden", "true");
  backdrop?.setAttribute("aria-hidden", "true");
  if (sidebar && "inert" in sidebar) sidebar.inert = true;

  openBtn?.addEventListener("click", openSidebar);
  closeBtn?.addEventListener("click", closeSidebar);
  backdrop?.addEventListener("click", closeSidebar);
  desktopQuery.addEventListener("change", syncSidebarMode);

  document.addEventListener("keydown", (event) => {
    if (desktopQuery.matches || !sidebar?.classList.contains("open")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeSidebar();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...sidebar.querySelectorAll(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  const path = window.location.pathname.replace(/\/$/, "") || "/";
  const activeItem = window.APP_NAV?.find((item) => item.path === path);
  document.querySelectorAll(".sidebar a[data-path]").forEach((link) => {
    if (link.getAttribute("data-path") === path) {
      link.classList.add("active");
      link.setAttribute("aria-current", "page");
    }
  });

  const rolePaths = {
    parent: "/flows/parent-chat",
    teacher: "/flows/teacher-workspace",
    management: "/flows/platform-overview",
  };

  function roleForPath() {
    if (["/flows/parent-chat", "/flows/ask-a-helper", "/flows/whatsapp"].includes(path)) return "parent";
    if (path === "/flows/teacher-workspace") return "teacher";
    if (path.startsWith("/flows/")) return "management";
    try {
      return sessionStorage.getItem("school.selectedRole") || "";
    } catch {
      return "";
    }
  }

  const currentRole = roleForPath();
  const roleText = currentRole
    ? `${currentRole.charAt(0).toUpperCase()}${currentRole.slice(1)}`
    : "Choose a role";
  const sidebarRole = document.getElementById("sidebarRole");
  if (sidebarRole) sidebarRole.textContent = roleText;

  const topbar = document.querySelector(".topbar");
  if (topbar) {
    const desktopTitle = document.createElement("div");
    desktopTitle.className = "desktop-topbar-title";
    const titleLabel = document.createElement("span");
    titleLabel.textContent = "Workspace";
    const title = document.createElement("strong");
    title.textContent = activeItem?.label || document.title.split("—")[0].trim();
    desktopTitle.append(titleLabel, title);

    const metadata = document.createElement("div");
    metadata.className = "desktop-topbar-meta";
    const school = document.createElement("span");
    school.className = "desktop-school-name";
    school.textContent = "School workspace";
    desktopSchool = school;
    try {
      const auth = JSON.parse(sessionStorage.getItem("school.authContext") || "{}");
      if (typeof auth.schoolName === "string" && auth.schoolName.trim()) {
        school.textContent = auth.schoolName;
      }
    } catch {
      // Keep the neutral school label when session data is unavailable.
    }

    const roleLabel = document.createElement("label");
    roleLabel.className = "sr-only";
    roleLabel.htmlFor = "desktopRole";
    roleLabel.textContent = "Current role";
    const roleSelect = document.createElement("select");
    roleSelect.id = "desktopRole";
    roleSelect.className = "desktop-role-select";
    if (!currentRole) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Choose role";
      option.selected = true;
      roleSelect.appendChild(option);
    }
    Object.entries(rolePaths).forEach(([value, destination]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = `${value.charAt(0).toUpperCase()}${value.slice(1)}`;
      option.dataset.destination = destination;
      option.selected = value === currentRole;
      roleSelect.appendChild(option);
    });
    roleSelect.addEventListener("change", () => {
      const destination = rolePaths[roleSelect.value];
      try {
        sessionStorage.setItem("school.selectedRole", roleSelect.value);
      } catch {
        // Navigation still works when storage is disabled.
      }
      if (destination) window.location.assign(destination);
    });
    metadata.append(school, roleLabel, roleSelect);
    topbar.append(desktopTitle, metadata);

    const loadedSchool = document.getElementById("schoolName");
    if (loadedSchool) {
      const updateSchoolName = () => {
        const value = loadedSchool.textContent?.trim();
        if (value && value !== "Connecting…") school.textContent = value;
      };
      new MutationObserver(updateSchoolName).observe(loadedSchool, {
        childList: true,
        characterData: true,
        subtree: true,
      });
      updateSchoolName();
    }
  }

  const sidebarLanguage = document.getElementById("sidebarLanguage");
  const interfaceLanguage = document.getElementById("interfaceLanguage");
  if (sidebarLanguage) {
    try {
      const savedLanguage = sessionStorage.getItem("school.interfaceLanguage");
      if (["en", "hi", "te"].includes(savedLanguage)) sidebarLanguage.value = savedLanguage;
    } catch {
      // Keep English when storage is unavailable.
    }
    if (interfaceLanguage instanceof HTMLSelectElement) {
      interfaceLanguage.value = sidebarLanguage.value;
    }
    sidebarLanguage.addEventListener("change", () => {
      document.documentElement.lang = sidebarLanguage.value;
      try {
        sessionStorage.setItem("school.interfaceLanguage", sidebarLanguage.value);
      } catch {
        // Language switching still works when storage is disabled.
      }
      if (interfaceLanguage instanceof HTMLSelectElement) {
        interfaceLanguage.value = sidebarLanguage.value;
        interfaceLanguage.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    if (interfaceLanguage instanceof HTMLSelectElement) {
      interfaceLanguage.addEventListener("change", () => {
        sidebarLanguage.value = interfaceLanguage.value;
        try {
          sessionStorage.setItem("school.interfaceLanguage", interfaceLanguage.value);
        } catch {
          // Keep the current page language when storage is unavailable.
        }
      });
    }
  }

  window.addEventListener("offline", updateOnlineStatus);
  window.addEventListener("online", updateOnlineStatus);
  window.addEventListener("schoolapi:status", (event) => {
    const { kind, message, retry } = event.detail || {};
    if (kind === "ready" && navigator.onLine) {
      status.hidden = true;
      try {
        const auth = JSON.parse(sessionStorage.getItem("school.authContext") || "{}");
        if (desktopSchool && typeof auth.schoolName === "string" && auth.schoolName.trim()) {
          desktopSchool.textContent = auth.schoolName;
        }
      } catch {
        // Keep the existing school label when session data is unavailable.
      }
    } else if (kind === "slow" || kind === "error") {
      showStatus(message, retry);
    }
  });
  syncSidebarMode();
  updateOnlineStatus();
})();
