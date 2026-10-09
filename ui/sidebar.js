(() => {
  "use strict";

  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("sidebarBackdrop");
  const openBtn = document.getElementById("openSidebar");
  const closeBtn = document.getElementById("closeSidebar");
  const main = document.querySelector("main");
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

  openBtn?.setAttribute("aria-controls", "sidebar");
  openBtn?.setAttribute("aria-expanded", "false");
  sidebar?.setAttribute("aria-hidden", "true");
  backdrop?.setAttribute("aria-hidden", "true");
  if (sidebar && "inert" in sidebar) sidebar.inert = true;

  openBtn?.addEventListener("click", openSidebar);
  closeBtn?.addEventListener("click", closeSidebar);
  backdrop?.addEventListener("click", closeSidebar);

  document.addEventListener("keydown", (event) => {
    if (!sidebar?.classList.contains("open")) return;
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
  document.querySelectorAll(".sidebar a[data-path]").forEach((link) => {
    if (link.getAttribute("data-path") === path) {
      link.classList.add("active");
      link.setAttribute("aria-current", "page");
    }
  });

  window.addEventListener("offline", updateOnlineStatus);
  window.addEventListener("online", updateOnlineStatus);
  window.addEventListener("schoolapi:status", (event) => {
    const { kind, message, retry } = event.detail || {};
    if (kind === "ready" && navigator.onLine) {
      status.hidden = true;
    } else if (kind === "slow" || kind === "error") {
      showStatus(message, retry);
    }
  });
  updateOnlineStatus();
})();
