(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  function voiceIcon(className = "voice-nav-icon") {
    const image = document.createElement("img");
    image.className = className;
    image.src = "/voice-mascot-32.png";
    image.alt = "";
    image.setAttribute("aria-hidden", "true");
    return image;
  }

  function lineIcon(pathData, className = "nav-line-icon") {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", className);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", pathData);
    svg.appendChild(path);
    return svg;
  }

  if (!document.querySelector('link[rel~="icon"]')) {
    const icon = document.createElement("link");
    icon.rel = "icon";
    icon.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23176B5B' d='M8 14 24 6l16 8v20l-16 8-16-8z'/%3E%3Cpath fill='%23FFFDF8' d='M14 18c5 0 8 1 10 3 2-2 5-3 10-3v15c-5 0-8 1-10 3-2-2-5-3-10-3z'/%3E%3C/svg%3E";
    document.head.appendChild(icon);
  }

  window.APP_NAV = [
    { href: "/", path: "/", label: "Choose a role", home: true, icon: "M4 11 12 4l8 7v9h-5v-6H9v6H4z" },
    { href: "/flows/platform-overview", path: "/flows/platform-overview", label: "Platform overview", group: "Explore", icon: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" },
    { href: "/flows/parent-chat", path: "/flows/parent-chat", label: "Parent chat", group: "Parent", icon: "M4 5h16v12H9l-5 3z" },
    { href: "/flows/voice-assistant", path: "/flows/voice-assistant", label: "Voice", group: "Parent", voice: true },
    { href: "/flows/ask-a-helper", path: "/flows/ask-a-helper", label: "Ask a helper", group: "Parent", icon: "M12 3 9.8 8.8 4 11l5.8 2.2L12 19l2.2-5.8L20 11l-5.8-2.2z" },
    { href: "/flows/whatsapp", path: "/flows/whatsapp", label: "WhatsApp", group: "Parent", icon: "M7 18 4 20l1-4a8 8 0 1 1 3 3z" },
    { href: "/flows/teacher-workspace", path: "/flows/teacher-workspace", label: "Teacher workspace", group: "Teacher", icon: "M4 5c4-1 6 0 8 2 2-2 4-3 8-2v14c-4-1-6 0-8 2-2-2-4-3-8-2z" },
    { href: "/flows/daily-updates", path: "/flows/daily-updates", label: "Daily updates", group: "Management", icon: "M5 4h14v16H5zM8 2v4M16 2v4M5 9h14" },
    { href: "/flows/risk-alerts", path: "/flows/risk-alerts", label: "Risk alerts", group: "Management", icon: "M12 3 20 7v5c0 5-3 8-8 10-5-2-8-5-8-10V7zM12 8v5M12 17h.01" },
    { href: "/flows/school-records", path: "/flows/school-records", label: "School records", group: "Management", icon: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8-1a2.5 2.5 0 1 0 0-5M3 20v-3c0-3 2-5 5-5s5 2 5 5v3M15 12c3 0 5 2 5 5v3" },
    { href: "/flows/school-account", path: "/flows/school-account", label: "Settings", settings: true, icon: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8 4 2 1-2 4-2-.5-1.5 1.5.5 2-4 2-1-2-2-.5-1 1.5-4-2 .5-2L5 16.5 3 17l-2-4 2-1 .5-2L2 8l4-2 1.5 1.5 2-.5 1-2h4l1 2 2 .5L19 6l3 2-2 2z" },
  ];

  const mount = document.getElementById("app-nav");
  if (!mount) return;

  const sidebar = document.getElementById("sidebar");
  const sidebarHeader = sidebar?.querySelector(".sidebar-header");
  const brand = document.createElement("a");
  brand.className = "sidebar-brand";
  brand.href = "/";
  brand.setAttribute("aria-label", "School Parent AI home");
  const mark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  mark.setAttribute("class", "brand-mark");
  mark.setAttribute("viewBox", "0 0 48 48");
  mark.setAttribute("aria-hidden", "true");
  const shield = document.createElementNS("http://www.w3.org/2000/svg", "path");
  shield.setAttribute("d", "M8 13.5 24 6l16 7.5v21L24 42 8 34.5z");
  shield.setAttribute("fill", "#2DD4FF");
  const book = document.createElementNS("http://www.w3.org/2000/svg", "path");
  book.setAttribute("d", "M14 17.5c4.8 0 8.1 1.1 10 3.4 1.9-2.3 5.2-3.4 10-3.4v15.1c-4.7 0-8 1.1-10 3.4-2-2.3-5.3-3.4-10-3.4z");
  book.setAttribute("fill", "#071D40");
  mark.append(shield, book);
  const brandName = document.createElement("span");
  brandName.textContent = "School Parent AI";
  brand.append(mark, brandName);
  sidebarHeader?.prepend(brand);

  const home = window.APP_NAV.find((item) => item.home);
  const links = window.APP_NAV.filter((item) => !item.home && !item.settings);
  const homeLink = document.createElement("a");
  homeLink.className = "sidebar-home";
  homeLink.href = home.href;
  homeLink.dataset.path = home.path;
  homeLink.append(lineIcon(home.icon), document.createTextNode(home.label));

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
    if (item.voice) {
      link.classList.add("sidebar-voice-link");
      link.append(voiceIcon(), document.createTextNode(item.label));
    } else {
      link.append(lineIcon(item.icon), document.createTextNode(item.label));
    }
    nav.appendChild(link);
  });

  const footer = document.createElement("div");
  footer.className = "sidebar-footer";
  const settingsItem = window.APP_NAV.find((item) => item.settings);
  const settingsLink = document.createElement("a");
  settingsLink.className = "sidebar-settings";
  settingsLink.href = settingsItem.href;
  settingsLink.dataset.path = settingsItem.path;
  settingsLink.append(lineIcon(settingsItem.icon), document.createTextNode(settingsItem.label));
  const roleLabel = document.createElement("span");
  roleLabel.className = "sidebar-footer-label";
  roleLabel.textContent = "Current role";
  const roleValue = document.createElement("strong");
  roleValue.id = "sidebarRole";
  roleValue.textContent = "Choose a role";
  const languageLabel = document.createElement("label");
  languageLabel.className = "sidebar-footer-label";
  languageLabel.htmlFor = "sidebarLanguage";
  languageLabel.textContent = "Language";
  const language = document.createElement("select");
  language.id = "sidebarLanguage";
  language.className = "sidebar-language";
  const languageOptions = window.location.pathname === "/flows/voice-assistant"
    ? [["en", "English"], ["hi", "हिन्दी"]]
    : [["en", "English"], ["hi", "हिन्दी"], ["te", "తెలుగు"]];
  languageOptions.forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    language.appendChild(option);
  });
  footer.append(settingsLink, roleLabel, roleValue, languageLabel, language);

  mount.replaceChildren(homeLink, nav, footer);

  const currentPath = window.location.pathname.replace(/\/$/, "") || "/";
  const bottomNav = document.createElement("nav");
  bottomNav.className = "mobile-bottom-nav";
  bottomNav.setAttribute("aria-label", "Parent navigation");
  const bottomItems = [
    ["Home", "/", "M5 11 12 5l7 6v8h-5v-5h-4v5H5z"],
    ["Chat", "/flows/parent-chat", "M4 5h16v12H9l-5 3z"],
    ["Voice", "/flows/voice-assistant", ""],
    ["Updates", "/flows/daily-updates", "M5 5h14v14H5zM8 9h8M8 13h6"],
  ];
  bottomItems.forEach(([label, href, path]) => {
    const link = document.createElement("a");
    link.href = href;
    if (href === currentPath) {
      link.classList.add("active");
      link.setAttribute("aria-current", "page");
    }
    if (label === "Voice") {
      link.classList.add("mobile-voice-destination");
      link.appendChild(voiceIcon("bottom-voice-icon"));
    } else {
      const icon = document.createElementNS(SVG_NS, "svg");
      icon.setAttribute("viewBox", "0 0 24 24");
      icon.setAttribute("aria-hidden", "true");
      const shape = document.createElementNS(SVG_NS, "path");
      shape.setAttribute("d", path);
      icon.appendChild(shape);
      link.appendChild(icon);
    }
    const text = document.createElement("span");
    text.textContent = label;
    link.appendChild(text);
    bottomNav.appendChild(link);
  });
  document.body.appendChild(bottomNav);

  if (currentPath === "/flows/parent-chat") {
    const launch = document.createElement("a");
    launch.className = "floating-voice-launch";
    launch.href = "/flows/voice-assistant";
    launch.setAttribute("aria-label", "Open hands-free voice assistant");
    launch.append(voiceIcon("floating-voice-icon"));
    document.body.appendChild(launch);
  }
})();
