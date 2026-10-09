(() => {
  "use strict";

  if (!document.querySelector('link[rel~="icon"]')) {
    const icon = document.createElement("link");
    icon.rel = "icon";
    icon.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23176B5B' d='M8 14 24 6l16 8v20l-16 8-16-8z'/%3E%3Cpath fill='%23FFFDF8' d='M14 18c5 0 8 1 10 3 2-2 5-3 10-3v15c-5 0-8 1-10 3-2-2-5-3-10-3z'/%3E%3C/svg%3E";
    document.head.appendChild(icon);
  }

  if (!document.querySelector('link[rel~="icon"]')) {
    const icon = document.createElement("link");
    icon.rel = "icon";
    icon.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23176B5B' d='M8 14 24 6l16 8v20l-16 8-16-8z'/%3E%3Cpath fill='%23FFFDF8' d='M14 18c5 0 8 1 10 3 2-2 5-3 10-3v15c-5 0-8 1-10 3-2-2-5-3-10-3z'/%3E%3C/svg%3E";
    document.head.appendChild(icon);
  }

  window.APP_NAV = [
    { href: "/", path: "/", label: "Choose a role", home: true },
    { href: "/flows/platform-overview", path: "/flows/platform-overview", label: "Platform overview", group: "Explore" },
    { href: "/flows/parent-chat", path: "/flows/parent-chat", label: "Parent chat", group: "Parent" },
    { href: "/flows/ask-a-helper", path: "/flows/ask-a-helper", label: "Ask a helper", group: "Parent" },
    { href: "/flows/whatsapp", path: "/flows/whatsapp", label: "WhatsApp", group: "Parent" },
    { href: "/flows/teacher-workspace", path: "/flows/teacher-workspace", label: "Teacher workspace", group: "Teacher" },
    { href: "/flows/daily-updates", path: "/flows/daily-updates", label: "Daily updates", group: "Management" },
    { href: "/flows/risk-alerts", path: "/flows/risk-alerts", label: "Risk alerts", group: "Management" },
    { href: "/flows/school-records", path: "/flows/school-records", label: "School records", group: "Management" },
    { href: "/flows/school-account", path: "/flows/school-account", label: "School account", group: "Management" },
  ];

  const mount = document.getElementById("app-nav");
  if (!mount) return;

  const home = window.APP_NAV.find((item) => item.home);
  const links = window.APP_NAV.filter((item) => !item.home);
  const homeLink = document.createElement("a");
  homeLink.className = "sidebar-home";
  homeLink.href = home.href;
  homeLink.dataset.path = home.path;
  homeLink.textContent = home.label;

  const nav = document.createElement("nav");
  nav.setAttribute("aria-label", "Platform pages");
  let currentGroup = "";
  links.forEach((item) => {
    if (item.group !== currentGroup) {
      currentGroup = item.group;
      const heading = document.createElement("p");
      heading.className = "sidebar-group";
      heading.textContent = currentGroup;
      nav.appendChild(heading);
    }
    const link = document.createElement("a");
    link.href = item.href;
    link.dataset.path = item.path;
    link.textContent = item.label;
    nav.appendChild(link);
  });

  mount.replaceChildren(homeLink, nav);
})();
