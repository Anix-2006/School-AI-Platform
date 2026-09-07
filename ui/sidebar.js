(() => {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("sidebarBackdrop");
  const openBtn = document.getElementById("openSidebar");
  const closeBtn = document.getElementById("closeSidebar");

  function openSidebar() {
    sidebar?.classList.add("open");
    backdrop?.classList.add("open");
    document.body.style.overflow = "hidden";
  }

  function closeSidebar() {
    sidebar?.classList.remove("open");
    backdrop?.classList.remove("open");
    document.body.style.overflow = "";
  }

  openBtn?.addEventListener("click", openSidebar);
  closeBtn?.addEventListener("click", closeSidebar);
  backdrop?.addEventListener("click", closeSidebar);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSidebar();
  });

  const path = window.location.pathname.replace(/\/$/, "") || "/";
  document.querySelectorAll(".sidebar a[data-path]").forEach((link) => {
    if (link.getAttribute("data-path") === path) {
      link.classList.add("active");
    }
  });
})();
