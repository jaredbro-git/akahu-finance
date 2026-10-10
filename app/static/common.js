// Shared by every page: the "Hide numbers" toggle. base.html applies the saved
// setting before the page renders; this wires up the button.
(() => {
  const root = document.documentElement;
  const toggle = document.getElementById("privacy-toggle");
  const isPrivate = () => root.classList.contains("private");
  function syncToggle() {
    toggle.setAttribute("aria-pressed", String(isPrivate()));
    toggle.textContent = isPrivate() ? "Show numbers" : "Hide numbers";
  }
  toggle.addEventListener("click", () => {
    root.classList.toggle("private");
    try { localStorage.setItem("finance.private", isPrivate() ? "1" : "0"); } catch { /* ignore */ }
    syncToggle();
    document.dispatchEvent(new Event("privacychange"));
  });
  syncToggle();
})();
