(() => {
  window.APP_NAV = [
    { href: "/", path: "/", label: "Home — Try the platform", home: true },
    { href: "/flows/platform-overview", path: "/flows/platform-overview", label: "Platform overview" },
    { href: "/flows/parent-chat", path: "/flows/parent-chat", label: "Parent chat" },
    { href: "/flows/ask-a-helper", path: "/flows/ask-a-helper", label: "Ask a helper" },
    { href: "/flows/whatsapp", path: "/flows/whatsapp", label: "WhatsApp" },
    { href: "/flows/daily-updates", path: "/flows/daily-updates", label: "Daily updates" },
    { href: "/flows/risk-alerts", path: "/flows/risk-alerts", label: "Risk alerts" },
    { href: "/flows/school-records", path: "/flows/school-records", label: "School records" },
    { href: "/flows/school-account", path: "/flows/school-account", label: "School account" },
  ];

  const mount = document.getElementById("app-nav");
  if (!mount) return;

  const home = window.APP_NAV.find((item) => item.home);
  const links = window.APP_NAV.filter((item) => !item.home);
  mount.innerHTML = `
    <a class="sidebar-home" href="${home.href}" data-path="${home.path}">${home.label}</a>
    <nav>
      ${links
        .map(
          (item) =>
            `<a href="${item.href}" data-path="${item.path}">${item.label}</a>`
        )
        .join("")}
    </nav>
  `;
})();
