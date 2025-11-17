document.addEventListener("click", (event) => {
  const toggleButton = event.target.closest("[data-story-toggle]");
  if (!toggleButton) {
    return;
  }

  const targetId = toggleButton.getAttribute("data-story-toggle");
  const target = document.getElementById(targetId);
  if (!target) {
    return;
  }

  const collapsed = target.getAttribute("data-collapsed") !== "false";
  target.setAttribute("data-collapsed", collapsed ? "false" : "true");
  toggleButton.textContent = collapsed ? "收起全文" : "展开全文";
});
